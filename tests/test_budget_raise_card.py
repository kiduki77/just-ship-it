"""A card that asks for more budget raises the project's budget when the owner approves it, and starts again the run the
budget stopped. A card that only asked the owner to pay for something still changes no budget. The round-2 reader met
"Add $4 ... the project resumes" beside "It doesn't raise the project's budget"."""
import pytest
from justshipit.contracts.requests import HumanDecision, HumanResponse, RequestStatus
from justshipit.db.models import Job
from justshipit.services import requests
from justshipit.services import control
from owner_task_kit import approve, draft
from sqlalchemy import select
from ask_kit import by_project_manager

pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def stopped(s, project, spent=4.9):
    project.status, project.error, project.spent_usd = "FAILED", "BudgetExceeded: project budget 10 USD-eq", spent
    s.commit()


def ask(s, project, amount=4.0, **kw):
    req = requests.create(s, draft(action_kind="budget.raise", estimated_cost_usd=amount, title="Add $4 to the test",
                                   **kw), from_agent="finance", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    return req


def decide(s, req, **kw):
    approve(s, req, **kw)
    s.commit()


def jobs(s, kind, **payload):
    s.expire_all()
    return [j for j in s.scalars(select(Job).where(Job.kind == kind)) if all(j.payload.get(k) == v for k, v in payload.items())]


def run_raise(request_id: int) -> None:
    control.raise_for_request_id(request_id)


def test_approving_raises_the_budget_by_the_amount_and_starts_the_stopped_run_again(s, project):
    stopped(s, project)
    req = ask(s, project)
    assert req.status == RequestStatus.PENDING.value
    decide(s, req)
    queued = jobs(s, "raise_budget", request_id=req.id)
    assert len(queued) == 1, "approving queues the raise once"

    run_raise(req.id)

    s.refresh(project)
    assert project.budget_usd == 14.0
    change = project.context["budget_changes"][-1]
    assert (change["from"], change["to"], change["request_id"]) == (10, 14.0, req.id)
    assert jobs(s, "retry_project", project_id=project.id), "the run the budget stopped starts again"


def test_a_job_that_runs_twice_adds_the_amount_once(s, project):
    stopped(s, project)
    req = ask(s, project)
    decide(s, req)

    run_raise(req.id)
    run_raise(req.id)

    s.refresh(project)
    assert project.budget_usd == 14.0 and len(project.context["budget_changes"]) == 1


@pytest.mark.parametrize("decision", [HumanDecision.REJECT, HumanDecision.DEFER])
def test_declining_changes_nothing(s, project, decision):
    stopped(s, project)
    req = ask(s, project)

    requests.respond(s, req.id, HumanResponse(decision=decision))
    s.commit()

    s.refresh(project)
    assert project.budget_usd == 10 and not (project.context or {}).get("budget_changes")
    assert not jobs(s, "raise_budget", request_id=req.id) and not jobs(s, "retry_project", project_id=project.id)


def test_the_job_refuses_a_card_the_owner_has_not_approved(s, project):
    req = ask(s, project)
    with pytest.raises(PermissionError):
        run_raise(req.id)
    s.refresh(project)
    assert project.budget_usd == 10


def test_the_card_says_what_approving_adds_and_that_the_run_starts_again(s, project):
    text = requests.outcome(s, ask(s, project), "en").says
    assert "Adds $4 to this project's budget" in text and "starts again" in text
    assert "doesn't raise" not in text


def test_a_payment_card_still_changes_no_budget(s, project):
    req = requests.create(s, draft(action_kind="budget.spend", estimated_cost_usd=4.0), from_agent="finance",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    decide(s, req, confirm_text=f"REQ-{req.id}")
    assert not jobs(s, "raise_budget", request_id=req.id)
    s.refresh(project)
    assert project.budget_usd == 10


def test_a_raise_with_no_amount_asks_the_owner_for_nothing_to_do(s, project):
    req = ask(s, project, amount=None)
    decide(s, req)
    assert not jobs(s, "raise_budget", request_id=req.id)


def test_the_policy_makes_it_the_owners_call_and_no_door_rule_lets_the_project_manager_decide_it():
    from justshipit.services import policy
    assert "budget.raise" not in policy.PROJECT_MANAGER_DECIDABLE
    assert str(policy.action_mode("budget.raise")) in ("approve", "PolicyMode.APPROVE")
