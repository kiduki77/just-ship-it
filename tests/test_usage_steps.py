"""A step that makes several model calls waits for headroom for all of them before its first call, and a cap
that no step could fit under is refused when the owner writes it."""
import uuid
from datetime import datetime, timezone

import pytest
from justshipit.agents import executor
from justshipit.api.app import app
from justshipit.contracts.planning import DecisionKind, DecisionOut, PositionOut
from justshipit.contracts.skills import SkillKind, SkillProposalOut
from justshipit.db.models import AgentRequest, AppSetting, BudgetMonth, Job, LLMCallRecord, Meeting, Skill
from justshipit.db.session import engine, session_scope
from justshipit.orchestration import company_graph
from justshipit.runtime import LLMResult
from justshipit.services import budget, usage
from justshipit.services.usage import MustWait
from conftest import store_usage
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select, update


@pytest.fixture(autouse=True)
def clean():
    def wipe():
        with session_scope(lease_checked=False) as s:
            s.execute(delete(AppSetting))
            s.execute(delete(LLMCallRecord))
            s.execute(delete(Skill).where(Skill.name.like("steps-%")))
    wipe()
    yield
    wipe()


@pytest.fixture
def console(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def never_called(*a, **kw):
    pytest.fail("a model was called")


def test_the_step_table_prices_a_meeting_as_every_seat_in_every_round_plus_the_decision():
    seats = 2 * (1 + 1)
    assert usage.STEPS["meeting"].usd(seats) == pytest.approx(seats * usage.purpose("meeting").reserve_usd
                                                              + usage.purpose("decision").reserve_usd)
    assert usage.STEPS["analysis"].usd(6) == pytest.approx(6 * 0.8 + 1.5)


def test_a_meeting_without_headroom_for_all_its_calls_waits_before_round_zero(korean_owner, s, project, monkeypatch):
    project.context = {"pending_meeting": {"agenda": "a", "participants": ["strategy", "risk"]}}
    s.commit()
    whole_meeting = usage.STEPS["meeting"].usd(2 * 2)
    store_usage(day_usd_eq=whole_meeting - 0.1)
    monkeypatch.setattr(company_graph, "run_agent", never_called)

    with pytest.raises(MustWait) as wait:
        company_graph.meeting({"project_id": project.id})

    assert wait.value.reason == "usage_cap_day" and "회의 전체" in wait.value.detail
    s.expire_all()
    assert s.scalar(select(func.count()).select_from(Meeting).where(Meeting.project_id == project.id)) == 0
    assert "pending_meeting" in s.get(type(project), project.id).context, "the meeting stays pending"


def test_a_meeting_with_headroom_runs_every_round(s, project, monkeypatch):
    project.context = {"pending_meeting": {"agenda": "a", "participants": ["strategy", "risk"]}}
    s.commit()
    store_usage(day_usd_eq=usage.STEPS["meeting"].usd(4) + 0.1)
    calls = []

    def model(agent, purpose, prompt, out, **kw):
        calls.append(purpose)
        if out is PositionOut:
            return PositionOut(stance="for", statement="s", evidence_ids=[], concerns=[])
        return DecisionOut(title="t", decision=list(DecisionKind)[0], rationale="r", supporting_evidence_ids=[],
                           dissenting_evidence_ids=[], dissent_summary="", open_questions=[], action_tasks=[],
                           approval_required=False, approval_reason="")
    monkeypatch.setattr(company_graph, "run_agent", model)

    company_graph.meeting({"project_id": project.id})

    assert calls.count("meeting") == 4 and calls.count("decision") == 1


def test_an_analysis_without_headroom_for_all_its_steps_waits_before_its_first_call(korean_owner, monkeypatch):
    from justshipit.analysis import runner
    store_usage(day_usd_eq=6.3)
    monkeypatch.setattr(runner, "run_agent", never_called)

    with pytest.raises(MustWait) as wait:
        runner.run_task("analyst", "p", project_id=990101, task_id=990101, project_slug="steps", attempt=1, lang="en")

    assert "데이터 분석 전체" in wait.value.detail


def proposal(name: str) -> SkillProposalOut:
    return SkillProposalOut(name=name, kind=SkillKind.KNOWLEDGE, target_agents=["research"], problem="p",
                            expected_benefit="b", risks=[], evaluation_plan="e")


@pytest.fixture
def factory_run(monkeypatch):
    from justshipit.capability import factory

    class Portfolio:
        proposals = [proposal("steps-checklist")]
    monkeypatch.setattr(factory, "gaps", lambda: [{"agent": "research", "gap": "g", "count": 3}])
    monkeypatch.setattr(factory, "run_agent", lambda *a, **kw: Portfolio())
    return factory


def skill_requests(name: str) -> int:
    with session_scope() as s:
        ids = s.scalars(select(Skill.id).where(Skill.name == name)).all()
        return sum(r.action_params.get("skill_id") in ids for r in s.scalars(
            select(AgentRequest).where(AgentRequest.action_kind == "skill.create_candidate")))


def test_a_skill_cycle_without_headroom_waits_before_it_proposes_anything(factory_run, monkeypatch):
    store_usage(day_usd_eq=5)
    monkeypatch.setattr(factory_run, "build_and_evaluate", never_called)

    with pytest.raises(MustWait):
        factory_run.run_factory()

    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Skill).where(Skill.name == "steps-checklist")) == 0
    assert skill_requests("steps-checklist") == 0


def test_a_skill_build_that_waits_midway_is_built_again_not_proposed_again(factory_run, monkeypatch):
    builds = []

    def build(sid):
        builds.append(sid)
        if len(builds) == 1:
            raise MustWait(datetime.now(timezone.utc), "subscription_limit", "한도")
        with session_scope() as s:
            s.get(Skill, sid).status = "AWAITING_APPROVAL"
            s.get(Skill, sid).eval_results = {"summary": "done"}
    monkeypatch.setattr(factory_run, "build_and_evaluate", build)

    with pytest.raises(MustWait):
        factory_run.run_factory()
    with session_scope() as s:
        (waiting,) = s.scalars(select(Skill).where(Skill.name == "steps-checklist")).all()
        assert waiting.status != "EVAL_FAILED", "a wait is not an evaluation failure"

    factory_run.run_factory()

    with session_scope() as s:
        rows = s.scalars(select(Skill).where(Skill.name == "steps-checklist")).all()
    assert [(r.id, r.version, r.status) for r in rows] == [(waiting.id, 1, "AWAITING_APPROVAL")]
    assert builds == [waiting.id, waiting.id]
    assert skill_requests("steps-checklist") == 1


def test_a_cap_the_largest_step_cannot_fit_under_is_refused_naming_the_work_and_the_minimum(korean_owner, console):
    need = usage.largest_need(20, "ko")
    minimum = usage.required_cap(need)

    refused = console.put("/api/settings/usage", json={"day_usd_eq": minimum - 0.5})

    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert need.what in detail and f"${need.usd:g}" in detail
    assert f"${minimum:g} 이상" in detail and "하루 상한" in detail and "상한의 20%는" in detail
    assert console.put("/api/settings/usage", json={"day_usd_eq": minimum}).status_code == 200


def test_the_week_cap_is_checked_too_and_a_smaller_reserve_lowers_the_minimum(console):
    assert console.put("/api/settings/usage", json={"week_usd_eq": 3}).status_code == 422
    smaller = usage.required_cap(usage.largest_need(0))
    assert smaller < usage.required_cap(usage.largest_need(20))
    assert console.put("/api/settings/usage", json={"day_usd_eq": smaller, "owner_reserve_pct": 0}).status_code == 200


def test_a_stored_cap_that_has_become_too_small_is_listed_not_raised(korean_owner, console):
    store_usage(day_usd_eq=1.0)

    body = console.get("/api/settings")

    assert body.status_code == 200
    problems = [p["message"] for p in body.json()["problems"] if p["section"] == "usage"]
    assert len(problems) == 1 and "너무 작아" in problems[0] and "하루 상한" in problems[0]
    assert body.json()["effective"]["usage"]["day_usd_eq"] == 1.0


def test_the_wait_names_the_reservation_that_did_not_fit_and_the_amount_used(korean_owner):
    store_usage(day_usd_eq=100)
    with session_scope(lease_checked=False) as s:
        s.add(LLMCallRecord(call_id=uuid.uuid4().hex, agent="research", purpose="work", provider="claude",
                            status="ok", cost_usd=78.0))

    with pytest.raises(MustWait) as wait:
        budget.reserve(None, None, "dev")

    assert "개발 1회에는 $4만큼 필요" in wait.value.detail
    assert "$78.00만큼 썼" in wait.value.detail


def test_a_cap_below_one_reservation_says_it_will_never_start_and_what_to_raise_it_to(korean_owner):
    store_usage(day_usd_eq=3)

    with pytest.raises(MustWait) as wait:
        budget.gate("dev")

    assert "기다려도 시작할 수 없어요" in wait.value.detail and "$5 이상" in wait.value.detail


def test_live_reservations_of_the_previous_month_still_count_against_the_cap():
    store_usage(day_usd_eq=10)
    this_month = datetime.now(timezone.utc).strftime("%Y-%m")
    previous = budget._previous_month()
    with session_scope(lease_checked=False) as s:
        s.execute(delete(BudgetMonth).where(BudgetMonth.month.in_([this_month, previous])))
        s.add(BudgetMonth(month=previous, limit_usd=300, spent_usd=0, reserved_usd=5.0))

    with pytest.raises(MustWait):
        budget.gate("dev")

    with session_scope(lease_checked=False) as s:
        s.execute(update(BudgetMonth).where(BudgetMonth.month == previous).values(reserved_usd=0.0))
    budget.gate("dev")


def test_the_reservation_is_released_in_the_transaction_that_records_the_call(owner_asked, monkeypatch):
    seen: list[set[str]] = []

    def watch(conn, cursor, statement, parameters, context, executemany):
        words = statement.split()
        table = {"INSERT": 2, "UPDATE": 1}.get(words[0])
        if table is not None:
            conn.info.setdefault("tables", set()).add(words[table])

    def committed(conn):
        seen.append(conn.info.pop("tables", set()))

    def model(call):
        return LLMResult(call_id=call.call_id, provider=call.provider, model="m", output={"ok": True},
                         input_tokens=1, output_tokens=1, cached_tokens=0, cost_usd=0.5, duration_ms=1,
                         session_id=None)

    from pydantic import BaseModel

    class Out(BaseModel):
        ok: bool
    monkeypatch.setattr(executor, "run_with_retry", model)
    monkeypatch.setattr(executor.queue, "check_lease", lambda: None)
    event.listen(engine(), "before_cursor_execute", watch)
    event.listen(engine(), "commit", committed)
    try:
        executor.run_agent("project_manager", "plan", "p", Out, project_id=None)
    finally:
        event.remove(engine(), "before_cursor_execute", watch)
        event.remove(engine(), "commit", committed)

    assert any({"budget_months", "llm_calls"} <= tables for tables in seen), seen


def test_the_alert_names_the_reservation_that_did_not_fit(korean_owner, s, project):
    s.commit()
    from justshipit.api import console as console_api
    from test_usage_defer import enqueue, run_once
    store_usage(day_usd_eq=100)
    with session_scope(lease_checked=False) as s:
        s.add(LLMCallRecord(call_id=uuid.uuid4().hex, agent="research", purpose="work", provider="claude",
                            status="ok", cost_usd=78.0))
        s.execute(update(Job).where(Job.wait_reason.is_not(None)).values(status="done", locked_by=None))

    def reserve_dev(payload):
        budget.reserve(None, None, "dev")
    enqueue("run_project", project_id=project.id)
    run_once("run_project", reserve_dev)

    (alert,) = [a for a in console_api.alerts(include_test=True) if a["kind"] == "usage_wait"]
    assert "개발 1회에는 $4만큼 필요" in alert["detail"]
    with session_scope() as s:
        s.execute(update(Job).where(Job.wait_reason.is_not(None)).values(status="done", locked_by=None))
