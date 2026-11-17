"""What a decision card says about its place in the work (docs/design/work-path.md 4.2), read from the records alone.
Text is a catalog key plus values (docs/I18N.md) that the console words; the one sentence the server words itself is
what approving does (`AfterLine.text`, from `requests.outcome`). A fact the records do not hold is `unknown`, never a
guess."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from justshipit.contracts.work_path import ExpiryPolicy, HoldPhase, HoldScope

# `derived`: read from the plan or computed from other rows. The rest are `work_path.Known`; `unknown` is the absence.
Known = Literal["recorded", "derived", "backfilled", "matched", "unknown"]
StepKind = Literal["goal", "cycle", "stage", "experiment", "project", "task", "round", "decision", "chat", "server",
                   "call", "origin", "this"]
Vars = dict[str, str | int]


class Step(BaseModel):
    """One link of the chain from the goal to this question. `origin` is the step that filed the card when the records
    do not say which it was."""
    kind: StepKind
    ref: str | None = None          # the code the owner knows it by: P-3, T-41, REQ-6
    ref_id: int | None = None
    label_key: str
    vars: Vars = {}
    state: str | None = None        # the record's own status code; the console words it
    known: Known


class Ref(BaseModel):
    """A thing the agent pointed at, linkable. `label` is None when the row is not in the graph."""
    kind: Literal["evidence", "task", "request", "round"]
    id: int
    label: str | None = None
    state: str | None = None


class Why(BaseModel):
    reason: str | None              # an `AskReason` key; None on a card that did not record one
    refs: list[Ref]
    note: str | None
    agent: str
    at: datetime
    recorded: bool                  # filed under the work path (context_version >= 1)
    stale: bool                     # the note's own round was replaced or a ref's task ended
    changed: list[Ref]


class HeldTask(BaseModel):
    id: int
    key: str
    title: str
    phase: HoldPhase
    state: str
    live: bool
    ended_on: datetime | None       # when a task that no longer waits ended


class Holds(BaseModel):
    scope: HoldScope
    tasks: list[HeldTask]
    frees: int                      # held tasks that nothing else holds (for a run or planning card, every task it stops): they go on once this closes
    behind: int                     # tasks after the held ones, through `depends_on`, that get closer to running
    nothing_live: bool              # the card holds tasks, and none of them still waits


class AfterLine(BaseModel):
    source: Literal["declared", "server", "followed"]
    option_key: str | None = None   # which answer; None for any
    effect: str | None = None       # a closed key: informs, replans, feeds, or what a decision ran
    text: str | None = None         # the server's own words, for source "server"
    targets: list[Ref] = []
    at: datetime | None = None      # when a followed effect ran


class Unanswered(BaseModel):
    policy: ExpiryPolicy | None     # None: no rule was recorded
    when: datetime | None
    default_option: str | None
    affects: list[HeldTask]         # what a close cancels or a default sets going


class CardContext(BaseModel):
    request_id: int
    status: str
    decided: bool
    why: Why
    chain: list[Step]
    also_asked: list[Step]          # a card several agents asked: the askers after the first
    # The pending card keeps its whole project waiting now (`holds.parks`): it holds the run, or it holds tasks or the
    # next plan step and nothing else in the project can go on.
    parks_project: bool
    holds: Holds | None             # None once decided
    after: list[AfterLine]          # before the answer: declared and server; after it: what followed
    unanswered: Unanswered | None   # None once decided
    complete: bool                  # nothing in this card is "not recorded"
    recorded_since: datetime | None  # when cards began to record this, for "asked before {date}"
    as_of: datetime
