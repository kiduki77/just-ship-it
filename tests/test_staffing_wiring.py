"""The running graph staffs each task through its crew (docs/design/staffing-structure.md, section 4.2): one crew when
`work` first sees the task, one turn for each authoring pass and each review, keyed by the ledger call, and the review
by the crew's checker. The model is a stand-in below run_agent, so every call is a real ledger row."""
from dataclasses import replace

import pytest
from justshipit import staffing
from justshipit.agents import executor
from justshipit.api import console
from justshipit.db.models import Crew, CrewTurn, LLMCallRecord, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import company_graph
from justshipit.runtime.base import LLMResult
from justshipit.services import stops
from justshipit.settings import staffing_config
from sqlalchemy import select

pytestmark = pytest.mark.usefixtures("owner_asked")


@pytest.fixture(autouse=True)
def always_check(monkeypatch):
    monkeypatch.setitem(staffing_config()["staffing"], "always_check", True)

RESULT = {"summary": "s", "findings_markdown": "f", "evidence": [], "assumptions": [], "risks": [], "open_questions": [],
          "unmet_criteria": [], "proposed_follow_up_tasks": [], "requests": [], "capability_gaps": [],
          "financial_model": None}


def review(verdict: str, unmet: list[str], fixed: bool | None = None) -> dict:
    return {"verdict": verdict, "criteria_met": [] if unmet else ["c"], "criteria_unmet": unmet, "notes": [],
            "previous_unmet": [] if fixed is None else [{"number": 1, "fixed": fixed}], "claim_checks": [],
            "counter_arguments": [], "rework_instructions": "fix it" if verdict == "rework" else None,
            "contested": False}


class Crash(BaseException):
    """The worker died mid-step: nothing in `work` catches it, so the checkpoint replays the node."""


@pytest.fixture
def model(monkeypatch):
    """The answers each call gets in turn: the author's result, or the next review (a Crash is raised instead)."""
    reviews: list = []

    def run(call):
        out = RESULT if call.agent == "research" else reviews.pop(0)
        if isinstance(out, BaseException):
            raise out
        return LLMResult(call_id=call.call_id, provider=call.provider, model="m", output=out, input_tokens=1,
                         output_tokens=1, cached_tokens=0, cost_usd=0.01, duration_ms=1, session_id=None)
    monkeypatch.setattr(executor, "run_with_retry", run)
    monkeypatch.setattr(company_graph.evidence, "verify_sources", lambda ids: None)
    return reviews


def leaf(s, project) -> int:
    s.commit()
    with session_scope() as s2:
        t = Task(project_id=project.id, key="leaf", title="t", owner="research", objective="o",
                 acceptance_criteria=["c"], status="READY", max_iterations=2, family="market_research")
        s2.add(t)
        s2.flush()
        return t.id


def crews_of(tid: int) -> list[Crew]:
    with session_scope() as s:
        return list(s.scalars(select(Crew).where(Crew.subject_kind == "task", Crew.subject_id == tid)))


def turns_of(tid: int) -> list[tuple[str, str, int, str]]:
    with session_scope() as s:
        return [(x.duty, x.role, x.iteration, x.call_id) for x in s.scalars(
            select(CrewTurn).join(Crew, Crew.id == CrewTurn.crew_id)
            .where(Crew.subject_kind == "task", Crew.subject_id == tid).order_by(CrewTurn.id))]


def calls_of(tid: int, purpose: str) -> list[tuple[str, str]]:
    with session_scope() as s:
        return [(r.agent, r.call_id) for r in s.scalars(select(LLMCallRecord).where(
            LLMCallRecord.task_id == tid, LLMCallRecord.purpose == purpose).order_by(LLMCallRecord.id))]


def test_a_task_gets_one_crew_one_turn_per_pass_and_one_review_call_per_pass_by_risk(s, project, model):
    tid = leaf(s, project)
    model += [review("rework", ["x is missing"]), review("pass", [], fixed=True)]

    company_graph.work({"project_id": project.id, "task_id": tid, "batch": [tid]})

    (crew,) = crews_of(tid)
    assert crew.family == "market_research"
    assert [(x["duty"], x["role"]) for x in crew.slots] == [("author", "research"), ("verifier", "risk")]
    assert "check.config" in [x["rule"] for x in crew.why], "always_check keeps the checker on every task"
    reviews, works = calls_of(tid, "review"), calls_of(tid, "work")
    assert [a for a, _ in reviews] == ["risk", "risk"] and len(works) == 2, "one review call per authoring pass"
    assert turns_of(tid) == [("author", "research", 1, works[0][1]), ("verifier", "risk", 1, reviews[0][1]),
                             ("author", "research", 2, works[1][1]), ("verifier", "risk", 2, reviews[1][1])]


def test_a_turn_keeps_the_whole_review_the_independence_label_and_what_the_rework_answered(s, project, model):
    tid = leaf(s, project)
    model += [review("rework", ["x is missing"]), review("pass", [], fixed=True)]

    company_graph.work({"project_id": project.id, "task_id": tid})

    with session_scope() as s2:
        records = [x.record for x in s2.scalars(select(CrewTurn).where(CrewTurn.crew_id == crews_of(tid)[0].id)
                                                 .order_by(CrewTurn.id))]
    assert records[1]["review"]["criteria_unmet"] == ["x is missing"]
    assert records[1]["independence"].startswith("review by risk")
    assert records[0]["fixing"] == "" and "x is missing" in records[2]["fixing"]


def test_a_replay_after_a_crash_mid_review_leaves_one_crew_and_no_second_authoring_turn(s, project, model):
    tid = leaf(s, project)
    model += [Crash(), review("pass", [])]

    with pytest.raises(Crash):
        company_graph.work({"project_id": project.id, "task_id": tid})
    company_graph.work({"project_id": project.id, "task_id": tid})

    assert len(crews_of(tid)) == 1
    assert [(d, i) for d, _, i, _ in turns_of(tid)] == [("author", 1), ("verifier", 1)]
    with session_scope() as s2:
        assert s2.get(Task, tid).status == "DONE"


def test_ensure_twice_from_the_same_step_leaves_one_crew(s, project):
    tid = leaf(s, project)
    for _ in range(2):
        with session_scope() as s2:
            t = s2.get(Task, tid)
            staffing.ensure(s2, tid, staffing.task_facts(s2, t), staffing.load_limits())

    assert len(crews_of(tid)) == 1


# ------------------------------------------------------------------ the readers that have a task

ALONE = staffing.Limits(always_check=False, solo_max_criteria=1, audit_rate=0.2, audit_budget_usd=2.0, min_samples=20,
                        solo_max_unmet_rate=0.1, model_tolerance=0.05, explore_rate=0.1)


@pytest.mark.parametrize(("limits", "waits_on"), [(ALONE, None), (replace(ALONE, always_check=True), "risk")])
def test_a_result_in_review_waits_on_its_crews_checker_and_on_nobody_when_it_has_none(s, project, limits, waits_on):
    tid = leaf(s, project)
    with session_scope() as s2:
        t = s2.get(Task, tid)
        staffing.ensure(s2, tid, staffing.task_facts(s2, t), limits)
        t.status = "REVIEW"
        s2.flush()
        held = stops.Stops(None, {"risk": object(), "strategy": object()})

        assert stops.waiting_on(t, held) == waits_on


def test_the_task_page_names_the_crews_checker_and_the_one_the_rule_would_place_before_it_starts(s, project):
    tid = leaf(s, project)
    assert console.task_detail(tid)["reviewer"] == "risk"

    with session_scope() as s2:
        staffing.ensure(s2, tid, staffing.task_facts(s2, s2.get(Task, tid)), ALONE)

    assert console.task_detail(tid)["reviewer"] is None
