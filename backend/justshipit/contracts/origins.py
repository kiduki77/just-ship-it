"""Where a card comes from, named by the code that files it, never by the agent (docs/design/work-path.md 2.1), and the
rules each origin and each request type keep (2.2, 2.4). `requests.create` takes an origin; services/request_contract.py
checks a draft against these tables before anything is stored."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from justshipit.contracts.requests import RequestType
from justshipit.contracts.work_path import AskReason, ExpiryPolicy, HoldKind, TargetKind


class OriginKind(StrEnum):
    # An agent writes the request's context.
    TASK = "task"                    # a task's author, as the round settles (company_graph.release_held)
    ROUND = "round"                  # the CEO's plan or review (a review_rounds row)
    KPI_REVIEW = "kpi_review"        # the CEO's KPI review of a finished project (operations/jobs.py)
    PORTFOLIO = "portfolio"          # the CEO's weekly company review
    CHAT = "chat"                    # an agent's reply in team chat (services/chat_reply.py)
    # The server writes the request; its row below gives the context.
    DECISION = "decision"            # a meeting decision that needs the owner's approval
    BUILD_ASK = "build_ask"          # the plan's build, asked for once its inputs are done
    DEV_PROPOSAL = "dev_proposal"    # the final report's development needs
    PLAN_APPROVAL = "plan_approval"  # the product plan's card
    PLAN_REASK = "plan_reask"        # the same plan asked again after a hold or an expiry
    SPEC_APPROVAL = "spec_approval"  # the spec's card, and the same spec asked again
    SPEC_QUESTION = "spec_question"  # a question the spec cannot settle (engineering/questions.py)
    BUILD_QUESTION = "build_question"  # the engineers' question the product agent could not answer
    MERGE = "merge"                  # the build's merge card
    FIX_QUESTION = "fix_question"    # a merge turned down without a note: what to change
    BUILD_FAILED = "build_failed"    # a build that failed its check: fix or drop
    USE_OF_BUILD = "use_of_build"    # how people will use a merged build
    SKILL_PROPOSAL = "skill_proposal"
    SKILL_ACTIVATION = "skill_activation"
    REASSESS = "reassess"            # the source watch found premises changed
    BET_ROUTINE = "bet_routine"      # a bet's retro: make it a routine
    BET_SCOPE = "bet_scope"          # a bet's retro proposed a next bet outside the brief's scope
    IDEA = "idea"                    # an owner idea that changes the approved plan
    RE_REQUEST = "re_request"        # the owner asked a failed action again


@dataclass(frozen=True)
class TaskOrigin:
    task_id: int

    @property
    def kind(self) -> OriginKind:
        return OriginKind.TASK

    @property
    def ref_id(self) -> int:
        return self.task_id


@dataclass(frozen=True)
class RoundOrigin:
    round_id: int

    @property
    def kind(self) -> OriginKind:
        return OriginKind.ROUND

    @property
    def ref_id(self) -> int:
        return self.round_id

    task_id = None


@dataclass(frozen=True)
class DecisionOrigin:
    decision_id: int

    @property
    def kind(self) -> OriginKind:
        return OriginKind.DECISION

    @property
    def ref_id(self) -> int:
        return self.decision_id

    task_id = None


_OWN_CLASS = {OriginKind.TASK, OriginKind.ROUND, OriginKind.DECISION}


@dataclass(frozen=True)
class ServerOrigin:
    """A call site other than a task, a CEO round or a meeting decision. `ref_id` is the record its rule's `target`
    names, None only where the record may be missing (a model call the ledger did not keep). `task_id` is the task a
    card asked again was about, so the new card keeps it."""
    kind: OriginKind
    ref_id: int | None
    task_id: int | None = None

    def __post_init__(self) -> None:
        if self.kind in _OWN_CLASS:
            raise ValueError(f"{self.kind} has its own origin class")


Origin = TaskOrigin | RoundOrigin | DecisionOrigin | ServerOrigin


@dataclass(frozen=True)
class AgentRule:
    """An origin whose agent writes the context: `target` is what its ref names, `may_hold` what that filer may
    declare. A task author sees only its own key, so it holds itself, its dependents or the plan step; the CEO holds
    planned tasks by key, or the run."""
    target: TargetKind
    may_hold: frozenset[HoldKind]


@dataclass(frozen=True)
class ServerRule:
    """An origin whose server code builds the draft: the context is this row's. `waits` is the server flow that waits on
    the card, as the action_params key that names its row and that row's kind: a `holds` link with no phase, which
    holds no task."""
    target: TargetKind
    reason: AskReason
    expiry: ExpiryPolicy
    hold: HoldKind = HoldKind.NOTHING
    waits: tuple[str, TargetKind] | None = None


_TASK_HOLDS = frozenset({HoldKind.NOTHING, HoldKind.THIS_TASK, HoldKind.MY_DEPENDENTS, HoldKind.PLANNING})
_PROJECT_MANAGER_HOLDS = frozenset({HoldKind.NOTHING, HoldKind.PLANNING, HoldKind.TASKS, HoldKind.RUN})
_NOTHING = frozenset({HoldKind.NOTHING})
_JOB = ("dev_job_id", TargetKind.DEV_JOB)
_CLOSE, _WAIT = ExpiryPolicy.CLOSE, ExpiryPolicy.KEEP_WAITING

ORIGIN_RULES: dict[OriginKind, AgentRule | ServerRule] = {
    OriginKind.TASK: AgentRule(TargetKind.TASK, _TASK_HOLDS),
    OriginKind.ROUND: AgentRule(TargetKind.ROUND, _PROJECT_MANAGER_HOLDS),
    OriginKind.KPI_REVIEW: AgentRule(TargetKind.CALL, frozenset({HoldKind.NOTHING, HoldKind.PLANNING})),
    OriginKind.PORTFOLIO: AgentRule(TargetKind.CALL, _NOTHING),
    OriginKind.CHAT: AgentRule(TargetKind.CHAT_MESSAGE, _NOTHING),
    # A direction change the owner approves stops the run until answered, as it always did.
    OriginKind.DECISION: ServerRule(TargetKind.DECISION, AskReason.FOLLOWS_DECISION, _CLOSE, HoldKind.RUN),
    OriginKind.BUILD_ASK: ServerRule(TargetKind.ROUND, AskReason.BUILD_NEEDED, _CLOSE),
    OriginKind.DEV_PROPOSAL: ServerRule(TargetKind.CALL, AskReason.BUILD_NEEDED, _CLOSE),
    OriginKind.PLAN_APPROVAL: ServerRule(TargetKind.PLAN, AskReason.NEEDS_PERMISSION, _CLOSE,
                                         waits=("plan_id", TargetKind.PLAN)),
    OriginKind.PLAN_REASK: ServerRule(TargetKind.PLAN, AskReason.NEEDS_PERMISSION, _CLOSE,
                                      waits=("plan_id", TargetKind.PLAN)),
    OriginKind.SPEC_APPROVAL: ServerRule(TargetKind.SPEC, AskReason.NEEDS_PERMISSION, _CLOSE,
                                         waits=("spec_id", TargetKind.SPEC)),
    # It grants nothing and the spec waits on it, so it never expires.
    OriginKind.SPEC_QUESTION: ServerRule(TargetKind.SPEC, AskReason.SPEC_UNCLEAR, _WAIT,
                                         waits=("spec_id", TargetKind.SPEC)),
    OriginKind.BUILD_QUESTION: ServerRule(TargetKind.DEV_JOB, AskReason.SPEC_UNCLEAR, _CLOSE, waits=_JOB),
    OriginKind.MERGE: ServerRule(TargetKind.DEV_JOB, AskReason.NEEDS_PERMISSION, _CLOSE, waits=_JOB),
    OriginKind.FIX_QUESTION: ServerRule(TargetKind.DEV_JOB, AskReason.INPUT_MISSING, _CLOSE, waits=_JOB),
    OriginKind.BUILD_FAILED: ServerRule(TargetKind.DEV_JOB, AskReason.RESULT_NEEDS_CALL, _CLOSE, waits=_JOB),
    # The build is merged, so nothing waits; an expired one could no longer be answered.
    OriginKind.USE_OF_BUILD: ServerRule(TargetKind.DEV_JOB, AskReason.INPUT_MISSING, _WAIT),
    OriginKind.SKILL_PROPOSAL: ServerRule(TargetKind.SKILL, AskReason.NEEDS_PERMISSION, _CLOSE),
    OriginKind.SKILL_ACTIVATION: ServerRule(TargetKind.SKILL, AskReason.RESULT_NEEDS_CALL, _CLOSE),
    OriginKind.REASSESS: ServerRule(TargetKind.CALL, AskReason.EVIDENCE_CONFLICTS, _CLOSE),
    OriginKind.BET_ROUTINE: ServerRule(TargetKind.BET, AskReason.RESULT_NEEDS_CALL, _CLOSE),
    OriginKind.BET_SCOPE: ServerRule(TargetKind.BET, AskReason.NEEDS_PERMISSION, _CLOSE),
    OriginKind.IDEA: ServerRule(TargetKind.IDEA, AskReason.NEEDS_PERMISSION, _CLOSE),
    OriginKind.RE_REQUEST: ServerRule(TargetKind.REQUEST, AskReason.NEEDS_PERMISSION, _CLOSE),
}


@dataclass(frozen=True)
class TypeRule:
    """What an agent's request of one type may declare (design 2.4). `approves`: approving it acts, so if nobody answers
    it only closes. `refs_needed`: it shows a record of why, unless the goal itself is unclear. `server_holds`: the
    server decides what waits on it, so the agent declares no hold."""
    holds: frozenset[HoldKind]
    expiry: frozenset[ExpiryPolicy]
    approves: bool = False
    refs_needed: bool = False
    server_holds: bool = False


_QUESTION = TypeRule(frozenset(HoldKind), frozenset(ExpiryPolicy), refs_needed=True)
_APPROVAL = TypeRule(frozenset({HoldKind.NOTHING, HoldKind.THIS_TASK, HoldKind.MY_DEPENDENTS}),
                     frozenset({ExpiryPolicy.CLOSE}), approves=True)

TYPE_RULES: dict[RequestType, TypeRule] = {
    RequestType.QUESTION: _QUESTION,
    RequestType.CLARIFICATION: _QUESTION,
    RequestType.ESCALATION: _QUESTION,
    RequestType.APPROVAL: _APPROVAL,
    RequestType.RESOURCE: _APPROVAL,
    RequestType.PROPOSAL: TypeRule(frozenset({HoldKind.NOTHING, HoldKind.PLANNING}),
                                   frozenset({ExpiryPolicy.CLOSE, ExpiryPolicy.KEEP_WAITING})),
    RequestType.DEVELOPMENT: TypeRule(_NOTHING, frozenset({ExpiryPolicy.CLOSE}), server_holds=True),
    # Approving a skill turns it on for agents, so it is an approval: it holds nothing and only closes.
    RequestType.SKILL: TypeRule(_NOTHING, frozenset({ExpiryPolicy.CLOSE}), approves=True),
}
