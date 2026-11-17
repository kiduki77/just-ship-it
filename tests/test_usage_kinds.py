"""Which jobs wait for usage and which fail with a message, and that a deferred rerun repeats nothing."""
from datetime import datetime, timedelta, timezone

import pytest
from conftest import store_usage
from justshipit.agents.executor import AgentFailed
from justshipit.contracts.handoff import IntakeOut
from justshipit.db.models import AppSetting, DevJob, Evidence, Job, Skill, Spec
from justshipit.db.session import session_scope
from justshipit.services import app_settings
from justshipit.services.usage import MustWait
from sqlalchemy import delete
from test_engineering import _acceptance_queued, _execute_claimed, _queued_job


def wait() -> MustWait:
    return MustWait(datetime.now(timezone.utc) + timedelta(hours=9), "usage_cap_day", "하루 사용 상한")


@pytest.fixture
def tiny_day_cap():
    store_usage(day_usd_eq=0.01)
    yield
    with session_scope(lease_checked=False) as s:
        s.execute(delete(AppSetting))


def test_an_acceptance_that_must_wait_is_deferred_and_the_dev_job_is_left_alone(s, project, monkeypatch):
    job, qjob = _acceptance_queued(s, project, monkeypatch, [wait(), None])

    _execute_claimed(s, qjob.id, {"dev_job_id": job.id})

    waiting = s.get(Job, qjob.id)
    assert (waiting.status, waiting.attempts, waiting.wait_reason) == ("queued", 0, "usage_cap_day")
    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED" and done.error is None


def test_a_dev_job_is_deferred_before_it_starts_while_a_cap_is_over(s, project, monkeypatch, tiny_day_cap):
    from justshipit.engineering import worker
    job = _queued_job(s, project)
    monkeypatch.setattr(worker, "run_agent", lambda *a, **kw: pytest.fail("a model was called"))

    with pytest.raises(MustWait) as wait_for:
        worker.run_dev_job(job.id)

    assert wait_for.value.reason == "usage_cap_day"
    s.expire_all()
    started = s.get(DevJob, job.id)
    assert started.status == "QUEUED" and started.error is None, "a deferred job must still be startable"


def test_a_dev_job_resumed_after_an_answer_waits_before_it_writes_the_answers(s, project, monkeypatch, tiny_day_cap):
    from justshipit.engineering import worker
    job = _queued_job(s, project)
    monkeypatch.setattr(worker.registry, "write_files", lambda *a, **kw: pytest.fail("answers written twice"))

    with pytest.raises(MustWait):
        worker.resume_after_answer(job.id)


def test_a_dev_job_that_runs_out_of_usage_mid_way_waits_at_its_step_instead_of_failing(
        s, project, tmp_path, monkeypatch):
    """It carries on from that step once the limit resets (test_work_survives)."""
    from justshipit.engineering import worker
    from justshipit.sandbox import docker

    job = _queued_job(s, project)
    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    monkeypatch.setattr(docker, "image_present", lambda image: True)
    monkeypatch.setattr(worker, "host_git_refusal", lambda lang: None)
    monkeypatch.setattr(worker, "prepare_worktree", lambda spec, h, attempt: (tmp_path, tmp_path, "ai/t", None))
    monkeypatch.setattr(worker.registry, "read_at", lambda *a: "# PRD")

    def model(agent_id, purpose, prompt, out, **kw):
        if agent_id == "product":
            raise wait()
        return IntakeOut(clear_enough=False, blocking_questions=["누가 결제하나요?"], assumptions_safe_to_make=[],
                         implementation_outline="")
    monkeypatch.setattr(worker, "run_agent", model)

    with pytest.raises(MustWait):
        worker.run_dev_job(job.id)

    s.expire_all()
    waiting = s.get(DevJob, job.id)
    assert (waiting.status, waiting.error) == ("CLARIFICATION_REQUIRED", None)
    assert worker.paused(waiting)


def _dev_run_that_reaches_intake(s, project, tmp_path, monkeypatch):
    from justshipit.engineering import worker
    from justshipit.sandbox import docker

    job = _queued_job(s, project)
    spec = s.get(Spec, job.spec_id)
    spec.status = "READY_FOR_DEV"
    s.commit()
    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    monkeypatch.setattr(docker, "image_present", lambda image: True)
    monkeypatch.setattr(worker, "host_git_refusal", lambda lang: None)
    monkeypatch.setattr(worker, "prepare_worktree", lambda spec, h, attempt: (tmp_path, tmp_path, "ai/t", None))
    monkeypatch.setattr(worker, "preflight", lambda *a: None)
    return worker, job


def test_a_dev_job_that_waits_at_intake_is_deferred_and_starts_again_not_failed(s, project, tmp_path, monkeypatch):
    worker, job = _dev_run_that_reaches_intake(s, project, tmp_path, monkeypatch)
    answers = iter([wait(), IntakeOut(clear_enough=True, blocking_questions=[], assumptions_safe_to_make=[],
                                      implementation_outline="o"), wait()])

    def model(agent_id, purpose, prompt, out, **kw):
        step = next(answers)
        if isinstance(step, MustWait):
            raise step
        return step
    monkeypatch.setattr(worker, "run_agent", model)

    with pytest.raises(MustWait):
        worker.run_dev_job(job.id)

    s.expire_all()
    waiting = s.get(DevJob, job.id)
    assert waiting.status == "QUEUED" and waiting.error is None, "a wait at intake must leave the job startable"
    assert s.get(Spec, waiting.spec_id).status == "READY_FOR_DEV"

    with pytest.raises(MustWait):
        worker.run_dev_job(job.id)

    s.expire_all()
    later = s.get(DevJob, job.id)
    assert later.status not in ("QUEUED", "FAILED") and later.error is None, "a wait after intake waits at its step"
    assert worker.paused(later)


def test_a_dev_job_resumed_after_an_answer_returns_to_its_clarification_when_intake_waits(
        s, project, tmp_path, monkeypatch):
    worker, job = _dev_run_that_reaches_intake(s, project, tmp_path, monkeypatch)
    job.status = "CLARIFICATION_REQUIRED"
    s.commit()
    monkeypatch.setattr(worker, "run_agent", lambda *a, **kw: (_ for _ in ()).throw(wait()))

    with pytest.raises(MustWait):
        worker.run_dev_job(job.id)

    s.expire_all()
    assert s.get(DevJob, job.id).status == "CLARIFICATION_REQUIRED"


def test_answers_to_a_dev_jobs_questions_are_appended_once_however_often_the_job_resumes(
        s, project, tmp_path, monkeypatch):
    from justshipit.contracts.requests import RequestType
    from justshipit.db.models import AgentRequest

    worker, job = _dev_run_that_reaches_intake(s, project, tmp_path, monkeypatch)
    s.add(AgentRequest(project_id=project.id, from_agent="product", type=RequestType.QUESTION.value, title="q",
                       summary="누가 결제하나요?", rationale="r", action_params={"dev_job_id": job.id},
                       policy_mode="auto", status="APPROVED", response={"answer": "구매자"}))
    s.commit()
    files = {}
    monkeypatch.setattr(worker.registry, "root", lambda: tmp_path)
    monkeypatch.setattr(worker.registry, "read", lambda path: files.get(path, ""))
    monkeypatch.setattr(worker.registry, "write_files", lambda f, msg: files.update(f))
    monkeypatch.setattr(worker, "run_dev_job", lambda job_id: None)
    clarifications = f"{s.get(Spec, job.spec_id).registry_path}/clarifications.md"

    worker.resume_after_answer(job.id)
    (tmp_path / clarifications).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / clarifications).write_text(files[clarifications], encoding="utf-8")
    worker.resume_after_answer(job.id)

    assert files[clarifications].count("A (Owner): 구매자") == 1


def test_a_watch_that_waits_and_reruns_notes_the_lost_source_once(s, project, monkeypatch):
    from justshipit.operations import jobs
    ev = Evidence(project_id=project.id, agent="research", claim="c", kind="verified_fact",
                  source_url="https://example.com/a", quote="q", verification_status="url_ok")
    s.add(ev)
    s.commit()
    monkeypatch.setattr(jobs.evidence, "_check", lambda url, quote: ("url_unreachable", "404"))
    monkeypatch.setattr(jobs, "run_agent", lambda *a, **kw: (_ for _ in ()).throw(wait()))

    for _ in range(2):
        with pytest.raises(MustWait):
            jobs.evidence_watch(project.id)

    s.expire_all()
    assert s.get(Evidence, ev.id).verification_note.count("Source recheck failed") == 1


def test_a_watch_rerun_on_another_day_does_not_note_the_same_lost_source_again(s, project, monkeypatch):
    from justshipit.operations import jobs
    ev = Evidence(project_id=project.id, agent="research", claim="c", kind="verified_fact",
                  source_url="https://example.com/b", quote="q", verification_status="url_ok",
                  verification_note="[watch 2020-01-01] 출처 재확인 실패: 404")
    s.add(ev)
    s.commit()
    monkeypatch.setattr(jobs.evidence, "_check", lambda url, quote: ("url_unreachable", "404"))
    monkeypatch.setattr(jobs, "run_agent", lambda *a, **kw: (_ for _ in ()).throw(wait()))

    with pytest.raises(MustWait):
        jobs.evidence_watch(project.id)

    s.expire_all()
    assert s.get(Evidence, ev.id).verification_note.count("출처 재확인 실패") == 1


def test_scheduled_reviews_count_as_background_so_they_wait_before_an_owner_answer(s, project, monkeypatch):
    from justshipit.operations import jobs
    from justshipit.services import usage
    seen = []

    def spy(agent, purpose, prompt, model, **kw):
        seen.append(purpose)
        raise wait()
    monkeypatch.setattr(jobs, "run_agent", spy)
    with pytest.raises(MustWait):
        jobs.portfolio_review()
    assert seen == ["scheduled"] and usage.purpose("scheduled").owner_facing is False


def test_other_agent_failures_in_a_skill_build_do_not_defer(s, monkeypatch):
    from justshipit.capability import factory

    skill = Skill(name="fail-skill", kind="prompt", status="CANDIDATE", owner_agent="skill_builder")
    s.add(skill)
    s.commit()

    class Portfolio:
        proposals = [object()]
    monkeypatch.setattr(factory, "gaps", lambda: [{"agent": "research", "gap": "g", "count": 3}])
    monkeypatch.setattr(factory, "run_agent", lambda *a, **kw: Portfolio())
    monkeypatch.setattr(factory, "_propose", lambda prop: skill.id)
    monkeypatch.setattr(factory, "build_and_evaluate", lambda sid: (_ for _ in ()).throw(AgentFailed("bad")))

    assert factory.run_factory() == [skill.id]
