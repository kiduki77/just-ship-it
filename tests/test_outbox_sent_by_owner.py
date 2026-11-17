"""An item the company kept in the outbox can be marked as sent by the owner, who sent it by hand. The mark records when,
and an optional link and note, on the request's to-do (closing an open one or making one already done). It never sends
anything: the gateway's row stays an outbox row and no connector is called."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from justshipit.api.app import app
from justshipit.contracts.requests import (
    AgentRequestDraft,
    HumanDecision,
    HumanResponse,
    RequestType,
    RiskLevel,
)
from justshipit.db.models import ActionExecution, OwnerTask
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.operations import gateway
from justshipit.services import requests
from sqlalchemy import func, select
from ask_kit import contract, by_project_manager


@pytest.fixture
def console(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


@pytest.fixture
def nothing_leaves(monkeypatch):
    """Called once the outbox item exists: from then on any connector call fails the test, since marking must not
    send."""
    def refuse(*a, **k):
        pytest.fail("a connector was called")

    def seal():
        monkeypatch.setattr(gateway.smtplib, "SMTP", refuse)
        monkeypatch.setattr(gateway, "execute_action", refuse)
        for name in ("email_send", "calendar_create", "survey_distribute"):
            monkeypatch.setattr(gateway, name, refuse)
    return seal


def kept_in_outbox(s, project) -> int:
    """An approved interview email the gateway wrote to the outbox instead of sending."""
    r = requests.create(s, AgentRequestDraft(
        type=RequestType.APPROVAL, title="Interview invite email", summary="s", rationale="r", options=[],
        recommended_option=None, action_kind="external.email_send",
        action_payload_json='{"to": ["a@example.com"], "subject": "Interview", "body": "Hi"}',
        estimated_cost_usd=None, risk_level=RiskLevel.LOW, context=contract()), from_agent="marketing",
        project_id=project.id, origin=by_project_manager(s, project.id))
    requests.respond(s, r.id, HumanResponse(decision=HumanDecision.APPROVE))
    s.commit()
    return gateway.execute_action(r.id)


def listed(console, action_id: int) -> dict:
    return next(a for a in console.get("/api/actions").json() if a["id"] == action_id)


def counts(action_id: int) -> tuple:
    with session_scope() as s:
        row = s.get(ActionExecution, action_id)
        return (row.mode, row.status, row.result_summary, s.scalar(select(func.count()).select_from(ActionExecution)))


def test_marking_records_the_owners_send_on_a_done_todo_and_sends_nothing(console, s, project, nothing_leaves):
    action = kept_in_outbox(s, project)
    nothing_leaves()
    before = counts(action)
    at = (datetime.now(UTC) - timedelta(hours=2)).replace(microsecond=0)

    r = console.post(f"/api/actions/{action}/sent-by-owner",
                     json={"at": at.isoformat(), "link": "https://mail.example.com/sent/1", "note": "Sent from Gmail"})

    assert r.status_code == 200, r.text
    task = r.json()
    assert task["status"] == "DONE" and task["kind"] == "other"
    assert task["result"]["link"] == "https://mail.example.com/sent/1" and task["result"]["note"] == "Sent from Gmail"
    assert datetime.fromisoformat(task["done_at"]) == at
    assert counts(action) == before, "the outbox row is unchanged and no new action was made"
    sent = listed(console, action)["sent_by_owner"]
    assert sent["ref"] == task["ref"] and datetime.fromisoformat(sent["at"]) == at
    assert sent["note"] == "Sent from Gmail"


def test_an_item_not_marked_lists_no_send(console, s, project):
    action = kept_in_outbox(s, project)

    assert listed(console, action)["sent_by_owner"] is None


def test_marking_again_returns_the_same_todo(console, s, project, nothing_leaves):
    action = kept_in_outbox(s, project)
    nothing_leaves()

    first = console.post(f"/api/actions/{action}/sent-by-owner", json={})
    again = console.post(f"/api/actions/{action}/sent-by-owner", json={"note": "again"})

    assert first.status_code == again.status_code == 200 and first.json()["id"] == again.json()["id"]
    with session_scope() as db:
        req = db.get(ActionExecution, action).request_id
        assert db.scalar(select(func.count()).select_from(OwnerTask).where(OwnerTask.request_id == req)) == 1


def test_an_open_todo_for_the_request_is_closed_with_the_record(console, s, project, nothing_leaves):
    action = kept_in_outbox(s, project)
    nothing_leaves()
    with session_scope() as db:
        req = db.get(ActionExecution, action).request_id
        db.add(OwnerTask(project_id=project.id, request_id=req, kind="other", title="Send the invite", card={},
                        status="OPEN"))

    r = console.post(f"/api/actions/{action}/sent-by-owner", json={"note": "done by hand"})

    assert r.status_code == 200 and r.json()["title"] == "Send the invite" and r.json()["status"] == "DONE"
    assert r.json()["result"]["note"] == "done by hand" and listed(console, action)["sent_by_owner"]["ref"] == r.json()["ref"]


def test_reopening_the_todo_takes_the_mark_back(console, s, project, nothing_leaves):
    action = kept_in_outbox(s, project)
    nothing_leaves()
    task = console.post(f"/api/actions/{action}/sent-by-owner", json={}).json()

    console.post(f"/api/owner-tasks/{task['id']}/reopen")

    assert listed(console, action)["sent_by_owner"] is None


def test_only_an_item_kept_in_the_outbox_can_be_marked(console, s, project, nothing_leaves):
    action = kept_in_outbox(s, project)
    nothing_leaves()
    with session_scope() as db:
        db.get(ActionExecution, action).mode = "live"

    r = console.post(f"/api/actions/{action}/sent-by-owner", json={})
    missing = console.post("/api/actions/987654321/sent-by-owner", json={})

    assert r.status_code == 409 and r.json()["detail"] == msg("owner_task.error.not_in_outbox", "en")
    assert missing.status_code == 404


def test_a_send_time_later_than_now_is_refused(console, s, project, nothing_leaves):
    action = kept_in_outbox(s, project)
    nothing_leaves()
    later = datetime.now(UTC) + timedelta(days=1)

    r = console.post(f"/api/actions/{action}/sent-by-owner", json={"at": later.isoformat()})

    assert r.status_code == 409 and r.json()["detail"] == msg("owner_task.error.sent_later", "en")
    assert listed(console, action)["sent_by_owner"] is None


def test_a_link_that_is_not_a_web_address_is_refused_without_echoing_it(console, s, project, nothing_leaves):
    action = kept_in_outbox(s, project)
    nothing_leaves()

    r = console.post(f"/api/actions/{action}/sent-by-owner", json={"link": "javascript:alert(1)"})

    assert r.status_code == 409 and "javascript" not in r.text


def test_a_todo_the_owner_already_closed_is_not_overwritten(console, s, project, nothing_leaves):
    action = kept_in_outbox(s, project)
    nothing_leaves()
    with session_scope() as db:
        req = db.get(ActionExecution, action).request_id
        task = OwnerTask(project_id=project.id, request_id=req, kind="other", title="Send the invite", card={},
                         status="OPEN")
        db.add(task)
        db.flush()
        task_id = task.id
    console.post(f"/api/owner-tasks/{task_id}/skip", json={"note": "not needed"})

    r = console.post(f"/api/actions/{action}/sent-by-owner", json={})

    assert r.status_code == 409 and f"OT-{task_id}" in r.json()["detail"]
    assert listed(console, action)["sent_by_owner"] is None
