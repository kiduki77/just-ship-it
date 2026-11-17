"""Meeting governance with stubbed model calls (no subscription usage)."""
from justshipit.contracts.planning import (
    ProjectManagerReviewAction, ProjectManagerReviewOut, DecisionKind, DecisionOut, InboxCleanupOut, PositionOut,
)
from justshipit.db.models import AgentRequest, Decision, Project, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import company_graph


def test_index_citations_become_ledger_ids():
    pos = PositionOut(stance="conditional", statement="분모 미확정[E1][E2], 범위 밖 [E9]",
                      evidence_ids=["EV-943", "EV-904"], concerns=["가정 의존 [E2]"])
    out = company_graph._resolve_citations(pos)
    assert out.statement == "분모 미확정[EV-943][EV-904], 범위 밖 [E9]"
    assert out.concerns == ["가정 의존 [EV-904]"]


def _review(action: ProjectManagerReviewAction) -> ProjectManagerReviewOut:
    return ProjectManagerReviewOut(assessment="a", unmet_success_criteria=[], action=action, meeting_agenda=None,
                        meeting_participants=[], follow_up_tasks=[], requests=[])


def test_contested_work_forces_a_meeting_before_closing(s, project, monkeypatch):
    s.add(Task(project_id=project.id, key="k", title="경쟁 분석", owner="strategy", objective="o",
               acceptance_criteria=["c"], status="DONE", contested=True,
               review={"counter_arguments": ["중복률 수작업 집계"], "criteria_unmet": []}))
    s.commit()
    monkeypatch.setattr(company_graph, "run_agent", lambda *a, **k: _review(ProjectManagerReviewAction.COMPLETE))
    assert company_graph.ceo_review({"project_id": project.id}) == {"next": "meeting"}
    with session_scope() as s2:
        pending = s2.get(Project, project.id).context["pending_meeting"]
    assert pending["participants"] == ["strategy", "risk"]
    assert "경쟁 분석" in pending["agenda"] and "중복률 수작업 집계" in pending["agenda"]


def _decision(approval: bool) -> DecisionOut:
    return DecisionOut(title="B 방향", decision=list(DecisionKind)[0], rationale="r", supporting_evidence_ids=[],
                       dissenting_evidence_ids=[], dissent_summary="", open_questions=[], action_tasks=[],
                       approval_required=approval, approval_reason=None)


def _pending(s, project, title: str, type_: str = "QUESTION") -> int:
    r = AgentRequest(project_id=project.id, from_agent="research", type=type_, title=title, summary="s",
                     rationale="r", status="PENDING", policy_mode="approve")
    s.add(r)
    s.commit()
    return r.id


def test_the_project_manager_reads_the_cards_still_waiting_for_the_owner(s, project, monkeypatch):
    """Maya REQ-68 and Daniel REQ-64: the CEO asked again what a pending card already asked, because its review and
    decision prompts listed answered cards but none still waiting."""
    rid = _pending(s, project, "첫 달 가격을 정해 주세요")
    prompts: dict[str, str] = {}

    def fake(agent, purpose, prompt, model, **kw):
        prompts[model.__name__] = prompt
        return _review(ProjectManagerReviewAction.COMPLETE) if model is ProjectManagerReviewOut else _decision(False)
    monkeypatch.setattr(company_graph, "run_agent", fake)
    company_graph.ceo_review({"project_id": project.id})
    company_graph.decide({"project_id": project.id})

    for name in ("ProjectManagerReviewOut", "DecisionOut"):
        assert f"REQ-{rid}" in prompts[name] and "첫 달 가격을 정해 주세요" in prompts[name], name


def test_the_inbox_cleanup_can_merge_into_the_decisions_own_card(s, project, monkeypatch):
    """Daniel REQ-61 and REQ-64: the cleanup listing left out the decision's own card, so it could not merge into it."""
    rid = _pending(s, project, "B 방향으로 갈까요?", "APPROVAL")
    prompts: dict[str, str] = {}

    def fake(agent, purpose, prompt, model, **kw):
        prompts[model.__name__] = prompt
        return _decision(True) if model is DecisionOut else InboxCleanupOut(merges=[], withdrawals=[])
    monkeypatch.setattr(company_graph, "run_agent", fake)
    company_graph.decide({"project_id": project.id})

    with session_scope() as s2:
        own = s2.get(Decision, s2.get(Project, project.id).context["final_decision_id"]).approval_request_id
    listing = prompts["InboxCleanupOut"]
    assert f"- {own}:" in listing and f"- {rid}:" in listing


def test_no_contest_closes_normally(s, project, monkeypatch):
    s.commit()
    monkeypatch.setattr(company_graph, "run_agent", lambda *a, **k: _review(ProjectManagerReviewAction.COMPLETE))
    assert company_graph.ceo_review({"project_id": project.id}) == {"next": "decide"}
