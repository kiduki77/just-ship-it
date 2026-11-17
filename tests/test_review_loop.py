"""Review and rework (persona round 3, 2026-11-05): 14 of the five businesses' tasks failed and took 42% of their task
spend, plus about $10.8 to redo them. 12 stopped at attempt 2 of 3 because the attempt-2 reviewer never saw its own
attempt-1 review, found new points, and the stop rule read a list as long as before as no progress. Tool limits
counted as unmet, so they never cleared. An analyst's third attempt ran past its task's hard cap (T-94)."""
import itertools

from sqlalchemy import select

from justshipit.agents.independence import Served
from justshipit.contracts.requests import AgentAskDraft, RequestType, RiskLevel
from justshipit.contracts.work import AgentResultOut, PreviousUnmetOut, ReviewOut, ReviewVerdict, TaskDraft
from justshipit.db.models import AgentRequest, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import company_graph
from justshipit.services import budget, tasks
from ask_kit import contract

CARD = AgentAskDraft(type=RequestType.APPROVAL, title="Send the data request to Daon Mall", summary="s",
                         rationale="r", options=[], recommended_option=None, action_kind="internal.other",
                         estimated_cost_usd=None, risk_level=RiskLevel.LOW, context=contract())
CRITERIA = ["A set menu", "A weekly flow", "Five reply templates", "A repeat-customer count"]


def _result() -> AgentResultOut:
    return AgentResultOut(summary="s", findings_markdown="f", evidence=[], assumptions=[], risks=[], open_questions=[],
                          proposed_follow_up_tasks=[], requests=[CARD], capability_gaps=[], financial_model=None)


def _review(verdict: ReviewVerdict, unmet: list[str], *, met: int = 1, notes: list[str] = (),
            fixed: dict[int, bool] | None = None) -> ReviewOut:
    return ReviewOut(verdict=verdict, criteria_met=CRITERIA[:met], criteria_unmet=unmet, notes=list(notes),
                     previous_unmet=[PreviousUnmetOut(number=n, fixed=f) for n, f in (fixed or {}).items()],
                     claim_checks=[], counter_arguments=[],
                     rework_instructions="fix" if verdict == ReviewVerdict.REWORK else None, contested=False)


def _run(s, project, monkeypatch, reviews: list[ReviewOut], *, max_iterations: int = 2, attempt_usd: float = 0.0,
         owner: str = "product") -> dict:
    """Run one task through authoring and review. Each authoring pass costs `attempt_usd`."""
    s.commit()
    seen = {"authored": 0, "review_prompts": []}
    queue = itertools.chain(reviews, itertools.repeat(reviews[-1]))

    def agent(name, purpose, prompt, model, **kw):
        if model is AgentResultOut:
            seen["authored"] += 1
            with session_scope() as s2:
                s2.get(Task, kw["task_id"]).spent_usd += attempt_usd
            return _result()
        seen["review_prompts"].append(prompt)
        return next(queue)

    monkeypatch.setattr(company_graph, "last_served", lambda: Served("claude", "m"))
    monkeypatch.setattr(company_graph.evidence, "verify_sources", lambda ids: None)
    monkeypatch.setattr(company_graph, "run_agent", agent)
    with session_scope() as s2:
        t = Task(project_id=project.id, key="set_and_ops", title="Set menu and weekly flow", owner=owner,
                 objective="o", acceptance_criteria=CRITERIA, status="READY", max_iterations=max_iterations,
                 budget_usd=3.0)
        s2.add(t)
        s2.flush()
        tid = t.id
    company_graph.work({"project_id": project.id, "task_id": tid})
    company_graph.schedule({"project_id": project.id})
    with session_scope() as s2:
        t = s2.get(Task, tid)
        cards = s2.scalars(select(AgentRequest).where(AgentRequest.task_id == tid)).all()
        return {**seen, "status": t.status, "verdict": (t.review or {}).get("verdict"), "error": t.error,
                "cards": [c.title for c in cards]}


def test_the_second_review_judges_its_own_first_review_first(s, project, monkeypatch):
    out = _run(s, project, monkeypatch, [
        _review(ReviewVerdict.REWORK, ["The weekly total adds to 95-110 minutes, not 2.5 hours"]),
        _review(ReviewVerdict.PASS, [], met=4, fixed={1: True})])
    first, second = out["review_prompts"]
    assert "[U1]" not in first
    assert "[U1] The weekly total adds to 95-110 minutes, not 2.5 hours" in second
    assert (out["status"], out["verdict"]) == ("DONE", "pass")


def test_a_rework_that_fixed_what_was_asked_goes_on_whatever_new_points_the_review_found(s, project, monkeypatch):
    """T-92: attempt 2 fixed the one point of attempt 1, and its review found two new ones. The old rule read 2 >= 1
    as no progress and failed the task."""
    out = _run(s, project, monkeypatch, [
        _review(ReviewVerdict.REWORK, ["Unclaimed sets have no rule for 8 pm"], met=3),
        _review(ReviewVerdict.REWORK, ["Rule 3 sells at 7:45 what reply 4 holds until 8", "Reply 3 has no time"],
                met=3, fixed={1: True}),
        _review(ReviewVerdict.PASS, [], met=4, fixed={1: True, 2: True})])
    assert out["authored"] == 3
    assert (out["status"], out["verdict"], out["cards"]) == ("DONE", "pass", [CARD.title])


def test_a_rework_that_fixed_nothing_stops_and_fails_when_most_criteria_are_unmet(s, project, monkeypatch):
    out = _run(s, project, monkeypatch, [
        _review(ReviewVerdict.REWORK, ["No café was checked this round", "No cluster was built"], met=1),
        _review(ReviewVerdict.REWORK, ["Only 4 cafés were checked", "Clusters still unbuilt"], met=1,
                fixed={1: False, 2: False})])
    assert out["authored"] == 2
    assert (out["status"], out["verdict"], out["cards"]) == ("FAILED", "unmet", [])
    assert "Only 4 cafés were checked" in out["error"]


def test_a_stopped_rework_that_met_most_criteria_is_kept_as_partial_and_raises_no_card(s, project, monkeypatch):
    """T-81: attempt 3 of 3 met four criteria, missed two, and failed; its redo then passed first time."""
    out = _run(s, project, monkeypatch, [
        _review(ReviewVerdict.REWORK, ["Thursday cooking time is left out of the total"], met=3)], max_iterations=1)
    assert out["authored"] == 2
    assert (out["status"], out["verdict"], out["cards"]) == ("DONE", "partial", [])
    with session_scope() as s2:
        t = s2.scalars(select(Task).where(Task.project_id == project.id)).one()
        assert t.review["criteria_unmet"] == ["Thursday cooking time is left out of the total"]


def test_a_tool_limit_is_a_note_that_never_sends_the_work_back(s, project, monkeypatch):
    """T-62 and T-97: '(tool limit)' items counted as unmet, so no rework could ever clear them."""
    note = "Smartstore's export columns can't be checked from the web (tool limit)"
    out = _run(s, project, monkeypatch, [_review(ReviewVerdict.REWORK, [], met=4, notes=[note])])
    assert out["authored"] == 1
    assert (out["status"], out["verdict"], out["cards"]) == ("DONE", "partial", [CARD.title])
    with session_scope() as s2:
        t = s2.scalars(select(Task).where(Task.project_id == project.id)).one()
        assert t.review["notes"] == [note] and t.review["criteria_unmet"] == []


def test_a_rework_verdict_that_lists_nothing_is_unmet_not_a_pass(s, project, monkeypatch):

    """The reviewer said rework and listed no unmet criterion and no note: the outcome read only the empty lists, so

    the task passed and its held card went to the owner."""

    out = _run(s, project, monkeypatch, [_review(ReviewVerdict.REWORK, [], met=4)])

    assert (out["status"], out["verdict"], out["cards"]) == ("FAILED", "unmet", [])





def test_an_attempt_that_would_end_past_the_hard_cap_does_not_start(s, project, monkeypatch):
    """T-94: $3.08 spent in two attempts of a $3 task (cap $4.50); the third was cut off at the cap with nothing."""
    rework = _review(ReviewVerdict.REWORK, ["The Daon Mall pick breaks its own rule"], met=3)
    out = _run(s, project, monkeypatch, [rework], attempt_usd=2.5)
    assert out["authored"] == 1
    assert (out["status"], out["verdict"]) == ("DONE", "partial")
    assert out["error"] is None


def test_an_analyst_task_has_room_for_three_of_its_costliest_attempts(s, project):
    """Analyst attempts with their review cost $1.35 median and $1.85 at most (18 attempts to 2026-11-05)."""
    made, _ = tasks.create_from_drafts(s, project, [
        TaskDraft(key=k, title=k, owner=k, objective="o", acceptance_criteria=["c"], depends_on=[])
        for k in ("analyst", "research")], origin="owner", plan_round=1)
    by_owner = {t.owner: t for t in made}
    assert budget.task_cap(by_owner["analyst"]) >= 3 * 1.85
    assert by_owner["research"].budget_usd == 3.0
