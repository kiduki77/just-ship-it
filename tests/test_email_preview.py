"""An email approval shows what it sends (recipients, subject, body) before the owner approves, or says which part
cannot be read so the console can hold the approval."""
import pytest
from fastapi.testclient import TestClient
from justshipit.api.app import app
from justshipit.contracts.requests import AgentRequestDraft, RequestType, RiskLevel
from justshipit.services import requests
from ask_kit import contract, by_project_manager


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def ask(s, project, payload_json, action_kind="external.email_send") -> int:
    r = requests.create(s, AgentRequestDraft(
        type=RequestType.APPROVAL, title="Interview invite", summary="s", rationale="r", options=[],
        recommended_option=None, action_kind=action_kind, action_payload_json=payload_json,
        estimated_cost_usd=None, risk_level=RiskLevel.LOW, context=contract()), from_agent="marketing",
        project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    return r.id


def test_an_email_approval_shows_recipients_subject_and_body(s, project, api):
    rid = ask(s, project, '{"to": "a@example.com, b@example.com", "subject": "Quick chat?", "body": "Hi,\\nten minutes?"}')

    card = api.get(f"/api/requests/{rid}").json()
    assert card["email_preview"] == {"to": ["a@example.com", "b@example.com"], "subject": "Quick chat?",
                                     "body": "Hi,\nten minutes?"}
    assert card["email_preview_missing"] is None
    listed = next(r for r in api.get("/api/inbox").json() if r["id"] == rid)
    assert listed["email_preview"] == card["email_preview"]


@pytest.mark.parametrize(("payload_json", "missing"), [
    (None, "no_payload"),
    ("not json", "no_payload"),
    ('{"subject": "x", "body": "y"}', "no_recipient"),
    ('{"to": ["a@example.com"], "body": "y"}', "no_subject"),
    ('{"to": ["a@example.com"], "subject": "x", "body": "   "}', "no_body"),
])
def test_an_email_approval_that_cannot_be_read_says_what_is_missing(s, project, api, payload_json, missing):
    rid = ask(s, project, payload_json)
    assert api.get(f"/api/requests/{rid}").json()["email_preview_missing"] == missing


def test_other_approvals_carry_no_email_fields(s, project, api):
    rid = ask(s, project, '{"title": "Call", "start": "2026-11-10T10:00"}', action_kind="external.calendar_create")
    card = api.get(f"/api/requests/{rid}").json()
    assert "email_preview" not in card and "email_preview_missing" not in card
