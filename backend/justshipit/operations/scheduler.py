"""Recurring operations. Schedules enqueue ordinary jobs; idempotency keys make ticks safe to repeat
and safe across several worker processes."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from justshipit.db.models import Business, BusinessBrief, Job, Kpi, Project, Schedule
from justshipit.operations import notify
from justshipit.services import cycle, gate, people_finder, queue, stops
from justshipit.settings import company_config

log = logging.getLogger(__name__)


def _ops() -> dict:
    return company_config().get("operations", {})


def ensure(s: Session, *, name: str, kind: str, hours: float, payload: dict | None = None,
           first_in_hours: float | None = None) -> None:
    """Create the schedule unless it exists. A schedule of the same kind and payload under another name is this
    one under the name it had before (they were Korean until the English names), so it takes this name instead of
    leaving two that both fire. Only the oldest such schedule is renamed: a second one under yet another name keeps
    its own, because two rows cannot take one name (kind and name are unique)."""
    other = aliased(Schedule)
    taken = select(other.id).where(other.kind == kind, other.name == name).exists()
    older_name = (select(Schedule.id).where(Schedule.kind == kind, Schedule.payload == (payload or {}),
                                            Schedule.name != name).order_by(Schedule.id).limit(1).scalar_subquery())
    s.execute(update(Schedule).where(Schedule.id == older_name, ~taken).values(name=name)
              .execution_options(synchronize_session=False))
    s.execute(insert(Schedule).values(
        name=name, kind=kind, payload=payload or {}, interval_hours=hours, enabled=True,
        next_run_at=datetime.now(timezone.utc) + timedelta(hours=hours if first_in_hours is None else first_in_hours),
    ).on_conflict_do_nothing(index_elements=["kind", "name"]))


def _bet_loop_schedules(s: Session, ops: dict) -> None:
    """The bet loop's clock: a sweep that ends running bets, a daily read of the posts' reactions, and for each active
    business with an approved brief a weekly promotion and, once it has a GitHub repo, a daily GitHub read."""
    ensure(s, name="Experiment sweep", kind="bet_sweep", hours=ops.get("bet_sweep_hours", 0.25))
    ensure(s, name="Post reactions", kind="post_reactions", hours=ops.get("post_reactions_hours", 12))
    approved = select(BusinessBrief.business_id).where(BusinessBrief.approved_at.is_not(None))
    for b in s.scalars(select(Business).where(Business.status == "ACTIVE", Business.id.in_(approved))):
        ensure(s, name=f"B-{b.id} weekly promotion", kind="promotion_week", hours=ops.get("promotion_week_hours", 168),
               payload={"business_id": b.id}, first_in_hours=24)
        if b.github_repo:
            ensure(s, name=f"B-{b.id} GitHub collection", kind="github_collect", hours=ops.get("github_collect_hours", 24),
                   payload={"business_id": b.id}, first_in_hours=1)


def sync(s: Session) -> None:
    """Derive schedules from company state (finished projects, projects with KPIs)."""
    ops = _ops()
    ensure(s, name="Weekly company review", kind="portfolio_review", hours=ops.get("portfolio_review_hours", 168))
    ensure(s, name="Keep-by cleanup", kind="retention", hours=24)
    ensure(s, name="DB backup", kind="backup", hours=24, first_in_hours=1)
    ensure(s, name="Weekly Skill Factory run", kind="skill_factory", hours=ops.get("skill_factory_hours", 168))
    _bet_loop_schedules(s, ops)
    for p in s.scalars(select(Project).where(Project.status == "DONE")):
        ensure(s, name=f"P-{p.id} change watch", kind="evidence_watch", hours=ops.get("evidence_watch_hours", 168),
               payload={"project_id": p.id})
    for pid in set(s.scalars(select(Kpi.project_id))):
        ensure(s, name=f"P-{pid} KPI review", kind="kpi_review", hours=ops.get("kpi_review_hours", 24),
               payload={"project_id": pid})
    _people_schedules(s)


def _people_schedules(s: Session) -> None:
    """For each active business with an approved brief: look for people to meet each week, and for public questions
    the owner can answer. The first run is the next tick, so a business starts with a list, not an empty page."""
    ops = _ops()
    approved = select(BusinessBrief.business_id).where(BusinessBrief.approved_at.is_not(None))
    live = set(s.scalars(select(Business.id).where(Business.status == "ACTIVE", Business.id.in_(approved))))
    for sc in s.scalars(select(Schedule).where(Schedule.kind.in_(people_finder.KINDS), Schedule.enabled.is_(True))):
        if sc.payload.get("business_id") not in live:
            sc.enabled = False
    for bid in live:
        ensure(s, name=f"B-{bid} find people", kind="find_people", hours=ops.get("find_people_hours", 168),
               payload={"business_id": bid}, first_in_hours=0)
        ensure(s, name=f"B-{bid} find posts to join", kind="find_threads", hours=ops.get("find_threads_hours", 168),
               payload={"business_id": bid}, first_in_hours=0)


def tick(s: Session) -> list[int]:
    """Fire the schedules that are due. A recurring kind no running stage lists is skipped by the queue, and its
    schedule still moves on a whole interval: no retry every minute. Starting a stage that lists the kind brings its
    schedule forward to now (cycle.py), so a weekly job runs in the stage that named it. While the owner has stopped the
    company, or the agent a kind belongs to, its schedules stay as they were: they fire once on the first tick after the
    resume, however many intervals the stop spanned, and a short stop does not push a weekly job out a week. The cycles
    do not move on either."""
    sync(s)
    now = datetime.now(timezone.utc)
    fired = []
    held = stops.read(s)
    for sc in s.scalars(select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_run_at <= now)
                        .with_for_update(skip_locked=True)):
        key = (people_finder.daily_key(sc.kind, sc.payload["business_id"]) if sc.kind in people_finder.KINDS
               else f"sched:{sc.id}:{sc.next_run_at.isoformat()}")
        if held.holds_kind(sc.kind):
            log.debug("schedule %s %s waits: the owner stopped it", sc.id, sc.kind)
            continue
        queue.enqueue(s, sc.kind, dict(sc.payload), idempotency_key=key, max_attempts=1)
        fired.append(sc.id)
        sc.last_run_at = now
        sc.next_run_at = now + timedelta(hours=sc.interval_hours)
    try:
        with s.begin_nested():
            notify.schedule_briefing(s)
    except Exception as exc:  # noqa: BLE001 - the other schedules have fired already
        log.error("scheduling the daily briefing failed: %s", exc)
    if held.company is not None:
        return fired
    try:
        with s.begin_nested():
            if moved := cycle.tick(s, now):
                log.info("cycle tick: %s", moved)
    except Exception as exc:  # noqa: BLE001 - the schedules have fired already; the next tick moves the stages
        log.error("moving the cycles on failed: %s", exc)
    return fired


@dataclass(frozen=True)
class RunNow:
    job: Job | None
    existing: bool        # a run of this routine was already in flight, and this is it
    outside_stage: bool   # no running stage would run it on its own: the owner's click is what runs it


def run_now(s: Session, schedule_id: int) -> RunNow:
    """The owner's "run it now": it runs whatever stage is open (gate.admit, by_owner). While a run of the same routine
    is still in flight (queue.in_flight: queued, parked, running, or held after a reclaim), whether this button or the
    schedule queued it, a second click returns that run instead of queueing another. The schedule row is locked so two
    clicks at once see each other."""
    sc = s.get(Schedule, schedule_id, with_for_update=True)
    payload = dict(sc.payload)
    outside = gate.admit(s, sc.kind, payload).verdict != "run"
    running = s.scalar(select(Job).where(Job.kind == sc.kind, Job.payload == payload, queue.in_flight())
                       .order_by(Job.id).limit(1))
    if running is not None:
        return RunNow(running, True, outside)
    job = queue.enqueue(s, sc.kind, payload, max_attempts=1, by_owner=True)
    sc.last_run_at = datetime.now(timezone.utc)
    return RunNow(job, False, outside)
