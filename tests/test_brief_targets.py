"""The business's goals and deadline come from the owner's brief: its own numbers and units, and its own weeks."""
import pytest
from justshipit.api.app import app
from justshipit.services import completion, projects, roadmap
from bet_loop_kit import install_people, line, reset_world
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    yield
    reset_world()


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000", headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def business_with_goal(s, *goal_lines: str, guesses: tuple[str, ...] = ()):
    b = roadmap.create_business(s, name="견적봇", one_liner="금형 공장 견적 메일 1차 답장 초안")
    sections = [{"key": "customer_problem", "title": "대상 고객과 문제", "lines": [line("l-p", "견적 메일 답장이 늦어 수주를 놓치는 금형 공장 사장")]},
                {"key": "goal_deadline", "title": "목표와 기한",
                 "lines": [line(f"l-g{i}", t) for i, t in enumerate(goal_lines)]
                          + [line(f"l-q{i}", t, "guess") for i, t in enumerate(guesses)]}]
    roadmap.new_brief_version(s, b.id, sections=sections, assumptions=[], author="owner", approved=True)
    s.commit()
    return b


def goals(got):
    return [(g["text"], g["number"], g["unit"], g["measure"]) for g in got["goals"]]


def test_the_briefs_own_numbers_and_units_are_the_goals_not_the_default_ten_users(s):
    b = business_with_goal(s, "6주 안에 3곳 무료 사용 시작, 8주 안에 1곳 유료 전환(월 5만원 수준)", "답장 중앙값 2시간")
    install_people(first_value=3)
    got = completion.check(s, b.id, lang="ko")
    assert got["targets_from"] == "brief"
    assert goals(got) == [("6주 안에 3곳 무료 사용 시작", 3, "곳", "first_value"),
                          ("8주 안에 1곳 유료 전환(월 5만원 수준)", 1, "곳", None),
                          ("답장 중앙값 2시간", 2, "시간", None)]
    assert [g["current"] for g in got["goals"]] == [3, None, None]
    assert "외부 사용자" not in got["sentence"] and "3곳 무료 사용 시작" in got["sentence"]
    assert got["done"] is False, "a goal the people list cannot count is the owner's to check, never assumed met"


def test_a_brief_goal_of_repeat_use_and_talks_is_counted_against_the_people_list(s):
    b = business_with_goal(s, "12명이 두 번 이상 쓰고, 그중 6명과 직접 대화하면 성공")
    install_people(used=(11, 6))
    got = completion.check(s, b.id, lang="ko")
    assert goals(got) == [("12명이 두 번 이상 쓰고", 12, "명", "repeat"), ("그중 6명과 직접 대화하면 성공", 6, "명", "repeat_talked")]
    assert got["done"] is False
    install_people(used=(12, 6))
    assert completion.check(s, b.id, lang="ko")["done"] is True


def test_the_owner_who_counts_people_in_another_word_still_gets_a_people_goal(s):
    b = business_with_goal(s, "6주 안에 산책을 맡긴 보호자 30명, 그중 15명과 직접 통화")
    install_people(first_value=30, used=(0, 0), talked=15)
    got = completion.check(s, b.id, lang="ko")
    assert goals(got)[0][1:] == (30, "명", "first_value")
    assert goals(got)[1][1:] == (15, "명", "talked"), "그중 after a first-value goal counts the people spoken with"


def test_a_goal_line_without_a_number_is_the_plans_goal_for_the_owner_to_check(s):
    b = business_with_goal(s, "빨리 첫 손님을 받는다")
    got = completion.check(s, b.id, lang="ko")
    assert got["targets_from"] == "brief"
    assert goals(got) == [("빨리 첫 손님을 받는다", None, "", None)]
    assert got["done"] is False


def test_a_plan_with_no_approved_brief_falls_back_to_the_default_and_says_so(s):
    b = roadmap.create_business(s, name="견적봇", one_liner="금형 공장 견적 메일 1차 답장 초안")
    got = completion.check(s, b.id, lang="ko")
    assert got["targets_from"] == "default"
    assert goals(got) == [("두 번 이상 쓴 외부 사용자 10명", 10, "명", "repeat"),
                          ("그중 오너님이 직접 대화한 5명", 5, "명", "repeat_talked")]


def test_a_guessed_goal_line_is_not_the_owners_target(s):
    b = business_with_goal(s, "빨리 첫 손님을 받는다", guesses=("50명이 두 번 이상 쓰면 성공",))
    assert [g["number"] for g in completion.check(s, b.id, lang="ko")["goals"]] == [None]


def test_the_numbers_route_carries_the_briefs_goals(s, api):
    b = business_with_goal(s, "6주 안에 3곳 무료 사용 시작")
    install_people(first_value=1)
    body = api.get(f"/api/businesses/{b.id}/numbers").json()
    assert body["completion"]["goals"][0]["text"] == "6주 안에 3곳 무료 사용 시작"
    assert body["completion"]["goals"][0]["current"] == 1


@pytest.mark.parametrize("text,days", [
    ("6주 안에 3곳 무료 사용 시작, 8주 안에 1곳 유료 전환", 42),
    ("석 달 안에 정한다", 90),
    ("30일 안에 첫 손님 5명", 30),
    ("in 6 weeks, 30 walkers", 42),
    ("2개월 내 보호자 30명", 60),
    ("11월 30일까지 첫 손님", None),
    ("빨리 첫 손님을 받는다", None),
])
def test_the_deadline_is_read_from_the_goal_lines(text, days):
    sections = [{"key": "goal_deadline", "title": "목표와 기한", "lines": [line("l-g", text)]}]
    assert roadmap.deadline_days(sections) == days


def test_the_first_cycle_and_a_bet_run_for_the_weeks_the_owner_said(s):
    b = business_with_goal(s, "6주 안에 3곳 무료 사용 시작, 8주 안에 1곳 유료 전환")
    s.refresh(b)
    assert b.cycle_days == 42
    c = projects.start_business(s, b.id)
    assert (c.ends_on - c.starts_on).days == 42
    bet = _running_bet(s, b)
    s.commit()
    view = roadmap.roadmap(s, b.id, lang="ko")
    shown = next(x for x in view["bets"] if x["id"] == bet.id)
    assert shown["window"]["days_left"] == 42


def _running_bet(s, b):
    """An experiment the owner started from the Business page, with no time limit of its own."""
    bet = roadmap.create_bet(s, b.id, title="첫 실험", pitch="세 곳에 무료로 써 보게 한다")
    roadmap.move_bet(s, bet.id, "PICKED")
    return roadmap.move_bet(s, bet.id, "RUNNING")


def test_a_bet_started_before_its_brief_set_the_window_runs_to_the_briefs_deadline(s):
    """견적봇 showed '14일 남음' for a brief that says 6주: its bet started with no window of its own and its business
    kept the default cycle, because the brief was approved before the deadline was read from it."""
    from datetime import datetime, timedelta, timezone
    from justshipit.db.models import Business
    b = business_with_goal(s, "6주 안에 3곳 무료 사용 시작, 8주 안에 1곳 유료 전환")
    bet = _running_bet(s, b)
    s.get(Business, b.id).cycle_days = 14
    bet.appetite_days = None
    bet.started_at = datetime.now(timezone.utc) - timedelta(days=15)
    s.commit()

    view = roadmap.roadmap(s, b.id, lang="ko")
    shown = next(x for x in view["bets"] if x["id"] == bet.id)

    assert roadmap.window_end(s, bet) == bet.started_at + timedelta(days=42)
    assert shown["window"]["days_left"] == 27 and view["business"]["cycle_days"] == 42


def test_a_bet_with_its_own_window_keeps_it(s):
    b = business_with_goal(s, "6주 안에 3곳 무료 사용 시작")
    bet = _running_bet(s, b)
    bet.appetite_days = 10
    s.commit()

    assert roadmap.window_end(s, bet) == bet.started_at + __import__("datetime").timedelta(days=10)
