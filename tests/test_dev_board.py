"""The 개발 page's account of each development: what is happening, since when, and the owner's one action. c3 saw
"아직 명세가 없어요" for 1h42m after approving development, and c1's failed job said only "engineer step failed"."""
from datetime import datetime, timedelta, timezone

import pytest
from justshipit.contracts.handoff import Handoff
from justshipit.db.models import AgentRequest, DevJob, Job, Spec
from justshipit.engineering import status
from justshipit.i18n import msg
from justshipit.services import queue
from sqlalchemy import select


@pytest.fixture(autouse=True)
def _korean_owner(monkeypatch):
    """These cases read the words a Korean owner sees; tests/test_build_i18n.py covers English."""
    from justshipit.services import app_settings
    monkeypatch.setattr(app_settings, "language", lambda: "ko")


@pytest.fixture
def s():
    """Rolled back: the board reads through this one session, and a running or failed job left behind would show in
    other tests' briefings and alerts."""
    from justshipit.db.session import session_scope
    with session_scope() as session:
        yield session
        session.rollback()


def _approved_development(s, project, status_="APPROVED") -> AgentRequest:
    r = AgentRequest(project_id=project.id, from_agent="product", type="DEVELOPMENT", title="개발 제안: 견적 답장 봇",
                     summary="s", rationale="r", status=status_, policy_mode="approve",
                     resolved_at=datetime.now(timezone.utc))
    s.add(r)
    s.flush()
    return r


def _write_spec_job(s, project, req, **kw) -> Job:
    payload = {"project_id": project.id, "request_id": req.id}
    job = Job(gate="direct", kind="write_spec", payload=payload, lock_key=queue._lock_key("write_spec", payload),
              idempotency_key=f"write_spec:{req.id}", **kw)
    s.add(job)
    s.flush()
    return job


def _only(s, project) -> dict:
    [stage] = status.board(s, project.id, lang="ko")
    return stage


def test_a_spec_being_written_says_who_writes_it_and_since_when(s, project):
    req = _approved_development(s, project)
    started = datetime.now(timezone.utc) - timedelta(minutes=12)
    _write_spec_job(s, project, req, status="running", locked_at=started, locked_by="w", lease_token="t")

    stage = _only(s, project)

    assert (stage["tone"], stage["headline"]) == ("run", "제품기획 에이전트가 명세를 쓰고 있어요.")
    assert stage["since"] == started and stage["ref"] == f"REQ-{req.id}" and stage["title"] == "견적 답장 봇"


def test_a_spec_that_waits_on_usage_says_until_when(s, project):
    req = _approved_development(s, project)
    _write_spec_job(s, project, req, status="queued", wait_reason="usage_cap_day",
                    run_after=datetime.now(timezone.utc) + timedelta(hours=3))

    stage = _only(s, project)

    assert stage["headline"] == "명세를 쓸 차례를 기다려요."
    assert "하루 사용량 상한에 닿아" in stage["detail"] and stage["detail"].endswith("까지 기다려요.")


def test_a_spec_that_could_not_be_written_offers_a_retry(s, project):
    req = _approved_development(s, project)
    job = _write_spec_job(s, project, req, status="failed", last_error="AgentFailed: product/spec: timeout\nTraceback")

    stage = _only(s, project)

    assert (stage["tone"], stage["retry_job"]) == ("bad", job.id)
    assert stage["detail"] == "AgentFailed: product/spec: timeout"


def _spec(s, project, approved=True, request=None) -> Spec:
    h = Handoff(spec_id="SPEC-B1", project="Test", title="t", source={"path": "specs/b", "commit_sha": "abc"},
                target={"repository": f"board-{project.id}"},
                approval={"spec_approved": approved, "implementation_authorized": approved})
    spec = Spec(spec_key=f"BOARD-{project.id}", project_id=project.id, title="원두 재고", registry_path="specs/b",
                approved_commit_sha="abc" if approved else None, handoff=h.model_dump(mode="json"),
                approval_request_id=request.id if request else None, status="READY_FOR_DEV" if approved else "IN_REVIEW")
    s.add(spec)
    s.flush()
    return spec


def test_a_written_spec_waits_for_the_owner_with_a_link_to_the_approval(s, project):
    ask = AgentRequest(project_id=project.id, from_agent="product", type="APPROVAL", title="명세 승인", summary="s",
                       rationale="r", status="PENDING", policy_mode="approve", action_kind="dev.spec_approve")
    s.add(ask)
    s.flush()
    _spec(s, project, approved=False, request=ask)

    stage = _only(s, project)

    assert (stage["tone"], stage["headline"]) == ("warn", "명세 승인을 기다려요.")
    assert stage["link"] == {"label": "명세 보고 승인하기", "to": f"/inbox?req={ask.id}"}


def _job(s, spec, status_, **kw) -> DevJob:
    job = DevJob(spec_id=spec.id, status=status_, **{"attempt": 1, **kw})
    s.add(job)
    s.flush()
    return job


def test_a_running_job_reads_as_its_last_owner_line(s, project):
    job = _job(s, _spec(s, project), "IMPLEMENTING", steps=[
        {"at": "2026-11-04T01:00:00+00:00", "status": "IMPLEMENTING", "summary": "I built the coffee bean stock MVP",
         "say": "기능을 만들기 시작했어요."}])

    stage = _only(s, project)

    assert (stage["tone"], stage["headline"]) == ("run", "개발 중이에요. 기능을 만들기 시작했어요.")
    assert stage["dev_job_id"] == job.id and "coffee" not in str(stage)


def test_a_job_that_stopped_while_shipping_says_the_code_is_done_and_offers_delivery(s, project):
    import subprocess

    from justshipit.settings import settings

    repo = settings().products_dir / f"board-{project.id}"
    repo.mkdir(parents=True)
    for args in (["init", "-q", "-b", "main"], ["-c", "user.name=e", "-c", "user.email=e@x", "commit", "-q",
                                                "--allow-empty", "-m", "feat"], ["branch", "ai/board-a1"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    reviewed = subprocess.run(["git", "rev-parse", "ai/board-a1"], cwd=repo, check=True, capture_output=True,
                              text=True).stdout.strip()
    _job(s, _spec(s, project), "FAILED", branch="ai/board-a1", error="호스트 작업 실패: git add: ignored",
         steps=[{"at": "t", "status": "REVIEWING", "summary": "리뷰 통과",
                 "data": {"verdict": "pass", "blocking": 0, "coverage": [], "commit": reviewed}},
                {"at": "t", "status": "SHIPPING", "summary": "guarded ship"},
                {"at": "t", "status": "FAILED", "summary": "호스트 작업 실패"}])

    stage = _only(s, project)

    assert stage["headline"] == "코드는 다 만들었어요(테스트·리뷰 통과). 전달하다 막혔어요."
    assert stage["actions"] == ["deliver_again"] and stage["retry_dev"]


def test_a_job_the_budget_stopped_links_to_raising_it(s, project):
    project.budget_usd, project.spent_usd = 40.0, 37.97
    _job(s, _spec(s, project), "FAILED", error=f"{msg('build.budget.stop', 'ko')} 예산 $40 중 $37.97 사용.",
         steps=[{"at": "t", "status": "INTAKE", "summary": "reading approved spec"}])

    stage = _only(s, project)

    assert stage["headline"] == msg("build.budget.stop", "ko") and stage["detail"].startswith("예산 $40 중 $37.97 사용")
    assert stage["link"] == {"label": "예산 올리기", "to": f"/projects/{project.id}/settings#budget"}
    assert stage["retry_dev"]


@pytest.mark.parametrize("job_status, error, shown", [
    (None, None, "READY_FOR_DEV"),
    ("QUEUED", None, "READY_FOR_DEV"),
    ("IMPLEMENTING", None, "IN_DEVELOPMENT"),
    ("FAILED", "예산이 모자라 개발을 멈췄어요. 예산 $40 중 $38.11 사용.", "BUDGET_STOPPED"),
    ("FAILED", "engineer step failed: project budget 40.0 USD-eq would be exceeded", "BUDGET_STOPPED"),
    ("FAILED", "호스트 작업 실패: x", "FAILED"),
    ("DELIVERED", None, "DELIVERED"),
])
def test_an_approved_specs_status_is_its_latest_jobs(s, project, job_status, error, shown):
    """견적봇's BUSINE2-0001 read '승인됨 · 개발 대기' while its job J-3 was stopped for budget."""
    spec = _spec(s, project)
    if job_status:
        _job(s, spec, "FAILED", error="older attempt")
        _job(s, spec, job_status, error=error, attempt=2)

    assert status.spec_state(s, spec) == shown


@pytest.mark.parametrize("asked, shown", [("PENDING", True), ("REJECTED", False)])
def test_a_rejected_spec_leaves_the_board(s, project, asked, shown):
    ask = AgentRequest(project_id=project.id, from_agent="product", type="APPROVAL", title="명세 승인", summary="s",
                       rationale="r", status=asked, policy_mode="approve", action_kind="dev.spec_approve")
    s.add(ask)
    s.flush()
    _spec(s, project, approved=False, request=ask)

    assert len(status.board(s, project.id, lang="ko")) == int(shown)


def _ask(s, project, status_, spec_id=None) -> AgentRequest:
    ask = AgentRequest(project_id=project.id, from_agent="product", type="APPROVAL", title="명세 승인", summary="s",
                       rationale="r", status=status_, policy_mode="approve", action_kind="dev.spec_approve",
                       action_params={"spec_id": spec_id} if spec_id else {})
    s.add(ask)
    s.flush()
    return ask


def _draft(s, project, key, ask=None) -> Spec:
    spec = _spec(s, project, approved=False, request=ask)
    spec.spec_key = key
    s.flush()
    return spec


def test_only_a_spec_with_its_own_live_card_waits_for_approval(s, project):
    """견적봇 listed seven '명세 승인을 기다려요' cards while REQ-50..52 alone were pending: BUSINE2-0005..0008 pointed
    at REQ-50, which decides 0004, and 0002/0003 at REQ-42, which approved 0001."""
    first = _draft(s, project, f"OWN-{project.id}-1")
    card = _ask(s, project, "PENDING", spec_id=first.id)
    first.approval_request_id = card.id
    riders = [_draft(s, project, f"OWN-{project.id}-{n}", card) for n in (2, 3)]
    s.flush()

    waiting = [st["ref"] for st in status.board(s, project.id, lang="ko") if st["headline"] == "명세 승인을 기다려요."]
    shown = {sp.spec_key: status.spec_state(s, sp) for sp in (first, *riders)}

    assert waiting == [first.spec_key]
    assert shown == {first.spec_key: "IN_REVIEW", **{r.spec_key: "SUPERSEDED" for r in riders}}


def test_a_spec_whose_card_was_rejected_reads_rejected(s, project):
    """원두재고 BUSINE-0002 read 검토 중 after REQ-43 was 반려."""
    spec = _draft(s, project, f"REJ-{project.id}")
    spec.approval_request_id = _ask(s, project, "REJECTED", spec_id=spec.id).id
    s.flush()

    assert status.spec_state(s, spec) == "REJECTED" and status.board(s, project.id, lang="ko") == []


def test_a_spec_whose_card_was_approved_is_being_started_not_deferred(s, project):
    spec = _draft(s, project, f"GO-{project.id}")
    spec.approval_request_id = _ask(s, project, "APPROVED", spec_id=spec.id).id
    s.flush()

    assert _only(s, project)["headline"] == "개발을 시작할 준비를 하고 있어요."


def test_a_project_with_a_stopped_development_names_it_and_not_an_older_attempt(s, project):
    """The bet card said 'P-2 완료' while J-3 was stopped: a run DONE with a failed latest dev job is not finished."""
    stopped = _draft(s, project, f"STOP-{project.id}")
    retried = _spec(s, project)
    job = _job(s, stopped, "FAILED")
    _job(s, retried, "FAILED")
    s.add(DevJob(spec_id=retried.id, status="IMPLEMENTING", attempt=2))
    s.flush()

    dev = status.by_project(s, [project.id])[project.id]
    assert (dev["dev_failed"], status.project_stage("DONE", dev)) == ([job.id], "DEV_STOPPED")


def test_the_board_of_one_business_shows_that_business_only(s, project):
    _approved_development(s, project)

    assert len(status.board(s, business_id=project.business_id, lang="ko")) == 1
    assert status.board(s, business_id=project.business_id + 1000, lang="ko") == []


def test_a_finished_project_whose_development_failed_says_so_in_its_summary(s, project):
    """c3's project overview showed a green 완료 while J-1 had failed."""
    from justshipit.api import app
    project.status = "DONE"
    spec = _spec(s, project)
    _job(s, spec, "FAILED")
    failed = DevJob(spec_id=spec.id, status="FAILED", attempt=2)
    s.add(failed)
    s.flush()

    [mine] = [p for p in app._project_summaries(s) if p["id"] == project.id]

    assert (mine["dev_failed"], mine["dev_active"], mine["dev_hold_usd"]) == ([failed.id], 0, 0.0)


@pytest.mark.parametrize("stored", [
    "engineer step failed: project budget 40.0 USD-eq would be exceeded",
    "예산 초과: project budget 40.0 USD-eq would be exceeded (12 USD-eq of it is kept for development)",
    "independent code review failed: project budget 40.0 USD-eq would be exceeded",
])
def test_a_job_an_older_build_stopped_on_budget_reads_as_a_budget_stop(s, project, stored):
    """견적봇's J-3 failed before the owner's sentence existed: its stored error is the English refusal, and the board
    offered only '처음부터 다시 구현'. It reads as the budget stop it was, with what development lacks now."""
    project.budget_usd, project.spent_usd = 40.0, 38.11
    _job(s, _spec(s, project), "FAILED", error=stored, steps=[{"at": "t", "status": "IMPLEMENTING", "summary": "x"}])

    stage = _only(s, project)

    assert stage["headline"] == msg("build.budget.stop", "ko")
    assert stage["detail"].startswith("예산 $40 중 $38.11 사용") and "더 필요해요" in stage["detail"]
    assert stage["link"] == {"label": "예산 올리기", "to": f"/projects/{project.id}/settings#budget"}


def test_a_monthly_budget_stop_of_an_older_build_names_the_month(s, project):
    _job(s, _spec(s, project), "FAILED", error="engineer step failed: monthly budget 300.0 USD-eq would be exceeded")

    stage = _only(s, project)

    assert stage["headline"] == msg("build.budget.stop", "ko") and stage["detail"].startswith("회사 전체 월 한도를 이번 달에 다 썼어요.")


@pytest.mark.parametrize("error, steps, kind", [
    ("engineer step failed: project budget 40.0 USD-eq would be exceeded", [], "budget"),
    ("호스트 작업 실패: git add: ignored", [{"at": "t", "status": "SHIPPING", "summary": "ship"}], "deliver"),
    ("에이전트 실패: engineer/dev: timeout", [{"at": "t", "status": "IMPLEMENTING", "summary": "x"}], "rebuild"),
])
def test_the_project_summary_names_the_way_on_for_a_stopped_development(s, project, error, steps, kind):
    """The overview said '개발 탭에서 다시 시도할 수 있어요' whatever stopped it, and 원두재고's sentence offered a new
    branch while its card offered 전달만 다시. The summary names the stop the card acts on."""
    from justshipit.api import app
    project.status, project.budget_usd, project.spent_usd = "DONE", 40.0, 38.11
    job = _job(s, _spec(s, project), "FAILED", error=error, branch="ai/b-a1", steps=steps)

    [mine] = [p for p in app._project_summaries(s) if p["id"] == project.id]

    assert mine["dev_stop"]["kind"] == kind and mine["dev_stop"]["jobs"] == [job.id]
    assert (mine["dev_stop"]["short_usd"] or 0) > 0 if kind == "budget" else mine["dev_stop"]["short_usd"] is None


def _worker_job(s, job, worker, **kw) -> Job:
    payload = {"dev_job_id": job.id}
    kw = {"attempts": 1, "max_attempts": 1, **kw}
    w = Job(gate="direct", kind="dev_job", payload=payload, lock_key=queue._lock_key("dev_job", payload), status="running",
            locked_by=worker, lease_token="t", **kw)
    s.add(w)
    s.flush()
    return w


def test_a_build_whose_worker_died_says_so_calmly_and_when_it_can_be_rebuilt(s, project):
    """J-9's worker was restarted mid-build. The board showed '워커 작업 #576이 끝나지 못해서… 워커 worker-pc:21024:eca3b8에서
    600초 넘게 응답이 없어… (시도 1/1)' and an enabled '처음부터 다시 구현' that the server refused until 19:26. A build now
    carries on after a restart (test_build_survive.py); this one's worker died on its last try."""
    job = _job(s, _spec(s, project), "IMPLEMENTING", steps=[{"at": "t", "status": "IMPLEMENTING", "summary": "x"}])
    worker = "worker-pc:21024:eca3b8"
    w = _worker_job(s, job, worker, locked_at=datetime.now(timezone.utc) - timedelta(hours=1), attempts=3,
                    max_attempts=3)
    queue.reclaim(s, {})
    s.refresh(job)
    assert job.status == "FAILED" and worker in job.error  # the raw reason stays for engineers

    stage = _only(s, project)

    said = f"{stage['headline']} {stage['detail']} {stage['retry_wait']}"
    assert stage["headline"] == "개발이 멈췄어요."
    assert not any(raw in said for raw in (worker, f"#{w.id}", "시도", "초 넘게", "BOARD-"))
    held = s.get(Job, w.id).held_until
    assert stage["retry_wait"] == f"{status.at(held, 'ko')} 이후에 처음부터 다시 만들 수 있어요."
    assert stage["detail"].endswith(stage["retry_wait"])

    reclaimed = s.get(Job, w.id)  # an hour on: the hold is over
    reclaimed.held_until -= timedelta(hours=1)
    reclaimed.finished_at -= timedelta(hours=1)
    s.flush()
    stage = _only(s, project)

    assert stage["retry_dev"] and stage["retry_wait"] is None
    assert stage["detail"] == msg("build.stage.worker_lost", "ko") and worker not in stage["detail"]


def test_a_build_whose_worker_stopped_answering_does_not_read_as_progressing(s, project):
    """After the worker restart the board said '계획한 작업 13개 중 2개를 마쳤어요' for 11 minutes: the build's last progress
    stood until the queue took the job back, ten minutes after the last heartbeat."""
    job = _job(s, _spec(s, project), "IMPLEMENTING", steps=[{"at": "t", "status": "IMPLEMENTING", "summary": "x"}],
               result={"progress": {"status": "IMPLEMENTING", "step": 1, "done": 2, "total": 13}})
    beat = datetime.now(timezone.utc) - timedelta(minutes=5)
    _worker_job(s, job, "worker-pc:21024:eca3b8", locked_at=beat)

    stage = _only(s, project)

    assert stage["tone"] == "wait" and "13개 중 2개" not in stage["headline"]
    assert stage["headline"] == "개발하던 백그라운드 프로세스가 5분째 응답이 없어요."
    assert stage["detail"] == (f"{status.at(beat + timedelta(seconds=queue.lease_s()), 'ko')}까지 응답이 없으면 "
                               "멈춘 것으로 봐요.")

    s.get(Job, s.scalar(select(Job.id).where(Job.lock_key == f"dev_job:{job.id}"))).locked_at = \
        datetime.now(timezone.utc)
    s.flush()
    assert _only(s, project)["headline"] == "개발 중이에요. 계획한 작업 13개 중 2개를 마쳤어요."


def test_a_build_whose_worker_died_alerts_the_owner_calmly(s, project):
    """The Decisions alert for that build carried the same reclaim text as its detail."""
    from justshipit.api import console
    job = _job(s, _spec(s, project), "IMPLEMENTING", steps=[{"at": "t", "status": "IMPLEMENTING", "summary": "x"}])
    worker = "worker-pc:21024:eca3b8"
    _worker_job(s, job, worker, locked_at=datetime.now(timezone.utc) - timedelta(hours=1), attempts=3, max_attempts=3)
    queue.reclaim(s, {})  # uncommitted: a failed build or run left behind would show on other files' lists

    [alert] = [a for a in console._alerts(s, set(), None, lang="ko") if a.get("dev_job_id") == job.id]

    assert alert["detail"] == msg("build.stage.worker_lost", "ko")
    assert alert["retry_wait"] == _only(s, project)["retry_wait"] is not None  # its 다시 시도 would be refused till then


@pytest.mark.parametrize("max_attempts", [1, 3])
def test_a_run_whose_worker_died_reads_calmly_everywhere_the_owner_looks(s, project, max_attempts):
    """After the worker restart the alerts read '워커 worker-pc:21024:eca3b8에서 600초 넘게 응답이 없어 작업을 회수했어요
    (시도 1/1)…', and a run given up on wrote '워커 작업 #576이 끝나지 못해서…' as the project's error."""
    from justshipit.api import console
    worker = "worker-pc:21024:eca3b8"
    project.status = "RUNNING"
    payload = {"project_id": project.id}
    w = Job(gate="direct", kind="run_project", payload=payload, lock_key=queue._lock_key("run_project", payload), status="running",
            locked_by=worker, lease_token="t", attempts=1, max_attempts=max_attempts,
            locked_at=datetime.now(timezone.utc) - timedelta(hours=1))
    s.add(w)
    s.flush()
    queue.reclaim(s, {})  # uncommitted, as above

    said = " ".join(f"{a.get('detail') or ''} {a.get('reason') or ''}"
                    for a in console._alerts(s, set(), None, lang="ko") if a.get("job_id") == w.id)
    s.refresh(project)
    said += f" {project.error or ''}"

    assert not any(raw in said for raw in (worker, f"#{w.id}", "시도 1/", "초 넘게")), said
    assert worker in s.get(Job, w.id).last_error  # the raw reason stays for engineers


# ------------------------------------------------------------------ Junho round 7 (P-10, J-10): what the board says is true

def _acceptance_run(s, job, status_="running") -> Job:
    payload = {"dev_job_id": job.id}
    run = Job(gate="direct", kind="acceptance", payload=payload, lock_key=f"acceptance:{job.id}", status=status_)
    s.add(run)
    s.flush()
    return run


def test_a_project_whose_build_is_being_checked_does_not_wait_on_the_owner_yet(s, project):
    """At 00:07 the product agent was still checking the acceptance criteria and no merge card existed, yet the header
    read '머지할지 고칠지 오너님 결정을 기다려요'. It waits on the owner once the card is there."""
    from justshipit.api import app
    from justshipit.engineering import worker
    project.status = "DONE"
    job = _job(s, _spec(s, project), "DELIVERED", branch="ai/b-a1", steps=[
        {"at": "2026-11-05T15:07:00+00:00", "status": "DELIVERED", "summary": "local branch",
         "say": {"key": "build.step.delivered"}}])
    run = _acceptance_run(s, job)

    def stage() -> str:
        [mine] = [p for p in app._project_summaries(s) if p["id"] == project.id]
        return status.project_stage(mine["status"], mine)

    assert stage() == "DEV_ACTIVE"
    assert _only(s, project)["headline"] == msg("build.stage.accepting", "ko")

    run.status = "done"
    worker._request_merge(s, job, s.get(Spec, job.spec_id), "머지 승인: 원두 재고", "판단", "ko")
    s.flush()
    assert stage() == "DEV_DELIVERED"


STEPS_AS_STORED = [
    {"at": "2026-11-05T14:07:00+00:00", "status": "INTAKE", "summary": "reading approved spec",
     "say": {"key": "build.step.intake_start"}},
    {"at": "2026-11-05T14:08:00+00:00", "status": "INTAKE",
     "summary": "T-110부터 만들고 US1.3 시나리오를 먼저 확인해요. 커밋 4a85a56c8b967938ac5df5c864bfaea055769501 기준.",
     "data": {"assumptions": ["T-111은 나중에 해도 돼요", "CP949 파일도 받아요"]}, "say": {"key": "build.step.intake_done"}},
    {"at": "2026-11-05T14:08:30+00:00", "status": "PLAN_REVIEW", "summary": "pstack:architect",
     "say": {"key": "build.step.plan_start"}},
    {"at": "2026-11-05T14:15:00+00:00", "status": "PLAN_REVIEW", "summary": "/pstack:architect 로 계획을 정했어요.",
     "data": {"decisions": ["US1 먼저", "pstack:tdd 로 테스트부터"]}, "say": {"key": "build.step.plan_done", "vars": {"n": 2}}},
    {"at": "2026-11-05T14:15:30+00:00", "status": "IMPLEMENTING", "summary": "implementing",
     "say": {"key": "build.step.build_start"}},
    {"at": "2026-11-05T14:22:00+00:00", "status": "IMPLEMENTING",
     "summary": "picked up at IMPLEMENTING: the worker that ran it stopped", "say": {"key": "build.step.resumed"}},
    {"at": "2026-11-05T14:58:00+00:00", "status": "QA", "summary": "tests: True",
     "data": {"log": "PASS src/rules.test.ts\nTests: 12 passed"}, "say": {"key": "build.step.tests_passed"}},
]


@pytest.mark.parametrize("lang", ["ko", "en"])
def test_a_steps_details_are_the_owners_words_without_skill_names_or_codes(s, project, lang):
    """With 자세히 open on a Korean console the log read 'picked up at IMPLEMENTING: the worker that ran it stopped',
    'implementing', 'pstack:architect' and T-110/T-111/US1.3. The worker's own records stay in the stored steps."""
    job = _job(s, _spec(s, project), "QA", steps=STEPS_AS_STORED)

    shown = status.steps(job, lang)

    text = repr(shown)
    for raw in ("picked up", "IMPLEMENTING:", "'implementing'", "reading approved spec", "pstack", "T-110", "T-111",
                "US1", "4a85a56", "tests: True", "PASS src", "'assumptions'", "'decisions'", "'log'"):
        assert raw not in text, (raw, shown)
    intake, plan = shown[1], shown[3]
    assert "만들고" in intake["detail"] and "시나리오를 먼저 확인해요" in intake["detail"]
    [assumed] = intake["lists"]
    assert assumed["label"] == msg("build.step.list.assumptions", lang) and len(assumed["items"]) == 2
    assert plan["lists"][0]["label"] == msg("build.step.list.decisions", lang)
    assert shown[2]["detail"] is None and shown[2]["lists"] == [], "a skill's name is no account"
    assert job.steps[0]["summary"] == "reading approved spec", "the engineers' record stays as it was written"


def test_a_running_builds_time_is_its_latest_progress_not_its_last_step(s, project):
    """'15개 중 14개를 마쳤어요 · 33분 전' while the build was still running: the time was its last step's."""
    noted = datetime.now(timezone.utc) - timedelta(minutes=2)
    job = _job(s, _spec(s, project), "IMPLEMENTING", steps=[
        {"at": "2026-11-05T14:15:30+00:00", "status": "IMPLEMENTING", "summary": "implementing",
         "say": {"key": "build.step.build_start"}}])
    job.result = {"progress": {"status": "IMPLEMENTING", "step": 1, "done": 14, "total": 15, "at": noted.isoformat()}}
    s.flush()

    stage = _only(s, project)

    assert stage["headline"] == "개발 중이에요. 계획한 작업 15개 중 14개를 마쳤어요."
    assert stage["since"] == noted.isoformat()


def _team_build(s, project) -> DevJob:
    import uuid

    from justshipit.db.models import LLMCallRecord
    spec = Spec(spec_key=f"SPEC-team-{uuid.uuid4().hex[:8]}", project_id=project.id, title="uxscope",
                registry_path="specs/t", handoff={"target": {"repository": "uxscope"}})
    s.add(spec)
    s.flush()
    job = DevJob(spec_id=spec.id, status="DELIVERED", branch="ai/team-a1", result={}, steps=[
        {"at": "2026-11-01T00:00:00+00:00", "status": "IMPLEMENTING", "summary": "built", "say": {"key": "build.step.build_done", "vars": {"files": 2, "tests": 1}}},
        {"at": "2026-11-01T01:00:00+00:00", "status": "REVIEWING", "summary": "ok"},
        {"at": "2026-11-01T02:00:00+00:00", "status": "DELIVERED", "summary": "CPO verdict: accepted",
         "say": {"key": "build.step.accepted"}},
        {"at": "2026-11-01T03:00:00+00:00", "status": "DELIVERED", "summary": "x", "by": "owner"},
    ])
    s.add(job)
    s.flush()
    for i, (agent, purpose, model, ok) in enumerate([
            ("engineer", "dev", "claude-opus-4", "ok"), ("engineer", "dev", "claude-opus-4", "ok"),
            ("eng_reviewer", "review", "claude-sonnet-4", "ok"), ("product", "spec", "claude-sonnet-4", "ok"),
            ("product", "acceptance", "claude-sonnet-4", "ok"), ("engineer", "chat", "claude-sonnet-4", "ok"),
            ("eng_reviewer", "review", "claude-haiku", "error")]):
        s.add(LLMCallRecord(call_id=f"team-{job.id}-{i}", project_id=project.id, dev_job_id=job.id if purpose != "chat" else None,
                            agent=agent, purpose=purpose, provider="claude", model=model, status=ok))
    s.flush()
    return job


def test_the_team_of_a_build_is_read_from_its_model_calls(s, project):
    from justshipit.engineering import team
    job = _team_build(s, project)

    assert team.of_build(s, job.id) == {"built": [{"agent": "engineer", "model": "claude-opus-4"}],
                                        "reviewed": [{"agent": "eng_reviewer", "model": "claude-sonnet-4"}],
                                        "checked": [{"agent": "product", "model": "claude-sonnet-4"}]}


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_each_step_names_who_did_it_and_an_old_step_reads_its_status(s, project, lang):
    job = _team_build(s, project)

    assert [x["by"] for x in status.steps(job, lang)] == ["engineer", "eng_reviewer", "product", "owner"]


def test_the_merge_card_carries_the_team(s, project):
    from justshipit.services import requests
    job = _team_build(s, project)
    req = AgentRequest(project_id=project.id, from_agent="product", type="APPROVAL", title="Merge", summary="s",
                       rationale="r", action_kind="dev.merge", action_params={"dev_job_id": job.id}, policy_mode="strong")
    s.add(req)
    s.flush()

    card = requests._merge_card(s, req)

    assert [x["agent"] for x in card["team"]["built"] + card["team"]["reviewed"] + card["team"]["checked"]] == [
        "engineer", "eng_reviewer", "product"]


def test_a_new_step_says_who_did_it(s, project):
    from justshipit.contracts.handoff import DevJobStatus
    from justshipit.engineering import worker
    job = _team_build(s, project)
    job.status = "QA"

    worker._move(s, job, DevJobStatus.REVIEWING, "review")
    worker._move(s, job, None, "the owner asked", by="owner")

    assert [x.get("by") for x in job.steps[-2:]] == ["eng_reviewer", "owner"]
