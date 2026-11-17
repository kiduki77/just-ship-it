"""The shell that reads a business's rows for `tracking_count.standing`, the `GET /businesses/{id}/tracking` read model, the
GitHub homepage offer, and the bet retro's wording of what the owner told."""
from datetime import UTC, datetime, timedelta

import pytest
from justshipit.api.app import app
from justshipit.db.models import ActivityEvent, Bet
from justshipit.db.session import session_scope
from justshipit.services import bet_retro, metrics, owner_tasks, people, tracking
from fastapi.testclient import TestClient

from bet_loop_kit import install_people, make_bet
from bet_loop_kit import make_business as make_bet_business
from owner_task_kit import post_card
from people_kit import add, make_business

NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def done_post(s, business, channel="linkedin", title="LinkedIn에 소개", *, at=NOW - timedelta(days=2)):
    card, _ = owner_tasks.build_post_card(post_card(channel), None, "en")
    task = owner_tasks.create_post_task(s, business.id, card, title)
    task.status, task.done_at = "DONE", at
    s.flush()
    return task


def moved(s, person, to, at):
    s.add(ActivityEvent(actor="owner", verb="person_moved", entity="person", entity_id=person.id, business_id=person.business_id,
                        summary="m", data={"to": to}, at=at))
    s.flush()


def test_the_rows_count_the_posts_the_answers_and_the_tally_and_the_got_value_total_is_the_people_lists(s):
    b = make_business(s)
    tracking.set_doors(s, b.id, [{"kind": "site", "url": "https://acme.dev"}])
    post = done_post(s, b)
    alice = add(s, b, "alice", stage="first_value", source="inbound")
    bob = add(s, b, "bob", stage="repeat", source="warm")
    add(s, b, "carol", stage="talked", source="public_problem")
    dave = add(s, b, "dave", stage="first_value", source="inbound")
    add(s, b, "eve", stage="dropped", source="inbound")
    tracking.set_heard(s, alice.id, "linkedin", None, lang="en")
    tracking.set_heard(s, dave.id, None, f"I clicked {post.card['track']['tag']}", lang="en")
    tracking.set_tally(s, b.id, "friend", 2, "2026-11-16", now=NOW)
    tracking.set_tag_counts(s, b.id, post.card["track"]["tag"], visits=40, signups=4, now=NOW)
    moved(s, alice, "first_value", NOW - timedelta(days=5))
    moved(s, bob, "first_value", NOW - timedelta(days=9))
    moved(s, bob, "repeat", NOW - timedelta(days=3))

    got = tracking.standing_for(s, b.id, lang="en", now=NOW)

    rows = {r["key"]: r for r in got["rows"]}
    assert sum(r["got_value"]["n"] for r in got["rows"]) == people.north_star(s, b.id)["first_value"] == 3
    assert (rows["linkedin"]["posts"], rows["linkedin"]["people"]["n"], rows["linkedin"]["got_value"]["n"]) == (1, 2, 2)
    assert rows["linkedin"]["visits"]["basis"] == "typed" and rows["linkedin"]["signups"]["n"] == 4
    assert rows["linkedin"]["tags"] == [{"tag": post.card["track"]["tag"], "post": f"OT-{post.id}", "visits": 40, "signups": 4}]
    assert (rows["friend"]["tallied"]["n"], rows["friend"]["tallied_today"], rows["friend"]["people"]["n"]) == (2, 2, 0)
    assert rows["known"]["label"] == "People you know" and rows["known"]["kind"] == "derived"
    assert rows["reached_out"]["people"]["n"] == 1 and "no_answer" not in rows, "eve dropped, dave answered by a tag"
    assert got["milestones"][0]["have"] == 3 and got["milestones"][0]["estimated"] == 1
    assert got["answers"] == 2 + 2 and got["too_few"] is True and got["min_answers"] == bet_retro.MIN_PEOPLE


def test_the_first_10_is_ordered_by_the_activity_log_and_says_how_many_days_are_estimated(s):
    b = make_business(s)
    a = add(s, b, "a", stage="first_value", source="warm")
    c = add(s, b, "c", stage="first_value", source="warm")
    moved(s, a, "first_value", NOW - timedelta(days=4))
    moved(s, c, "first_value", NOW - timedelta(days=8))

    first_10 = tracking.standing_for(s, b.id, lang="en", now=NOW)["milestones"][0]

    assert first_10 == {"n": 10, "have": 2, "reached_on": None, "estimated": 0,
                        "by_key": [{"key": "known", "label": "People you know", "n": 2}]}


def test_a_post_of_another_business_and_a_waiting_post_count_for_nothing(s):
    b = make_business(s)
    other = make_business(s, name="다른 사업")
    done_post(s, other)
    waiting = done_post(s, b, "devto", "dev.to에 소개")
    waiting.status, waiting.done_at = "OPEN", None
    s.flush()

    got = tracking.standing_for(s, b.id, lang="en", now=NOW)

    assert got["rows"] == []


def test_github_referrers_are_credited_by_the_post_and_read_as_of_the_newest_day(s):
    b = make_business(s)
    b.github_repo = "acme/tool"
    done_post(s, b, "hacker_news", "HN에 소개", at=NOW - timedelta(days=1))
    for days_ago, n in ((8, 20), (1, 33)):
        metrics.record(s, b.id, "github_referrer", n, NOW - timedelta(days=days_ago), source="github", ref="news.ycombinator.com")
    metrics.record(s, b.id, "github_referrer", 9, NOW - timedelta(days=1), source="github", ref="Google")
    s.flush()

    got = tracking.standing_for(s, b.id, lang="en", now=NOW)

    (hn,) = [h for h in got["hosts"] if h["host"] == "news.ycombinator.com"]
    assert hn["channels"] == [{"key": "hacker_news", "label": "Hacker News"}] and hn["shared"] is False
    assert hn["last_14d"] == {"n": 33, "basis": "read", "basis_label": "read", "refs": hn["last_14d"]["refs"],
                              "as_of": "2026-11-15"}
    assert hn["at_least"]["n"] == 33, "8 days apart is one window, not two"
    google = [h for h in got["hosts"] if h["host"] == "google"][0]
    assert google["organic"] is True
    rows = {r["key"]: r for r in got["rows"]}
    assert rows["hacker_news"]["visits"]["n"] == 33 and rows["organic"]["visits"]["n"] == 9
    assert got["referrers"] == {"state": "ok", "reason": None, "as_of": "2026-11-15", "stale": False}


def test_the_traffic_states_say_why_visits_are_unknown_and_never_zero(s):
    b = make_business(s)
    assert tracking.standing_for(s, b.id, lang="en", now=NOW)["referrers"]["state"] == "no_repo"
    b.github_repo = "acme/tool"
    s.flush()
    assert tracking.standing_for(s, b.id, lang="en", now=NOW)["referrers"]["state"] == "none_yet"

    metrics.record(s, b.id, "github_referrer", None, NOW - timedelta(days=4), source="github", ref="Resource not accessible (HTTP 403)")
    metrics.record(s, b.id, "github_referrer", 5, NOW - timedelta(days=9), source="github", ref="reddit.com")
    s.flush()

    got = tracking.standing_for(s, b.id, lang="en", now=NOW)

    assert got["referrers"]["state"] == "unreadable" and got["referrers"]["reason"] == "Resource not accessible (HTTP 403)"
    assert got["referrers"]["stale"] is True and got["referrers"]["as_of"] == "2026-11-07"


def test_the_repository_that_lists_a_site_offers_it_until_the_owner_saves_one(s):
    b = make_business(s)
    b.github_repo = "acme/tool"
    metrics.record(s, b.id, "github_homepage", 1, NOW, source="github", ref="acme.dev")
    s.flush()

    offer = tracking.standing_for(s, b.id, lang="en", now=NOW)["homepage_offer"]

    assert offer == {"url": "https://acme.dev", "host": "acme.dev", "text": "Your repository lists acme.dev. Use it?"}
    assert tracking.standing_for(s, b.id, lang="ko", now=NOW)["homepage_offer"]["text"] == "저장소에 적힌 사이트(acme.dev)를 쓸까요?"
    tracking.set_doors(s, b.id, [{"kind": "site", "url": offer["url"]}])
    assert tracking.standing_for(s, b.id, lang="en", now=NOW)["homepage_offer"] is None


def test_an_unusable_homepage_is_not_offered_and_a_removed_one_stops_being_offered(s):
    b = make_business(s)
    metrics.record(s, b.id, "github_homepage", 1, NOW - timedelta(days=1), source="github", ref="javascript:alert(1)")
    metrics.record(s, b.id, "github_homepage", 1, NOW, source="github", ref="ftp://x.example.com")
    s.flush()
    assert tracking.standing_for(s, b.id, lang="en", now=NOW)["homepage_offer"] is None

    metrics.record(s, b.id, "github_homepage", 1, NOW - timedelta(days=3), source="github", ref="acme.dev")
    metrics.record(s, b.id, "github_homepage", 0, NOW - timedelta(days=2), source="github", ref="")
    s.flush()
    assert tracking.standing_for(s, b.id, lang="en", now=NOW)["homepage_offer"] is None


def test_the_read_model_gives_the_places_the_choices_and_the_signup_block_in_the_owners_language(s):
    b = make_business(s)
    b.github_repo = "acme/tool"
    tracking.set_doors(s, b.id, [{"kind": "site", "url": "https://acme.dev"}])

    en = tracking.standing_for(s, b.id, lang="en", now=NOW)
    ko = tracking.standing_for(s, b.id, lang="ko", now=NOW)

    assert [(d["kind"], d["derived"]) for d in en["doors"]] == [("site", False), ("repo", True)]
    assert en["signup"]["options"] == ["A post (where?)", "A friend or colleague", "Search", "Other"]
    assert ko["signup"]["question"] == "저희를 어디서 알게 되셨어요?" and len(ko["signup"]["options"]) == 4
    assert en["choices"][-3:] == [{"key": "friend", "kind": "word", "label": "Someone told them"},
                                  {"key": "search", "kind": "word", "label": "Search"},
                                  {"key": "other", "kind": "word", "label": "Other"}]
    assert ko["choices"][-1]["label"] == "기타"


def test_the_route_serves_the_section_and_a_missing_business_is_a_404(s, api):
    b = make_business(s)
    done_post(s, b)
    s.commit()

    got = api.get(f"/api/businesses/{b.id}/tracking")

    assert got.status_code == 200
    assert set(got.json()) == {"business_id", "today", "rows", "hosts", "milestones", "answers", "too_few", "min_answers",
                               "referrers", "doors", "homepage_offer", "choices", "signup"}
    assert api.get("/api/businesses/99999999/tracking").status_code == 404
    assert TestClient(app, base_url="http://127.0.0.1:8000").get(f"/api/businesses/{b.id}/tracking").status_code == 401


def test_the_existing_numbers_route_still_answers_as_before(s, api):
    b = make_business(s)
    s.commit()

    got = api.get(f"/api/businesses/{b.id}/numbers")

    assert got.status_code == 200 and {"stages", "completion"} <= set(got.json())


# ------------------------------------------------------------------ the bet retro's wording

@pytest.fixture
def bet_business(s):
    return make_bet_business(s, repo="owner/repo")


def test_a_bet_retro_says_the_tally_was_told_and_the_tag_counts_typed(s, bet_business, outside_cycles):
    install_people(first_value=0)
    tag = done_post(s, bet_business).card["track"]["tag"]
    bet = make_bet(s, bet_business, status="REVIEW")
    now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    bet.started_at = now - timedelta(days=1)
    tracking.set_tally(s, bet_business.id, "friend", 4, tracking.owner_today(now).isoformat(), now=now)
    tracking.set_tag_counts(s, bet_business.id, tag, visits=11, now=now)
    s.commit()

    with session_scope() as x:
        packet = bet_retro.build_packet(x, x.get(Bet, bet.id), now + timedelta(minutes=1), lang="en")

    assert "People not on your list who said they came from Someone told them: 4 (a count the owner told us, sum of 1 day)" in packet.prompt
    assert "Visits to your own link " + tag + ": 11 (typed in by the owner from their own analytics, latest value)" in packet.prompt
    assert not any("No first_value" in m or "got value numbers" in m.lower() for m in packet.observed.missing)
