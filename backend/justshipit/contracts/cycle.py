"""What the CEO returns for a cycle: the proposal of stages, a revision after the owner said what to change, the
summary that opens the gate at a stage's end, and the look back at the cycle's end.

The model writes judgement only. The server checks every list against closed sets it owns (the recurring kinds in
gate.POLICY, the kinds of to-do), and writes every number the owner reads as fact itself. Every field is required and
nullable ones say so, like the other CEO contracts, so the schema stays strict.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from justshipit.contracts.retro import Cause, NextBet, Verdict
from justshipit.services.gate import OWNER_WORK_KINDS, RECURRING_KINDS

Confidence = Literal["high", "mid", "low"]
EndsWhen = Literal["run_settled", "window_closed", "owner_marks"]
RecurringKind = Literal[*RECURRING_KINDS]
OwnerWorkKind = Literal[*OWNER_WORK_KINDS]
MIN_STAGES, MAX_STAGES = 4, 9


class ExistingRef(BaseModel):
    kind: Literal["project", "bet", "schedule"]
    id: int


class StageDraft(BaseModel):
    key: str = Field(description="Stable slug of 3 to 30 lowercase letters, digits or dashes, unique in the cycle. Keep "
                                 "a stage's key when you edit it")
    title: str = Field(description="Short stage name in the owner's language, such as 'Validate'")
    doing: str = Field(description="What the company does in this stage, in two or three sentences. It becomes the "
                                   "goal of the stage's work")
    owner_does: str = Field(description="What the owner does in this stage. Say 'Nothing' in the owner's language when "
                                        "nothing")
    decision_points: list[str] = Field(description="Moments the owner will be asked to decide. Empty when none")
    est_minutes: int | None = Field(description="A guess of the working time in minutes. null when you cannot tell",
                                    ge=1, le=20160)
    est_usd: float | None = Field(description="A guess of the usage in USD equivalent. null when you cannot tell", ge=0)
    estimate_basis: Literal["guess", "history"] = Field(description="history only when the input has earlier stages or "
                                                                    "projects of this business to compare with")
    ends_when: EndsWhen = Field(description="run_settled for work the company does. window_closed for waiting on market "
                                            "reaction. owner_marks when the owner does the work and says when it is done")
    listen_days: int | None = Field(description="Only for window_closed: days to wait for reaction. Otherwise null",
                                    ge=1, le=60)
    allows_kinds: list[RecurringKind] = Field(description="Recurring jobs that may run in this stage. A job not listed "
                                                          "here does not run. Empty when none")
    allows_owner_work: list[OwnerWorkKind] = Field(description="Kinds of to-do the company may put in the owner's list "
                                                               "in this stage. Must agree with owner_does. Empty when "
                                                               "none")
    experiment: NextBet | None = Field(description="The experiment this stage runs, for promotion and reaction "
                                                   "stages. null for other stages")
    maps_existing: list[ExistingRef] = Field(description="Adopt mode only: running work this stage takes over. Empty "
                                                         "otherwise")


class CycleProposalOut(BaseModel):
    intro: str = Field(description="Two or three sentences to the owner on what this cycle is for")
    stages: list[StageDraft] = Field(description="4 to 9 stages, in order", min_length=MIN_STAGES, max_length=MAX_STAGES)
    past_stage_keys: list[str] = Field(description="Adopt mode only: keys of stages whose work is already finished. "
                                                   "Empty otherwise")
    current_stage_key: str | None = Field(description="Adopt mode only: the stage the running work belongs to. null "
                                                      "otherwise")


class ChangeNote(BaseModel):
    stage_key: str
    why: str = Field(description="Why you changed this stage, one sentence that answers the owner's note")


class CycleRevisionOut(BaseModel):
    reply: str = Field(description="One to three sentences to the owner: what you changed and what you did not")
    intro: str = Field(description="Two or three sentences on what this cycle is for, after the change")
    stages: list[StageDraft] = Field(description="The whole list after the change, in order, the finished and current "
                                                 "stages included as they are", min_length=MIN_STAGES,
                                     max_length=MAX_STAGES)
    why: list[ChangeNote] = Field(description="One entry per stage you added, removed, edited or moved")


class Claim(BaseModel):
    claim: str = Field(description="One sentence the owner can check")
    evidence_ids: list[str] = Field(description="Ids from the input only, such as EV-12 or T-4. Empty when none")
    confidence: Confidence = Field(description="Match it to the evidence. low when there is no evidence id")


class StageSummaryOut(BaseModel):
    headline: str = Field(description="One sentence: how the stage went")
    done: list[str] = Field(description="What the company finished, as short sentences")
    not_done: list[str] = Field(description="What it did not finish, and why. Empty when nothing")
    claims: list[Claim] = Field(description="What the stage showed, each with its evidence and confidence")
    needs_owner: list[str] = Field(description="What only the owner can decide or do next. Empty when nothing")
    next_key: str | None = Field(description="Key of the stage you suggest starting next. null at the last stage")
    next_reason: str | None = Field(description="Why that one, one sentence. null at the last stage")
    change_suggestions: list[str] = Field(description="Changes to the remaining stages you would suggest. Empty when "
                                                      "none")


class CycleRetroOut(BaseModel):
    causes: list[Cause] = Field(description="1 to 4 candidate reasons the cycle went as it did")
    counter_evidence: list[str] = Field(description="Facts that weaken those reasons. Empty when there are none")
    verdict: Verdict = Field(description="continue, stop, change, or routine, as for an experiment's retro")
    verdict_reason: str = Field(description="Why, in one or two sentences")
    next_cycle_note: str = Field(description="What the next cycle should be about, in two sentences")


# ------------------------------------------------------------------ the dashboard's read model (services/cycle_board.py)

BoardState = Literal["none", "proposing", "proposal", "legacy", "revising", "proposal_failed", "running", "closing",
                     "gate", "stage_stopped", "retro", "stopped"]
StageStatus = Literal["planned", "running", "closing", "awaiting_owner", "done", "skipped", "stopped"]


class CycleHead(BaseModel):
    id: int
    number: int
    state: Literal["draft", "running", "retro", "closed", "stopped"]
    mode: Literal["new", "adopt"]
    starts_on: str
    ends_on: str
    started_at: str | None


class StageEstimate(BaseModel):
    minutes: int | None
    usd: float | None
    basis: Literal["guess", "history"]


class StageCard(BaseModel):
    """A stage as a proposal or a gate shows it: what the owner consents to by pressing Start."""
    key: str
    title: str
    doing: str
    owner_does: str
    decision_points: list[str]
    estimate: StageEstimate
    ends_when: EndsWhen
    recurring: list[str]
    owner_work: list[str]
    takes_over: list[ExistingRef]
    change: Literal["added", "edited", "moved"] | None


class ChangeView(BaseModel):
    stage_key: str
    kind: Literal["added", "edited", "moved", "removed"]
    fields: list[str]
    why: str | None


class ProposalCard(BaseModel):
    kind: Literal["proposal"] = "proposal"
    cycle_id: int
    version: int
    mode: Literal["new", "adopt"]
    intro: str
    reply: str | None
    stages: list[StageCard]
    changes: list[ChangeView]
    revising: bool
    revision_failed: str | None
    since: str | None
    actions: list[Literal["start_cycle", "revise", "retry_proposal"]]


class ClaimView(BaseModel):
    claim: str
    evidence_ids: list[str]
    confidence: Confidence
    capped: bool


class TaskCounts(BaseModel):
    done: int
    failed: int
    total: int


class SummaryView(BaseModel):
    headline: str
    done: list[str]
    not_done: list[str]
    claims: list[ClaimView]
    needs_owner: list[str]
    next_key: str | None
    next_reason: str | None
    change_suggestions: list[str]
    cost_usd: float
    elapsed_s: int
    tasks: TaskCounts
    by: Literal["project_manager", "server"]
    note: str | None


class WaitingItem(BaseModel):
    """What waits on the gate by name: an open decision of the stage's work, or work parked for the next stage."""
    kind: Literal["decision", "job"]
    id: int
    title: str


class GateCard(BaseModel):
    kind: Literal["gate"] = "gate"
    cycle_id: int
    stage_id: int
    stage_title: str
    round: int
    summary: SummaryView | None
    next_stage: StageCard | None
    proposal_version: int   # the version of the plan this card shows; StartNext sends it as `version`, a Revise as `base_version`
    waiting: list[WaitingItem]
    reply: str | None   # the CEO's answer to a change the owner asked for at this gate
    actions: list[Literal["start_next", "finish_cycle", "revise", "stop_here", "retry_stage"]]


class StoppedCard(BaseModel):
    kind: Literal["stopped_stage"] = "stopped_stage"
    cycle_id: int
    stage_id: int
    stage_title: str
    waiting: list[WaitingItem]
    actions: list[Literal["resume_stage", "stop_here"]]


class DecisionCard(BaseModel):
    kind: Literal["decision"] = "decision"
    request_id: int
    title: str
    project_id: int | None
    blocking: bool


class StageChip(BaseModel):
    id: int
    key: str
    position: int
    title: str
    status: StageStatus
    tasks_done: int | None
    tasks_total: int | None
    is_current: bool
    adopted: bool


class TaskRow(BaseModel):
    id: int
    ref: str
    title: str
    agent: str
    status: str
    started_at: str | None
    project_id: int


class CurrentStage(BaseModel):
    stage: StageChip
    doing: str
    owner_does: str
    estimate: StageEstimate
    recurring: list[str]
    owner_work: list[str]
    tasks: list[TaskRow]
    tasks_done: int
    tasks_total: int
    cost_usd: float
    started_at: str | None
    close_requested: bool
    summary: SummaryView | None
    actions: list[Literal["stop_stage", "resume_stage", "close_stage_now"]]
    # What the stage's work is doing while it has no task yet: a worker is on its first job (`planning`), or the job
    # waits for a worker to take it (`queued`). None once there is a task or no job of the stage is alive.
    starting: Literal["planning", "queued"] | None = None
    starting_since: str | None = None


class ActionItem(BaseModel):
    """One thing an agent is doing now: a task, a review, a build or a background job."""
    kind: Literal["task", "review", "dev", "job"]
    cycle_number: int | None
    stage_id: int | None
    stage_title: str | None
    task_id: int | None
    dev_job_id: int | None
    job_id: int | None
    title: str
    project_id: int | None
    started_at: str | None
    spent_usd: float


class ActionCard(BaseModel):
    """One per agent at work, whatever the number of things it is on: those are `items`, the one started last first.
    An idle agent gets no card. `stopping`: the owner stopped the company or this agent (`stopped`), and these are the
    steps it finishes first."""
    agent: str
    items: list[ActionItem] = Field(min_length=1)
    stopping: bool = False
    stopped: bool = False


class CycleRow(BaseModel):
    id: int
    number: int
    state: Literal["draft", "running", "retro", "closed", "stopped"]
    started_at: str | None
    closed_at: str | None
    stages_done: int
    stages_total: int
    cost_usd: float


class CycleBoardOut(BaseModel):
    state: BoardState
    sentence: str
    since: str | None   # when the wait the owner sees began: the CEO's draft, the stage's run, its gate
    cycle: CycleHead | None
    waiting: list[Annotated[ProposalCard | GateCard | StoppedCard | DecisionCard, Field(discriminator="kind")]]
    strip: list[StageChip]
    current: CurrentStage | None
    working: list[ActionCard]
    idle_agents: int
    # of the idle agents, the ones the owner stopped: "idle" alone would hide that they wait on a resume
    idle_stopped: int = 0
    history: list[CycleRow]
