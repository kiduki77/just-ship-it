"""The owner's decisions in the order of what each frees, and the outline of every project's progress (docs/design/work-path.md
5). Both read the one graph `work_path.load` builds, so the order, the counts on a card and the path cannot disagree."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from justshipit.contracts import owner_text
from justshipit.contracts.work import TaskStatus
from justshipit.contracts.work_path import HoldScope
from justshipit.contracts.your_turn import Attention, Counts, Outline, OutlineNode, TurnCard, YourTurn
from justshipit.services import requests, work_path

Row = dict[str, Any]

# What each scope costs the company while it waits, most first.
SCOPE_RANK = {HoldScope.RUN: 0, HoldScope.PLANNING: 1, HoldScope.TASKS: 2, HoldScope.NONE: 3}
_NOT_COUNTED = (TaskStatus.CANCELLED.value, TaskStatus.REPLACED.value)
_ARCHIVED = "ARCHIVED"


def sort_key(card: TurnCard) -> tuple[int, int, float, int]:
    """`(scope, -(frees + behind), expires_at nulls last, id)`: a card that holds the run before one that holds a plan
    step before one that holds tasks before one that holds nothing, then the one that frees the most, then the one that
    expires first."""
    expires = card.expires_at.timestamp() if card.expires_at else float("inf")
    return SCOPE_RANK[card.effective], -(card.frees + card.behind), expires, card.id


def cards(g: work_path.WorkGraph, ids: list[int]) -> list[TurnCard]:
    """The pending cards among `ids` in the one order, each with what answering it frees. A card whose holds are all
    moot sorts as `none`."""
    found = []
    for rid in ids:
        r = g.requests[rid]
        reach = work_path.reach_of(g, r)
        scope = HoldScope(r["hold_scope"])
        found.append(TurnCard(
            id=rid, ref=requests.display_id(rid), title=owner_text.title(r["title"], 120), type=r["type"],
            project_id=r["project_id"], scope=scope, effective=HoldScope.NONE if reach.moot else scope,
            frees=reach.frees, behind=reach.behind, nothing_live=reach.moot, expires_at=r["expires_at"], rank=0))
    found.sort(key=sort_key)
    return [c.model_copy(update={"rank": i}) for i, c in enumerate(found, 1)]


def your_turn(s: Session, business_id: int, now: datetime | None = None) -> YourTurn:
    """Every pending decision of the business in the one order, and the outline of its projects. The decisions are the
    ones the owner's list shows (`requests.pending`), company-wide ones included."""
    now = now or datetime.now(UTC)
    ids = [r.id for r in requests.pending(s, business_id=business_id)]
    g = work_path.load(s, business_id, cards=ids)
    ordered = cards(g, ids)
    return YourTurn(business_id=business_id, cards=ordered, outline=outline(g, ordered), as_of=now)


def order_of(s: Session, business_id: int) -> dict[int, TurnCard]:
    """The owner's list asks for each pending decision's card by id, so it sorts by the same key."""
    return {c.id: c for c in your_turn(s, business_id).cards}


def _counts(g: work_path.WorkGraph, pid: int, waiting: list[TurnCard]) -> Counts:
    tasks = [t for t in g.tasks.values() if t["project_id"] == pid]
    live = {h.task_id for h in g.holds if h.task_id in g.tasks and g.tasks[h.task_id]["project_id"] == pid}
    return Counts(
        done=sum(1 for t in tasks if t["status"] == TaskStatus.DONE.value),
        total=sum(1 for t in tasks if t["status"] not in _NOT_COUNTED), held=len(live),
        failed=sum(1 for t in tasks if t["status"] == TaskStatus.FAILED.value), cards=len(waiting),
        todos=sum(1 for o in g.todos.values() if o["project_id"] == pid))


def _sum(parts: list[Counts]) -> Counts:
    return Counts(**{k: sum(getattr(c, k) for c in parts) for k in Counts.model_fields})


def project_node(g: work_path.WorkGraph, pid: int, ordered: list[TurnCard]) -> OutlineNode:
    p = g.projects[pid]
    waiting = [c for c in ordered if c.project_id == pid]
    bet = g.bets.get(p["bet_id"])
    failed = sorted((t for t in g.tasks.values() if t["project_id"] == pid and t["status"] == TaskStatus.FAILED.value),
                    key=lambda t: t["id"])
    return OutlineNode(
        kind="project", id=pid, ref=f"P-{pid}", title=owner_text.title(p["name"], 80), status=p["status"],
        experiment={"id": bet["id"], "title": owner_text.title(bet["title"], 80), "status": bet["status"]} if bet else None,
        counts=_counts(g, pid, waiting), cards=waiting,
        attention=[Attention(id=t["id"], ref=f"T-{t['id']}", key=t["key"], title=owner_text.title(t["title"], 80),
                             status=t["status"]) for t in failed])


def outline(g: work_path.WorkGraph, ordered: list[TurnCard], only: int | None = None) -> Outline:
    """Cycle > stage > project, each row summing the projects under it. A project of a cycle's experiment with no stage
    sits directly under the cycle; one outside any cycle comes after the cycles. Archived projects are left out.
    `only`: just that project's branch."""
    by_cycle: dict[int, dict[int | None, list[OutlineNode]]] = defaultdict(lambda: defaultdict(list))
    loose = []
    for pid in sorted(g.projects):
        p = g.projects[pid]
        if p["status"] == _ARCHIVED or (only is not None and pid != only):
            continue
        node = project_node(g, pid, ordered)
        stage, bet = g.stages.get(p["stage_id"]), g.bets.get(p["bet_id"])
        cycle_id = (stage or bet or {}).get("cycle_id")
        if cycle_id in g.cycles:
            by_cycle[cycle_id][stage["id"] if stage else None].append(node)
        else:
            loose.append(node)
    rows = []
    for cid in sorted(by_cycle, key=lambda i: g.cycles[i]["number"]):
        c = g.cycles[cid]
        kids = [OutlineNode(kind="stage", id=sid, title=owner_text.title(g.stages[sid]["title"], 80),
                            status=g.stages[sid]["status"], counts=_sum([n.counts for n in nodes]), children=nodes)
                for sid, nodes in sorted((k, v) for k, v in by_cycle[cid].items() if k is not None)]
        kids += by_cycle[cid].get(None, [])
        rows.append(OutlineNode(kind="cycle", id=cid, number=c["number"], status=c["state"],
                                counts=_sum([n.counts for n in kids]), children=kids))
    rows += loose
    return Outline(goal=g.goal, counts=_sum([r.counts for r in rows]), children=rows)
