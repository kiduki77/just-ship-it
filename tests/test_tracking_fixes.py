"""Regression tests for the defects a review of step 0.5 found: door addresses, the done form's heard fields, the day a
tally is written to, the owner's day against the time windows, what opens the routine verdict, how the owner's counts are
named, the prep card's question, the numbers counted in a live bet's window, and an answer whose channel has gone."""
from datetime import UTC, datetime, timedelta

import pytest
from justshipit.api.app import app
from justshipit.db.models import MetricPoint, Person
from justshipit.services import bet_retro, channels, metrics, owner_tasks, people, people_tasks, roadmap, tracking
from fastapi.testclient import TestClient
from sqlalchemy import select

import bet_loop_kit
from owner_task_kit import post_card
from people_kit import add, make_business


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


@pytest.fixture
def seoul(monkeypatch):
    monkeypatch.setattr(metrics.app_settings, "effective", lambda: type("E", (), {"usage": type("U", (), {
        "timezone": "Asia/Seoul"})()})())


def contact_task(s, business, person, kind="contact"):
    return people_tasks.create_task(s, person.id, kind, lang="en", actor="owner")


# ------------------------------------------------------------------ 1. the address of a place

@pytest.mark.parametrize("url", ["https://map.naver.com/p/entry/place/1738583931",
                                 "https://m.place.naver.com/restaurant/1893458392/home"])
def test_an_id_in_a_path_is_not_a_phone_number(s, url):
    b = make_business(s)

    (door,) = tracking.set_doors(s, b.id, [{"kind": "map", "url": url}])

    assert door.url == url


@pytest.mark.parametrize("url", ["https://acme.dev/contact/carol@example.com", "https://acme.dev/?mail=carol@example.com",
                                 "https://carol:pw@acme.dev/"])
def test_an_address_with_an_email_or_userinfo_is_still_refused(s, url):
    b = make_business(s)

    with pytest.raises(tracking.TrackingError):
        tracking.set_doors(s, b.id, [{"kind": "site", "url": url}])


# ------------------------------------------------------------------ 2. empty heard fields on the done form

def test_empty_heard_fields_on_a_done_form_leave_the_earlier_answer(s):
    b = make_business(s)
    p = add(s, b, "alice", source="inbound")
    tracking.set_heard(s, p.id, "friend", "my cousin", lang="en")
    task = contact_task(s, b, p)

    owner_tasks.done(s, task.id, note="talked", outcome=people_tasks.Outcome("talked", heard_via="", heard_words="  "))

    assert (p.heard_via, p.heard_words) == ("friend", "my cousin")


def test_empty_heard_fields_on_a_post_to_do_are_not_an_outcome(s):
    b = make_business(s)
    card, _ = owner_tasks.build_post_card(post_card("linkedin"), None, "en")
    task = owner_tasks.create_post_task(s, b.id, card, "LinkedIn에 소개")

    done = owner_tasks.done(s, task.id, link="https://linkedin.com/x",
                            outcome=people_tasks.Outcome(heard_via="", heard_words=""))

    assert done.status == "DONE"


# ------------------------------------------------------------------ 3. the day a tally is written to

def test_a_tally_goes_to_the_day_the_page_showed_when_it_is_today_or_yesterday(s, seoul):
    b = make_business(s)
    midnight_passed = datetime(2026, 11, 16, 15, 5, tzinfo=UTC)   # 00:05 on the 17th in Seoul

    got = tracking.set_tally(s, b.id, "friend", 4, "2026-11-16", now=midnight_passed)

    assert got["day"] == "2026-11-16"
    assert tracking.set_tally(s, b.id, "friend", 1, "2026-11-17", now=midnight_passed)["total"] == 5


@pytest.mark.parametrize("day", ["2026-11-15", "2026-11-18", "soon", "", None, 20261116])
def test_a_tally_for_any_other_day_is_refused_with_a_reload_message_and_writes_nothing(s, seoul, day):
    b = make_business(s)

    with pytest.raises(tracking.TrackingError) as caught:
        tracking.set_tally(s, b.id, "friend", 4, day, now=datetime(2026, 11, 16, 15, 5, tzinfo=UTC))

    assert "Reload" in caught.value.words("en") and "새로 고친" in caught.value.words("ko")
    assert s.scalar(select(MetricPoint.id).where(MetricPoint.business_id == b.id)) is None


def test_the_tally_route_needs_the_day_and_refuses_a_stale_one(s, api):
    b = make_business(s)
    s.commit()

    assert api.post(f"/api/businesses/{b.id}/tally", json={"key": "friend", "count": 1}).status_code == 422
    stale = api.post(f"/api/businesses/{b.id}/tally", json={"key": "friend", "count": 1, "day": "2001-01-01"})
    assert stale.status_code == 400 and "Reload" in stale.json()["detail"]


# ------------------------------------------------------------------ 4. the owner's day against a window

def test_a_tally_written_at_eight_in_the_morning_in_seoul_is_inside_the_window_that_ends_now(s, seoul):
    b = make_business(s)
    now = datetime(2026, 11, 15, 23, 0, tzinfo=UTC)   # 08:00 on the 16th in Seoul

    tracking.set_tally(s, b.id, "friend", 2, "2026-11-16", now=now)
    tracking.set_tally(s, b.id, "friend", 3, "2026-11-16", now=now + timedelta(hours=1))
    seen = metrics.observed(s, b.id, now - timedelta(days=7), now + timedelta(hours=1), lang="en")

    (point,) = s.scalars(select(MetricPoint).where(MetricPoint.business_id == b.id)).all()
    assert point.observed_at <= now
    assert [(o.metric, o.value) for o in seen.by_stage["first_value"]] == [("heard_tally", 3.0)]
    standing = tracking.standing_for(s, b.id, lang="en", now=now)
    assert standing["today"] == "2026-11-16"
    assert next(r for r in standing["rows"] if r["key"] == "friend")["tallied_today"] == 3


def test_a_window_that_opens_later_the_same_owner_day_keeps_that_days_tally(s, seoul):
    b = make_business(s)
    now = datetime(2026, 11, 16, 2, 0, tzinfo=UTC)   # 11:00 on the 16th in Seoul
    tracking.set_tally(s, b.id, "friend", 2, "2026-11-16", now=now)

    seen = metrics.observed(s, b.id, now - timedelta(hours=1), now, lang="en")

    assert [o.value for o in seen.by_stage["first_value"]] == [2.0]


# ------------------------------------------------------------------ 5. what opens the routine verdict

def test_an_owner_tally_does_not_open_the_routine_verdict(s):
    bet_loop_kit.reset_world()
    bet_loop_kit.install_people(first_value=0)
    b = bet_loop_kit.make_business(s)
    metrics.record(s, b.id, "heard_tally", 5, datetime.now(UTC), source="owner", ref="friend")
    s.flush()

    assert bet_retro._first_value_seen(s, b.id) is False

    metrics.record(s, b.id, "people_first_value", 1, datetime.now(UTC), source="people")
    s.flush()
    assert bet_retro._first_value_seen(s, b.id) is True
    bet_loop_kit.reset_world()


# ------------------------------------------------------------------ 6. the name of a tallied place

@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_tally_is_named_by_the_channels_name_or_the_words_label_and_a_gone_key_stays_as_it_is(s, lang):
    b = make_business(s)
    at = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
    for ref in ("hacker_news", "friend", "gone_channel"):
        metrics.record(s, b.id, "heard_tally", 1, at, source="owner", ref=ref)
    s.flush()

    seen = metrics.observed(s, b.id, at - timedelta(days=1), at + timedelta(days=1), lang=lang)

    prefix = metrics.metric_label("heard_tally", lang) + " "
    assert {o.name.removeprefix(prefix) for o in seen.by_stage["first_value"]} == {
        channels.get("hacker_news").names[lang], tracking.label("friend", lang), "gone_channel"}


# ------------------------------------------------------------------ 7. the prep card's question

def test_the_prep_card_stops_asking_once_the_answer_is_recorded(s):
    b = make_business(s)
    p = add(s, b, "alice", source="inbound")
    ask = "How did you hear about us?"
    assert ask in people_tasks.person_card(s, p, "contact", lang="en")["questions"]

    tracking.set_heard(s, p.id, "friend", None, lang="en")

    assert ask not in people_tasks.person_card(s, p, "contact", lang="en")["questions"]


# ------------------------------------------------------------------ 8. the numbers so far in a live bet

def test_the_hidden_homepage_point_is_not_one_of_the_numbers_so_far(s):
    bet_loop_kit.reset_world()
    b = bet_loop_kit.make_business(s, repo="owner/repo")
    bet = bet_loop_kit.make_bet(s, b, status="RUNNING")
    now = datetime.now(UTC)
    bet.started_at, bet.loop_started_at = now - timedelta(days=1), now - timedelta(days=1)
    metrics.record(s, b.id, "github_homepage", 1, now, source="github", ref="https://acme.dev")
    metrics.record(s, b.id, "github_stars_total", 3, now, source="github")
    s.flush()

    view = roadmap.window_view(s, bet, now)

    assert view["so_far"]["numbers"] == 1
    bet_loop_kit.reset_world()


# ------------------------------------------------------------------ 9. an answer whose channel has gone

def test_a_stored_answer_is_accepted_again_after_its_channel_has_gone_and_a_new_one_must_be_current(s):
    b = make_business(s)
    p = add(s, b, "alice")
    p.heard_via = "gone_channel"
    s.flush()

    tracking.set_heard(s, p.id, "gone_channel", "still the same", lang="en")

    assert (p.heard_via, p.heard_words) == ("gone_channel", "still the same")
    with pytest.raises(people.PeopleError):
        tracking.set_heard(s, p.id, "another_gone_one", None, lang="en")
    other = add(s, b, "bob")
    with pytest.raises(people.PeopleError):
        tracking.set_heard(s, other.id, "gone_channel", None, lang="en")
    assert isinstance(s.get(Person, p.id), Person)
