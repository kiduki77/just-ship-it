"""D2: an owner task made from a decision follows the option the owner chose (persona round 2026-11-04)."""
import pytest
from owner_task_kit import approve, draft, events, post_card, tasks_of, what

from justshipit.contracts.requests import CardOption, DecisionCard, HumanDecision, HumanResponse, RequestType
from justshipit.services import owner_tasks, requests
from ask_kit import by_project_manager


def _legal_card() -> DecisionCard:
    opt = lambda label, summary, rec=False: CardOption(label=label, verdict=HumanDecision.APPROVE, summary=summary,
                                                       evidence_ids=[], risks=[], recommended=rec)
    return DecisionCard(headline="법률 자문을 언제 받을까요?", owner_does="변호사에게 7개 질문을 직접 물어요",
                        why="국외 이전과 NDA는 파일럿 전에 정해야 해요", metrics=[],
                        options=[opt("6주차", "파일럿 6주차에 변호사에게 7개 질문을 한 번에 물어요", True),
                                 opt("첫 사장 메일 전", "첫 사장 메일을 받기 전에 변호사에게 물어요")],
                        recommendation="6주차를 추천해요", reversible=True)


def test_the_task_follows_the_option_the_owner_chose_not_the_request_s_question(s, project):
    """OT-4: the owner chose 6주차 and the task still said '첫 사장 메일을 받기 전에…물을지 정한다'."""
    req = requests.create(s, draft(type=RequestType.PROPOSAL, title="법률 자문 1회: 국외 이전·NDA·수탁자 지위",
                                   summary="첫 사장 메일을 받기 전에 변호사에게 7개 질문을 물을지 정한다.",
                                   options=["6주차", "첫 사장 메일 전"], action_kind="external.legal_consult",
                                   card=_legal_card()),
                          from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))
    approve(s, req, chosen_option="6주차", note="파일럿 결과를 본 뒤에")
    s.commit()

    (task,) = tasks_of(req.id)
    assert task.title == "법률 자문 1회: 국외 이전·NDA·수탁자 지위", "the title is the action; the card says the pick"
    assert task.card["what"].startswith("6주차") and "파일럿 6주차" in task.card["what"]
    assert "받기 전에" not in task.card["what"]
    assert task.card["chosen_option"] == "6주차" and task.card["owner_note"] == "파일럿 결과를 본 뒤에"


@pytest.mark.parametrize("option", ["지금은 올리지 않음", "보류", "나중에 다시 보기", "이번 주는 건너뛰기"])
def test_approving_an_option_that_says_not_now_makes_no_task(s, project, option):
    """OT-12: '지금은 올리지 않음' was approved and an owner task '…올릴지 결정' was created anyway. An option whose
    words say 반려 or 보류 is sent as that decision (C's typed options); one whose words say neither is approved, and
    the task still sees that it declines."""
    req = requests.create(s, draft(title=f"당근알바 도우미 공고 {option}", options=["올림", option],
                                   post_card=post_card("other", where_url=None, what=what(
                                       title="망원동 산책 도우미 대기자", body="망원동 산책 도우미 대기자를 받아요.",
                                       facts=[], expected_questions=[]))), from_agent="marketing",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    verdict = next(c["verdict"] for c in requests.choices(req) if c["label"] == option) or HumanDecision.APPROVE
    requests.respond(s, req.id, HumanResponse(decision=verdict, chosen_option=option))
    s.commit()

    assert tasks_of(req.id) == []
    if verdict == HumanDecision.APPROVE:
        assert [e.verb for e in events("request", req.id)][-1] == "owner_task_not_needed"


@pytest.mark.parametrize("option", ["올림", "주말 제외하고 올림", "내용 수정 후 올림", "6주차"])
def test_an_option_that_acts_is_not_a_decline(option):
    assert not owner_tasks.declines(option)


def test_the_post_card_task_keeps_the_chosen_option(s, project):
    req = requests.create(s, draft(options=["화요일 오전에 올림", "보류"]), from_agent="marketing", project_id=project.id,
                          origin=by_project_manager(s, project.id))
    approve(s, req, chosen_option="화요일 오전에 올림")
    s.commit()

    (task,) = tasks_of(req.id)
    assert task.card["chosen_option"] == "화요일 오전에 올림" and task.card["channel"] == "hacker_news"


@pytest.mark.parametrize("options", [["Post it", "Don't post"], ["올려요", "지금은 올리지 않음"]])
def test_a_to_do_from_a_yes_or_no_card_is_titled_with_the_action_alone(s, project, options):
    """OT-19..24 read 'LinkedIn first message to agency contacts (Post it)': the only option that acts is the title's
    own action, so naming it again says nothing. A choice between two ways to act (6주차 or not) is named on the card,
    not in a parenthetical after the title (round 4: 'Make monday-support-digest usable (Run it on my computer)')."""
    req = requests.create(s, draft(title="LinkedIn first message to agency contacts", options=options),
                          from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    approve(s, req, chosen_option=options[0])
    s.commit()

    (task,) = tasks_of(req.id)
    assert task.title == "LinkedIn first message to agency contacts"
    assert task.card["chosen_option"] == options[0]
