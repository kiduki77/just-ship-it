"""Team chat's store: one conversation per subject, made by its first line, its lines numbered without gaps, and the
owner's task comments as lines of that task's conversation."""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from justshipit.agents import roles
from justshipit.db.models import AgentMessage, AgentRequest, ChatThread, DevJob, Job, Spec
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.i18n.said import Refusal
from justshipit.services import chat, chat_tags


def _project() -> int:
    from justshipit.services import projects
    with session_scope() as s:
        return projects.create(s, "chat store", name="Chat store", budget_usd=5, start=False).id


def _send(project_id: int, body: str, client_id: str | None = None) -> dict:
    with session_scope() as s:
        return chat.send(s, subject=chat_tags.resolve(s, "project", str(project_id), None), to_agent=None, body=body,
                         client_id=client_id, lang="en")


def test_two_first_messages_racing_on_one_subject_share_one_thread_with_no_gap(own_jobs_settled):
    pid = _project()

    with ThreadPoolExecutor(4) as pool:
        sent = list(pool.map(lambda n: _send(pid, f"line {n}"), range(8)))

    assert len({x["thread_id"] for x in sent}) == 1
    with session_scope() as s:
        thread = s.get(ChatThread, sent[0]["thread_id"])
        seqs = list(s.scalars(select(AgentMessage.seq).where(AgentMessage.thread_id == thread.id).order_by(AgentMessage.seq)))
        assert seqs == list(range(1, 9)) and thread.last_seq == 8
        assert thread.owner_read_seq == 8, "the owner has read their own lines"
        assert (thread.tag_kind, thread.tag_ref, thread.default_agent) == ("project", str(pid), "project_manager")


def test_quick_lines_make_one_waiting_reply_and_a_retry_sends_once(own_jobs_settled):
    pid = _project()

    first = _send(pid, "is the landing page done?", client_id="c-1")
    again = _send(pid, "is the landing page done?", client_id="c-1")
    _send(pid, "and the pricing section?")

    assert again["message_id"] == first["message_id"]
    with session_scope() as s:
        lines = s.scalar(select(ChatThread.last_seq).where(ChatThread.id == first["thread_id"]))
        jobs = s.scalars(select(Job).where(Job.kind == "chat_reply",
                                           Job.payload["thread_id"].as_integer() == first["thread_id"])).all()
    assert lines == 2
    assert [(j.payload["agent"], j.lock_key, j.gate) for j in jobs] == [("project_manager", f"chat:{first['thread_id']}", "direct")]


def test_a_line_in_a_conversation_from_before_the_rename_goes_to_the_project_manager(own_jobs_settled):
    pid = _project()
    first = _send(pid, "is the landing page done?")
    with session_scope() as s:
        s.get(ChatThread, first["thread_id"]).default_agent = roles.FORMER_PROJECT_MANAGER

    sent = _send(pid, "and the pricing section?")

    with session_scope() as s:
        assert s.get(AgentMessage, sent["message_id"]).to_agent == "project_manager"
        jobs = s.scalars(select(Job).where(Job.kind == "chat_reply",
                                           Job.payload["thread_id"].as_integer() == first["thread_id"])).all()
        assert [j.payload["agent"] for j in jobs] == ["project_manager"]


def test_opening_the_dock_makes_nothing(s):
    pid = _project()

    view = chat.subject(s, chat_tags.resolve(s, "project", str(pid), None), "en")

    assert view["thread_id"] is None and view["default_agent"]["id"] == "project_manager"
    assert s.scalar(select(ChatThread).where(ChatThread.tag_kind == "project", ChatThread.tag_ref == str(pid))) is None


def test_a_task_comment_is_a_line_of_its_tasks_conversation(s, project):
    from justshipit.db.models import Task
    from justshipit.services import control
    t = Task(project_id=project.id, key="k-chat", title="Survey", owner="research", objective="o")
    s.add(t)
    s.flush()

    control.comment_task(s, t.id, "please ask 20 cafés, not 10", lang="en")
    control.comment_task(s, t.id, "and keep it under 5 questions", lang="en")
    s.flush()

    thread = s.scalar(select(ChatThread).where(ChatThread.tag_kind == "task", ChatThread.tag_ref == str(t.id)))
    lines = s.scalars(select(AgentMessage).where(AgentMessage.thread_id == thread.id).order_by(AgentMessage.seq)).all()
    assert [(m.seq, m.kind, m.from_agent, m.to_agent, m.ref_type, m.ref_id) for m in lines] == [
        (1, "comment", "owner", "research", "task", t.id), (2, "comment", "owner", "research", "task", t.id)]
    assert thread.default_agent == "research"
    from justshipit.orchestration import context
    assert "20 cafés" in context.owner_comments(s, t), "the task's next run still reads them"


def test_archive_never_deletes_and_a_new_line_brings_the_thread_back(own_jobs_settled):
    pid = _project()
    sent = _send(pid, "hello")
    with session_scope() as s:
        chat.archive(s, sent["thread_id"], True)
    with session_scope() as s:
        assert [t["id"] for t in chat.threads(s, None, "en")].count(sent["thread_id"]) == 0
        assert sent["thread_id"] in [t["id"] for t in chat.threads(s, None, "en", archived=True)]

    _send(pid, "one more thing")

    with session_scope() as s:
        assert s.get(ChatThread, sent["thread_id"]).archived_at is None
        assert s.scalar(select(ChatThread.last_seq).where(ChatThread.id == sent["thread_id"])) == 2


def test_the_read_pointer_only_moves_forward(own_jobs_settled):
    pid = _project()
    sent = _send(pid, "hello")
    with session_scope() as s:
        thread = s.get(ChatThread, sent["thread_id"])
        chat.status_line(s, chat.locked(s, thread.id), "chat.status.round_running")
        chat.status_line(s, chat.locked(s, thread.id), "chat.status.round_delivered")
    with session_scope() as s:
        assert chat.thread_view(s, s.get(ChatThread, sent["thread_id"]), "en")["unread_count"] == 2
        chat.read(s, sent["thread_id"], 3)
        chat.read(s, sent["thread_id"], 2)
        assert s.get(ChatThread, sent["thread_id"]).owner_read_seq == 3


def test_a_tasks_comments_are_the_owners_comments_not_the_rest_of_its_conversation(own_jobs_settled):
    """A task's conversation also holds the owner's chat lines, the agent's replies and status lines. The task page's
    comments and the task agent's "follow every one" list are the owner's comments only."""
    from justshipit.api import console
    from justshipit.db.models import Task
    from justshipit.orchestration import context
    from justshipit.services import control
    pid = _project()
    with session_scope() as s:
        t = Task(project_id=pid, key="t-chat", title="t", owner="research", objective="o", acceptance_criteria=["c"],
                 status="READY")
        s.add(t)
        s.flush()
        tid = t.id
        control.comment_task(s, tid, "only use 2025 data", lang="en")
        sent = chat.send(s, subject=chat_tags.resolve(s, "task", str(tid), None), to_agent=None, body="why is it slow?",
                         client_id=None, lang="en")
        thread = chat.locked(s, sent["thread_id"])
        chat.append(s, thread, from_agent="research", to_agent=chat.OWNER, kind="text", body="Ignore the 2025 rule.")
        chat.status_line(s, thread, "chat.status.reply_failed")

    comments = console.task_detail(tid)["comments"]

    assert [c["body"] for c in comments] == ["only use 2025 data"]
    with session_scope() as s:
        said = context.owner_comments(s, s.get(Task, tid))
    assert "only use 2025 data" in said and "Ignore the 2025 rule." not in said and "why is it slow?" not in said


# ------------------------------------------------------------------ a merge card step, attached to a line

HEAD = "9f3c1d2e4a5b6c7d8e9f0a1b2c3d4e5f60718293"
OTHER_HEAD = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"


def _step(sid: str, do: str, printed: str) -> dict:
    return {"id": sid, "do": do, "why": "to start", "see": "A new folder", "kind": "run", "action": "npx uxscope init app",
            "expect": "ok", "seed": [], "state": "differs", "not_run": None, "need": None, "fault": "product",
            "fault_by": "agent", "judged": True, "looks_like": "", "hint": "",
            "capture": {"text": printed, "lines": 1, "cut_after": None, "exit": 1, "ms": 40, "shot": None, "file": None,
                        "volatile": [], "scrubbed": []}}


def merge_card(s, project, *, printed: str = "error: no features.md\n") -> tuple[AgentRequest, DevJob]:
    """A delivered build, the walkthrough its acceptance recorded for HEAD, and the merge card about it."""
    spec = Spec(spec_key=f"SPEC-about-{uuid.uuid4().hex[:10]}", project_id=project.id, title="uxscope",
                registry_path="specs/x", handoff={})
    s.add(spec)
    s.flush()
    run = {"v": 1, "head": HEAD, "spec_commit": "c0ffee", "ran_at": "2026-11-13T00:00:00+00:00", "ms": 900, "setup_ms": 100,
           "about": None, "stories": [], "setup": [_step("setup.1", "Install it", "added 1 package\n")],
           "journeys": [{"id": "JN1", "title": "Turn a list into specs",
                         "steps": [_step("JN1.1", "Start a project", "ok\n"), _step("JN1.2", "Make the specs", printed)]}]}
    job = DevJob(spec_id=spec.id, status="DELIVERED", steps=[], result={"walkthroughs": [run]})
    s.add(job)
    s.flush()
    card = AgentRequest(project_id=project.id, from_agent="product", type="APPROVAL", title="Merge uxscope", summary="s",
                        rationale="r", status="PENDING", policy_mode="approve", action_kind="dev.merge",
                        action_params={"dev_job_id": job.id, "head": HEAD})
    s.add(card)
    s.commit()
    return card, job


def _ask(s, kind: str, ref: str, about: dict | None, body: str = "I got something else") -> dict:
    return chat.send(s, subject=chat_tags.resolve(s, kind, ref, None), to_agent=None, body=body, client_id=None, lang="en",
                     about=about)


def _about(step: str = "JN1.2", head: str = HEAD, kind: str = "walkthrough_step") -> dict:
    return {"kind": kind, "step": step, "head": head}


@pytest.mark.parametrize("step", ["JN1.2", "setup.1"])
def test_a_step_of_the_cards_own_commit_is_kept_with_the_line_in_the_builds_conversation(s, project, own_jobs_settled, step):
    card, job = merge_card(s, project)

    sent = _ask(s, "request", str(card.id), _about(step))
    s.commit()

    line = s.get(AgentMessage, sent["message_id"])
    thread = s.get(ChatThread, sent["thread_id"])
    assert (thread.tag_kind, thread.tag_ref) == ("dev_job", str(job.id)), "a merge card is its build's conversation"
    assert line.data == {"about": _about(step)} and line.body == "I got something else"


def test_a_line_without_a_step_carries_no_about(s, project, own_jobs_settled):
    card, _ = merge_card(s, project)

    sent = _ask(s, "request", str(card.id), None)
    s.commit()

    assert s.get(AgentMessage, sent["message_id"]).data == {}


@pytest.mark.parametrize("about, why", [
    (_about("JN9.9"), "chat.refuse.about_step"),
    (_about("JN1.2", OTHER_HEAD), "chat.refuse.about_step"),
    (_about("JN1.2", ""), "chat.refuse.about_step"),
    (_about(kind="file"), "chat.refuse.about_subject"),
])
def test_a_step_the_build_never_recorded_for_that_commit_is_refused_and_writes_nothing(s, project, own_jobs_settled,
                                                                                       about, why):
    card, job = merge_card(s, project)

    with pytest.raises(Refusal) as refused:
        _ask(s, "request", str(card.id), about)

    assert refused.value.words("en") == msg(why, "en")
    assert s.scalar(select(ChatThread.id).where(ChatThread.tag_kind == "dev_job", ChatThread.tag_ref == str(job.id))) is None


def test_a_step_on_a_subject_that_is_no_merge_card_is_refused(s, project, own_jobs_settled):
    merge_card(s, project)
    plain = AgentRequest(project_id=project.id, from_agent="product", type="APPROVAL", title="Something else", summary="s",
                         rationale="r", status="PENDING", policy_mode="approve", action_kind="skill.create_candidate")
    s.add(plain)
    s.commit()

    for kind, ref in (("request", str(plain.id)), ("project", str(project.id)), ("general", None)):
        with pytest.raises(Refusal) as refused:
            _ask(s, kind, ref, _about())
        assert refused.value.words("en") == msg("chat.refuse.about_subject", "en")
    assert s.scalar(select(AgentMessage.id).where(AgentMessage.from_agent == chat.OWNER,
                                                  AgentMessage.project_id == project.id)) is None


def test_the_step_is_checked_in_the_build_the_card_is_about_not_in_any_build(s, project, own_jobs_settled):
    card, _ = merge_card(s, project)
    other, other_job = merge_card(s, project)
    other_job.result = {"walkthroughs": []}
    s.commit()

    with pytest.raises(Refusal):
        _ask(s, "request", str(other.id), _about())
    assert _ask(s, "request", str(card.id), _about())["seq"] == 1
