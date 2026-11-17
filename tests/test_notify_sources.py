"""Where a notice starts: each source enqueues it in its own transaction, once per event."""
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from justshipit.contracts.notify import Notice
from justshipit.contracts.requests import AgentRequestDraft, RequestStatus, RequestType, RiskLevel
from justshipit.db.models import DevJob, Job, Project, Spec
from justshipit.db.session import session_scope
from justshipit.engineering import worker
from justshipit.services import app_settings, queue, requests
from justshipit.services.usage import MustWait
from notify_kit import choose, notice_jobs, wipe
from owner_task_kit import approve, tasks_of
from owner_task_kit import draft as post_draft
from sqlalchemy import select, text
from ask_kit import by_task, contract, by_project_manager
from justshipit.contracts.work_path import HoldKind


@pytest.fixture(autouse=True)
def clean():
    wipe()
    yield
    wipe()
    with session_scope() as s:
        s.execute(text("DELETE FROM jobs WHERE payload ? 'notify_test'"))


def words(job: Job, lang: str = "ko") -> tuple[str, str]:
    """The notice's title and body as the sender puts them into words."""
    return Notice.model_validate(job.payload).words(lang)


def plain_draft(**kw) -> AgentRequestDraft:
    base = dict(type=RequestType.APPROVAL, title="t", summary="s", rationale="r", options=[],
                recommended_option=None, action_kind=None, estimated_cost_usd=None,
                risk_level=RiskLevel.LOW, context=contract())
    return AgentRequestDraft(**{**base, **kw})


def keys() -> list[str]:
    return [j.idempotency_key for j in notice_jobs()]


def running_job(kind: str, payload: dict, *, attempts: int = 1, max_attempts: int = 1, stale: bool = False) -> int:
    payload = {**payload, "notify_test": True}
    with session_scope() as s:
        job = Job(gate="direct", kind=kind, payload=payload, status="running", attempts=attempts, max_attempts=max_attempts,
                  locked_by="worker-notify", lease_token="tok", lock_key=queue._lock_key(kind, payload),
                  locked_at=datetime.now(timezone.utc) - (timedelta(hours=1) if stale else timedelta()))
        s.add(job)
        s.flush()
        return job.id


def dev_job(project_id: int, status: str = "IMPLEMENTING") -> tuple[int, str]:
    key = f"SPEC-N-{uuid.uuid4().hex[:8]}"
    with session_scope() as s:
        spec = Spec(spec_key=key, project_id=project_id, title="알림 명세", registry_path="specs/n",
                    status="IN_DEVELOPMENT")
        s.add(spec)
        s.flush()
        job = DevJob(spec_id=spec.id, status=status)
        s.add(job)
        s.flush()
        return job.id, key


def running_project(project) -> None:
    with session_scope() as s:
        s.get(Project, project.id).status = "RUNNING"


# ------------------------------------------------------------------ a decision the owner has to make

def test_a_blocking_request_makes_exactly_one_notice(s, project):
    choose()

    req = requests.create(s, plain_draft(context=contract(HoldKind.THIS_TASK), title="도메인 구매"), from_agent="project_manager", project_id=project.id, origin=by_task(s, project.id))
    again = requests.create(s, plain_draft(context=contract(HoldKind.THIS_TASK), title="도메인 구매"), from_agent="cpo", project_id=project.id, origin=by_task(s, project.id))
    s.commit()

    assert again.id == req.id
    [job] = notice_jobs()
    assert job.idempotency_key == f"notify:decision_needed:REQ-{req.id}:pending"
    assert job.payload["title"] == "도메인 구매" and job.payload["link"] == f"/inbox?req={req.id}"


def test_a_decision_notice_belongs_to_its_business_so_another_business_does_not_list_it(s, api_token):
    from fastapi.testclient import TestClient
    from justshipit.api.app import app
    from justshipit.services import projects, roadmap
    choose()
    mine, other = (roadmap.create_business(s, name=n, one_liner="x") for n in ("Notice owner", "Someone else"))
    p = projects.create(s, "알림 목표", name="Notice", budget_usd=10, start=False, business=mine)
    requests.create(s, plain_draft(context=contract(HoldKind.THIS_TASK), title="Which audience first"), from_agent="project_manager", project_id=p.id, origin=by_task(s, p.id))
    s.commit()

    [job] = notice_jobs()
    assert job.business_id == mine.id
    assert "about" not in job.payload  # the message the sender reads is unchanged
    api = TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": f"Bearer {api_token}"})
    listed = lambda bid: {j["id"] for j in api.get(f"/api/jobs?business_id={bid}&limit=200").json()}
    assert job.id in listed(mine.id) and job.id not in listed(other.id)


def test_a_routine_approval_waits_in_the_inbox_without_a_message(s, project):
    choose()

    req = requests.create(s, plain_draft(), from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()

    assert req.status == RequestStatus.PENDING.value and notice_jobs() == []


@pytest.mark.parametrize("kw", [dict(type=RequestType.QUESTION),
                                dict(type=RequestType.ESCALATION),
                                dict(action_kind="budget.spend", estimated_cost_usd=5.0)])
def test_a_question_an_escalation_and_a_strong_approval_are_told_though_not_blocking(s, project, kw):
    choose()

    req = requests.create(s, plain_draft(**kw), from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()

    assert req.status == RequestStatus.PENDING.value and keys() == [f"notify:decision_needed:REQ-{req.id}:pending"]


def test_a_request_the_company_decided_itself_is_not_told(s, project):
    choose()

    req = requests.create(s, plain_draft(type=RequestType.SKILL, action_kind="skill.create_candidate"),
                          from_agent="research", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()

    assert req.status == RequestStatus.AUTO_APPROVED.value and notice_jobs() == []


def test_a_request_that_becomes_blocking_when_raised_again_is_told_then(s, project):
    choose()
    quiet = requests.create(s, plain_draft(title="도메인 구매"), from_agent="project_manager", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    s.commit()
    assert notice_jobs() == []

    same = requests.create(s, plain_draft(context=contract(HoldKind.THIS_TASK), title="도메인 구매"), from_agent="cpo", project_id=project.id, origin=by_task(s, project.id))
    s.commit()

    assert same.id == quiet.id and keys() == [f"notify:decision_needed:REQ-{quiet.id}:pending"]


def test_the_notice_is_in_the_requests_own_transaction(s, project):
    s.commit()
    choose()
    with pytest.raises(RuntimeError), session_scope() as tx:
        requests.create(tx, plain_draft(context=contract(HoldKind.THIS_TASK), title="롤백될 요청"), from_agent="project_manager", project_id=project.id, origin=by_task(tx, project.id))
        raise RuntimeError("the change did not commit")

    assert notice_jobs() == []


# ------------------------------------------------------------------ a task only the owner can do

def test_an_owner_task_makes_one_notice_when_the_owner_chose_that_kind(s, project):
    choose(kinds=["owner_task"])
    req = requests.create(s, post_draft(), from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    approve(s, req)
    s.commit()

    [task] = tasks_of(req.id)
    [job] = notice_jobs()
    assert job.idempotency_key == f"notify:owner_task:OT-{task.id}:open" and job.payload["title"] == "Show HN 게시"
    assert "게시" in words(job)[1] and "Post" in words(job, "en")[1]


def test_an_owner_task_is_not_told_with_the_default_kinds(s, project):
    choose()
    approve(s, requests.create(s, post_draft(), from_agent="marketing", project_id=project.id,
                               origin=by_project_manager(s, project.id)))
    s.commit()

    assert notice_jobs() == []


# ------------------------------------------------------------------ a run or a dev job that gave up

def test_a_run_that_gives_up_makes_one_notice_with_its_project_and_reason(s, project):
    choose()
    s.commit()
    running_project(project)
    jid = running_job("run_project", {"project_id": project.id})

    with session_scope() as s2:
        queue.finish(s2, jid, "tok", "RuntimeError: 모델이 응답하지 않았습니다\nTraceback ...")

    [job] = notice_jobs()
    assert job.idempotency_key == f"notify:failed:P-{project.id}:job{jid}"
    assert words(job)[0] == "Test 실행이 실패했어요" and "응답하지 않았습니다" in job.payload["body"]
    assert words(job, "en")[0] == "Test: the run failed"
    assert job.payload["link"] == f"/projects/{project.id}"


def test_a_run_with_attempts_left_is_retried_and_not_told(s, project):
    choose()
    s.commit()
    running_project(project)
    jid = running_job("run_project", {"project_id": project.id}, attempts=1, max_attempts=3)

    with session_scope() as s2:
        queue.finish(s2, jid, "tok", "RuntimeError: x")

    assert notice_jobs() == []


def test_a_run_reclaimed_for_good_makes_one_notice_however_often_the_queue_looks(s, project):
    choose()
    s.commit()
    running_project(project)
    jid = running_job("retry_project", {"project_id": project.id}, attempts=2, max_attempts=2, stale=True)

    for _ in range(3):
        with session_scope() as s2:
            queue.claim(s2, "worker-reclaimer", ["no_such_kind"])

    assert keys() == [f"notify:failed:P-{project.id}:job{jid}"]


def test_a_second_failure_of_the_same_project_is_told_again(s, project):
    choose()
    s.commit()
    running_project(project)
    first = running_job("run_project", {"project_id": project.id})
    with session_scope() as s2:
        queue.finish(s2, first, "tok", "RuntimeError: 첫 번째")
    running_project(project)
    second = running_job("retry_project", {"project_id": project.id})
    with session_scope() as s2:
        queue.finish(s2, second, "tok", "RuntimeError: 두 번째")

    assert keys() == [f"notify:failed:P-{project.id}:job{first}", f"notify:failed:P-{project.id}:job{second}"]


def test_a_dev_job_that_fails_by_the_workers_own_record_is_told_once_even_if_the_queue_fails_it_too(s, project):
    choose()
    s.commit()
    dev_id, spec_key = dev_job(project.id)

    worker._record_failure(dev_id, "AgentFailed: engineer/dev: 테스트 실패")
    jid = running_job("dev_job", {"dev_job_id": dev_id})
    with session_scope() as s2:
        queue.finish(s2, jid, "tok", "RuntimeError: 뒤늦은 실패")

    [job] = notice_jobs()
    assert job.idempotency_key == f"notify:failed:J-{dev_id}:failed"
    assert words(job)[0] == f"{spec_key} 개발이 실패했어요" and "테스트 실패" in job.payload["body"]
    assert job.payload["link"] == f"/engineering?job={dev_id}"


def test_a_dev_job_the_queue_gives_up_on_is_told(s, project):
    choose()
    s.commit()
    dev_id, _ = dev_job(project.id)
    jid = running_job("dev_job", {"dev_job_id": dev_id})

    with session_scope() as s2:
        queue.finish(s2, jid, "tok", "RuntimeError: 워커가 죽었습니다")

    assert keys() == [f"notify:failed:J-{dev_id}:failed"]


def test_a_failed_merge_is_told(s, project):
    choose()
    s.commit()
    dev_id, _ = dev_job(project.id, "REVIEWING")
    jid = running_job("dev_merge", {"dev_job_id": dev_id})

    with session_scope() as s2:
        queue.finish(s2, jid, "tok", "RuntimeError: 머지 충돌")

    assert keys() == [f"notify:failed:J-{dev_id}:merge"]


def test_a_merge_that_fails_after_a_failed_dev_job_is_its_own_notice(s, project):
    choose()
    s.commit()
    dev_id, spec_key = dev_job(project.id, "REVIEWING")
    worker._record_failure(dev_id, "AgentFailed: engineer/dev: 테스트 실패")
    jid = running_job("dev_merge", {"dev_job_id": dev_id})

    with session_scope() as s2:
        queue.finish(s2, jid, "tok", "RuntimeError: 머지 충돌")

    first, merge = notice_jobs()
    assert (first.idempotency_key, merge.idempotency_key) == (
        f"notify:failed:J-{dev_id}:failed", f"notify:failed:J-{dev_id}:merge")
    assert words(merge)[0] == f"{spec_key} 머지가 실패했어요"


# ------------------------------------------------------------------ work that waits on usage

def waiting(until: datetime, reason: str = "usage_cap_day") -> str:
    jid = running_job("kpi_review", {"project_id": None})
    with session_scope() as s:
        queue.defer(s, jid, "tok", MustWait(until, reason, "하루 사용 상한(50 USD 환산)을 넘었습니다"))
    return reason


def test_a_deferral_for_usage_is_told_once_per_wait_window_not_once_per_job(korean_owner):
    choose(kinds=["usage_wait"])
    until = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=9)

    waiting(until)
    waiting(until)
    waiting(until + timedelta(minutes=30))

    [job] = notice_jobs()
    assert job.idempotency_key == f"notify:usage_wait:usage:usage_cap_day-{until:%Y%m%dT%H}"
    assert words(job)[0] == "하루 사용량 상한에 닿아서 일이 기다려요"
    assert words(job, "en")[0] == "Work is waiting: the company hit the daily usage cap"
    assert "(KST)" in words(job)[1] and job.payload["link"] == "/audit/costs"


def test_the_next_wait_window_is_told_again():
    choose(kinds=["usage_wait"])
    until = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=9)

    waiting(until)
    waiting(until + timedelta(days=1))
    waiting(until, "usage_cap_week")

    assert len(notice_jobs()) == 3, "a day cap and a week cap that end together are two reasons"


def test_a_usage_wait_is_not_told_with_the_default_kinds():
    choose()

    waiting(datetime.now(timezone.utc) + timedelta(hours=9))

    assert notice_jobs() == []


def test_the_zone_of_a_usage_wait_is_the_owners(korean_owner, s):
    choose(kinds=["usage_wait"])
    app_settings.save(s, "usage", {"timezone": "America/New_York"}, actor="test")
    s.commit()

    until = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=9)
    waiting(until)

    [job] = notice_jobs()
    there = until.astimezone(ZoneInfo("America/New_York"))
    assert f"{there.month}월 {there.day}일 {there:%H:%M} ({there.tzname()})" in words(job)[1]


# ------------------------------------------------------------------ the scheduler

def test_the_scheduler_tick_asks_for_the_briefing_and_survives_its_failure(s, monkeypatch):
    from justshipit.operations import notify, scheduler
    calls = []
    monkeypatch.setattr(notify, "schedule_briefing", lambda session: calls.append(session))
    scheduler.tick(s)
    assert calls == [s]

    def broken(session):
        session.execute(text("SELECT no_such_column FROM jobs"))
    monkeypatch.setattr(notify, "schedule_briefing", broken)
    scheduler.tick(s)
    assert s.scalar(select(1)) == 1, "the tick's own transaction is still usable"
