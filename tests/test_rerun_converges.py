from datetime import datetime, timedelta, timezone

import pytest
from story_kit import JOURNEYS
from story_kit import P1 as P1_STORIES
from justshipit.contracts.planning import ProjectManagerReviewAction, ProjectManagerReviewOut, DecisionKind, DecisionOut
from justshipit.db.models import AgentRequest, Decision, Project, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import company_graph, runner
from sqlalchemy import func, select


def _count(model, *where) -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def test_rerun_after_the_decision_reuses_it_instead_of_deciding_again(s, project, monkeypatch):
    calls: list[str] = []

    def fake(agent, purpose, prompt, model, **kw):
        calls.append(model.__name__)
        if model is ProjectManagerReviewOut:
            return ProjectManagerReviewOut(assessment="ok", unmet_success_criteria=[], action=ProjectManagerReviewAction.COMPLETE,
                                meeting_agenda=None, meeting_participants=[], follow_up_tasks=[], requests=[])
        if model is DecisionOut:
            return DecisionOut(title="진입 보류", decision=list(DecisionKind)[0], rationale="근거 부족",
                               supporting_evidence_ids=[], dissenting_evidence_ids=[], dissent_summary="",
                               open_questions=[], action_tasks=[], approval_required=True, approval_reason="방향 변경")
        raise RuntimeError(f"report step failed ({model.__name__})")
    monkeypatch.setattr(company_graph, "run_agent", fake)
    project.plan = {"goal_interpretation": "계획됨"}
    s.add(Task(project_id=project.id, key="t1", title="조사", owner="research", objective="o",
               acceptance_criteria=["c"], status="DONE", finished_at=datetime.now(timezone.utc) - timedelta(hours=1)))
    s.commit()

    runner.start(project.id)  # decides, then waits at the final gate for the owner's approval
    approval = AgentRequest.project_id == project.id, AgentRequest.action_kind == "project.direction_change"
    with session_scope() as s2:
        s2.scalar(select(AgentRequest).where(*approval)).status = "APPROVED"
    with pytest.raises(RuntimeError):
        runner.resume(project.id)

    runner.graph().checkpointer.delete_thread(f"project-{project.id}")
    calls.clear()
    with pytest.raises(RuntimeError, match="report step failed"):
        try:
            runner.retry(project.id)
        finally:
            assert calls == ["FinalReportOut"]

    assert _count(Decision, Decision.project_id == project.id) == 1
    assert _count(AgentRequest, *approval) == 1
    with session_scope() as s2:
        assert s2.get(Project, project.id).status == "FAILED"

    # New work finished after the decision: the next run decides again on the new results.
    with session_scope() as s2:
        s2.add(Task(project_id=project.id, key="t2", title="추가 조사", owner="research", objective="o",
                    acceptance_criteria=["c"], status="DONE", finished_at=datetime.now(timezone.utc)))
    runner.graph().checkpointer.delete_thread(f"project-{project.id}")
    calls.clear()
    runner.retry(project.id)
    assert calls == ["ProjectManagerReviewOut", "DecisionOut"]
    assert _count(Decision, Decision.project_id == project.id) == 2


def test_writing_the_spec_again_for_the_same_request_keeps_one_spec(s, project, monkeypatch):
    from justshipit.contracts.handoff import RequirementOut, SpecDraftOut
    from justshipit.db.models import Spec
    from justshipit.engineering import specs
    calls: list[str] = []

    def fake(agent, purpose, prompt, model, **kw):
        calls.append(purpose)
        return SpecDraftOut(title="예약 알림", prd_markdown="문제", design_notes_markdown="설계",
                            requirements=[RequirementOut(id="REQ-1", statement="알림", priority="must", rationale="r")],
                            acceptance_criteria=["Given/When/Then"], suggested_target_repository="reminder",
                            suggested_test_command="pytest", suggested_test_image="python:3.13-slim",
                            user_stories=P1_STORIES, journeys=JOURNEYS)
    monkeypatch.setattr(specs, "run_agent", fake)
    req = AgentRequest(project_id=project.id, from_agent="product", type="DEVELOPMENT", title="개발 제안: 예약 알림",
                       summary="s", rationale="r", status="APPROVED", policy_mode="approve",
                       action_kind="dev.implementation_start", action_params={"need": {"title": "예약 알림"}})
    s.add(req)
    s.commit()

    first = specs.write_spec(project.id, req.id)
    again = specs.write_spec(project.id, req.id)

    assert again == first
    assert calls == ["spec"]
    assert _count(Spec, Spec.project_id == project.id) == 1


def test_the_spec_writer_reads_the_cited_task_and_the_engineer_gets_every_result_whole(s, project, monkeypatch):
    """A spec said "이 작업에서는 T-30 원문을 볼 수 없어 §4 규칙에서 값을 다시 계산했다": the CPO saw a 16,000-character
    report excerpt and no task results. The tasks the request names are in the prompt (a cut says where the rest
    is), and every result is committed beside the spec for the engineer to read whole."""
    from justshipit.contracts.handoff import RequirementOut, SpecDraftOut
    from justshipit.db.models import Spec
    from justshipit.engineering import specs
    from justshipit.services import registry
    monkeypatch.setattr(specs, "CITED_TASK_CHARS", 500)
    table = "| 원두 | 재고 |\n" + "".join(f"| bean-{i} | {i} |\n" for i in range(60))
    cited = Task(project_id=project.id, key="t30", title="재고 기준표", owner="research", objective="o",
                 acceptance_criteria=[], status="DONE", result={"summary": "기준표 60행", "findings_markdown": table})
    other = Task(project_id=project.id, key="t31", title="경쟁사", owner="research", objective="o",
                 acceptance_criteria=[], status="DONE", result={"summary": "경쟁사 3곳", "findings_markdown": "본문"})
    s.add_all([cited, other])
    s.flush()
    prompts: list[str] = []

    def fake(agent, purpose, prompt, model, **kw):
        prompts.append(prompt)
        return SpecDraftOut(title="재고 알림", prd_markdown="문제", design_notes_markdown="설계",
                            requirements=[RequirementOut(id="REQ-1", statement="알림", priority="must", rationale="r")],
                            acceptance_criteria=["Given/When/Then"], suggested_target_repository="stock",
                            suggested_test_command="pytest", suggested_test_image="python:3.13-slim",
                            user_stories=P1_STORIES, journeys=JOURNEYS)
    monkeypatch.setattr(specs, "run_agent", fake)
    req = AgentRequest(project_id=project.id, from_agent="product", type="DEVELOPMENT", title="개발 제안: 재고 알림",
                       summary="s", rationale="r", status="APPROVED", policy_mode="approve",
                       action_kind="dev.implementation_start",
                       action_params={"need": {"title": "재고 알림", "problem": f"T-{cited.id}의 기준표대로 알린다"}})
    s.add(req)
    s.commit()

    spec = s.get(Spec, specs.write_spec(project.id, req.id))

    [prompt] = prompts
    assert table[:500] in prompt and f"The whole text is in sources/T-{cited.id}.md." in prompt
    assert f"(the full text is in sources/T-{other.id}.md)" in prompt and "경쟁사 3곳" in prompt
    assert registry.read_at(f"{spec.registry_path}/sources/T-{cited.id}.md", spec.draft_commit_sha).endswith(
        table.strip())


def _decided(s, project, *, approval: bool = False) -> int:
    """A project that recorded its final decision after its last task finished, then failed in a later step."""
    s.add(Task(project_id=project.id, key="k", title="t", owner="research", objective="o", acceptance_criteria=[],
               status="DONE", finished_at=datetime.now(timezone.utc) - timedelta(hours=1)))
    s.commit()
    with session_scope() as s2:
        d = Decision(project_id=project.id, title="A 방향", decision="GO", rationale="r", supporting_evidence_ids=[],
                     dissenting_evidence_ids=[], open_questions=[], approval_required=approval, status="EFFECTIVE")
        s2.add(d)
        s2.flush()
        if approval:
            r = AgentRequest(project_id=project.id, from_agent="project_manager", type="APPROVAL", title="의사결정 승인: A 방향",
                             summary="s", rationale="r", action_kind="project.direction_change", status="PENDING",
                             policy_mode="approve", hold_scope="run")
            s2.add(r)
            s2.flush()
            d.approval_request_id = r.id
        p = s2.get(Project, project.id)
        p.context = {**(p.context or {}), "final_decision_id": d.id}
        p.status = "WAITING_HUMAN"
        return d.id


def _rerun_decide(monkeypatch, pid: int) -> tuple[list[str], int]:
    calls: list[str] = []

    def fake(agent, purpose, prompt, model, **kw):
        calls.append(model.__name__)
        return DecisionOut(title="B 방향", decision=list(DecisionKind)[0], rationale="오너 지시", supporting_evidence_ids=[],
                           dissenting_evidence_ids=[], dissent_summary="", open_questions=[], action_tasks=[],
                           approval_required=False, approval_reason=None)
    monkeypatch.setattr(company_graph, "run_agent", fake)
    company_graph.decide({"project_id": pid})
    with session_scope() as s:
        return calls, s.get(Project, pid).context["final_decision_id"]


def test_a_rerun_decides_again_after_an_owner_directive_that_came_after_the_decision(s, project, monkeypatch):
    from justshipit.services import control
    dec = _decided(s, project)
    with session_scope() as s2:
        control.add_directive(s2, project.id, "A 방향은 접고 B 방향으로 결론을 내라")

    calls, final = _rerun_decide(monkeypatch, project.id)

    assert calls == ["DecisionOut"] and final != dec


def test_a_rerun_decides_again_after_the_owner_answered_a_request_after_the_decision(s, project, monkeypatch):
    from justshipit.contracts.requests import HumanDecision, HumanResponse
    from justshipit.services import requests
    dec = _decided(s, project)
    with session_scope() as s2:
        q = AgentRequest(project_id=project.id, from_agent="research", type="QUESTION", title="타깃 고객?",
                         summary="s", rationale="r", status="PENDING", policy_mode="approve")
        s2.add(q)
        s2.flush()
        requests.respond(s2, q.id, HumanResponse(decision=HumanDecision.ANSWER, answer="B 고객만"))

    calls, final = _rerun_decide(monkeypatch, project.id)

    assert calls == ["DecisionOut"] and final != dec


def test_a_rerun_decides_again_after_a_realign_changed_the_plan_after_the_decision(s, project, monkeypatch):
    from justshipit.orchestration import realign
    from justshipit.services import control
    s.commit()
    with session_scope() as s2:
        s2.get(Project, project.id).status = "WAITING_HUMAN"
        control.add_directive(s2, project.id, "B 방향으로 바꿔라")
    dec = _decided(s, project)
    from justshipit.contracts.planning import RealignOut
    monkeypatch.setattr(realign, "run_agent", lambda *a, **k: RealignOut(
        assessment="B로 전환", goal_interpretation="B 방향", out_of_scope_additions=["A 방향"], cancel_task_ids=[],
        revise_tasks=[], new_tasks=[], withdraw_requests=[]))
    realign.realign(project.id, directive=1)

    calls, final = _rerun_decide(monkeypatch, project.id)

    assert calls == ["DecisionOut"] and final != dec


def test_a_rerun_reuses_a_decision_the_owner_approved_with_nothing_newer(s, project, monkeypatch):
    from justshipit.contracts.requests import HumanDecision, HumanResponse
    from justshipit.services import requests
    dec = _decided(s, project, approval=True)
    with session_scope() as s2:
        rid = s2.get(Decision, dec).approval_request_id
        requests.respond(s2, rid, HumanResponse(decision=HumanDecision.APPROVE, note="좋습니다"))

    calls, final = _rerun_decide(monkeypatch, project.id)

    assert calls == [] and final == dec
