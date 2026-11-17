"""The roadmap, the experiment loop and the done sentence write for the owner in the language the owner picked:
English with no Korean in it, or Korean that reads as written in Korean (해요체, none of the 번역체 patterns). What
the owner wrote keeps its language, and a goal line or a stored status line is read in either."""
import re
from datetime import timedelta

import pytest
from justshipit.api.app import app
from justshipit.db.models import ActivityEvent, AppSetting, Bet
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import app_settings, bet_loop, completion, roadmap
from justshipit.services.control import ControlError
from bet_loop_kit import (
    NOW,
    clear_jobs,
    install_people,
    line,
    make_bet,
    make_business,
    reset_world,
    set_bets,
    start,
)
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from test_korean_style import smells_in

HANGUL = re.compile(r"[가-힣]")
LANGS = ["en", "ko"]


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    set_bets(failed_grace_minutes=0)
    clear_jobs("run_project", "bet_retro")
    yield
    reset_world()


@pytest.fixture
def language():
    """Sets the owner's language in Settings, and takes the setting away afterwards."""
    def wipe():
        with session_scope(lease_checked=False) as s:
            s.execute(delete(AppSetting).where(AppSetting.section == "general"))

    def choose(lang: str) -> None:
        with session_scope() as s:
            app_settings.save(s, "general", {"language": lang}, actor="test")
    wipe()
    yield choose
    wipe()


def reads_in(lang: str, texts: list[str]) -> None:
    """English has no Hangul. Korean has no 번역체 smell, and a sentence ends in 해요체, not 합니다 or 해라체."""
    assert texts and all(texts)
    for said in texts:
        if lang == "en":
            assert not HANGUL.search(said), said
        else:
            assert not smells_in(said), (said, smells_in(said))
            assert not re.search(r"다[.)]?$", said.strip()), f"not 해요체: {said}"


PLAN = [{"key": "one_liner", "title": "x", "lines": [line("l-1", "터미널 문서화 CLI")]},
        {"key": "owner_time", "title": "x", "lines": [line("l-2", "주 2시간", "guess")]},
        {"key": "old_section", "title": "조직", "lines": [line("l-3", "개발과 문서화 역할만")]}]


@pytest.mark.parametrize("lang", LANGS)
def test_the_plan_sections_read_in_the_owners_language(lang):
    views = [v for v in roadmap.section_views(PLAN, lang=lang) if not v["legacy"]]
    defaults = roadmap.empty_defaults(PLAN, lang=lang)

    reads_in(lang, [v["title"] for v in views] + [v["holds"] for v in views] + [v["drives"] for v in views]
             + [v["default"] for v in views if v["default"]] + [d["default"] for d in defaults]
             + [x["provenance_label"] for v in views for x in v["lines"]] + roadmap.approval_blockers(PLAN, lang=lang))
    assert {d["key"] for d in defaults} == {"alternative_value", "path", "owner_time", "red_lines"}
    legacy = next(v for v in roadmap.section_views(PLAN, lang=lang) if v["legacy"])
    assert legacy["title"] == "조직", "a section an older plan had keeps the title it was stored with"


@pytest.mark.parametrize("lang", LANGS)
def test_refusals_read_in_the_owners_language(s, lang):
    business = roadmap.create_business(s, name="Refusals", one_liner="x")
    s.commit()
    said = []
    for refuse in (
        lambda: roadmap.new_brief_version(s, business.id, sections=[], assumptions=[], author="owner", approved=True,
                                          lang=lang),
        lambda: roadmap.update_lists(s, business.id, lang=lang, rungs=[{"id": "r-1", "name": "a"},
                                                                       {"id": "r-1", "name": "b"}]),
        lambda: roadmap.update_lists(s, business.id, lang=lang, ladders=[]),
        lambda: roadmap.create_bet(s, business.id, title=" ", pitch="x", lang=lang),
        lambda: roadmap.create_bet(s, business.id, title="t", pitch="p", track_id="t-none", lang=lang),
        lambda: roadmap.move_bet(s, 10**9, "RUNNING", lang=lang),
        lambda: bet_loop.redo_retro(s, 10**9, lang=lang),
        lambda: bet_loop.stop_routine(s, 10**9, lang=lang),
        lambda: completion.check(s, 10**9, lang=lang),
    ):
        with pytest.raises(ControlError) as caught:
            refuse()
        s.rollback()
        said.append(str(caught.value))
    reads_in(lang, said)
    assert f"B-{business.id}" in said[0] and roadmap.BRIEF_BY_KEY["goal_deadline"].text("title", lang) in said[0]


@pytest.mark.parametrize("lang", LANGS)
def test_a_bet_whose_window_closed_says_why_in_the_owners_language(s, lang):
    business = make_business(s)
    bet = make_bet(s, business, run_mode="owner", appetite_days=2)
    start(bet.id)
    s.expire_all()
    s.get(Bet, bet.id).started_at = NOW - timedelta(days=3)
    s.commit()

    with session_scope() as x:
        assert (bet.id, "time_limit") in bet_loop.sweep(x, NOW, lang=lang)

    s.expire_all()
    reads_in(lang, [s.get(Bet, bet.id).status_reason])


@pytest.mark.parametrize("lang", LANGS)
def test_the_default_done_sentence_is_in_the_owners_language(s, lang):
    business = roadmap.create_business(s, name="No plan", one_liner="x")
    s.commit()
    install_people(used=(10, 5))

    got = completion.check(s, business.id, lang=lang)

    assert got["targets_from"] == "default" and [g["number"] for g in got["goals"]] == [10, 5] and got["done"]
    reads_in(lang, [got["sentence"], *(g["text"] for g in got["goals"])])


def test_an_english_goal_line_sets_the_targets_as_a_korean_one_does(s):
    business = roadmap.create_business(s, name="English goals", one_liner="x")
    roadmap.new_brief_version(s, business.id, approved=True, author="owner", assumptions=[], sections=[
        {"key": "customer_problem", "title": "x", "lines": [line("l-p", "devs who lose their shell history")]},
        {"key": "goal_deadline", "title": "x",
         "lines": [line("l-g", "In six weeks, 12 users use it twice, and talk to 6 of them; 100 GitHub stars")]}])
    s.commit()
    install_people(used=(12, 5))

    got = completion.check(s, business.id, lang="en")

    assert [(g["number"], g["unit"], g["measure"]) for g in got["goals"]] == [
        (12, "users", "repeat"), (6, "users", "repeat_talked"), (100, "stars", None)]
    assert got["targets_from"] == "brief" and got["done"] is False
    assert roadmap.promised_days(s, business.id) == 42, "the deadline phrase is the window, not a goal"


@pytest.mark.parametrize("clause,measure", [
    ("Have 10 conversations with customers", "talked"),
    ("20 users try the chatbot", "first_value"),
    ("3 users convert to paid", None),
])
def test_a_conversation_is_not_a_conversion_and_a_chatbot_is_not_a_chat(clause, measure):
    assert completion._goal(clause, None).measure == measure


def test_a_stuck_review_line_stored_in_korean_is_not_written_again_in_english(s):
    business = make_business(s)
    bet = make_bet(s, business)
    start(bet.id)
    korean = msg("bet.reason.stuck_ask", "ko", stuck=msg("bet.reason.stuck", "ko", n=roadmap.RETRO_ROUNDS))
    with session_scope() as x:
        roadmap.move_bet(x, bet.id, "REVIEW", lang="ko")
        x.get(Bet, bet.id).status_reason = korean

    for _ in range(roadmap.RETRO_ROUNDS + 1):
        with session_scope() as x:
            x.execute(text("UPDATE jobs SET status = 'failed' WHERE kind = 'bet_retro' "
                           "AND (payload->>'bet_id')::int = :b"), {"b": bet.id})
        with session_scope() as x:
            bet_loop.sweep(x, lang="en")

    s.expire_all()
    assert roadmap.retro_stuck(s, s.get(Bet, bet.id))
    assert s.get(Bet, bet.id).status_reason == korean, "the line stays in the language it was written in"
    assert s.scalars(select(ActivityEvent).where(ActivityEvent.verb == "bet_retro_stuck",
                                                 ActivityEvent.entity_id == bet.id)).all() == []


@pytest.mark.parametrize("lang", LANGS)
def test_the_business_page_routes_answer_in_the_settings_language(s, api_token, language, lang):
    business = make_business(s)
    language(lang)
    api = TestClient(app, base_url="http://127.0.0.1:8000",
                     headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})

    brief = api.get(f"/api/businesses/{business.id}/brief").json()["brief"]
    refused = api.post(f"/api/businesses/{business.id}/bets", json={"title": " ", "pitch": "x"})
    missing = api.get("/api/businesses/999999/roadmap")

    assert refused.status_code == 409 and missing.status_code == 404
    registry = [sec for sec in brief["sections"] if not sec["legacy"]]
    reads_in(lang, [sec["title"] for sec in registry] + [sec["holds"] for sec in registry]
             + [d["default"] for d in brief["empty_defaults"]] + [refused.json()["detail"], missing.json()["detail"]])
