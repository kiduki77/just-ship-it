"""What the owner's open cards hold up (docs/design/work-path.md 1.5): the one read every gate, scheduler and waker uses,
so they cannot disagree about whether a task waits, or whether the run does."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.contracts.requests import RequestStatus, RequestType
from justshipit.contracts.work import TERMINAL_TASK_STATUSES, TaskStatus
from justshipit.contracts.work_path import HoldPhase, HoldScope, Rel, TargetKind
from justshipit.db.models import AgentRequest, Job, Project, RequestLink, Task
from justshipit.services import queue, requests

_TERMINAL = {st.value for st in TERMINAL_TASK_STATUSES}


@dataclass(frozen=True)
class Hold:
    request_id: int  # the card the owner answers: the one that now carries the held card's decision
    task_id: int
    phase: HoldPhase


def live_holds(s: Session, project_id: int) -> list[Hold]:
    """The task holds of a project that still wait. A hold is live while its link is not ended, its card (the one it was
    merged or superseded into, `requests.current`) is PENDING, and its task is not terminal, so a task that realign
    cancels drops out with no write. A task hold names its phase; a `holds` link without one is not read."""
    found: dict[tuple[int, int, HoldPhase], Hold] = {}
    for link, status in s.execute(
            select(RequestLink, Task.status).join(Task, Task.id == RequestLink.target_id)
            .where(RequestLink.rel == Rel.HOLDS.value, RequestLink.target_kind == TargetKind.TASK.value,
                   RequestLink.ended_at.is_(None), RequestLink.phase.is_not(None), Task.project_id == project_id)
            .order_by(RequestLink.id)):
        card = requests.current(s, s.get(AgentRequest, link.request_id))
        if status in _TERMINAL or card is None or card.status != RequestStatus.PENDING.value:
            continue
        hold = Hold(card.id, link.target_id, HoldPhase(link.phase))
        found[(hold.request_id, hold.task_id, hold.phase)] = hold
    return list(found.values())


def held(s: Session, project_id: int, phase: HoldPhase) -> set[int]:
    """The project's tasks an open card holds at this phase: one that may not start, or one whose result waits."""
    return {h.task_id for h in live_holds(s, project_id) if h.phase == phase}


PARKED = ("WAITING_HUMAN", "PAUSED")  # a run the gate stopped, which only a resume moves on


class ParkReason(StrEnum):
    """Why a run waits for the owner, each outranking the ones after it."""
    PAUSED = "paused"      # the owner paused the run
    RUN = "run"            # a card holds the whole run
    PLANNING = "planning"  # a card holds the CEO's next plan step, and no task is left to go on before it
    STARVED = "starved"    # no task can start: some are held and none runs


@dataclass(frozen=True)
class Work:
    """A task as the park rule reads it."""
    status: str
    depends_on: tuple[int, ...]


_UNDER_WAY = {TaskStatus.RUNNING.value, TaskStatus.REVIEW.value, TaskStatus.REWORK.value}


def moves(tasks: Mapping[int, Work], live: Iterable[Hold]) -> bool:
    """Whether some task of the project goes on without the owner: one under way, one no card holds that is ready or
    whose dependencies have all ended (`tasks.promote_ready`), or a result whose cards are all decided, which the gate
    settles. While one does, a card that holds tasks or the next plan step leaves the run going."""
    live = list(live)
    start = {h.task_id for h in live if h.phase == HoldPhase.START}
    finish = {h.task_id for h in live if h.phase == HoldPhase.FINISH}
    for tid, t in tasks.items():
        if (t.status in _UNDER_WAY
                or (t.status == TaskStatus.APPROVAL_REQUIRED.value and tid not in finish)
                or (t.status == TaskStatus.READY.value and tid not in start)
                or (t.status == TaskStatus.PROPOSED.value and tid not in start
                    and all(tasks[d].status in _TERMINAL for d in t.depends_on if d in tasks))):
            return True
    return False


def parks(card_id: int, status: str, scope: str, live: Iterable[Hold], *, stalled: bool) -> ParkReason | None:
    """What one card parks, given its project's live holds and whether any task can still go on without the owner
    (`stalled` when none can). A `run` card parks the run. A `planning` card parks it once nothing is left to go on,
    since the run's next step is then a plan step. A card that holds tasks parks it only then too, while one of its
    tasks still waits, so a task that ended frees it with no write. Nothing once it is answered or when it holds
    nothing."""
    if status != RequestStatus.PENDING.value or scope == HoldScope.NONE.value:
        return None
    if scope == HoldScope.RUN.value:
        return ParkReason.RUN
    if not stalled:
        return None
    if scope == HoldScope.PLANNING.value:
        return ParkReason.PLANNING
    return ParkReason.STARVED if any(h.request_id == card_id for h in live) else None


def _work(s: Session, project_id: int) -> dict[int, Work]:
    return {tid: Work(status, tuple(deps or ())) for tid, status, deps in s.execute(
        select(Task.id, Task.status, Task.depends_on).where(Task.project_id == project_id))}


def _parked(s: Session, project_id: int, besides: int | None = None) -> list[tuple[AgentRequest, ParkReason]]:
    live = [h for h in live_holds(s, project_id) if h.request_id != besides]
    stalled = not moves(_work(s, project_id), live)
    cards = s.scalars(select(AgentRequest).where(
        AgentRequest.project_id == project_id, AgentRequest.status == RequestStatus.PENDING.value,
        AgentRequest.hold_scope != HoldScope.NONE.value).order_by(AgentRequest.id))
    return [(r, why) for r in cards
            if r.id != besides and (why := parks(r.id, r.status, r.hold_scope, live, stalled=stalled)) is not None]


def parking(s: Session, project_id: int) -> list[AgentRequest]:
    """The project's cards that park its run, oldest first: what the gate says the run waits for."""
    return [r for r, _ in _parked(s, project_id)]


def park_reason(s: Session, project_id: int, *, besides: int | None = None) -> ParkReason | None:
    """Why the project's run waits for the owner, or None when it may go on. The run gate, `schedule`, `ceo_review`,
    every waker (`requests._maybe_resume`, `runner.resume`, `control.resume`) and `reconcile_parked` decide from this
    and nothing else, so the gate and the code that wakes a waiting run cannot disagree. `besides`: as if that card
    were decided, which is what its card says answering it does to the run."""
    from justshipit.services import control  # at module level it would close an import cycle through requests
    if control.is_paused(s.get(Project, project_id)):
        return ParkReason.PAUSED
    found = {why for _, why in _parked(s, project_id, besides)}
    return next((why for why in ParkReason if why in found), None)


def replanning(s: Session, project_id: int, *, besides_running: bool = False) -> bool:
    """Whether a re-plan of this project is queued or running. Until it is done nothing else resumes a waiting run, so
    the run never goes on with the plan that re-plan is changing (design 7, Order on resume): one queued with
    `resume_after` resumes the run itself, and the sweep resumes it after any other, such as one an answer queued in the
    window before the gate wrote WAITING_HUMAN. `besides_running`: only one still to start that resumes the run, which
    is what the running re-plan asks before it resumes the run itself; re-plans of a project run one at a time."""
    q = select(Job.id).where(Job.kind == "realign_project", Job.lock_key == f"realign:{project_id}")
    q = (q.where(Job.status == "queued", Job.payload.contains({"resume_after": True})) if besides_running
         else q.where(queue.in_flight()))
    return s.scalar(q.limit(1)) is not None


SWEEP_RETRY = timedelta(minutes=10)


def reconcile_parked(s: Session, now: datetime | None = None) -> list[int]:
    """Queue a resume for every run the gate stopped that has nothing left to wait for and no run job or re-plan in
    flight. An answer committed between the gate's check and its WAITING_HUMAN write saw a running project and woke
    nothing, and a held task that ended frees a run with no write at all; the worker runs this every tick. One resume
    per version of the project row and per SWEEP_RETRY: a resume that finds nothing to resume writes nothing, and one
    skipped because a card held the run then must be asked again once that card's task ends, which writes no project
    row either. Returns the projects it queued a resume for."""
    window = int((now or datetime.now(UTC)).timestamp() // SWEEP_RETRY.total_seconds())
    busy = set(s.scalars(select(Job.lock_key).where(Job.kind.in_(queue.RUN_KINDS), queue.in_flight())))
    queued = []
    for p in s.scalars(select(Project).where(Project.status.in_(PARKED)).order_by(Project.id)).all():
        if f"project:{p.id}" in busy or park_reason(s, p.id) is not None or replanning(s, p.id):
            continue
        if queue.enqueue(s, "resume_project", {"project_id": p.id},
                         idempotency_key=f"resume:{p.id}:swept:{p.updated_at.isoformat()}:{window}") is not None:
            queued.append(p.id)
    return queued


_COUNTS = {RequestStatus.APPROVED.value, RequestStatus.ANSWERED.value, RequestStatus.AUTO_APPROVED.value}


def result_counts(s: Session, task_id: int) -> bool:
    """Whether a result that waited on the owner (APPROVAL_REQUIRED) counts: every card that holds its finish said yes,
    each followed to the card that now carries it. A question nobody answered that went on with its default says the yes
    its default stands for; one that closed says no. A task held before these records has no finish hold when its card
    was answered or merged before the backfill, which wrote one only for an open card: it is judged by the blocking
    approvals it raised, never by a later card about something else that carries its task id."""
    held = list(s.scalars(select(RequestLink.request_id).where(
        RequestLink.rel == Rel.HOLDS.value, RequestLink.target_kind == TargetKind.TASK.value,
        RequestLink.target_id == task_id, RequestLink.phase == HoldPhase.FINISH.value, RequestLink.ended_at.is_(None))))
    if not held:
        held = list(s.scalars(select(AgentRequest.id).join(RequestLink, RequestLink.request_id == AgentRequest.id).where(
            RequestLink.rel == Rel.RAISED_BY.value, RequestLink.target_kind == TargetKind.TASK.value,
            RequestLink.target_id == task_id, AgentRequest.type == RequestType.APPROVAL.value,
            AgentRequest.hold_scope != HoldScope.NONE.value)))
    cards = [requests.current(s, s.get(AgentRequest, i)) for i in dict.fromkeys(held)]
    return bool(cards) and all(c is not None and (c.status in _COUNTS or requests.defaulted(c)) for c in cards)
