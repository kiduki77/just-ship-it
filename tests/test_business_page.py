"""What the business page reads: the approved brief with each line's quote and the defaults it applies, the three numbers
with a trend and the reason one is unknown, the routine card to open, and the limits that cap running bets."""
from datetime import UTC, datetime, timedelta

import pytest
from justshipit.api.app import app
from justshipit.services import metrics, roadmap
from bet_loop_kit import answer, card_of, install_people, make_bet, make_business, no_people, reset_world, set_bets
from fastapi.testclient import TestClient
from test_bet_routine import propose

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    yield
    reset_world()


@pytest.fixture
def business(s):
    return make_business(s)


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000", headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def point(s, business, metric, value, day_offset, ref=""):
    at = metrics.day_of(NOW - timedelta(days=day_offset))
    metrics.record(s, business.id, metric, value, at, source="github" if metric.startswith("github") else "people", ref=ref)
    s.commit()


def test_the_brief_lists_every_section_with_the_owners_quote_beside_a_said_line(s, api):
    b = roadmap.create_business(s, name="견본", one_liner="견본 한 줄")
    sections = roadmap.empty_sections()
    for sec in sections:
        if sec["key"] == "customer_problem":
            sec["lines"] = [{"id": "l1", "text": "작은 팀이 로그를 뒤진다", "provenance": "said", "source": "kickoff:1:2",
                             "quote": "로그를 뒤지느라 시간을 다 써요"}]
        if sec["key"] == "goal_deadline":
            sec["lines"] = [{"id": "l2", "text": "석 달에 100명", "provenance": "confirmed", "source": "kickoff:1:3"}]
        if sec["key"] == "known_people":
            sec["lines"] = [{"id": "l3", "text": "친구 둘", "provenance": "guess", "source": "kickoff:1:4"}]
    sections.append({"key": "deliverable", "title": "산출물", "lines": [
        {"id": "l4", "text": "랜딩 페이지", "provenance": "said", "source": "kickoff:1:5", "quote": "랜딩 페이지가 필요해요"}]})
    roadmap.new_brief_version(s, b.id, sections=sections, assumptions=[], author="owner", approved=True)
    s.commit()

    body = api.get(f"/api/businesses/{b.id}/brief").json()

    brief = body["brief"]
    by_key = {x["key"]: x for x in brief["sections"]}
    said = by_key["customer_problem"]["lines"][0]
    assert said["provenance_label"] == "You said" and said["quote"] == "로그를 뒤지느라 시간을 다 써요"
    assert by_key["goal_deadline"]["lines"][0]["provenance_label"] == "Confirmed"
    assert by_key["customer_problem"]["required"] and not by_key["known_people"]["required"]
    assert by_key["deliverable"]["legacy"] and by_key["deliverable"]["lines"][0]["quote"] == "랜딩 페이지가 필요해요"
    assert [x["key"] for x in brief["sections"]][-1] == "deliverable"
    assert by_key["red_lines"]["lines"] == [] and by_key["red_lines"]["default"]
    assert {x["key"] for x in brief["empty_defaults"]} >= {"red_lines", "owner_time"}
    assert "customer_problem" not in {x["key"] for x in brief["empty_defaults"]}
    assert body["draft_waiting"] is False


def test_a_business_with_no_approved_brief_says_so_and_shows_a_waiting_draft(s, api):
    b = roadmap.create_business(s, name="견본", one_liner="견본 한 줄")
    s.commit()
    assert api.get(f"/api/businesses/{b.id}/brief").json() == {"brief": None, "draft_waiting": False}

    draft_sections = roadmap.empty_sections()
    draft_sections[0]["lines"] = [{"id": "l1", "text": "견본", "provenance": "guess", "source": "kickoff:1:0"}]
    roadmap.new_brief_version(s, b.id, sections=draft_sections, assumptions=[], author="project_manager", approved=False)
    s.commit()

    body = api.get(f"/api/businesses/{b.id}/brief").json()
    assert body["brief"] is None and body["draft_waiting"] is True
    assert api.get("/api/businesses/999999/brief").status_code == 404


def test_the_numbers_give_each_stage_a_value_a_daily_trend_and_the_done_sentence(s, business, api):
    install_people(first_value=4, repeat=2, talked=1, contacted=9)
    point(s, business, "github_stars_total", 30, 2)
    point(s, business, "github_stars_total", 32, 0)
    point(s, business, "people_first_value", 3, 1)
    point(s, business, "people_repeat", 1, 1)

    body = api.get(f"/api/businesses/{business.id}/numbers?days=30").json()

    stages = {x["stage"]: x for x in body["stages"]}
    assert [x["stage"] for x in body["stages"]] == ["interest", "first_value", "repeat"]
    assert stages["interest"]["value"] == 32 and [p["value"] for p in stages["interest"]["series"]] == [30, 32]
    assert stages["first_value"]["value"] == 4 and stages["repeat"]["value"] == 2
    assert stages["first_value"]["series"][0]["value"] == 3
    assert stages["interest"]["why_unknown"] is None
    assert body["completion"]["done"] is False and body["completion"]["goals"][0]["number"] >= 1


def test_an_unknown_number_comes_back_empty_with_the_reason(s, business, api):
    no_people()

    stages = {x["stage"]: x for x in api.get(f"/api/businesses/{business.id}/numbers").json()["stages"]}

    assert stages["interest"]["value"] is None and "GitHub repository" in stages["interest"]["why_unknown"]
    assert stages["first_value"]["value"] is None and stages["first_value"]["why_unknown"]
    assert stages["repeat"]["series"] == []


def test_the_numbers_and_the_launch_gate_speak_the_language_chosen_in_settings(s, business, api):
    no_people()

    def read():
        stages = {x["stage"]: x for x in api.get(f"/api/businesses/{business.id}/numbers").json()["stages"]}
        gate = {x["item"]: x["label"] for x in api.get(f"/api/businesses/{business.id}/launch-gate").json()["items"]}
        return stages["first_value"]["label"], stages["first_value"]["why_unknown"], gate["demo"]

    assert read() == ("Got value", "The People list isn't available", "A recorded demo")
    missing = api.get("/api/businesses/99999999/numbers")
    assert missing.status_code == 409 and missing.json()["detail"] == "B-99999999 not found"
    try:
        assert api.put("/api/settings/general", json={"language": "ko"}).status_code == 200
        assert read() == ("도움 됨", "명단을 쓸 수 없어요", "녹화한 데모")
    finally:
        assert api.put("/api/settings/general", json={}).status_code == 200


def test_a_star_count_github_would_not_give_shows_the_collectors_reason(s, api):
    b = make_business(s, repo="owner/repo")
    point(s, b, "github_stars_total", None, 0, ref="GitHub 호출 한도에 닿았어요")

    [interest] = [x for x in api.get(f"/api/businesses/{b.id}/numbers").json()["stages"] if x["stage"] == "interest"]

    assert interest["value"] is None and interest["why_unknown"] == "GitHub 호출 한도에 닿았어요"


def test_only_the_days_asked_for_make_the_trend(s, business, api):
    point(s, business, "github_stars_total", 10, 40)
    point(s, business, "github_stars_total", 20, 3)

    [interest] = [x for x in api.get(f"/api/businesses/{business.id}/numbers?days=7").json()["stages"] if x["stage"] == "interest"]

    assert [p["value"] for p in interest["series"]] == [20] and interest["value"] == 20


def test_the_routine_list_names_the_card_that_waits_for_the_owner(outside_cycles, s, business, monkeypatch, api):
    bet = propose(s, business, monkeypatch)
    card = card_of(s, bet.id)

    [row] = api.get(f"/api/businesses/{business.id}/routines").json()
    assert row["state"] == "proposed" and row["request_id"] == card.id

    answer(card.id)
    [row] = api.get(f"/api/businesses/{business.id}/routines").json()
    assert row["state"] == "active" and row["request_id"] is None


def test_the_roadmap_reports_the_cap_on_running_bets_and_how_many_run(s, business, api):
    set_bets(max_running=2)
    bet = make_bet(s, business)
    api.post(f"/api/bets/{bet.id}/move", json={"to": "RUNNING"})

    limits = api.get(f"/api/businesses/{business.id}/roadmap").json()["limits"]

    assert limits["max_running"] == 2 and limits["running"] == 1 and limits["max_routines"] >= 1
