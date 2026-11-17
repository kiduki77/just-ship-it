import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from justshipit.db.models import Job, Project
from justshipit.db.session import session_scope
from justshipit.services import queue
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("made_up_kinds")


KINDS = ["lease_run", "lease_dev"]


@pytest.fixture(autouse=True)
def _reclaimed_jobs_rerun_at_once(monkeypatch):
    """These tests are about claim and reclaim mechanics; the wait for an old holder is test_stale_holder's."""
    monkeypatch.setattr(queue, "rerun_delay_s", lambda: 0.0)


@pytest.fixture(autouse=True)
def _isolated_jobs():
    yield
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL WHERE kind LIKE 'lease_%' "
                       "OR (payload->>'project_id')::int BETWEEN 910000 AND 919999"))


def _enqueue(kind: str, payload: dict, max_attempts: int = 3) -> int:
    with session_scope() as s:
        return queue.enqueue(s, kind, payload, max_attempts=max_attempts).id


def _claim(worker: str):
    with session_scope() as s:
        j = queue.claim(s, worker, KINDS)
        return (j.id, j.locked_by) if j else None


def _age_lease(job_id: int, by: timedelta) -> None:
    with session_scope() as s:
        s.get(Job, job_id).locked_at = datetime.now(timezone.utc) - by


def _token(job_id: int) -> str | None:
    return _job(job_id).lease_token


def _job(job_id: int) -> Job:
    with session_scope() as s:
        j = s.get(Job, job_id)
        s.expunge(j)
        return j


def test_live_job_is_not_reclaimed_after_hours():
    jid = _enqueue("lease_run", {"project_id": 910001})
    assert _claim("worker-a") == (jid, "worker-a")
    _age_lease(jid, timedelta(hours=3))
    with session_scope() as s:
        assert queue.heartbeat(s, {jid: _token(jid)}) == {jid}
    assert _claim("worker-b") is None
    j = _job(jid)
    assert (j.status, j.locked_by, j.attempts) == ("running", "worker-a", 1)


def test_stale_lease_is_reclaimed():
    jid = _enqueue("lease_run", {"project_id": 910002})
    _claim("worker-a")
    _age_lease(jid, timedelta(minutes=30))
    assert _claim("worker-b") == (jid, "worker-b")
    assert _job(jid).attempts == 2


def test_reclaim_respects_max_attempts():
    jid = _enqueue("lease_run", {"project_id": 910003}, max_attempts=1)
    _claim("worker-a")
    _age_lease(jid, timedelta(hours=3))
    assert _claim("worker-b") is None
    j = _job(jid)
    assert j.status == "failed" and j.locked_by is None and j.finished_at is not None
    assert "worker-a" in j.last_error


def test_finish_from_a_worker_that_lost_its_lease_is_ignored():
    jid = _enqueue("lease_run", {"project_id": 910004})
    _claim("worker-a")
    first_lease = _token(jid)
    _age_lease(jid, timedelta(hours=3))
    _claim("worker-b")
    with session_scope() as s:
        queue.finish(s, jid, first_lease)
    j = _job(jid)
    assert (j.status, j.locked_by) == ("running", "worker-b")


def test_concurrent_claims_never_run_one_project_twice():
    first = _enqueue("lease_run", {"project_id": 910005})
    _enqueue("lease_run", {"project_id": 910005})
    other = _enqueue("lease_run", {"project_id": 910006})
    got_b: list = []
    with session_scope() as a:
        ja = queue.claim(a, "worker-a", KINDS)
        assert ja.id == first
        t = threading.Thread(target=lambda: got_b.append(_claim("worker-b")))
        t.start()
        t.join(0.5)
    t.join(10)
    assert got_b == [(other, "worker-b")]


def test_dev_job_kinds_are_serialized_per_dev_job():
    first = _enqueue("lease_dev", {"dev_job_id": 920001})
    second = _enqueue("lease_dev", {"dev_job_id": 920001})
    assert _claim("worker-a") == (first, "worker-a")
    assert _claim("worker-b") is None
    with session_scope() as s:
        queue.finish(s, first, _token(first))
    assert _claim("worker-b") == (second, "worker-b")


def test_two_realigns_of_one_project_run_one_at_a_time():
    """반품 예측: two realigns 34 s apart each planned the same task (T-97, T-99), neither seeing the other's."""
    with session_scope() as s:
        first = queue.enqueue(s, "realign_project", {"project_id": 910008, "request_id": 1}).id
        second = queue.enqueue(s, "realign_project", {"project_id": 910008, "request_id": 2}).id
        other = queue.enqueue(s, "realign_project", {"project_id": 910009, "request_id": 3}).id

    def claim(worker: str):
        while True:   # realigns other tests left queued are not this test's
            with session_scope() as s:
                j = queue.claim(s, worker, ["realign_project"])
                if j is None or j.id in (first, second, other):
                    return j and (j.id, j.lease_token)
                queue.finish(s, j.id, j.lease_token)

    held, token = claim("worker-a")
    assert held == first
    assert claim("worker-b")[0] == other
    assert claim("worker-b") is None
    with session_scope() as s:
        queue.finish(s, first, token)
    assert claim("worker-b")[0] == second


def test_realign_runs_beside_the_project_run():
    _enqueue("lease_run", {"project_id": 910007})
    with session_scope() as s:
        realign = queue.enqueue(s, "realign_project", {"project_id": 910007}).id
    _claim("worker-a")
    claimed = []
    while not claimed or claimed[-1] not in (None, realign):
        with session_scope() as s:
            j = queue.claim(s, "worker-b", ["realign_project"])
            claimed.append(j and j.id)
            if j:
                queue.finish(s, j.id, j.lease_token)
    assert claimed[-1] == realign


def test_worker_heartbeat_keeps_a_long_job_alive(docker_log, monkeypatch):
    from justshipit.settings import company_config
    from justshipit.workers import runner

    monkeypatch.setitem(company_config()["operations"], "job_heartbeat_s", 0.05)
    started, release, stop = threading.Event(), threading.Event(), threading.Event()

    def long_job(_payload):
        started.set()
        release.wait(10)

    monkeypatch.setattr(runner, "_handlers", lambda: {"lease_run": long_job})
    jid = _enqueue("lease_run", {"project_id": 910008})
    t = threading.Thread(target=runner.serve, kwargs={"concurrency": 1, "poll_s": 0.05, "stop": stop})
    t.start()
    try:
        assert started.wait(10)
        _age_lease(jid, timedelta(hours=3))
        deadline = datetime.now(timezone.utc) + timedelta(seconds=5)
        while _job(jid).locked_at < datetime.now(timezone.utc) - timedelta(minutes=1):
            assert datetime.now(timezone.utc) < deadline, "heartbeat never refreshed the lease"
            time.sleep(0.02)
        assert _claim("worker-b") is None
    finally:
        release.set()
        stop.set()
        t.join(10)
    assert _job(jid).status == "done"


def _serve(monkeypatch, handler, concurrency: int = 4, heartbeat_s: float = 1000.0):
    """A worker whose heartbeat is slow enough that the claim loop, not the beat, decides the lease's fate."""
    from justshipit.settings import company_config
    from justshipit.workers import runner

    monkeypatch.setitem(company_config()["operations"], "job_heartbeat_s", heartbeat_s)
    monkeypatch.setattr(runner, "_handlers", lambda: {"lease_run": handler})
    stop = threading.Event()
    t = threading.Thread(target=runner.serve, kwargs={"concurrency": concurrency, "poll_s": 0.05, "stop": stop})
    t.start()
    return t, stop


def test_a_worker_whose_lease_looks_stale_never_runs_the_job_twice_at_once(docker_log, monkeypatch):
    lock, inflight, peak = threading.Lock(), [], [0]
    started, release = threading.Event(), threading.Event()

    def handler(_payload):
        with lock:
            inflight.append(1)
            peak[0] = max(peak[0], len(inflight))
        started.set()
        release.wait(4)
        with lock:
            inflight.pop()

    jid = _enqueue("lease_run", {"project_id": 910009})
    t, stop = _serve(monkeypatch, handler)
    try:
        assert started.wait(10)
        _age_lease(jid, timedelta(minutes=11))  # host suspend or DB outage longer than job_lease_s
        time.sleep(1.0)
        release.set()
        time.sleep(0.5)
    finally:
        release.set()
        stop.set()
        t.join(10)
    assert peak[0] == 1
    j = _job(jid)
    assert (j.status, j.attempts) == ("done", 1)


def test_a_job_whose_finish_is_slow_to_commit_is_never_reclaimed_and_run_again(docker_log, monkeypatch):
    """The handler returned, but the row still says running with a stale lease while its finish waits on the
    database (a slow commit, a host that slept): the claim loop must still count the job as its own."""
    runs = []
    real_finish = queue.finish

    def slow_finish(s, job_id, token, error=None):
        if len(runs) == 1:
            _age_lease(job_id, timedelta(minutes=11))
            time.sleep(0.3)
        return real_finish(s, job_id, token, error)

    monkeypatch.setattr(queue, "finish", slow_finish)
    jid = _enqueue("lease_run", {"project_id": 910012})
    t, stop = _serve(monkeypatch, lambda _payload: runs.append(1))
    try:
        deadline = time.monotonic() + 10
        while _job(jid).status != "done":
            assert time.monotonic() < deadline, "the job never finished"
            time.sleep(0.05)
        time.sleep(0.5)
    finally:
        stop.set()
        t.join(10)
    j = _job(jid)
    assert (len(runs), j.status, j.attempts) == (1, "done", 1)


def test_a_beat_during_a_finish_that_already_committed_reports_no_lost_lease(docker_log, monkeypatch, caplog):
    real_finish = queue.finish

    def finish_then_stall(s, job_id, token, error=None):
        real_finish(s, job_id, token, error)
        s.commit()
        time.sleep(0.3)

    monkeypatch.setattr(queue, "finish", finish_then_stall)
    jid = _enqueue("lease_run", {"project_id": 910013})
    t, stop = _serve(monkeypatch, lambda _payload: None, heartbeat_s=0.05)
    try:
        deadline = time.monotonic() + 10
        while _job(jid).status != "done":
            assert time.monotonic() < deadline, "the job never finished"
            time.sleep(0.05)
        time.sleep(0.5)
    finally:
        stop.set()
        t.join(10)
    lost = [m for m in (r.getMessage() for r in caplog.records) if f"job {jid}" in m and "LEASE LOST" in m]
    assert (lost, _job(jid).status, _job(jid).attempts) == ([], "done", 1)


def test_claim_never_hands_out_a_job_this_process_still_runs():
    jid = _enqueue("lease_run", {"project_id": 910010})
    _claim("worker-a")
    _age_lease(jid, timedelta(minutes=11))
    with session_scope() as s:
        assert queue.claim(s, "worker-a", KINDS, held={jid: None}) is None
    assert _job(jid).status == "queued"
    assert _claim("worker-a") == (jid, "worker-a")


def test_a_lease_taken_by_another_worker_is_detected_and_the_old_result_discarded(docker_log, monkeypatch, caplog):
    started, release = threading.Event(), threading.Event()

    def handler(_payload):
        started.set()
        release.wait(10)

    jid = _enqueue("lease_run", {"project_id": 910011})
    t, stop = _serve(monkeypatch, handler, concurrency=1, heartbeat_s=0.05)
    try:
        assert started.wait(10)
        with session_scope() as s:  # what a reclaim and a claim by worker-b leave on the row
            s.execute(text("UPDATE jobs SET locked_by = 'worker-b', lease_token = 'other', locked_at = now(), "
                           "attempts = 2 WHERE id = :id"), {"id": jid})
        deadline = time.monotonic() + 5
        while not any(r.levelname == "ERROR" and f"job {jid}" in r.getMessage() and "LEASE LOST" in r.getMessage()
                      for r in caplog.records):
            assert time.monotonic() < deadline, "the worker never noticed its lease was gone"
            time.sleep(0.02)
        release.set()
        time.sleep(0.3)
    finally:
        release.set()
        stop.set()
        t.join(10)
    j = _job(jid)
    assert (j.status, j.locked_by, j.attempts) == ("running", "worker-b", 2)


def test_a_reclaimed_project_run_continues_from_its_checkpoint_once_then_fails(s, project):
    s.execute(text("UPDATE projects SET status = 'RUNNING' WHERE id = :id"), {"id": project.id})
    run_kinds = list(queue.RUN_KINDS)
    s.execute(text("UPDATE jobs SET status = 'done' WHERE status = 'queued' AND kind = ANY(:kinds)"),
              {"kinds": run_kinds})  # leftovers of earlier tests would be claimed first
    s.commit()
    with session_scope() as s2:
        jid = queue.enqueue(s2, "run_project", {"project_id": project.id}, max_attempts=1).id

    def claim(worker):
        with session_scope() as s2:
            j = queue.claim(s2, worker, run_kinds)
            return (j.id, j.kind, j.attempts) if j else None

    assert claim("worker-a") == (jid, "run_project", 1)
    _age_lease(jid, timedelta(minutes=11))
    assert claim("worker-b") == (jid, "retry_project", 2)  # a worker death costs one checkpointed step, not the run
    with session_scope() as s2:
        assert s2.get(Project, project.id).status == "RUNNING"
    _age_lease(jid, timedelta(minutes=11))
    assert claim("worker-c") is None
    with session_scope() as s2:
        assert (s2.get(Job, jid).status, s2.get(Project, project.id).status) == ("failed", "FAILED")
