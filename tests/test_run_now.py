"""The owner's "Run now" on a routine: a second click while a run is in flight returns that run, and the answer says
whether a running stage would have run it on its own."""
from datetime import UTC, datetime, timedelta

import cycle_kit
import pytest
from fastapi.testclient import TestClient
from justshipit.api.app import app
from justshipit.db.models import Business, Job, Schedule
from justshipit.db.session import session_scope
from justshipit.services import roadmap
from sqlalchemy import delete, select, update


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


@pytest.fixture
def routine(s):
    b = roadmap.create_business(s, name="Run-now business", one_liner="x")
    payload = {"business_id": b.id, "case": "run-now"}
    sc = Schedule(name=f"run-now {b.id}", kind="find_people", payload=payload, interval_hours=24, enabled=True,
                  next_run_at=datetime.now(UTC) + timedelta(days=1))
    s.add(sc)
    s.commit()
    yield b, sc.id, payload
    with session_scope() as s2:
        s2.execute(update(Job).where(Job.payload == payload).values(status="done"))
        s2.execute(delete(Schedule).where(Schedule.id == sc.id))


def in_flight(payload) -> list[int]:
    with session_scope() as s:
        return list(s.scalars(select(Job.id).where(Job.payload == payload, Job.status.in_(("queued", "gated", "running")))))


def test_a_second_click_returns_the_run_already_in_flight(api, routine):
    _, sid, payload = routine
    first = api.post(f"/api/schedules/{sid}/run-now").json()
    again = api.post(f"/api/schedules/{sid}/run-now").json()

    assert first["ok"] and not first["existing"]
    assert again["existing"] and again["job_id"] == first["job_id"]
    assert in_flight(payload) == [first["job_id"]]


def test_once_the_run_is_over_the_next_click_queues_a_new_one(api, routine):
    _, sid, payload = routine
    first = api.post(f"/api/schedules/{sid}/run-now").json()
    with session_scope() as s:
        s.get(Job, first["job_id"]).status = "done"

    later = api.post(f"/api/schedules/{sid}/run-now").json()
    assert not later["existing"] and later["job_id"] != first["job_id"]
    assert in_flight(payload) == [later["job_id"]]


def test_a_run_the_schedule_queued_counts_as_the_run_in_flight(api, routine, s):
    _, sid, payload = routine
    from justshipit.services import queue
    queued = queue.enqueue(s, "find_people", dict(payload), idempotency_key=f"sched:{sid}:test", max_attempts=1,
                           by_owner=True)
    s.commit()

    again = api.post(f"/api/schedules/{sid}/run-now").json()
    assert again["existing"] and again["job_id"] == queued.id


def test_the_list_says_whether_a_running_stage_runs_each_routine(api, routine):
    b, sid, _ = routine
    row = lambda: next(x for x in api.get(f"/api/schedules?business_id={b.id}").json() if x["id"] == sid)  # noqa: E731
    assert row()["admit"] == "skip"   # no stage runs, so the schedule's next run is dropped, not caught up

    with session_scope() as s2:
        cycle_kit.stage(s2, s2.get(Business, b.id), kinds=("find_people",))
    assert row()["admit"] == "run"


def test_the_answer_says_whether_the_run_is_outside_the_running_stage(api, routine):
    b, sid, payload = routine
    assert api.post(f"/api/schedules/{sid}/run-now").json()["outside_stage"] is True   # no stage runs

    with session_scope() as s2:
        s2.execute(update(Job).where(Job.payload == payload).values(status="done"))
        cycle_kit.stage(s2, s2.get(Business, b.id), kinds=("find_people",))
    assert api.post(f"/api/schedules/{sid}/run-now").json()["outside_stage"] is False


def test_a_running_stage_that_does_not_list_the_routine_leaves_it_outside(api, routine):
    b, sid, _ = routine
    with session_scope() as s2:
        cycle_kit.stage(s2, s2.get(Business, b.id), kinds=("promotion_week",))
    assert api.post(f"/api/schedules/{sid}/run-now").json()["outside_stage"] is True
