"""A crashed worker's budget reservations are given back as soon as its job is reclaimed, while other workers run."""
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from justshipit.db.models import BudgetMonth, Job, Project
from justshipit.db.session import session_scope
from justshipit.services import budget, queue
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _isolated():
    budget.release_orphaned_reservations()  # what earlier tests left
    yield
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL WHERE kind = 'reservation_probe'"))


def _running(owner: str) -> queue.Lease:
    with session_scope() as s:
        job = Job(gate="direct", kind="reservation_probe", payload={}, status="running", attempts=1, max_attempts=3, locked_by=owner,
                  lease_token=uuid.uuid4().hex, locked_at=datetime.now(timezone.utc))
        s.add(job)
        s.flush()
        return queue.Lease(job.id, job.lease_token)


@contextmanager
def _inside(lease: queue.Lease):
    token = queue.current_lease.set(lease)
    try:
        yield
    finally:
        queue.current_lease.reset(token)


def _reserved(project_id: int) -> tuple[float, float]:
    with session_scope() as s:
        return s.get(BudgetMonth, budget._month()).reserved_usd, s.get(Project, project_id).reserved_usd


def test_a_dead_workers_reservation_is_released_while_another_worker_runs(s, project):
    s.commit()
    live, dead = _running("worker-live"), _running("worker-dead")
    with _inside(dead):
        lost = budget.reserve(project.id, None, "dev")
    with _inside(live):
        kept = budget.reserve(project.id, None, "work")
    month_before, project_before = _reserved(project.id)

    with session_scope() as s2:
        s2.get(Job, dead.job_id).locked_at = datetime.now(timezone.utc) - timedelta(hours=2)
        queue.reclaim(s2, {})
    assert budget.release_orphaned_reservations() == lost.amount
    assert _reserved(project.id) == (pytest.approx(month_before - lost.amount), project_before - lost.amount)

    budget.settle(project.id, None, lost, 0.0)  # the dead worker's host wakes up and settles its call
    assert _reserved(project.id) == (pytest.approx(month_before - lost.amount), project_before - lost.amount)

    budget.settle(project.id, None, kept, 0.0)
    assert _reserved(project.id) == (pytest.approx(month_before - lost.amount - kept.amount), 0.0)


def test_a_live_workers_reservation_stays_while_its_lease_holds(s, project):
    s.commit()
    live = _running("worker-live")
    with _inside(live):
        held = budget.reserve(project.id, None, "dev")
    assert budget.release_orphaned_reservations() == 0.0
    assert _reserved(project.id)[1] == held.amount
    budget.settle(project.id, None, held, 0.0)
