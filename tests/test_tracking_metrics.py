"""The three metrics the owner enters (`heard_tally`, `tag_visits`, `tag_signups`) and the repository's homepage, and how a
bet's observations word them: a count the owner told, or typed from their own analytics, never one the company read."""
from datetime import UTC, datetime, timedelta

import pytest
from justshipit.db.models import MetricPoint
from justshipit.services import metrics
from justshipit.services.metrics import Reply
from sqlalchemy import select

from bet_loop_kit import NOW, clear_jobs, github, make_business, reset_world


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    clear_jobs("github_collect")
    yield
    reset_world()


@pytest.fixture
def business(s):
    return make_business(s, repo="owner/repo")


def window(s, business, lang="en"):
    return metrics.observed(s, business.id, NOW - timedelta(days=14), NOW + timedelta(days=1), lang=lang)


def test_the_three_metrics_join_the_registry_at_the_stages_the_design_names():
    assert metrics.METRICS["heard_tally"] == metrics.Metric("first_value", "sum", from_owner="told")
    assert metrics.METRICS["tag_visits"].stage == metrics.METRICS["tag_signups"].stage == "interest"
    assert metrics.METRICS["tag_visits"].agg == metrics.METRICS["tag_signups"].agg == "latest"


def test_a_tally_is_worded_as_told_and_a_tag_count_as_typed_and_no_first_value_is_missing(s, business):
    metrics.record(s, business.id, "heard_tally", 2, NOW - timedelta(days=1), source="owner", ref="hacker_news")
    metrics.record(s, business.id, "heard_tally", 3, NOW, source="owner", ref="hacker_news")
    metrics.record(s, business.id, "heard_tally", 1, NOW, source="owner", ref="friend")
    metrics.record(s, business.id, "tag_visits", 40, NOW, source="owner", ref="dg-1c")
    metrics.record(s, business.id, "tag_signups", 4, NOW, source="owner", ref="dg-1c")

    seen = window(s, business)

    told = {o.name: (o.value, o.basis) for o in seen.by_stage["first_value"]}
    assert told == {"People not on your list who said they came from Hacker News": (5.0, "a count the owner told us, sum of 2 days"),
                    "People not on your list who said they came from Someone told them": (1.0, "a count the owner told us, sum of 1 day")}
    typed = {o.name: (o.value, o.basis) for o in seen.by_stage["interest"]}
    assert typed == {"Visits to your own link dg-1c": (40.0, "typed in by the owner from their own analytics, latest value"),
                     "Sign-ups through your own link dg-1c": (4.0, "typed in by the owner from their own analytics, latest value")}
    assert not any("got value" in m.lower() for m in seen.missing)


def test_the_same_day_tally_replaces_instead_of_adding(s, business):
    for n in (1, 2, 2):
        metrics.record(s, business.id, "heard_tally", n, NOW, source="owner", ref="x")
    s.flush()

    assert [p.value for p in s.scalars(select(MetricPoint).where(MetricPoint.metric == "heard_tally",
                                                                 MetricPoint.business_id == business.id))] == [2.0]


def test_the_daily_read_keeps_the_repository_homepage_and_the_bet_never_lists_it(s, business):
    http = github()
    key = "https://api.github.com/repos/owner/repo"
    http.routes[key] = Reply(200, {**http.routes[key].body, "homepage": "https://acme.dev"}, {})

    metrics.collect_github(s, business.id, lang="en", fetch=http, now=NOW)

    home = list(s.scalars(select(MetricPoint).where(MetricPoint.metric == "github_homepage",
                                                    MetricPoint.business_id == business.id)))
    assert [(p.value, p.ref, p.source) for p in home] == [(1.0, "https://acme.dev", "github")]
    assert not any("homepage" in o.name.lower() or "website" in o.name.lower()
                   for rows in window(s, business).by_stage.values() for o in rows)


def test_a_repository_that_lists_no_site_stores_a_zero_so_a_removed_site_stops_being_offered(s, business):
    http = github()
    key = "https://api.github.com/repos/owner/repo"
    http.routes[key] = Reply(200, {**http.routes[key].body, "homepage": ""}, {})

    metrics.collect_github(s, business.id, lang="en", fetch=http, now=NOW)

    assert [(p.value, p.ref) for p in s.scalars(select(MetricPoint).where(
        MetricPoint.metric == "github_homepage", MetricPoint.business_id == business.id))] == [(0.0, "")]
