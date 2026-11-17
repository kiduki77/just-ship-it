"""A handler whose lease was reclaimed stops at its next side-effect boundary and leaves every row to the new
holder: no finish, no FAILED, no further model call."""
import threading
import time

import pytest
from justshipit.db.models import Job, Project
from justshipit.db.session import session_scope
from justshipit.services import queue
from justshipit.settings import company_config
from justshipit.workers import runner
from pydantic import BaseModel
from sqlalchemy import text


pytestmark = pytest.mark.usefixtures("made_up_kinds")


class Out(BaseModel):
    ok: bool


@pytest.fixture(autouse=True)
def _isolated_jobs():
    yield
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL WHERE kind LIKE 'lost_%'"))


def _enqueue(kind: str, payload: dict) -> int:
    with session_scope() as s:
        return queue.enqueue(s, kind, payload, max_attempts=1).id


def _job(job_id: int) -> Job:
    with session_scope() as s:
        j = s.get(Job, job_id)
        s.expunge(j)
        return j


def _wait(predicate, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def _taken_by_another_worker(job_id: int) -> None:
    """What a reclaim and a claim by worker-b leave on the row."""
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET locked_by = 'worker-b', lease_token = 'other', locked_at = now(), "
                       "attempts = 2 WHERE id = :id"), {"id": job_id})


def test_a_handler_whose_lease_is_lost_stops_at_its_next_model_call_and_does_not_finish(docker_log, monkeypatch,
                                                                                         caplog):
    from justshipit.agents import executor
    from justshipit.runtime import LLMResult

    monkeypatch.setitem(company_config()["operations"], "job_heartbeat_s", 0.05)
    calls: list[str] = []

    def model(call):
        calls.append(call.prompt)
        return LLMResult(call_id=call.call_id, provider=call.provider, model="m", output={"ok": True},
                         input_tokens=1, output_tokens=1, cached_tokens=0, cost_usd=0.0, duration_ms=1,
                         session_id=None)

    monkeypatch.setattr(executor, "run_with_retry", model)
    first_done, proceed, outcome = threading.Event(), threading.Event(), []

    def handler(payload):
        if payload.get("project_id") != 940001:
            return
        executor.run_agent("project_manager", "plan", "first", Out, project_id=None)
        first_done.set()
        proceed.wait(10)
        try:
            executor.run_agent("project_manager", "plan", "second", Out, project_id=None)
        except queue.LeaseLost:
            outcome.append("stopped")
            raise
        outcome.append("second call returned")

    monkeypatch.setattr(runner, "_handlers", lambda: {"lost_run": handler})
    jid = _enqueue("lost_run", {"project_id": 940001})
    stop = threading.Event()
    t = threading.Thread(target=runner.serve, kwargs={"concurrency": 1, "poll_s": 0.05, "stop": stop})
    t.start()
    try:
        assert first_done.wait(10)
        _taken_by_another_worker(jid)
        assert _wait(lambda: any(f"job {jid}" in r.getMessage() and "LEASE LOST" in r.getMessage()
                                 for r in caplog.records)), "the worker never noticed its lease was gone"
        proceed.set()
        assert _wait(lambda: outcome)
        time.sleep(0.3)
    finally:
        proceed.set()
        stop.set()
        t.join(10)
    assert outcome == ["stopped"]
    assert calls == ["first"]
    j = _job(jid)
    assert (j.status, j.locked_by, j.attempts) == ("running", "worker-b", 2)


def test_a_graph_node_does_not_start_once_the_lease_is_lost(s, project, monkeypatch):
    from justshipit.orchestration import company_graph
    from justshipit.orchestration import runner as orchestration

    monkeypatch.setattr(company_graph, "run_agent", lambda *a, **kw: pytest.fail("the plan node called a model"))
    s.commit()
    lease = queue.Lease(job_id=0, token="t")
    lease.lost.set()
    token = queue.current_lease.set(lease)
    try:
        with pytest.raises(queue.LeaseLost):
            orchestration.start(project.id)
    finally:
        queue.current_lease.reset(token)
    with session_scope() as s2:
        assert s2.get(Project, project.id).status == "CREATED"  # nothing written, FAILED least of all: the new holder's


def test_an_irreversible_step_confirms_the_lease_against_the_row():
    jid = _enqueue("lost_run", {"project_id": 940002})
    with session_scope() as s:
        claimed = queue.claim(s, "worker-a", ["lost_run"])
        assert claimed.id == jid
        lease = queue.Lease(job_id=jid, token=claimed.lease_token)
    token = queue.current_lease.set(lease)
    try:
        queue.confirm_lease()
        _taken_by_another_worker(jid)
        with pytest.raises(queue.LeaseLost):
            queue.confirm_lease()
        assert lease.lost.is_set()
    finally:
        queue.current_lease.reset(token)


def test_a_beat_that_ends_after_the_job_does_not_pin_it_as_held(monkeypatch):
    jid = _enqueue("lost_run", {"project_id": 940003})
    with session_scope() as s:
        lease = queue.Lease(job_id=jid, token=queue.claim(s, "worker-a", ["lost_run"]).lease_token)
    runs = runner.Runs()
    runs.add(lease)
    in_beat, go, real = threading.Event(), threading.Event(), queue.heartbeat

    def slow_heartbeat(s, leases):
        in_beat.set()
        go.wait(5)
        return real(s, leases)

    monkeypatch.setattr(queue, "heartbeat", slow_heartbeat)
    beat = threading.Thread(target=runner._beat, args=("worker-a", runs))
    beat.start()
    assert in_beat.wait(5)
    runs.pop(jid)  # the handler finished while the beat was in flight
    go.set()
    beat.join(10)
    assert runs.held() == {}



def test_a_stale_local_flag_is_checked_against_the_row_after_a_host_suspend():
    jid = _enqueue("lost_suspend", {"project_id": 950001})
    with session_scope() as s:
        token = queue.claim(s, "worker-a", ["lost_suspend"]).lease_token
    lease = queue.Lease(jid, token)
    lease.confirmed_at = time.time() - 2 * queue.lease_s()  # the host slept past the lease without a beat
    ctx = queue.current_lease.set(lease)
    try:
        queue.check_lease()  # the row is still ours
        _taken_by_another_worker(jid)
        lease.confirmed_at = time.time() - 2 * queue.lease_s()
        with pytest.raises(queue.LeaseLost):
            queue.check_lease()
    finally:
        queue.current_lease.reset(ctx)
