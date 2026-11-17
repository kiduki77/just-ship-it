import json
import smtplib
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from justshipit.api import console
from justshipit.contracts.requests import AgentRequestDraft, HumanDecision, HumanResponse, RequestType, RiskLevel
from justshipit.db.models import ActionExecution, AgentRequest, CrmContact, Grant, Job
from justshipit.db.session import session_scope
from justshipit.operations import gateway
from justshipit.services import requests
from justshipit.settings import settings
from fastapi import HTTPException
from sqlalchemy import func, select
from ask_kit import contract, by_project_manager


def _approved(s, pid, title) -> int:
    r = AgentRequest(project_id=pid, from_agent="sales", type="APPROVAL", title=title, summary="s", rationale="r",
                     action_kind="crm.contact_upsert", action_params={"payload": {"name": title}},
                     status="APPROVED", policy_mode="approve")
    s.add(r)
    s.flush()
    s.add(Grant(request_id=r.id, project_id=pid, action_kind="crm.contact_upsert", max_uses=1,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24)))
    return r.id


def _flaky(monkeypatch, fail_calls: set[int]) -> list[str]:
    real, keys = gateway.CONNECTORS["crm.contact_upsert"], []

    def connector(s, payload, key):
        keys.append(key)
        if len(keys) in fail_calls:
            raise gateway.ConnectorError("CRM 설정이 없어 보내기 전에 멈춤")
        return real(s, payload, key)
    monkeypatch.setitem(gateway.CONNECTORS, "crm.contact_upsert", connector)
    return keys


def test_a_connector_refused_before_any_io_keeps_its_grant_and_never_spends_a_siblings(s, project, monkeypatch):
    a, b = _approved(s, project.id, "A"), _approved(s, project.id, "B")
    s.commit()
    _flaky(monkeypatch, fail_calls={1})

    with pytest.raises(gateway.ConnectorError):
        gateway.execute_action(a)
    with session_scope() as s2:
        assert s2.scalar(select(func.sum(Grant.uses)).where(Grant.request_id.in_([a, b]))) == 0

    gateway.execute_action(a)
    gateway.execute_action(b)
    with session_scope() as s2:
        rows = s2.scalars(select(ActionExecution).where(ActionExecution.request_id.in_([a, b]))).all()
        assert sorted((r.request_id, r.status) for r in rows) == [(a, "done"), (b, "done")]
        assert s2.scalar(select(func.sum(Grant.uses)).where(Grant.request_id.in_([a, b]))) == 2


@pytest.mark.parametrize("kind", ["execute_action", "dev_merge"])
def test_job_retry_refuses_side_effects_that_need_a_new_owner_decision(korean_owner, s, kind):
    with session_scope() as s2:
        j = Job(gate="direct", kind=kind, payload={"request_id": 880001, "dev_job_id": 880001}, status="failed", attempts=1,
                max_attempts=1)
        s2.add(j)
        s2.flush()
        jid = j.id
    with pytest.raises(HTTPException) as err:
        console.job_retry(jid)
    assert err.value.status_code == 409
    assert "승인" in err.value.detail
    with session_scope() as s2:
        assert s2.scalar(select(func.count()).select_from(Job).where(Job.kind == kind, Job.status == "queued",
                                                                    Job.payload["request_id"].as_integer() == 880001)) == 0
        s2.get(Job, jid).status = "done"


def _approved_email(s, project, to="c@example.com") -> int:
    r = requests.create(s, AgentRequestDraft(
        type=RequestType.APPROVAL, title="재시도 메일", summary="s", rationale="r", options=[], recommended_option=None,
        action_kind="external.email_send", action_payload_json=json.dumps({"to": [to], "subject": "안내", "body": "b"}),
        estimated_cost_usd=None, risk_level=RiskLevel.LOW, context=contract()), from_agent="marketing", project_id=project.id,
        origin=by_project_manager(s, project.id))
    requests.respond(s, r.id, HumanResponse(decision=HumanDecision.APPROVE))
    s.commit()
    return r.id


@pytest.mark.parametrize("death", [RuntimeError("worker died before recording the result"), SystemExit(1)])
def test_an_attempt_after_a_send_that_may_have_landed_is_refused_so_the_outbox_holds_one_send(s, project,
                                                                                               monkeypatch, death):
    rid = _approved_email(s, project)
    real, keys = gateway.CONNECTORS["external.email_send"], []

    def sends_then_dies(s, payload, key):
        keys.append(key)
        out = real(s, payload, key)
        if len(keys) == 1:
            raise death
        return out
    monkeypatch.setitem(gateway.CONNECTORS, "external.email_send", sends_then_dies)

    with pytest.raises(type(death)):
        gateway.execute_action(rid)
    with pytest.raises(PermissionError, match="isn.t sent again"):
        gateway.execute_action(rid)

    assert keys == [f"req-{rid}"]
    assert [p.name for p in (settings().var_dir / "outbox" / "email").glob(f"req-{rid}*")] == [f"req-{rid}.eml"]


def test_a_live_email_whose_quit_fails_after_delivery_is_never_delivered_again(s, project, monkeypatch, email_live):
    s.add(CrmContact(name="c", email="c@example.com", consent=True))
    rid = _approved_email(s, project)
    delivered = []

    class QuitFails:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a):
            raise smtplib.SMTPResponseException(451, b"quit failed")
        def starttls(self, context=None): pass
        def login(self, *a): pass
        def send_message(self, msg): delivered.append(msg["Message-ID"])

    monkeypatch.setattr(settings(), "smtp_host", "smtp.invalid")  # main reads SMTP from settings, not os.environ
    monkeypatch.setattr(gateway.smtplib, "SMTP", QuitFails)

    with pytest.raises(smtplib.SMTPResponseException):
        gateway.execute_action(rid)
    with pytest.raises(PermissionError, match="isn.t sent again"):
        gateway.execute_action(rid)

    assert delivered == [f"<req-{rid}@ship.local>"]
    with session_scope() as s2:
        assert s2.scalar(select(Grant.uses).where(Grant.request_id == rid)) == 1


def test_a_send_refused_before_any_io_refunds_its_grant_and_can_go_out_once_fixed(s, project, monkeypatch, email_live):
    rid = _approved_email(s, project, to="later-consent@example.com")
    delivered = []

    class Delivers:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self, context=None): pass
        def login(self, *a): pass
        def send_message(self, msg): delivered.append(msg["Message-ID"])

    monkeypatch.setattr(settings(), "smtp_host", "smtp.invalid")  # main reads SMTP from settings, not os.environ
    monkeypatch.setattr(gateway.smtplib, "SMTP", Delivers)

    with pytest.raises(gateway.ConnectorError, match="consent"):
        gateway.execute_action(rid)
    with session_scope() as s2:
        assert s2.scalar(select(Grant.uses).where(Grant.request_id == rid)) == 0
        s2.add(CrmContact(name="c", email="later-consent@example.com", consent=True))

    gateway.execute_action(rid)
    assert delivered == [f"<req-{rid}@ship.local>"]
    with session_scope() as s2:
        assert s2.scalar(select(Grant.uses).where(Grant.request_id == rid)) == 1


@pytest.mark.parametrize("kind, payload", [
    ("external.email_send", {"to": ["c@example.com"], "subject": "제목만"}),
    ("external.calendar_create", {"title": "회의", "start": "다음 주 화요일"}),
    ("crm.contact_upsert", {"email": "no-name@example.com"}),
    ("crm.interaction_log", {"contact_id": 99999999, "summary": "없는 연락처"}),
])
def test_every_connectors_payload_check_refunds_the_grant(s, project, kind, payload):
    r = AgentRequest(project_id=project.id, from_agent="sales", type="APPROVAL", title=kind, summary="s",
                     rationale="r", action_kind=kind, action_params={"payload": payload}, status="APPROVED",
                     policy_mode="approve")
    s.add(r)
    s.flush()
    s.add(Grant(request_id=r.id, project_id=project.id, action_kind=kind, max_uses=1,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24)))
    s.commit()

    with pytest.raises(gateway.ConnectorError):
        gateway.execute_action(r.id)

    with session_scope() as s2:
        assert s2.scalar(select(Grant.uses).where(Grant.request_id == r.id)) == 0
        assert s2.scalar(select(ActionExecution.status).where(ActionExecution.request_id == r.id)) == "not_sent"


def test_a_grant_that_expires_while_its_spend_waits_on_the_lock_is_not_used(s, project):
    rid = _approved(s, project.id, "lock-wait")
    s.commit()
    with session_scope() as s2:
        g = s2.scalar(select(Grant).where(Grant.request_id == rid))
        g.expires_at = s2.scalar(select(func.clock_timestamp())) + timedelta(seconds=1)
    outcome = []

    def run():
        try:
            gateway.execute_action(rid)
            outcome.append("done")
        except PermissionError as exc:
            outcome.append(str(exc))

    with session_scope() as holder:
        holder.scalar(select(Grant).where(Grant.request_id == rid).with_for_update())
        th = threading.Thread(target=run)
        th.start()
        time.sleep(2)
    th.join(10)

    assert outcome and outcome[0].startswith("no valid grant")
    with session_scope() as s2:
        assert s2.scalar(select(Grant.uses).where(Grant.request_id == rid)) == 0


def test_a_database_error_confirming_the_lease_after_the_spend_refunds_the_grant(s, project, monkeypatch):
    """The confirm comes before the connector, so nothing went out: the send must not stay "started"."""
    from justshipit.services import queue
    from sqlalchemy.exc import OperationalError

    rid = _approved(s, project.id, "confirm-db-error")
    s.commit()
    real, calls = queue.confirm_lease, []

    def db_drops_on_the_second_confirm():
        calls.append(1)
        if len(calls) == 2:
            raise OperationalError("SELECT pg_advisory_xact_lock", {}, Exception("server closed the connection"))
        real()
    monkeypatch.setattr(queue, "confirm_lease", db_drops_on_the_second_confirm)

    with pytest.raises(OperationalError):
        gateway.execute_action(rid)
    with session_scope() as s2:
        assert s2.scalar(select(Grant.uses).where(Grant.request_id == rid)) == 0
        assert s2.scalar(select(ActionExecution.status).where(ActionExecution.request_id == rid)) == "not_sent"

    gateway.execute_action(rid)
    with session_scope() as s2:
        assert s2.scalar(select(ActionExecution.status).where(ActionExecution.request_id == rid)) == "done"
        assert s2.scalar(select(Grant.uses).where(Grant.request_id == rid)) == 1
