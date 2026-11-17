import os
import pathlib
import signal
import time
from datetime import datetime, timedelta, timezone

import pytest
from justshipit.db.models import BudgetMonth, Job
from justshipit.db.session import session_scope
from justshipit.services import budget, queue
from justshipit.runtime import children
from justshipit.workers import runner
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

pytestmark = pytest.mark.usefixtures("made_up_kinds")


@pytest.fixture(autouse=True)
def _isolated_jobs():
    yield
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL WHERE kind LIKE 'recovery_%'"))


def _running_job(owner: str, lease_age: timedelta, max_attempts: int = 1) -> int:
    with session_scope() as s:
        job = Job(gate="direct", kind="recovery_run", payload={}, status="running", attempts=1, max_attempts=max_attempts,
                  locked_by=owner, locked_at=datetime.now(timezone.utc) - lease_age)
        s.add(job)
        s.flush()
        return job.id


def _enqueue(kind: str) -> int:
    with session_scope() as s:
        return queue.enqueue(s, kind, {}).id


def _status(job_id: int) -> str:
    with session_scope() as s:
        return s.get(Job, job_id).status


def test_reservations_are_released_after_a_crash():
    budget.reserve(None, None, "dev")
    _running_job("worker-dead", timedelta(hours=3))
    assert budget.release_orphaned_reservations() == 0.0  # a running row is in flight until the queue settles it
    with session_scope() as s:
        queue.reclaim(s, {})
    assert budget.release_orphaned_reservations() >= 4.0
    with session_scope() as s:
        assert s.get(BudgetMonth, budget._month()).reserved_usd == 0.0


def test_worker_start_removes_engineer_and_test_containers_no_live_job_owns(docker_log, monkeypatch):
    monkeypatch.setattr(runner, "_handlers", lambda: {"recovery_noop": lambda p: None})
    _running_job("worker-dead", timedelta(hours=3))
    _running_job("worker-live", timedelta(seconds=5))
    runner.serve(concurrency=1, poll_s=0.01, once=True)
    removed = [line.split()[2:] for line in docker_log.read_text().splitlines() if line.startswith("rm -f")]
    assert removed == [["ship-eng-dead", "ship-eng-old", "ship-test-dead"]]


def test_a_test_container_carries_its_workers_labels_so_the_reaper_can_find_it(docker_log, tmp_path):
    from justshipit.engineering import worker
    from justshipit.sandbox import docker
    from justshipit.settings import PROCESS_ID, settings

    worker.run_tests(tmp_path, "pytest -q", image="product-tests:1")
    [run] = [line.split() for line in docker_log.read_text().splitlines() if line.startswith("run ")]
    assert run[run.index("--name") + 1].startswith("ship-test-")
    labels = {run[i + 1] for i, arg in enumerate(run) if arg == "--label"}
    assert labels == {f"{docker.OWNER_LABEL}={PROCESS_ID}", f"{docker.DEPLOYMENT_LABEL}={settings().deployment_id}"}


def test_a_test_run_that_times_out_removes_its_container(docker_log, tmp_path):
    from justshipit.engineering import worker

    fake = pathlib.Path(os.environ["PATH"].split(os.pathsep)[0]) / "docker"
    fake.write_text(fake.read_text() + '[ "$1" = run ] && sleep 10\nexit 0\n')
    assert worker.run_tests(tmp_path, "pytest -q", image="product-tests:1", timeout_s=1) == (
        False, "timeout after 1s (container removed)")
    log = [line.split() for line in docker_log.read_text().splitlines()]
    [run] = [a for a in log if a[0] == "run"]
    assert ["rm", "-f", run[run.index("--name") + 1]] in log


def test_reaper_only_lists_containers_of_its_own_deployment(docker_log, monkeypatch):
    from justshipit.sandbox import docker
    from justshipit.settings import settings

    monkeypatch.setattr(runner, "_handlers", lambda: {"recovery_noop": lambda p: None})
    runner.serve(concurrency=1, poll_s=0.01, once=True)
    [ps] = [line for line in docker_log.read_text().splitlines() if line.startswith("ps ")]
    assert f"--filter label={docker.DEPLOYMENT_LABEL}={settings().deployment_id}" in ps


def test_worker_start_without_docker(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(runner, "_handlers", lambda: {"recovery_noop": lambda p: None})
    runner.serve(concurrency=1, poll_s=0.01, once=True)


def test_database_error_while_claiming_does_not_stop_the_worker(docker_log, monkeypatch):
    real_claim, failures = queue.claim, []

    def flaky_claim(s, worker_id, kinds=None, **kw):
        if len(failures) < 2:
            failures.append(1)
            raise OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"))
        return real_claim(s, worker_id, kinds, **kw)

    monkeypatch.setattr(queue, "claim", flaky_claim)
    monkeypatch.setattr(runner, "_handlers", lambda: {"recovery_run": lambda p: None})
    jid = _enqueue("recovery_run")
    runner.serve(concurrency=1, poll_s=0.01, once=True)
    assert len(failures) == 2 and _status(jid) == "done"


def test_sigterm_stops_claiming_and_lets_the_running_job_finish(docker_log, monkeypatch):
    later: list[int] = []

    def first(_payload):
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.3)
        with session_scope(lease_checked=False) as s:  # not this job's write: a lease check would hand the job back
            later.append(queue.enqueue(s, "recovery_run", {}).id)
        time.sleep(0.3)

    monkeypatch.setattr(runner, "_handlers", lambda: {"recovery_first": first, "recovery_run": lambda p: None})
    seen: list[int] = []

    def test_handler(signum, frame):
        seen.append(signum)

    previous = signal.signal(signal.SIGTERM, test_handler)
    try:
        jid = _enqueue("recovery_first")
        runner.serve(concurrency=2, poll_s=0.01, once=True)
    finally:
        restored = signal.signal(signal.SIGTERM, previous)
    assert _status(jid) == "done"
    assert _status(later[0]) == "queued"
    assert restored is test_handler and not seen


def test_the_first_loop_ticks_even_on_a_freshly_booted_host(docker_log, monkeypatch):
    monkeypatch.setattr(runner, "_handlers", lambda: {"recovery_noop": lambda p: None})
    monkeypatch.setattr(runner.time, "monotonic", lambda: 12.0)
    runner.serve(concurrency=1, poll_s=0.01, once=True)
    assert any(line.startswith("ps ") for line in docker_log.read_text().splitlines())


SECOND_SIGNAL = """
import os, signal, threading, time
from justshipit.runtime import children
from justshipit.workers import runner
from justshipit.db.session import session_scope
from justshipit.services import gate, queue

stop = threading.Event()

def block(_p):
    os.kill(os.getpid(), signal.SIGINT)
    stop.wait(10)  # the main thread handles the first signal whenever it next runs; the second must follow it
    time.sleep(children.SAME_PRESS_S + 0.3)  # a repeat inside the window is the same Ctrl+C, not a second press
    os.kill(os.getpid(), signal.SIGINT)
    time.sleep(30)

gate.POLICY["recovery_block"] = gate.Rule(gate.Gate.ASKED, "a kind this test made up")
runner._handlers = lambda: {"recovery_block": block}
with session_scope() as s:
    queue.enqueue(s, "recovery_block", {"second_signal": True})
runner.serve(concurrency=1, poll_s=0.01, stop=stop)
"""


def test_a_second_signal_exits_at_once_and_leaves_the_job_to_its_lease(docker_log):
    import subprocess
    import sys

    p = subprocess.run([sys.executable, "-c", SECOND_SIGNAL], capture_output=True, text=True, timeout=20)
    assert p.returncode == 128 + signal.SIGINT, p.stderr[-2000:]
    with session_scope() as s:
        job = s.scalar(select(Job).where(Job.kind == "recovery_block").order_by(Job.id.desc()))
        assert job.status == "running"
        s.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL WHERE kind = 'recovery_block'"))


def test_a_dead_workers_job_is_settled_while_every_slot_is_busy(docker_log, monkeypatch):
    from justshipit import settings
    real = settings.company_config()
    monkeypatch.setattr(settings, "company_config",
                        lambda: {**real, "operations": {**real["operations"], "scheduler_tick_s": 0.05}})
    seen: list[str] = []

    def busy(_payload):
        dead = _running_job("worker-dead", timedelta(hours=1))
        for _ in range(100):
            if _status(dead) != "running":
                break
            time.sleep(0.02)
        seen.append(_status(dead))

    monkeypatch.setattr(runner, "_handlers", lambda: {"recovery_busy": busy})
    _enqueue("recovery_busy")
    runner.serve(concurrency=1, poll_s=0.01, once=True)
    assert seen == ["failed"]


def test_each_tick_sweeps_the_parked_runs_and_a_failing_sweep_does_not_stop_the_worker(docker_log, monkeypatch, caplog):
    from justshipit.services import holds

    def sweep(s):
        swept.append(1)
        raise OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"))
    swept: list[int] = []
    monkeypatch.setattr(holds, "reconcile_parked", sweep)
    monkeypatch.setattr(runner, "_handlers", lambda: {"recovery_noop": lambda p: None})
    runner.serve(concurrency=1, poll_s=0.01, once=True)
    assert swept == [1]
    assert [r for r in caplog.records if "resuming parked runs failed" in r.getMessage()]


def test_each_tick_expires_the_due_decisions_before_it_sweeps_and_a_failing_expiry_does_not_stop_it(
        docker_log, monkeypatch, caplog):
    from justshipit.services import holds, requests

    def expire(s, project_id, now):
        order.append(("expire", project_id))
        raise OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"))
    order: list[tuple] = []
    monkeypatch.setattr(requests, "expire_due", expire)
    monkeypatch.setattr(holds, "reconcile_parked", lambda s: order.append(("sweep", None)) or [])
    monkeypatch.setattr(runner, "_handlers", lambda: {"recovery_noop": lambda p: None})
    runner.serve(concurrency=1, poll_s=0.01, once=True)
    assert order == [("expire", None), ("sweep", None)], "every project's cards, then the runs they freed"
    assert [r for r in caplog.records if "expiring unanswered decisions failed" in r.getMessage()]


def test_a_failing_reclaim_does_not_skip_the_scheduler_pass(docker_log, monkeypatch, caplog):
    from justshipit.operations import scheduler

    fired: list[int] = []
    monkeypatch.setattr(scheduler, "tick", lambda s: fired.append(1) or [])
    real_reclaim, broken = queue.reclaim, []

    def reclaim_once_broken(s, held, gone=()):
        if not broken:
            broken.append(1)
            raise OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"))
        return real_reclaim(s, held, gone)

    monkeypatch.setattr(queue, "reclaim", reclaim_once_broken)
    monkeypatch.setattr(runner, "_handlers", lambda: {"recovery_noop": lambda p: None})
    runner.serve(concurrency=1, poll_s=0.01, once=True)
    assert fired == [1]
    assert [r.getMessage() for r in caplog.records if r.levelname == "ERROR" and "reclaim" in r.getMessage()]
    assert not [r for r in caplog.records if "scheduler tick failed" in r.getMessage()]
