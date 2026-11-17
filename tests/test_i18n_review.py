"""The owner approves only what an option says to do, and keeps the provenance of their own words, in either language.
Each test replays a finding of the review of the i18n foundation (2026-11-04), by its ID."""
import re
from typing import get_args

import pytest
from owner_task_kit import draft, events, post_card, tasks_of, what
from sqlalchemy import delete, select

from justshipit.agents import executor
from justshipit.api import console
from justshipit.contracts.requests import HumanDecision, HumanResponse, RequestStatus
from justshipit.db.models import OWNER_TASK_KINDS, AppSetting, BudgetReservation, Project
from justshipit.db.session import session_scope
from justshipit.i18n import LANGS, msg
from justshipit.i18n.match.verdicts import WORDS
from justshipit.i18n.prompting import output_rule
from justshipit.services import app_settings, kickoff, owner_quote, requests, roadmap, today, usage, verdicts
from i18n_kit import ROOT
from test_approval_integrity import card, email, option, sends
from test_kickoff import _settled_jobs, api, project_manager, lines, open_session, out, play, view  # noqa: F401
from ask_kit import by_project_manager

pytestmark = pytest.mark.usefixtures("strengths_answered")

# ------------------------------------------------------------------ H1: an English option that says not to act

EN_DECLINES = ["Do not merge yet", "Do not email them", "Do not contact", "Do not deploy", "Hold", "Hold the post",
               "Defer", "Postpone the launch", "Pause", "Wait a week", "Never", "Stop", "Won't send",
               "Don\u2019t send", "Don\u2018t send", "Don\u02bct send", "Keep it as a draft", "Put it on hold",
               "Let's wait", "Delay the launch", "Leave it for now", "Not yet"]


@pytest.mark.parametrize("label", EN_DECLINES)
def test_an_english_option_that_says_not_to_act_declines(label):
    assert verdicts.declines(label)
    assert verdicts.infer_verdict(label, question=False) in (HumanDecision.REJECT, HumanDecision.DEFER, None)


@pytest.mark.parametrize("label", ["Don\u2019t send", "Do not email them", "Hold", "Wait a week"])
def test_approving_an_english_option_that_says_not_to_send_never_sends(s, project, label):
    req = requests.create(s, email(card=card(option("Send it", "approve"), option(label, "approve"))),
                          from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))

    assert {o["label"]: o["verdict"] for o in req.action_params["card"]["options"]}[label] is None
    with pytest.raises(requests.ResponseRejected):
        requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE, chosen_option=label))
    assert sends(s, req) == [] and req.status == RequestStatus.PENDING.value


@pytest.mark.parametrize("option_label", ["Keep it as a draft", "Hold the post", "Never", "Don\u2019t post"])
def test_approving_an_english_option_that_says_not_now_makes_no_owner_task(s, project, option_label):
    req = requests.create(s, draft(title="Walker wait-list post", options=["Post it", option_label],
                                   post_card=post_card("other", where_url=None, what=what(
                                       title="Walker wait-list", body="We take walkers on the wait-list.",
                                       facts=[], expected_questions=[]))), from_agent="marketing",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    verdict = next(c["verdict"] for c in requests.choices(req) if c["label"] == option_label) or HumanDecision.APPROVE
    requests.respond(s, req.id, HumanResponse(decision=verdict, chosen_option=option_label))
    s.commit()

    assert tasks_of(req.id) == []
    if verdict == HumanDecision.APPROVE:
        assert [e.verb for e in events("request", req.id)][-1] == "owner_task_not_needed"


# Each Korean decline and how an English option says the same. Keys are Korean options; every Korean fragment is
# matched by at least one of them (checked below), so a Korean word added without an English twin fails here.
NOT_NOW_PAIRS: dict[str, list[str]] = {
    "지금은 하지 않음": ["Not now", "Don't do it yet"],
    "올리지 않음": ["Don't post it", "Not posting"],
    "게시하지 않음": ["Do not publish"],
    "보내지 않음": ["Don't send", "Won't send", "Not sending"],
    "보내지 말기": ["Don't send it"],
    "진행하지 않음": ["Do not proceed"],
    "시작하지 않음": ["Don't start yet", "Not starting"],
    "쓰지 않음": ["Don't write it", "Not using it"],
    "사지 않음": ["Don't buy", "Not buying"],
    "결제하지 않음": ["Don't pay", "Not paying"],
    "만들지 않음": ["Don't build it", "Not making it"],
    "연락하지 않음": ["Do not contact", "Not contacting"],
    "신청하지 않음": ["Don't apply"],
    "등록하지 않음": ["Don't register"],
    "맡기지 않음": ["Don't hand it off"],
    "공개하지 않음": ["Don't publish", "Not publishing"],
    "묻지 않음": ["Don't ask"],
    "물어보지 않음": ["Not asking"],
    "안 함": ["Won't do it"],
    "안 올림": ["Not posting"],
    "안 보냄": ["Not sending"],
    "안 씀": ["Not using it"],
    "안 삼": ["Not buying"],
    "안 묻기": ["Not asking"],
    "보류": ["Hold", "On hold", "Hold the post", "Put it on hold"],
    "연기": ["Postpone the launch", "Delay"],
    "나중에": ["Later", "Defer", "Maybe later"],
    "생략": ["Skip"],
    "이번 주는 건너뛰기": ["Skip this week"],
    "미루기": ["Put it off", "Wait a week"],
    "그만": ["Stop", "Pause"],
    "반려": ["Reject"],
    "거부": ["Refuse"],
    "거절": ["Decline"],
    "취소": ["Cancel"],
}


def _spelled_out(fragment: str) -> list[str]:
    """The fragment once per alternative of each group of plain words in it, so "(?:하|보내)지" checks 하지 and 보내지."""
    groups = re.findall(r"\(\?:([가-힣|]+)\)", fragment)
    if not groups:
        return [fragment]
    return [fragment.replace(f"(?:{g})", alt, 1) for g in groups for alt in g.split("|")]


@pytest.mark.parametrize("kind", ["not_now", "not_now_lead"])
def test_every_korean_decline_word_has_an_english_twin(kind):
    for fragment in WORDS["ko"][kind]:
        for f in _spelled_out(fragment):
            pattern = f"^{f}" if kind == "not_now_lead" else f
            assert any(re.search(pattern, ko) for ko in NOT_NOW_PAIRS), f"no pair names Korean {f!r}"


@pytest.mark.parametrize("korean", list(NOT_NOW_PAIRS))
def test_a_korean_decline_and_its_english_twins_all_decline(korean):
    assert verdicts.declines(korean)
    for english in NOT_NOW_PAIRS[korean]:
        assert verdicts.declines(english), english
        assert verdicts.infer_verdict(english, question=False) != HumanDecision.APPROVE, english


@pytest.mark.parametrize("label", ["Send the stakeholder update", "Post the stop-loss notice", "Approve the collateral",
                                   "Publish the hold music page", "Merge the waitlist fix", "Proceed"])
def test_an_english_option_that_acts_while_naming_a_waiting_word_is_not_a_decline(label):
    assert not verdicts.declines(label)


# ------------------------------------------------------------------ M3: English verdict words are whole words

@pytest.mark.parametrize("label,verdict", [
    ("Disapprove", "reject"), ("Unacceptable", "reject"), ("Don\u2019t approve", None),
    ("Approve the stakeholder email", "approve"), ("Approve the new threshold", "approve"),
    ("Approve the collateral", "approve"), ("Stakeholder review", None), ("Threshold", None), ("Collateral", None),
])
def test_an_english_verdict_word_counts_only_as_a_whole_word(label, verdict):
    assert verdicts.infer_verdict(label, question=False) == verdict


# ------------------------------------------------------------------ H2: the owner's quote in another language

@pytest.fixture
def language():
    def wipe():
        with session_scope(lease_checked=False) as s:
            s.execute(delete(AppSetting).where(AppSetting.section == "general"))

    def choose(lang: str) -> None:
        with session_scope() as s:
            app_settings.save(s, "general", {"language": lang}, actor="owner")
    wipe()
    yield choose
    wipe()


def _said(section, text, quote, gloss):
    turn = out("Next question?", section="path", patch=[(section, text, "said", quote)])
    turn.brief_patch[0].quote_gloss = gloss
    return turn


KO_IDEA = "개발자가 쓴 터미널 명령을 자동으로 문서화하는 CLI. 석 달에 100명이 쓰면 성공이에요"
KO_RED_LINE = "고객 로그는 절대 외부 서버로 보내지 않아요"
EN_IDEA = "A CLI that documents the terminal commands developers type. Success is 100 people using it in 3 months"
EN_RED_LINE = "We never send customer logs to an outside server"


def test_a_korean_owners_quote_backs_an_english_line_through_its_gloss(api, project_manager, language):
    language("en")
    sid = open_session(api, one_liner=KO_IDEA)
    play(api, project_manager, sid, out("Who needs this most?"), answer={"text": f"신입이 헤맸어요. {KO_RED_LINE}"})
    project_manager.says(_said("red_lines", "Customer logs never go to an outside server", KO_RED_LINE,
                   "Customer logs never go to an outside server"))
    kickoff.run_turn(sid, 2)

    [line] = lines(view(api, sid), "red_lines")
    assert (line["provenance"], line["source"], line["quote"]) == ("said", f"kickoff:{sid}:1", KO_RED_LINE)


def test_a_korean_owners_deadline_backs_an_english_goal_and_sets_the_cycle(api, project_manager, language):
    language("en")
    sid = open_session(api, one_liner=KO_IDEA)
    project_manager.says(_said("goal_deadline", "100 people use it within three months", "석 달에 100명이 쓰면 성공이에요",
                   "Success is 100 people using it in three months"))
    kickoff.run_turn(sid, 1)

    v = view(api, sid)
    [line] = lines(v, "goal_deadline")
    assert line["provenance"] == "said" and v["turns"][0]["patch_notes"] == []
    with session_scope() as s:
        assert roadmap.deadline_days(roadmap.draft_brief(s, v["session"]["business_id"]).sections) == 90


def test_an_english_owners_quote_backs_a_korean_line_through_its_gloss(api, project_manager, language):
    language("ko")
    sid = open_session(api, one_liner=EN_IDEA)
    play(api, project_manager, sid, out("누가 제일 필요로 할까요?"), answer={"text": f"A new hire got lost. {EN_RED_LINE}"})
    project_manager.says(_said("red_lines", "고객 로그는 바깥 서버로 보내지 않는다", EN_RED_LINE, "고객 로그를 바깥 서버로 보내지 않아요"))
    kickoff.run_turn(sid, 2)

    [line] = lines(view(api, sid), "red_lines")
    assert (line["provenance"], line["source"], line["quote"]) == ("said", f"kickoff:{sid}:1", EN_RED_LINE)


def test_a_gloss_keeps_the_number_check_on_the_owners_own_words(api, project_manager, language):
    """The gloss is the CEO's text: a number it adds does not back a gate line."""
    language("ko")
    sid = open_session(api, one_liner=EN_IDEA)
    project_manager.says(_said("goal_deadline", "6주 안에 100명이 쓰면 성공", "Success is 100 people using it in 3 months",
                   "6주 안에 100명이 쓰면 성공이에요"))
    kickoff.run_turn(sid, 1)

    v = view(api, sid)
    [line] = lines(v, "goal_deadline")
    assert line["provenance"] == "guess"
    assert [n["reason"] for n in v["turns"][0]["patch_notes"]] == ["not_backing"]


@pytest.mark.parametrize("gloss", ["", "The owner likes dark mode in the console"])
def test_a_quote_in_another_language_without_a_gloss_about_the_line_stays_a_guess(gloss):
    quote = owner_quote.norm(KO_RED_LINE)
    assert owner_quote.problem(quote, "Customer logs never go to an outside server", gate=False, said=quote,
                               gloss=gloss)


def test_a_gloss_does_not_rescue_a_quote_in_the_lines_own_language():
    quote = owner_quote.norm("The owner likes dark mode in the console")
    assert owner_quote.problem(quote, "Customer logs never leave the building", gate=False, said=quote,
                               gloss="Customer logs never leave the building")


# ------------------------------------------------------------------ M1: the product's own language

def test_the_language_rule_leaves_the_products_own_text_to_the_spec():
    for lang in LANGS:
        rule = output_rule(lang)
        assert "the product being built" in rule and "README" in rule


def test_an_engineering_call_scopes_the_language_rule_to_what_the_owner_reads(owner_asked, monkeypatch):
    from justshipit.runtime import LLMResult
    from pydantic import BaseModel

    class Out(BaseModel):
        ok: bool

    sent: dict[str, str] = {}

    def model(call):
        sent[call.agent] = call.system_prompt
        return LLMResult(call_id=call.call_id, provider=call.provider, model="m", output={"ok": True}, input_tokens=1,
                         output_tokens=1, cached_tokens=0, cost_usd=0.0, duration_ms=1, session_id=None)

    monkeypatch.setattr(executor, "run_with_retry", model)
    for agent in ("project_manager", "engineer"):
        executor.run_agent(agent, "kickoff", "hello", Out, project_id=None, system_override="role")
    assert "only the fields the owner reads" in sent["engineer"].rsplit("## Output language", 1)[1]
    assert "only the fields the owner reads" not in sent["project_manager"]


# ------------------------------------------------------------------ M2: the allowlist's counts are a ceiling

def test_a_listed_file_with_more_korean_than_its_count_fails():
    from i18n_kit import over_count

    assert over_count({"a.py": 4, "b.py": 2, "c.py": 1}, {"a.py": 3, "b.py": 2}) == {"a.py": (4, 3)}


# ------------------------------------------------------------------ L1: English durations and screen chips

@pytest.mark.parametrize("text,days", [
    ("100 people within two months", 60), ("six weeks to the first 10 walkers", 42), ("in three months", 90),
    ("Ten days to the first reply", 10), ("a one-month pilot", 30), ("someone said it twice a week", None),
    ("6주 안에 3곳", 42), ("석 달 안에 정한다", 90),
])
def test_an_english_span_of_time_sets_the_deadline(text, days):
    sections = [{"key": "goal_deadline", "title": "Goal", "lines": [
        {"id": "l-g", "text": text, "provenance": "said", "source": "kickoff:1:1"}]}]
    assert roadmap.deadline_days(sections) == days


def test_the_project_managers_dont_know_and_later_chips_are_dropped_in_either_language(api, project_manager):
    sid = open_session(api)
    project_manager.says(out("Who needs it most?", chips=("New hires", "I don't know", "Later", "I don\u2019t know.", "나중에",
                                               "모르겠어요")))
    kickoff.run_turn(sid, 1)

    assert view(api, sid)["turns"][0]["chips"] == ["New hires"]


# ------------------------------------------------------------------ L2: the usage alert names its waiting work

def test_the_usage_alert_names_the_waiting_work_in_its_own_field(s, project):
    from test_usage_alert import _settle_waiting_jobs
    from test_usage_defer import enqueue, run_once
    from datetime import datetime, timedelta, timezone
    from justshipit.services.usage import MustWait

    _settle_waiting_jobs()
    s.commit()
    try:
        enqueue("run_project", project_id=project.id)
        wait = MustWait(datetime.now(timezone.utc) + timedelta(hours=9), "usage_cap_day", "Limit: resets at 09:00")
        run_once("run_project", lambda p: (_ for _ in ()).throw(wait))
        (alert,) = [a for a in console.alerts(include_test=True) if a["kind"] == "usage_wait"]
        assert alert["waiting_work"] == project.name
    finally:
        _settle_waiting_jobs()


# ------------------------------------------------------------------ L4: the budget hold comes after the prompt

def test_a_prompt_that_cannot_be_built_holds_no_budget(s, project, monkeypatch):
    from pydantic import BaseModel

    class Out(BaseModel):
        ok: bool

    s.commit()

    def broken():
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(app_settings, "language", broken)
    with pytest.raises(RuntimeError):
        executor.run_agent("project_manager", "kickoff", "hello", Out, project_id=project.id, system_override="role")
    with session_scope() as fresh:
        assert fresh.get(Project, project.id).reserved_usd == 0
        assert fresh.scalars(select(BudgetReservation).where(BudgetReservation.project_id == project.id)).all() == []


# ------------------------------------------------------------------ L5: the kickoff prompt names the language

@pytest.mark.parametrize("lang,name", [("en", "English"), ("ko", "Korean")])
def test_the_kickoff_prompt_names_the_output_language(lang, name):
    system = kickoff._system(lang)
    assert f"plan lines) in {name}" in system and f"Write in {name} even when the owner answers in another language" in system
    assert "{output_language}" not in system and "{closing_question}" not in system
    assert f'"{kickoff.closing_question(lang)}"' in system


# ------------------------------------------------------------------ L6: every runtime-built message key exists

@pytest.mark.parametrize("lang", LANGS)
def test_every_owner_task_kind_and_usage_reason_has_its_messages(lang):
    for kind in OWNER_TASK_KINDS:
        assert msg(f"today.task.{kind}", lang) and msg(f"owner_task.kind.{kind}", lang) and today.label(kind, lang)
    for source in today.SOURCES:
        assert today.label(source, lang)
    for reason in get_args(usage.Reason):
        assert msg(f"usage.reason.{reason}", lang)


# ------------------------------------------------------------------ L7: the page's language before the console loads

def test_the_console_page_starts_in_english():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert '<html lang="en">' in html
