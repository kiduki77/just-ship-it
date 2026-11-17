"""One owner approval sends at most once: a worker that froze mid-send may still be running after its lease was
reclaimed, so neither a re-request nor a retry may send again before it can have stopped."""
import contextvars
import re
import threading
from datetime import UTC, datetime, timedelta

import pytest
from justshipit.api import console
from justshipit.contracts.requests import AgentRequestDraft, HumanDecision, HumanResponse, RequestType, RiskLevel
from justshipit.db.models import ActionExecution, AgentRequest, Grant, Job
from justshipit.i18n import msg
from justshipit.db.session import session_scope
from justshipit.operations import gateway
from justshipit.services import queue, requests
from justshipit.settings import settings
from fastapi import HTTPException
from sqlalchemy import select, text
from ask_kit import contract, by_project_manager

STALE = timedelta(minutes=11)  # longer than job_lease_s


@pytest.fixture(autouse=True)
def _isolated_jobs():
    def settle():
        with session_scope() as s:
            s.execute(text("UPDATE jobs SET status = 'done', held_until = NULL WHERE kind = 'execute_action' "
                           "AND (status IN ('queued', 'running') OR held_until > now())"))
    settle()
    yield
    settle()


def _email(s, project, title="안내 메일") -> int:
    r = requests.create(s, AgentRequestDraft(
        type=RequestType.APPROVAL, title=title, summary="s", rationale="r", options=[], recommended_option=None,
        action_kind="external.email_send",
        action_payload_json='{"to": ["c@example.com"], "subject": "안내", "body": "b"}',
        estimated_cost_usd=None, risk_level=RiskLevel.LOW, context=contract()), from_agent="marketing",
        project_id=project.id, origin=by_project_manager(s, project.id))
    s.flush()
    _approve(s, r.id)
    s.commit()
    return r.id


def _approve(s, rid: int) -> None:
    r = s.get(AgentRequest, rid)
    requests.respond(s, rid, HumanResponse(
        decision=HumanDecision.APPROVE, confirm_text=f"REQ-{rid}" if r.policy_mode == "strong_approve" else None))


def _claim_send(holder: str = "worker-frozen") -> tuple[int, str]:
    with session_scope() as s:
        j = queue.claim(s, holder, ["execute_action"])
        return j.id, j.lease_token


def _suspend_and_reclaim(jid: int) -> datetime:
    """The holder's host suspends mid-send: its lease lapses with the handler alive, and the queue gives the job up."""
    with session_scope() as s:
        s.get(Job, jid).locked_at = datetime.now(UTC) - STALE
    with session_scope() as s:
        queue.reclaim(s, {})
    with session_scope() as s:
        j = s.get(Job, jid)
        assert j.status == "failed"
        return j.held_until


def test_a_reclaimed_send_whose_holder_may_still_be_sending_is_shown_waiting_until_when(s, project):
    rid = _email(s, project)
    jid, _ = _claim_send()
    until = _suspend_and_reclaim(jid)

    waiting = [a for a in console.alerts(include_test=True) if a["kind"] == "job_waiting" and a["job_id"] == jid]
    assert [(a["project_id"], a["until"]) for a in waiting] == [(project.id, until)]
    with session_scope() as s2:
        assert s2.get(Job, jid).lock_key == f"action:{rid}"


def test_a_re_request_waits_out_a_holder_that_froze_mid_send_and_the_owner_sees_until_when(korean_owner, s, project, monkeypatch):
    rid = _email(s, project)
    jid, token = _claim_send()
    sending, thaw = threading.Event(), threading.Event()
    real = gateway.CONNECTORS["external.email_send"]

    def frozen(s2, payload, key):  # the host suspends inside the send
        sending.set()
        thaw.wait(20)
        return real(s2, payload, key)
    monkeypatch.setitem(gateway.CONNECTORS, "external.email_send", frozen)

    def holder():
        queue.current_lease.set(queue.Lease(jid, token))
        gateway.execute_action(rid)
    th = threading.Thread(target=contextvars.copy_context().run, args=(holder,))
    th.start()
    assert sending.wait(10)
    until = _suspend_and_reclaim(jid)

    with pytest.raises(HTTPException) as refused:
        console.job_re_request(jid)
    assert refused.value.status_code == 409
    assert f"{queue.local_time(until)}까지 기다려요" in refused.value.detail

    thaw.set()
    th.join(20)
    with session_scope() as s2:
        s2.execute(text("UPDATE jobs SET held_until = now() WHERE id = :id"), {"id": jid})
        assert s2.scalar(select(ActionExecution.status).where(ActionExecution.request_id == rid)) == "done"
    with pytest.raises(HTTPException, match=re.escape(msg("request.already_ran", "ko", id=1).split("(")[0])):
        console.job_re_request(jid)
    assert (settings().var_dir / "outbox" / "email" / f"req-{rid}.eml").exists()
    with session_scope() as s2:
        asked_again = AgentRequest.action_params["re_request_of"].as_integer() == rid
        assert s2.scalar(select(AgentRequest.id).where(asked_again)) is None


def test_a_holder_that_lost_its_job_while_spending_the_grant_does_not_send(s, project, monkeypatch):
    rid = _email(s, project)
    jid, token = _claim_send()
    real_consume = requests.consume_grant

    def slow_spend(s2, *a, **kw):  # the spend waits on a lock long enough for the lease to lapse and be reclaimed
        grant = real_consume(s2, *a, **kw)
        _suspend_and_reclaim(jid)
        return grant
    monkeypatch.setattr(requests, "consume_grant", slow_spend)
    queue.current_lease.set(queue.Lease(jid, token))
    try:
        with pytest.raises(queue.LeaseLost):
            gateway.execute_action(rid)
    finally:
        queue.current_lease.set(None)

    assert not (settings().var_dir / "outbox" / "email" / f"req-{rid}.eml").exists()
    with session_scope() as s2:
        row = s2.scalar(select(ActionExecution).where(ActionExecution.request_id == rid))
        assert (row.status, row.grant_id) == ("not_sent", None)
        assert s2.scalar(select(Grant.uses).where(Grant.request_id == rid)) == 0


def test_a_failed_send_spends_its_own_grant_and_a_re_request_leaves_the_sibling_approval_its_grant(
        s, project, monkeypatch):
    b = _email(s, project, "B 공지 다른")
    a = _email(s, project, "A 메일 고객")

    def dies(s2, payload, key):
        raise OSError("socket reset after DATA")
    monkeypatch.setitem(gateway.CONNECTORS, "external.email_send", dies)
    with pytest.raises(OSError):
        gateway.execute_action(a)
    with session_scope() as s2:
        assert s2.scalar(select(ActionExecution.grant_id).where(ActionExecution.request_id == a)) == \
            s2.scalar(select(Grant.id).where(Grant.request_id == a))  # a send spends its own approval's grant
        assert s2.scalar(select(Grant.uses).where(Grant.request_id == b)) == 0
        job = s2.scalar(select(Job).where(Job.lock_key == f"action:{a}"))
        job.status, job.attempts = "failed", 1
        jid = job.id
    console.job_re_request(jid)
    monkeypatch.undo()

    gateway.execute_action(b)
    with session_scope() as s2:
        assert s2.scalar(select(ActionExecution.status).where(ActionExecution.request_id == b)) == "done"


def _run_send(jid: int, token: str, rid: int) -> None:
    """What the worker does with a claimed send: run it under its lease, then record how it ended."""
    queue.current_lease.set(queue.Lease(jid, token))
    try:
        gateway.execute_action(rid)
        error = None
    except gateway.ConnectorError as exc:
        error = f"ConnectorError: {exc}"
    finally:
        queue.current_lease.set(None)
    with session_scope() as s:
        queue.finish(s, jid, token, error)


def test_a_send_that_stopped_before_anything_went_out_can_be_retried_on_the_same_approval(korean_owner, s, project, monkeypatch):
    rid = _email(s, project)
    real = gateway.CONNECTORS["external.email_send"]

    def not_configured(s2, payload, key):
        raise gateway.ConnectorError("SMTP 설정이 없어 보내기 전에 멈춤")
    monkeypatch.setitem(gateway.CONNECTORS, "external.email_send", not_configured)
    jid, token = _claim_send("worker-a")
    _run_send(jid, token, rid)
    alert = next(a for a in console.alerts(include_test=True) if a.get("job_id") == jid)
    assert (alert["kind"], alert["retryable"]) == ("job_failed", True)
    assert "밖으로 나간 건 없어요" in alert["hint"]

    monkeypatch.setitem(gateway.CONNECTORS, "external.email_send", real)  # the owner fixed the configuration
    again = console.job_retry(jid)["job_id"]
    assert console.job_retry(jid)["job_id"] == again
    with pytest.raises(HTTPException, match="대기열에 있어요"):
        console.job_re_request(jid)
    jid2, token2 = _claim_send("worker-b")
    assert jid2 == again
    _run_send(jid2, token2, rid)

    with session_scope() as s2:
        assert (s2.get(Job, again).status, s2.scalar(select(ActionExecution.status).where(
            ActionExecution.request_id == rid))) == ("done", "done")
        assert s2.scalar(select(Grant.uses).where(Grant.request_id == rid)) == 1
    assert (settings().var_dir / "outbox" / "email" / f"req-{rid}.eml").exists()


def test_a_send_that_may_have_gone_out_or_was_asked_again_is_not_retried(korean_owner, s, project, monkeypatch):
    rid = _email(s, project)

    def dies(s2, payload, key):
        raise OSError("socket reset after DATA")
    monkeypatch.setitem(gateway.CONNECTORS, "external.email_send", dies)
    jid, token = _claim_send()
    queue.current_lease.set(queue.Lease(jid, token))
    with pytest.raises(OSError):
        gateway.execute_action(rid)
    queue.current_lease.set(None)
    with session_scope() as s2:
        queue.finish(s2, jid, token, "OSError: socket reset after DATA")
    with pytest.raises(HTTPException) as refused:
        console.job_retry(jid)
    assert refused.value.status_code == 409 and "다시 요청" in refused.value.detail

    with session_scope() as s2:
        row = s2.scalar(select(ActionExecution).where(ActionExecution.request_id == rid))
        row.status = "not_sent"
    console.job_re_request(jid)
    with pytest.raises(HTTPException) as refused:
        console.job_retry(jid)
    assert refused.value.status_code == 409 and "다시 요청" in refused.value.detail


@pytest.mark.parametrize("ends", ["sent", "died"])
def test_a_send_that_happened_is_recorded_even_when_the_lease_was_lost_during_it(s, project, monkeypatch, ends):
    rid = _email(s, project)
    jid, token = _claim_send()
    lease = queue.Lease(jid, token)
    real = gateway.CONNECTORS["external.email_send"]

    def lease_lost_mid_send(s2, payload, key):  # the heartbeat finds the job reclaimed while the send is out
        result = real(s2, payload, key)
        lease.lost.set()
        if ends == "died":
            raise OSError("socket reset after DATA")
        return result
    monkeypatch.setitem(gateway.CONNECTORS, "external.email_send", lease_lost_mid_send)
    queue.current_lease.set(lease)
    try:
        if ends == "died":
            with pytest.raises(OSError):
                gateway.execute_action(rid)
        else:
            gateway.execute_action(rid)
    finally:
        queue.current_lease.set(None)

    assert (settings().var_dir / "outbox" / "email" / f"req-{rid}.eml").exists()
    with session_scope() as s2:
        row = s2.scalar(select(ActionExecution).where(ActionExecution.request_id == rid))
        assert row.status == {"sent": "done", "died": "failed"}[ends]
        assert s2.scalar(select(Grant.uses).where(Grant.request_id == rid)) == 1
