"""The work path read model (docs/design/work-path.md 3 and 4): one graph of a business's projects, tasks, cards and the
links between them, read with a handful of `IN` queries, and the pure projections over it. `card_context` is the first
(the decision card's place in the work); the list and the path view read the same graph later, so the card, the list and
the graph cannot count differently. Nothing here writes, and nothing is guessed: what the records lack is `unknown`."""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from justshipit.agents import roles
from justshipit.contracts import owner_text
from justshipit.contracts.card_context import (
    AfterLine,
    CardContext,
    HeldTask,
    Holds,
    Known,
    Ref,
    Step,
    Unanswered,
    Why,
)
from justshipit.contracts.requests import RequestStatus
from justshipit.contracts.work import TERMINAL_TASK_STATUSES
from justshipit.contracts.work_path import ExpiryPolicy, HoldPhase, HoldScope, Rel, TargetKind
from justshipit.db.models import (
    AgentRequest,
    Bet,
    Business,
    Cycle,
    CycleStage,
    Decision,
    Evidence,
    LLMCallRecord,
    OwnerTask,
    Project,
    RequestLink,
    ReviewRound,
    Task,
)
from justshipit.services import holds as holds_service
from justshipit.services import roadmap

log = logging.getLogger(__name__)

Row = dict[str, Any]

_TERMINAL = {st.value for st in TERMINAL_TASK_STATUSES}
# The model calls whose purpose names a CEO step; any other purpose is "a recorded model call".
PROJECT_MANAGER_CALLS = ("plan", "review", "decision")
# The targets of a `raised_by` link that the console names by kind alone: a card the server filed for its own flow.
SERVER_STEPS = (TargetKind.DEV_JOB, TargetKind.SPEC, TargetKind.PLAN, TargetKind.BET, TargetKind.IDEA, TargetKind.SKILL,
                TargetKind.JOB)
_UNSTARTED = ("PROPOSED", "READY")
_REF_KINDS = ("evidence", "task", "request")
_REQUEST = ("id", "project_id", "task_id", "from_agent", "type", "title", "status", "hold_scope", "context_version",
            "ask_reason", "reason_refs", "reason_note", "expiry_policy", "default_option", "expires_at", "created_at")
_LINK = ("id", "request_id", "rel", "target_kind", "target_id", "phase", "option_key", "effect", "known", "recorded_at",
         "ended_at")
_TASK = ("id", "project_id", "key", "title", "status", "depends_on", "finished_at", "updated_at")
_TODO = ("id", "project_id", "request_id", "kind", "title")


@dataclass(frozen=True)
class WorkGraph:
    """Plain rows, so a projection is pure. `holds` come from `services/holds.py`, the one liveness read."""
    goal: str | None
    recorded_since: datetime | None
    projects: dict[int, Row]
    cycles: dict[int, Row]
    stages: dict[int, Row]
    bets: dict[int, Row]
    tasks: dict[int, Row]
    requests: dict[int, Row]
    links: dict[int, tuple[Row, ...]]       # by request id, oldest first
    rounds: dict[int, Row]
    calls: dict[int, Row]
    decisions: dict[int, Row]
    evidence: dict[int, Row]
    todos: dict[int, Row]                   # the owner's open to-dos of these projects
    holds: tuple[holds_service.Hold, ...]


def _rows(s: Session, model: type, cols: Iterable[str], *where: Any) -> dict[int, Row]:
    found = (dict(r._mapping) for r in s.execute(select(*(getattr(model, c) for c in cols)).where(*where)))
    return {row["id"]: row for row in found}


def _goal(s: Session, business_id: int | None) -> str | None:
    """The plan's goal line in the owner's words; the business's one-liner while no plan is approved."""
    if business_id is None:
        return None
    brief = roadmap.approved_brief(s, business_id)
    lines = roadmap.lines_by_key(brief.sections).get("goal_deadline", []) if brief else []
    text = next((x["text"] for x in lines if x["provenance"] in roadmap.SETTLED), None)
    text = text or s.scalar(select(Business.one_liner).where(Business.id == business_id))
    return owner_text.one_line(text, 120) if text else None


def load(s: Session, business_id: int | None, projects: Iterable[int] | None = None, *,
         cards: Iterable[int] = ()) -> WorkGraph:
    """The graph of `projects` (default: every project of the business): their tasks, their pending cards and the
    cards in `cards`, those cards' links, and the rows above and beside them."""
    pids = (list(projects) if projects is not None else
            list(s.scalars(select(Project.id).where(Project.business_id == business_id))) if business_id is not None
            else [])
    proj = _rows(s, Project, ("id", "name", "business_id", "bet_id", "stage_id", "plan_round", "status"), Project.id.in_(pids))
    bets = _rows(s, Bet, ("id", "title", "status", "cycle_id"),
                 Bet.id.in_({p["bet_id"] for p in proj.values() if p["bet_id"]}))
    stages = _rows(s, CycleStage, ("id", "title", "status", "cycle_id"),
                   CycleStage.id.in_({p["stage_id"] for p in proj.values() if p["stage_id"]}))
    cycles = _rows(s, Cycle, ("id", "number", "state"),
                   Cycle.id.in_({x["cycle_id"] for x in (*bets.values(), *stages.values()) if x["cycle_id"]}))
    reqs = _rows(s, AgentRequest, _REQUEST, or_(and_(AgentRequest.project_id.in_(pids),
                                                      AgentRequest.status == RequestStatus.PENDING.value),
                                                 AgentRequest.id.in_(set(cards))))
    links = _rows(s, RequestLink, _LINK, RequestLink.request_id.in_(reqs))
    named = {k: {row["target_id"] for row in links.values() if row["target_kind"] == k.value} for k in TargetKind}
    refs = defaultdict(set)
    for r in reqs.values():
        for ref in r["reason_refs"] or []:
            if ref.get("kind") in _REF_KINDS:
                refs[ref["kind"]].add(ref["id"])
    reqs |= _rows(s, AgentRequest, _REQUEST, AgentRequest.id.in_((named[TargetKind.REQUEST] | refs["request"]) - set(reqs)))
    extra_tasks = named[TargetKind.TASK] | refs["task"] | {r["task_id"] for r in reqs.values() if r["task_id"]}
    tasks = _rows(s, Task, _TASK, or_(Task.project_id.in_(pids), Task.id.in_(extra_tasks)))
    by_request: dict[int, list[Row]] = defaultdict(list)
    for row in sorted(links.values(), key=lambda x: x["id"]):
        by_request[row["request_id"]].append(row)
    return WorkGraph(
        goal=_goal(s, business_id),
        recorded_since=s.scalar(select(func.min(AgentRequest.created_at)).where(AgentRequest.context_version >= 1)),
        projects=proj, cycles=cycles, stages=stages, bets=bets, tasks=tasks, requests=reqs,
        links={k: tuple(v) for k, v in by_request.items()},
        rounds=_rows(s, ReviewRound, ("id", "project_id", "seq", "kind", "plan_round"),
                     or_(ReviewRound.project_id.in_(pids), ReviewRound.id.in_(named[TargetKind.ROUND]))),
        calls=_rows(s, LLMCallRecord, ("id", "agent", "purpose"), LLMCallRecord.id.in_(named[TargetKind.CALL])),
        decisions=_rows(s, Decision, ("id", "title"), Decision.id.in_(named[TargetKind.DECISION])),
        evidence=_rows(s, Evidence, ("id", "claim"), Evidence.id.in_(refs["evidence"])),
        todos=_rows(s, OwnerTask, _TODO, OwnerTask.status == "OPEN", OwnerTask.project_id.in_(pids)),
        holds=tuple(h for pid in pids for h in holds_service.live_holds(s, pid)))


# ------------------------------------------------------------------ the card

def card_for(s: Session, req: AgentRequest, *, says: str | None = None, now: datetime) -> CardContext:
    """The context of one card: its project's graph, then the projection. `says` is what approving does, in the
    owner's words, which `requests.report` already wrote for the card."""
    project = s.get(Project, req.project_id) if req.project_id else None
    g = load(s, project.business_id if project else None, [project.id] if project else [], cards=[req.id])
    return card_context(g, req.id, now, says=says)


def card_or_none(s: Session, req: AgentRequest, *, says: str | None = None) -> CardContext | None:
    """`card_for` now, or None when the read fails: the card, and a chat about it, never depend on its context. The
    savepoint keeps a failed read from poisoning the caller's transaction."""
    try:
        with s.begin_nested():
            return card_for(s, req, says=says, now=datetime.now(UTC))
    except Exception:
        log.exception("card context of request %s failed", req.id)
        return None


def card_context(g: WorkGraph, request_id: int, now: datetime, *, says: str | None = None) -> CardContext:
    r = g.requests[request_id]
    pending = r["status"] == RequestStatus.PENDING.value
    links = [x for x in g.links.get(request_id, ()) if x["ended_at"] is None]
    askers = [asker(g, x) for x in links if x["rel"] == Rel.RAISED_BY.value]
    first = askers[0] if askers else Step(kind="origin", label_key="path.step.unknown", known="unknown")
    chain = [*above(g, r), first, Step(kind="this", ref=f"REQ-{request_id}", ref_id=request_id,
                                        label_key="path.step.this", vars={"title": owner_text.title(r["title"], 120)},
                                        state=r["status"], known="recorded")]
    why = _why(g, r, links)
    held = _held(g, r, links) if pending else None
    unanswered = _unanswered(r, held.tasks) if held else None
    work = {tid: holds_service.Work(t["status"], tuple(t["depends_on"] or ())) for tid, t in g.tasks.items()
            if t["project_id"] == r["project_id"]}
    return CardContext(
        request_id=request_id, status=r["status"], decided=not pending, why=why, chain=chain, also_asked=askers[1:],
        parks_project=holds_service.parks(request_id, r["status"], r["hold_scope"], g.holds,
                                          stalled=_run_live(g, r) and not holds_service.moves(work, g.holds))
        is not None, holds=held,
        after=_after(g, links, pending, says), unanswered=unanswered,
        complete=all(x.known != "unknown" for x in chain) and why.reason is not None
        and (not pending or (unanswered is not None and unanswered.policy is not None)),
        recorded_since=g.recorded_since, as_of=now)


_LIVE_RUN = ("RUNNING", "WAITING_HUMAN", "PAUSED")


def _run_live(g: WorkGraph, r: Row) -> bool:
    """Whether the card's project has a run a card could stall. A finished project, or a card with none, has nothing
    left that waits on it."""
    return (g.projects.get(r["project_id"]) or {}).get("status") in _LIVE_RUN


def _step(kind: Any, key: str, known: Known, *, ref: str | None = None, ref_id: int | None = None,
          state: str | None = None, **vars: str | int) -> Step:
    return Step(kind=kind, ref=ref, ref_id=ref_id, label_key=f"path.step.{key}", vars=vars, state=state, known=known)


def above(g: WorkGraph, r: Row) -> list[Step]:
    """The goal, the stage or cycle, the experiment and the project above a card, as far as its project reaches."""
    if (p := g.projects.get(r["project_id"])) is None:
        return []
    steps = [_step("goal", "goal", "derived", text=g.goal)] if g.goal else []
    stage, bet = g.stages.get(p["stage_id"]), g.bets.get(p["bet_id"])
    cycle = g.cycles.get((stage or bet or {}).get("cycle_id"))
    if stage and cycle:
        steps.append(_step("stage", "stage", "recorded", ref_id=stage["id"], state=stage["status"],
                           title=owner_text.title(stage["title"], 80), n=cycle["number"]))
    elif cycle:
        steps.append(_step("cycle", "cycle", "recorded", ref_id=cycle["id"], state=cycle["state"], n=cycle["number"]))
    if bet:
        steps.append(_step("experiment", "experiment", "recorded", ref_id=bet["id"], state=bet["status"],
                           title=owner_text.title(bet["title"], 80)))
    steps.append(_step("project", "project", "recorded", ref=f"P-{p['id']}", ref_id=p["id"],
                       name=owner_text.title(p["name"], 80)))
    return steps


def asker(g: WorkGraph, link: Row) -> Step:
    """What led to the card, from one `raised_by` link, as sure as the link says it is."""
    kind, tid, known = link["target_kind"], link["target_id"], link["known"]
    if kind == TargetKind.TASK.value and (t := g.tasks.get(tid)):
        return _step("task", "task", known, ref=f"T-{tid}", ref_id=tid, state=t["status"],
                     title=owner_text.title(t["title"], 80))
    if kind == TargetKind.ROUND.value and (rd := g.rounds.get(tid)):
        return _step("round", f"round.{rd['kind']}", known, ref_id=tid, round=rd["plan_round"])
    if kind == TargetKind.DECISION.value and (d := g.decisions.get(tid)):
        return _step("decision", "decision", known, ref=f"DEC-{tid}", ref_id=tid, title=owner_text.title(d["title"], 80))
    if kind == TargetKind.REQUEST.value and (q := g.requests.get(tid)):
        return _step("decision", "request", known, ref=f"REQ-{tid}", ref_id=tid, state=q["status"],
                     title=owner_text.title(q["title"], 80))
    if kind == TargetKind.CHAT_MESSAGE.value:
        return _step("chat", "chat", known, ref_id=tid)
    if kind == TargetKind.CALL.value and (c := g.calls.get(tid)):
        project_manager = roles.current(c["agent"]) == roles.PROJECT_MANAGER and c["purpose"] in PROJECT_MANAGER_CALLS
        return _step("call", f"call.{c['purpose'] if project_manager else 'other'}", known)
    if kind in SERVER_STEPS:
        return _step("server", f"server.{kind}", known, ref_id=tid)
    return Step(kind="origin", label_key="path.step.unknown", known="unknown")


def _ref(g: WorkGraph, kind: Any, id_: int) -> Ref:
    """A thing a card points at; its label and state when the graph holds the row."""
    if kind == "task" and (t := g.tasks.get(id_)):
        return Ref(kind="task", id=id_, label=owner_text.title(t["title"], 80), state=t["status"])
    if kind == "request" and (q := g.requests.get(id_)):
        return Ref(kind="request", id=id_, label=owner_text.title(q["title"], 80), state=q["status"])
    if kind == "evidence" and (e := g.evidence.get(id_)):
        return Ref(kind="evidence", id=id_, label=owner_text.one_line(e["claim"], 80))
    return Ref(kind=kind, id=id_)


def _why(g: WorkGraph, r: Row, links: list[Row]) -> Why:
    refs = [_ref(g, x["kind"], x["id"]) for x in r["reason_refs"] or [] if x.get("kind") in _REF_KINDS]
    # The note is the one stored line that can go stale: its round was followed by another, or a task it cites ended.
    changed = [x for x in refs if x.kind == "task" and x.state in _TERMINAL]
    for link in links:
        if link["rel"] == Rel.RAISED_BY.value and link["target_kind"] == TargetKind.ROUND.value:
            rd = g.rounds.get(link["target_id"])
            if rd and any(o["project_id"] == rd["project_id"] and o["seq"] > rd["seq"] for o in g.rounds.values()):
                changed.append(Ref(kind="round", id=rd["id"]))
    note = owner_text.one_line(r["reason_note"], 120) if r["reason_note"] else None
    return Why(reason=r["ask_reason"], refs=refs, note=note, agent=r["from_agent"], at=r["created_at"],
               recorded=r["context_version"] >= 1, stale=bool(note and changed), changed=changed)


def _held(g: WorkGraph, r: Row, links: list[Row]) -> Holds:
    """What a pending card holds up. The tasks that still wait come from `services/holds.py`; a task the card held that
    no longer waits is listed as ended, so the card can say it holds nothing now."""
    live = sorted((h for h in g.holds if h.request_id == r["id"]), key=lambda h: (h.task_id, h.phase.value))
    tasks = [_held_task(g.tasks[h.task_id], h.phase, True) for h in live if h.task_id in g.tasks]
    waiting = {t.id for t in tasks}
    tasks += [_held_task(g.tasks[x["target_id"]], HoldPhase(x["phase"]), False) for x in links
              if x["rel"] == Rel.HOLDS.value and x["target_kind"] == TargetKind.TASK.value and x["phase"]
              and x["target_id"] in g.tasks and x["target_id"] not in waiting]
    reach = reach_of(g, r, waiting)
    return Holds(scope=HoldScope(r["hold_scope"]), tasks=tasks, frees=reach.frees, behind=reach.behind,
                 nothing_live=reach.moot)


@dataclass(frozen=True)
class Reach:
    """What answering a pending card moves, by design 5.1: `frees` tasks go on at once (`planning` also counts the next
    plan step as one), `behind` get closer to running. `moot`: it holds tasks, and none of them still waits."""
    frees: int
    behind: int
    moot: bool


def reach_of(g: WorkGraph, r: Row, waiting: set[int] | None = None) -> Reach:
    """`frees` and `behind` of one pending card, the one count the card, the your-turn list and the path share. A
    `tasks` card frees the held tasks no other card holds and counts the ones after them as behind; a `run` card frees
    every task of its project that has not ended; a `planning` card frees the project's unstarted tasks and the next
    plan step."""
    scope = HoldScope(r["hold_scope"])
    mine = [t for t in g.tasks.values() if t["project_id"] == r["project_id"] and t["status"] not in _TERMINAL]
    if scope == HoldScope.RUN:
        return Reach(len(mine), 0, False)
    if scope == HoldScope.PLANNING:
        return Reach(sum(1 for t in mine if t["status"] in _UNSTARTED) + 1, 0, False)
    if scope == HoldScope.NONE:
        return Reach(0, 0, False)
    if waiting is None:
        waiting = {h.task_id for h in g.holds if h.request_id == r["id"]}
    elsewhere = {h.task_id for h in g.holds if h.request_id != r["id"]}
    frees = waiting - elsewhere
    return Reach(len(frees), len(_downstream(g, waiting) - frees), not waiting)


def _held_task(t: Row, phase: HoldPhase, live: bool) -> HeldTask:
    return HeldTask(id=t["id"], key=t["key"], title=owner_text.title(t["title"], 80), phase=phase, state=t["status"],
                    live=live, ended_on=None if live else t["finished_at"] or t["updated_at"])


def _downstream(g: WorkGraph, start: set[int]) -> set[int]:
    """The tasks still to run after `start`, through `depends_on`, not counting `start` itself unless another path
    reaches it."""
    after: dict[int, list[int]] = defaultdict(list)
    for t in g.tasks.values():
        if t["status"] not in _TERMINAL:
            for dep in t["depends_on"] or []:
                after[dep].append(t["id"])
    found: set[int] = set()
    todo = list(start)
    while todo:
        for nxt in after.get(todo.pop(), ()):
            if nxt not in found:
                found.add(nxt)
                todo.append(nxt)
    return found


def _after(g: WorkGraph, links: list[Row], pending: bool, says: str | None) -> list[AfterLine]:
    """Before the answer: what the agent declared and what the server will do. After it: what ran."""
    rel = Rel.ANSWER_USE if pending else Rel.FOLLOWED
    grouped: dict[tuple[str | None, str | None], list[Row]] = defaultdict(list)
    for x in links:
        if x["rel"] == rel.value:
            grouped[(x["option_key"], x["effect"])].append(x)
    lines = [AfterLine(source="declared" if pending else "followed", option_key=key, effect=effect,
                       targets=[_ref(g, x["target_kind"], x["target_id"]) for x in group
                                if x["target_kind"] == TargetKind.TASK.value],
                       at=None if pending else max(x["recorded_at"] for x in group))
             for (key, effect), group in grouped.items()]
    return [*lines, AfterLine(source="server", text=says)] if pending and says else lines


def _unanswered(r: Row, held: list[HeldTask]) -> Unanswered:
    """What happens if nobody answers: the recorded rule and date, never a rule read from the date alone."""
    policy = ExpiryPolicy(r["expiry_policy"]) if r["expiry_policy"] else None
    return Unanswered(policy=policy, when=r["expires_at"], default_option=r["default_option"], affects=[x for x in held if x.live])
