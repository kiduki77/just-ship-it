from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from justshipit.capability.factory import _summarize
from justshipit.contracts.requests import AgentRequestDraft, HumanDecision, HumanResponse, RequestType, RiskLevel
from justshipit.db.models import ActionExecution, CrmContact, Job, Schedule
from justshipit.db.session import session_scope
from justshipit.operations import gateway, scheduler
from justshipit.services import requests
from ask_kit import contract, by_project_manager


def _email_request(s, project, payload_json):
    return requests.create(s, AgentRequestDraft(
        type=RequestType.APPROVAL, title="인터뷰 섭외 메일", summary="s", rationale="r", options=[],
        recommended_option=None, action_kind="external.email_send", action_payload_json=payload_json,
        estimated_cost_usd=None, risk_level=RiskLevel.LOW, context=contract()), from_agent="marketing",
        project_id=project.id, origin=by_project_manager(s, project.id))


def test_external_action_runs_once_through_gateway_in_outbox_mode(s, project):
    r = _email_request(s, project, '{"to": ["a@example.com"], "subject": "인터뷰 요청", "body": "안녕하세요"}')
    assert r.action_params["payload"]["subject"] == "인터뷰 요청"
    requests.respond(s, r.id, HumanResponse(decision=HumanDecision.APPROVE))
    assert s.scalar(select(Job).where(Job.idempotency_key == f"execute_action:{r.id}")) is not None
    s.commit()
    first = gateway.execute_action(r.id)
    again = gateway.execute_action(r.id)   # retry must not re-send
    assert first == again
    with session_scope() as s2:
        row = s2.get(ActionExecution, first)
        assert row.status == "done" and row.mode == "outbox" and "not sent" in row.result_summary


def test_an_email_that_only_went_to_the_outbox_is_recorded_as_outbox_not_sent(s, project):
    from justshipit.db.models import ActivityEvent
    r = _email_request(s, project, '{"to": ["a@example.com"], "subject": "Hello", "body": "Hi"}')
    requests.respond(s, r.id, HumanResponse(decision=HumanDecision.APPROVE))
    s.commit()
    run = gateway.execute_action(r.id)
    with session_scope() as s2:
        [ev] = s2.scalars(select(ActivityEvent).where(ActivityEvent.entity == "action", ActivityEvent.entity_id == run,
                                                      ActivityEvent.verb == "status",
                                                      ActivityEvent.data["to"].astext == "done")).all()
        assert (ev.data["mode"], ev.data["outcome"]) == ("outbox", "outbox")
        assert ev.summary.endswith("→ outbox")


def test_gateway_refuses_unapproved_and_live_email_needs_consent(s, project, email_live):
    r = _email_request(s, project, '{"to": ["b@example.com"], "subject": "x", "body": "y"}')
    s.commit()
    with pytest.raises(PermissionError):
        gateway.execute_action(r.id)
    with session_scope() as s2:
        s2.add(CrmContact(name="B", email="b@example.com", consent=False))
        requests.respond(s2, r.id, HumanResponse(decision=HumanDecision.APPROVE))
    with pytest.raises(gateway.ConnectorError, match="consent"):
        gateway.execute_action(r.id)


def test_scheduler_fires_due_schedules_once(s):
    """Under a payload of its own, so it never takes the standing company review (same kind, empty payload) that
    an earlier test file's sync left, and removed after, so the next file does not meet its row."""
    payload = {"case": "fires-once"}
    scheduler.ensure(s, name="test-sched", kind="portfolio_review", hours=24, payload=payload, first_in_hours=0)
    s.flush()
    sc = s.scalar(select(Schedule).where(Schedule.name == "test-sched"))
    try:
        sc.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        fired = scheduler.tick(s)
        assert sc.id in fired
        assert sc.next_run_at > datetime.now(timezone.utc) + timedelta(hours=23)
        assert sc.id not in scheduler.tick(s)
    finally:
        s.delete(sc)
        s.flush()


def test_a_schedule_rename_takes_one_row_and_never_two_under_one_name(s):
    """The English names replaced Korean ones by renaming the schedule of the same kind and payload. Two rows of that
    kind and payload under other names (one a leftover) were both renamed in one statement, and the unique
    (kind, name) stopped it: every scheduler tick failed from then on."""
    kind = "rename_probe"
    older = Schedule(name="옛 이름", kind=kind, payload={}, interval_hours=24, next_run_at=datetime.now(timezone.utc))
    newer = Schedule(name="another old name", kind=kind, payload={}, interval_hours=24,
                     next_run_at=datetime.now(timezone.utc))
    s.add_all([older, newer])
    s.flush()
    try:
        scheduler.ensure(s, name="Probe", kind=kind, hours=24)
        scheduler.ensure(s, name="Probe", kind=kind, hours=24)
        s.expire_all()
        names = {r.id: r.name for r in s.scalars(select(Schedule).where(Schedule.kind == kind))}
        assert names == {older.id: "Probe", newer.id: "another old name"}
    finally:
        for r in s.scalars(select(Schedule).where(Schedule.kind == kind)):
            s.delete(r)
        s.flush()


def _case(b_pass, b_unmet, c_pass, c_unmet, b_sup=0.5, c_sup=0.5):
    return {"baseline": {"pass": b_pass, "unmet": b_unmet, "supported": b_sup, "contradicted": 0},
            "candidate": {"pass": c_pass, "unmet": c_unmet, "supported": c_sup, "contradicted": 0}}


def test_skill_promotion_requires_measured_improvement():
    assert _summarize([])["promote"] is False
    assert _summarize([_case(False, 4, True, 1), _case(False, 3, False, 2), _case(True, 0, True, 0)])["promote"]
    assert not _summarize([_case(True, 0, False, 2), _case(False, 3, False, 3)])["promote"]


def test_live_email_and_tracing_read_their_settings_from_dotenv(s, tmp_path, monkeypatch):
    from justshipit.services import tracing
    from justshipit.settings import Settings

    dotenv = tmp_path / ".env"
    dotenv.write_text("SHIP_SMTP_HOST=smtp.example.com\nSHIP_SMTP_PORT=2525\nSHIP_SMTP_USER=bot\n"
                      "SHIP_SMTP_PASSWORD=pw\nSHIP_SMTP_FROM=ceo@example.com\nSHIP_OTEL_ENDPOINT=console\n")
    for k in ("HOST", "PORT", "USER", "PASSWORD", "FROM"):
        monkeypatch.delenv(f"SHIP_SMTP_{k}", raising=False)
    monkeypatch.delenv("SHIP_OTEL_ENDPOINT", raising=False)
    from_dotenv = Settings(_env_file=dotenv)
    monkeypatch.setattr(gateway, "settings", lambda: from_dotenv)
    monkeypatch.setattr(tracing, "settings", lambda: from_dotenv, raising=False)
    monkeypatch.setattr(gateway, "_mode", lambda name: "live")
    sent: list = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent.append((host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            sent.append((user, password))

        def send_message(self, msg):
            sent.append(msg["From"])

    monkeypatch.setattr(gateway.smtplib, "SMTP", FakeSMTP)
    s.add(CrmContact(name="C", email="c@example.com", consent=True))
    s.flush()
    gateway.email_send(s, {"to": ["c@example.com"], "subject": "x", "body": "y"}, "k-dotenv")
    monkeypatch.setattr(tracing, "_tracer", None)
    monkeypatch.setattr(tracing, "_disabled", False)

    assert sent == [("smtp.example.com", 2525), ("bot", "pw"), "ceo@example.com"]
    assert tracing.tracer() is not None
