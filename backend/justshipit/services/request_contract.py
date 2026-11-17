"""The request contract on the server (docs/design/work-path.md 2): a new card's origin, reason, holds, answer uses and
expiry rule, checked against ORIGIN_RULES and TYPE_RULES before anything is stored, and the `request_links` rows that
record them. services/requests.py calls in here, in the transaction that writes the card; nothing else writes links."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from justshipit.contracts.origins import ORIGIN_RULES, TYPE_RULES, AgentRule, Origin, ServerRule, TypeRule
from justshipit.contracts.requests import AgentRequestDraft, HumanDecision, RequestType
from justshipit.contracts.work import TERMINAL_TASK_STATUSES
from justshipit.contracts.work_path import (
    HOLD_SCOPE, AnswerEffect, AskReason, ExpiryPolicy, FollowedEffect, HoldKind, HoldPhase, HoldScope, Known,
    LinkSource, RefKind, Rel, RequestContext, TargetKind,
)
from justshipit.db.models import AgentRequest, Evidence, LLMCallRecord, RequestLink, Task
from justshipit.i18n import Lang
from justshipit.i18n.said import Refusal, Said, said, say
from justshipit.services import owner_tasks, policy
from justshipit.services.tasks import slug
from justshipit.services.verdicts import infer_verdict

_QUESTIONS = {RequestType.QUESTION, RequestType.CLARIFICATION, RequestType.ESCALATION}
_TERMINAL = {st.value for st in TERMINAL_TASK_STATUSES}


class RefusalKey(StrEnum):
    CONTEXT_MISSING = "context_missing"
    RUN_NOT_GOAL_UNCLEAR = "run_not_goal_unclear"
    REFS_EMPTY = "refs_empty"
    REF_MISSING = "ref_missing"
    HOLD_NOT_ALLOWED = "hold_not_allowed"
    HOLD_TARGET_IS_ANCESTOR = "hold_target_is_ancestor"
    HOLD_TARGET_TERMINAL = "hold_target_terminal"
    HOLD_TARGET_UNKNOWN = "hold_target_unknown"
    ANSWER_TARGET_UNKNOWN = "answer_target_unknown"
    EXPIRY_NOT_ALLOWED = "expiry_not_allowed"
    EXPIRY_CANNOT_APPROVE = "expiry_cannot_approve"
    DEFAULT_NOT_ALLOWED = "default_not_allowed"
    DEFAULT_NOT_RECOMMENDED = "default_not_recommended"
    DEFAULT_DECLINES = "default_declines"
    DEVELOPMENT_HOLDS_SERVER_OWNED = "development_holds_server_owned"
    OWNER_WORK_MISSING = "owner_work_missing"


@dataclass(frozen=True)
class Refused:
    """One reason the server does not file a request: a catalog key (`path.refused.<key>`) and what it names."""
    key: RefusalKey
    vars: dict[str, str] = field(default_factory=dict)

    def said(self) -> Said:
        return said(f"path.refused.{self.key.value}", **self.vars)


class RequestRefused(Refusal):
    """A request `requests.create` did not store: no row and no links were written."""

    def __init__(self, refusals: list[Refused]) -> None:
        self.refusals = refusals
        super().__init__(refusals[0].said())

    @property
    def keys(self) -> list[str]:
        return [r.key.value for r in self.refusals]

    def words(self, lang: Lang) -> str:
        return " ".join(say(r.said(), lang) for r in self.refusals)


@dataclass(frozen=True)
class Link:
    rel: Rel
    kind: TargetKind
    id: int
    source: LinkSource
    phase: HoldPhase | None = None
    option: str | None = None
    effect: AnswerEffect | FollowedEffect | None = None


@dataclass(frozen=True)
class Filing:
    """What a checked draft records: the card's work path columns and its links."""
    reason: AskReason
    scope: HoldScope
    expiry: ExpiryPolicy
    links: tuple[Link, ...]
    refs: tuple[dict, ...] = ()
    note: str | None = None
    default: str | None = None
    owner_work: dict | None = None

    @property
    def joined(self) -> tuple[Link, ...]:
        """What a draft that joins another card adds to it: its asker and what it holds."""
        return tuple(x for x in self.links if x.rel in (Rel.RAISED_BY, Rel.HOLDS))


def resolve(s: Session, draft: AgentRequestDraft, origin: Origin, project_id: int | None,
            params: dict) -> Filing | list[Refused]:
    """The draft checked against its origin's row and its type's row, every key resolved inside the card's own project.
    A server row gives the context; an agent row reads the draft's. `params` are the card's action params, where a
    server row finds the flow that waits on the card."""
    rule = ORIGIN_RULES[origin.kind]
    raised = (Link(Rel.RAISED_BY, rule.target, origin.ref_id, LinkSource.SERVER),) if origin.ref_id is not None else ()
    if isinstance(rule, ServerRule):
        key, kind = rule.waits or (None, None)
        waits = ((Link(Rel.HOLDS, kind, params[key], LinkSource.SERVER),)
                 if key and isinstance(params.get(key), int) else ())
        return Filing(rule.reason, HOLD_SCOPE[rule.hold], rule.expiry, raised + waits)
    ctx = draft.context
    if ctx is None:
        return [Refused(RefusalKey.CONTEXT_MISSING)]
    refused: list[Refused] = []
    tr = TYPE_RULES[draft.type]
    refs = _refs(s, ctx, project_id, tr, refused)
    holds = _holds(s, ctx, origin, project_id, tr, rule, refused)
    uses = _answer_uses(s, draft, ctx, project_id, refused)
    default = _expiry(draft, ctx, tr, refused)
    _owner_work(draft, ctx, refused)
    if refused:
        return refused
    return Filing(ctx.reason, HOLD_SCOPE[ctx.holds.kind], ctx.if_unanswered.policy, raised + holds + uses,
                  refs=tuple(refs), note=ctx.note or None, default=default,
                  owner_work=ctx.owner_work.model_dump(mode="json") if ctx.owner_work else None)


def _task(s: Session, project_id: int | None, *, id: int | None = None, key: str | None = None) -> Task | None:
    if project_id is None:
        return None
    where = Task.id == id if id is not None else Task.key == slug(key or "")
    return s.scalar(select(Task).where(where, Task.project_id == project_id))


def _refs(s: Session, ctx: RequestContext, project_id: int | None, tr: TypeRule, refused: list[Refused]) -> list[dict]:
    parsed = ctx.parsed_refs()
    if not parsed and tr.refs_needed and ctx.reason != AskReason.GOAL_UNCLEAR:
        refused.append(Refused(RefusalKey.REFS_EMPTY))
    out: list[dict] = []
    for text, ref in zip(ctx.refs, parsed):
        if ref.kind == RefKind.TASK:
            found = getattr(_task(s, project_id, id=ref.id, key=ref.key), "id", None)
        elif ref.kind == RefKind.EVIDENCE:
            found = s.scalar(select(Evidence.id).where(Evidence.id == ref.id, Evidence.project_id == project_id))
        else:  # a company-wide card may cite a card of any project
            found = s.scalar(select(AgentRequest.id).where(
                AgentRequest.id == ref.id, *([AgentRequest.project_id == project_id] if project_id is not None else [])))
        if found is None:
            refused.append(Refused(RefusalKey.REF_MISSING, {"ref": text}))
        elif (row := {"kind": ref.kind.value, "id": found}) not in out:
            out.append(row)
    return out


def _ancestors(s: Session, task: Task) -> set[int]:
    seen: set[int] = set()
    todo = list(task.depends_on or [])
    while todo:
        if (tid := todo.pop()) in seen:
            continue
        seen.add(tid)
        if (t := s.get(Task, tid)) is not None:
            todo += t.depends_on or []
    return seen


def _holds(s: Session, ctx: RequestContext, origin: Origin, project_id: int | None, tr: TypeRule, rule: AgentRule,
           refused: list[Refused]) -> tuple[Link, ...]:
    """The task holds a declaration makes. A task author holds its own result (finish) or the tasks that start after it
    (start); the CEO holds planned tasks by key (start). A hold on a task the filer waits on, or on one that ended, is
    refused, so hold edges never close a cycle."""
    kind = ctx.holds.kind
    if tr.server_holds and kind != HoldKind.NOTHING:
        refused.append(Refused(RefusalKey.DEVELOPMENT_HOLDS_SERVER_OWNED))
        return ()
    own = s.get(Task, origin.task_id) if origin.task_id is not None else None
    targets: list[tuple[Task, HoldPhase]] = []
    if kind == HoldKind.THIS_TASK and own is not None:
        targets = [(own, HoldPhase.FINISH)]
    elif kind == HoldKind.MY_DEPENDENTS and own is not None:
        targets = [(t, HoldPhase.START) for t in s.scalars(select(Task).where(
            Task.project_id == own.project_id, Task.depends_on.contains([own.id]),
            Task.status.notin_(_TERMINAL)).order_by(Task.id))]
    elif kind == HoldKind.TASKS:
        ancestors = _ancestors(s, own) if own is not None else set()
        for key in ctx.holds.task_keys or [""]:
            t = _task(s, project_id, key=key) if key else None
            if t is None:
                refused.append(Refused(RefusalKey.HOLD_TARGET_UNKNOWN, {"task": key or "-"}))
            elif t.id in ancestors:
                refused.append(Refused(RefusalKey.HOLD_TARGET_IS_ANCESTOR, {"task": key}))
            else:
                targets.append((t, HoldPhase.START))
    for t, _ in targets:
        if t.status in _TERMINAL:
            refused.append(Refused(RefusalKey.HOLD_TARGET_TERMINAL, {"task": f"T-{t.id}"}))
    if kind not in rule.may_hold or kind not in tr.holds:
        refused.append(Refused(RefusalKey.HOLD_NOT_ALLOWED))
    elif kind == HoldKind.RUN and ctx.reason != AskReason.GOAL_UNCLEAR:
        refused.append(Refused(RefusalKey.RUN_NOT_GOAL_UNCLEAR))
    return tuple(Link(Rel.HOLDS, TargetKind.TASK, t.id, LinkSource.AGENT, phase=ph) for t, ph in targets)


def _answer_uses(s: Session, draft: AgentRequestDraft, ctx: RequestContext, project_id: int | None,
                 refused: list[Refused]) -> tuple[Link, ...]:
    """What the agent says answering does: `feeds` targets the named tasks, the rest the project's plan. A re-plan says
    more than informing, so it is kept when both are declared for the same answer."""
    out: list[Link] = []
    uses = sorted(ctx.on_answer, key=lambda u: u.effect != AnswerEffect.REPLANS)
    for use in uses:
        if use.option is not None and use.option not in draft.options:
            refused.append(Refused(RefusalKey.ANSWER_TARGET_UNKNOWN, {"what": use.option}))
            continue
        if use.effect != AnswerEffect.FEEDS:
            if project_id is not None:
                out.append(Link(Rel.ANSWER_USE, TargetKind.PROJECT, project_id, LinkSource.AGENT,
                                option=use.option, effect=use.effect))
            continue
        for key in use.task_keys or [""]:
            t = _task(s, project_id, key=key) if key else None
            if t is None or t.status in _TERMINAL:
                refused.append(Refused(RefusalKey.ANSWER_TARGET_UNKNOWN, {"what": key or use.effect.value}))
            else:
                out.append(Link(Rel.ANSWER_USE, TargetKind.TASK, t.id, LinkSource.AGENT, option=use.option,
                                effect=use.effect))
    return tuple(out)


def sends(action_kind: str | None, cost_usd: float | None) -> bool:
    """Whether approving spends money or sends something outside the company."""
    from justshipit.operations.gateway import CONNECTORS
    kind = action_kind or ""
    return (cost_usd or 0) > 0 or kind in CONNECTORS or kind.startswith("external.")


def _expiry(draft: AgentRequestDraft, ctx: RequestContext, tr: TypeRule, refused: list[Refused]) -> str | None:
    """The owner's rule: an unanswered question may go on with its recommended option; an approval, a spend and
    anything sent outside only close; a card that waits with no end holds nothing."""
    policy_, option = ctx.if_unanswered.policy, ctx.if_unanswered.option
    if policy_ != ExpiryPolicy.CLOSE and (tr.approves or sends(draft.action_kind, draft.estimated_cost_usd)):
        refused.append(Refused(RefusalKey.EXPIRY_CANNOT_APPROVE))
    elif policy_ == ExpiryPolicy.DEFAULT and draft.type not in _QUESTIONS:
        refused.append(Refused(RefusalKey.DEFAULT_NOT_ALLOWED))
    elif policy_ not in tr.expiry or (policy_ == ExpiryPolicy.KEEP_WAITING and ctx.holds.kind != HoldKind.NOTHING):
        refused.append(Refused(RefusalKey.EXPIRY_NOT_ALLOWED))
    elif policy_ == ExpiryPolicy.DEFAULT and (option is None or option != draft.recommended_option):
        refused.append(Refused(RefusalKey.DEFAULT_NOT_RECOMMENDED))
    elif policy_ == ExpiryPolicy.DEFAULT and not goes_ahead(option_verdict(draft, option)):
        refused.append(Refused(RefusalKey.DEFAULT_DECLINES))
    return option if policy_ == ExpiryPolicy.DEFAULT else None


def option_verdict(draft: AgentRequestDraft, option: str) -> HumanDecision | None:
    """The decision an option is, as `requests.choices` types it: the card's own verdict, else its words."""
    typed = next((o.verdict for o in (draft.card.options if draft.card else []) if o.label == option), None)
    return typed or infer_verdict(option, draft.type in _QUESTIONS)


def goes_ahead(verdict: str | None) -> bool:
    """Whether an option is a yes or an answer, the only kind a deadline may stand in for: a default that declines or
    puts off would turn a no the owner never gave into a yes, since the owner's own pick of it is refused."""
    return verdict in (None, HumanDecision.ANSWER.value, HumanDecision.APPROVE.value)


def asks_owner_work(draft: AgentRequestDraft) -> bool:
    """Whether the card asks the owner to do something by hand: what makes a to-do when approved
    (`owner_tasks.task_kind`), a message or steps to follow, or a post."""
    question = draft.type in _QUESTIONS
    return (owner_tasks.kind_for(draft.action_kind) is not None or draft.handout is not None
            or draft.post_card is not None
            or (not question and policy.no_rule_spend(draft.action_kind, draft.estimated_cost_usd)))


def _owner_work(draft: AgentRequestDraft, ctx: RequestContext, refused: list[Refused]) -> None:
    """Fields present and filled, and nothing more: whether a program could do the work is not something the server
    can judge from them."""
    work = ctx.owner_work
    if (asks_owner_work(draft) or work is not None) and (work is None or not work.if_skipped.strip()):
        refused.append(Refused(RefusalKey.OWNER_WORK_MISSING))


def stamp(req: AgentRequest, filing: Filing) -> None:
    """The work path columns of a card filed or revised under the contract."""
    req.context_version = 1
    req.ask_reason, req.expiry_policy, req.default_option = filing.reason.value, filing.expiry.value, filing.default
    req.reason_refs, req.reason_note, req.owner_work = list(filing.refs), filing.note, filing.owner_work


def link(s: Session, request_id: int, links: tuple[Link, ...], revision: int) -> None:
    """Write the links at the card's revision. An edge already written for it stays as it is."""
    for x in links:
        s.execute(insert(RequestLink).values(
            request_id=request_id, rel=x.rel.value, target_kind=x.kind.value, target_id=x.id,
            phase=x.phase.value if x.phase else None, option_key=x.option, effect=x.effect.value if x.effect else None,
            revision=revision, source=x.source.value, known=Known.RECORDED.value).on_conflict_do_nothing())


def end_agent_rows(s: Session, request_id: int, revision: int) -> None:
    """A revision replaces what the agent declared before it; what the server linked stays."""
    s.execute(update(RequestLink).where(
        RequestLink.request_id == request_id, RequestLink.source == LinkSource.AGENT.value,
        RequestLink.ended_at.is_(None), RequestLink.revision < revision).values(ended_at=func.now()))


def call_row(s: Session, call_id: str | None) -> int | None:
    """The ledger row (`llm_calls.id`) of the model call that wrote a card, which the card names as where it came from.
    None when the ledger kept no such call."""
    return s.scalar(select(LLMCallRecord.id).where(LLMCallRecord.call_id == call_id)) if call_id else None
