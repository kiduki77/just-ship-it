"""Team chat: the owner talks to the team about what is on the screen, and asks for work from there.

A conversation (`ChatThread`) is one per subject per business (services/chat_tags.py), made by its first message, so
opening the dock makes nothing. Its lines are `agent_messages` rows numbered by `seq` under the thread's row lock. The
owner's text is scrubbed of secrets here, before the row exists, so no copy reaches the database, an event, a prompt or
a log: the row keeps only what kind was removed and how many. A line the server writes (a status, an escalation) keeps
its catalog key and values in `data`, and is worded in the owner's language when it is read.

Sending enqueues one `chat_reply` job (services/chat_reply.py) in the same transaction. Its key is the agent's last
answered line, so three quick messages make one job that answers all three. Chat never approves, merges or pushes: a
reply's effects are the typed intents `chat_reply` checks against `chat_tags.allowed`.

A line can recall the role that worked on a task or build (`about = {"kind": "recall", "role"}`, design section 8). A
recall line is not one of the lines an ordinary reply answers: it gets its own reply job keyed by its `seq` and its
own answered marker (`recall_answered`), and its answer comes from the kept records only (services/chat_recall.py).
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from justshipit import staffing
from justshipit.agents import roles
from justshipit.contracts import secrets_scan
from justshipit.db.models import AgentMessage, ChatThread, DevJob, Job
from justshipit.i18n import Lang, glossary, msg
from justshipit.i18n.said import Refusal, said
from justshipit.services import app_settings, chat_tags, queue
from justshipit.settings import agents_config, company_config

log = logging.getLogger(__name__)

REPLY = "chat_reply"  # the queue job that answers
STEP = "walkthrough_step"  # what a message's `about` can name: a step of a merge card's walkthrough
RECALL = "recall"  # or the role that worked on the subject, asked about that work
OWNER = "owner"
SYSTEM = "system"  # the sender of a line the server writes
_LAST_CHARS = 140  # of the last line a conversation row shows
_DONE = ("done", "stopped", "cancelled")


def config() -> dict:
    return company_config().get("chat") or {}


def max_hops() -> int:
    return int(config().get("max_hops", 3))


def body_cap() -> int:
    return int(config().get("body_chars", 20_000))


def _marker(lang: Lang) -> Callable[[str], str]:
    return lambda kind: msg("chat.scrub.marker", lang, what=msg(f"chat.secret.{kind}", lang))


def scrub(text: str, lang: Lang) -> tuple[str, list[dict]]:
    """`text` with every secret replaced by a marker in the owner's language, and what was removed (kind and count)."""
    return secrets_scan.scrub(text, _marker(lang))


# ------------------------------------------------------------------ threads and lines

def _key(subject: chat_tags.Subject):
    return (ChatThread.business_id.is_not_distinct_from(subject.business_id), ChatThread.tag_kind == subject.kind,
            ChatThread.tag_ref == subject.ref)


def find_thread(s: Session, subject: chat_tags.Subject) -> ChatThread | None:
    return s.scalar(select(ChatThread).where(*_key(subject)))


def thread_for(s: Session, subject: chat_tags.Subject) -> ChatThread:
    """The subject's thread, made now when it has none, locked for the caller's next line. Two first messages racing
    on one subject land in one thread: the second insert waits on the first's unique key, then does nothing."""
    s.execute(insert(ChatThread).values(
        business_id=subject.business_id, tag_kind=subject.kind, tag_ref=subject.ref,
        default_agent=chat_tags.owner_agent(s, subject)).on_conflict_do_nothing())
    return s.scalar(select(ChatThread).where(*_key(subject)).with_for_update())


def locked(s: Session, thread_id: int) -> ChatThread:
    return s.get(ChatThread, thread_id, with_for_update=True)


def _ref_id(thread: ChatThread) -> int | None:
    return int(thread.tag_ref) if thread.tag_ref.isdigit() else None


def append(s: Session, thread: ChatThread, *, from_agent: str, to_agent: str, kind: str, body: str = "",
           data: dict | None = None, scrubbed: list[dict] | None = None, in_reply_to: int | None = None,
           call_id: str | None = None, client_id: str | None = None, project_id: int | None = None) -> AgentMessage:
    """The thread's next line. The caller holds the thread's row lock (`thread_for`, `locked`), so `seq` has no gaps.
    A new line takes the thread out of the archive; the owner has read every line up to their own."""
    thread.last_seq += 1
    thread.last_message_at = func.now()
    thread.archived_at = None
    if from_agent == OWNER:
        thread.owner_read_seq = thread.last_seq
    m = AgentMessage(thread_id=thread.id, seq=thread.last_seq, business_id=thread.business_id, project_id=project_id,
                     from_agent=from_agent, to_agent=to_agent, kind=kind, ref_type=thread.tag_kind,
                     ref_id=_ref_id(thread), body=body, data=data or {}, scrubbed=scrubbed or [],
                     in_reply_to=in_reply_to, call_id=call_id, client_id=client_id)
    s.add(m)
    s.flush()
    return m


def status_line(s: Session, thread: ChatThread, key: str, *, in_reply_to: int | None = None, **vars: object) -> AgentMessage:
    """A line the server writes. Its values can carry what a command printed (a failed push's URL), so they are
    scrubbed like the owner's text: the dock shows them and the next reply's prompt reads them."""
    lang = app_settings.language()
    vars = {k: scrub(v, lang)[0] if isinstance(v, str) else v for k, v in vars.items()}
    return append(s, thread, from_agent=SYSTEM, to_agent=OWNER, kind="status", in_reply_to=in_reply_to,
                  data={"key": key, **({"vars": vars} if vars else {})})


def answered_seq(s: Session, thread_id: int, agent: str) -> int:
    """The last owner line `agent` has answered in the thread (0 before its first reply)."""
    return s.scalar(select(func.coalesce(func.max(AgentMessage.data["answered_seq"].as_integer()), 0)).where(
        AgentMessage.thread_id == thread_id, AgentMessage.from_agent.in_(roles.stored_ids(agent)))) or 0


def unanswered(s: Session, thread_id: int, agent: str) -> list[AgentMessage]:
    """The owner's lines to `agent` since its last reply, oldest first. A recall line is never one of them: it has its
    own reply."""
    return list(s.scalars(select(AgentMessage).where(
        AgentMessage.thread_id == thread_id, AgentMessage.from_agent == OWNER,
        AgentMessage.to_agent.in_(roles.stored_ids(agent)), AgentMessage.kind == "text",
        AgentMessage.data[("about", "kind")].as_string().is_distinct_from(RECALL),
        AgentMessage.seq > answered_seq(s, thread_id, agent)).order_by(AgentMessage.seq)))


def recall_answered(s: Session, thread_id: int, seq: int) -> bool:
    """Whether the recall line `seq` has its answer."""
    return s.scalar(select(AgentMessage.id).where(
        AgentMessage.thread_id == thread_id,
        AgentMessage.data["recall_answered"].as_integer() == seq).limit(1)) is not None


def scope_ids(s: Session, thread: ChatThread) -> dict:
    """What a reply job's payload names so the queue places it and its cost: the subject's project, or its business."""
    try:
        where = chat_tags.TAGS[thread.tag_kind].scope(s, thread.tag_ref, thread.business_id)
    except chat_tags.UnknownSubject:
        where = chat_tags.Scope(None, thread.business_id)
    if where.project_id is not None:
        return {"project_id": where.project_id}
    return {"business_id": where.business_id} if where.business_id is not None else {}


def enqueue_reply(s: Session, thread: ChatThread, agent: str, *, hop: int = 1, escalation: int | None = None,
                  recall: int | None = None) -> Job | None:
    """One waiting reply per agent per thread: the key is the line it last answered, so every owner line sent before
    the job runs joins it. An escalation's reply is keyed by its escalation line, and a recall's by its own line."""
    mark = (f"e{escalation}" if escalation is not None else f"r{recall}" if recall is not None
            else str(answered_seq(s, thread.id, agent)))
    payload = {"thread_id": thread.id, "agent": agent, "hop": hop, **scope_ids(s, thread),
               **({"escalation_id": escalation} if escalation is not None else {}),
               **({"recall_seq": recall} if recall is not None else {})}
    return queue.enqueue(s, "chat_reply", payload, idempotency_key=f"{REPLY}:{thread.id}:{agent}:{mark}", max_attempts=2)


def reply_gave_up(s: Session, job: Job) -> None:
    """A reply job that gave up or was cancelled: the conversation says no answer came, and the job lets go of its key
    so the owner's next line asks again."""
    job.idempotency_key = None
    if (thread := locked(s, job.payload["thread_id"])) is not None:
        status_line(s, thread, "chat.status.reply_failed")


# ------------------------------------------------------------------ the owner's writes

def _refuse(key: str, **vars: object) -> Refusal:
    return Refusal(said(key, **vars))


def walk_step(job: DevJob, about: dict) -> dict | None:
    """The step `about` names, as the walkthrough this build recorded for that commit holds it (in its setup or in any
    journey), or None when that commit has no run or the run has no such step."""
    for run in (job.result or {}).get("walkthroughs") or []:
        if run.get("head") == about.get("head"):
            found = [*(run.get("setup") or []), *(st for x in run.get("journeys") or [] for st in x.get("steps") or [])]
            return next((st for st in found if isinstance(st, dict) and st.get("id") == about.get("step")), None)
    return None


def _checked_recall(s: Session, subject: chat_tags.Subject, about: dict) -> dict:
    """A recall is of a task or a build that has a crew, and of a role with at least one turn on it."""
    role = about.get("role")
    if not isinstance(role, str) or role not in agents_config():
        raise _refuse("chat.refuse.bad_agent", agent=str(role))
    kind = {"task": staffing.SubjectKind.TASK, "dev_job": staffing.SubjectKind.BUILD}.get(subject.kind)
    bundle = staffing.recall_bundle(s, kind, int(subject.ref)) if kind is not None else None
    if bundle is None:
        raise _refuse("chat.refuse.recall_subject")
    if role not in bundle.roles:
        raise _refuse("chat.refuse.recall_role")
    return {"kind": RECALL, "role": role}


def _checked_about(s: Session, subject: chat_tags.Subject, about: dict) -> dict:
    """`about` as it is kept, or a refusal. A merge card's subject is its build (`chat_tags.resolve`), so a step may be
    attached to a build's conversation only, and only a step the build's walkthrough of that commit holds."""
    if about.get("kind") == RECALL:
        return _checked_recall(s, subject, about)
    job = s.get(DevJob, int(subject.ref)) if subject.kind == "dev_job" else None
    if about.get("kind") != STEP or job is None:
        raise _refuse("chat.refuse.about_subject")
    if not all(isinstance(about.get(k), str) and about[k] for k in ("step", "head")) or walk_step(job, about) is None:
        raise _refuse("chat.refuse.about_step")
    return {"kind": STEP, "step": about["step"], "head": about["head"]}


def send(s: Session, *, subject: chat_tags.Subject, to_agent: str | None, body: str, client_id: str | None,
         lang: Lang, about: dict | None = None) -> dict:
    """The owner's line: scrubbed, written in the subject's thread, and a reply job for its recipient. A `client_id`
    seen before in the thread returns that line again, so a double press or a retry sends once. `about` is a merge
    card's walkthrough step the line is about, which only tells the reply what the owner is looking at, or a recall of
    the role that worked on the subject, which goes to that role and only to it."""
    body = body.strip()
    if not body:
        raise _refuse("chat.refuse.empty")
    if len(body) > body_cap():
        raise _refuse("chat.refuse.too_long", n=body_cap())
    if to_agent is not None and to_agent not in agents_config():
        raise _refuse("chat.refuse.bad_agent", agent=to_agent)
    kept = _checked_about(s, subject, about) if about is not None else None
    recall = kept is not None and kept["kind"] == RECALL
    if recall and to_agent not in (None, kept["role"]):
        raise _refuse("chat.refuse.recall_to")
    thread = thread_for(s, subject)
    if client_id and (seen := s.scalar(select(AgentMessage).where(AgentMessage.thread_id == thread.id,
                                                                  AgentMessage.client_id == client_id))):
        return {"thread_id": thread.id, "message_id": seen.id, "seq": seen.seq, "scrubbed": seen.scrubbed}
    text, removed = scrub(body, lang)
    to = kept["role"] if recall else to_agent or roles.current(thread.default_agent)
    m = append(s, thread, from_agent=OWNER, to_agent=to, kind="text", body=text, scrubbed=removed, client_id=client_id,
               project_id=subject.scope.project_id, data={"about": kept} if kept else None)
    enqueue_reply(s, thread, to, recall=m.seq if recall else None)
    log.info("team chat: line %s in thread %s, removed %s", m.id, thread.id, removed or "nothing")
    return {"thread_id": thread.id, "message_id": m.id, "seq": m.seq, "scrubbed": removed}


def read(s: Session, thread_id: int, seq: int) -> ChatThread:
    """The owner has read up to `seq`. The pointer only moves forward."""
    thread = locked(s, thread_id)
    if thread is None:
        raise _refuse("chat.refuse.no_thread", id=thread_id)
    thread.owner_read_seq = max(thread.owner_read_seq, min(seq, thread.last_seq))
    return thread


def archive(s: Session, thread_id: int, archived: bool) -> ChatThread:
    """Archive or bring back a conversation. Nothing is deleted, and a new line brings it back too."""
    thread = locked(s, thread_id)
    if thread is None:
        raise _refuse("chat.refuse.no_thread", id=thread_id)
    thread.archived_at = func.now() if archived else None
    return thread


def cancel_task(s: Session, message_id: int, lang: Lang) -> dict:
    """Cancel the work a task line started, before it starts: its queued job is cancelled (queue.cancel_unstarted) and
    its fix round ends without touching the branch. A round already running stops the way every build does, by
    pausing the agent or the company."""
    from justshipit.engineering import revise  # revise imports the worker, which imports most services
    from justshipit.db.models import DevJob
    m = s.get(AgentMessage, message_id)
    work = (m.data or {}).get("work") if m is not None and m.kind == "task" else None
    if not work or work.get("kind") != "dev_fix" or not isinstance(work.get("n"), int):
        raise _refuse("chat.refuse.no_task", id=message_id)
    job = s.get(DevJob, work["dev_job_id"], with_for_update=True)
    run = s.scalar(select(Job).where(Job.idempotency_key == f"{revise.RUN}:{job.id}:{work['n']}").with_for_update())
    if run is not None and run.status not in queue.UNSTARTED:
        raise _refuse("chat.refuse.started")
    if not revise.cancel(job, work["n"], msg("chat.status.round_cancelled", lang)):
        raise _refuse("chat.refuse.started")
    if run is not None:
        queue.cancel_unstarted(s, run, lang)
    status_line(s, locked(s, m.thread_id), "chat.status.round_cancelled", in_reply_to=m.id)
    return {"message_id": m.id, "state": "cancelled"}


def round_line(s: Session, job, key: str, *, n: int | None = None, **vars: object) -> None:
    """Fix round `n` (the latest when None) of a chat line changed state: one status line in that conversation, under
    its task line."""
    from justshipit.engineering import revise  # revise imports the worker, which imports most services
    fix = revise.latest(job) if n is None else next((f for f in revise.rounds(job) if f["n"] == n), None)
    asked_in = (fix or {}).get("asked_in")
    task = s.get(AgentMessage, asked_in) if isinstance(asked_in, int) else None
    if task is None or task.thread_id is None:
        return
    status_line(s, locked(s, task.thread_id), key, in_reply_to=task.id, **vars)


# ------------------------------------------------------------------ what the dock reads

def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _sender(agent: str) -> dict:
    """Name and role: the agent id and its department, which the console words from its catalog."""
    cfg = agents_config().get(agent) or {}
    return {"id": agent, "department": cfg.get("department")}


def line_text(m: AgentMessage, lang: Lang) -> str | None:
    """The words of a line the server wrote, in `lang`, a recalled role's "No record" among them; None for a line
    someone typed."""
    data = m.data or {}
    if m.kind in ("status", "text") and data.get("key"):
        try:
            return msg(data["key"], lang, **(data.get("vars") or {}))
        except KeyError:
            return None
    if m.kind in ("handoff", "escalation"):
        return msg(f"chat.line.{m.kind}", lang, **{"from": glossary.role(data.get("from"), lang)},
                   to=glossary.role(data.get("to"), lang), why=data.get("why") or "")
    if m.kind == "decision" and isinstance(data.get("request_id"), int):
        return msg("chat.status.decision_filed", lang, ref=f"REQ-{data['request_id']}")
    return None


def view(s: Session, m: AgentMessage, lang: Lang) -> dict:
    data = m.data or {}
    out = {"id": m.id, "seq": m.seq, "at": m.created_at, "kind": m.kind, "from": _sender(m.from_agent),
           "to": _sender(m.to_agent), "body": m.body, "text": line_text(m, lang), "scrubbed": m.scrubbed or [],
           "in_reply_to": m.in_reply_to}
    if m.kind == "task":
        work = data.get("work") or {}
        out["task"] = {"title": data.get("title") or "", "work": work, "branch": data.get("branch"),
                       "state": chat_tags.task_state(s, work) or {"state": "received", "who": [m.from_agent]}}
    if m.kind == "decision":
        out["decision"] = {"request_id": data.get("request_id")}
    if "recall" in data:
        out["recall"] = data["recall"]
    return out


def _replying(s: Session, thread_id: int) -> list[dict]:
    """The replies on their way: each in-flight reply job's agent, and what it waits on when it waits."""
    jobs = s.scalars(select(Job).where(Job.kind == REPLY, Job.payload["thread_id"].as_integer() == thread_id,
                                       queue.in_flight()).order_by(Job.id))
    return [{"agent": j.payload.get("agent"), "status": j.status, "wait_reason": j.wait_reason,
             "run_after": j.run_after if j.wait_reason else None, "waits_on": j.waits_on} for j in jobs]


def thread_view(s: Session, thread: ChatThread, lang: Lang) -> dict:
    last = s.scalar(select(AgentMessage).where(AgentMessage.thread_id == thread.id)
                    .order_by(AgentMessage.seq.desc()).limit(1))
    unread = s.scalar(select(func.count()).where(AgentMessage.thread_id == thread.id,
                                                 AgentMessage.seq > thread.owner_read_seq,
                                                 AgentMessage.from_agent != OWNER)) or 0
    tasks = [view(s, m, lang)["task"]["state"]["state"] for m in s.scalars(select(AgentMessage).where(
        AgentMessage.thread_id == thread.id, AgentMessage.kind == "task"))]
    recalled = s.scalar(select(AgentMessage.id).where(AgentMessage.thread_id == thread.id,
                                                      AgentMessage.data.has_key("recall")).limit(1)) is not None
    return {"id": thread.id, "subject": {"kind": thread.tag_kind, "ref": thread.tag_ref},
            "business_id": thread.business_id, "facts": chat_tags.facts(s, thread.tag_kind, thread.tag_ref),
            "default_agent": _sender(thread.default_agent), "last_seq": thread.last_seq,
            "owner_read_seq": thread.owner_read_seq, "unread": unread > 0, "unread_count": unread,
            "archived": thread.archived_at is not None, "last_message_at": thread.last_message_at,
            "last": {"from": _sender(last.from_agent),
                     "text": _clip(line_text(last, lang) or last.body, _LAST_CHARS)} if last else None,
            "open_tasks": [x for x in tasks if x not in _DONE], "recall": recalled}


def threads(s: Session, business_id: int | None, lang: Lang, *, archived: bool = False) -> list[dict]:
    """The business's conversations and the company's General, General first, then by last line."""
    q = select(ChatThread).where(ChatThread.archived_at.is_not(None) if archived else ChatThread.archived_at.is_(None))
    if business_id is not None:
        q = q.where(ChatThread.business_id.is_(None) | (ChatThread.business_id == business_id))
    rows = s.scalars(q.order_by(ChatThread.last_message_at.desc().nulls_last())).all()
    return [thread_view(s, t, lang) for t in sorted(rows, key=lambda t: t.tag_kind != "general")]


def subject(s: Session, sub: chat_tags.Subject, lang: Lang) -> dict:
    """What the dock shows for a screen's subject before anything is written: its facts, who answers, the work it takes
    and its thread when it has one."""
    thread = find_thread(s, sub)
    return {"subject": {"kind": sub.kind, "ref": sub.ref}, "business_id": sub.business_id,
            "facts": chat_tags.facts(s, sub.kind, sub.ref),
            "default_agent": _sender(thread.default_agent if thread else chat_tags.owner_agent(s, sub)),
            "work": list(chat_tags.work(s, sub)), "thread_id": thread.id if thread else None}


def messages(s: Session, thread_id: int, lang: Lang, *, before: int | None = None, limit: int = 50) -> dict:
    thread = s.get(ChatThread, thread_id)
    if thread is None:
        raise _refuse("chat.refuse.no_thread", id=thread_id)
    q = select(AgentMessage).where(AgentMessage.thread_id == thread_id)
    if before is not None:
        q = q.where(AgentMessage.seq < before)
    rows = list(s.scalars(q.order_by(AgentMessage.seq.desc()).limit(limit)))[::-1]
    return {"thread": thread_view(s, thread, lang), "messages": [view(s, m, lang) for m in rows],
            "more": bool(rows) and rows[0].seq > 1, "replying": _replying(s, thread_id)}


def agents(lang: Lang) -> list[dict]:
    """Who the owner can address with @, in the roster's order."""
    return [{**_sender(a), "name": glossary.role(a, lang)} for a in agents_config()]
