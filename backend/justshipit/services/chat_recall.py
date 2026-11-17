"""The recall reply (design section 8): the role that worked on a task or build answers the owner's question about it
from the records kept for it (`staffing.recall_bundle`), and from nothing else.

A recall line has its own reply job (`chat.enqueue_reply(recall=seq)`) and its own answered marker, so it never joins
an ordinary reply and an ordinary reply never marks it answered. The role reads its records between data fences in
one isolated call with no tools, under its own role and its own brief. The server then checks the answer (`check`): a
reply that cites nothing, or cites an id the records do not hold, becomes the catalog's "No record". The server cannot
check that a cited record says what the reply says, which is why the line keeps its citations for the owner to open.

The contract has no intent and this job carries out no effect, so a recalled reply causes nothing: no fix round,
escalation, handoff or card, whatever a recorded turn says. The line records its intent as `answer`, the one intent
`chat_tags.allowed(recall=True)` permits. A run again after the answer was written finds the answer and does nothing.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit import staffing
from justshipit.agents.executor import last_call_id, run_agent
from justshipit.contracts.chat import RecallReplyOut
from justshipit.db.models import AgentMessage, ChatThread
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, glossary
from justshipit.services import app_settings, chat
from justshipit.services.fence import fenced
from justshipit.settings import agents_config, settings

PURPOSE = "chat"
NO_RECORD = "chat.recall.no_record"
_KINDS = {"task": staffing.SubjectKind.TASK, "dev_job": staffing.SubjectKind.BUILD}


@dataclass(frozen=True)
class Checked:
    reply: str | None  # None: the server's "No record" replaces what the role wrote
    cites: tuple[str, ...]
    no_record: bool


def check(out: RecallReplyOut, ids: frozenset[str]) -> Checked:
    """The reply as the owner gets it. Kept only when it cites at least one record and every id it cites is one the
    role was shown; otherwise "No record", never words the records do not back."""
    cites = tuple(dict.fromkeys(c.strip().strip("[]") for c in out.cites))
    if not cites or any(c not in ids for c in cites):
        return Checked(None, (), True)
    return Checked(out.reply, cites, out.no_record)


def _system(role: str) -> str:
    brief = (settings().prompts_dir / "agents" / agents_config()[role]["prompt"]).read_text(encoding="utf-8")
    return (settings().prompts_dir / "chat_recall.md").read_text(encoding="utf-8").format(
        name=glossary.role(role, "en"), brief=brief)


def _prompt(bundle: staffing.Bundle, question: str) -> str:
    """The records and the question, and nothing of the conversation: an earlier answer is not a record, and the
    answer has to be the same whenever it is asked."""
    subject = f"{'Task' if bundle.kind is staffing.SubjectKind.TASK else 'Build'} {bundle.records[0].id}"
    state = ("It is still in progress: the records stop where the work is now." if bundle.in_progress
             else "It has ended.")
    return "\n\n".join([
        f"## The work\n\n{subject}. {state}",
        "## The records kept for it", fenced("records", bundle.text()),
        "## The owner's question", fenced("the owner's question", question),
        "## Your reply\n\nAnswer from the records above, citing their ids.",
    ])


def _asked(s: Session, thread_id: int, seq: int) -> AgentMessage | None:
    return s.scalar(select(AgentMessage).where(
        AgentMessage.thread_id == thread_id, AgentMessage.seq == seq, AgentMessage.from_agent == chat.OWNER,
        AgentMessage.data[("about", "kind")].as_string() == chat.RECALL))


def run(thread_id: int, seq: int) -> None:
    lang = app_settings.language()
    with session_scope() as s:
        thread = s.get(ChatThread, thread_id)
        line = _asked(s, thread_id, seq) if thread is not None else None
        if line is None or chat.recall_answered(s, thread_id, seq) or thread.tag_kind not in _KINDS:
            return
        bundle = staffing.recall_bundle(s, _KINDS[thread.tag_kind], int(thread.tag_ref))
        if bundle is None:
            return
        role = line.data["about"]["role"]
        prompt = _prompt(bundle, line.body)
        where = chat.scope_ids(s, thread)
    out = run_agent(role, PURPOSE, prompt, RecallReplyOut, project_id=where.get("project_id"),
                    business_id=where.get("business_id"), runtime="isolated", tools=[],
                    model_name=chat.config().get("reply_model"), system_override=_system(role))
    call_id = last_call_id()
    with session_scope() as s:
        apply(s, thread_id, role, out, seq=seq, ids=bundle.ids, in_progress=bundle.in_progress, call_id=call_id,
              lang=lang)


def apply(s: Session, thread_id: int, agent: str, out: RecallReplyOut, *, seq: int, ids: frozenset[str],
          in_progress: bool, call_id: str | None, lang: Lang) -> AgentMessage | None:
    """Write the checked answer under the recall line, marked as its answer. Returns the line, or None when an earlier
    run already wrote it."""
    thread = chat.locked(s, thread_id)
    if chat.recall_answered(s, thread_id, seq):
        return None
    asked = _asked(s, thread_id, seq)
    checked = check(out, ids)
    text, removed = chat.scrub(checked.reply, lang) if checked.reply is not None else ("", [])
    return chat.append(s, thread, from_agent=agent, to_agent=chat.OWNER, kind="text", body=text, scrubbed=removed,
                       in_reply_to=asked.id if asked is not None else None, call_id=call_id,
                       project_id=chat.scope_ids(s, thread).get("project_id"),
                       data={"recall_answered": seq, "hop": 1, "intent": "answer",
                             "recall": {"rebuilt": True, "cites": list(checked.cites), "no_record": checked.no_record,
                                        "in_progress": in_progress},
                             **({"key": NO_RECORD} if checked.reply is None else {})})
