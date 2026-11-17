"""The owner's decisions on decision safety (2026-11-09): money going out on an action the company has no rule for
takes the decision's number typed in, on the server as on the console; an approved spend becomes the owner's payment
to-do; a held or declined decision can be reopened while nothing has followed from it."""
import json
from datetime import UTC

import pytest
from fastapi.testclient import TestClient
from justshipit.api.app import app
from justshipit.contracts.requests import (
    AgentRequestDraft,
    HumanDecision,
    HumanResponse,
    RequestStatus,
    RequestType,
    RiskLevel,
)
from justshipit.db.models import OwnerTask
from justshipit.services import owner_tasks, requests
from owner_task_kit import LANDING_CARD
from sqlalchemy import func, select
from ask_kit import by_project_manager, by_task, contract
from justshipit.contracts.work_path import HoldKind


def draft(**kw) -> AgentRequestDraft:
    base = {"type": RequestType.RESOURCE, "title": "Spend $12 on a log dataset",
            "summary": "Buy the sample log dataset.", "rationale": "We need real logs to test the parser.",
            "options": [], "recommended_option": None, "action_kind": "spend.purchase", "estimated_cost_usd": 12.0,
            "risk_level": RiskLevel.HIGH, "context": contract()}
    return AgentRequestDraft(**{**base, **kw})


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def approve(api, req, **kw):
    return api.post(f"/api/requests/{req.id}/respond", json={"decision": "approve", "revision": 1, **kw})


def spend_card(s, project, **kw):
    req = requests.create(s, draft(**kw), from_agent="research", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    return req


def tasks_of(s, req) -> list[OwnerTask]:
    return list(s.scalars(select(OwnerTask).where(OwnerTask.request_id == req.id)))


# ------------------------------------------------------------------ Q1: the typed number, on the server too

def test_a_spend_with_no_rule_is_refused_without_the_typed_number_and_says_why(s, project, api):
    req = spend_card(s, project)
    assert req.action_kind == "internal.other" and req.policy_mode == "approve", "the seeded REQ-9 shape"

    report = api.get(f"/api/requests/{req.id}").json()["report"]
    assert report["typed_confirm"] is True and report["typed_confirm_why"] == "unknown_spend"

    for typed in (None, str(req.id + 1), f"REQ-{req.id + 1}"):
        r = approve(api, req, **({"confirm_text": typed} if typed else {}))
        assert r.status_code == 409, r.text
        assert "no rule for" in r.json()["detail"] and f"REQ-{req.id}" in r.json()["detail"]
    s.expire_all()
    assert s.get(type(req), req.id).status == RequestStatus.PENDING.value


def test_a_spend_with_no_rule_is_approved_with_its_number_typed(s, project, api):
    req = spend_card(s, project)

    r = approve(api, req, confirm_text=f" req-{req.id} ")

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVED"


@pytest.mark.parametrize("kw", [{"estimated_cost_usd": None}, {"estimated_cost_usd": 0.0},
                                {"action_kind": "internal.report_draft"}])
def test_a_free_catch_all_or_a_known_action_takes_no_typed_number_for_the_spend(s, project, kw):
    req = requests.create(s, draft(risk_level=RiskLevel.LOW, **kw), from_agent="research", project_id=project.id,
                          origin=by_project_manager(s, project.id))

    assert requests.typed_confirm_why(req) is None
    requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE))
    assert req.status == RequestStatus.APPROVED.value


def test_the_policys_own_strong_approval_still_says_its_own_reason(s, project, api):
    req = spend_card(s, project, action_kind="external.ads_run", type=RequestType.APPROVAL)
    assert req.policy_mode == "strong_approve"

    r = approve(api, req)

    assert api.get(f"/api/requests/{req.id}").json()["report"]["typed_confirm_why"] == "strong"
    assert r.status_code == 409 and "hard to take back" in r.json()["detail"]


# ------------------------------------------------------------------ Q15: the payment is the owner's to-do

def test_approving_a_spend_with_no_rule_makes_one_payment_todo_with_amount_where_and_decision(s, project, api):
    req = spend_card(s, project, action_payload_json=json.dumps({"vendor": "Kaggle datasets", "item": "nginx logs"}))
    report = api.get(f"/api/requests/{req.id}").json()["report"]
    assert "pay for it yourself" in report["on_approve"] and "pay for it yourself" in report["owner_does"]

    assert approve(api, req, confirm_text=f"REQ-{req.id}").status_code == 200
    s.expire_all()

    [task] = tasks_of(s, req)
    assert task.kind == "spend" and task.status == "OPEN"
    assert task.card["payment"] == {"amount_usd": 12.0, "where": "Kaggle datasets", "request": f"REQ-{req.id}"}
    owner_tasks.create_for_request(s, s.get(type(req), req.id), by_owner=True)
    assert len(tasks_of(s, req)) == 1, "asking again makes no second payment to-do"


def test_a_question_that_names_a_cost_promises_no_payment_and_answering_it_makes_none(s, project, api):
    req = spend_card(s, project, type=RequestType.QUESTION, options=["Monthly", "Yearly"], recommended_option="Monthly")
    assert req.action_kind == "internal.other" and owner_tasks.task_kind(req) is None
    report = api.get(f"/api/requests/{req.id}").json()["report"]
    assert "pay" not in report["on_approve"] and "pay" not in report["owner_does"]

    r = approve(api, req, chosen_option="Monthly", confirm_text=f"REQ-{req.id}")

    assert r.status_code == 200 and r.json()["status"] == "ANSWERED"
    s.expire_all()
    assert tasks_of(s, req) == [], "the answer is kept; no payment to-do follows"


def test_a_payment_todo_says_so_when_the_decision_names_no_place(s, project):
    req = spend_card(s, project)
    requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE, confirm_text=f"REQ-{req.id}"))

    [task] = tasks_of(s, req)
    assert task.card["payment"]["where"] is None


@pytest.mark.parametrize("decision", [HumanDecision.REJECT, HumanDecision.DEFER])
def test_a_spend_held_or_declined_makes_no_payment_todo(s, project, decision):
    req = spend_card(s, project)
    requests.respond(s, req.id, HumanResponse(decision=decision))

    assert tasks_of(s, req) == []


def test_a_by_hand_action_that_costs_money_keeps_its_own_todo_and_says_the_owner_pays(s, project):
    req = requests.create(s, draft(type=RequestType.APPROVAL, action_kind="external.other", title="Print 200 flyers",
                                   post_card=LANDING_CARD, risk_level=RiskLevel.LOW),
                          from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    assert "about $12, is yours to pay" in requests.report(s, req, "en")["on_approve"]

    requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE, confirm_text=f"REQ-{req.id}"))

    [task] = tasks_of(s, req)
    assert task.kind == "other" and task.card["payment"]["amount_usd"] == 12.0
    assert s.scalar(select(func.count()).select_from(OwnerTask).where(OwnerTask.request_id == req.id)) == 1


# ------------------------------------------------------------------ Q19: reopening a held or declined decision

def card(s, project, **kw):
    base = {"type": RequestType.APPROVAL, "title": "Try a weekly digest", "action_kind": "internal.report_draft",
                "estimated_cost_usd": None, "risk_level": RiskLevel.LOW}
    req = requests.create(s, draft(**{**base, **kw.pop("draft", {})}), from_agent="strategy", project_id=project.id,
                          **kw, origin=by_task(s, project.id))
    s.commit()
    return req


def reopen(api, req):
    return api.post(f"/api/requests/{req.id}/reopen")


def decided(s, req, decision, **kw):
    requests.respond(s, req.id, HumanResponse(decision=decision, **kw))
    s.commit()
    return req


@pytest.mark.parametrize("decision", [HumanDecision.DEFER, HumanDecision.REJECT])
def test_a_held_or_declined_decision_nothing_followed_from_reopens_and_can_be_decided_again(s, project, api, decision):
    req = decided(s, card(s, project), decision)
    assert api.get(f"/api/requests/{req.id}").json()["report"]["reopen"] == {"allowed": True, "why": None}

    r = reopen(api, req)

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PENDING" and r.json()["response"] is None and r.json()["revision"] == 1
    assert r.json()["report"]["on_approve"], "the card reads as one waiting for the owner again"
    s.expire_all()
    again = s.get(type(req), req.id)
    assert [x["status"] for x in again.action_params["reopened"]] == [requests.RequestStatus(
        "DEFERRED" if decision == HumanDecision.DEFER else "REJECTED").value]
    assert approve(api, req).json()["status"] == "APPROVED"


def test_a_held_decision_whose_project_carried_on_reopens_without_holding_up_work(s, project, api):
    project.status = "WAITING_HUMAN"
    req = decided(s, card(s, project, draft={"context": contract(HoldKind.THIS_TASK)}), HumanDecision.DEFER, note="Later, after launch")

    r = reopen(api, req)

    assert r.status_code == 200, r.text
    assert r.json()["blocking"] is False


def test_a_decline_with_a_note_the_project_manager_re_planned_around_is_not_reopened(s, project, api):
    project.status = "RUNNING"
    req = decided(s, card(s, project), HumanDecision.REJECT, note="Not this quarter, focus on sales")

    r = reopen(api, req)

    assert r.status_code == 409 and "work has moved on since you declined" in r.json()["detail"]
    assert api.get(f"/api/requests/{req.id}").json()["report"]["reopen"]["allowed"] is False


def test_a_blocking_decline_its_project_resumed_from_is_not_reopened(s, project, api):
    project.status = "WAITING_HUMAN"
    req = decided(s, card(s, project, draft={"context": contract(HoldKind.THIS_TASK)}), HumanDecision.REJECT)

    r = reopen(api, req)

    assert r.status_code == 409 and "carried on" in r.json()["detail"]


@pytest.mark.parametrize("decision", [HumanDecision.DEFER, HumanDecision.REJECT])
def test_a_builds_card_is_asked_again_from_the_build_not_reopened(s, project, api, decision):
    req = card(s, project, draft={"action_kind": "dev.merge"}, system=True, action_params={"dev_job_id": 4242})
    assert api.get(f"/api/requests/{req.id}").json()["report"]["reopen_later"] == {"defer": False, "reject": False}
    decided(s, req, decision)

    r = reopen(api, req)

    assert r.status_code == 409 and "Engineering screen" in r.json()["detail"]


def test_a_bet_card_reopens_after_a_hold_but_not_after_the_no_dropped_the_bet(s, project, api):
    bet_card = {"action_kind": "project.direction_change"}
    held = card(s, project, draft=bet_card, system=True, action_params={"bet_id": 77})
    no = card(s, project, draft={**bet_card, "title": "Drop the paid tier"}, system=True, action_params={"bet_id": 78})
    assert api.get(f"/api/requests/{held.id}").json()["report"]["reopen_later"] == {"defer": True, "reject": False}
    decided(s, held, HumanDecision.DEFER)
    decided(s, no, HumanDecision.REJECT)

    assert reopen(api, held).status_code == 200
    r = reopen(api, no)
    assert r.status_code == 409 and "already acted on the decline" in r.json()["detail"]


def test_an_expired_decision_is_not_reopened(s, project, api):
    from datetime import datetime, timedelta
    req = decided(s, card(s, project), HumanDecision.DEFER)
    req.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    s.commit()

    r = reopen(api, req)

    assert r.status_code == 409 and "expired" in r.json()["detail"]


def test_only_a_held_or_declined_decision_reopens(s, project, api):
    waiting = card(s, project)
    done = decided(s, card(s, project, draft={"title": "Publish the report"}), HumanDecision.APPROVE)

    for req in (waiting, done):
        r = reopen(api, req)
        assert r.status_code == 409 and "nothing to reopen" in r.json()["detail"]
        assert api.get(f"/api/requests/{req.id}").json()["report"]["reopen"] is None


@pytest.mark.parametrize("decision", [HumanDecision.DEFER, HumanDecision.REJECT])
def test_a_decision_of_an_archived_project_is_not_reopened_and_says_to_unarchive_first(s, project, api, decision):
    from justshipit.services import projects
    req = decided(s, card(s, project), decision)
    projects.archive(s, project.id, lang="en")
    s.commit()

    r = reopen(api, req)

    assert r.status_code == 409 and f"P-{project.id} is archived" in r.json()["detail"]
    assert "Unarchive the project first" in r.json()["detail"]
    assert api.get(f"/api/requests/{req.id}").json()["report"]["reopen"] == {
        "allowed": False, "why": r.json()["detail"]}, "the card says why, as the refusal does"
    s.expire_all()
    assert s.get(type(req), req.id).status == req.status, "the decision stays as the owner left it"


def test_a_company_wide_card_of_an_archived_business_is_not_reopened(s, api):
    from uuid import uuid4

    from justshipit.db.models import Bet
    from justshipit.services import projects, roadmap
    b = roadmap.create_business(s, name=f"Archived {uuid4().hex[:6]}", one_liner="x")
    bet = Bet(business_id=b.id, title="Try a paid tier", pitch="p", status="REVIEW")
    s.add(bet)
    s.flush()
    req = requests.create(s, draft(type=RequestType.APPROVAL, title="Change direction", estimated_cost_usd=None,
                                   action_kind="project.direction_change", risk_level=RiskLevel.LOW),
                          from_agent="strategy", project_id=None, system=True, action_params={"bet_id": bet.id},
                          origin=by_project_manager(s, None))
    s.commit()
    decided(s, req, HumanDecision.DEFER)
    assert api.get(f"/api/requests/{req.id}").json()["report"]["reopen"]["allowed"] is True, "before the archive"
    projects.archive_business(s, b.id, lang="en")
    s.commit()

    r = reopen(api, req)

    assert r.status_code == 409 and f"B-{b.id} is archived" in r.json()["detail"]


def realigns_of(s, req) -> int:
    from justshipit.db.models import Job
    s.expire_all()
    return s.scalar(select(func.count()).select_from(Job).where(
        Job.kind == "realign_project", Job.payload["request_id"].as_integer() == req.id))


def test_a_reopened_decisions_next_note_is_re_planned_around_and_then_blocks_a_reopen(s, project, api):
    project.status = "RUNNING"
    req = decided(s, card(s, project), HumanDecision.DEFER, note="After launch")
    assert realigns_of(s, req) == 1
    assert reopen(api, req).status_code == 200
    s.expire_all()

    decided(s, s.get(type(req), req.id), HumanDecision.REJECT, note="Not this quarter, focus on sales")

    assert realigns_of(s, req) == 2, "the second decision's note is re-planned around too"
    r = reopen(api, req)
    assert r.status_code == 409 and "moved on since you declined" in r.json()["detail"]


def test_a_decline_after_a_held_round_the_project_manager_re_planned_reopens_while_nothing_followed_the_no(s, project, api):
    project.status = "RUNNING"
    req = decided(s, card(s, project), HumanDecision.DEFER, note="After launch")
    assert reopen(api, req).status_code == 200
    s.expire_all()

    decided(s, s.get(type(req), req.id), HumanDecision.REJECT)

    assert realigns_of(s, req) == 1, "a no without a note sets nothing going"
    r = reopen(api, req)
    assert r.status_code == 200, "the held round's re-plan is not a follow-up of this no"
