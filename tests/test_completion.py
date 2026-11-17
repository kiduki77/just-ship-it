"""The program's done sentence: N users used it twice and the owner talked with M of them."""
import pytest
from justshipit.api.app import app
from justshipit.services import completion, roadmap
from bet_loop_kit import install_people, line, make_business, no_people, reset_world
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    yield
    reset_world()


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000", headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def with_goal(s, text_: str, provenance="said"):
    b = roadmap.create_business(s, name="목표 있는 사업", one_liner="x")
    sections = [{"key": "customer_problem", "title": "대상 고객과 문제", "lines": [line("l-p", "터미널 기록을 문서로 못 남기는 개발자")]},
                {"key": "goal_deadline", "title": "목표와 기한",
                 "lines": [line("l-d", "석 달 안에 정한다"), line("l-s", text_, provenance)]}]
    roadmap.new_brief_version(s, b.id, sections=sections, assumptions=[], author="owner", approved=True)
    s.commit()
    return b


def without_plan(s):
    b = roadmap.create_business(s, name="계획 없는 사업", one_liner="x")
    s.commit()
    return b


def test_without_targets_in_the_brief_the_sentence_wants_ten_users_and_five_talks(s):
    b = without_plan(s)
    install_people(used=(10, 5))
    got = completion.check(s, b.id, lang="ko")
    assert ([g["number"] for g in got["goals"]], got["targets_from"]) == ([10, 5], "default")
    assert got["done"] is True
    assert got["sentence"] == "두 번 이상 쓴 외부 사용자 10명, 그중 오너님이 직접 대화한 5명"


@pytest.mark.parametrize("used,done", [((10, 5), True), ((9, 5), False), ((10, 4), False), ((40, 0), False), ((0, 0), False)])
def test_both_numbers_must_be_met(s, used, done):
    b = without_plan(s)
    install_people(used=used)
    got = completion.check(s, b.id, lang="ko")
    assert got["done"] is done and (got["used_twice"], got["talked_of_them"]) == used


def test_the_brief_can_set_n_and_m(s):
    b = with_goal(s, "12명이 두 번 이상 쓰고, 그중 6명과 직접 대화하면 성공")
    install_people(used=(11, 6))
    got = completion.check(s, b.id, lang="ko")
    assert ([g["number"] for g in got["goals"]], got["targets_from"], got["done"]) == ([12, 6], "brief", False)
    install_people(used=(12, 6))
    assert completion.check(s, b.id, lang="ko")["done"] is True


def test_a_guess_in_the_brief_does_not_set_the_targets(s):
    b = with_goal(s, "50명이 두 번 이상 쓰면 성공", provenance="guess")
    got = completion.check(s, b.id, lang="ko")
    assert ([g["number"] for g in got["goals"]], got["targets_from"]) == ([None], "brief")


def test_without_a_people_list_the_sentence_is_false_and_says_why(s):
    no_people()
    b = make_business(s)
    got = completion.check(s, b.id, lang="ko")
    assert got["done"] is False and got["used_twice"] == 0 and "명단이 연결되지 않아서" in got["missing"][0]


def test_the_api_serves_it_for_the_home_screen(s, api):
    b = make_business(s)
    install_people(used=(10, 5))
    body = api.get(f"/api/businesses/{b.id}/completion").json()
    assert body["done"] is False and body["goals"][0]["text"] == "3개월 안에 첫 가치에 닿은 사람 30명"
    assert api.get("/api/businesses/999999/completion").status_code == 409
