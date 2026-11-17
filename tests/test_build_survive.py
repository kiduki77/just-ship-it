"""Junho round 6: at 18:31 the worker was restarted (the deploy kills its process tree) while J-9 was building, "13개 중
2개 완료". The build died, the queue marked it FAILED once its lease ran out ten minutes later, and the rebuild was
refused for another 46 minutes, the hold for a holder that might still be working. A build the queue takes back now
carries on from the step it was in, and a holder whose process is gone, with every container it started removed, is
not waited for."""
import os
from datetime import UTC, datetime, timedelta

import pytest
from justshipit.agents.independence import Served
from justshipit.contracts.handoff import CodeReviewOut, DevJobStatus, DevStepOut, Handoff, IntakeOut
from justshipit.db.models import DevJob, Job, Spec, Worker
from justshipit.db.session import session_scope
from justshipit.engineering import status
from justshipit.i18n import msg
from justshipit.services import queue
from justshipit.settings import HOST
from justshipit.workers import runner
from sqlalchemy import select, text


@pytest.fixture(autouse=True)
def _korean_owner(monkeypatch):
    from justshipit.services import app_settings
    monkeypatch.setattr(app_settings, "language", lambda: "ko")


@pytest.fixture(autouse=True)
def _isolated_jobs():
    """A claim takes the oldest runnable row, so nothing another file left queued or running may be one."""
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL, lease_token = NULL "
                       "WHERE status IN ('queued', 'running')"))
    yield
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL, lease_token = NULL, held_until = NULL "
                       "WHERE status IN ('queued', 'running') OR held_until > now()"))


IMPLEMENTING = [{"at": "t", "status": s, "summary": s, "say": {"key": k}} for s, k in (
    ("INTAKE", "build.step.intake_start"), ("INTAKE", "build.step.intake_done"), ("PLAN_REVIEW", "build.step.plan_start"),
    ("PLAN_REVIEW", "build.step.plan_done"), ("IMPLEMENTING", "build.step.build_start"))]


def _building(s, project, status_: str = "IMPLEMENTING", steps=None) -> DevJob:
    h = Handoff(spec_id="SPEC-B1", project="Test", title="출고 예외 리포트",
                source={"path": "specs/b1", "commit_sha": "abc123"}, target={"repository": "demo"},
                approval={"spec_approved": True, "implementation_authorized": True})
    spec = Spec(spec_key=f"SPEC-SURVIVE-{project.id}", project_id=project.id, title="출고 예외 리포트",
                registry_path="specs/b1", approved_commit_sha="abc123", handoff=h.model_dump(mode="json"),
                status="IN_DEVELOPMENT")
    s.add(spec)
    s.flush()
    job = DevJob(spec_id=spec.id, status=status_, branch="ai/spec-b1-a1", steps=steps or IMPLEMENTING[:5],
                 result={"progress": {"status": status_, "step": 5, "done": 2, "total": 13}})
    s.add(job)
    s.commit()
    return job


def _held_by(job: DevJob, worker: str, *, beat_ago: timedelta, attempts: int = 1, max_attempts: int = 1) -> int:
    """The build's worker job, claimed by `worker`, whose last heartbeat was `beat_ago`."""
    with session_scope() as s:
        payload = {"dev_job_id": job.id}
        w = Job(gate="direct", kind="dev_job", payload=payload, lock_key=queue._lock_key("dev_job", payload), status="running",
                locked_by=worker, lease_token="t", attempts=attempts, max_attempts=max_attempts,
                locked_at=datetime.now(UTC) - beat_ago)
        s.add(w)
        s.flush()
        return w.id


def _seen(worker: str, ago: timedelta) -> None:
    with session_scope() as s:
        s.merge(Worker(id=worker, seen_at=datetime.now(UTC) - ago))


def _row(job_id: int) -> Job:
    with session_scope() as s:
        j = s.get(Job, job_id)
        s.expunge(j)
        return j


def _dev(job_id: int) -> DevJob:
    with session_scope() as s:
        j = s.get(DevJob, job_id)
        s.expunge(j)
        return j


# ------------------------------------------------------------------ the queue gives a build back, not up

def test_a_build_the_queue_takes_back_is_queued_to_carry_on_not_failed(s, project):
    job = _building(s, project)
    jid = _held_by(job, "worker-pc:21024:eca3b8", beat_ago=timedelta(hours=1))

    with session_scope() as s2:
        queue.reclaim(s2, {})

    assert _row(jid).status == "queued"
    assert _dev(job.id).status == "IMPLEMENTING" and _dev(job.id).error is None


def test_under_host_isolation_a_build_the_queue_takes_back_gets_no_resume_tries(s, project, monkeypatch):
    """Integration review (round 7): on the host a build can't pick up its step, so the reclaim must not promise it
    one; it stops as before instead of saying it carries on."""
    monkeypatch.setattr(queue, "_builds_resume", lambda: False)
    job = _building(s, project)
    jid = _held_by(job, "worker-pc:21024:eca3b8", beat_ago=timedelta(hours=1))

    with session_scope() as s2:
        queue.reclaim(s2, {})

    assert _row(jid).status == "failed" and _row(jid).max_attempts == 1


def test_a_long_hostname_still_fits_the_worker_id_column():
    """Integration review (round 7): worker ids are String(80); the host part is capped so a long hostname can't stop
    a worker from claiming jobs."""
    from justshipit.settings import PROCESS_ID
    assert len(HOST) <= 47 and len(PROCESS_ID) <= 80


def test_a_build_whose_worker_keeps_dying_stops_after_its_resumes(s, project):
    job = _building(s, project)
    jid = _held_by(job, "worker-pc:21024:eca3b8", beat_ago=timedelta(hours=1), attempts=3, max_attempts=3)

    with session_scope() as s2:
        queue.reclaim(s2, {})

    assert _row(jid).status == "failed" and _dev(job.id).status == "FAILED"


def test_a_build_waiting_to_carry_on_says_so_calmly(s, project):
    job = _building(s, project)
    _held_by(job, "worker-pc:21024:eca3b8", beat_ago=timedelta(hours=1))
    with session_scope() as s2:
        queue.reclaim(s2, {})

    [stage] = status.board(s, project.id, lang="ko")

    assert stage["tone"] == "wait" and stage["headline"] == msg("build.stage.resuming", "ko")
    held = _row(s.scalar(select(Job.id).where(Job.lock_key == f"dev_job:{job.id}"))).run_after
    assert status.at(held, "ko") in stage["detail"] and "worker-pc" not in stage["detail"]
    assert not stage["retry_dev"] and stage["actions"] == []


# ------------------------------------------------------------------ a dead holder is not waited for

def _restart(monkeypatch, ran: list[int]) -> None:
    """The new worker's first pass, with the build's own run stubbed: it records the job it was given."""
    monkeypatch.setattr(runner, "_handlers", lambda: {"dev_job": lambda p: ran.append(p["dev_job_id"])})
    monkeypatch.setattr(runner, "MACHINE", "a1b2c3")  # a host that keeps a kernel id (the test container has none)
    runner.serve(concurrency=1, poll_s=0.01, once=True)


def test_a_worker_restart_carries_the_build_on_within_minutes_not_an_hour(s, project, dead_worker, docker_log,
                                                                           monkeypatch):
    """The old worker's process is gone and it has left no container, so its build is taken back at the new
    worker's first pass, long before the lease runs out, and runs again at once."""
    job = _building(s, project)
    jid = _held_by(job, dead_worker, beat_ago=timedelta(minutes=2))
    _seen(dead_worker, timedelta(minutes=3))
    ran: list[int] = []

    _restart(monkeypatch, ran)

    assert ran == [job.id]
    row = _row(jid)
    assert row.status == "done" and row.held_until is None and row.attempts == 2


ALIVE = f"{HOST}:{os.getpid()}:live00"
STUCK_CONTAINER = """#!/bin/sh
echo "$@" >> "$FAKE_DOCKER_LOG"
if [ "$1" = ps ]; then printf 'ship-eng-stuck\\t%s\\n' "$STUCK_OWNER"; fi
"""


@pytest.mark.parametrize("case", ["alive", "answered lately", "container stays", "other host", "same name, other kernel"])
def test_a_holder_that_may_still_be_working_is_waited_for(s, project, dead_worker, docker_log, monkeypatch, case):
    """A worker of another host, or of a WSL beside Windows that shares its hostname but not its pids, may be alive
    with a pid this host has never seen."""
    pid = dead_worker.split(":")[1]
    holder = {"alive": ALIVE, "other host": f"elsewhere:{pid}:dead00",
              "same name, other kernel": f"{HOST.split('.')[0]}.0ther0:{pid}:dead00"}.get(case, dead_worker)
    _seen(holder, timedelta(seconds=10) if case == "answered lately" else timedelta(minutes=3))
    if case == "container stays":
        fake = docker_log.parent / "bin" / "docker"
        fake.write_text(STUCK_CONTAINER)
        monkeypatch.setenv("STUCK_OWNER", holder)
    job = _building(s, project)
    jid = _held_by(job, holder, beat_ago=timedelta(minutes=2))
    ran: list[int] = []

    _restart(monkeypatch, ran)

    assert ran == [] and _row(jid).status == "running"


# ------------------------------------------------------------------ the build carries on from its step

@pytest.fixture
def engineer(monkeypatch, tmp_path):
    """The build's run with its ground stubbed; records each agent call's prompt."""
    from justshipit.engineering import worker
    from justshipit.sandbox import docker

    calls: list[tuple[str, str]] = []

    def fake_run_agent(agent_id, purpose, prompt, model, **kw):
        calls.append((agent_id, prompt))
        if model is IntakeOut:
            return IntakeOut(clear_enough=True, blocking_questions=[], assumptions_safe_to_make=[],
                             implementation_outline="outline")
        if model is CodeReviewOut:
            return CodeReviewOut(verdict="pass", summary="review ok", spec_coverage=[], findings=[])
        return DevStepOut(status="done", summary="done", files_changed=[], tests_run=[], decisions=[], blockers=[])

    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    monkeypatch.setattr(docker, "image_present", lambda image: True)
    monkeypatch.setattr(worker, "host_git_refusal", lambda lang: None)
    monkeypatch.setattr(worker, "run_agent", fake_run_agent)
    monkeypatch.setattr(worker, "last_served", lambda: Served("claude", "opus"))
    monkeypatch.setattr(worker, "prepare_worktree", lambda spec, h, attempt: (tmp_path, tmp_path, "ai/spec-b1-a1", None))
    monkeypatch.setattr(worker, "_write_spec", lambda wt, h, given: None)
    monkeypatch.setattr(worker, "run_tests", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(worker, "_git", lambda *a, **kw: "")
    shipped: list[str] = []
    monkeypatch.setattr(worker, "_deliver", lambda job_id, spec_id, repo, branch, reviewed, *a: shipped.append(reviewed))
    return calls, shipped


def test_a_build_taken_back_mid_implementation_carries_on_from_its_committed_tasks(s, project, engineer):
    from justshipit.engineering import worker

    calls, shipped = engineer
    job = _building(s, project)

    worker.run_dev_job(job.id)

    prompts = [p for agent, p in calls if agent == "engineer"]
    assert not any("judge whether it can be built" in p or p.startswith("Use the pstack:architect") for p in prompts)
    assert "stopped partway" in prompts[0] and "[task k/M]" in prompts[0]
    done = _dev(job.id)
    assert done.status == "SHIPPING" and shipped
    said = [x["say"]["key"] for x in done.steps if x.get("say")]
    assert said[5] == "build.progress.resumed" and "build.step.build_done" in said


@pytest.mark.parametrize("at", ["QA", "REVIEWING"])
def test_a_build_taken_back_in_its_checks_runs_them_again_without_building_again(s, project, engineer, at):
    from justshipit.engineering import worker

    calls, shipped = engineer
    job = _building(s, project, at, steps=[*IMPLEMENTING, {"at": "t", "status": at, "summary": at}])

    worker.run_dev_job(job.id)

    assert not any(p.startswith("Build the features") or "stopped partway" in p for agent, p in calls)
    assert any(agent == "eng_reviewer" for agent, _ in calls)
    assert _dev(job.id).status == "SHIPPING" and shipped


def test_a_build_taken_back_while_shipping_delivers_the_reviewed_commit(s, project, engineer, monkeypatch):
    from justshipit.engineering import worker

    calls, shipped = engineer
    monkeypatch.setattr(worker, "_delivery_commit", lambda s, job: "reviewed1")
    job = _building(s, project, "SHIPPING", steps=[*IMPLEMENTING, {"at": "t", "status": "SHIPPING", "summary": "ship"}])

    worker.run_dev_job(job.id)

    assert calls == [] and shipped == ["reviewed1"]


def test_a_build_the_owner_answered_still_starts_from_its_answer(s, project, engineer):
    """A build back from the owner's answer is no resume: it reads the spec with the answer in it."""
    from justshipit.engineering import worker

    calls, _ = engineer
    job = _building(s, project, "CLARIFICATION_REQUIRED", steps=IMPLEMENTING[:2])

    worker.run_dev_job(job.id)

    assert "judge whether it can be built" in calls[0][1]
    assert "build.step.resumed" not in [x.get("say", {}).get("key") for x in _dev(job.id).steps]


def test_a_build_run_again_while_its_question_waits_leaves_the_question_to_the_owner(s, project, engineer):
    """Its worker died after it asked the owner and before the queue heard it finish: the run again must not build
    past the unanswered question."""
    from justshipit.db.models import AgentRequest
    from justshipit.engineering import worker

    calls, shipped = engineer
    job = _building(s, project, "CLARIFICATION_REQUIRED", steps=[*IMPLEMENTING[:2], {
        "at": "t", "status": "CLARIFICATION_REQUIRED", "summary": "q"}])
    s.add(AgentRequest(project_id=project.id, from_agent="product", type="QUESTION", title="q", summary="q",
                       rationale="r", status="PENDING", policy_mode="approve", action_params={"dev_job_id": job.id}))
    s.commit()

    worker.run_dev_job(job.id)

    assert calls == [] and shipped == [] and _dev(job.id).status == "CLARIFICATION_REQUIRED"


def test_under_host_isolation_a_build_taken_back_stops_instead_of_building_beside_its_old_engineer(s, project, engineer,
                                                                                                   monkeypatch):
    from justshipit.engineering import worker

    calls, _ = engineer
    monkeypatch.setattr(worker, "_containerized", lambda: False)
    job = _building(s, project)

    worker.run_dev_job(job.id)

    assert calls == [] and _dev(job.id).status == "FAILED"


def test_a_build_taken_back_mid_implementation_says_it_carries_on_not_that_it_starts(s, project, engineer, monkeypatch,
                                                                                     tmp_path):
    """Junho round 7: after the 23:22 restart the board said '기능을 만들기 시작했어요' while the build carried on from
    its first committed task; the resume note sat only in the log."""
    from justshipit.engineering import worker

    (tmp_path / ".git" / "logs").mkdir(parents=True)
    (tmp_path / ".git" / "logs" / "HEAD").write_text("0 1 e <e> 1 +0000\tcommit: [task 1/15] upload\n")
    job = _building(s, project)
    seen: list[str] = []
    stubbed = worker.run_agent

    def building(agent_id, purpose, prompt, model, **kw):
        if prompt.startswith("Build the features"):
            with session_scope() as r:
                stage = next(x for x in status.board(r, project_id=project.id, lang="ko") if x["dev_job_id"] == job.id)
                seen.append(stage["headline"])
        return stubbed(agent_id, purpose, prompt, model, **kw)
    monkeypatch.setattr(worker, "run_agent", building)

    worker.run_dev_job(job.id)

    assert len(seen) == 1 and "만들기 시작" not in seen[0], seen
    assert "이어서" in seen[0] and "15개 중 1개" in seen[0], seen
    lines = [worker.step_line(x, "ko") for x in _dev(job.id).steps]
    assert msg("build.step.build_start", "ko") not in lines[5:], "a resumed build never logs that it starts building"


def test_without_a_kernel_id_no_holder_is_taken_as_gone(monkeypatch):
    """Security review (round 9) Low #2: with no machine id, two kernels sharing a hostname share a host id, so a live
    worker of the other one could read as dead here; the fast path stays off and the lease decides."""
    monkeypatch.setattr(runner, "MACHINE", "")
    monkeypatch.setattr(queue, "silent_holders", lambda s: {f"{HOST}:999999:abc"})
    monkeypatch.setattr(runner.children, "alive", lambda pid: False)
    monkeypatch.setattr(runner.docker, "started_by", lambda holder: [])
    assert runner._gone_holders() == set()
    monkeypatch.setattr(runner, "MACHINE", "a1b2c3")
    assert runner._gone_holders() == {f"{HOST}:999999:abc"}, "with a kernel id the same holder is gone"
