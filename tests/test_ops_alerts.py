from datetime import datetime, timedelta, timezone

import pytest
from justshipit.api import console
from justshipit.db.models import DevJob, Job, Project, Spec
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import queue
from sqlalchemy import func, select, text

STALE = timedelta(hours=1)


@pytest.fixture(autouse=True)
def _isolated_jobs():
    yield
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL WHERE payload ? 'ops_alert_test'"))


def _job(**kw) -> int:
    with session_scope() as s:
        job = Job(gate="direct", **{"payload": {}, "status": "failed", "attempts": 1, "max_attempts": 1, **kw})
        job.payload = {**job.payload, "ops_alert_test": True}
        job.lock_key = queue._lock_key(job.kind, job.payload)
        s.add(job)
        s.flush()
        return job.id


def _alerts(kind: str, **match) -> list[dict]:
    return [a for a in console.alerts(include_test=True)
            if a["kind"] == kind and all(a.get(k) == v for k, v in match.items())]


def _reclaim() -> None:
    with session_scope() as s:
        queue.claim(s, "worker-reclaimer", ["no_such_kind"])


def _dev_job(project_id: int, status: str) -> int:
    from justshipit.contracts.handoff import Handoff

    handoff = Handoff(spec_id="SPEC-OPS", project="Test", title="알림 명세", source={"path": "specs/ops", "commit_sha": "abc"},
                      target={"repository": "demo"}, approval={"spec_approved": True, "implementation_authorized": True})
    with session_scope() as s:
        spec = Spec(spec_key=f"SPEC-OPS-{project_id}-{status}", project_id=project_id, title="알림 명세",
                    registry_path="specs/ops", status="IN_DEVELOPMENT", approved_commit_sha="abc",
                    handoff=handoff.model_dump(mode="json"))
        s.add(spec)
        s.flush()
        job = DevJob(spec_id=spec.id, status=status)
        s.add(job)
        s.flush()
        return job.id


def test_failed_queue_job_alerts_once_with_its_kind_until_retried(korean_owner, s, project):
    s.commit()
    payload = {"project_id": project.id, "request_id": 990001}
    first = _job(kind="write_spec", payload=payload, last_error="AgentFailed: cpo/spec: 명세 초안 실패\nTraceback ...")
    [a] = _alerts("job_failed", project_id=project.id)
    assert (a["job_id"], a["severity"]) == (first, "bad")
    assert a["title"] == "명세 쓰기"
    assert a["detail"] == "AgentFailed: cpo/spec: 명세 초안 실패"

    second = _job(kind="write_spec", payload=payload, last_error="AgentFailed: 두 번째")
    assert [a["job_id"] for a in _alerts("job_failed", project_id=project.id)] == [second]

    queue.worker_seen(s, "worker-ops-test")   # a worker runs the copy; without one the alert stays (test below)
    s.commit()
    retried = console.job_retry(second)
    assert [a["job_id"] for a in _alerts("job_failed", project_id=project.id)] == []
    with session_scope() as s2:
        j = s2.get(Job, retried["job_id"])
        assert (j.kind, j.status, j.payload) == ("write_spec", "queued", {**payload, "ops_alert_test": True})


def test_a_job_retried_while_no_worker_runs_leaves_the_alerts_and_the_worker_alert_stays(korean_owner, s, project):
    s.execute(text("UPDATE workers SET seen_at = now() - interval '1 hour'"))
    s.commit()
    payload = {"project_id": project.id, "request_id": 990002}
    failed = _job(kind="write_spec", payload=payload, last_error="AgentFailed: 명세 초안 실패")

    assert console.job_retry(failed)["worker_running"] is False
    assert _alerts("job_failed", project_id=project.id) == [], "the owner retried it: nothing is left for them there"
    assert len(_alerts("worker_down")) == 1, "the worker alert says why nothing runs yet"


def _failed_task(s, project, error: str, key: str = "ops-retry"):
    from justshipit.contracts.work import TaskStatus
    from justshipit.db.models import Task
    from justshipit.services import tasks
    t = Task(project_id=project.id, key=key, title="조사하기", objective="o", owner="research", status="READY",
             acceptance_criteria=[], budget_usd=1.0)
    s.add(t)
    s.flush()
    tasks.transition(t, TaskStatus.RUNNING)
    s.flush()
    tasks.transition(t, TaskStatus.FAILED, error=error)
    return t


def test_a_task_retried_while_no_worker_runs_leaves_the_alerts(korean_owner, s, project):
    t = _failed_task(s, project, "usage limit reached")
    s.execute(text("UPDATE workers SET seen_at = now() - interval '1 hour'"))
    s.commit()
    [a] = _alerts("task_failed", task_id=t.id)
    assert a["retryable"] is True

    answer = console.task_retry(t.id)
    assert answer["worker_running"] is False
    assert (answer["budget_was_usd"], answer["budget_usd"] > 1.0) == (1.0, True), "the retry says it raised the budget"
    assert _alerts("task_failed", task_id=t.id) == [], "a retried task is no longer a failure waiting on the owner"
    assert len(_alerts("worker_down")) == 1


def test_a_task_retry_carries_the_owners_note_to_the_new_attempt(korean_owner, s, project):
    from justshipit.db.models import AgentMessage
    t = _failed_task(s, project, "usage limit reached", key="ops-note")
    s.commit()
    console.task_retry(t.id, console.TaskRetryIn(note="  공개 가격표만 보세요  "))
    with session_scope() as s2:
        notes = s2.scalars(select(AgentMessage.body).where(AgentMessage.ref_type == "task", AgentMessage.ref_id == t.id,
                                                          AgentMessage.kind == "comment")).all()
        assert notes == ["공개 가격표만 보세요"]
        assert [c["body"] for c in console.task_detail(t.id)["comments"]] == ["공개 가격표만 보세요"]


def test_a_refused_task_retry_keeps_no_half_of_it(korean_owner, s, project):
    from fastapi import HTTPException
    from justshipit.db.models import AgentMessage
    t = _failed_task(s, project, "boom went the thing", key="ops-refused")
    project.phase = "reporting"
    s.commit()
    [a] = _alerts("task_failed", task_id=t.id)
    assert (a["retryable"], a["hint"]) == (False, msg("console.alert.task_closed", "ko"))
    with pytest.raises(HTTPException):
        console.task_retry(t.id, console.TaskRetryIn(note="메모"))
    with session_scope() as s2:
        assert s2.scalar(select(func.count()).select_from(AgentMessage).where(AgentMessage.ref_id == t.id,
                                                                             AgentMessage.ref_type == "task")) == 0


def test_a_usage_limit_reads_as_the_owners_sentence_and_the_raw_text_stays_in_detail(s, project):
    t = _failed_task(s, project, "claude -p exited 1: usage limit reached", key="ops-quota")
    s.commit()
    [a] = _alerts("task_failed", task_id=t.id)
    assert a["reason"] == msg("console.reason.quota", "en")
    assert a["detail"] == "claude -p exited 1: usage limit reached"


def test_a_build_whose_spec_is_not_approved_offers_no_retry_but_says_why(s, project):
    project.status = "RUNNING"
    s.commit()
    dev_id = _dev_job(project.id, "QA")
    with session_scope() as s2:
        spec = s2.get(Spec, s2.get(DevJob, dev_id).spec_id)
        spec.approved_commit_sha = None
    queue.worker_seen(s, "worker-ops-test")  # with no worker at all, a build waits for it instead of stalling
    s.commit()
    [a] = _alerts("dev_stalled", project_id=project.id)
    assert (a["retryable"], a["hint"]) == (False, msg("console.alert.dev_no_spec", "en"))


def test_every_queue_job_kind_alerts_in_korean(korean_owner, s, project):
    s.commit()
    for kind in ("dev_merge", "execute_action", "realign_project", "acceptance", "kpi_review", "portfolio_review"):
        _job(kind=kind, payload={"project_id": project.id, "request_id": 990501, "kind_case": kind},
             last_error="RuntimeError: x")
    titles = {a["title"] for a in _alerts("job_failed", project_id=project.id)}
    assert titles == {"머지", "외부 실행", "계획 다시 맞추기", "완료 기준 확인", "KPI 점검", "사업 전체 점검"}


def test_reclaimed_run_job_fails_its_project_with_one_alert(korean_owner, s, project):
    project.status = "RUNNING"
    s.commit()
    jid = _job(kind="retry_project", payload={"project_id": project.id}, status="running", locked_by="worker-dead",
               attempts=2, max_attempts=2, locked_at=datetime.now(timezone.utc) - STALE)  # its one retry used
    _reclaim()
    with session_scope() as s2:
        p = s2.get(Project, project.id)
        assert p.status == "FAILED"
        assert p.error == msg("queue.gave_up.run_lost", "ko")  # the reclaim's own text stays in the job's error
    assert len(_alerts("project_failed", project_id=project.id)) == 1
    assert _alerts("job_failed", job_id=jid) == []
    assert _alerts("run_stalled", project_id=project.id) == []


def test_dev_job_that_gives_up_marks_its_dev_job_failed(korean_owner, s, project):
    s.commit()
    reclaimed_dev = _dev_job(project.id, "IMPLEMENTING")
    _job(kind="dev_job", payload={"dev_job_id": reclaimed_dev}, status="running", locked_by="worker-dead",
         locked_at=datetime.now(timezone.utc) - STALE, attempts=3, max_attempts=3)  # its last try
    _reclaim()
    raised_dev = _dev_job(project.id, "QA")
    jid = _job(kind="dev_job", payload={"dev_job_id": raised_dev}, status="running", locked_by="worker-a",
               lease_token="tok-a", locked_at=datetime.now(timezone.utc))
    with session_scope() as s2:
        queue.finish(s2, jid, "tok-a", "AgentFailed: engineer/dev: 테스트 실패\nTraceback ...")
    with session_scope() as s2:
        for dev_id, why in ((reclaimed_dev, "회수"), (raised_dev, "AgentFailed: engineer/dev: 테스트 실패")):
            d = s2.get(DevJob, dev_id)
            assert d.status == "FAILED"
            assert why in d.error
            assert s2.get(Spec, d.spec_id).status == "READY_FOR_DEV"
    assert {a["dev_job_id"] for a in _alerts("dev_failed", project_id=project.id)} == {reclaimed_dev, raised_dev}
    assert _alerts("job_failed", project_id=project.id) == []


def test_no_live_worker_alerts_until_a_worker_reports_in(s):
    s.execute(text("UPDATE workers SET seen_at = now() - interval '1 hour'"))
    s.commit()
    [a] = _alerts("worker_down")
    assert a["severity"] == "bad"
    assert console.liveness(s)[0] is False
    queue.worker_seen(s, "worker-ops-test")
    s.commit()
    assert _alerts("worker_down") == []
    assert console.liveness(s)[0] is True


def test_running_project_and_dev_job_without_a_live_job_are_stalled(s, project):
    project.status = "RUNNING"
    queue.worker_seen(s, "worker-ops-test")   # with no worker at all, every run waits for it instead
    s.commit()
    dev_id = _dev_job(project.id, "IMPLEMENTING")
    [a] = _alerts("run_stalled", project_id=project.id)
    assert a["severity"] == "bad"
    assert [a["dev_job_id"] for a in _alerts("dev_stalled", project_id=project.id)] == [dev_id]
    assert project.id in console.liveness(s)[1]

    _job(kind="run_project", payload={"project_id": project.id}, status="running", locked_by="worker-a",
         locked_at=datetime.now(timezone.utc))
    _job(kind="dev_job", payload={"dev_job_id": dev_id}, status="queued")
    assert _alerts("run_stalled", project_id=project.id) == []
    assert _alerts("dev_stalled", project_id=project.id) == []


def test_stalled_running_project_can_be_retried(s, project):
    from justshipit.services import control
    project.status = "RUNNING"
    s.commit()
    with session_scope() as s2:
        control.retry_project(s2, project.id)
    assert _alerts("run_stalled", project_id=project.id) == []
    with session_scope() as s2:
        s2.execute(text("UPDATE jobs SET status = 'done' WHERE kind = 'retry_project' AND status = 'queued' "
                        "AND (payload->>'project_id')::int = :p"), {"p": project.id})


def test_owner_dismisses_a_failed_job_alert_with_or_without_a_project(s, project):
    s.commit()
    with_project = _job(kind="write_spec", payload={"project_id": project.id, "request_id": 990101},
                        last_error="AgentFailed: x")
    companywide = _job(kind="portfolio_review", payload={"dismiss_case": 1}, last_error="AgentFailed: y")
    assert {a["job_id"] for a in _alerts("job_failed")} >= {with_project, companywide}

    for jid in (with_project, companywide):
        console.job_dismiss(jid)
        console.job_dismiss(jid)
    assert {a["job_id"] for a in _alerts("job_failed")} & {with_project, companywide} == set()
    with session_scope() as s2:
        assert s2.get(Job, companywide).status == "failed"  # the record stays; only the alert closes
        events = s2.execute(text("SELECT count(*) FROM activity_events WHERE entity = 'job' AND verb = 'dismissed' "
                                 "AND entity_id IN (:a, :b) AND actor = 'owner'"),
                            {"a": with_project, "b": companywide}).scalar()
        assert events == 2


def test_retrying_a_failed_job_twice_queues_one_copy(s, project):
    s.commit()
    jid = _job(kind="write_spec", payload={"project_id": project.id, "request_id": 990201}, last_error="x")
    first, second = console.job_retry(jid)["job_id"], console.job_retry(jid)["job_id"]
    assert first == second
    with session_scope() as s2:
        assert s2.scalar(select(func.count()).select_from(Job).where(
            Job.status == "queued", Job.payload["request_id"].as_integer() == 990201)) == 1


def test_side_effect_job_alert_offers_no_retry(s, project):
    s.commit()
    action = _job(kind="execute_action", payload={"project_id": project.id, "request_id": 990502, "retry_case": 1},
                  last_error="x")
    spec = _job(kind="write_spec", payload={"project_id": project.id, "retry_case": 2}, last_error="x")
    shown = {a["job_id"]: a["retryable"] for a in _alerts("job_failed", project_id=project.id)}
    assert shown == {action: False, spec: True}


REFUSED = "BudgetExceeded: project budget 10.0 USD-eq would be exceeded"


def test_a_job_the_budget_stopped_asks_for_budget_and_offers_no_retry_that_would_fail_again(korean_owner, s, project):
    """The home read '도전 회고 작업 #239: 실패' with no reason, and its retry ran into the same budget."""
    project.spent_usd = project.budget_usd
    s.commit()
    jid = _job(kind="kpi_review", payload={"project_id": project.id, "budget_case": 1}, last_error=REFUSED)

    [a] = _alerts("job_failed", job_id=jid)
    assert (a["stop"], a["budget_project_id"], a["retryable"], a["hint"]) == ("budget", project.id, False, None)
    assert a["reason"].startswith("예산이 모자라 멈췄어요.") and "올리" in a["reason"]
    with pytest.raises(console.HTTPException) as refused:
        console.job_retry(jid)
    assert refused.value.status_code == 409 and "예산" in refused.value.detail

    with session_scope() as s2:
        s2.get(Project, project.id).budget_usd = 30
    [a] = _alerts("job_failed", job_id=jid)
    assert (a["stop"], a["retryable"]) == ("budget", True)


def test_a_bet_jobs_budget_is_its_runs(korean_owner, s, project):
    from bet_loop_kit import make_bet, make_business
    bet = make_bet(s, make_business(s, name="예산 사업"))
    project.bet_id, project.business_id, project.spent_usd = bet.id, bet.business_id, project.budget_usd
    s.commit()
    jid = _job(kind="bet_retro", payload={"bet_id": bet.id}, last_error=REFUSED)

    [a] = _alerts("job_failed", job_id=jid)
    assert (a["title"], a["stop"], a["budget_project_id"], a["retryable"]) == ("실험 회고", "budget", project.id, False)


def test_a_failed_job_says_why_in_the_owners_words():
    assert console.failure_reason("AgentFailed: cpo/spec: 명세 초안 실패\nTraceback", "ko") == "명세 초안 실패"
    assert console.failure_reason("RuntimeError: x", "ko") == "예상하지 못한 오류로 멈췄어요. 원문은 백그라운드 작업 화면에 있어요."
    assert console.failure_reason(None, "ko") == "원인이 기록되지 않았어요."


def test_an_english_owner_reads_an_english_cause_and_a_plain_sentence_for_a_bare_code():
    assert console.failure_reason("AgentFailed: cpo/spec: the spec draft failed\nTraceback", "en") == "the spec draft failed"
    assert console.failure_reason("RuntimeError: x", "en") == "It stopped on an unexpected error. The full text is under Background work."
    assert console.failure_reason(None, "en") == "No cause was recorded."


def test_a_task_the_budget_stopped_asks_for_budget(korean_owner, s, project):
    from justshipit.db.models import Task
    project.spent_usd = project.budget_usd
    t = Task(project_id=project.id, key="k", title="시장 조사", owner="research", objective="o", acceptance_criteria=[],
             status="FAILED", error="project budget 10.0 USD-eq would be exceeded")
    s.add(t)
    s.commit()

    [a] = _alerts("task_failed", task_id=t.id)
    assert (a["stop"], a["budget_project_id"]) == ("budget", project.id) and a["reason"].startswith("예산이 모자라")
    assert console.task_detail(t.id)["can"]["retry"] is False


def test_upgrade_closes_the_alerts_of_failures_from_before_it(s, project):
    import subprocess
    import sys
    from pathlib import Path
    s.commit()
    # The staffing revision refuses a database that holds tasks or builds, and this shared test database holds the rows
    # of every earlier test. Only the test database is emptied here.
    s.execute(text("TRUNCATE tasks, dev_jobs CASCADE"))
    s.commit()
    before = _job(kind="write_spec", payload={"project_id": project.id, "upgrade_case": 1}, last_error="old")
    root = Path(__file__).resolve().parents[1]
    for step in (["downgrade", "d4f8b2a6e713"], ["upgrade", "head"]):
        subprocess.run([sys.executable, "-m", "alembic", *step], cwd=root, check=True, capture_output=True)
    after = _job(kind="write_spec", payload={"project_id": project.id, "upgrade_case": 2}, last_error="new")
    assert [a["job_id"] for a in _alerts("job_failed", project_id=project.id)] == [after]
    with session_scope() as s2:
        assert s2.get(Job, before).status == "failed"


def test_a_job_whose_lease_went_stale_is_in_flight_for_alerts_and_retry_alike(korean_owner, s, project, dead_worker):
    from justshipit.engineering.specs import retry_dev
    project.status = "RUNNING"
    s.commit()
    stale = datetime.now(timezone.utc) - STALE
    _job(kind="run_project", payload={"project_id": project.id}, status="running", locked_by=dead_worker,
         locked_at=stale)
    dev_id = _dev_job(project.id, "IMPLEMENTING")
    _job(kind="dev_job", payload={"dev_job_id": dev_id}, status="running", locked_by=dead_worker, locked_at=stale,
         attempts=3, max_attempts=3)  # its last try: a build with tries left carries on (test_build_survive.py)

    assert _alerts("run_stalled", project_id=project.id) == []
    assert _alerts("dev_stalled", project_id=project.id) == []
    with session_scope() as s2:
        key = s2.get(Spec, s2.get(DevJob, dev_id).spec_id).spec_key
    with pytest.raises(ValueError, match="까지 기다려요"):
        retry_dev(key, "ko")
    _reclaim()  # the worker's tick
    with session_scope() as s2:
        assert s2.get(DevJob, dev_id).status == "FAILED"
        s2.execute(text("UPDATE jobs SET held_until = now() WHERE payload->>'dev_job_id' = :d"), {"d": str(dev_id)})
    retry_id = retry_dev(key, "ko")
    with session_scope() as s2:
        assert s2.get(DevJob, retry_id).status == "QUEUED"


def test_retry_project_reclaims_a_dead_workers_run_first_and_refuses_in_korean_until_its_hold_ends(korean_owner, 
        s, project, dead_worker):
    from justshipit.services import control
    project.status = "RUNNING"
    s.commit()
    run = _job(kind="run_project", payload={"project_id": project.id}, status="running", locked_by=dead_worker,
               locked_at=datetime.now(timezone.utc), attempts=2, max_attempts=2)
    with pytest.raises(control.ControlError, match="실행 중이에요"), session_scope() as s2:
        control.retry_project(s2, project.id)

    with session_scope() as s2:  # the worker died: its lease went stale
        s2.get(Job, run).locked_at = datetime.now(timezone.utc) - STALE
    with pytest.raises(control.ControlError, match="까지 기다려요"), session_scope() as s2:
        control.retry_project(s2, project.id)
    _reclaim()  # the worker's tick
    with session_scope() as s2:
        assert (s2.get(Job, run).status, s2.get(Project, project.id).status) == ("failed", "FAILED")
        s2.get(Job, run).held_until = func.now()
    with session_scope() as s2:
        control.retry_project(s2, project.id)

    with session_scope() as s2:
        assert s2.get(Job, run).status == "failed"
        assert s2.get(Project, project.id).status == "FAILED"
        assert s2.scalar(select(func.count()).select_from(Job).where(
            Job.kind == "retry_project", Job.status == "queued",
            Job.payload["project_id"].as_integer() == project.id)) == 1
        s2.execute(text("UPDATE jobs SET payload = payload || '{\"ops_alert_test\": true}' WHERE kind = 'retry_project'"
                        " AND (payload->>'project_id')::int = :p"), {"p": project.id})


def test_only_a_run_job_keeps_a_running_project_from_looking_stalled(s, project):
    project.status = "RUNNING"
    queue.worker_seen(s, "worker-ops-test")
    s.commit()
    _job(kind="write_spec", payload={"project_id": project.id, "request_id": 990301}, status="queued")
    assert [a["project_id"] for a in _alerts("run_stalled", project_id=project.id)] == [project.id]


def test_a_reclaimed_jobs_wait_is_an_alert_with_its_end_time_and_the_retry_names_the_same_time(s, project):
    from justshipit.services import control
    project.status = "RUNNING"
    s.commit()
    run = _job(kind="run_project", payload={"project_id": project.id}, status="running", locked_by="worker-dead",
               locked_at=datetime.now(timezone.utc) - STALE)
    _reclaim()
    with session_scope() as s2:
        until = s2.get(Job, run).held_until
        before = console.liveness(s2)
    assert [(a["job_id"], a["until"], a["resumes"], a["severity"]) for a in _alerts("job_waiting", project_id=project.id)] \
        == [(run, until, True, "warn")]
    assert _alerts("run_stalled", project_id=project.id) == []
    with pytest.raises(control.ControlError, match=until.astimezone().strftime("%H:%M")), session_scope() as s2:
        control.retry_project(s2, project.id)
    with session_scope() as s2:
        s2.execute(text("UPDATE jobs SET held_until = now() WHERE id = :id"), {"id": run})
        after = console.liveness(s2)
    assert _alerts("job_waiting", project_id=project.id) == []
    assert after != before  # the live feed announces the end of the wait


def test_a_failed_run_short_of_budget_is_a_budget_stop_even_when_its_error_does_not_say_so(s, project):
    """The project page offered the budget, the alert offered "Run again", which would only stop again."""
    project.status, project.error, project.spent_usd = "FAILED", "Stopped: the next call would pass the budget.", 9.9
    s.commit()

    [a] = _alerts("budget_stop", project_id=project.id)

    assert a["kind"] == "budget_stop"
    assert not _alerts("project_failed", project_id=project.id)
