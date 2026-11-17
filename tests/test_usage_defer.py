"""A job that must wait goes back to the queue for the moment it may run, keeps its attempts, and runs again."""
from datetime import datetime, timedelta, timezone

import pytest
from justshipit.db.models import Job
from justshipit.db.session import session_scope
from justshipit.services import queue
from justshipit.services.usage import MustWait
from justshipit.workers import runner
from sqlalchemy import func, select, text, update


pytestmark = pytest.mark.usefixtures("made_up_kinds")


def settle_queue(*kinds: str) -> None:
    """`run_once` claims the oldest queued job of a kind, so a job of that kind another test file left queued would be
    run in place of the one under test. With kinds named, only those are cleared: other queued jobs are not the
    caller's. With none, every queued job is, which is what a file that claims jobs wants: a list of kinds missed each
    kind a test began to claim."""
    only = "AND kind = ANY(:kinds)" if kinds else ""
    with session_scope() as s:
        s.execute(text(f"UPDATE jobs SET status = 'done', locked_by = NULL, lease_token = NULL "
                       f"WHERE status = 'queued' {only}"), {"kinds": list(kinds)} if kinds else {})


@pytest.fixture(autouse=True)
def _isolated_jobs():
    settle_queue()
    yield
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL WHERE payload ? 'usage_defer_test'"))


def test_settling_the_queue_clears_only_the_kinds_named_and_every_kind_when_none_is():
    mine = enqueue("wait_probe")
    other = enqueue("somebody_elses_job")

    settle_queue("wait_probe")

    assert job(mine).status == "done" and job(other).status == "queued"
    again = enqueue("wait_probe")

    settle_queue()

    assert job(again).status == "done" and job(other).status == "done"


def tomorrow() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=9)


def enqueue(kind: str, max_attempts: int = 1, **payload) -> int:
    with session_scope() as s:
        return queue.enqueue(s, kind, {"usage_defer_test": True, **payload}, max_attempts=max_attempts,
                             by_owner=True).id


def run_once(kind: str, handler) -> Job:
    """One attempt the way the worker runs it: claimed, executed, finished."""
    with session_scope() as s:
        claimed = queue.claim(s, "worker-test", [kind])
        assert claimed is not None
        jid, payload, token = claimed.id, dict(claimed.payload), claimed.lease_token
    runner._execute(jid, kind, payload, {kind: handler}, runner.Runs(), queue.Lease(job_id=jid, token=token))
    return job(jid)


def job(jid: int) -> Job:
    with session_scope() as s:
        j = s.get(Job, jid)
        s.expunge(j)
        return j


def test_a_waiting_job_returns_to_the_queue_for_its_time_without_spending_an_attempt():
    enqueue("wait_probe", max_attempts=1)
    until = tomorrow()

    def hit_the_cap(payload):
        raise MustWait(until, "usage_cap_day", "하루 사용 상한에 닿았습니다. 내일 00:00 (KST)에 이어서 합니다.")

    j = run_once("wait_probe", hit_the_cap)

    assert (j.status, j.attempts, j.locked_by, j.lease_token) == ("queued", 0, None, None)
    assert j.run_after == until and j.wait_reason == "usage_cap_day"
    assert "하루 사용 상한" in j.last_error and "00:00" in j.last_error
    with session_scope() as s:
        assert queue.claim(s, "worker-test", ["wait_probe"]) is None, "it must not run before its time"


def test_a_waiting_job_runs_again_when_its_time_comes_and_the_wait_clears():
    jid = enqueue("wait_probe", max_attempts=1)
    run_once("wait_probe", lambda p: (_ for _ in ()).throw(MustWait(tomorrow(), "usage_cap_week", "주간")))
    with session_scope() as s:
        s.get(Job, jid).run_after = datetime.now(timezone.utc) - timedelta(seconds=1)

    j = run_once("wait_probe", lambda p: None)

    assert (j.status, j.attempts, j.wait_reason, j.last_error) == ("done", 1, None, None)


def test_a_failure_still_spends_its_attempt():
    jid = enqueue("wait_probe", max_attempts=2)

    def broken(payload):
        raise RuntimeError("boom")

    j = run_once("wait_probe", broken)
    assert (j.status, j.attempts, j.wait_reason) == ("queued", 1, None)
    assert j.id == jid


@pytest.mark.parametrize("kind", queue.RUN_KINDS)
def test_a_run_resumes_from_its_checkpoint_after_it_waits(kind):
    jid = enqueue(kind, project_id=987001)

    j = run_once(kind, lambda p: (_ for _ in ()).throw(MustWait(tomorrow(), "subscription_limit", "구독")))

    assert j.id == jid and j.kind == "retry_project"
    assert j.status == "queued" and j.lock_key == "project:987001"


def test_a_worker_whose_lease_was_reclaimed_does_not_defer_the_new_holders_job():
    jid = enqueue("wait_probe")
    with session_scope() as s:
        claimed = queue.claim(s, "worker-test", ["wait_probe"])
        assert claimed.id == jid
        s.execute(text("UPDATE jobs SET lease_token = 'other-holder', locked_by = 'worker-b' WHERE id = :i"), {"i": jid})
    with session_scope() as s:
        queue.defer(s, jid, "stale-token", MustWait(tomorrow(), "usage_cap_day", "x"))
    j = job(jid)
    assert (j.status, j.locked_by, j.attempts) == ("running", "worker-b", 1)


def test_raising_a_cap_releases_the_jobs_waiting_on_it():
    waiting = enqueue("wait_probe")
    run_once("wait_probe", lambda p: (_ for _ in ()).throw(MustWait(tomorrow(), "usage_cap_day", "d")))
    quota = enqueue("wait_probe")
    run_once("wait_probe", lambda p: (_ for _ in ()).throw(MustWait(tomorrow(), "subscription_limit", "q")))

    with session_scope() as s:
        queue.release_usage_waits(s)

    assert job(waiting).run_after <= datetime.now(timezone.utc)
    assert job(quota).run_after > datetime.now(timezone.utc), "the subscription's own limit is not the owner's to lift"


def test_a_deferral_to_a_moment_already_past_still_waits_the_minimum_on_the_database_clock():
    with session_scope() as s:
        job = queue.enqueue(s, "wait_probe", {"usage_defer_test": True}, max_attempts=1)
        jid = job.id
    with session_scope() as s:
        claimed = queue.claim(s, "worker-test", ["wait_probe"])
        token = claimed.lease_token
    long_ago = datetime.now(timezone.utc) - timedelta(hours=2)

    with session_scope() as s:
        queue.defer(s, jid, token, MustWait(long_ago, "subscription_limit", "한도"))

    with session_scope() as s:
        gap = s.scalar(select(Job.run_after - func.now()).where(Job.id == jid))
        assert gap > timedelta(seconds=queue.MIN_DEFER_S - 5), "it would be claimed again at once"
        assert queue.claim(s, "worker-test", ["wait_probe"]) is None
        s.execute(update(Job).where(Job.id == jid).values(status="done"))


def test_a_deferral_far_ahead_keeps_its_time():
    until = datetime.now(timezone.utc) + timedelta(hours=9)
    with session_scope() as s:
        jid = queue.enqueue(s, "wait_probe", {"usage_defer_test": True}, max_attempts=1).id
    with session_scope() as s:
        token = queue.claim(s, "worker-test", ["wait_probe"]).lease_token
    with session_scope() as s:
        queue.defer(s, jid, token, MustWait(until, "usage_cap_day", "상한"))
    with session_scope() as s:
        assert abs((s.get(Job, jid).run_after - until).total_seconds()) < 1
        s.execute(update(Job).where(Job.id == jid).values(status="done"))
