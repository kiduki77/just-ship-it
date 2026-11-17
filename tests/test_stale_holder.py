"""A lease that looks stale is not proof that its holder stopped: after a host suspend or a database outage
longer than job_lease_s the handler is alive and still working. A running row is therefore in flight whatever
its lease age, a reclaimed job waits out its old holder, and the old holder stops at its next lease check."""
import os
import stat
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from justshipit.db.models import BudgetMonth, Job
from justshipit.db.session import session_scope
from justshipit.services import budget, queue
from justshipit.settings import PROCESS_ID, company_config
from justshipit.workers import runner
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("made_up_kinds")


KINDS = ["stale_run"]
STALE = timedelta(minutes=11)  # longer than job_lease_s: a host suspend or a database outage


@pytest.fixture(autouse=True)
def _isolated_jobs():
    """A claim takes the oldest runnable row of the kinds it names, so a queued job of any kind that an earlier file
    left must not be one. Settling by a list of kinds missed each kind a test began to claim."""
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL, lease_token = NULL WHERE status = 'queued'"))
    yield
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL WHERE kind LIKE 'stale_%' "
                       "OR (payload->>'project_id')::int BETWEEN 930000 AND 939999 "
                       "OR (payload->>'dev_job_id')::int BETWEEN 930000 AND 939999"))


def _enqueue(kind: str, payload: dict, max_attempts: int = 1) -> int:
    with session_scope() as s:
        return queue.enqueue(s, kind, payload, max_attempts=max_attempts).id


def _claim(worker: str, kinds=KINDS):
    with session_scope() as s:
        j = queue.claim(s, worker, kinds)
        return (j.id, j.locked_by) if j else None


def _age_lease(job_id: int, by: timedelta = STALE) -> None:
    with session_scope() as s:
        s.get(Job, job_id).locked_at = datetime.now(UTC) - by


def _row(job_id: int):
    with session_scope() as s:
        return s.execute(text("SELECT status, kind, locked_by, attempts, last_error, EXTRACT(EPOCH FROM run_after - now()) "
                              "FROM jobs WHERE id = :id"), {"id": job_id}).one()


def _job(job_id: int) -> Job:
    with session_scope() as s:
        j = s.get(Job, job_id)
        s.expunge(j)
        return j


def _serve(concurrency: int = 2):
    stop = threading.Event()
    t = threading.Thread(target=runner.serve, kwargs={"concurrency": concurrency, "poll_s": 0.05, "stop": stop})
    t.start()
    return t, stop


def _wait(predicate, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def test_two_workers_never_run_one_project_run_at_once(docker_log, monkeypatch):
    monkeypatch.setitem(company_config()["operations"], "job_heartbeat_s", 1000.0)  # the first holder's beats stall
    monkeypatch.setattr(queue, "rerun_delay_s", lambda: 3.0, raising=False)
    lock, inflight, peak, spans = threading.Lock(), [], [0], []

    def copy(kind):
        def handler(payload):
            if payload.get("project_id") != 930001:  # another test's leftover
                return
            with lock:
                inflight.append(kind)
                peak[0] = max(peak[0], len(inflight))
            began = time.monotonic()
            time.sleep(2.5)
            with lock:
                inflight.remove(kind)
                spans.append((kind, began, time.monotonic()))
        return handler

    monkeypatch.setattr(runner, "_handlers", lambda: {k: copy(k) for k in queue.RUN_KINDS})
    jid = _enqueue("run_project", {"project_id": 930001})
    a, stop_a = _serve()
    b = stop_b = None
    try:
        assert _wait(lambda: inflight)
        _age_lease(jid)
        b, stop_b = _serve()
        assert _wait(lambda: len(spans) == 2), spans
    finally:
        stop_a.set()
        a.join(10)
        if b:
            stop_b.set()
            b.join(10)
    assert peak[0] == 1
    assert [kind for kind, _, _ in spans] == ["run_project", "retry_project"]
    (_, _, first_ended), (_, second_began, _) = spans
    assert second_began >= first_ended
    j = _job(jid)
    assert (j.kind, j.status, j.attempts) == ("retry_project", "done", 2)


def test_a_reclaimed_job_is_not_handed_out_before_its_old_holder_can_have_stopped():
    jid = _enqueue("stale_run", {"project_id": 930003}, max_attempts=3)
    assert _claim("worker-a") == (jid, "worker-a")
    _age_lease(jid)
    assert _claim("worker-b") is None
    with session_scope() as s:
        status, waits_s = s.execute(text("SELECT status, EXTRACT(EPOCH FROM run_after - now()) FROM jobs WHERE id = :id"),
                                    {"id": jid}).one()
    assert status == "queued"
    assert waits_s >= queue.rerun_delay_s() - 5
    assert queue.rerun_delay_s() >= float(company_config()["limits"]["llm_call_timeout_s"])


def test_a_running_job_keeps_its_container_and_reservations_whatever_its_lease_age(tmp_path, monkeypatch):
    bin_dir, log = tmp_path / "bin", tmp_path / "docker.log"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(f'#!/bin/sh\necho "$@" >> {log}\n[ "$1" = ps ] && printf "ship-eng-mine\\t{PROCESS_ID}\\n"\nexit 0\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    log.write_text("")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setitem(company_config()["operations"], "scheduler_tick_s", 0.3)
    monkeypatch.setattr(runner, "_beat", lambda *a: None)  # host suspend: no beat renews the lease
    started, release, reserved = threading.Event(), threading.Event(), []

    def handler(payload):
        if payload.get("dev_job_id") != 930002:  # another test's leftover
            return
        reserved.append(budget.reserve(None, None, "dev"))  # what a model call inside the job holds
        started.set()
        release.wait(10)

    monkeypatch.setattr(runner, "_handlers", lambda: {"dev_job": handler})
    jid = _enqueue("dev_job", {"dev_job_id": 930002})
    t, stop = _serve()
    try:
        assert started.wait(10) and _wait(lambda: _job(jid).status == "running")
        log.write_text("")
        _age_lease(jid)
        time.sleep(1.2)  # several ticks
        with session_scope() as s:
            j = s.get(Job, jid)
            assert (j.status, j.lease_token is not None) == ("running", True)
            assert "dev_job:930002" in queue.busy_lock_keys(s)
            month_reserved = s.get(BudgetMonth, budget._month()).reserved_usd
        removed = [line for line in log.read_text().splitlines() if line.startswith("rm -f")]
        assert removed == []
        assert month_reserved >= reserved[0].amount
    finally:
        release.set()
        stop.set()
        t.join(10)
        for held in reserved:
            budget.settle(None, None, held, 0.0)
    assert _job(jid).status == "done"


def test_a_stale_holder_stops_at_its_next_lease_check_and_the_rerun_starts_after_it(docker_log, monkeypatch):
    monkeypatch.setitem(company_config()["operations"], "job_heartbeat_s", 0.1)
    monkeypatch.setattr(queue, "rerun_delay_s", lambda: 1.0)
    lock, inflight, peak, spans = threading.Lock(), [], [0], []

    def copy(kind):
        def handler(payload):
            if payload.get("project_id") != 930004:
                return
            with lock:
                inflight.append(kind)
                peak[0] = max(peak[0], len(inflight))
            began, how = time.monotonic(), "ran to the end"
            try:
                for _ in range(40):  # two seconds of work with a lease check before each step
                    queue.check_lease()
                    time.sleep(0.05)
            except queue.LeaseLost:
                how = "stopped"
                raise
            finally:
                with lock:
                    inflight.remove(kind)
                    spans.append((kind, how, began, time.monotonic()))
        return handler

    monkeypatch.setattr(runner, "_handlers", lambda: {k: copy(k) for k in queue.RUN_KINDS})
    jid = _enqueue("run_project", {"project_id": 930004})
    a, stop_a = _serve()
    b = stop_b = None
    try:
        assert _wait(lambda: inflight)
        with session_scope() as s:  # a host suspend on A, then B's tick settles the lease
            s.get(Job, jid).locked_at = datetime.now(UTC) - STALE
            s.flush()
            queue.reclaim(s, {})
        reclaimed = time.monotonic()
        b, stop_b = _serve()
        assert _wait(lambda: len(spans) == 2), (spans, _row(jid))
    finally:
        stop_a.set()
        a.join(10)
        if b:
            stop_b.set()
            b.join(10)
    assert peak[0] == 1
    (first, how_first, _, first_ended), (second, _, second_began, _) = spans
    assert (first, how_first, second) == ("run_project", "stopped", "retry_project")
    assert first_ended - reclaimed < 1.0
    assert second_began >= first_ended
    j = _job(jid)
    assert (j.status, j.attempts) == ("done", 2)
