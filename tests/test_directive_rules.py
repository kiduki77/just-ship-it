"""D1 and B5: an owner directive binds every plan, request and scheduled task as rules the server checks, and the
planner re-plans finished work once at most, with new evidence (persona round 2026-11-04)."""
import pytest
from sqlalchemy import select

from justshipit.contracts.planning import DirectiveRuleOut, RealignOut
from justshipit.contracts.requests import AgentRequestDraft, RequestType, RiskLevel
from justshipit.contracts.work import AgentResultOut, ReviewOut, ReviewVerdict, TaskDraft
from justshipit.db.models import ActivityEvent, Business, Job, Project, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import company_graph, context, realign
from justshipit.services import control, projects, requests, tasks
from ask_kit import contract, by_project_manager

DIRECTIVE = "계산 규칙 재검증은 T-30으로 충분합니다. 같은 표를 다시 검증하는 업무는 더 만들지 마세요."
TABLE_RULE = DirectiveRuleOut(label="같은 표 재검증", keywords=["계산표", "재검증"])


def _realign_out(*rules: DirectiveRuleOut) -> RealignOut:
    return RealignOut(assessment="a", goal_interpretation="", out_of_scope_additions=[], cancel_task_ids=[],
                      revise_tasks=[], new_tasks=[], withdraw_requests=[], directive_rules=list(rules))


def _task(pid: int, title: str, status: str, **kw) -> Task:
    return Task(project_id=pid, key=title[:20], title=title, owner="research", objective="o",
                acceptance_criteria=["c"], status=status, **kw)


def _draft(title: str, **kw) -> TaskDraft:
    return TaskDraft(key=title[:10], title=title, owner="research", objective="o", acceptance_criteria=["c"],
                     depends_on=[], **kw)


def _give(s, project, monkeypatch, text: str, *rules: DirectiveRuleOut) -> dict:
    with session_scope() as x:
        n = control.add_directive(x, project.id, text)["n"]
    monkeypatch.setattr(realign, "run_agent", lambda *a, **k: _realign_out(*rules))
    return realign.realign(project.id, directive=n)


def test_a_directive_cancels_queued_work_it_rules_out_whatever_the_project_manager_listed(s, project, monkeypatch):
    project.status = "RUNNING"
    queued = _task(project.id, "정본 계산 엔진과 테스트 계산표 최종 재검증 (JSON 케이스)", "READY")
    other = _task(project.id, "파일럿 판정 기준을 2주 범위로 정리", "READY")
    s.add_all([queued, other])
    s.commit()

    rec = _give(s, project, monkeypatch, DIRECTIVE, TABLE_RULE)

    with session_scope() as s2:
        assert s2.get(Task, queued.id).status == "CANCELLED"
        assert s2.get(Task, other.id).status == "READY"
        d = s2.get(Project, project.id).context["directives"][0]
    assert rec["cancelled"] == [queued.id] and d["rules"] == [{"label": "같은 표 재검증", "keywords": ["계산표", "재검증"]}]


def test_a_task_running_when_the_directive_came_stops_at_its_next_step(s, project, monkeypatch):
    """T-43 was running when the owner said to stop re-verifying the table, and it ran to the end."""
    project.status = "RUNNING"
    s.commit()
    with session_scope() as x:
        t = _task(project.id, "정본 계산 엔진과 테스트 계산표 최종 재검증", "READY", max_iterations=2)
        x.add(t)
        x.flush()
        tid = t.id
    reviews = []

    def run_agent(agent, purpose, prompt, model, **kw):
        if model is AgentResultOut:   # the directive arrives while the author is writing
            _give(s, project, monkeypatch, DIRECTIVE, TABLE_RULE)
            monkeypatch.setattr(company_graph, "run_agent", run_agent)
            return AgentResultOut(summary="s", findings_markdown="f", evidence=[], assumptions=[], risks=[],
                                  open_questions=[], proposed_follow_up_tasks=[], requests=[], capability_gaps=[],
                                  financial_model=None)
        reviews.append(1)
        return ReviewOut(verdict=ReviewVerdict.PASS, criteria_met=[], criteria_unmet=[], claim_checks=[],
                         counter_arguments=[], rework_instructions=None, contested=False)

    monkeypatch.setattr(company_graph, "run_agent", run_agent)
    monkeypatch.setattr(company_graph.evidence, "verify_sources", lambda ids: None)
    company_graph.work({"project_id": project.id, "task_id": tid})

    with session_scope() as s2:
        assert s2.get(Task, tid).status == "CANCELLED"
    assert reviews == [], "no review is paid for work the owner ruled out"


def test_a_request_for_ruled_out_work_never_reaches_the_inbox(s, project, monkeypatch):
    """REQ-4 asked the owner to download KOSIS market-size files after '시장규모 집계 필요 없음'."""
    project.status = "RUNNING"
    s.commit()
    _give(s, project, monkeypatch, "시장규모 집계 필요 없음", DirectiveRuleOut(label="시장규모 집계", keywords=["시장규모"]))
    draft = AgentRequestDraft(type=RequestType.RESOURCE, title="시장규모 집계 실패: KOSIS 파일을 받아 주세요",
                              summary="자동 수집이 안 됩니다", rationale="r", options=[], recommended_option=None,
                              action_kind=None, estimated_cost_usd=None, risk_level=RiskLevel.LOW, context=contract())
    with session_scope() as s2:
        req = requests.create(s2, draft, from_agent="project_manager", project_id=project.id, origin=by_project_manager(s2, project.id))
        rid, status = req.id, req.status
    assert status == "CANCELLED"
    with session_scope() as s2:
        assert requests.pending(s2, project.id) == []
        ev = s2.scalar(select(ActivityEvent).where(ActivityEvent.verb == "directive_conflict",
                                                   ActivityEvent.entity_id == rid))
        assert "instruction #1 (P-" in ev.summary


def test_a_plan_cannot_bring_ruled_out_work_back_and_another_project_of_the_business_is_bound(s, project, monkeypatch):
    project.status = "RUNNING"
    s.commit()
    _give(s, project, monkeypatch, DIRECTIVE, TABLE_RULE)
    with session_scope() as s2:
        sibling = projects.create(s2, "다음 도전", name="Next", budget_usd=10, start=False, business=s2.get(
            Business, s2.get(Project, project.id).business_id))
        made, warnings = tasks.create_from_drafts(s2, sibling, [_draft("재고 계산표 재검증"), _draft("첫 카페 온보딩")],
                                                  origin="follow_up", plan_round=1)
        assert [t.title for t in made] == ["첫 카페 온보딩"] and "#1 (P-" in warnings[0]
        assert "[Owner instruction #1]" in context.project_brief(s2, sibling)


def test_a_directive_on_a_finished_project_still_gets_its_rules(s, project, monkeypatch):
    project.status = "DONE"
    s.commit()
    rec = _give(s, project, monkeypatch, DIRECTIVE, TABLE_RULE)
    assert rec["rules"] and rec["added"] == []
    with session_scope() as s2:
        assert s2.scalars(select(Job).where(Job.kind == "realign_project")).all()


def test_finished_work_is_re_planned_once_and_only_with_new_evidence(s, project, korean_owner):
    """원두재고 verified one table four times (T-2, T-30, T-32, T-43); 견적봇 recomputed unit economics three times."""
    s.add(_task(project.id, "카페 단위 경제성 계산", "DONE"))
    s.flush()
    made, warnings = tasks.create_from_drafts(s, project, [_draft("카페 단위 경제성 재계산")], origin="follow_up",
                                              plan_round=2)
    assert made == [] and "새 근거" in warnings[0]

    made, _ = tasks.create_from_drafts(s, project, [_draft("카페 단위 경제성 재계산", new_evidence="EV-40 원두 단가 인상")],
                                       origin="follow_up", plan_round=2)
    assert len(made) == 1
    made[0].status = "DONE"
    s.flush()
    made, warnings = tasks.create_from_drafts(s, project, [_draft("카페 단위 경제성 계산", new_evidence="EV-41")],
                                              origin="follow_up", plan_round=3)
    assert made == [] and "한 번까지" in warnings[0]


def test_the_owner_s_own_task_is_never_screened(s, project):
    s.add(_task(project.id, "카페 단위 경제성 계산", "DONE"))
    s.flush()
    made, _ = tasks.create_from_drafts(s, project, [_draft("카페 단위 경제성 계산")], origin="owner", plan_round=1)
    assert len(made) == 1


@pytest.mark.parametrize(("a", "b", "same"), [
    ("카페 단위 경제성 계산", "카페 단위 경제성 재계산", True),
    ("경쟁사 가격 조사", "첫 카페 온보딩 안내", False),
])
def test_same_work_is_judged_by_the_titles(a, b, same):
    from justshipit.services import plan_screen
    assert plan_screen.same_work(a, b) is same


# Security review M6: a rule that is too broad, invisible to the owner, or that the owner cannot take back.

def test_a_rule_with_a_one_letter_word_is_refused_and_shown_instead_of_applied(s, project, monkeypatch, korean_owner):
    """"표" sits in 목표, 발표 and 지표: the rule would cancel work the owner never named."""
    project.status = "RUNNING"
    goal = _task(project.id, "첫 달 목표 재검증", "READY")
    s.add(goal)
    s.commit()

    rec = _give(s, project, monkeypatch, DIRECTIVE, DirectiveRuleOut(label="같은 표 재검증", keywords=["표", "재검증"]))

    with session_scope() as s2:
        assert s2.get(Task, goal.id).status == "READY"
        d = s2.get(Project, project.id).context["directives"][0]
        assert d["rules"] == [] and d["refused_rules"][0]["label"] == "같은 표 재검증"
        assert "한 글자" in d["refused_rules"][0]["why"]
        assert s2.scalar(select(ActivityEvent).where(ActivityEvent.verb == "directive_rule_refused",
                                                     ActivityEvent.project_id == project.id))
    assert rec["rules"] == [] and len(rec["rules_refused"]) == 1


def test_a_stored_rule_with_a_one_letter_word_applies_no_more(s, project):
    from justshipit.services import plan_screen

    project.context = {"directives": [{"n": 1, "at": "t", "text": "x",
                                       "rules": [{"label": "같은 표 재검증", "keywords": ["표", "재검증"]}]}]}
    s.flush()
    assert plan_screen.rules(s, project) == []


def test_a_rule_that_matches_most_of_the_business_s_tasks_is_refused(s, project, monkeypatch, korean_owner):
    project.status = "RUNNING"
    titles = ["카페 고객 인터뷰", "카페 단가 조사", "카페 경쟁사 정리", "로스터리 납품 조건 정리"]
    s.add_all([_task(project.id, t, "READY") for t in titles])
    s.commit()

    rec = _give(s, project, monkeypatch, "카페 일은 그만", DirectiveRuleOut(label="카페 관련 일", keywords=["카페"]))

    assert rec["rules"] == [] and rec["cancelled"] == []
    assert "너무 넓어요" in rec["rules_refused"][0]["why"]


def test_the_owner_removes_a_rule_and_the_work_it_blocked_can_be_planned_again(s, project, monkeypatch, api_token):
    from fastapi.testclient import TestClient

    from justshipit.api.app import app

    project.status = "RUNNING"
    s.commit()
    _give(s, project, monkeypatch, DIRECTIVE, TABLE_RULE)
    api = TestClient(app, base_url="http://127.0.0.1:8000",
                     headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})

    stale = api.post(f"/api/projects/{project.id}/directives/1/rules/0/remove", json={"label": "다른 규칙"})
    assert stale.status_code == 409
    r = api.post(f"/api/projects/{project.id}/directives/1/rules/0/remove", json={"label": "같은 표 재검증"})
    assert r.status_code == 200, r.text

    with session_scope() as s2:
        p = s2.get(Project, project.id)
        d = p.context["directives"][0]
        assert d["rules"] == [] and d["removed_rules"][0]["label"] == "같은 표 재검증"
        made, _ = tasks.create_from_drafts(s2, p, [_draft("재고 계산표 재검증")], origin="follow_up", plan_round=1)
        assert [t.title for t in made] == ["재고 계산표 재검증"]
