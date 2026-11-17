"""Work asked in team chat never races the build's other work and never loses a request: while the build has a check, a
merge or another round on its way, the engineer says so instead of starting a round; a fix-or-drop question on the build
is set aside like its merge card, so a later answer to it cannot throw a good build away; a round that follows a chat
round stays in that conversation and on this machine; and a reply that waits on its build does not hold the
conversation while it waits."""
from __future__ import annotations

import threading
import time
import uuid

import pytest
from sqlalchemy import select, text

from justshipit.contracts.chat import ChatReplyOut, WorkAsk
from justshipit.contracts.handoff import FIX_QUESTION_KEY
from justshipit.db.models import AgentMessage, AgentRequest, DevJob, Job, Spec
from justshipit.db.session import session_scope
from justshipit.engineering import revise
from justshipit.services import chat, chat_reply, chat_tags, queue

FAILED_WORDS = "Fix what made the check fail:\n- The setup script failed."


def _build(s, project, *fixes: dict, **result) -> DevJob:
    spec = Spec(spec_key=f"SPEC-work-{uuid.uuid4().hex[:10]}", project_id=project.id, title="uxscope",
                registry_path="specs/x", handoff={"target": {"repository": "uxscope"}})
    s.add(spec)
    s.flush()
    job = DevJob(spec_id=spec.id, status=result.pop("status", "DELIVERED"), branch="ai/work-a1", steps=[],
                 result={"head": "a" * 40, "fixes": list(fixes), **result})
    s.add(job)
    s.flush()
    return job


def _round(n: int, state: str, **kw) -> dict:
    return {"n": n, "state": state, "note": f"round {n}", "from": "b" * 40, "at": "2026-11-13T00:00:00+00:00", **kw}


def _ask(s, job: DevJob, body: str = "the footer is cut off, please fix") -> tuple[int, AgentMessage | None]:
    """The owner's line on the build and the engineer's reply taking the work: the thread, and the reply line."""
    sent = chat.send(s, subject=chat_tags.resolve(s, "dev_job", str(job.id), None), to_agent=None, body=body,
                     client_id=None, lang="en")
    out = ChatReplyOut(reply="On it.", intent="work", work=WorkAsk(title="Fix the footer", instructions="Stop the clip."),
                       to=None, why=None, decision=None)
    reply = chat_reply.apply(s, sent["thread_id"], "engineer", out, hop=1, escalation=None, upto=sent["seq"],
                             asked_ids=[sent["message_id"]], call_id=None, lang="en")
    s.flush()
    return sent["thread_id"], reply


def _kinds(s, thread_id: int) -> list[tuple[str, str | None]]:
    return [(m.kind, (m.data or {}).get("key")) for m in s.scalars(
        select(AgentMessage).where(AgentMessage.thread_id == thread_id).order_by(AgentMessage.seq))]


def test_a_chat_ask_while_the_check_is_on_the_queue_starts_no_round_and_says_so(s, project, own_jobs_settled):
    """The chat round was delivered and its acceptance waits on the queue. A second ask now would start a round the
    acceptance's merge card would sit beside, and its "done" line would land under the wrong task."""
    job = _build(s, project, _round(1, "delivered", asked_in=None))
    queue.enqueue(s, "acceptance", {"dev_job_id": job.id}, idempotency_key=f"acceptance:{job.id}:fix1", approved=True)

    assert chat.subject(s, chat_tags.resolve(s, "dev_job", str(job.id), None), "en")["work"] == [], \
        "the reply is not offered work while the build is busy"
    thread_id, _ = _ask(s, job)

    assert len(revise.rounds(job)) == 1, "no round beside the check"
    assert _kinds(s, thread_id) == [("text", None), ("text", None), ("status", "chat.status.round_busy")]
    line = s.scalars(select(AgentMessage).where(AgentMessage.thread_id == thread_id)).all()[-1]
    assert "ask me again" in chat.view(s, line, "en")["text"]
    s.rollback()


@pytest.mark.parametrize("state", ["queued", "waiting_budget"])
def test_a_chat_ask_while_a_round_waits_keeps_that_rounds_words(s, project, own_jobs_settled, state):
    job = _build(s, project, _round(1, state, asked_in=None))

    thread_id, _ = _ask(s, job)

    assert [(f["n"], f["note"], f["state"]) for f in revise.rounds(job)] == [(1, "round 1", state)]
    assert _kinds(s, thread_id)[-1] == ("status", "chat.status.round_busy")
    s.rollback()


def test_a_chat_fix_sets_the_fix_or_drop_card_aside_and_carries_what_failed(s, project, own_jobs_settled):
    """The build failed its check and the owner's fix-or-drop card waits. Asking for a fix in chat answers it: the card
    is set aside, so a later "drop" on it cannot throw away the build the round fixes, and the round fixes what
    failed as well as what the owner asked."""
    job = _build(s, project, failed=[{"key": "build.failed.run"}])
    card = AgentRequest(project_id=project.id, from_agent="product", type="QUESTION", title="It failed its check",
                        summary="s", rationale="r", policy_mode="approve", status="PENDING",
                        action_params={FIX_QUESTION_KEY: job.id, revise.FAILED: FAILED_WORDS, "drop_option": "Drop"})
    s.add(card)
    s.flush()

    thread_id, _ = _ask(s, job)

    s.refresh(card)
    assert (card.status, card.response["decision"]) == ("CANCELLED", "withdrawn")
    fix = revise.latest(job)
    assert fix["state"] == "queued" and FAILED_WORDS in fix["note"] and "Stop the clip." in fix["note"]
    assert ("status", "chat.status.card_set_aside") in _kinds(s, thread_id)
    s.rollback()


def test_asking_again_for_a_stopped_chat_round_stays_in_its_conversation(s, project, own_jobs_settled):
    job = _build(s, project, _round(1, "delivered"))
    s.commit()
    thread_id, _ = _ask(s, job)
    task = s.scalar(select(AgentMessage).where(AgentMessage.thread_id == thread_id, AgentMessage.kind == "task"))
    first = revise.latest(job)
    job.result = {**job.result, "fixes": [*revise.rounds(job)[:-1], {**first, "state": "stopped", "error": "x"}]}
    s.execute(text("UPDATE jobs SET status = 'done' WHERE idempotency_key = :k"), {"k": f"dev_fix:{job.id}:{first['n']}"})
    s.commit()

    revise.again(job.id, "en")

    s.expire_all()
    job, task = s.get(DevJob, job.id), s.get(AgentMessage, task.id)
    again = revise.latest(job)
    assert (again["n"], again["asked_in"], again["owner_said"]) == (first["n"] + 1, task.id, first["owner_said"])
    assert task.data["work"]["n"] == again["n"], "the task card follows the round asked again"
    assert chat_tags.task_state(s, task.data["work"])["state"] == "received"
    assert _kinds(s, thread_id)[-1] == ("status", "chat.status.round_queued")
    revise._keep(job, {**again, "state": "running"})
    assert revise.stays_local(job), "a round asked again from a chat round never pushes"
    s.rollback()


def test_the_automatic_round_after_a_chat_round_failed_its_check_stays_in_that_conversation(s, project,
                                                                                          own_jobs_settled):
    job = _build(s, project, failed=[{"key": "build.failed.run"}], acceptance={"accepted": False, "criteria_results": []})
    thread_id, _ = _ask(s, job)
    task = s.scalar(select(AgentMessage).where(AgentMessage.thread_id == thread_id, AgentMessage.kind == "task"))
    chat_round = revise.latest(job)
    revise._keep(job, {**chat_round, "state": "delivered"})
    s.execute(text("UPDATE jobs SET status = 'done' WHERE idempotency_key = :k"),
              {"k": f"dev_fix:{job.id}:{chat_round['n']}"})

    revise.failed_check(s, job, s.get(Spec, job.spec_id), "en")

    auto = revise.latest(job)
    assert (auto["n"], auto.get("auto"), auto.get("asked_in")) == (chat_round["n"] + 1, True, task.id)
    assert task.data["work"]["n"] == auto["n"]
    revise._keep(job, {**auto, "state": "running"})
    assert revise.stays_local(job)
    s.rollback()


def test_a_status_line_goes_to_the_conversation_of_the_round_it_is_about(s, project, own_jobs_settled):
    job = _build(s, project)
    thread_id, _ = _ask(s, job)
    task = s.scalar(select(AgentMessage).where(AgentMessage.thread_id == thread_id, AgentMessage.kind == "task"))
    job.result = {**job.result, "fixes": [{**revise.latest(job), "state": "delivered"}, _round(2, "queued")]}

    chat.round_line(s, job, "chat.status.round_done", n=1, ref="REQ-1")

    assert _kinds(s, thread_id)[-1] == ("status", "chat.status.round_done")
    s.rollback()


def test_a_reply_waiting_on_its_build_does_not_hold_the_conversation(s, project, own_jobs_settled):
    """Every round transition locks the build, then its conversation. A reply that took work locked them the other way
    round, so a round moving at the same moment deadlocked with it."""
    job = _build(s, project)
    sent = chat.send(s, subject=chat_tags.resolve(s, "dev_job", str(job.id), None), to_agent=None, body="fix it",
                     client_id=None, lang="en")
    job_id = job.id
    s.commit()
    out = ChatReplyOut(reply="On it.", intent="work", work=WorkAsk(title="Fix", instructions="x"), to=None, why=None,
                       decision=None)
    done = threading.Event()

    def reply():
        with session_scope() as s:
            chat_reply.apply(s, sent["thread_id"], "engineer", out, hop=1, escalation=None, upto=sent["seq"],
                             asked_ids=[sent["message_id"]], call_id=None, lang="en")
        done.set()

    with session_scope() as holder:
        holder.get(DevJob, job_id, with_for_update=True)  # a round transition holds the build
        t = threading.Thread(target=reply)
        t.start()
        try:
            deadline = time.monotonic() + 20
            while not holder.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
                                         "AND datname = current_database()")):
                assert time.monotonic() < deadline, "the reply never waited on the build"
                time.sleep(0.05)
            with session_scope() as other:
                other.execute(text("SET LOCAL lock_timeout = '500ms'"))
                other.execute(text("SELECT id FROM chat_threads WHERE id = :id FOR UPDATE"), {"id": sent["thread_id"]})
        finally:
            holder.commit()
            t.join(30)
    assert done.is_set()
