"""Owner guidance must persist and steer work that has not started (stubbed model calls)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from justshipit.contracts.planning import RealignOut, RequestWithdrawOut, TaskRevisionOut
from justshipit.contracts.requests import HumanDecision, HumanResponse
from justshipit.contracts.work import TaskDraft
from justshipit.db.models import AgentRequest, Job, Project, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import realign
from justshipit.services import requests
from ask_kit import contract, by_project_manager


def _req(s, pid: int, title: str) -> int:
    r = AgentRequest(project_id=pid, from_agent="research", type="APPROVAL", title=title, summary="s",
                     rationale="r", status="PENDING", policy_mode="approve")
    s.add(r)
    s.flush()
    return r.id


def test_every_owner_answer_and_note_is_kept(s, project):
    project.status = "RUNNING"
    ids = [_req(s, project.id, f"요청 {i}") for i in range(3)]
    s.commit()
    with session_scope() as s2:
        requests.respond(s2, ids[0], HumanResponse(decision=HumanDecision.APPROVE, chosen_option="진행"))
    with session_scope() as s2:
        requests.respond(s2, ids[1], HumanResponse(decision=HumanDecision.DEFER, note="오픈소스 루트로 가자"))
    with session_scope() as s2:
        requests.respond(s2, ids[2], HumanResponse(decision=HumanDecision.DEFER, note="창업 패키지 말고"))
    with session_scope() as s2:
        answers = s2.get(Project, project.id).context["owner_answers"]
        realigns = s2.scalars(select(Job).where(Job.kind == "realign_project",
                                                Job.payload["project_id"].as_integer() == project.id)).all()
    assert [a["note"] for a in answers] == [None, "오픈소스 루트로 가자", "창업 패키지 말고"]
    assert len(realigns) == 2   # only free-text guidance triggers a realignment


def test_realign_changes_only_unstarted_work(s, project, monkeypatch):
    project.status = "RUNNING"
    mk = lambda key, st: Task(project_id=project.id, key=key, title=key, owner="research", objective="o",
                              acceptance_criteria=["c"], status=st)
    running, old, fix = mk("running", "RUNNING"), mk("kstartup", "PROPOSED"), mk("segments", "READY")
    s.add_all([running, old, fix])
    s.flush()
    off_track = _req(s, project.id, "K-Startup 문의")
    trigger = _req(s, project.id, "제약 조건")
    s.get(AgentRequest, trigger).status = "ANSWERED"
    s.get(AgentRequest, trigger).response = {"answer": "오픈소스, 월드와이드"}
    s.commit()
    out = RealignOut(
        assessment="오픈소스 전환", goal_interpretation="오픈소스 기반 글로벌 사업화",
        out_of_scope_additions=["정부 창업지원 사업"], cancel_task_ids=[old.id, running.id],
        revise_tasks=[TaskRevisionOut(task_id=fix.id, title="글로벌 OSS 세그먼트", objective="o2", acceptance_criteria=["c2"])],
        new_tasks=[TaskDraft(key="oss_models", title="오픈코어 수익모델", owner="strategy", objective="o",
                             acceptance_criteria=["c"], depends_on=[])],
        withdraw_requests=[RequestWithdrawOut(id=off_track, reason="오너가 배제")])
    monkeypatch.setattr(realign, "run_agent", lambda *a, **k: out)
    rec = realign.realign(project.id, trigger)
    with session_scope() as s2:
        assert s2.get(Task, old.id).status == "CANCELLED"
        assert s2.get(Task, running.id).status == "RUNNING"          # in-flight work is never touched
        assert s2.get(Task, fix.id).title == "글로벌 OSS 세그먼트"
        assert s2.get(AgentRequest, off_track).status == "CANCELLED"
        p = s2.get(Project, project.id)
        assert "정부 창업지원 사업" in p.plan["out_of_scope"] and p.context["realignments"][-1]["added"] == rec["added"]
    assert rec["cancelled"] == [old.id] and len(rec["added"]) == 1


def test_stale_resume_does_not_skip_an_open_interrupt(s, project, monkeypatch):
    from justshipit.orchestration import runner as orch

    calls = []
    monkeypatch.setattr(orch, "_invoke", lambda pid, payload: calls.append(pid) or {})
    monkeypatch.setattr(orch, "snapshot", lambda pid: {"next": ["ceo_review"], "interrupts": [{"x": 1}]})
    project.status = "WAITING_HUMAN"
    rid = _req(s, project.id, "막힌 요청")
    s.get(AgentRequest, rid).hold_scope = "run"
    s.commit()
    assert "blocking" in orch.resume(project.id)["skipped"]
    with session_scope() as s2:
        s2.get(AgentRequest, rid).status = "ANSWERED"
        s2.get(Project, project.id).status = "RUNNING"
    assert "RUNNING" in orch.resume(project.id)["skipped"]     # a duplicate after the run already resumed
    with session_scope() as s2:
        s2.get(Project, project.id).status = "WAITING_HUMAN"
    orch.resume(project.id)
    assert calls == [project.id]


def test_model_drafted_request_cannot_carry_a_system_action(s, project):
    from justshipit.contracts.requests import AgentRequestDraft, RequestType, RiskLevel

    def draft(kind):
        return AgentRequestDraft(type=RequestType.APPROVAL, title=f"계획 승인 {kind}", summary="s", rationale="r",
                                 options=["승인"], recommended_option="승인", action_kind=kind,
                                 estimated_cost_usd=None, risk_level=RiskLevel.LOW, context=contract())
    agent = requests.create(s, draft("project.reassess"), from_agent="strategy", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    server = requests.create(s, draft("dev.merge"), from_agent="product", project_id=project.id, system=True,
                             origin=by_project_manager(s, project.id))
    assert agent.action_kind == "internal.other" and agent.action_params["proposed_action_kind"] == "project.reassess"
    assert server.action_kind == "dev.merge"


def test_expired_request_can_still_be_closed_but_not_approved(s, project):
    rid = _req(s, project.id, "기한 지난 요청")
    req = s.get(AgentRequest, rid)
    req.hold_scope = "run"
    req.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    s.commit()
    with pytest.raises(requests.ResponseRejected), session_scope() as s2:
        requests.respond(s2, rid, HumanResponse(decision=HumanDecision.APPROVE))
    with session_scope() as s2:
        assert requests.respond(s2, rid, HumanResponse(decision=HumanDecision.REJECT)).status == "REJECTED"
