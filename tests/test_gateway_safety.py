"""What the gateway refuses on its own: a grant another request's approval made, a mail server whose certificate
does not verify, and an address entry that is more than one address."""
import ssl
from datetime import datetime, timedelta, timezone

import pytest
from justshipit.api import console
from justshipit.db.models import ActionExecution, AgentRequest, CrmContact, Grant, Job
from justshipit.db.session import session_scope
from justshipit.operations import gateway
from justshipit.services import requests
from justshipit.settings import settings
from sqlalchemy import select


def _approved(s, project, kind, payload, mode=None, hours=24) -> int:
    r = AgentRequest(project_id=project.id, from_agent="sales", type="APPROVAL", title=kind, summary="s",
                     rationale="r", action_kind=kind, action_params={"payload": payload}, status="APPROVED",
                     policy_mode="approve")
    s.add(r)
    s.flush()
    s.add(Grant(request_id=r.id, project_id=project.id, action_kind=kind, max_uses=1, connector_mode=mode,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=hours)))
    return r.id


def _mail(to="c@example.com") -> dict:
    return {"to": [to], "subject": "Hello", "body": "Hi"}


@pytest.fixture
def smtp(monkeypatch):
    """A mail server that records what it was asked: the TLS context and each message's recipients."""
    calls = {"tls": [], "to": []}

    class Server:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self, context=None): calls["tls"].append(context)
        def login(self, *a): pass
        def send_message(self, msg): calls["to"].append(msg["To"])

    monkeypatch.setattr(settings(), "smtp_host", "smtp.invalid")
    monkeypatch.setattr(gateway.smtplib, "SMTP", Server)
    return calls


def test_an_email_approved_for_the_outbox_never_goes_out_live_on_another_emails_grant(s, project, email_live, smtp):
    s.add(CrmContact(name="c", email="c@example.com", consent=True))
    a = _approved(s, project, "external.email_send", _mail(), mode="outbox", hours=-1)
    b = _approved(s, project, "external.email_send", _mail(), mode="live")
    s.commit()

    with pytest.raises(requests.GrantRefused):
        gateway.execute_action(a)

    assert smtp["to"] == []
    with session_scope() as s2:
        assert s2.scalar(select(Grant.uses).where(Grant.request_id == b)) == 0
        assert not requests.grant_left(s2, "external.email_send", project.id, request_id=a)
        assert requests.grant_left(s2, "external.email_send", project.id, request_id=b)


def test_an_unspent_outbox_grant_does_not_stand_in_the_way_of_a_later_live_approval(s, project, email_live, smtp):
    s.add(CrmContact(name="c", email="c@example.com", consent=True))
    a = _approved(s, project, "external.email_send", _mail(), mode="outbox")
    b = _approved(s, project, "external.email_send", _mail(), mode="live")
    s.commit()

    gateway.execute_action(b)

    assert smtp["to"] == ["c@example.com"]
    with session_scope() as s2:
        assert s2.scalar(select(Grant.uses).where(Grant.request_id == a)) == 0
        assert s2.scalar(select(Grant.uses).where(Grant.request_id == b)) == 1


def test_the_console_offers_a_retry_only_while_the_requests_own_grant_is_left(s, project):
    a = _approved(s, project, "crm.contact_upsert", {"name": "A"}, hours=-1)
    _approved(s, project, "crm.contact_upsert", {"name": "B"})
    s.add(ActionExecution(request_id=a, project_id=project.id, action_kind="crm.contact_upsert", connector="?",
                          mode="?", idempotency_key=f"req-{a}", payload={"name": "A"}, status="not_sent"))
    job = Job(gate="direct", kind="execute_action", payload={"request_id": a}, status="failed", attempts=1,
              max_attempts=1)
    s.add(job)
    s.flush()

    assert console._why_not_retryable(s, job) is not None, "the other request's grant is not this one's to spend"
    job.status = "done"


def test_live_email_verifies_the_mail_servers_certificate(s, project, email_live, smtp):
    s.add(CrmContact(name="c", email="c@example.com", consent=True))
    s.flush()

    gateway.email_send(s, _mail(), "k-tls")

    [context] = smtp["tls"]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


TWO_IN_ONE = "friend@example.com, attacker@evil.example"


@pytest.mark.parametrize("entry", [TWO_IN_ONE, "friend@example.com;attacker@evil.example",
                                   "Friend <attacker@evil.example>", "friend@example.com\nBcc: attacker@evil.example",
                                   "friend@example.com attacker@evil.example", "no-at-sign", "a@b@c"])
def test_a_contact_is_stored_under_exactly_one_address(s, entry):
    with pytest.raises(gateway.ConnectorError, match="single email address"):
        gateway.crm_contact_upsert(s, {"name": "Friend", "email": entry}, "k-contact")
    assert s.scalar(select(CrmContact.id).where(CrmContact.email == entry)) is None


def test_a_contact_with_one_address_is_stored_without_the_space_around_it(s):
    gateway.crm_contact_upsert(s, {"name": "Friend", "email": " friend.one+tag@example.co.kr "}, "k-contact")
    assert s.scalar(select(CrmContact.name).where(CrmContact.email == "friend.one+tag@example.co.kr")) == "Friend"


@pytest.mark.parametrize("to", [[TWO_IN_ONE], ["Friend <attacker@evil.example>"], ["a@example.com;b@example.com"]])
def test_an_address_entry_that_holds_two_recipients_is_not_sent_even_with_consent_recorded_for_it(s, email_live, smtp,
                                                                                                    to):
    s.add(CrmContact(name="stored before the check", email=to[0], consent=True))
    s.flush()

    with pytest.raises(gateway.ConnectorError, match="single email address"):
        gateway.email_send(s, {"to": to, "subject": "Hello", "body": "Hi"}, "k-two")
    assert smtp["to"] == []


def test_a_comma_separated_string_is_still_several_recipients_each_needing_consent(s, email_live, smtp):
    s.add(CrmContact(name="a", email="a@example.com", consent=True))
    s.flush()

    with pytest.raises(gateway.ConnectorError, match="consent for: b@example.com"):
        gateway.email_send(s, {"to": "a@example.com, b@example.com", "subject": "Hello", "body": "Hi"}, "k-list")
    assert smtp["to"] == []


def test_a_calendar_attendee_is_one_address(s):
    with pytest.raises(gateway.ConnectorError, match="single email address"):
        gateway.calendar_create(s, {"title": "Call", "start": "2026-12-01T10:00:00",
                                    "attendees": ["a@example.com\r\nATTENDEE:mailto:b@evil.example"]}, "k-cal")
