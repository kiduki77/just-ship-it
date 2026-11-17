from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from justshipit.contracts.requests import (
    AgentRequestDraft, HumanDecision, HumanResponse, PolicyMode, RequestStatus, RequestType, RiskLevel,
)
from justshipit.db.models import Grant, Job, Plan
from justshipit.services import policy, requests
from owner_task_kit import LANDING_CARD
from ask_kit import by_task, contract, by_project_manager
from justshipit.contracts.work_path import HoldKind


def draft(**kw) -> AgentRequestDraft:
    base = dict(type=RequestType.APPROVAL, title="t", summary="s", rationale="r", options=[],
                recommended_option=None, action_kind=None, estimated_cost_usd=None,
                risk_level=RiskLevel.LOW, context=contract())
    return AgentRequestDraft(**{**base, **kw})


def test_policy_never_loosens_action_mode():
    # SKILL defaults to auto, but executable activation is strong_approve in tool_policy.
    assert policy.evaluate(RequestType.SKILL, "skill.activate_executable", RiskLevel.LOW, None) \
        == PolicyMode.STRONG_APPROVE
    assert policy.evaluate(RequestType.SKILL, "skill.create_candidate", RiskLevel.LOW, None) == PolicyMode.AUTO
    assert policy.evaluate(RequestType.APPROVAL, "external.pii_share", RiskLevel.LOW, None) == PolicyMode.DENY
    # Unknown actions are treated as strong_approve.
    assert policy.evaluate(RequestType.SKILL, "something.new", RiskLevel.LOW, None) == PolicyMode.STRONG_APPROVE
    # Any spending requires at least approval.
    assert policy.evaluate(RequestType.SKILL, None, RiskLevel.LOW, 5.0) == PolicyMode.APPROVE


def test_auto_request_gets_grant_and_deny_is_resolved(s, project):
    auto = requests.create(s, draft(type=RequestType.SKILL, action_kind="skill.create_candidate"),
                           from_agent="research", project_id=project.id, origin=by_project_manager(s, project.id))
    assert auto.status == RequestStatus.AUTO_APPROVED.value
    assert s.scalar(select(Grant).where(Grant.request_id == auto.id)) is not None
    denied = requests.create(s, draft(action_kind="external.pii_share"), from_agent="marketing",
                             project_id=project.id, origin=by_project_manager(s, project.id))
    assert denied.status == RequestStatus.DENIED_BY_POLICY.value


def test_strong_approval_requires_confirmation(s, project):
    r = requests.create(s, draft(action_kind="external.ads_run", estimated_cost_usd=100),
                        from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    assert r.policy_mode == PolicyMode.STRONG_APPROVE.value
    with pytest.raises(requests.ResponseRejected):
        requests.respond(s, r.id, HumanResponse(decision=HumanDecision.APPROVE))
    requests.respond(s, r.id, HumanResponse(decision=HumanDecision.APPROVE, confirm_text=f"REQ-{r.id}",
                                            max_amount_usd=30))
    with pytest.raises(requests.ResponseRejected):  # cannot respond twice
        requests.respond(s, r.id, HumanResponse(decision=HumanDecision.REJECT))


def test_grant_scope_amount_uses_and_expiry(s, project):
    r = requests.create(s, draft(action_kind="external.email_send", estimated_cost_usd=10),
                        from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    requests.respond(s, r.id, HumanResponse(decision=HumanDecision.APPROVE, max_amount_usd=10, max_uses=2))
    requests.consume_grant(s, "external.email_send", project.id, amount_usd=6)
    with pytest.raises(PermissionError):           # amount cap
        requests.consume_grant(s, "external.email_send", project.id, amount_usd=6)
    requests.consume_grant(s, "external.email_send", project.id, amount_usd=4)
    with pytest.raises(PermissionError):           # uses cap
        requests.consume_grant(s, "external.email_send", project.id, amount_usd=0)
    g = s.scalar(select(Grant).where(Grant.request_id == r.id))
    g.uses, g.expires_at = 0, datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(PermissionError):           # expired
        requests.consume_grant(s, "external.email_send", project.id)


def test_question_answer_lands_in_context_and_resumes_waiting_project_after_its_re_plan(s, project):
    """The owner's words can change direction, so the waiting run resumes once the CEO has re-planned around them
    (the realign resumes it, tests/test_respond_followed.py), never before."""
    project.status = "WAITING_HUMAN"
    q = requests.create(s, draft(type=RequestType.QUESTION, context=contract(HoldKind.RUN)), from_agent="project_manager", project_id=project.id,
                        origin=by_project_manager(s, project.id))
    assert q.status == RequestStatus.PENDING.value
    requests.respond(s, q.id, HumanResponse(decision=HumanDecision.ANSWER, answer="기관 우선"))
    assert project.context["owner_answers"][0]["answer"] == "기관 우선"
    jobs = {j.kind: j for j in s.scalars(select(Job).where(Job.payload["project_id"].as_integer() == project.id))}
    assert jobs["realign_project"].payload == {"project_id": project.id, "request_id": q.id, "resume_after": True}
    assert "resume_project" not in jobs


def test_a_development_ask_starts_its_product_plan_without_a_card_of_its_own(s, project):
    """The owner decides the build on the plan's card, then on the spec's; the ask itself needs no card."""
    r = requests.create(s, draft(type=RequestType.DEVELOPMENT, action_kind="dev.implementation_start"),
                        from_agent="product", project_id=project.id, origin=by_project_manager(s, project.id))
    assert isinstance(r, Plan)
    assert s.scalar(select(Job).where(Job.idempotency_key == f"write_plan:{r.id}")) is not None
    assert s.scalar(select(Job).where(Job.kind == "write_spec",
                                      Job.payload["project_id"].as_integer() == project.id)) is None


def test_similar_pending_requests_are_merged(s, project):
    a = requests.create(s, draft(title="법률 전문가 검토 의뢰 (개인정보·장기요양)", action_kind="data.purchase"),
                        from_agent="research", project_id=project.id, origin=by_project_manager(s, project.id))
    b = requests.create(s, draft(title="개인정보·장기요양 법률 전문가 검토", action_kind="data.purchase", context=contract(HoldKind.THIS_TASK)),
                        from_agent="risk", project_id=project.id, origin=by_task(s, project.id))
    assert a.id == b.id and b.blocking and "[Risk & audit asked the same]" in b.rationale, "named in the owner's words"
    c = requests.create(s, draft(title="경쟁사 유료 리포트 구매", action_kind="data.purchase"),
                        from_agent="research", project_id=project.id, origin=by_project_manager(s, project.id))
    assert c.id != a.id


def test_unknown_action_kinds_are_normalized_and_inbox_consolidation(s, project):
    from justshipit.contracts.planning import RequestMergeOut, RequestWithdrawOut
    a = requests.create(s, draft(title="전남 기관 인터뷰 섭외 연락", action_kind="external.interview_outreach_x"),
                        from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    assert a.action_kind == "external.other" and a.action_params["proposed_action_kind"] == "external.interview_outreach_x"
    assert a.policy_mode == "strong_approve"
    b = requests.create(s, draft(title="세그먼트 검증 인터뷰 모집", action_kind="external.interview_recruit"),
                        from_agent="research", project_id=project.id, origin=by_project_manager(s, project.id))
    c = requests.create(s, draft(title="사전관심 페이지 게시", action_kind="external.landing_publish",
                                 post_card=LANDING_CARD), from_agent="marketing", project_id=project.id,
                                 origin=by_project_manager(s, project.id))
    done = requests.consolidate(s, project.id, [RequestMergeOut(keep_id=a.id, merge_ids=[b.id, 999], merged_title="인터뷰 섭외·모집 연락 승인", reason="같은 결정")],
                                [RequestWithdrawOut(id=c.id, reason="결정으로 불필요")])
    assert done == {"merged": [b.id], "withdrawn": [c.id]}
    assert a.title == "인터뷰 섭외·모집 연락 승인" and "REQ-" in a.rationale
    assert b.status == "CANCELLED" and b.response["into"] == f"REQ-{a.id}"
    assert {r.id for r in requests.pending(s, project.id)} == {a.id}
