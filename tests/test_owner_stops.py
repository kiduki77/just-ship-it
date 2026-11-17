"""The owner's stops (services/stops.py). Stopping everything starts nothing but housekeeping, stops running work at its
next lease check and hands it back with its attempt returned, and skips the scheduler's ticks; resume lets the queued
work run. Stopping an agent holds its own jobs, makes a run skip its tasks and reviews while the rest goes on, and makes
work that reached it wait; resume makes all of it eligible again. Stopping or resuming twice changes nothing."""
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from justshipit.api.app import app
from justshipit.contracts.work import AgentResultOut, EvidenceKind, EvidenceOut, ReviewOut, ReviewVerdict
from justshipit.db.models import ActivityEvent, Job, Schedule, Stop, Task
from justshipit.db.session import session_scope
from justshipit.operations import scheduler
from justshipit.orchestration import company_graph
from justshipit.services import queue, stops, work_now
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = pytest.mark.usefixtures("own_jobs_settled")


@pytest.fixture(autouse=True)
def nothing_stopped_after():
    yield
    with session_scope(lease_checked=False) as s:
        s.execute(delete(Stop))


def key(name: str) -> str:
    return f"stops:{name}:{uuid.uuid4().hex[:8]}"


def claim_mine(s, job: Job) -> Job | None:
    """Claim with every other queued job held back, so a job another test left behind is not the one taken."""
    others = set(s.scalars(select(Job.id).where(Job.status == "queued", Job.id != job.id)))
    got = queue.claim(s, "test-stops", [job.kind], held=dict.fromkeys(others))
    s.commit()
    return got


@contextmanager
def leased(lease: queue.Lease):
    ctx = queue.current_lease.set(lease)
    try:
        yield lease
    finally:
        queue.current_lease.reset(ctx)


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def lease_of(job: Job) -> queue.Lease:
    return queue.Lease(job.id, job.lease_token, gate=job.gate, stage_id=job.stage_id, kind=job.kind)


# ------------------------------------------------------------------ stop everything

def test_a_stopped_company_starts_only_housekeeping_and_resume_lets_the_rest_run(s, made_up_kinds):
    spend = queue.enqueue(s, "noop", {}, idempotency_key=key("spend"))
    notice = queue.enqueue(s, "notify", {"kind": "x"}, idempotency_key=key("notice"))
    send = queue.enqueue(s, "execute_action", {"request_id": 990001}, idempotency_key=key("send"))
    s.commit()
    stops.stop_company(s, "점검", lang="en")
    s.commit()

    assert claim_mine(s, spend) is None
    assert claim_mine(s, send) is None, "an emergency stop holds what would reach the outside world"
    got = claim_mine(s, notice)
    assert got is not None and got.id == notice.id
    queue.finish(s, got.id, got.lease_token)
    s.commit()

    stops.resume_company(s, lang="en")
    s.commit()
    got = claim_mine(s, spend)
    assert got is not None and got.id == spend.id
    queue.finish(s, got.id, got.lease_token)
    s.commit()


def test_a_running_job_stops_at_its_next_check_and_goes_back_with_its_attempt(s, project, monkeypatch):
    monkeypatch.setattr(queue, "STAGE_CHECK_S", 0.0)
    job = queue.enqueue(s, "run_project", {"project_id": project.id}, idempotency_key=key("run"))
    s.commit()
    got = claim_mine(s, job)
    assert got.attempts == 1
    with leased(lease_of(got)):
        queue.check_lease()
        stops.stop_company(s, lang="en")
        s.commit()
        with pytest.raises(queue.Stopping):
            queue.check_lease()
    with leased(queue.Lease(0, "free", gate="none", kind="notify")):
        queue.check_lease()   # housekeeping spends nothing and goes on

    queue.release(s, got.id, got.lease_token)
    s.commit()
    s.refresh(got)
    assert (got.kind, got.status, got.attempts) == ("retry_project", "queued", 0), "it carries on from its checkpoint"
    assert "stopped the company" in got.last_error
    assert claim_mine(s, got) is None
    stops.resume_company(s, lang="en")
    s.commit()
    assert claim_mine(s, got) is not None
    s.rollback()


def test_a_stop_seen_once_holds_for_every_thread_sharing_the_lease(s, project, monkeypatch):
    job = queue.enqueue(s, "run_project", {"project_id": project.id}, idempotency_key=key("sticky"))
    s.commit()
    got = claim_mine(s, job)
    monkeypatch.setattr(queue, "STAGE_CHECK_S", 0.0)
    with leased(lease_of(got)):
        stops.stop_company(s, lang="en")
        s.commit()
        with pytest.raises(queue.Stopping):
            queue.check_lease()
        monkeypatch.setattr(queue, "STAGE_CHECK_S", 10.0)
        with pytest.raises(queue.Stopping):
            queue.check_lease()   # a sibling thread's check, inside the window the first raise used to refresh
        with pytest.raises(queue.Stopping):
            queue.check_agent("research")
    s.rollback()


def test_stopping_or_resuming_twice_changes_nothing_and_the_activity_log_has_each_once(s):
    mark = s.scalar(select(func.coalesce(func.max(ActivityEvent.id), 0)))
    first = stops.stop_company(s, "점검", lang="en")
    again = stops.stop_company(s, "다른 이유", lang="en")
    s.commit()
    assert first == again and again["stopped"]["reason"] == "점검"
    assert stops.resume_company(s, lang="en") == stops.resume_company(s, lang="en") == {"stopped": None}
    s.commit()
    logged = [(e.verb, e.actor) for e in s.scalars(select(ActivityEvent).where(
        ActivityEvent.id > mark, ActivityEvent.entity == "company").order_by(ActivityEvent.id))]
    assert logged == [("stopped", "owner"), ("resumed", "owner")]


def test_the_scheduler_leaves_a_held_schedule_as_it_was_and_fires_it_once_after_the_resume(s, made_up_kinds):
    due = datetime.now(timezone.utc) - timedelta(minutes=1)
    spend = Schedule(name=key("spend"), kind="noop", payload={}, interval_hours=168, enabled=True, next_run_at=due)
    keep = Schedule(name=key("keep"), kind="retention", payload={}, interval_hours=24, enabled=True, next_run_at=due)
    s.add_all([spend, keep])
    s.commit()
    ids = [spend.id, keep.id]

    def tick() -> list[int]:
        with session_scope() as x:
            fired = scheduler.tick(x)
        s.expire_all()
        return fired

    try:
        stops.stop_company(s, lang="en")
        s.commit()
        for _ in range(3):
            fired = tick()
            assert keep.id in fired and spend.id not in fired
            assert spend.next_run_at == due and spend.last_run_at is None, "a minutes-long stop does not push a weekly job out"
            keep.next_run_at = due
            s.commit()
        assert not s.scalars(select(Job).where(Job.kind == "noop", Job.idempotency_key.like(f"sched:{spend.id}:%"))).all()

        stops.resume_company(s, lang="en")
        s.commit()
        assert spend.id in tick(), "it fires on the first tick after the resume"
        assert spend.next_run_at > datetime.now(timezone.utc) + timedelta(hours=167)
        assert len(s.scalars(select(Job).where(Job.kind == "noop", Job.idempotency_key.like(f"sched:{spend.id}:%"))).all()) == 1
        assert spend.id not in tick(), "once, however many intervals the stop spanned"
    finally:
        s.rollback()
        s.execute(delete(Schedule).where(Schedule.id.in_(ids)))
        s.commit()


def test_a_stopped_company_holds_the_schedule_that_reads_pages_from_the_web_but_keeps_the_books_and_the_notices(s):
    assert stops.HOUSEKEEPING == {"notify", "daily_briefing", "backup", "retention", "bet_sweep"}
    reactions = queue.enqueue(s, "post_reactions", {}, idempotency_key=key("reactions"))
    s.commit()
    stops.stop_company(s, lang="en")
    s.commit()
    assert claim_mine(s, reactions) is None, "fetching Hacker News and Reddit pages is outbound"
    due = datetime.now(timezone.utc) - timedelta(minutes=1)
    with session_scope() as x:
        scheduler.sync(x)
    sc = s.scalar(select(Schedule).where(Schedule.kind == "post_reactions"))
    sc.next_run_at = due
    s.commit()
    with session_scope() as x:
        assert sc.id not in scheduler.tick(x)
    s.expire_all()
    assert sc.next_run_at == due
    stops.resume_company(s, lang="en")
    s.commit()
    assert claim_mine(s, reactions) is not None
    s.rollback()


# ------------------------------------------------------------------ stop an agent

def test_a_stopped_agent_takes_no_new_job_its_running_one_stops_and_other_agents_carry_on(s, monkeypatch):
    monkeypatch.setattr(queue, "STAGE_CHECK_S", 0.0)
    project_manager = queue.enqueue(s, "triage_idea", {"idea_id": 990002}, idempotency_key=key("project_manager"))
    running = queue.enqueue(s, "triage_idea", {"idea_id": 990003}, idempotency_key=key("ceo-running"))
    critic = queue.enqueue(s, "idea_review", {"review_id": 990004}, idempotency_key=key("critic"))
    s.commit()
    got = claim_mine(s, running)
    stops.stop_agent(s, "project_manager", lang="en")
    s.commit()

    assert claim_mine(s, project_manager) is None
    other = claim_mine(s, critic)
    assert other is not None and other.id == critic.id, "the owner's other work is unaffected"
    with leased(lease_of(other)):
        queue.check_lease()
    with leased(lease_of(got)), pytest.raises(queue.Stopping):
        queue.check_lease()
    queue.release(s, got.id, got.lease_token)
    s.commit()
    s.refresh(got)
    assert (got.status, got.attempts, got.last_error) == ("queued", 0, "Waiting: Project Manager is paused.")
    assert claim_mine(s, got) is None

    stops.resume_agent(s, "project_manager", lang="en")
    s.commit()
    assert claim_mine(s, project_manager) is not None
    s.rollback()


def test_a_run_held_on_a_stopped_agent_waits_on_it_and_any_resume_lets_it_look_again(s, project):
    job = queue.enqueue(s, "run_project", {"project_id": project.id}, idempotency_key=key("held-run"))
    s.commit()
    got = claim_mine(s, job)
    stops.stop_agent(s, "research", lang="en")
    stops.stop_agent(s, "marketing", lang="en")
    s.commit()
    queue.hold(s, got.id, got.lease_token, queue.AgentStopped("research", stops.waiting_text("research", "en")))
    s.commit()
    s.refresh(got)
    assert (got.kind, got.status, got.attempts, got.waits_on) == ("retry_project", "queued", 0, "research")
    assert claim_mine(s, got) is None

    stops.resume_agent(s, "marketing", lang="en")   # the run also had marketing's tasks
    s.commit()
    s.refresh(got)
    assert got.waits_on is None
    again = claim_mine(s, got)
    assert again is not None and again.waits_on is None
    s.rollback()


def authored() -> AgentResultOut:
    return AgentResultOut(
        summary="시장은 크다", findings_markdown="본문 [E1]", assumptions=[], risks=[], open_questions=[],
        proposed_follow_up_tasks=[], requests=[], capability_gaps=[], financial_model=None,
        evidence=[EvidenceOut(claim="가설", kind=EvidenceKind.HYPOTHESIS, source_url=None, source_title=None,
                              quote=None, published_at=None, confidence=0.4)])


def passed() -> ReviewOut:
    return ReviewOut(verdict=ReviewVerdict.PASS, criteria_met=["c"], criteria_unmet=[], claim_checks=[],
                     counter_arguments=[], rework_instructions=None, contested=False)


class Model:
    """A model that answers each purpose in turn, after the check run_agent makes: a stopped agent is not called."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def __call__(self, agent, purpose, prompt, model, **kw):
        queue.check_agent(agent)
        self.calls.append((agent, purpose))
        return authored() if purpose == "work" else passed()


def a_task(s, project, owner: str, status: str = "READY", **kw) -> int:
    t = Task(project_id=project.id, key=f"{owner}_{uuid.uuid4().hex[:8]}", title=f"{owner} 업무", owner=owner, objective="o",
             acceptance_criteria=["c"], status=status, origin="owner", **kw)
    s.add(t)
    s.commit()
    return t.id


def status_of(tid: int) -> tuple:
    with session_scope() as x:
        t = x.get(Task, tid)
        return t.status, t.iteration


def test_a_task_step_of_a_stopped_agent_waits_where_it_stood_and_resumes_without_paying_twice(s, project, monkeypatch):
    model = Model()
    monkeypatch.setattr(company_graph, "run_agent", model)
    tid = a_task(s, project, "research")
    state = {"project_id": project.id, "task_id": tid}
    with leased(queue.Lease(0, "run", kind="run_project")):
        stops.stop_agent(s, "research", lang="en")
        s.commit()
        assert company_graph.work(state) == {}
        assert status_of(tid) == ("READY", 0) and model.calls == [], "the author was not called and lost no try"

        stops.resume_agent(s, "research", lang="en")
        stops.stop_agent(s, "risk", lang="en")
        s.commit()
        assert company_graph.work(state) == {}
        assert status_of(tid) == ("REVIEW", 1), "the result waits for its stopped reviewer"

        stops.resume_agent(s, "risk", lang="en")
        s.commit()
        assert company_graph.work(state) == {"done": [tid]}
    assert status_of(tid) == ("DONE", 1)
    assert model.calls == [("research", "work"), ("risk", "review")]


def test_a_stop_during_the_authors_call_keeps_its_result_and_the_resume_does_not_call_it_again(s, project, monkeypatch):
    monkeypatch.setattr(queue, "STAGE_CHECK_S", 0.0)

    class StopsDuringTheAuthor(Model):
        def __call__(self, agent, purpose, *a, **kw):
            out = super().__call__(agent, purpose, *a, **kw)
            if purpose == "work":
                stops.stop_company(s, lang="en")
                s.commit()
            return out

    model = StopsDuringTheAuthor()
    monkeypatch.setattr(company_graph, "run_agent", model)
    tid = a_task(s, project, "research")
    state = {"project_id": project.id, "task_id": tid}
    with leased(queue.Lease(0, "run", kind="run_project")), pytest.raises(queue.Stopping):
        company_graph.work(state)
    assert status_of(tid) == ("REVIEW", 1), "the result the author was paid for is saved"
    assert model.calls == [("research", "work")]

    stops.resume_company(s, lang="en")
    s.commit()
    with leased(queue.Lease(0, "run", kind="run_project")):
        assert company_graph.work(state) == {"done": [tid]}
    assert model.calls == [("research", "work"), ("risk", "review")], "the author is not paid twice"


def test_a_job_claimed_before_a_company_stop_makes_no_model_call_after_it(s, project, monkeypatch):
    from justshipit.agents import executor
    called = []
    monkeypatch.setattr(executor, "_check_quota", lambda provider: None)
    monkeypatch.setattr(executor, "_run_once", lambda *a, **kw: called.append(a) or (authored(), None))
    job = queue.enqueue(s, "run_project", {"project_id": project.id}, idempotency_key=key("first-call"))
    s.commit()
    got = claim_mine(s, job)
    with leased(lease_of(got)):   # its cached check is fresh: the claim just happened
        stops.stop_company(s, lang="en")
        s.commit()
        with pytest.raises(queue.Stopping):
            executor.run_agent("research", "work", "p", AgentResultOut, project_id=project.id)
    assert called == []
    s.rollback()


def test_the_run_skips_a_stopped_agents_tasks_goes_on_with_the_rest_and_waits_when_only_those_are_left(s, project, api):
    research = a_task(s, project, "research")
    marketing = a_task(s, project, "marketing")
    review = a_task(s, project, "finance", "REVIEW", iteration=1,
                    result={"summary": "s", "findings_markdown": "f", "evidence_ids": [], "request_ids": []})
    state = {"project_id": project.id}
    stops.stop_agent(s, "research", lang="en")
    stops.stop_agent(s, "risk", lang="en")
    s.commit()

    assert company_graph.schedule(state) == {"batch": [marketing], "next": "work"}
    with session_scope() as x:
        x.get(Task, marketing).status = "DONE"
    with pytest.raises(queue.AgentStopped) as held:
        company_graph.schedule(state)
    assert held.value.agent in ("research", "risk")
    s.expire_all()
    rows = {t["id"]: t["waiting_on"] for t in api.get("/api/tasks", params={"project_id": project.id}).json()["tasks"]}
    assert (rows[research], rows[review], rows[marketing]) == ("research", "risk", None)

    stops.resume_agent(s, "research", lang="en")
    stops.resume_agent(s, "risk", lang="en")
    s.commit()
    assert sorted(company_graph.schedule(state)["batch"]) == sorted([research, review])


def test_while_stopped_working_now_shows_only_the_steps_still_finishing(s, project):
    tid = a_task(s, project, "research", "RUNNING")
    run = queue.enqueue(s, "run_project", {"project_id": project.id}, idempotency_key=key("in-flight"))
    s.commit()

    def shown() -> list[bool]:
        return [r.stopping for r in work_now.rows(s, set()) if r.task_id == tid]

    assert shown() == [False]
    got = claim_mine(s, run)
    stops.stop_company(s, lang="en")
    s.commit()
    assert shown() == [True], "the step a worker still runs shows, as stopping"
    queue.release(s, got.id, got.lease_token)
    s.commit()
    assert shown() == [], "once its run is handed back it waits for the resume, and is not work in progress"

    stops.resume_company(s, lang="en")
    stops.stop_agent(s, "research", lang="en")
    s.commit()
    assert shown() == []
    claim_mine(s, run)
    assert shown() == [True]
    stops.resume_agent(s, "research", lang="en")
    s.commit()
    assert shown() == [False]


# ------------------------------------------------------------------ the console's routes

def test_the_console_stops_and_resumes_the_company_and_an_agent(s, api):
    stopped = api.post("/api/company/stop", json={"reason": "점검"})
    assert stopped.status_code == 200 and stopped.json()["stopped"]["reason"] == "점검"
    assert api.post("/api/company/stop").json() == stopped.json()
    assert api.get("/api/company").json() == stopped.json()

    assert api.post("/api/agents/research/stop").json()["stopped"]["by"] == "owner"
    agents = {a["id"]: a for a in api.get("/api/agents").json()}
    assert agents["research"]["state"] == "stopped" and agents["marketing"]["stopped"] is None
    assert api.post("/api/agents/nobody/stop").status_code == 404

    assert api.post("/api/agents/research/resume").json() == {"agent": "research", "stopped": None}
    assert api.post("/api/company/resume").json() == {"stopped": None}
    assert api.get("/api/company").json() == {"stopped": None}


def test_the_foreground_run_refuses_with_a_sentence_while_the_company_is_stopped(s, project, monkeypatch):
    from justshipit import cli
    from justshipit.orchestration import runner
    from typer.testing import CliRunner

    started = []
    monkeypatch.setattr(runner, "start", lambda pid: started.append(pid))
    stops.stop_company(s, lang="en")
    s.commit()
    out = CliRunner().invoke(cli.app, ["project", "run", str(project.id)])
    assert out.exit_code == 1 and "stopped" in out.output and "ship resume" in out.output
    assert started == [] and "Traceback" not in out.output
