"""An English owner reads their own language everywhere, and an owner's instruction cancels only the work it names.
Each test replays a finding of the review of the i18n merge (2026-11-04), by its ID."""
import copy
import pickle

import pytest
from sqlalchemy import select

from justshipit.contracts.planning import DirectiveRuleOut
from justshipit.contracts.requests import AgentRequestDraft, HumanDecision, HumanResponse, RequestType, RiskLevel
from justshipit.db.models import AgentRequest, KickoffTurn, Project, Task
from justshipit.db.session import session_scope
from justshipit.engineering import owner_trace, status, worker
from justshipit.i18n import LANGS, catalog, msg
from justshipit.i18n.match.copy_rules import PLACEHOLDER
from justshipit.i18n.said import Refusal, SaidError, said
from justshipit.operations import notify
from justshipit.services import channels, copy_rules, owner_tasks, requests, roadmap, secrets
from i18n_kit import ROOT, web_catalog
from test_directive_rules import _give, _task
from test_kickoff import _settled_jobs, api, project_manager, kickoff, lines, open_session, out, view  # noqa: F401
from ask_kit import contract, by_project_manager

pytestmark = pytest.mark.usefixtures("strengths_answered")


def _card(title: str) -> AgentRequestDraft:
    return AgentRequestDraft(type=RequestType.APPROVAL, title=title, summary="s", rationale="r", options=[],
                             recommended_option=None, action_kind=None, estimated_cost_usd=None,
                             risk_level=RiskLevel.LOW, context=contract())


# ------------------------------------------------------------------ M1: the kickoff's confirm lines

def test_m1_an_english_owner_reads_the_confirm_lines_under_english_section_titles(api, project_manager):
    sid = open_session(api)
    project_manager.says(out("Here is a first guess", kind="proposal", section="path",
                 patch=[("path", "Start as an open-source CLI", "guess")]))
    kickoff.run_turn(sid, 1)

    [line] = view(api, sid)["turns"][-1]["promote"]
    assert (line["section"], line["title"]) == ("path", roadmap.BRIEF_BY_KEY["path"].text("title", "en"))


def test_m1_a_confirm_line_stored_with_a_korean_title_reads_in_the_owners_language(api, project_manager):
    sid = open_session(api)
    project_manager.says(out("Here is a first guess", kind="proposal", section="path",
                 patch=[("path", "Start as an open-source CLI", "guess")]))
    kickoff.run_turn(sid, 1)
    with session_scope() as s:
        turn = s.scalar(select(KickoffTurn).where(KickoffTurn.session_id == sid, KickoffTurn.n == 1))
        turn.promote = [{**x, "title": roadmap.BRIEF_BY_KEY["path"].text("title", "ko")} for x in turn.promote]

    [line] = view(api, sid)["turns"][-1]["promote"]
    assert line["title"] == "Launch path"


# ------------------------------------------------------------------ M2: an English keyword is a whole word

def test_m2_stop_running_ads_cancels_the_ads_work_and_leaves_the_leads_alone(s, project, monkeypatch):
    project.status = "RUNNING"
    leads = _task(project.id, "Qualify inbound leads", "READY")
    ads = _task(project.id, "Run a Facebook ads test", "READY")
    s.add_all([leads, ads])
    s.commit()
    card = requests.create(s, _card("Approve outreach to the 12 webinar leads"), from_agent="marketing",
                           project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()

    rec = _give(s, project, monkeypatch, "Stop running ads", DirectiveRuleOut(label="ads", keywords=["ads"]))

    assert rec["cancelled"] == [ads.id] and rec["withdrawn"] == []
    with session_scope() as s2:
        assert s2.get(Task, leads.id).status == "READY"
        assert s2.get(AgentRequest, card.id).status == "PENDING"


def test_m2_an_english_keyword_matches_whole_words_and_their_plural():
    from justshipit.services import plan_screen
    rule = plan_screen.Rule(1, 1, "ads", ("ads",))
    assert plan_screen.broken([rule], "Run Facebook ads")
    assert plan_screen.broken([rule], "ADS budget for May")
    assert not plan_screen.broken([rule], "Qualify inbound leads")
    assert not plan_screen.broken([rule], "Approve outreach to the 12 webinar leads")
    sheet = plan_screen.Rule(1, 1, "spreadsheet", ("spreadsheet", "recheck"))
    assert plan_screen.broken([sheet], "Recheck the pricing spreadsheets")
    assert plan_screen.broken([plan_screen.Rule(1, 1, "m", ("market size",))], "Market-size count")


def test_m2_a_latin_keyword_needs_three_letters(s, project):
    from justshipit.services import plan_screen
    kept, refused = plan_screen.screen_rules(s, project, [{"label": "ads", "keywords": ["ad"]},
                                                          {"label": "ads", "keywords": ["ads"]}], lang="en")
    assert [r["keywords"] for r in kept] == [["ads"]]
    assert [r["keywords"] for r in refused] == [["ad"]] and "ad" in refused[0]["why"]


def test_m2_a_rules_only_short_korean_word_matches_only_where_it_starts_a_word():
    """시장 sits inside 재래시장: a rule of that word alone would cancel interviews the owner never named."""
    from justshipit.services import plan_screen
    rule = plan_screen.Rule(1, 1, "시장 조사", ("시장",))
    assert plan_screen.broken([rule], "온라인 시장 규모 조사")
    assert plan_screen.broken([rule], "시장조사 정리")
    assert not plan_screen.broken([rule], "재래시장 상인 인터뷰")
    pair = plan_screen.Rule(1, 1, "시장 조사", ("시장", "조사"))
    assert plan_screen.broken([pair], "온라인시장 조사"), "two words together match inside words"
    longer = plan_screen.Rule(1, 1, "시장규모 집계", ("시장규모",))
    assert plan_screen.broken([longer], "국내시장규모 집계"), "a longer Korean word still matches inside a word"


def test_m2_a_rule_is_judged_too_broad_over_the_decisions_it_would_withdraw_too(s, project):
    """A business with two tasks and three waiting decisions has five titles a rule can take away."""
    from justshipit.services import plan_screen
    s.add_all([_task(project.id, "Interview cafe owners", "READY"), _task(project.id, "Cafe price survey", "READY")])
    s.flush()
    cards = [requests.create(s, _card(title), from_agent=agent, project_id=project.id,
                             origin=by_project_manager(s, project.id)) for title, agent in (
        ("Publish the cafe landing page", "marketing"), ("Buy a cafe owner mailing list", "research"),
        ("Hire a bakery photographer", "finance"))]
    s.flush()
    assert [c.status for c in cards] == ["PENDING"] * 3

    _, refused = plan_screen.screen_rules(s, project, [{"label": "cafe work", "keywords": ["cafe"]}], lang="en")

    assert len(refused) == 1 and "4" in refused[0]["why"] and "5" in refused[0]["why"]


# ------------------------------------------------------------------ L1 and L2: bare decision words

DECIDE_WORDS = {k: v for k, v in web_catalog()["decide"].items() if k.startswith("decide.word.")}
VERDICT_OPTIONS = ("build.option.approve", "build.option.reject", "build.option.defer", "work.option.approve",
                   "work.option.hold", "work.option.decline", "operations.option.hold", "operations.option.reject",
                   "factory.activate.no")


def test_l1_an_answer_that_is_any_server_option_or_console_word_is_no_ask():
    words = [msg(k, lang) for k in catalog() for lang in LANGS
             if k.startswith(("build.option.", "work.option.", "operations.option.")) or k == "factory.activate.no"]
    words += [entry[lang] for entry in DECIDE_WORDS.values() for lang in LANGS]
    assert "decide.word.defer" in DECIDE_WORDS
    for word in words:
        assert word.lower() in owner_trace._BARE_VERDICTS, word


def test_l1_a_bare_hold_click_is_not_traced_as_an_owner_ask(s, project):
    project.context = {"owner_answers": [
        {"request": "REQ-1", "title": "Start the work?", "decision": "DEFERRED", "answer": "Hold", "note": None},
        {"request": "REQ-2", "title": "Which platform?", "decision": "ANSWERED", "answer": "Android first",
         "note": None}]}
    s.flush()

    traced = [x.ref for x in owner_trace.owner_sources(s, project, "en")]

    assert "REQ-2" in traced and "REQ-1" not in traced


@pytest.mark.parametrize("key", VERDICT_OPTIONS)
def test_l2_every_verdict_option_reads_as_a_bare_word_in_both_languages(key):
    """The console drops a bare hold or decline because its buttons say it. A spec card showed a Defer radio beside
    the buttons in English and none in Korean."""
    bare = {entry[lang].lower() for entry in DECIDE_WORDS.values() for lang in LANGS}
    assert {lang: msg(key, lang).lower() in bare for lang in LANGS} == dict.fromkeys(LANGS, True)


# ------------------------------------------------------------------ L3: a stored-token refusal

def test_l3_a_token_file_problem_is_said_in_the_owners_language(monkeypatch):
    refusal = secrets.SecretError("not_a_file", file="slack_bot_token")

    def unreadable(name):
        raise refusal
    monkeypatch.setattr(secrets, "get", unreadable)

    with pytest.raises(notify.NotifyError) as raised:
        notify.post_message("#company", "hello", lang="ko")

    assert str(raised.value) == refusal.say("ko") != refusal.say("en")


# ------------------------------------------------------------------ L4: the picked option's stored label

def test_l4_a_picked_option_is_stored_with_its_label_in_the_owners_language(s, project, korean_owner):
    req = requests.create(s, AgentRequestDraft(type=RequestType.QUESTION, title="Pick one", summary="s", rationale="r",
                                               options=["T-3", "T-4"], recommended_option=None, action_kind=None,
                                               estimated_cost_usd=None, risk_level=RiskLevel.LOW, context=contract()),
                          from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))
    s.flush()

    requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE, chosen_option="T-3"))

    assert req.response["chosen_option"] == msg("request.option", "ko", n=1)


# ------------------------------------------------------------------ L5: refusals survive a copy and a pickle

@pytest.mark.parametrize("exc", [SaidError("build.step.tests_run", n=2), Refusal(said("request.stale")),
                                 Refusal("written as is"), requests.ResponseRejected(said("request.stale"))])
def test_l5_a_refusal_keeps_its_words_through_a_copy_and_a_pickle(exc):
    for again in (copy.copy(exc), copy.deepcopy(exc), pickle.loads(pickle.dumps(exc))):
        assert type(again) is type(exc) and str(again) == str(exc)
        assert again.words("ko") == exc.words("ko")


# ------------------------------------------------------------------ L6: a step whose values a message outgrew

def test_l6_a_step_without_a_value_its_message_now_takes_shows_no_line(s, project):
    from test_dev_board import _job, _spec
    job = _job(s, _spec(s, project), "IMPLEMENTING", steps=[
        {"at": "2026-11-04T01:00:00+00:00", "status": "IMPLEMENTING", "summary": "raw",
         "say": {"key": "build.step.tests_run"}}])

    assert worker.step_line(job.steps[0], "en") is None
    assert status.steps(job, "en")[0]["say"] is None
    assert status.board(s, project.id, lang="en")


# ------------------------------------------------------------------ L8: a to-do's channel name

def test_l8_a_post_to_do_names_its_channel_in_the_owners_language():
    place = channels.get("hacker_news")
    stored = {"channel": place.key, "channel_name": place.names["ko"], "rules_summary": place.rules["ko"],
              "mode": place.mode}

    card = owner_tasks.readable_card(stored, "en")

    assert (card["channel_name"], card["rules_summary"]) == (place.names["en"], place.rules["en"])


# ------------------------------------------------------------------ L9: the placeholder for the owner's experience

@pytest.mark.parametrize("prompt", ["prompts/agents/marketing.md", "prompts/promotion.md"])
def test_l9_each_drafting_prompt_gives_the_placeholder_in_both_languages(prompt):
    text = (ROOT / prompt).read_text(encoding="utf-8")
    for placeholder in PLACEHOLDER.values():
        assert placeholder in text, (prompt, placeholder)


@pytest.mark.parametrize("lang", LANGS)
def test_l9_a_draft_holding_either_placeholder_is_not_refused(lang):
    body = f"{PLACEHOLDER[lang]}\n\nMost teams used a wiki for this. 많은 팀이 위키를 쓰다가 그만뒀어요."
    assert copy_rules.public_copy_problem("Show HN: docs from your shell", body) is None
