"""D5: a reviewer's 미충족 blocks completion and the cards drafted from that output; a pass that still lists unmet
criteria is not 검토 통과 (persona round 2026-11-04: T-26, T-39 completed with a card after 미충족; T-12 showed
'완료, 검토 통과' while its own output said it was incomplete)."""
import itertools

import pytest
from sqlalchemy import select

from justshipit.agents.independence import Served
from justshipit.contracts.requests import AgentAskDraft, RequestType, RiskLevel
from justshipit.contracts.work import AgentResultOut, ReviewOut, ReviewVerdict
from justshipit.db.models import AgentRequest, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import company_graph
from ask_kit import contract, by_project_manager

CARD = AgentAskDraft(type=RequestType.APPROVAL, title="당근 동네생활 첫 글 올리기", summary="첫 글을 올립니다",
                         rationale="도우미 15명 목표", options=[], recommended_option=None, action_kind="internal.other",
                         estimated_cost_usd=None, risk_level=RiskLevel.LOW, context=contract())


def _result(unmet: list[str]) -> AgentResultOut:
    return AgentResultOut(summary="T-12는 미완료입니다" if unmet else "s", findings_markdown="f", evidence=[],
                          assumptions=[], risks=[], open_questions=[], unmet_criteria=unmet,
                          proposed_follow_up_tasks=[], requests=[CARD], capability_gaps=[], financial_model=None)


def _review(verdict: ReviewVerdict, unmet: list[str]) -> ReviewOut:
    return ReviewOut(verdict=verdict, criteria_met=[], criteria_unmet=unmet, claim_checks=[], counter_arguments=[],
                     rework_instructions="다시" if verdict == ReviewVerdict.REWORK else None, contested=False)


def _run(s, project, monkeypatch, *, reviews: list[ReviewOut], author_unmet: list[str] = (), max_iterations: int = 1):
    s.commit()
    monkeypatch.setattr(company_graph, "last_served", lambda: Served("claude", "m"))
    monkeypatch.setattr(company_graph.evidence, "verify_sources", lambda ids: None)
    queue = itertools.chain(reviews, itertools.repeat(reviews[-1]))
    monkeypatch.setattr(company_graph, "run_agent", lambda agent, purpose, prompt, model, **kw:
                        _result(list(author_unmet)) if model is AgentResultOut else next(queue))
    with session_scope() as s2:
        t = Task(project_id=project.id, key="k", title="사업체 수 집계", owner="research", objective="o",
                 acceptance_criteria=["사업체 수 집계"], status="READY", max_iterations=max_iterations)
        s2.add(t)
        s2.flush()
        tid = t.id
    company_graph.work({"project_id": project.id, "task_id": tid})
    company_graph.schedule({"project_id": project.id})   # the round settles: its cards go up together
    with session_scope() as s2:
        t = s2.get(Task, tid)
        cards = s2.scalars(select(AgentRequest).where(AgentRequest.task_id == tid)).all()
        return t.status, (t.review or {}).get("verdict"), t.error, [c.title for c in cards]


def test_a_rework_the_author_never_finished_fails_the_task_and_raises_no_card(s, project, monkeypatch, korean_owner):
    unmet = ["남의 경험을 1인칭으로 씀"]
    status, verdict, error, cards = _run(s, project, monkeypatch, reviews=[_review(ReviewVerdict.REWORK, unmet)],
                                         max_iterations=1)
    assert (status, verdict) == ("FAILED", "unmet")
    assert "검토를 통과하지 못했어요" in error and unmet[0] in error
    assert cards == []


def test_a_stalled_rework_fails_too(s, project, monkeypatch):
    status, verdict, _, cards = _run(s, project, monkeypatch, reviews=[_review(ReviewVerdict.REWORK, ["a", "b"])],
                                     max_iterations=5)
    assert (status, verdict, cards) == ("FAILED", "unmet", [])


@pytest.mark.parametrize(("review_unmet", "author_unmet"), [(["사업체 수 (도구 한계)"], []), ([], ["사업체 수 미집계"])])
def test_a_pass_that_lists_unmet_criteria_is_partial_not_passed(s, project, monkeypatch, review_unmet, author_unmet):
    status, verdict, _, cards = _run(s, project, monkeypatch, reviews=[_review(ReviewVerdict.PASS, review_unmet)],
                                     author_unmet=author_unmet)
    assert (status, verdict) == ("DONE", "partial")
    assert cards == [CARD.title]


def test_a_clean_pass_raises_the_card_after_the_review(s, project, monkeypatch):
    status, verdict, _, cards = _run(s, project, monkeypatch, reviews=[_review(ReviewVerdict.PASS, [])])
    assert (status, verdict, cards) == ("DONE", "pass", [CARD.title])


def test_a_rework_then_pass_raises_the_card_once(s, project, monkeypatch):
    status, verdict, _, cards = _run(s, project, monkeypatch, max_iterations=3,
                                     reviews=[_review(ReviewVerdict.REWORK, ["x"]), _review(ReviewVerdict.PASS, [])])
    assert (status, verdict, cards) == ("DONE", "pass", [CARD.title])


def test_a_rework_of_a_task_whose_earlier_pass_raised_a_card_leaves_one_card(s, project, monkeypatch):
    """A task mid-rework at deploy raised its card before the review (the flow before D5). Its passing pass raises
    the card at the end into the same matter, so the owner sees one card, not two."""
    from justshipit.services import requests
    old = requests.create(s, CARD.model_copy(update={"title": "동네생활 첫 글 (초안)"}), from_agent="research",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    monkeypatch.setattr(company_graph, "last_served", lambda: Served("claude", "m"))
    monkeypatch.setattr(company_graph.evidence, "verify_sources", lambda ids: None)
    monkeypatch.setattr(company_graph, "run_agent", lambda agent, purpose, prompt, model, **kw:
                        _result([]) if model is AgentResultOut else _review(ReviewVerdict.PASS, []))
    with session_scope() as s2:
        t = Task(project_id=project.id, key="k", title="사업체 수 집계", owner="research", objective="o",
                 acceptance_criteria=["사업체 수 집계"], status="READY", max_iterations=2,
                 result={"summary": "s", "request_ids": [old.id]})
        s2.add(t)
        s2.flush()
        s2.get(AgentRequest, old.id).task_id = tid = t.id
    company_graph.work({"project_id": project.id, "task_id": tid})
    company_graph.schedule({"project_id": project.id})
    with session_scope() as s2:
        cards = s2.scalars(select(AgentRequest).where(AgentRequest.task_id == tid)).all()
        assert len([c for c in cards if c.status == "PENDING"]) == 1
