"""How a person heard about the product: the answer on the person, its routes, the outcome field on a person to-do, the
question on an inbound person's prep card, the tally of people not on the list, and the counts typed for a tag."""
from datetime import UTC, datetime, timedelta

import pytest
from justshipit.api.app import app
from justshipit.db.models import ActivityEvent, MetricPoint, OwnerTask
from justshipit.db.session import session_scope
from justshipit.services import channels, metrics, owner_tasks, people, people_tasks, tracking
from fastapi.testclient import TestClient
from sqlalchemy import select

from owner_task_kit import post_card
from people_kit import add, make_business


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def heard_events(s, person):
    return list(s.scalars(select(ActivityEvent).where(ActivityEvent.entity == "person", ActivityEvent.entity_id == person.id,
                                                      ActivityEvent.verb == "person_heard").order_by(ActivityEvent.id)))


def post_task(s, business, channel="linkedin", title="LinkedIn에 소개") -> OwnerTask:
    card, _ = owner_tasks.build_post_card(post_card(channel), None, "en")
    return owner_tasks.create_post_task(s, business.id, card, title)


# ------------------------------------------------------------------ the answer on a person

def test_a_pick_is_kept_with_the_move_in_the_activity_log(s):
    b = make_business(s)
    p = add(s, b, "alice", source="inbound")

    tracking.set_heard(s, p.id, "hacker_news", "Show HN에서 봤어요", lang="en")

    assert (p.heard_via, p.heard_words) == ("hacker_news", "Show HN에서 봤어요")
    (event,) = heard_events(s, p)
    assert event.data == {"ref": f"PPL-{p.id}", "from": None, "to": "hacker_news", "words": "Show HN에서 봤어요"}
    assert event.summary == f"PPL-{p.id} alice: heard from Hacker News"


def test_recording_the_same_answer_again_changes_nothing_and_a_different_one_replaces_it(s):
    b = make_business(s)
    p = add(s, b, "alice")
    tracking.set_heard(s, p.id, "friend", None, lang="en")
    tracking.set_heard(s, p.id, "friend", None, lang="en")
    assert len(heard_events(s, p)) == 1

    tracking.set_heard(s, p.id, "search", None, lang="en")

    assert p.heard_via == "search" and [e.data["from"] for e in heard_events(s, p)] == [None, "friend"]


def test_both_left_out_clears_the_answer(s):
    b = make_business(s)
    p = add(s, b, "alice")
    tracking.set_heard(s, p.id, "friend", "my cousin", lang="en")

    tracking.set_heard(s, p.id, None, None, lang="en")

    assert (p.heard_via, p.heard_words) == (None, None)


def test_words_with_no_pick_propose_the_channel_they_name_in_any_language(s):
    b = make_business(s)
    p = add(s, b, "alice")

    tracking.set_heard(s, p.id, None, "당근 동네생활에서 봤어요", lang="en")
    assert p.heard_via == "daangn_community"
    tracking.set_heard(s, p.id, None, "saw it on Hacker News", lang="en")
    assert p.heard_via == "hacker_news"
    tracking.set_heard(s, p.id, None, "a stranger on a train", lang="en")
    assert p.heard_via == "other"


def test_a_typed_tag_resolves_by_its_task_id_to_that_posts_channel(s):
    b = make_business(s)
    p = add(s, b, "alice")
    task = post_task(s, b, "linkedin")

    tracking.set_heard(s, p.id, None, f"link was {task.card['track']['tag']} I think", lang="en")

    assert p.heard_via == "linkedin"


def test_a_tag_whose_channel_code_does_not_match_its_task_is_not_trusted(s):
    b = make_business(s)
    p = add(s, b, "alice")
    task = post_task(s, b, "linkedin")
    wrong = f"hn-{tracking._base36(task.id)}"

    tracking.set_heard(s, p.id, None, wrong, lang="en")

    assert p.heard_via == "other"


def test_an_explicit_pick_wins_over_what_the_words_name(s):
    b = make_business(s)
    p = add(s, b, "alice")

    tracking.set_heard(s, p.id, "friend", "Hacker News, then a friend", lang="en")

    assert p.heard_via == "friend"


@pytest.mark.parametrize("words", ["mail me at carol@example.com", "call 010-9999-8888", "kakao id: carol99"])
def test_words_that_hold_a_contact_detail_are_refused_whole_and_not_repeated(s, words):
    b = make_business(s)
    p = add(s, b, "alice")

    with pytest.raises(people.PeopleError) as refused:
        tracking.set_heard(s, p.id, "friend", words, lang="en")

    assert words not in str(refused.value) and (p.heard_via, p.heard_words) == (None, None)
    assert heard_events(s, p) == []


def test_a_pick_that_is_no_place_and_words_that_are_too_long_are_refused(s):
    b = make_business(s)
    p = add(s, b, "alice")

    with pytest.raises(people.PeopleError, match="Use one of"):
        tracking.set_heard(s, p.id, "telepathy", None, lang="en")
    with pytest.raises(people.PeopleError, match="120"):
        tracking.set_heard(s, p.id, "friend", "x" * 121, lang="en")


def test_an_archived_person_keeps_no_answer(s):
    b = make_business(s)
    p = add(s, b, "alice")
    people.archive(s, p.id, lang="en")

    with pytest.raises(people.PeopleError):
        tracking.set_heard(s, p.id, "friend", None, lang="en")


def test_the_names_of_the_answers_are_in_both_languages():
    assert tracking.label("friend", "en") == "Someone told them" and tracking.label("friend", "ko") == "누가 알려 줬어요"
    assert tracking.label("known", "en") == "People you know" and tracking.label("no_answer", "ko") == "아직 답 없음"
    assert tracking.label("hacker_news", "en") == "Hacker News"
    assert tracking.label("other", "ko") == "기타"


def test_the_derived_row_of_a_person_with_no_answer_follows_their_source_and_an_answer_replaces_it(s):
    b = make_business(s)
    warm, reached, inbound = (add(s, b, "w", source="warm"), add(s, b, "r", source="public_problem"),
                              add(s, b, "i", source="inbound"))

    rows = {p.handle: people.view(p, lang="en")["heard_row"] for p in (warm, reached, inbound)}
    assert rows == {"w": "known", "r": "reached_out", "i": "no_answer"}

    tracking.set_heard(s, warm.id, "x", None, lang="en")
    assert people.view(warm, lang="en")["heard_row"] == "x"
    assert people.view(warm, lang="ko")["heard_row_label"] == channels.get("x").names["ko"]


# ------------------------------------------------------------------ the route

def test_the_put_route_records_the_answer_and_returns_the_person(s, api):
    b = make_business(s)
    p = add(s, b, "alice", source="inbound")
    s.commit()

    got = api.put(f"/api/people/{p.id}/heard", json={"heard_via": "friend", "heard_words": "my cousin"})

    assert got.status_code == 200
    body = got.json()
    assert (body["heard_via"], body["heard_words"], body["heard_row"]) == ("friend", "my cousin", "friend")
    assert body["ref"] == f"PPL-{p.id}"
    cleared = api.put(f"/api/people/{p.id}/heard", json={}).json()
    assert (cleared["heard_via"], cleared["heard_row"]) == (None, "no_answer")


def test_the_put_route_refuses_in_the_owners_language(s, api):
    b = make_business(s)
    p = add(s, b, "alice")
    s.commit()

    assert api.put(f"/api/people/{p.id}/heard", json={"heard_via": "telepathy"}).status_code == 409
    assert "carol@example.com" not in api.put(f"/api/people/{p.id}/heard", json={"heard_words": "carol@example.com"}).text
    assert api.put("/api/people/99999999/heard", json={"heard_via": "friend"}).status_code == 404
    bad = api.put(f"/api/people/{p.id}/heard", json={"heard_via": 4})
    assert bad.status_code == 422 and "Where they heard" in bad.json()["detail"]
    assert api.put(f"/api/people/{p.id}/heard", json={"nope": 1}).status_code == 422


def test_the_list_gives_the_choices_for_the_business_and_the_derived_row_of_each_person(s, api):
    b = make_business(s)
    add(s, b, "alice", source="warm")
    s.commit()

    got = api.get(f"/api/businesses/{b.id}/people").json()

    keys = [c["key"] for c in got["heard_choices"]]
    assert keys[-3:] == ["friend", "search", "other"] and "hacker_news" in keys and "other" not in keys[:-1]
    assert {c["kind"] for c in got["heard_choices"]} == {"channel", "word"}
    assert got["items"][0]["heard_row"] == "known"


# ------------------------------------------------------------------ on a person to-do

def finish(s, task, **outcome):
    return owner_tasks.done(s, task.id, outcome=people_tasks.Outcome(**outcome))


def person_task(s, source="public_problem"):
    b = make_business(s)
    p = add(s, b, "alice", source=source)
    t = people_tasks.create_task(s, p.id, "contact", actor="system", lang="en")
    s.flush()
    return p, t


def test_finishing_a_person_to_do_records_how_they_heard_with_the_stage(s):
    p, t = person_task(s)

    finish(s, t, stage="talked", heard_via="search", heard_words="googled the name")

    assert (p.stage, p.heard_via, p.heard_words) == ("talked", "search", "googled the name")
    assert len(heard_events(s, p)) == 1


def test_leaving_the_answer_out_of_the_outcome_keeps_the_one_already_there(s):
    p, t = person_task(s)
    tracking.set_heard(s, p.id, "friend", None, lang="en")

    finish(s, t, stage="talked")

    assert p.heard_via == "friend"


def test_a_bad_answer_in_the_outcome_refuses_the_whole_outcome_and_the_to_do_stays_open(s):
    p, t = person_task(s)

    with pytest.raises(people.PeopleError):
        finish(s, t, stage="talked", heard_words="reach me at carol@example.com")

    assert (t.status, p.stage, p.heard_via) == ("OPEN", "found", None)


def test_an_answer_on_a_to_do_that_is_not_about_a_person_is_refused(s):
    b = make_business(s)
    task = post_task(s, b, "hacker_news")

    with pytest.raises(owner_tasks.OwnerTaskError):
        finish(s, task, heard_via="friend")


def test_the_done_route_takes_the_answer(s, api):
    p, t = person_task(s)
    s.commit()

    done = api.post(f"/api/owner-tasks/{t.id}/done", json={"stage": "talked", "heard_via": "friend"})

    assert done.status_code == 200
    with session_scope() as s2:
        assert people.load(s2, p.id, lang="en").heard_via == "friend"


# ------------------------------------------------------------------ the question on the prep card

def test_an_inbound_persons_card_ends_with_the_one_fixed_question_in_their_language(s):
    b = make_business(s)
    inbound = add(s, b, "i", source="inbound")
    found = add(s, b, "f", source="public_problem")
    warm = add(s, b, "w", source="warm")

    en = people_tasks.person_card(s, inbound, "contact", lang="en")["questions"]
    ko = people_tasks.person_card(s, inbound, "contact", lang="ko")["questions"]

    assert en[-1] == "How did you hear about us?" and ko[-1] == "저희를 어디서 알게 되셨어요?"
    assert en.count("How did you hear about us?") == 1
    for other in (found, warm):
        assert "How did you hear about us?" not in people_tasks.person_card(s, other, "contact", lang="en")["questions"]
    assert people_tasks.person_card(s, inbound, "follow_up", lang="en")["questions"][-1] == "How did you hear about us?"


# ------------------------------------------------------------------ the tally and the tag counts

NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)


def points(s, business, metric):
    return [(p.ref, p.value, p.source) for p in s.scalars(select(MetricPoint).where(
        MetricPoint.business_id == business.id, MetricPoint.metric == metric).order_by(MetricPoint.id))]


def test_the_days_tally_is_replaced_so_a_double_tap_adds_nothing(s):
    b = make_business(s)

    tracking.set_tally(s, b.id, "daangn_community", 1, "2026-11-16", now=NOW)
    got = tracking.set_tally(s, b.id, "daangn_community", 2, "2026-11-16", now=NOW)
    tracking.set_tally(s, b.id, "daangn_community", 2, "2026-11-16", now=NOW)

    assert points(s, b, "heard_tally") == [("daangn_community", 2.0, "owner")]
    assert got["count"] == 2 and got["total"] == 2


def test_another_day_adds_to_the_total_and_the_day_is_the_owners_not_utcs(s, monkeypatch):
    b = make_business(s)
    monkeypatch.setattr(metrics.app_settings, "effective", lambda: type("E", (), {"usage": type("U", (), {
        "timezone": "Asia/Seoul"})()})())
    late = datetime(2026, 11, 15, 20, 0, tzinfo=UTC)   # 05:00 on the 16th in Seoul

    first = tracking.set_tally(s, b.id, "friend", 1, "2026-11-16", now=late)
    second = tracking.set_tally(s, b.id, "friend", 1, "2026-11-17", now=late + timedelta(days=1))

    assert first["day"] == "2026-11-16" and second["day"] == "2026-11-17" and second["total"] == 2


@pytest.mark.parametrize("count", [-1, 1001, 1.5, "3", True, None])
def test_a_count_that_is_not_a_whole_number_in_range_is_refused(s, count):
    b = make_business(s)

    with pytest.raises(tracking.TrackingError, match="whole number"):
        tracking.set_tally(s, b.id, "friend", count, "2026-11-16", now=NOW)

    assert points(s, b, "heard_tally") == []


def test_a_tally_for_no_place_or_no_business_is_refused(s):
    b = make_business(s)
    with pytest.raises(tracking.TrackingError, match="Use one of"):
        tracking.set_tally(s, b.id, "telepathy", 1, "2026-11-16", now=NOW)
    with pytest.raises(tracking.TrackingNotFound):
        tracking.set_tally(s, 99999999, "friend", 1, "2026-11-16", now=NOW)


def test_the_tag_counts_are_kept_as_typed_for_a_tag_of_this_business_and_replace_the_day(s):
    b = make_business(s)
    task = post_task(s, b, "linkedin")
    tag = task.card["track"]["tag"]

    tracking.set_tag_counts(s, b.id, tag, visits=40, signups=4, now=NOW)
    got = tracking.set_tag_counts(s, b.id, tag.upper(), visits=45, now=NOW)

    assert got == {"tag": tag, "day": "2026-11-16", "visits": 45, "signups": None}
    assert points(s, b, "tag_visits") == [(tag, 45.0, "owner")] and points(s, b, "tag_signups") == [(tag, 4.0, "owner")]


def test_a_tag_that_is_not_this_businesss_or_counts_that_are_missing_or_negative_are_refused(s):
    b = make_business(s)
    other = make_business(s, name="다른 사업")
    task = post_task(s, other, "linkedin")
    mine = post_task(s, b, "linkedin", title="내 글").card["track"]["tag"]

    with pytest.raises(tracking.TrackingNotFound):
        tracking.set_tag_counts(s, b.id, task.card["track"]["tag"], visits=1, now=NOW)
    with pytest.raises(tracking.TrackingNotFound):
        tracking.set_tag_counts(s, b.id, "hn-zzzz", visits=1, now=NOW)
    with pytest.raises(tracking.TrackingError, match="visits, the sign-ups"):
        tracking.set_tag_counts(s, b.id, mine, now=NOW)
    with pytest.raises(tracking.TrackingError, match="whole number"):
        tracking.set_tag_counts(s, b.id, mine, visits=-3, now=NOW)


def test_the_owners_counts_join_a_bet_as_told_and_typed_and_no_row_calls_them_read(s):
    b = make_business(s)
    tag = post_task(s, b, "linkedin").card["track"]["tag"]
    tracking.set_tally(s, b.id, "friend", 3, "2026-11-16", now=NOW)
    tracking.set_tag_counts(s, b.id, tag, visits=9, now=NOW)

    seen = metrics.observed(s, b.id, NOW - timedelta(days=1), NOW + timedelta(days=1), lang="en")

    assert [(o.name, o.value, o.basis) for o in seen.by_stage["first_value"]] == [
        ("People not on your list who said they came from Someone told them", 3.0, "a count the owner told us, sum of 1 day")]
    assert [(o.value, "typed in by the owner" in o.basis) for o in seen.by_stage["interest"]] == [(9.0, True)]


# ------------------------------------------------------------------ the routes of the business

def test_the_three_business_routes_write_and_refuse(s, api):
    b = make_business(s)
    task = post_task(s, b, "linkedin")
    tag = task.card["track"]["tag"]
    s.commit()

    today = api.get(f"/api/businesses/{b.id}/tracking").json()["today"]
    tally = api.post(f"/api/businesses/{b.id}/tally", json={"key": "friend", "count": 2, "day": today})
    assert tally.status_code == 200 and set(tally.json()) == {"key", "day", "count", "total"}
    assert tally.json()["count"] == 2 and tally.json()["day"] == today

    tags = api.post(f"/api/businesses/{b.id}/tag-counts", json={"tag": tag, "visits": 7, "signups": 1})
    assert tags.status_code == 200 and tags.json()["visits"] == 7 and tags.json()["signups"] == 1

    doors = api.put(f"/api/businesses/{b.id}/doors", json={"doors": [{"kind": "site", "url": "https://acme.dev"}]})
    assert doors.status_code == 200
    assert doors.json()["doors"] == [{"kind": "site", "label": "Your site", "url": "https://acme.dev", "param": "ref",
                                      "derived": False}]

    assert api.post(f"/api/businesses/{b.id}/tally", json={"key": "friend", "count": -1, "day": today}).status_code == 400
    assert api.post(f"/api/businesses/{b.id}/tally", json={"key": "friend"}).status_code == 422
    assert api.post("/api/businesses/99999999/tally", json={"key": "friend", "count": 1, "day": "2026-11-16"}).status_code == 404
    assert api.post(f"/api/businesses/{b.id}/tag-counts", json={"tag": "xx-1", "visits": 1}).status_code == 404
    assert api.put(f"/api/businesses/{b.id}/doors", json={"doors": [{"kind": "site", "url": "ftp://x"}]}).status_code == 400
    assert api.put(f"/api/businesses/{b.id}/doors", json={"doors": [{"kind": "site"}]}).status_code == 422
