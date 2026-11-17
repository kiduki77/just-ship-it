"""Development work the owner waits on never ends as failed because the worker restarted or the subscription hit its
usage limit. A fix round the queue takes back carries on from its step, as a first build does (test_build_survive),
and a usage limit met in the middle of a build or a fix round puts it back in the queue until the limit resets, from
where it carries on. Before, a restarted fix round went back to delivered with "fix again", and a mid-build usage
limit failed the build ("개발은 중간부터 이어 가지 못하니, 그때 새로 시도해 주세요")."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from justshipit.contracts.handoff import ClarificationAnswerOut, ClarificationItemOut, IntakeOut
from justshipit.contracts.requests import HumanDecision
from justshipit.db.models import DevJob, Job
from justshipit.db.session import session_scope
from justshipit.engineering import status
from justshipit.i18n import msg
from justshipit.services import queue
from justshipit.services.usage import MustWait
from test_build_survive import IMPLEMENTING, _building, _dev, _isolated_jobs, _row, engineer  # noqa: F401
from test_dev_fix import NOTE, _delivered, _respond, _run_jobs
from test_engineering import _engineer_commit, _execute_claimed, _pending_merge, _run_queued, _sh

# What the subscription CLI said, which the owner must never read.
RAW = "Claude AI usage limit reached|1762412400"


@pytest.fixture(autouse=True)
def _korean_owner(monkeypatch):
    from justshipit.services import app_settings
    monkeypatch.setattr(app_settings, "language", lambda: "ko")


def _limit(hours: float = 3) -> MustWait:
    until = (datetime.now(UTC) + timedelta(hours=hours)).replace(microsecond=0)
    return MustWait(until, "subscription_limit", RAW, "claude")


def _board(s, project_id: int, job_id: int, lang: str) -> dict:
    s.expire_all()
    return next(st for st in status.board(s, project_id=project_id, lang=lang) if st["dev_job_id"] == job_id)


def _fresh(s, project) -> DevJob:
    """A build the queue has not started."""
    job = _building(s, project, "QUEUED")
    job.steps, job.result = [], {}
    s.commit()
    return job


def _run_due(s, job_id: int) -> list[str]:
    """The dev job's queue jobs the worker would run now, in order: one waiting for a later time is not run."""
    ran = []
    while (qjob := s.scalar(select(Job).where(Job.status == "queued", Job.run_after <= datetime.now(UTC),
                                               Job.payload["dev_job_id"].as_integer() == job_id)
                            .order_by(Job.id))) is not None:
        ran.append(qjob.kind)
        _run_queued(s, qjob.id, qjob.payload)
        assert len(ran) < 10, ran
    return ran


def _queued_build(s, job: DevJob) -> int:
    payload = {"dev_job_id": job.id}
    w = Job(gate="direct", kind="dev_job", payload=payload, lock_key=queue._lock_key("dev_job", payload), max_attempts=1)
    s.add(w)
    s.commit()
    return w.id


def _due(job_id: int) -> None:
    """The usage limit has reset: the deferred run is due now."""
    with session_scope() as s:
        s.get(Job, job_id).run_after = datetime.now(UTC) - timedelta(seconds=1)


# ------------------------------------------------------------------ a fix round the worker restarts carries on

def _half_fix(clone):
    (clone / "app.py").write_text("v2\n")
    _engineer_commit(clone, "fix: the list shows re-uploaded files")
    raise queue.Stopping()  # the worker restarts while the engineer is still on the second change


def _rest_of_fix(clone):
    (clone / "returns.py").write_text("ok = True\n")
    _engineer_commit(clone, "fix: an all-N return column is fine")


def test_a_fix_round_the_worker_restarts_mid_change_carries_on_and_is_delivered(s, project, tmp_path, monkeypatch):
    from justshipit.engineering import revise, worker

    job, _, repo, fixes, seen = _delivered(s, project, tmp_path, monkeypatch, f"ws-restart-{project.id}")
    fixes.extend([_half_fix, _rest_of_fix])

    _respond(s, _pending_merge(s, job.id).id, HumanDecision.REJECT, note=NOTE)

    assert _run_jobs(s, job.id) == ["dev_merge_declined", "dev_fix", "dev_fix", "acceptance"]
    s.expire_all()
    d = s.get(DevJob, job.id)
    assert (d.status, d.error) == ("DELIVERED", None)
    assert [(f["n"], f["state"]) for f in revise.rounds(d)] == [(1, "delivered")], "one round, carried on, not a new one"
    assert _sh(repo, "git", "show", f"{job.branch}:app.py") == "v2", "what the stopped run committed is kept"
    assert _sh(repo, "git", "show", f"{job.branch}:returns.py") == "ok = True"
    fix_prompts = [p for a, _, p in seen["prompts"] if a == "engineer" and "turned this build down" in p]
    assert len(fix_prompts) == 2 and "stopped partway" not in fix_prompts[0]
    assert "stopped partway" in fix_prompts[1] and "restarted" in fix_prompts[1]
    assert "요청대로 고침" in _pending_merge(s, job.id).title
    lines = [worker.step_line(x, "ko") for x in d.steps]
    assert any(line and "백그라운드 프로세스가 다시 시작해서 하던 곳부터 이어서" in line for line in lines)


def test_a_fix_round_the_worker_restarts_in_its_checks_checks_again_without_changing_again(s, project, tmp_path,
                                                                                         monkeypatch):
    from justshipit.engineering import revise, worker

    job, _, repo, fixes, seen = _delivered(s, project, tmp_path, monkeypatch, f"ws-check-{project.id}")
    fixes.append(lambda clone: (clone / "app.py").write_text("v2\n") or _engineer_commit(clone, "fix"))
    agent, reviews = worker.run_agent, []

    def reviewer_cut_once(agent_id, purpose, prompt, model, **kw):
        if agent_id == "eng_reviewer" and NOTE in prompt:
            reviews.append(prompt)
            if len(reviews) == 1:
                raise queue.Stopping()
        return agent(agent_id, purpose, prompt, model, **kw)
    monkeypatch.setattr(worker, "run_agent", reviewer_cut_once)

    _respond(s, _pending_merge(s, job.id).id, HumanDecision.REJECT, note=NOTE)

    assert _run_jobs(s, job.id) == ["dev_merge_declined", "dev_fix", "dev_fix", "acceptance"]
    s.expire_all()
    d = s.get(DevJob, job.id)
    assert (d.status, revise.latest(d)["state"]) == ("DELIVERED", "delivered")
    assert len([p for a, _, p in seen["prompts"] if a == "engineer" and "turned this build down" in p]) == 1
    assert len(reviews) == 2 and _sh(repo, "git", "show", f"{job.branch}:app.py") == "v2"


def test_a_fix_round_the_queue_takes_back_from_a_dead_worker_is_queued_to_carry_on(s, project):
    from justshipit.engineering import revise

    job = _building(s, project)
    revise._keep(job, {"n": 1, "note": NOTE, "request_id": None, "from": "abc", "state": "running"})
    s.commit()
    payload = {"dev_job_id": job.id, "n": 1}
    with session_scope() as s2:
        w = Job(gate="direct", kind="dev_fix", payload=payload, lock_key=queue._lock_key("dev_fix", payload), status="running",
                locked_by="worker-pc:21024:eca3b8", lease_token="t", attempts=1, max_attempts=1,
                locked_at=datetime.now(UTC) - timedelta(hours=1))
        s2.add(w)
        s2.flush()
        jid = w.id

    with session_scope() as s2:
        queue.reclaim(s2, {})

    assert _row(jid).status == "queued" and _row(jid).max_attempts == queue.BUILD_ATTEMPTS_AFTER_RECLAIM
    d = _dev(job.id)
    assert d.status == "IMPLEMENTING" and revise.latest(d)["state"] == "running"
    assert _board(s, project.id, job.id, "ko")["headline"] == msg("build.stage.resuming", "ko")



def test_under_host_isolation_a_fix_round_a_stopped_worker_gave_back_says_it_ends_not_that_it_resumes(s, project,
                                                                                                     monkeypatch):
    """On the host a killed worker's engineer may still be writing in the tree, so revise.run ends the round instead
    of carrying on (worker.carries_on): the board must not promise it picks up where it left off."""
    from justshipit.engineering import revise, worker

    monkeypatch.setattr(worker, "_containerized", lambda: False)
    job = _building(s, project)
    revise._keep(job, {"n": 1, "note": NOTE, "request_id": None, "from": "abc", "state": "running"})
    payload = {"dev_job_id": job.id, "n": 1}
    s.add(Job(gate="direct", kind="dev_fix", payload=payload, lock_key=queue._lock_key("dev_fix", payload),
              max_attempts=1))
    s.commit()

    line = _board(s, project.id, job.id, "ko")
    assert line["headline"] != msg("build.stage.resuming", "ko")
    assert line["headline"] == msg("build.stage.interrupted", "ko")


def test_under_host_isolation_a_build_back_at_the_question_waiting_for_usage_says_it_waits(s, project, monkeypatch):
    """A run that met a usage limit at intake after the owner's answer is back at the question, with no step to lose
    (worker._rewind): the next run starts it, so the board says it waits for the reset, not that the run ends."""
    from justshipit.engineering import worker

    monkeypatch.setattr(worker, "_containerized", lambda: False)
    job = _building(s, project, "CLARIFICATION_REQUIRED", steps=[])
    payload = {"dev_job_id": job.id}
    s.add(Job(gate="direct", kind="dev_job", payload=payload, lock_key=queue._lock_key("dev_job", payload),
              max_attempts=1, wait_reason="subscription_limit", run_after=datetime.now(UTC) + timedelta(hours=2)))
    s.commit()

    assert _board(s, project.id, job.id, "ko")["headline"] != msg("build.stage.interrupted", "ko")

# ------------------------------------------------------------------ a usage limit mid-way waits, then carries on

def test_a_usage_limit_mid_build_waits_for_the_reset_and_carries_on_from_its_step(s, project, engineer, monkeypatch):
    from justshipit.engineering import worker

    calls, shipped = engineer
    job = _fresh(s, project)
    qid = _queued_build(s, job)
    wait, stubbed = _limit(), worker.run_agent

    def limit_once(agent_id, purpose, prompt, model, **kw):
        if prompt.startswith("Build the features") and not any(p.startswith("Build the features") for _, p in calls):
            calls.append((agent_id, prompt))
            raise wait
        return stubbed(agent_id, purpose, prompt, model, **kw)
    monkeypatch.setattr(worker, "run_agent", limit_once)

    _execute_claimed(s, qid, {"dev_job_id": job.id})

    row, waiting = _row(qid), _dev(job.id)
    assert (row.status, row.wait_reason, row.attempts, row.deferrals) == ("queued", "subscription_limit", 0, 1)
    assert row.run_after == wait.until
    assert (waiting.status, waiting.error) == ("IMPLEMENTING", None), "a usage limit is no failure"
    for lang in ("ko", "en"):
        stage = _board(s, project.id, job.id, lang)
        assert stage["tone"] == "wait"
        assert stage["headline"] == msg("build.stage.usage_paused", lang, until=status.at(wait.until, lang))
        said = " ".join(x for x in (stage["headline"], stage["detail"] or "") if x)
        assert RAW not in said and "usage limit reached" not in said
        assert not stage["retry_dev"] and stage["actions"] == []
    assert "사용 한도에 걸려서" in _board(s, project.id, job.id, "ko")["headline"]

    _due(qid)
    before = len(calls)
    _execute_claimed(s, qid, {"dev_job_id": job.id})

    again = [p for agent, p in calls[before:] if agent == "engineer"]
    assert not any("judge whether it can be built" in p or p.startswith("Use the pstack:architect") for p in again)
    assert "stopped partway" in again[0] and "usage limit" in again[0] and "[task k/M]" in again[0]
    done = _dev(job.id)
    assert done.status == "SHIPPING" and shipped and _row(qid).status == "done"
    lines = [worker.step_line(x, "ko") for x in done.steps]
    assert any(line and line.startswith("사용 한도가 풀려서 하던 곳부터 이어서") for line in lines)
    assert msg("build.step.build_start", "ko") not in lines[lines.index(msg("build.step.build_start", "ko")) + 1:]


@pytest.mark.parametrize("at", ["QA", "REVIEWING"])
def test_a_usage_limit_in_the_checks_waits_and_runs_the_checks_again(s, project, engineer, monkeypatch, at):
    from justshipit.engineering import worker

    calls, shipped = engineer
    job = _building(s, project, at, steps=[*IMPLEMENTING, {"at": "t", "status": at, "summary": at}])
    qid = _queued_build(s, job)
    stubbed, limited = worker.run_agent, []

    def reviewer_limited_once(agent_id, purpose, prompt, model, **kw):
        if agent_id == "eng_reviewer" and not limited:
            limited.append(1)
            raise _limit()
        return stubbed(agent_id, purpose, prompt, model, **kw)
    monkeypatch.setattr(worker, "run_agent", reviewer_limited_once)

    _execute_claimed(s, qid, {"dev_job_id": job.id})
    assert _row(qid).status == "queued" and _dev(job.id).status == "REVIEWING" and not shipped

    _due(qid)
    _execute_claimed(s, qid, {"dev_job_id": job.id})

    assert not any(p.startswith("Build the features") for _, p in calls)
    assert _dev(job.id).status == "SHIPPING" and shipped


def test_under_host_isolation_a_build_that_waited_for_usage_still_carries_on(s, project, engineer, monkeypatch):
    """A run that stopped for a usage limit left nothing running: its CLI call had already answered. So it carries on
    on the host too, unlike a run whose worker was killed (test_build_survive)."""
    from justshipit.engineering import worker

    calls, shipped = engineer
    monkeypatch.setattr(worker, "_containerized", lambda: False)
    job = _fresh(s, project)
    qid = _queued_build(s, job)
    stubbed = worker.run_agent

    def limit_once(agent_id, purpose, prompt, model, **kw):
        if prompt.startswith("Build the features") and not any(p.startswith("Build the features") for _, p in calls):
            calls.append((agent_id, prompt))
            raise _limit()
        return stubbed(agent_id, purpose, prompt, model, **kw)
    monkeypatch.setattr(worker, "run_agent", limit_once)

    _execute_claimed(s, qid, {"dev_job_id": job.id})
    _due(qid)
    _execute_claimed(s, qid, {"dev_job_id": job.id})

    assert _dev(job.id).status == "SHIPPING" and shipped


def test_a_usage_limit_while_the_cpo_answers_the_questions_asks_them_again_after_the_wait(s, project, engineer,
                                                                                        monkeypatch):
    """The build must not carry on past questions nobody answered: a wait met while the CPO answers them leaves the
    job asking, and the run after the wait asks again."""
    from justshipit.engineering import worker

    calls, shipped = engineer
    job = _fresh(s, project)
    qid = _queued_build(s, job)
    stubbed, asked = worker.run_agent, []

    def unclear(agent_id, purpose, prompt, model, **kw):
        if model is IntakeOut:
            calls.append((agent_id, prompt))
            return IntakeOut(clear_enough=False, blocking_questions=["반품 열이 비면?"], assumptions_safe_to_make=[],
                             implementation_outline="outline")
        if agent_id == "product":
            asked.append(prompt)
            if len(asked) == 1:
                raise _limit()
            return ClarificationAnswerOut(answers=[ClarificationItemOut(question="반품 열이 비면?", answer="N으로 봐요",
                                                                        needs_owner=False)])
        return stubbed(agent_id, purpose, prompt, model, **kw)
    monkeypatch.setattr(worker, "run_agent", unclear)
    monkeypatch.setattr(worker.registry, "read_at", lambda *a: "# PRD")
    monkeypatch.setattr(worker.registry, "write_files", lambda *a, **kw: "sha")

    _execute_claimed(s, qid, {"dev_job_id": job.id})
    assert _row(qid).status == "queued" and _dev(job.id).error is None
    stage = _board(s, project.id, job.id, "ko")
    assert stage["headline"] == msg("build.stage.usage_paused", "ko", until=status.at(_row(qid).run_after, "ko"))
    assert stage["link"] is None, "no question waits on the owner"

    _due(qid)
    _execute_claimed(s, qid, {"dev_job_id": job.id})

    assert len(asked) == 2, "the questions go to the CPO again, not past it"
    assert _dev(job.id).status == "SHIPPING" and shipped


def test_a_usage_limit_mid_fix_round_waits_and_the_round_carries_on(s, project, tmp_path, monkeypatch):
    from justshipit.engineering import revise

    job, _, repo, fixes, seen = _delivered(s, project, tmp_path, monkeypatch, f"ws-usage-{project.id}")
    wait = _limit()

    def half_then_limit(clone):
        (clone / "app.py").write_text("v2\n")
        _engineer_commit(clone, "fix: the list shows re-uploaded files")
        raise wait
    fixes.extend([half_then_limit, _rest_of_fix])

    _respond(s, _pending_merge(s, job.id).id, HumanDecision.REJECT, note=NOTE)
    assert _run_due(s, job.id) == ["dev_merge_declined", "dev_fix"]

    s.expire_all()
    d = s.get(DevJob, job.id)
    assert (d.status, d.error, revise.latest(d)["state"]) == ("IMPLEMENTING", None, "running")
    qjob = s.scalar(select(Job).where(Job.kind == "dev_fix", Job.payload["dev_job_id"].as_integer() == job.id))
    assert (qjob.status, qjob.wait_reason, qjob.run_after) == ("queued", "subscription_limit", wait.until)
    stage = _board(s, project.id, job.id, "ko")
    assert stage["headline"] == msg("build.stage.usage_paused", "ko", until=status.at(wait.until, "ko"))
    assert RAW not in (stage["detail"] or "")

    _due(qjob.id)
    assert _run_due(s, job.id) == ["dev_fix", "acceptance"]

    s.expire_all()
    d = s.get(DevJob, job.id)
    assert (d.status, d.error, revise.latest(d)["state"]) == ("DELIVERED", None, "delivered")
    assert _sh(repo, "git", "show", f"{job.branch}:app.py") == "v2"
    assert _sh(repo, "git", "show", f"{job.branch}:returns.py") == "ok = True"
    resumed = [p for a, _, p in seen["prompts"] if a == "engineer" and "turned this build down" in p][-1]
    assert "stopped partway" in resumed and "usage limit" in resumed


def test_a_usage_limit_as_the_worker_stops_still_leaves_the_line_the_next_run_carries_on_from(s, project, engineer,
                                                                                              monkeypatch):
    """Review (L1): a stop that lands as the limit is met must not drop the line, or on the host the build would end."""
    import threading

    from justshipit.engineering import worker

    calls, shipped = engineer
    monkeypatch.setattr(worker, "_containerized", lambda: False)
    job = _fresh(s, project)
    lease = queue.Lease(job_id=0, token="t", stop=threading.Event())
    stubbed = worker.run_agent

    def limit_as_it_stops(agent_id, purpose, prompt, model, **kw):
        if prompt.startswith("Build the features") and not lease.stop.is_set():
            lease.stop.set()
            raise _limit()
        return stubbed(agent_id, purpose, prompt, model, **kw)
    monkeypatch.setattr(worker, "run_agent", limit_as_it_stops)
    held = queue.current_lease.set(lease)
    try:
        with pytest.raises(MustWait):
            worker.run_dev_job(job.id)
    finally:
        queue.current_lease.reset(held)

    assert worker.paused(_dev(job.id))
    worker.run_dev_job(job.id)
    assert _dev(job.id).status == "SHIPPING" and shipped


def test_a_build_whose_usage_wait_is_over_names_no_time_already_past(s, project, engineer, monkeypatch):
    """Review (L2): once the reset time has passed and no worker has picked it up yet, the line names no past time."""
    from justshipit.engineering import worker

    calls, _ = engineer
    job = _fresh(s, project)
    qid = _queued_build(s, job)
    stubbed = worker.run_agent

    def limit(agent_id, purpose, prompt, model, **kw):
        if prompt.startswith("Build the features"):
            raise _limit()
        return stubbed(agent_id, purpose, prompt, model, **kw)
    monkeypatch.setattr(worker, "run_agent", limit)
    _execute_claimed(s, qid, {"dev_job_id": job.id})
    _due(qid)

    for lang in ("ko", "en"):
        stage = _board(s, project.id, job.id, lang)
        assert stage["headline"] == msg("build.stage.usage_reset", lang) and stage["detail"]
