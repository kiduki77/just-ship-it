"""The gate's one list (services/gate.py): every job kind the worker handles is declared with the reason it may run, a
kind that is not declared is denied at every point, and a kind declared FREE cannot spend model usage."""
import threading

import pytest
from justshipit.agents import executor
from justshipit.contracts import notify as notices
from justshipit.contracts.work import AgentResultOut
from justshipit.db.models import Job
from justshipit.db.session import session_scope
from justshipit.services import gate, queue
from justshipit.services.usage import MustWait
from justshipit.workers import runner
from sqlalchemy import select


pytestmark = pytest.mark.usefixtures("own_jobs_settled")


class Spent(BaseException):
    """A model call got past the gate."""


@pytest.fixture
def no_model(monkeypatch):
    def spent(*_a, **_kw):
        raise Spent()
    monkeypatch.setattr(executor, "_run_once", spent)


def _free_lease(job_id: int = 990001):
    return queue.Lease(job_id, "t", gate="none")


def test_the_policy_declares_exactly_the_kinds_the_worker_handles():
    assert set(gate.POLICY) == set(runner._handlers())


def test_every_row_says_why_it_may_run_and_the_recurring_kinds_come_from_the_table():
    assert [k for k, r in gate.POLICY.items() if not r.why.strip()] == []
    assert set(gate.RECURRING_KINDS) == {k for k, r in gate.POLICY.items() if r.gate is gate.Gate.STAGE_RECURRING}
    assert {"find_people", "find_threads", "promotion_week"} <= set(gate.RECURRING_KINDS)


def test_a_kind_nobody_declared_is_refused_before_any_row_exists(s):
    with pytest.raises(gate.UndeclaredJobKind):
        queue.enqueue(s, "brand_new_kind", {"business_id": 1}, idempotency_key="undeclared:1")
    s.rollback()
    assert s.scalar(select(Job).where(Job.idempotency_key == "undeclared:1")) is None


def test_the_worker_refuses_to_start_with_a_handler_that_has_no_row(monkeypatch):
    real = runner._handlers
    monkeypatch.setattr(runner, "_handlers", lambda: {**real(), "brand_new_kind": lambda p: None})
    with pytest.raises(RuntimeError, match="brand_new_kind"):
        runner.serve(once=True, stop=threading.Event())


def test_a_free_job_commits_like_any_other(monkeypatch):
    """Probe B of the review: a FREE check inside check_lease failed every housekeeping job at its first commit."""
    ctx = queue.current_lease.set(_free_lease())
    try:
        with session_scope():
            pass
    finally:
        queue.current_lease.reset(ctx)


def test_a_free_job_that_calls_a_model_fails_at_the_call(no_model):
    ctx = queue.current_lease.set(_free_lease())
    try:
        with pytest.raises(queue.GiveUp, match="FREE"):
            executor.run_agent("project_manager", "plan", "x", AgentResultOut, project_id=None)
    finally:
        queue.current_lease.reset(ctx)


def test_a_model_call_outside_any_job_is_refused_unless_the_owner_asked_in_person(no_model):
    with pytest.raises(gate.GateViolation):
        executor.run_agent("project_manager", "plan", "x", AgentResultOut, project_id=None)
    with gate.asked("test:asked"), pytest.raises(Spent):
        executor.run_agent("project_manager", "plan", "x", AgentResultOut, project_id=None)


def _notice() -> dict:
    return notices.run_failed(990001, "P", "boom", 990001).model_dump(mode="json")


FREE_RUNS = {
    "notify": _notice,
    "daily_briefing": lambda: {"date": "2026-11-03"},
    "backup": dict,
    "retention": dict,
    "bet_sweep": dict,
    "execute_action": lambda: {"request_id": 2_000_000_000},
    "post_reactions": dict,
}


def test_every_free_handler_runs_without_reaching_a_model(no_model, monkeypatch, tmp_path):
    """Run each FREE handler under a FREE lease. A model call would end in GiveUp at run_agent (or Spent past it).
    Whatever else a handler raises on test data (a missing row, a backup tool) is not spending."""
    from justshipit.operations import backup
    monkeypatch.setattr(backup.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(OSError("no pg_dump here")))
    assert set(FREE_RUNS) == {k for k, r in gate.POLICY.items() if r.gate is gate.Gate.FREE}
    handlers = runner._handlers()
    for kind, payload in FREE_RUNS.items():
        ctx = queue.current_lease.set(_free_lease())
        try:
            handlers[kind]({**payload(), "attempt": 1, "max_attempts": 1})
        except (queue.GiveUp, Spent) as exc:
            pytest.fail(f"{kind} is declared FREE and reached a model: {exc!r}")
        except (Exception, queue.Hold, queue.LeaseLost, MustWait):  # noqa: BLE001, S110 - see the docstring
            pass
        finally:
            queue.current_lease.reset(ctx)
