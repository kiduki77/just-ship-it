"""A reclaimed job's old holder may still be running (host suspend, database outage), so its lock key, not only
its row, stays held until the old holder cannot still be mid-call. No sibling job, owner retry or new dev
attempt on the same subject starts before then, and all of them start once the wait has passed."""
from datetime import UTC, datetime, timedelta

import pytest
from justshipit.contracts.handoff import Handoff
from justshipit.db.models import DevJob, Job, Project, Spec
from justshipit.db.session import session_scope
from justshipit.engineering import specs
from justshipit.i18n import msg
from justshipit.services import control, queue
from sqlalchemy import text

STALE = timedelta(minutes=11)  # longer than job_lease_s


@pytest.fixture(autouse=True)
def _isolated_jobs():
    """A claim takes the oldest runnable row of the kinds it names, so a queued job of any kind that an earlier file
    left (a reassess_project from an approved decision card, in a sharded run) must not be one. Settling by a list
    of kinds missed each kind a test began to claim."""
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'done' WHERE status = 'queued'"))
    yield
    with session_scope() as s:
        s.execute(text("DELETE FROM jobs WHERE payload ? 'hold_test'"))


def _enqueue(kind: str, payload: dict, max_attempts: int = 1) -> int:
    with session_scope() as s:
        return queue.enqueue(s, kind, {**payload, "hold_test": True}, max_attempts=max_attempts).id


def _claim(worker: str, kinds: list[str]):
    with session_scope() as s:
        j = queue.claim(s, worker, kinds)
        return (j.id, j.kind, j.attempts) if j else None


def _suspend(jid: int, *, spent: bool = False, holder: str | None = None) -> None:
    """The holder's host suspends: its lease lapses with the handler alive and mid-call."""
    with session_scope() as s:
        j = s.get(Job, jid)
        j.locked_at = datetime.now(UTC) - STALE
        if spent:
            j.attempts = queue.RUN_ATTEMPTS_AFTER_RECLAIM
        if holder:
            j.locked_by = holder


def _reclaim() -> None:
    with session_scope() as s:
        queue.reclaim(s, {})


def _wait_passes(jid: int) -> None:
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET run_after = now(), held_until = now() WHERE id = :id"), {"id": jid})


def _last_error(jid: int) -> str:
    with session_scope() as s:
        return s.get(Job, jid).last_error or ""


def _dev_job(s, project) -> tuple[DevJob, str]:
    h = Handoff(spec_id="SPEC-HOLD", project="Test", title="t", source={"path": "specs/t", "commit_sha": "abc"},
                target={"repository": "demo"}, approval={"spec_approved": True, "implementation_authorized": True})
    spec = Spec(spec_key=f"SPEC-HOLD-{project.id}", project_id=project.id, title="t", registry_path="specs/t",
                approved_commit_sha="abc", handoff=h.model_dump(mode="json"), status="IN_DEVELOPMENT")
    s.add(spec)
    s.flush()
    d = DevJob(spec_id=spec.id, status="IMPLEMENTING")
    s.add(d)
    s.commit()
    return d, spec.spec_key


def test_a_sibling_job_on_the_same_subject_waits_out_the_old_holder(s, project):
    pid = project.id
    jid = _enqueue("run_project", {"project_id": pid})
    assert _claim("worker-a", ["run_project"])[0] == jid
    _suspend(jid)
    _reclaim()
    sib = _enqueue("reassess_project", {"project_id": pid, "request_id": 1})
    assert _claim("worker-b", ["reassess_project", "retry_project"]) is None
    _wait_passes(jid)
    assert _claim("worker-b", ["reassess_project", "retry_project"]) == (jid, "retry_project", 2)
    assert _claim("worker-c", ["reassess_project"]) is None  # the run holds the key now
    with session_scope() as s2:
        queue.finish(s2, jid, s2.get(Job, jid).lease_token)
    assert _claim("worker-c", ["reassess_project"]) == (sib, "reassess_project", 1)


def test_a_spec_for_an_approved_development_is_written_while_the_project_runs(s, project):
    """write_spec waited 1h42m behind the project's graph run, so the 개발 page said "no spec yet" all that time."""
    pid = project.id
    run = _enqueue("run_project", {"project_id": pid})
    assert _claim("worker-a", ["run_project"])[0] == run
    spec = _enqueue("write_spec", {"project_id": pid, "request_id": 1})
    second = _enqueue("write_spec", {"project_id": pid, "request_id": 2})
    assert _claim("worker-b", ["write_spec"]) == (spec, "write_spec", 1)
    assert _claim("worker-c", ["write_spec"]) is None, "one draft per project at a time"
    with session_scope() as s2:
        queue.finish(s2, spec, s2.get(Job, spec).lease_token)
    assert _claim("worker-c", ["write_spec"]) == (second, "write_spec", 1)


def test_the_owner_retry_of_a_spent_run_waits_out_the_old_holder(s, project):
    pid = project.id
    project.status = "RUNNING"
    s.commit()
    jid = _enqueue("run_project", {"project_id": pid})
    assert _claim("worker-a", ["run_project"])[0] == jid
    _suspend(jid, spent=True)
    _reclaim()
    with session_scope() as s2:
        assert s2.get(Project, pid).status == "FAILED"
        with pytest.raises(control.ControlError) as refused:
            control.retry_project(s2, pid)
    with session_scope() as s2:
        until = s2.get(Job, jid).held_until
    assert until.astimezone().strftime("%H:%M") in str(refused.value)
    assert _claim("worker-c", list(queue.RUN_KINDS)) is None
    _wait_passes(jid)
    with session_scope() as s2:
        control.retry_project(s2, pid)
    got = _claim("worker-c", list(queue.RUN_KINDS))
    assert got is not None and got[1:] == ("retry_project", 1)
    with session_scope() as s2:
        queue.finish(s2, got[0], s2.get(Job, got[0]).lease_token)


def test_a_new_dev_attempt_waits_out_the_old_holder_of_the_spent_one(korean_owner, s, project):
    d, key = _dev_job(s, project)
    jid = _enqueue("dev_job", {"dev_job_id": d.id}, max_attempts=queue.BUILD_ATTEMPTS_AFTER_RECLAIM)
    assert _claim("worker-a", ["dev_job"])[0] == jid
    _suspend(jid)
    with session_scope() as s2:  # its worker died on the build's last try
        s2.get(Job, jid).attempts = queue.BUILD_ATTEMPTS_AFTER_RECLAIM
    _reclaim()
    with session_scope() as s2:
        assert (s2.get(Job, jid).status, s2.get(DevJob, d.id).status) == ("failed", "FAILED")
    with pytest.raises(ValueError, match="까지"):
        specs.retry_dev(key, "ko")
    assert _claim("worker-b", ["dev_job"]) is None
    _wait_passes(jid)
    new = specs.retry_dev(key, "ko")
    with session_scope() as s2:
        s2.execute(text("UPDATE jobs SET payload = payload || '{\"hold_test\": true}' WHERE payload->>'dev_job_id' = :d"),
                   {"d": str(new)})
    got = _claim("worker-b", ["dev_job"])
    assert got is not None and got[1] == "dev_job"


def test_a_holder_whose_process_is_gone_is_waited_for_too_since_its_children_may_still_run(korean_owner, 
        s, project, dead_worker):
    """A worker killed by SIGKILL or the OOM killer leaves `claude -p` and its test containers running, so a gone
    pid does not prove the work stopped: the job waits out the rerun delay and says for how long."""
    jid = _enqueue("run_project", {"project_id": project.id})
    assert _claim(dead_worker, ["run_project"])[0] == jid
    _suspend(jid)
    _reclaim()
    assert _claim("worker-new", ["retry_project"]) is None
    with session_scope() as s2:
        wait = s2.scalar(text("SELECT extract(epoch FROM held_until - now()) FROM jobs WHERE id = :id"), {"id": jid})
    assert abs(float(wait) - queue.rerun_delay_s()) < 30
    assert f"{int(queue.rerun_delay_s() // 60)}분 뒤에 멈춘 곳부터 다시 실행해요" in _last_error(jid)
    _wait_passes(jid)
    assert _claim("worker-new", ["retry_project"]) == (jid, "retry_project", 2)


def test_a_budget_raise_that_resumes_the_run_and_a_reclaim_of_its_job_never_deadlock(s, project, dead_worker):
    """set_budget(resume_run=True) locked the project row and then took the claim lock inside retry_project,
    while a reclaim that fails the same project's spent job takes them in the other order."""
    import threading

    pid = project.id
    project.status, project.error, project.budget_usd = "FAILED", "BudgetExceeded: project budget 10", 10.0
    s.commit()
    jid = _enqueue("run_project", {"project_id": pid})
    assert _claim(dead_worker, ["run_project"])[0] == jid
    _suspend(jid, spent=True)
    claim_lock_held, row_locked = threading.Event(), threading.Event()
    original_fail, original_retry = queue.fail_subject, control.retry_failed_tasks

    def fail_subject(s2, job, **kw):  # the reclaimer holds the claim lock here and now wants the project row
        claim_lock_held.set()
        row_locked.wait(3)
        return original_fail(s2, job, **kw)

    def retry_failed_tasks(s2, pid2, **kw):  # set_budget holds the project row here and now wants the claim lock
        row_locked.set()
        claim_lock_held.wait(3)
        return original_retry(s2, pid2, **kw)

    errors: list[BaseException] = []

    def run(fn):
        try:
            with session_scope() as s2:
                fn(s2)
        except BaseException as exc:  # noqa: BLE001 - the test reports it
            errors.append(exc)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(queue, "fail_subject", fail_subject)
        mp.setattr(control, "retry_failed_tasks", retry_failed_tasks)
        reclaimer = threading.Thread(target=run, args=(lambda s2: queue.reclaim(s2, {}),))
        reclaimer.start()
        assert claim_lock_held.wait(5)
        owner = threading.Thread(target=run, args=(lambda s2: control.set_budget(s2, pid, 20.0, resume_run=True),))
        owner.start()
        reclaimer.join(15)
        owner.join(15)
    assert errors == []
    with session_scope() as s2:
        assert (s2.get(Project, pid).budget_usd, s2.get(Job, jid).status) == (20.0, "failed")
        retried = s2.execute(text("UPDATE jobs SET status = 'done' WHERE kind = 'retry_project' AND status = 'queued' "
                                  "AND (payload->>'project_id')::int = :p"), {"p": pid}).rowcount
    assert retried == 1


def test_a_handler_waking_with_its_project_row_locked_and_a_reclaim_of_its_job_never_hang(s, project):
    """A handler whose host slept while it held its project's row commits on waking, and a lease flag older than a
    lease is then re-read from the job row. Taking the claim lock for that read, on a connection of its own, waited
    for a reclaim that waited for the project row: PostgreSQL never sees that cycle, so both waited forever."""
    import threading
    import time

    pid = project.id
    project.status = "RUNNING"
    s.commit()
    jid = _enqueue("run_project", {"project_id": pid})
    with session_scope() as s2:
        token = queue.claim(s2, "worker-asleep", ["run_project"]).lease_token
    _suspend(jid, spent=True)
    row_locked, reclaim_waits = threading.Event(), threading.Event()
    errors: list[BaseException] = []

    def handler():
        lease = queue.Lease(jid, token)
        lease.confirmed_at = time.time() - 2 * queue.lease_s()  # the beat thread slept with the host
        queue.current_lease.set(lease)
        try:
            with session_scope() as s2:
                s2.get(Project, pid, with_for_update=True)
                row_locked.set()
                reclaim_waits.wait(5)
        except BaseException as exc:  # noqa: BLE001 - the test reports it
            errors.append(exc)

    def reclaimer():
        row_locked.wait(5)
        try:
            _reclaim()
        except BaseException as exc:  # noqa: BLE001 - the test reports it
            errors.append(exc)

    waiting_on_row = text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                          "AND wait_event_type = 'Lock' AND query ILIKE '%FROM projects%'")
    threads = [threading.Thread(target=handler), threading.Thread(target=reclaimer)]
    for t in threads:
        t.start()
    for _ in range(50):
        with session_scope() as s2:
            if s2.scalar(waiting_on_row):
                break
        time.sleep(0.1)
    reclaim_waits.set()
    for t in threads:
        t.join(10)
    hung = [t for t in threads if t.is_alive()]
    if hung:  # break the cycle so the rest of the suite can run
        with session_scope() as s2:
            s2.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = current_database() "
                            "AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"))
        for t in threads:
            t.join(10)
    assert (len(hung), errors) == (0, [])
    with session_scope() as s2:
        assert (s2.get(Job, jid).status, s2.get(Project, pid).status) == ("failed", "FAILED")


def test_a_budget_raise_stands_while_the_run_waits_out_its_old_holder_and_the_run_restarts_at_the_hold_end(
        s, project, korean_owner):
    from justshipit.db.models import Task

    pid = project.id
    project.status, project.error, project.budget_usd = "FAILED", "BudgetExceeded: project budget 10", 10.0
    task = Task(project_id=pid, key="budget-held", title="예산에 막힌 업무", objective="o", owner="research",
                status="FAILED", error="BudgetExceeded: project budget 10 USD-eq would be exceeded")
    s.add(task)
    s.commit()
    run = _enqueue("run_project", {"project_id": pid})
    _claim("worker-asleep", ["run_project"])
    _suspend(run, spent=True)
    _reclaim()

    with session_scope() as s2:
        control.set_budget(s2, pid, 20.0, resume_run=True)

    with session_scope() as s2:
        until = s2.get(Job, run).held_until
        change = s2.get(Project, pid).context["budget_changes"][-1]
        assert (s2.get(Project, pid).budget_usd, change["to"]) == (20.0, 20.0)
        assert f"{queue.local_time(until)}에 멈춘 곳부터 다시 실행해요" in change["resume"]
        assert s2.get(Task, task.id).status == "READY"
    assert _claim("worker-new", ["retry_project"]) is None
    with session_scope() as s2:
        s2.execute(text("UPDATE jobs SET held_until = now() WHERE id = :id"), {"id": run})
        s2.execute(text("UPDATE jobs SET run_after = now() WHERE kind = 'retry_project' AND status = 'queued' "
                        "AND (payload->>'project_id')::int = :p"), {"p": pid})
    assert _claim("worker-new", ["retry_project"])[1] == "retry_project"
    with session_scope() as s2:
        s2.execute(text("UPDATE jobs SET status = 'done' WHERE kind = 'retry_project' "
                        "AND (payload->>'project_id')::int = :p"), {"p": pid})


def test_a_budget_raise_stands_while_a_worker_still_runs_the_run_and_says_so(korean_owner, s, project):
    pid = project.id
    project.status, project.error, project.budget_usd = "FAILED", "BudgetExceeded: project budget 10", 10.0
    s.commit()
    run = _enqueue("run_project", {"project_id": pid})
    _claim("worker-busy", ["run_project"])

    with session_scope() as s2:
        control.set_budget(s2, pid, 20.0, resume_run=True)

    with session_scope() as s2:
        p = s2.get(Project, pid)
        assert p.budget_usd == 20.0
        assert msg("queue.busy.running", "ko", id=run, worker="worker-busy").rstrip(".") in p.context["budget_changes"][-1]["resume"]
        s2.execute(text("UPDATE jobs SET status = 'done' WHERE id = :id"), {"id": run})


@pytest.mark.parametrize("queued_kind, continues", [("run_project", True), ("retry_project", True),
                                                    ("resume_project", False)])
def test_a_budget_raise_says_the_run_continues_only_when_a_queued_job_will_restart_it(korean_owner, s, project, queued_kind,
                                                                                       continues):
    """A queued resume is skipped on a FAILED project, so it carries nothing on: the raise queues a retry behind it."""
    from justshipit.orchestration import runner

    pid = project.id
    project.status, project.error, project.budget_usd = "FAILED", "BudgetExceeded: project budget 10", 10.0
    s.commit()
    queued = _enqueue(queued_kind, {"project_id": pid})

    with session_scope() as s2:
        control.set_budget(s2, pid, 20.0, resume_run=True)

    with session_scope() as s2:
        said = s2.get(Project, pid).context["budget_changes"][-1]["resume"]
    assert _claim("worker-a", list(queue.RUN_KINDS))[:2] == (queued, queued_kind)
    if continues:
        assert said == msg("work.restart.carries_on", "ko", why=msg("queue.busy.queued", "ko", id=queued, when="").rstrip("."))
        assert _claim("worker-b", list(queue.RUN_KINDS)) is None
    else:
        assert said == "멈춘 곳부터 다시 실행할게요."
        assert runner.resume(pid) == {"skipped": "project is FAILED"}
        with session_scope() as s2:
            queue.finish(s2, queued, s2.get(Job, queued).lease_token)
        assert _claim("worker-b", list(queue.RUN_KINDS))[1] == "retry_project"
    with session_scope() as s2:
        s2.execute(text("UPDATE jobs SET status = 'done' WHERE lock_key = :k AND status IN ('queued', 'running')"),
                   {"k": f"project:{pid}"})


def test_a_budget_raise_names_no_wait_for_an_old_holder_whose_hold_already_ended(s, project, korean_owner):
    """A resume reclaimed long ago and queued again by a failed attempt's backoff still carries its ended hold."""
    pid = project.id
    project.status, project.error, project.budget_usd = "FAILED", "BudgetExceeded: project budget 10", 10.0
    s.commit()
    resume = _enqueue("resume_project", {"project_id": pid}, max_attempts=3)
    with session_scope() as s2:
        s2.execute(text("UPDATE jobs SET held_until = now() - interval '1 hour', "
                        "run_after = now() + interval '2 minutes' WHERE id = :id"), {"id": resume})

    with session_scope() as s2:
        control.set_budget(s2, pid, 20.0, resume_run=True)

    with session_scope() as s2:
        said = s2.get(Project, pid).context["budget_changes"][-1]["resume"]
    assert said == "멈춘 곳부터 다시 실행할게요."
    assert _claim("worker-a", list(queue.RUN_KINDS))[1] == "retry_project"
    with session_scope() as s2:
        s2.execute(text("UPDATE jobs SET status = 'done' WHERE lock_key = :k AND status IN ('queued', 'running')"),
                   {"k": f"project:{pid}"})


def test_a_handler_waking_with_the_budget_month_locked_and_the_orphan_release_never_hang(s, project):
    """The reviewer's proof at f4031b1: a handler whose host slept commits with the month's budget row locked
    (as budget.reserve does), while the reaper's orphan release holds the claim lock and waits for that row."""
    import threading
    import time

    from justshipit.db.models import BudgetMonth
    from justshipit.services import budget

    with session_scope() as s2:  # no live lease anywhere, so the release clears every reservation
        s2.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL WHERE status = 'running'"))
    jid = _enqueue("run_project", {"project_id": project.id}, max_attempts=3)
    with session_scope() as s2:
        token = queue.claim(s2, "worker-asleep", ["run_project"]).lease_token
    _suspend(jid)
    _reclaim()
    month = budget._month()
    with session_scope() as s2:
        s2.execute(text("INSERT INTO budget_months(month, limit_usd, spent_usd, reserved_usd) VALUES (:m, 100, 0, 1) "
                        "ON CONFLICT (month) DO UPDATE SET reserved_usd = 1"), {"m": month})
    row_locked = threading.Event()
    out: dict[str, object] = {}

    def handler():
        lease = queue.Lease(jid, token)
        lease.confirmed_at = time.time() - 2 * queue.lease_s()  # the beat thread slept with the host
        queue.current_lease.set(lease)
        try:
            with session_scope() as s2:
                s2.get(BudgetMonth, month, with_for_update=True)
                row_locked.set()
                time.sleep(1)  # the reaper is now waiting for this row
            out["handler"] = "committed"
        except BaseException as exc:  # noqa: BLE001 - the test reports it
            out["handler"] = type(exc).__name__

    def reaper():
        row_locked.wait(5)
        try:
            out["reaper"] = budget.release_orphaned_reservations()
        except BaseException as exc:  # noqa: BLE001 - the test reports it
            out["reaper"] = repr(exc)

    threads = [threading.Thread(target=handler), threading.Thread(target=reaper)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    hung = [t for t in threads if t.is_alive()]
    if hung:  # break the cycle so the rest of the suite can run
        with session_scope() as s2:
            s2.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = current_database() "
                            "AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"))
        for t in threads:
            t.join(10)
    assert (len(hung), out) == (0, {"handler": "LeaseLost", "reaper": 1.0})


@pytest.mark.parametrize("subject", ["run", "dev"])
def test_two_refused_retries_a_minute_apart_name_the_same_end(s, project, monkeypatch, subject):
    """With no worker running, the owner's retry is what reclaims the lapsed job. A refusal that rolled that reclaim
    back left the next click to reclaim again, so every click named a new "now + rerun delay"."""
    from justshipit.api import console
    from fastapi import HTTPException

    delay = [46 * 60.0]
    monkeypatch.setattr(queue, "rerun_delay_s", lambda: delay[0])
    if subject == "run":
        project.status = "RUNNING"
        s.commit()
        jid = _enqueue("run_project", {"project_id": project.id})
        _claim("worker-asleep", ["run_project"])

        def retry():
            with pytest.raises(HTTPException) as refused:
                console.retry_project(project.id)
            return refused.value.detail
    else:
        d, key = _dev_job(s, project)
        jid = _enqueue("dev_job", {"dev_job_id": d.id})
        _claim("worker-asleep", ["dev_job"])

        def retry():
            with pytest.raises(ValueError) as refused:
                specs.retry_dev(key, "ko")
            return str(refused.value)
    _suspend(jid)

    first = retry()
    delay[0] += 60  # the owner clicks again a minute later
    second = retry()

    with session_scope() as s2:
        until = s2.get(Job, jid).held_until
    assert second == first
    assert until is not None and queue.local_time(until, "ko" if subject == "dev" else None) in first


def test_a_wait_names_its_end_by_date_time_and_zone(korean_owner, s, project, monkeypatch):
    """The owner may read the refusal the next day, on a server in another zone: 19:33 alone says neither."""
    import time
    from zoneinfo import ZoneInfo

    monkeypatch.setenv("TZ", "Asia/Seoul")
    time.tzset()
    try:
        pid = project.id
        project.status = "RUNNING"
        s.commit()
        for spent, says in ((False, "에 이어서 실행해요"), (True, "까지 기다려요")):  # requeued, then failed
            jid = _enqueue("run_project", {"project_id": pid})
            _claim("worker-asleep", ["run_project"])
            _suspend(jid, spent=spent)
            _reclaim()
            with session_scope() as s2:
                until = s2.get(Job, jid).held_until.astimezone(ZoneInfo("Asia/Seoul"))
            with pytest.raises(control.ControlError) as refused, session_scope() as s2:
                control.retry_project(s2, pid)
            assert f"{until.month}월 {until.day}일 {until:%H:%M} (KST){says}" in str(refused.value)
            _wait_passes(jid)
            with session_scope() as s2:
                s2.get(Job, jid).status = "done"
    finally:
        monkeypatch.undo()
        time.tzset()


def test_a_merge_never_waits_for_the_claim_lock_while_it_holds_its_grant_row(s, project):
    """confirm_lease waits for the claim lock on a connection of its own, so a caller holding a row lock then
    holds it for as long as a reclaim runs, and forever if that reclaim ever needs the row."""
    import threading
    import time

    from justshipit.db.models import AgentRequest, Grant
    from justshipit.engineering import worker
    from sqlalchemy import select
    from sqlalchemy.exc import OperationalError

    d, _ = _dev_job(s, project)
    r = AgentRequest(project_id=project.id, from_agent="product", type="APPROVAL", title="merge", summary="s",
                     rationale="r", action_kind="dev.merge", action_params={"dev_job_id": d.id}, status="APPROVED",
                     policy_mode="strong_approve")
    s.add(r)
    s.flush()
    s.add(Grant(request_id=r.id, project_id=project.id, action_kind="dev.merge", max_uses=1,
                expires_at=datetime.now(UTC) + timedelta(hours=1)))
    s.commit()
    jid = _enqueue("dev_merge", {"dev_job_id": d.id})
    with session_scope() as s2:
        token = queue.claim(s2, "worker-merging", ["dev_merge"]).lease_token

    def merger():
        queue.current_lease.set(queue.Lease(jid, token))
        try:
            worker.merge(d.id)
        except Exception:  # noqa: BLE001 - there is no product repo; only the locks matter here
            pass

    waits_on_claim_lock = text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                               "AND wait_event = 'advisory'")
    with session_scope() as reclaim:  # a reclaim is running
        queue.take_claim_lock(reclaim)
        th = threading.Thread(target=merger)
        th.start()
        for _ in range(50):
            with session_scope() as s2:
                if s2.scalar(waits_on_claim_lock):
                    break
            time.sleep(0.1)
        try:
            with session_scope() as s2:
                s2.scalar(select(Grant.id).where(Grant.request_id == r.id).with_for_update(nowait=True))
            grant_free = True
        except OperationalError:
            grant_free = False
    th.join(10)
    assert (grant_free, th.is_alive()) == (True, False)
