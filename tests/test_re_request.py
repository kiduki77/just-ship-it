from datetime import UTC, datetime

import pytest
from justshipit.api import console
from justshipit.contracts.requests import AgentRequestDraft, HumanDecision, HumanResponse, RequestType, RiskLevel
from justshipit.db.models import AgentRequest, Grant, Job
from justshipit.db.session import session_scope
from justshipit.operations import gateway
from justshipit.services import requests
from justshipit.settings import settings
from fastapi import HTTPException
from sqlalchemy import select
from ask_kit import contract, by_project_manager


def _failed_send(s, project, monkeypatch) -> tuple[int, int]:
    """A strongly approved email whose send died after it reached the outbox, and the job that gave up."""
    r = requests.create(s, AgentRequestDraft(
        type=RequestType.APPROVAL, title="고객 안내 메일", summary="s", rationale="r", options=[], recommended_option=None,
        action_kind="external.email_send", action_payload_json='{"to": ["c@example.com"], "subject": "안내", "body": "b"}',
        estimated_cost_usd=None, risk_level=RiskLevel.CRITICAL, context=contract()), from_agent="marketing",
        project_id=project.id, origin=by_project_manager(s, project.id))
    requests.respond(s, r.id, HumanResponse(decision=HumanDecision.APPROVE, confirm_text=f"REQ-{r.id}"))
    s.commit()
    real = gateway.CONNECTORS["external.email_send"]

    def sends_then_dies(s, payload, key):
        real(s, payload, key)
        raise RuntimeError("worker died before recording the result")
    monkeypatch.setitem(gateway.CONNECTORS, "external.email_send", sends_then_dies)
    with pytest.raises(RuntimeError):
        gateway.execute_action(r.id)
    monkeypatch.setitem(gateway.CONNECTORS, "external.email_send", real)
    with session_scope() as s2:
        s2.execute(Job.__table__.update().where(Job.kind == "execute_action", Job.payload["request_id"].as_integer() == r.id)
                   .values(status="failed", last_error="RuntimeError: worker died", attempts=1))
        jid = s2.scalar(select(Job.id).where(Job.kind == "execute_action", Job.payload["request_id"].as_integer() == r.id))
    return r.id, jid


def _alert(jid):
    return next((a for a in console.alerts(include_test=True) if a.get("job_id") == jid), None)


def test_a_failed_external_action_can_be_asked_again_as_a_new_request_under_the_same_policy(korean_owner, s, project, monkeypatch):
    old, jid = _failed_send(s, project, monkeypatch)
    assert "다시 요청" in _alert(jid)["hint"]

    new = console.job_re_request(jid)["request_id"]

    assert console.job_re_request(jid)["request_id"] == new
    assert _alert(jid) is None
    with session_scope() as s2:
        r, o = s2.get(AgentRequest, new), s2.get(AgentRequest, old)
        assert (r.status, r.policy_mode, r.action_kind) == ("PENDING", "strong_approve", "external.email_send")
        assert r.action_params["payload"] == o.action_params["payload"]
        assert r.action_params["re_request_of"] == old
        assert s2.scalar(select(Grant.revoked).where(Grant.request_id == old)) is True
        assert s2.scalar(select(Grant.id).where(Grant.request_id == new)) is None
        with pytest.raises(requests.ResponseRejected, match="needs one more check"):
            requests.respond(s2, new, HumanResponse(decision=HumanDecision.APPROVE))
        requests.respond(s2, new, HumanResponse(decision=HumanDecision.APPROVE, confirm_text=f"REQ-{new}"))

    gateway.execute_action(new)
    with pytest.raises(PermissionError, match="다시 요청"):
        gateway.execute_action(old)
    sent = sorted(p.name for p in (settings().var_dir / "outbox" / "email").glob("req-*.eml")
                  if p.name in (f"req-{old}.eml", f"req-{new}.eml"))
    assert sent == sorted([f"req-{old}.eml", f"req-{new}.eml"])


def test_an_external_action_that_already_ran_cannot_be_asked_again(s, project):
    r = requests.create(s, AgentRequestDraft(
        type=RequestType.APPROVAL, title="연락처 저장", summary="s", rationale="r", options=[], recommended_option=None,
        action_kind="crm.contact_upsert", action_payload_json='{"name": "다시 요청 금지"}', estimated_cost_usd=None,
        risk_level=RiskLevel.LOW, context=contract()), from_agent="sales", project_id=project.id,
        origin=by_project_manager(s, project.id))
    requests.respond(s, r.id, HumanResponse(decision=HumanDecision.APPROVE))
    s.commit()
    gateway.execute_action(r.id)
    with session_scope() as s2:
        j = Job(gate="direct", kind="execute_action", payload={"request_id": r.id}, status="failed", attempts=1, max_attempts=1)
        s2.add(j)
        s2.flush()
        jid = j.id

    with pytest.raises(HTTPException) as err:
        console.job_re_request(jid)
    assert err.value.status_code == 409 and "already ran" in err.value.detail


def _unsent_send(s, project, monkeypatch, title: str) -> tuple[int, int]:
    """An approved email whose connector stopped before anything went out, and the job that gave up."""
    r = requests.create(s, AgentRequestDraft(
        type=RequestType.APPROVAL, title=title, summary="s", rationale="r", options=[], recommended_option=None,
        action_kind="external.email_send", action_payload_json='{"to": ["c@example.com"], "subject": "a", "body": "b"}',
        estimated_cost_usd=None, risk_level=RiskLevel.LOW, context=contract()), from_agent="marketing",
        project_id=project.id, origin=by_project_manager(s, project.id))
    requests.respond(s, r.id, HumanResponse(decision=HumanDecision.APPROVE,
                                            confirm_text=f"REQ-{r.id}" if r.policy_mode == "strong_approve" else None))
    s.commit()
    rid = r.id

    def not_configured(s2, payload, key):
        raise gateway.ConnectorError("SMTP 설정이 없어 보내기 전에 멈춤")
    monkeypatch.setitem(gateway.CONNECTORS, "external.email_send", not_configured)
    with pytest.raises(gateway.ConnectorError):
        gateway.execute_action(rid)
    mine = (Job.kind == "execute_action", Job.payload["request_id"].as_integer() == rid)
    with session_scope() as s2:
        s2.execute(Job.__table__.update().where(*mine)
                   .values(status="failed", last_error="ConnectorError: SMTP", attempts=1))
        jid = s2.scalar(select(Job.id).where(*mine))
    return rid, jid


def test_a_retry_and_a_re_request_of_one_unsent_send_never_both_stay_open(korean_owner, s, project, monkeypatch):
    """The owner presses '다시 시도' and '다시 요청' on one alert at once. The retry has checked the alert and not yet
    queued its copy: the re-request waits for it and refuses, or the old approval and a new one would both send."""
    import threading

    rid, jid = _unsent_send(s, project, monkeypatch, "동시 클릭")
    assert _alert(jid)["retryable"]

    real, checked, go = console._why_not_retryable, threading.Event(), threading.Event()

    def checks_then_stalls(s2, job, lang):
        why = real(s2, job, lang)
        checked.set()
        go.wait(5)
        return why
    monkeypatch.setattr(console, "_why_not_retryable", checks_then_stalls)
    out = {}
    retry = threading.Thread(target=lambda: out.update(retry=console.job_retry(jid)))
    retry.start()
    assert checked.wait(5)
    monkeypatch.setattr(console, "_why_not_retryable", real)

    def re_request():
        try:
            out["re_request"] = console.job_re_request(jid)
        except HTTPException as exc:
            out["re_request"] = exc.status_code
    asking = threading.Thread(target=re_request)
    asking.start()
    asking.join(1.5)
    go.set()
    retry.join(10)
    asking.join(10)

    assert out["re_request"] == 409
    with session_scope() as s2:
        copy = s2.scalar(select(Job).where(Job.id == out["retry"]["job_id"]))
        assert (copy.kind, copy.status) == ("execute_action", "queued")
        assert s2.scalar(select(AgentRequest.id).where(
            AgentRequest.action_params["re_request_of"].as_integer() == rid)) is None
        copy.status = "done"


def test_an_unsent_send_whose_approval_lapsed_points_to_a_new_request_not_a_retry(korean_owner, s, project, monkeypatch):
    """A retry spends the approval's grant again, so once the grant has expired a retry can only fail the same way."""
    rid, jid = _unsent_send(s, project, monkeypatch, "만료된 승인")
    assert _alert(jid)["retryable"]
    with session_scope() as s2:
        s2.get(Grant, s2.scalar(select(Grant.id).where(Grant.request_id == rid))).expires_at = datetime.now(UTC)

    alert = _alert(jid)
    assert alert["retryable"] is False and "다시 요청" in alert["hint"]
    with pytest.raises(HTTPException) as refused:
        console.job_retry(jid)
    assert refused.value.status_code == 409 and "다시 요청" in refused.value.detail
    assert console.job_re_request(jid)["status"] == "PENDING"
