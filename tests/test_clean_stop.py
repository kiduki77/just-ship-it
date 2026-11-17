"""A worker asked to stop ends its jobs' calls, and hands each job back to the queue at once: the next worker resumes
it from its checkpoint without waiting out a lease, and never while the old copy still runs."""
import threading
import time

import pytest
from justshipit.db.models import Job
from justshipit.db.session import session_scope
from justshipit.runtime.base import run_process
from justshipit.services import queue
from justshipit.workers import runner
from sqlalchemy import text

PROJECT = 930101


@pytest.fixture(autouse=True)
def _isolated():
    yield
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL WHERE lock_key = :k"),
                  {"k": f"project:{PROJECT}"})


def _row(job_id: int):
    with session_scope() as s:
        return s.execute(text("SELECT status, kind, attempts, locked_by, held_until, run_after <= now() "
                              "FROM jobs WHERE id = :id"), {"id": job_id}).one()


def test_a_stopped_worker_hands_its_run_back_and_the_next_one_resumes_it_at_once(docker_log, monkeypatch):
    events: list[tuple[str, float]] = []
    calling = threading.Event()

    def first(_payload):
        try:
            calling.set()
            run_process(["sleep", "60"], "", None, 120)  # a model call that would outlast the test
            events.append(("call returned", time.monotonic()))
        finally:
            events.append(("first unwound", time.monotonic()))

    def resumed(_payload):
        events.append(("resumed", time.monotonic()))

    monkeypatch.setattr(runner, "_handlers", lambda: {"run_project": first, "retry_project": resumed})
    with session_scope() as s:
        jid = queue.enqueue(s, "run_project", {"project_id": PROJECT}).id

    stop = threading.Event()
    worker = threading.Thread(target=runner.serve, kwargs={"concurrency": 1, "poll_s": 0.05, "stop": stop})
    worker.start()
    assert calling.wait(10)
    time.sleep(0.3)
    asked = time.monotonic()
    stop.set()
    worker.join(15)
    assert not worker.is_alive() and time.monotonic() - asked < 10

    status, kind, attempts, locked_by, held_until, runnable = _row(jid)
    assert (status, kind, attempts, locked_by, held_until, runnable) == ("queued", "retry_project", 0, None, None, True)
    assert [e for e, _ in events] == ["first unwound"]

    runner.serve(concurrency=1, poll_s=0.05, once=True)
    assert [e for e, _ in events] == ["first unwound", "resumed"]
    assert events[1][1] > events[0][1]
    assert _row(jid)[0] == "done"


def test_a_job_past_its_last_check_still_finishes_when_the_worker_stops(docker_log, monkeypatch):
    stop = threading.Event()

    def last_step(_payload):
        stop.set()
        time.sleep(0.2)

    monkeypatch.setattr(runner, "_handlers", lambda: {"run_project": last_step})
    with session_scope() as s:
        jid = queue.enqueue(s, "run_project", {"project_id": PROJECT}).id
    runner.serve(concurrency=1, poll_s=0.05, stop=stop)
    assert _row(jid)[0] == "done"


def test_a_lease_check_after_the_stop_unwinds_without_recording_a_failure(docker_log, monkeypatch):
    stop = threading.Event()

    def graph(_payload):
        stop.set()
        queue.check_lease()  # the next graph node
        raise AssertionError("unreachable")

    monkeypatch.setattr(runner, "_handlers", lambda: {"run_project": graph})
    with session_scope() as s:
        jid = queue.enqueue(s, "run_project", {"project_id": PROJECT}).id
    runner.serve(concurrency=1, poll_s=0.05, stop=stop)
    with session_scope() as s:
        job = s.get(Job, jid)
        assert (job.status, job.kind, job.attempts) == ("queued", "retry_project", 0)
