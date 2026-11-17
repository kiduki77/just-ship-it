"""Alone tasks (docs/design/staffing-structure.md, sections 6.1 and 7): a small leaf finishes after one author call and
no review, reads as partial when its author lists unmet criteria, gets a checker when it would put a card to the
owner, and a sample of alone tasks gets a shadow check afterwards that gates nothing and is paid apart from the task.
The model is a stand-in below run_agent, so every call is a real ledger row."""
import pytest
from owner_task_kit import draft
from justshipit import staffing
from justshipit.agents import executor
from justshipit.contracts.requests import RequestType, RiskLevel
from justshipit.db.models import Artifact, Crew, CrewTurn, Evidence, Job, LLMCallRecord, Project, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import audit, company_graph
from justshipit.orchestration import context as ctx
from justshipit.runtime.base import LLMResult
from justshipit.services import budget, control, registry, rounds, usage
from justshipit.settings import limits, staffing_config
from sqlalchemy import func, select

pytestmark = pytest.mark.usefixtures("owner_asked", "own_jobs_settled")

EVIDENCE = {"claim": "c", "kind": "sourced_claim", "source_url": "https://example.org/a", "source_title": "t",
            "quote": "q", "published_at": "2025", "confidence": 0.9}
CARD = draft(type=RequestType.APPROVAL, title="Send the survey", action_kind="internal.other", risk_level=RiskLevel.LOW).model_dump(mode="json")
PASS = {"verdict": "pass", "criteria_met": ["c"], "criteria_unmet": [], "notes": [], "previous_unmet": [],
        "claim_checks": [{"evidence_index": 0, "status": "supported", "note": "ok", "counter_source_url": None}],
        "counter_arguments": [], "rework_instructions": None, "contested": False}


def result(unmet=(), requests=()) -> dict:
    return {"summary": "s", "findings_markdown": "f [E1]", "evidence": [EVIDENCE], "assumptions": [], "risks": [],
            "open_questions": [], "unmet_criteria": list(unmet), "proposed_follow_up_tasks": [],
            "requests": list(requests), "capability_gaps": [], "financial_model": None}


class Crash(BaseException):
    """The worker died mid-step: nothing in `work` catches it, so the checkpoint replays the node."""


@pytest.fixture
def model(monkeypatch):
    """`out["author"]` is what the author returns. A review always passes."""
    out = {"author": result()}

    def run(call):
        body = out["author"] if call.agent == "research" else PASS
        return LLMResult(call_id=call.call_id, provider=call.provider, model="m", output=body, input_tokens=1,
                         output_tokens=1, cached_tokens=0, cost_usd=0.4, duration_ms=1, session_id=None)
    monkeypatch.setattr(executor, "run_with_retry", run)
    monkeypatch.setattr(company_graph.evidence, "verify_sources", lambda ids: None)
    return out


def leaf(s, project, **kw) -> int:
    s.commit()
    with session_scope() as s2:
        t = Task(project_id=project.id, key=kw.pop("key", "leaf"), title="t", owner="research", objective="o",
                 acceptance_criteria=kw.pop("acceptance_criteria", ["c"]), status=kw.pop("status", "READY"), max_iterations=2,
                 family="market_research", **kw)
        s2.add(t)
        s2.flush()
        return t.id


def calls(tid: int) -> list[tuple[str, str]]:
    with session_scope() as s:
        return [(r.agent, r.purpose) for r in s.scalars(select(LLMCallRecord).where(LLMCallRecord.task_id == tid)
                                                         .order_by(LLMCallRecord.id))]


def crew_turns(tid: int) -> list[tuple[str, bool]]:
    with session_scope() as s:
        return [(x.duty, x.shadow) for x in s.scalars(select(CrewTurn).join(Crew, Crew.id == CrewTurn.crew_id).where(
            Crew.subject_kind == "task", Crew.subject_id == tid).order_by(CrewTurn.id))]


def audits(tid: int) -> list[Job]:
    with session_scope() as s:
        return list(s.scalars(select(Job).where(Job.kind == "audit_task", Job.payload["task_id"].as_integer() == tid)))


def work(project, tid):
    company_graph.work({"project_id": project.id, "task_id": tid, "batch": [tid]})


@pytest.fixture
def sampled(monkeypatch):
    monkeypatch.setitem(staffing_config()["staffing"], "audit_rate", 1.0)


# ------------------------------------------------------------------ the alone path

def test_a_small_leaf_finishes_after_one_author_call_and_no_review(s, project, model):
    tid = leaf(s, project)

    work(project, tid)

    assert calls(tid) == [("research", "work")]
    assert crew_turns(tid) == [("author", False)]
    with session_scope() as s2:
        t = s2.get(Task, tid)
        assert t.status == "DONE"
        assert t.review == {"verdict": "unchecked", "reviewer": None, "outcome": "pass", "criteria_unmet": []}
        assert s2.get(Evidence, t.result["evidence_ids"][0]).kind == "sourced_claim", "never promoted"


@pytest.fixture
def two_alone(monkeypatch):
    """A two-criterion leaf may run alone, so its author can meet one criterion and miss the other."""
    monkeypatch.setitem(staffing_config()["staffing"], "solo_max_criteria", 2)


def test_an_alone_task_whose_author_lists_unmet_criteria_reads_as_partial_to_the_project_managers_review(
        s, project, model, two_alone):
    model["author"] = result(unmet=["Only two of three competitors priced"])
    tid = leaf(s, project, acceptance_criteria=["prices", "competitors"])

    work(project, tid)

    with session_scope() as s2:
        t = s2.get(Task, tid)
        assert (t.status, t.review["outcome"]) == ("DONE", "partial")
        overview = ctx.results_overview(s2, s2.get(Project, project.id))
    assert "outcome: partial" in overview and "Only two of three competitors priced" in overview


def test_an_alone_task_that_meets_no_criterion_fails_like_a_checked_one(s, project, model):
    """Its author missed its one criterion. A checked task in that state fails (`review_outcome`), so the owner's retry
    and the list of failed work see it; an alone one must not read as done."""
    model["author"] = result(unmet=["No competitor priced"])
    tid = leaf(s, project)

    work(project, tid)

    with session_scope() as s2:
        t = s2.get(Task, tid)
        assert (t.status, t.review["verdict"], t.review["outcome"]) == ("FAILED", "unchecked", "unmet")
        assert "No competitor priced" in t.error and "review" not in t.error.lower(), "no review was due"
        assert control.retry_failed_tasks(s2, project.id) == [tid]


def test_a_task_whose_result_drafts_a_card_for_the_owner_gets_a_checker(s, project, model, sampled):
    model["author"] = result(requests=[CARD])
    tid = leaf(s, project)

    work(project, tid)

    assert [a for a, p in calls(tid) if p == "review"] == ["risk"]
    with session_scope() as s2:
        crew = staffing.task_crew(s2, tid)
        assert [x["why"]["rule"] for x in crew.amendments] == ["check.owner_card"]
        assert s2.get(Task, tid).review["reviewer"] == "risk"
    assert audits(tid) == [], "a checked task is never audited"


def test_an_alone_result_with_a_financial_model_gets_a_checker_for_the_computed_figures(s, project, model):
    """The finance engine turns the model into calculation evidence, and a figure nobody checked would reach the
    report as computed."""
    scenario = {"name": "base", "price_per_customer_month": 10.0, "customers_month_1": 5.0,
                "monthly_customer_growth_rate": 0.05, "monthly_churn_rate": 0.02,
                "variable_cost_per_customer_month": 2.0, "fixed_cost_month": 100.0,
                "acquisition_cost_per_customer": 20.0, "months": 12, "assumption_notes": []}
    model["author"] = {**result(), "financial_model": {"currency": "USD", "scenarios": [scenario]}}
    tid = leaf(s, project)

    work(project, tid)

    assert [a for a, p in calls(tid) if p == "review"] == ["risk"]
    with session_scope() as s2:
        crew = staffing.task_crew(s2, tid)
        assert [(x["why"]["rule"], x["why"]["facts"]) for x in crew.amendments] == [("check.calculation",
                                                                                       {"scenarios": 1})]
        assert s2.get(Task, tid).review["reviewer"] == "risk"


def test_a_failed_alone_task_the_owner_retries_is_checked_the_second_time(s, project, model, sampled):
    """A task run again has been authored, so `check.resumed` holds for it now. The rule is not rerun: the crew keeps
    the slots it was placed with, and the checker is an amendment."""
    model["author"] = result(unmet=["No competitor priced"])
    tid = leaf(s, project)
    work(project, tid)
    with session_scope() as s2:
        control.retry_task(s2, tid)

    audit.run(tid)
    model["author"] = result()
    work(project, tid)

    assert [p for _, p in calls(tid)] == ["work", "work", "review"], "no shadow check of a task that now has a checker"
    with session_scope() as s2:
        crew = staffing.task_crew(s2, tid)
        assert [x["duty"] for x in crew.slots] == ["author"]
        assert [(x["slot"]["role"], x["why"]["rule"]) for x in crew.amendments] == [("risk", "check.resumed")]
        assert s2.get(Task, tid).review["reviewer"] == "risk"


def test_a_dependent_is_told_its_input_was_not_reviewed(s, project, model, two_alone):
    model["author"] = result(unmet=["No price for the third"])
    tid = leaf(s, project, acceptance_criteria=["prices", "competitors"])
    work(project, tid)
    with session_scope() as s2:
        later = Task(project_id=project.id, key="later", title="later", owner="strategy", objective="o",
                     acceptance_criteria=["c"], depends_on=[tid], status="PROPOSED")
        s2.add(later)
        s2.flush()

        said = ctx.dependency_outputs(s2, later)

    assert "Not reviewed" in said and "Its author says unmet: No price for the third" in said
    assert "The reviewer found unmet" not in said


def test_the_task_document_says_it_was_not_reviewed_instead_of_naming_a_reviewer(s, project, model, two_alone):
    model["author"] = result(unmet=["No price for the third"])
    tid = leaf(s, project, acceptance_criteria=["prices", "competitors"])

    work(project, tid)

    with session_scope() as s2:
        doc = registry.read(s2.get(Artifact, s2.get(Task, tid).artifact_id).path)
    assert "no second role checked it" in doc and "Reviewed by" not in doc and "Independent review" not in doc
    assert "Not met: No price for the third" in doc


# ------------------------------------------------------------------ the shadow check

def test_an_alone_task_in_the_sample_queues_one_shadow_check_beside_the_run(s, project, model, sampled):
    tid = leaf(s, project)

    work(project, tid)

    (job,) = audits(tid)
    assert job.lock_key == f"audit:{project.id}", "the check never waits for the run, nor holds it"


def test_the_sample_is_drawn_where_the_check_is_queued(s, project):
    tid = leaf(s, project)
    with session_scope() as s2:
        t = s2.get(Task, tid)
        crew = staffing.ensure(s2, tid, staffing.task_facts(s2, t), staffing.load_limits())

        assert staffing.enqueue_audit(s2, crew, t, draw=lambda: 0.99) is None
        assert staffing.enqueue_audit(s2, crew, t, draw=lambda: 0.0) is not None


def test_the_shadow_check_of_a_task_is_queued_once_however_often_its_step_runs(s, project):
    """A replay of `work` that reaches the queuing step again finds the job by its key."""
    tid = leaf(s, project)
    for _ in range(2):
        with session_scope() as s2:
            t = s2.get(Task, tid)
            crew = staffing.ensure(s2, tid, staffing.task_facts(s2, t), staffing.load_limits())
            staffing.enqueue_audit(s2, crew, t, draw=lambda: 0.0)

    assert len(audits(tid)) == 1


def test_an_audit_budget_of_zero_turns_the_shadow_checks_off(s, project, monkeypatch):
    monkeypatch.setitem(staffing_config()["staffing"], "audit_budget_usd", 0)
    tid = leaf(s, project)
    with session_scope() as s2:
        t = s2.get(Task, tid)
        crew = staffing.ensure(s2, tid, staffing.task_facts(s2, t), staffing.load_limits())

        assert staffing.enqueue_audit(s2, crew, t, draw=lambda: 0.0) is None, "no job the budget would only refuse"


def test_a_task_amended_with_a_checker_no_longer_counts_as_alone(s, project):
    """The round's mean per task leaves out alone tasks only; one that got a checker later was reviewed."""
    tid = leaf(s, project)
    with session_scope() as s2:
        t = s2.get(Task, tid)
        crew = staffing.ensure(s2, tid, staffing.task_facts(s2, t), staffing.load_limits())
        assert tid in s2.scalars(staffing.alone_task_ids()).all()

        staffing.amend(s2, crew, staffing.Slot(staffing.Duty.VERIFIER, "risk", None),
                       staffing.Reason("check.owner_card", {"requests": 1}))

        assert tid not in s2.scalars(staffing.alone_task_ids()).all()


def test_a_replay_of_an_alone_task_leaves_one_crew_one_turn_and_one_check(s, project, model, sampled, monkeypatch):
    tid = leaf(s, project)
    real, crashed = company_graph._finish_unchecked, []

    def finish_then_crash(*a, **kw):
        real(*a, **kw)
        if not crashed:
            crashed.append(1)
            raise Crash()   # the finish and the queued check were written, and the transaction never commits
    monkeypatch.setattr(company_graph, "_finish_unchecked", finish_then_crash)

    with pytest.raises(Crash):
        work(project, tid)
    work(project, tid)

    assert crew_turns(tid) == [("author", False)] and calls(tid) == [("research", "work")]
    assert len(audits(tid)) == 1
    with session_scope() as s2:
        assert s2.scalar(select(func.count()).select_from(Crew).where(Crew.subject_kind == "task",
                                                                       Crew.subject_id == tid)) == 1
        assert s2.get(Task, tid).status == "DONE"


def test_a_shadow_check_gates_nothing_promotes_nothing_and_leaves_the_task_and_the_round_as_they_were(
        s, project, model, sampled):
    tid = leaf(s, project)
    work(project, tid)
    with session_scope() as s2:
        t = s2.get(Task, tid)
        before = (dict(t.review), t.status, t.spent_usd, s2.get(Evidence, t.result["evidence_ids"][0]).kind)
        cap = budget.task_cap(t)
        room = rounds.envelope(s2, s2.get(Project, project.id))

    audit.run(tid)

    assert crew_turns(tid) == [("author", False), ("verifier", True)]
    assert calls(tid) == [("research", "work"), ("risk", "audit")]
    with session_scope() as s2:
        t = s2.get(Task, tid)
        assert (t.review, t.status, t.spent_usd, s2.get(Evidence, t.result["evidence_ids"][0]).kind) == before
        assert budget.task_cap(t) == cap
        assert rounds.envelope(s2, s2.get(Project, project.id)) == room, "the round's room is unchanged"
        assert s2.get(Project, project.id).spent_usd == pytest.approx(0.8), "the project still pays for it"
        (seen,) = [v.outcome for v in staffing.crew_views(s2, [project.id], "en") if v.subject.id == tid]
    assert (seen.calls, seen.audit.calls) == (1, 1), "the task still shows one call; its audit has its own line"
    assert (seen.cost_usd, seen.audit.cost_usd) == (pytest.approx(0.4), pytest.approx(0.4))


def test_a_second_run_of_the_check_makes_no_second_call(s, project, model, sampled):
    tid = leaf(s, project)
    work(project, tid)

    audit.run(tid)
    audit.run(tid)

    assert [p for _, p in calls(tid)].count("audit") == 1


def test_a_check_that_would_pass_the_audit_budget_is_not_taken(s, project, model, sampled, monkeypatch):
    monkeypatch.setitem(staffing_config()["staffing"], "audit_budget_usd", 0.1)
    tid = leaf(s, project)
    work(project, tid)

    audit.run(tid)

    assert [p for _, p in calls(tid)] == ["work"] and crew_turns(tid) == [("author", False)]


def test_a_checked_task_is_never_audited(s, project, model):
    model["author"] = result(requests=[CARD])
    tid = leaf(s, project)
    work(project, tid)

    audit.run(tid)

    assert "audit" not in [p for _, p in calls(tid)]


# ------------------------------------------------------------------ the round

def test_the_round_admits_as_many_tasks_with_alone_tasks_as_without(s, project):
    """Cheap alone tasks of an earlier round would pull the project's mean per task down and widen this round."""
    project.budget_usd, project.plan_round = 40, 1
    s.flush()
    ready = [leaf(s, project, key=f"r{i}", plan_round=1) for i in range(8)]
    for i in range(3):
        leaf(s, project, key=f"c{i}", acceptance_criteria=["a", "b"], spent_usd=1.5, status="DONE")
    staff(project)
    without = admitted(project, ready)

    for i in range(5):
        leaf(s, project, key=f"a{i}", spent_usd=0.05, status="DONE")
    staff(project)

    with session_scope() as s2:
        assert rounds.envelope(s2, s2.get(Project, project.id)).per_task_usd == pytest.approx(1.5)
    assert admitted(project, ready) == without


def test_an_audit_call_leaves_the_rounds_room_where_the_budget_left_decides_it(s, project, model, sampled):
    """Earlier rounds spent most of the budget, so what is left sets the room, not the round's share. A shadow check
    is paid from the audit budget kept back for it, so it does not narrow the room."""
    project.budget_usd, project.plan_round, project.spent_usd = 40, 2, 24.0
    leaf(s, project, key="earlier", spent_usd=24.0, status="DONE", plan_round=1)
    tid = leaf(s, project, plan_round=2)
    work(project, tid)
    with session_scope() as s2:
        room = rounds.envelope(s2, s2.get(Project, project.id))
    assert room.limit_usd < limits()["round_budget_share"] * 40

    audit.run(tid)

    assert [p for _, p in calls(tid)] == ["work", "audit"]
    with session_scope() as s2:
        assert rounds.envelope(s2, s2.get(Project, project.id)) == room


def test_the_round_never_counts_on_money_a_shadow_check_took(s, project):
    """Checks that cost more than the audit budget still spent the project's money: the room left to the round is
    never more than the budget left after the calls that close the run."""
    project.budget_usd, project.plan_round, project.spent_usd = 40, 2, 30.0
    leaf(s, project, key="earlier", spent_usd=25.0, status="DONE", plan_round=1)
    with session_scope() as s2:
        s2.add(LLMCallRecord(call_id=f"audit-{project.id}", project_id=project.id, agent="risk", purpose="audit",
                             provider="claude", status="ok", cost_usd=5.0))

    with session_scope() as s2:
        p = s2.get(Project, project.id)
        room = rounds.envelope(s2, p)
        closing = sum(usage.purpose(x).reserve_usd for x in ("plan", "decision", "report"))
        assert room.limit_usd - room.spent_usd <= p.budget_usd - p.spent_usd - p.reserved_usd - closing + 0.005


def staff(project) -> None:
    with session_scope() as s2:
        for t in s2.scalars(select(Task).where(Task.project_id == project.id, Task.status == "DONE")):
            staffing.ensure(s2, t.id, staffing.task_facts(s2, t), staffing.load_limits())
        p = s2.get(Project, project.id)
        p.spent_usd = sum(t.spent_usd for t in s2.scalars(select(Task).where(Task.project_id == project.id)))


def admitted(project, ready: list[int]) -> int:
    with session_scope() as s2:
        return len(rounds.admit(s2, s2.get(Project, project.id), [s2.get(Task, i) for i in ready])[0])


# ------------------------------------------------------------------ the other readers of tasks.review

def test_the_skill_builder_reads_review_findings_and_never_an_alone_authors_own_unmet_list(s, project, monkeypatch):
    from justshipit.capability import factory
    from justshipit.contracts.skills import SkillPackageOut, SkillSafetyOut
    from justshipit.db.models import Skill

    for key, review in (("checked", {"verdict": "partial", "reviewer": "risk", "criteria_unmet": ["Reviewer found"]}),
                        ("alone", {"verdict": "unchecked", "reviewer": None, "outcome": "partial",
                                   "criteria_unmet": ["Author said"]})):
        s.add(Task(project_id=project.id, key=key, title=key, owner="research", objective="o",
                   acceptance_criteria=["c"], status="DONE", review=review))
    prop = dict(name="probe-skill", kind="knowledge", target_agents=["research"], problem="p", expected_benefit="b",
                risks=[], evaluation_plan="e")
    skill = Skill(name="probe-skill", kind="knowledge", status="CANDIDATE", owner_agent="skill_builder", version=1,
                  target_agents=["research"], proposal=prop)
    s.add(skill)
    s.commit()
    asked = []

    def agent(role, purpose, prompt, schema, **kw):
        asked.append(prompt)
        if schema is SkillPackageOut:
            return SkillPackageOut(name="probe-skill", description="d", eval_cases=[], files=[])
        return SkillSafetyOut(safe=False, issues=["stop here"])
    monkeypatch.setattr(factory, "run_agent", agent)

    factory.build_and_evaluate(skill.id)

    assert "Reviewer found" in asked[0] and "Author said" not in asked[0]


def test_an_alone_task_is_never_contested_so_no_meeting_is_called_for_it(s, project, model):
    tid = leaf(s, project)

    work(project, tid)

    with session_scope() as s2:
        assert s2.get(Task, tid).contested is False


def test_the_document_of_an_alone_task_that_met_everything_has_no_review_section(s, project, model):
    tid = leaf(s, project)

    work(project, tid)

    with session_scope() as s2:
        doc = registry.read(s2.get(Artifact, s2.get(Task, tid).artifact_id).path)
    assert "no second role checked it" in doc and "Independent review" not in doc
