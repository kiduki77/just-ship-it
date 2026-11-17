"""The owner's ordered decisions, the per-project outline and the path view (docs/design/work-path.md 5 and 6), as the
console reads them. Text is a catalog key plus values that the console words (docs/I18N.md); no coordinates are sent,
because layout is the client's job."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from justshipit.contracts.card_context import Known, Vars
from justshipit.contracts.work_path import HoldScope


class Counts(BaseModel):
    """What a project, stage or cycle has: `done` over `total` (every task but CANCELLED and REPLACED), the tasks a card
    holds, the failed ones, and the cards and to-dos that wait on the owner. A stage or cycle sums its projects."""
    done: int = 0
    total: int = 0
    held: int = 0
    failed: int = 0
    cards: int = 0
    todos: int = 0


class TurnCard(BaseModel):
    """A pending decision with what answering it moves. `scope` is what it says it holds; `effective` is what it sorts
    as, which is `none` once all it held is moot (`nothing_live`). `rank` is its place in the one order."""
    id: int
    ref: str                        # REQ-6
    title: str
    type: str
    project_id: int | None
    scope: HoldScope
    effective: HoldScope
    frees: int
    behind: int
    nothing_live: bool
    expires_at: datetime | None
    rank: int                       # 1-based


class Attention(BaseModel):
    """A task of a project that needs a look: one that failed."""
    id: int
    ref: str                        # T-38
    key: str
    title: str
    status: str


class OutlineNode(BaseModel):
    """One row of the outline: a cycle, a stage or a project, with its roll-up. A project carries the decisions that
    wait on the owner (in the one order) and the tasks that failed, so the outline holds every fact the path shows."""
    kind: Literal["cycle", "stage", "project"]
    id: int
    ref: str | None = None          # P-3 for a project
    title: str | None = None        # a stage's or project's name
    number: int | None = None       # a cycle's number
    status: str | None = None
    experiment: dict[str, str | int] | None = None   # a project's experiment: id, title, status
    counts: Counts
    cards: list[TurnCard] = []
    attention: list[Attention] = []
    children: list[OutlineNode] = []


class Outline(BaseModel):
    goal: str | None
    counts: Counts
    children: list[OutlineNode]     # cycles, then projects outside any cycle


class YourTurn(BaseModel):
    business_id: int | None
    cards: list[TurnCard]           # every pending decision of the business, in the one order
    outline: Outline
    as_of: datetime


NodeKind = Literal["goal", "cycle", "stage", "experiment", "project", "task", "decision", "project_manager", "todo",
                   "origin", "group"]
EdgeKind = Literal["contains", "depends_on", "holds", "raised_by", "followed"]


class Node(BaseModel):
    """One box. `id` is stable across revisions: `g`, `c:1`, `s:4`, `b:2`, `p:3`, `t:41`, `r:6`, `m:9`, `o:5`, `x:chat:7`
    and `f:tasks_done`. `focus` marks the card the view was opened on; `chain` the steps from the goal to it."""
    id: str
    kind: NodeKind
    ref: str | None = None          # the code the owner knows it by: T-41, REQ-6, P-3, OT-5
    ref_id: int | None = None
    label_key: str                  # `path.node.*` for a plain node, or the asker's `path.step.*`
    vars: Vars = {}
    state: str | None = None        # the record's own status code; the console words it
    known: Known = "recorded"
    focus: bool = False
    chain: bool = False
    waiting: bool = False           # a decision waiting for the owner
    held: bool = False              # a task a pending card holds
    scope: HoldScope | None = None  # a pending decision's hold scope
    frees: int | None = None
    behind: int | None = None
    count: int | None = None        # a group: how many nodes it folds
    members: list[str] = []         # a group: the ids it folds, in order


class Edge(BaseModel):
    """A link, always from what came first to what follows: a prerequisite to the task after it, the asker to the card,
    the card to the task it holds, a decided card to what it started. `known` is `unknown` for the "not recorded" edge.
    `phase` names a hold, `effect` a followed link. `via_group`: one end is a folded group standing for hidden nodes."""
    id: str
    kind: EdgeKind
    source: str
    target: str
    label_key: str                  # `path.edge.<kind>`, `path.edge.holds_result`, `path.edge.raised_by_unknown`
    known: Known = "recorded"
    phase: Literal["start", "finish"] | None = None
    effect: str | None = None
    via_group: bool = False


class PathLimits(BaseModel):
    default: int = 20
    unfolded: int = 60
    too_big: int = 150


class PathView(BaseModel):
    project_id: int
    focus: str | None
    rev: str                        # changes only when the project's graph does; independent of folding
    too_big: bool                   # past the limit the graph is not drawn: `nodes` and `edges` are empty
    neighbourhood: bool             # drawn for the focused card's neighbourhood alone
    can_narrow: bool                # `neighbourhood=true` with this `focus` may draw it
    unfolded: list[str]             # the groups that were opened
    total: int                      # nodes the whole view has, folded or not
    shown: int
    limits: PathLimits
    nodes: list[Node]
    edges: list[Edge]
    outline: Outline                # the same facts as a list; the whole view when `too_big`
    as_of: datetime
