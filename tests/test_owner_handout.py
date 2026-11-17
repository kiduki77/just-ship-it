"""What the owner sends or follows by hand is on the to-do, whole; the company fixes its own text instead of asking the
owner to; a message keeps one register; and a to-do the owner carries out by hand takes no typed id.

Persona round 3: 이준호 dug the 다온몰 message out of the Documents tab while his to-do (OT-35) said nothing of it and
his contact to-do said "회사는 초안을 쓰지 않아요"; approving his own KakaoTalk message took typing REQ-88; the message
read "오랜만에 연락드립니다. 이준호예요."; and 박정숙's post card (REQ-85) asked her to change 19:30 to 20:00 in the
company's own reply text."""

import pytest
from sqlalchemy import select

from justshipit.agents.independence import Served
from justshipit.contracts.requests import AgentAskDraft, Handout, RequestType, RiskLevel
from justshipit.contracts.work import AgentResultOut, ReviewOut, ReviewVerdict
from justshipit.db.models import Task
from justshipit.db.session import session_scope
from justshipit.i18n import LANGS, msg
from justshipit.orchestration import company_graph
from justshipit.services import owner_tasks, requests
from owner_task_kit import approve, draft, post_card, tasks_of, what
from ask_kit import contract, by_project_manager

MESSAGE = ("안녕하세요 실장님, 오랜만에 연락드립니다. 반품 예측 서비스를 준비하고 있어서, 주문·반품 파일의 "
           "맨 윗줄(열 이름)만 보내 주실 수 있을지 여쭙습니다. 고객 정보는 받지 않습니다. 감사합니다.")


def outreach(**kw) -> AgentAskDraft:
    base = dict(type=RequestType.APPROVAL, title="다온몰에 1단계 데이터 요청 보내기", summary="다온몰에 열 이름 줄만 물어봐요",
                rationale="웹으로는 열 이름을 확인하지 못했어요", options=[], recommended_option=None,
                action_kind="external.other", estimated_cost_usd=None, risk_level=RiskLevel.LOW, context=contract(),
                handout=Handout(kind="message", to="다온몰 실장님", body=MESSAGE))
    return AgentAskDraft(**{**base, **kw})


# ------------------------------------------------------------------ the text is on the to-do

def test_the_message_the_company_wrote_is_on_the_to_do_whole(s, project):
    req = requests.create(s, outreach(), from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    approve(s, req, confirm_text=f"REQ-{req.id}")
    s.commit()
    (task,) = tasks_of(req.id)
    shown = owner_tasks.view(task, "ko")["card"]["handout"]
    assert shown == {"kind": "message", "to": "다온몰 실장님", "body": MESSAGE}
    assert requests.report(s, req, "en")["handout"]["body"] == MESSAGE, "the decision card shows it too"


def test_steps_for_the_owner_become_a_to_do_with_the_steps(s, project):
    steps = "1. 스마트플레이스 앱을 설치해요.\n2. 가게 사진 3장을 올려요.\n3. 영업시간을 저녁 8시로 적어요."
    req = requests.create(s, outreach(title="스마트플레이스 첫 정비", handout=Handout(kind="steps", to=None, body=steps)),
                          from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    approve(s, req, confirm_text=f"REQ-{req.id}")
    s.commit()
    (task,) = tasks_of(req.id)
    assert task.card["handout"]["body"] == steps


def test_a_handout_that_asks_for_stars_or_reviews_is_refused_like_any_card(s, project):
    req = requests.create(s, outreach(handout=Handout(kind="message", to="단골", body="별점 5개 리뷰 부탁드려요!")),
                          from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    assert req.status == "DENIED_BY_POLICY"


def test_a_person_card_no_longer_claims_the_company_wrote_nothing(s):
    """The line was false when a task had written the message, and the checklist already says 'in your own words'."""
    from justshipit.services import people_tasks
    from people_kit import add, make_business
    card = people_tasks.person_card(s, add(s, make_business(s)), "contact", lang="ko")
    assert "note" not in card
    for lang in LANGS:
        stored = {**card, "note": msg("people.card.no_draft", lang)}
        assert "note" not in owner_tasks.readable_card(stored, "ko"), "an old row's line is dropped when read"


# ------------------------------------------------------------------ what goes back to the author

@pytest.mark.parametrize("text", [
    "오랜만에 연락드립니다. 이준호예요.",
    "안녕하세요, 다온몰 실장님. 파일 맨 윗줄만 보내 주실 수 있을까요? 고객 정보는 받지 않습니다.",
])
def test_a_message_that_mixes_registers_is_a_problem(text):
    assert owner_tasks.register_mixed(text)


@pytest.mark.parametrize("text", [
    MESSAGE,
    "안녕하세요, 정숙이네 반찬이에요. 이번 주 금요일 저녁 세트를 준비했어요. 목요일까지 카톡으로 알려 주세요. 감사합니다.",
    "Hi, it's Junho. Could you send me just the header row of the order file? Thanks.",
    "오늘 저녁에 들를게요. 잘 부탁드립니다.",
    "답이 늦어서 죄송합니다. 내일 다시 연락드릴게요.",
])
def test_one_register_throughout_is_fine(text):
    assert not owner_tasks.register_mixed(text)


@pytest.mark.parametrize("line", [
    "카톡 답장 문구의 19:30을 20:00으로 고친 뒤 올려요",
    "올리기 전에 카톡 문구도 20:00으로 맞춰요",
    "Change 19:30 to 20:00 in the reply text before you post",
])
def test_a_card_line_that_asks_the_owner_to_fix_the_companys_text_is_a_problem(line):
    assert owner_tasks.owner_fix_ask(line)


@pytest.mark.parametrize("line", [
    "[ ] 안 대괄호 두 곳을 오너님 카톡 주소와 가게 주소로 바꿔요",
    "단지 이름 자리를 모르면 그대로 '망원동 강변 쪽 새 아파트'로 둬요",
    "회사가 픽업 시간을 20:00으로 고쳤어요",
    "Fill in your KakaoTalk ID where the brackets are",
    "메뉴 가격을 9,900원으로 바꿔요",
    "간판 영업시간을 20시로 고쳐 주세요",
    "Change the price to $29 a month before you post",
    "Replace the placeholder with 1 photo",
])
def test_filling_in_what_only_the_owner_knows_is_not(line):
    """A value the owner decides (a price, opening hours) names no text of the company's."""
    assert not owner_tasks.owner_fix_ask(line)


def _post_asking_a_fix() -> AgentAskDraft:
    return draft(post_card=post_card("other", where_url=None, what=what(
        title="금요일 저녁 픽업 세트", body="금요일 저녁 8시까지 찾아가세요.", facts=[], expected_questions=[],
        checklist=["카톡 답장 문구의 19:30을 20:00으로 고친 뒤 올려요"])))


# ------------------------------------------------------------------ no blanks, one voice (persona round 4, Sam)

PRIYA = ("Hi Priya,\n\nIt's [Your name]. I'm building a small tool that turns a week of shop support emails into a "
         "one-page Monday summary.\n\nHow to hand over the emails:\n1. In the search box, type: (to:[your shop email "
         "address] OR from:[your shop email address]) after:2026/10/26\n\nWhere it's stored: [hosting company], "
         "[region].\nQuestions, or want it deleted now? Reply to [your email].\n\nThanks so much,\n[Your name]")
TOM = ("Hi Tom,\n\nI'm testing a small tool that turns a week of shop support emails into a one-page summary. I don't "
       "know yet whether it will tell you anything new.\n\nWhat we remove: before the AI sees anything, the tool removes "
       "names. Who processes it: we use Anthropic's Claude API. We don't use its feedback feature, and our processor "
       "terms apply.\n\nThank you so much")


def test_a_message_reaches_the_to_do_with_no_blank_for_the_owner_to_fill(s, project):
    """The drafts read "It's [Your name]", "[hosting company], [region]" and "Reply to [your email]": the owner sends
    from their own account, so who it is from is on it already. A blank for the person it goes to stays."""
    req = requests.create(s, outreach(title="Ask message to Priya", handout=Handout(kind="message", to="Priya",
                                                                                   body=PRIYA)),
                          from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    approve(s, req, confirm_text=f"REQ-{req.id}")
    s.commit()
    (task,) = tasks_of(req.id)
    body = task.card["handout"]["body"]
    assert body.startswith("Hi Priya,\n\nI'm building a small tool") and body.endswith("Thanks so much,")
    for blank in ("[Your name]", "[hosting company]", "[region]", "[your email]"):
        assert blank not in body
    assert "(to:[your shop email address] OR from:[your shop email address])" in body
    assert "Questions, or want it deleted now?" in body


def test_the_author_is_told_to_write_a_message_without_blanks_and_in_one_voice():
    blanks = owner_tasks.author_problems([outreach(handout=Handout(kind="message", to="Priya", body=PRIYA))])
    voices = owner_tasks.author_problems([outreach(handout=Handout(kind="message", to="Tom", body=TOM))])
    assert len(blanks) == 1 and "[Your name]" in blanks[0]
    assert len(voices) == 1 and "we" in voices[0]
    plain = PRIYA.replace("It's [Your name]. ", "").replace("\n[Your name]", "").replace(
        "Where it's stored: [hosting company], [region].\n", "").replace(" Reply to [your email].", "")
    assert owner_tasks.author_problems([outreach(handout=Handout(kind="message", to="Priya", body=plain))]) == []


@pytest.mark.parametrize("text", [
    "I'd love to hear how you handle it. Could we talk on Friday? I can call you.",
    "We're a two-person shop and we read every email ourselves. Our customers write a lot.",
    "Hi Priya, could you send me last week's emails? I'll send you the page back. Let us know if it helps.",
])
def test_one_voice_or_an_inclusive_we_is_fine(text):
    assert owner_tasks.voice_mixed(text) is False


def test_the_server_names_what_its_author_must_fix_in_a_task_output():
    problems = owner_tasks.author_problems([_post_asking_a_fix(), outreach(handout=Handout(
        kind="message", to="다온몰", body="오랜만에 연락드립니다. 이준호예요."))])
    assert len(problems) == 2 and "19:30" in problems[0] and "register" in problems[1]
    assert owner_tasks.author_problems([outreach()]) == []


def test_a_task_whose_card_asks_the_owner_to_fix_the_draft_is_written_again_before_review(s, project, monkeypatch):
    """REQ-85 reached 박정숙 with '19:30을 20:00으로 고친 뒤 올려요'. The author gets the server's note and writes again;
    only the fixed card goes up."""
    fixed = _post_asking_a_fix()
    fixed.post_card.what.checklist = ["사진은 낮 자연광에서 찍어요"]
    outputs = iter([_post_asking_a_fix(), fixed])
    prompts: list[str] = []

    def run_agent(agent, purpose, prompt, model, **kw):
        if model is AgentResultOut:
            prompts.append(prompt)
            return AgentResultOut(summary="s", findings_markdown="f", evidence=[], assumptions=[], risks=[],
                                  open_questions=[], unmet_criteria=[], proposed_follow_up_tasks=[],
                                  requests=[next(outputs)], capability_gaps=[], financial_model=None)
        return ReviewOut(verdict=ReviewVerdict.PASS, criteria_met=[], criteria_unmet=[], claim_checks=[],
                         counter_arguments=[], rework_instructions=None, contested=False)

    s.commit()
    monkeypatch.setattr(company_graph, "last_served", lambda: Served("claude", "m"))
    monkeypatch.setattr(company_graph.evidence, "verify_sources", lambda ids: None)
    monkeypatch.setattr(company_graph, "run_agent", run_agent)
    with session_scope() as s2:
        t = Task(project_id=project.id, key="k", title="당근 첫 글", owner="marketing", objective="o",
                 acceptance_criteria=["글"], status="READY", max_iterations=2)
        s2.add(t)
        s2.flush()
        tid = t.id
    company_graph.work({"project_id": project.id, "task_id": tid})
    company_graph.schedule({"project_id": project.id})
    assert len(prompts) == 2 and "19:30" in prompts[1]
    with session_scope() as s2:
        from justshipit.db.models import AgentRequest
        (card,) = s2.scalars(select(AgentRequest).where(AgentRequest.task_id == tid)).all()
        assert card.action_params["post_card"]["what"]["checklist"][0] == "사진은 낮 자연광에서 찍어요"


def test_the_server_sends_a_task_back_once_and_leaves_the_reviewers_reworks(s, project, monkeypatch):
    """A send-back spends an author pass. A second miss goes on to the review, whose rework is still there."""
    authored: list[str] = []
    reviews = iter([ReviewOut(verdict=ReviewVerdict.REWORK, criteria_met=[], criteria_unmet=["사진 기준"], claim_checks=[],
                              counter_arguments=[], rework_instructions="사진 기준을 적어요", contested=False),
                    ReviewOut(verdict=ReviewVerdict.PASS, criteria_met=["글", "사진 기준"], criteria_unmet=[],
                              claim_checks=[], counter_arguments=[], rework_instructions=None, contested=False)])

    def run_agent(agent, purpose, prompt, model, **kw):
        if model is AgentResultOut:
            authored.append(prompt)
            return AgentResultOut(summary="s", findings_markdown="f", evidence=[], assumptions=[], risks=[],
                                  open_questions=[], unmet_criteria=[], proposed_follow_up_tasks=[],
                                  requests=[_post_asking_a_fix()], capability_gaps=[], financial_model=None)
        return next(reviews)

    s.commit()
    monkeypatch.setattr(company_graph, "last_served", lambda: Served("claude", "m"))
    monkeypatch.setattr(company_graph.evidence, "verify_sources", lambda ids: None)
    monkeypatch.setattr(company_graph, "run_agent", run_agent)
    with session_scope() as s2:
        t = Task(project_id=project.id, key="k", title="당근 첫 글", owner="marketing", objective="o",
                 acceptance_criteria=["글", "사진 기준"], status="READY", max_iterations=2)
        s2.add(t)
        s2.flush()
        tid = t.id
    company_graph.work({"project_id": project.id, "task_id": tid})
    assert len(authored) == 3, "one send-back, then the reviewer's one rework"
    assert "The server sends these back" in authored[1] and "사진 기준을 적어요" in authored[2]
    with session_scope() as s2:
        assert s2.get(Task, tid).review["verdict"] == "pass"


def _work_one(s, project, monkeypatch, results: list[list[AgentAskDraft]], reviews: list[ReviewOut], *,
              attempt_usd: float = 0.0) -> tuple[int, list[str]]:
    """One task authored with each pass's requests in turn and reviewed in turn. Each pass costs `attempt_usd`."""
    authored: list[str] = []
    passes, verdicts = iter(results), iter(reviews)

    def run_agent(agent, purpose, prompt, model, **kw):
        if model is AgentResultOut:
            authored.append(prompt)
            with session_scope() as s2:
                s2.get(Task, kw["task_id"]).spent_usd += attempt_usd
            return AgentResultOut(summary="s", findings_markdown="f", evidence=[], assumptions=[], risks=[],
                                  open_questions=[], unmet_criteria=[], proposed_follow_up_tasks=[],
                                  requests=next(passes), capability_gaps=[], financial_model=None)
        return next(verdicts)

    s.commit()
    monkeypatch.setattr(company_graph, "last_served", lambda: Served("claude", "m"))
    monkeypatch.setattr(company_graph.evidence, "verify_sources", lambda ids: None)
    monkeypatch.setattr(company_graph, "run_agent", run_agent)
    with session_scope() as s2:
        t = Task(project_id=project.id, key="k", title="당근 첫 글", owner="marketing", objective="o",
                 acceptance_criteria=["글", "사진 기준"], status="READY", max_iterations=2, budget_usd=3.0)
        s2.add(t)
        s2.flush()
        tid = t.id
    company_graph.work({"project_id": project.id, "task_id": tid})
    return tid, authored


def test_a_reworked_pass_goes_to_its_review_with_the_reviewers_list_kept(s, project, monkeypatch):
    """A send-back after the reviewer's rework replaced the reviewer's unmet list with the server's, and spent the
    pass the rework's review was for."""
    rework = ReviewOut(verdict=ReviewVerdict.REWORK, criteria_met=["글"], criteria_unmet=["사진 기준"], claim_checks=[],
                       counter_arguments=[], rework_instructions="사진 기준을 적어요", contested=False)
    passed = ReviewOut(verdict=ReviewVerdict.PASS, criteria_met=["글", "사진 기준"], criteria_unmet=[], claim_checks=[],
                       counter_arguments=[], rework_instructions=None, contested=False)
    tid, authored = _work_one(s, project, monkeypatch, [[], [_post_asking_a_fix()], []], [rework, passed, passed])
    assert len(authored) == 2, "the reworked pass goes to the review that asked for it"
    assert "사진 기준을 적어요" in authored[1]
    with session_scope() as s2:
        assert s2.get(Task, tid).review["iteration"] == 2


def test_no_send_back_starts_a_pass_that_would_end_past_the_hard_cap(s, project, monkeypatch):
    passed = ReviewOut(verdict=ReviewVerdict.PASS, criteria_met=["글", "사진 기준"], criteria_unmet=[], claim_checks=[],
                       counter_arguments=[], rework_instructions=None, contested=False)
    _, authored = _work_one(s, project, monkeypatch, [[_post_asking_a_fix()], []], [passed], attempt_usd=2.5)
    assert len(authored) == 1, "$2.50 spent of a $3 task: another $2.50 pass would end past the $4.50 cap"


def test_a_revision_without_the_handout_drops_the_old_text(s, project):
    first = requests.create(s, outreach(), from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    again = requests.create(s, outreach(handout=None, replaces=f"REQ-{first.id}"), from_agent="marketing",
                            project_id=project.id, origin=by_project_manager(s, project.id))
    assert again.id == first.id and "handout" not in again.action_params, "the old message is not this revision's"
    assert requests.typed_confirm(again), "with no screened text to hand over, it is a plain catch-all again"


def test_a_handout_rides_only_on_a_request_that_makes_a_to_do(s, project):
    req = requests.create(s, outreach(action_kind="internal.other"), from_agent="marketing", project_id=project.id,
                          origin=by_project_manager(s, project.id))
    assert "handout" not in (req.action_params or {}), "only a to-do kind's texts are screened"


# ------------------------------------------------------------------ the owner's own to-do takes no typed id

def test_steps_the_owner_follows_skip_the_typed_id_only_when_they_cost_nothing(s, project):
    """Steps (install an app, open an account) can cost what the agent left out; a message the owner sends cannot."""
    steps = Handout(kind="steps", to=None, body="1. 앱을 설치해요. 2. 가입해요.")
    unknown = requests.create(s, outreach(handout=steps), from_agent="marketing", project_id=project.id,
                              origin=by_project_manager(s, project.id))
    free = requests.create(s, outreach(title="네이버 스마트플레이스 등록하기", handout=steps, estimated_cost_usd=0),
                           from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    assert free.id != unknown.id
    assert (requests.typed_confirm(unknown), requests.typed_confirm(free)) == (True, False)


def test_approving_a_message_the_owner_sends_himself_takes_no_typed_id(s, project):
    req = requests.create(s, outreach(), from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    assert req.policy_mode == "strong_approve"
    assert not requests.typed_confirm(req)
    approve(s, req)
    s.commit()
    assert tasks_of(req.id), "approved with one click, it made the to-do"


@pytest.mark.parametrize("kw", [dict(handout=None), dict(estimated_cost_usd=40.0),
                                dict(risk_level=RiskLevel.HIGH),
                                dict(handout=Handout(kind="steps", to=None, body="1. 앱을 설치해요. 2. 가입해요."))])
def test_an_unscreened_paid_or_risky_catch_all_still_takes_the_typed_id(s, project, kw):
    req = requests.create(s, outreach(**kw), from_agent="marketing", project_id=project.id,
                          origin=by_project_manager(s, project.id))
    assert requests.typed_confirm(req)


def test_a_blank_for_the_person_it_goes_to_keeps_the_ask():
    """Review M1: "[Name]" in a greeting is the recipient, not the sender. Cutting its sentence cut the ask."""
    from justshipit.services.owner_tasks import without_blanks
    out = without_blanks("Hi [Name], could you send me one week of your support emails? It would help a lot.\n\n"
                         "Thanks,\n[Your name]")
    assert "could you send me one week of your support emails?" in out and "[Your name]" not in out
    assert "제 연락처" not in without_blanks("[이름]님, 지난주 메일을 보내 주실 수 있을까요? [제 연락처]로 답장 주세요.")
    assert "지난주 메일을 보내 주실 수 있을까요?" in without_blanks("[이름]님, 지난주 메일을 보내 주실 수 있을까요?")
