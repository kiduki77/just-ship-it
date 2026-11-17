"""The `chat_reply` job: one agent answers the owner's new lines in a team chat conversation.

The agent reads the subject's records and the conversation between data fences, in one isolated call recorded under its
own role. The engineer, whose configured runtime is a writable container, answers as a reader instead: an export of
the build's branch with Read, Glob and Grep only, the view the code reviewer and the product agent get. The reply's
words go into the thread scrubbed of secrets. Its intent is a request the server checks (`chat_tags.allowed`): a
disallowed one is an answer, and the log says why. Work becomes a fix round on the build's own branch
(`revise.from_chat`), an escalation or handoff is a line and the next agent's reply, and a decision is a card the owner
decides. Nothing here approves, merges or pushes.

A run again after its reply was written finds the reply and does nothing, and owner lines that arrived while it ran get
the next reply job.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.agents import roles
from justshipit.agents.executor import last_call_id, run_agent
from justshipit.contracts.card_context import AfterLine, CardContext, Ref, Step
from justshipit.contracts.chat import ChatReplyOut
from justshipit.contracts.handoff import DevJobStatus
from justshipit.contracts.origins import OriginKind, ServerOrigin
from justshipit.contracts.requests import RequestType
from justshipit.db.models import AgentMessage, AgentRequest, ChatThread, DevJob, Spec
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, glossary, msg
from justshipit.services import app_settings, chat, chat_recall, chat_tags, requests, work_path
from justshipit.services.fence import fenced
from justshipit.settings import agents_config, settings

log = logging.getLogger(__name__)
PURPOSE = "chat"


def _system(agent: str) -> str:
    cfg = agents_config()[agent]
    return (settings().prompts_dir / "chat_reply.md").read_text(encoding="utf-8").format(
        name=glossary.role(agent, "en"), department=glossary.DEPARTMENTS.get(cfg.get("department"), {}).get("en", ""))


def _line(m: AgentMessage, chars: int) -> str:
    who = "Owner" if m.from_agent == chat.OWNER else glossary.role(m.from_agent, "en")
    text = chat.line_text(m, "en") or m.body
    if m.kind == "task":
        text = f"[task card] {(m.data or {}).get('title', '')}"
    return f"[{m.seq}] {who}: {text[:chars]}"


def _brief_card(card: dict) -> dict:
    """The merge card without what each walkthrough step printed and the sample inputs: those run to tens of thousands
    of characters, and a step the owner asks about comes in whole on its own (`step_records`)."""
    run = card.get("walkthrough")
    if not run:
        return card

    def brief(st: dict) -> dict:
        return {k: v for k, v in st.items() if k not in ("capture", "seed")}
    return {**card, "walkthrough": {**run, "setup": [brief(x) for x in run.get("setup") or []],
                                    "journeys": [{**j, "steps": [brief(x) for x in j.get("steps") or []]}
                                                 for j in run.get("journeys") or []]}}


def build_records(s: Session, job: DevJob, lang: Lang) -> str:
    from justshipit.engineering import merge_review, revise, status, try_it  # the worker imports this lane's services
    spec = s.get(Spec, job.spec_id)
    repo = chat_tags._repository(spec) or ""
    steps = [f"- {x['status']}: {x['say'] or ''}".rstrip() for x in status.steps(job, lang)[-15:]]
    fix = revise.latest(job)
    return "\n\n".join([
        f"Build J-{job.id} of {spec.spec_key} \"{spec.title}\", status {job.status}, branch {job.branch or '(none)'}.",
        fenced("merge card review", json.dumps(_brief_card(merge_review.card(job, repo)), ensure_ascii=False,
                                               default=str)),
        fenced("try it", json.dumps(try_it.card(job, repo), ensure_ascii=False, default=str)),
        fenced("build log", "\n".join(steps)),
        fenced("latest fix round", json.dumps({k: fix.get(k) for k in ("n", "state", "note", "error")}
                                              if fix else None, ensure_ascii=False)),
    ])


_HOLD_SCOPE = {"none": "nothing waits on it", "tasks": "the tasks below wait for the answer",
               "planning": "the project's next plan waits, and work already started goes on",
               "run": "the whole project waits while it is open"}
_HELD_TASKS = 5


def _ref_text(ref: Ref) -> str:
    return f"{ref.kind} {ref.id}" + (f" \"{ref.label}\"" if ref.label else "") + (f" ({ref.state})" if ref.state else "")


def _step_text(step: Step) -> str:
    """One link of the chain for the model, with the values the card shows. A step the records lack says so."""
    if step.known == "unknown":
        return f"{step.kind}: not recorded"
    _, _, sub = step.label_key.removeprefix("path.step.").partition(".")
    name = f"{step.kind} ({sub})" if sub else step.kind
    detail = " ".join(str(x) for x in (step.ref, *step.vars.values()) if x not in (None, ""))
    tags = [x for x in (step.state, step.known if step.known in ("matched", "backfilled") else None) if x]
    return (f"{name}: {detail}" if detail else name) + "".join(f" [{x}]" for x in tags)


def _after_text(line: AfterLine) -> str:
    if line.source == "server":
        return f"the server: {line.text}"
    return (f"{line.source}: {line.effect or 'an effect'}"
            + (f" for answer {line.option_key}" if line.option_key else "")
            + (f" ({'; '.join(_ref_text(x) for x in line.targets)})" if line.targets else ""))


def _card_text(ctx: CardContext) -> str:
    """The card's place in the work as plain English lines, from the read its own screen shows. It is short: the card's
    context, not the graph. What the records lack reads `not recorded`, so the model never guesses it."""
    why = ctx.why
    note = f" Note by the agent: {why.note}" if why.note else ""
    stale = " The note may be out of date: what it cites has changed since." if why.stale else ""
    reason = msg(f"path.reason.{why.reason}", "en") if why.reason else "not recorded"
    lines = [f"Why it is asked: {reason} (asked by {glossary.role(why.agent, 'en')} on {why.at:%Y-%m-%d}).{note}{stale}"]
    if why.refs:
        lines.append("It is based on: " + "; ".join(_ref_text(x) for x in why.refs) + ".")
    lines += ["Where it sits, from the goal down to this card:", *(f"- {_step_text(x)}" for x in ctx.chain)]
    if ctx.also_asked:
        lines.append("Also asked by: " + "; ".join(_step_text(x) for x in ctx.also_asked) + ".")
    if (held := ctx.holds) is not None:
        lines.append(f"Holding up: {_HOLD_SCOPE[held.scope.value]}."
                     + (" Nothing it held still waits." if held.nothing_live else ""))
        lines += [f"- {x.key} \"{x.title}\" ({x.phase.value}, {'waiting' if x.live else 'ended'})"
                  for x in held.tasks[:_HELD_TASKS]]
        if len(held.tasks) > _HELD_TASKS:
            lines.append(f"- and {len(held.tasks) - _HELD_TASKS} more")
        if held.behind:
            lines.append(f"{held.behind} more tasks come after them.")
    lines.append("What happened after the answer:" if ctx.decided else "After the answer:")
    lines += [f"- {_after_text(x)}" for x in ctx.after] or ["- not recorded"]
    if (lapse := ctx.unanswered) is not None:
        lines.append(f"If nobody answers: rule {lapse.policy.value if lapse.policy else 'not recorded'}"
                     + (f", on {lapse.when:%Y-%m-%d}" if lapse.when else "")
                     + (f", the default is \"{lapse.default_option}\"" if lapse.default_option else "") + ".")
    if not ctx.complete:
        lines.append("Some of this was not recorded when the card was filed. Say so; do not fill it in.")
    return "\n".join(lines)


def card_records(s: Session, req: AgentRequest) -> str:
    """The card the owner asks about and its place in the work, as its own screen reads it. Both go between fences:
    the agent's note and the titles in them are text an agent wrote."""
    ctx = work_path.card_or_none(s, req, says=requests.on_approve(s, req, "en") if req.status == "PENDING" else None)
    return "\n\n".join([
        fenced("the decision card", f"{req.title}\n\n{req.summary}\n\nStatus: {req.status}"),
        fenced("where the card sits in the work", _card_text(ctx)) if ctx is not None
        else "The card's place in the work could not be read just now. Say that, and do not guess it."])


def step_records(job: DevJob, asked: list[AgentMessage]) -> list[str]:
    """The walkthrough steps the owner's lines are about, as the build recorded them: what the step asks, what ran and
    what it printed. The capture is what the product printed, so it is data between a fence like the rest."""
    steps = {(a.get("step"), a.get("head")): a for m in asked if isinstance(a := (m.data or {}).get("about"), dict)}
    out = []
    for about in steps.values():
        if (st := chat.walk_step(job, about)) is not None:
            cap = st.get("capture") or {}
            out.append(fenced(f"walkthrough step {st['id']}", json.dumps(
                {k: st.get(k) for k in ("id", "do", "see", "action", "state", "not_run")}
                | {"printed": cap.get("text"), "exit": cap.get("exit")}, ensure_ascii=False)))
    return out


def _subject_records(s: Session, thread: ChatThread, lang: Lang, asked: list[AgentMessage] | None = None) -> str:
    facts = chat_tags.facts(s, thread.tag_kind, thread.tag_ref)
    out = [f"Subject: {thread.tag_kind} {thread.tag_ref}".rstrip(), fenced("subject facts", json.dumps(facts, ensure_ascii=False))]
    try:
        out += chat_tags.TAGS[thread.tag_kind].records(s, thread.tag_ref, lang, asked or [])
    except chat_tags.UnknownSubject:
        pass  # the record is gone, and the facts above say so by being empty
    return "\n\n".join(out)


def _branch(s: Session, thread: ChatThread) -> tuple[Path, str] | None:
    """The product repo and branch of the build a conversation is about, when it has one to read."""
    job = s.get(DevJob, int(thread.tag_ref)) if thread.tag_kind == "dev_job" else None
    spec = s.get(Spec, job.spec_id) if job is not None else None
    name = chat_tags._repository(spec) if spec is not None else None
    if job is None or not job.branch or not name:
        return None
    repo = settings().products_dir / name
    return (repo, job.branch) if (repo / ".git").exists() else None


@contextmanager
def _branch_copy(where: tuple[Path, str] | None) -> Iterator[Path | None]:
    """A read-only copy of the build's branch for the engineer to read: its files with no .git and no links."""
    from justshipit.engineering import worker  # the worker imports this lane's services
    if where is None:
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="ship-chat-", ignore_cleanup_errors=True) as export:
        os.chmod(export, 0o755)  # the reader's container user is not this one, and mkdtemp makes it 0700
        try:
            worker._export_branch(where[0], where[1], Path(export))
            worker.drop_links(Path(export))
        except (RuntimeError, OSError) as exc:
            log.warning("team chat: no copy of branch %s: %s", where[1], exc)
            yield None
            return
        yield Path(export)


def _prompt(s: Session, thread: ChatThread, agent: str, asked: list[AgentMessage], escalation: AgentMessage | None,
            lang: Lang, work: tuple[str, ...], hop: int) -> str:
    cfg = chat.config()
    history = list(s.scalars(select(AgentMessage).where(AgentMessage.thread_id == thread.id)
                             .order_by(AgentMessage.seq.desc()).limit(int(cfg.get("history", 20)))))[::-1]
    chars = int(cfg.get("line_chars", 600))
    roles = ", ".join(a for a in agents_config() if a != agent)
    parts = [
        "## What the conversation is about", _subject_records(s, thread, lang, asked),
        "## The conversation so far", fenced("conversation", "\n".join(_line(m, chars) for m in history) or "(none)"),
    ]
    if escalation is not None:
        data = escalation.data or {}
        parts += [f"## {glossary.role(data.get('from'), 'en')} brought this to you",
                  fenced("why", data.get("why") or "")]
    parts += ["## The owner's new messages to answer",
              fenced("the owner's messages", "\n\n".join(m.body for m in asked) or "(see the conversation)"),
              "## Your reply",
              "Answer the newest messages. Work this subject takes from chat: "
              + (", ".join(work) or "none, so do not choose intent work")
              + f". Role keys for escalate and handoff: {roles}."
              + (" This message has no replies left after yours, so do not escalate or hand off." if hop >= chat.max_hops()
                 else "")]
    return "\n\n".join(parts)


def run(thread_id: int, agent: str, *, hop: int = 1, escalation: int | None = None, recall: int | None = None) -> None:
    if recall is not None:
        chat_recall.run(thread_id, recall)
        return
    agent = roles.current(agent)
    lang = app_settings.language()
    with session_scope() as s:
        thread = s.get(ChatThread, thread_id)
        if thread is None:
            return
        esc = s.get(AgentMessage, escalation) if escalation is not None else None
        if esc is not None:
            if _answered_escalation(s, thread_id, escalation):
                return
            asked = _escalated_lines(s, esc)
        else:
            asked = chat.unanswered(s, thread_id, agent)
            if not asked:
                return
        try:
            sub = chat_tags.Subject(thread.tag_kind, thread.tag_ref,  # type: ignore[arg-type]
                                    chat_tags.TAGS[thread.tag_kind].scope(s, thread.tag_ref, thread.business_id))
            work = chat_tags.work(s, sub)
        except chat_tags.UnknownSubject:
            sub, work = chat_tags.Subject(thread.tag_kind, thread.tag_ref, chat_tags.Scope(None, thread.business_id)), ()  # type: ignore[arg-type]
        prompt = _prompt(s, thread, agent, asked, esc, lang, work, hop)
        upto, asked_ids = asked[-1].seq if asked else 0, [m.id for m in asked]
        where = _branch(s, thread)
    with _branch_copy(where) as copy:
        out = run_agent(agent, PURPOSE, prompt, ChatReplyOut, project_id=sub.scope.project_id,
                        business_id=None if sub.scope.project_id is not None else sub.scope.business_id, cwd=copy,
                        # Read the build's branch, or nothing: pasted text must not steer a fetch.
                        runtime="isolated", tools=["read_workspace"] if copy is not None else [],
                        model_name=chat.config().get("reply_model"), system_override=_system(agent))
    call_id = last_call_id()
    with session_scope() as s:
        apply(s, thread_id, agent, out, hop=hop, escalation=escalation, upto=upto, asked_ids=asked_ids,
              call_id=call_id, lang=lang)


def _answered_escalation(s: Session, thread_id: int, escalation: int) -> bool:
    return s.scalar(select(AgentMessage.id).where(
        AgentMessage.thread_id == thread_id,
        AgentMessage.data["escalation_id"].as_integer() == escalation).limit(1)) is not None


def _escalated_lines(s: Session, esc: AgentMessage) -> list[AgentMessage]:
    """The owner's lines the reply that escalated answered."""
    ids = (esc.data or {}).get("asked") or []
    return list(s.scalars(select(AgentMessage).where(AgentMessage.id.in_(ids)).order_by(AgentMessage.seq))) if ids else []


def apply(s: Session, thread_id: int, agent: str, out: ChatReplyOut, *, hop: int, escalation: int | None, upto: int,
          asked_ids: list[int], call_id: str | None, lang: Lang) -> AgentMessage | None:
    """Write the reply and carry out its intent, if the rule allows it. Returns the reply line, or None when an earlier
    run of this job already wrote it."""
    subject = s.get(ChatThread, thread_id)
    if subject.tag_kind == "dev_job":
        # The build before its conversation, the order every round transition takes them in (revise, worker._accept,
        # chat.cancel_task): taking the conversation first deadlocked with a round moving at the same time.
        s.get(DevJob, int(subject.tag_ref), with_for_update=True)
    thread = chat.locked(s, thread_id)
    if escalation is not None and _answered_escalation(s, thread_id, escalation):
        return None
    if escalation is None and chat.answered_seq(s, thread_id, agent) >= upto:
        return None
    intent = out.intent
    if not chat_tags.allowed(agent, intent, thread.tag_kind, hop=hop, max_hops=chat.max_hops()):
        log.info("team chat: %s asked for %s on %s:%s at hop %s; answered instead", agent, intent, thread.tag_kind,
                 thread.tag_ref, hop)
        intent = "answer"
    project_id = chat.scope_ids(s, thread).get("project_id")
    text, removed = chat.scrub(out.reply, lang)
    mark = {"escalation_id": escalation} if escalation is not None else {"answered_seq": upto}
    reply = chat.append(s, thread, from_agent=agent, to_agent=chat.OWNER, kind="text", body=text, scrubbed=removed,
                        in_reply_to=asked_ids[-1] if asked_ids else None, call_id=call_id, project_id=project_id,
                        data={**mark, "hop": hop, "intent": intent})
    if intent == "work" and out.work is not None:
        _work(s, thread, agent, out, asked_ids, lang)
    elif intent in ("escalate", "handoff"):
        _pass_on(s, thread, agent, out, intent, hop, asked_ids, reply, lang)
    elif intent == "decision" and out.decision is not None:
        _decision(s, thread, agent, out, project_id, reply)
    if escalation is None and chat.unanswered(s, thread_id, agent):
        chat.enqueue_reply(s, thread, agent)  # lines that came while this one ran
    return reply


def _work(s: Session, thread: ChatThread, agent: str, out: ChatReplyOut, asked_ids: list[int], lang: Lang) -> None:
    from justshipit.engineering import revise  # revise imports the worker, which imports this lane's services
    job = s.get(DevJob, int(thread.tag_ref), with_for_update=True)
    if job.status == DevJobStatus.MERGED.value:
        chat.status_line(s, thread, "chat.status.merged")
        return
    if job.status != DevJobStatus.DELIVERED.value:
        chat.status_line(s, thread, "chat.status.not_delivered")
        return
    if revise.busy(s, job):
        chat.status_line(s, thread, "chat.status.round_busy", in_reply_to=asked_ids[-1] if asked_ids else None)
        return
    title, _ = chat.scrub(out.work.title, lang)
    instructions, _ = chat.scrub(out.work.instructions, lang)
    owner_words = [m.body for m in s.scalars(select(AgentMessage).where(AgentMessage.id.in_(asked_ids))
                                             .order_by(AgentMessage.seq))]
    task = chat.append(s, thread, from_agent=agent, to_agent=chat.OWNER, kind="task",
                       in_reply_to=asked_ids[-1] if asked_ids else None, project_id=s.get(Spec, job.spec_id).project_id,
                       data={"title": title[:200], "work": {"kind": "dev_fix", "dev_job_id": job.id},
                             "branch": job.branch})
    if revise.from_chat(s, job, f"## {title}\n{instructions}", "\n\n".join(owner_words), task.id, lang) is None:
        chat.status_line(s, thread, "chat.status.no_round", in_reply_to=task.id)


def _pass_on(s: Session, thread: ChatThread, agent: str, out: ChatReplyOut, intent: str, hop: int,
             asked_ids: list[int], reply: AgentMessage, lang: Lang) -> None:
    """An escalation to the CEO, or the CEO's handoff to the role that owns it: a quiet line, then that agent's reply."""
    to = "project_manager" if intent == "escalate" else out.to
    if to not in agents_config() or to == agent:
        return
    why, _ = chat.scrub(out.why or "", lang)
    line = chat.append(s, thread, from_agent=agent, to_agent=to, kind="handoff" if intent == "handoff" else "escalation",
                       in_reply_to=reply.id, data={"from": agent, "to": to, "why": why[:300], "asked": asked_ids})
    chat.enqueue_reply(s, thread, to, hop=hop + 1, escalation=line.id)


def _decision(s: Session, thread: ChatThread, agent: str, out: ChatReplyOut, project_id: int | None,
              reply: AgentMessage) -> None:
    """A card the owner decides, filed as any agent files one, with dedupe, but never settled by the policy or the
    CEO's door rules: a pasted log can steer what the reply files. A development ask would start a product plan, which
    chat never changes, so none is filed. Nothing is decided here."""
    if out.decision.type == RequestType.DEVELOPMENT or out.decision.action_kind == requests.DEV_ASK:
        log.info("team chat: %s's development ask was not filed: chat starts no plan", agent)
        return
    lang = app_settings.language()
    title, summary, rationale = (chat.scrub(x, lang)[0] for x in (out.decision.title, out.decision.summary,
                                                                   out.decision.rationale))
    # A card filed from chat never rewrites another card.
    draft = out.decision.model_copy(update={"replaces": None, "title": title, "summary": summary, "rationale": rationale})
    try:
        req = requests.create(s, draft, from_agent=agent, project_id=project_id, owner_decides=True,
                              origin=ServerOrigin(OriginKind.CHAT, reply.id))
    except ValueError as exc:
        log.info("team chat: %s's decision card was refused: %s", agent, exc)
        return
    if isinstance(req, AgentRequest):
        s.flush()
        chat.append(s, thread, from_agent=agent, to_agent=chat.OWNER, kind="decision", in_reply_to=reply.id,
                    data={"request_id": req.id}, project_id=project_id)
