"""The metric collectors on recorded replies: GitHub numbers, traffic kept past GitHub's 14 days, outside issue authors
going onto the people list, post reactions, and a rate limit that waits instead of failing."""
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest
from justshipit.db.models import Job, MetricPoint
from justshipit.db.session import session_scope
from justshipit.i18n import LANGS, msg
from justshipit.operations import growth
from justshipit.services import metrics, owner_tasks, queue
from justshipit.services.metrics import CollectError, Reply
from justshipit.services.usage import MustWait
from justshipit.workers import runner
from bet_loop_kit import (
    API,
    NOW,
    FakeHttp,
    clear_jobs,
    github,
    install_people,
    make_business,
    no_people,
    reset_world,
)
from i18n_kit import HANGUL
from sqlalchemy import func, select, text
from test_korean_style import smells_in


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    clear_jobs("github_collect")
    yield
    reset_world()


@pytest.fixture
def business(s):
    return make_business(s, repo="owner/repo")


def points(s, business, metric, ref=None):
    q = select(MetricPoint).where(MetricPoint.business_id == business.id, MetricPoint.metric == metric)
    if ref is not None:
        q = q.where(MetricPoint.ref == ref)
    return {p.observed_at.date().isoformat(): p.value for p in s.scalars(q.order_by(MetricPoint.observed_at))}


def collect(business, http, now=NOW):
    with session_scope() as s:
        return metrics.collect_github(s, business.id, lang="ko", fetch=http, now=now)


def test_a_run_stores_the_repo_totals_stars_by_day_and_outside_issues_by_day(s, business):
    people = install_people()
    http = github()

    done = collect(business, http)

    s.expire_all()
    assert points(s, business, "github_stars_total") == {"2026-11-03": 91491.0}
    assert points(s, business, "github_forks_total") == {"2026-11-03": 3749.0}
    stars = points(s, business, "github_stars_new")
    assert stars["2026-11-03"] == 1 and stars["2026-11-02"] == 2 and stars["2026-11-01"] == 1 and stars["2026-10-22"] == 1
    assert stars["2026-10-30"] == 0, "a quiet day inside the window is a zero, not a missing day"
    assert "2026-10-01" not in stars and "2026-10-15" not in stars
    issues = points(s, business, "github_issues_external_new")
    assert issues["2026-11-03"] == 1 and issues["2026-11-02"] == 10, "pull requests and members are not outside issues"
    assert done.missing == {} and done.inbound == 11
    assert len(people.inbound) == 11
    platform, _, url, said = people.inbound[0]
    assert platform == "github" and url.startswith("https://github.com/astral-sh/uv/issues/") and said


def test_the_stargazer_list_is_read_from_its_last_page_back_and_stops_when_a_page_reaches_before_the_window(s, business):
    http = github()

    collect(business, http)

    pages = [c[1]["page"] for c in http.calls if c[0].endswith("/stargazers")]
    assert pages == [1, 2, 1]
    accept = next(c[2]["Accept"] for c in http.calls if c[0].endswith("/stargazers"))
    assert accept == "application/vnd.github.star+json"


def test_traffic_is_stored_by_day_and_referrers_keep_their_name(s, business):
    collect(business, github())

    s.expire_all()
    assert points(s, business, "github_views") == {"2026-11-01": 50.0, "2026-11-02": 70.0}
    assert points(s, business, "github_clones") == {"2026-11-01": 4.0, "2026-11-02": 5.0}
    assert points(s, business, "github_referrer", ref="news.ycombinator.com") == {"2026-11-03": 80.0}
    assert points(s, business, "github_path", ref="/owner/repo") == {"2026-11-03": 100.0}


def test_traffic_days_that_github_has_dropped_stay_in_the_table(s, business):
    collect(business, github())
    http = github()
    later = http.routes[f"{API}/repos/owner/repo/traffic/views"].body
    later["views"] = [{"timestamp": "2026-11-16T00:00:00Z", "count": 33, "uniques": 10}]
    collect(business, http, now=NOW + timedelta(days=14))

    s.expire_all()
    assert points(s, business, "github_views") == {"2026-11-01": 50.0, "2026-11-02": 70.0, "2026-11-16": 33.0}


def test_running_twice_the_same_day_stores_each_number_once(s, business):
    collect(business, github())
    s.expire_all()
    before = s.scalar(select(func.count()).select_from(MetricPoint).where(MetricPoint.business_id == business.id))
    collect(business, github())

    s.expire_all()
    assert s.scalar(select(func.count()).select_from(MetricPoint).where(MetricPoint.business_id == business.id)) == before


def test_without_a_token_the_public_numbers_are_stored_and_each_gap_says_why(s, business):
    http = github(authed=False)

    done = collect(business, http)

    s.expire_all()
    assert points(s, business, "github_stars_total") == {"2026-11-03": 91491.0}
    assert set(done.missing) == {"github_stars_new", "github_views", "github_clones", "github_referrer", "github_path"}
    assert all("401" in why or "토큰" in why for why in done.missing.values())
    gap = s.scalar(select(MetricPoint).where(MetricPoint.business_id == business.id, MetricPoint.metric == "github_views"))
    assert gap.value is None and "토큰" in gap.ref
    seen = metrics.observed(s, business.id, NOW - timedelta(days=7), NOW, lang="ko")
    assert any("저장소 방문" in m and "토큰" in m for m in seen.missing)
    assert [o.name for o in seen.by_stage["interest"] if o.name == "저장소 방문"] == []


def test_the_token_goes_in_a_header_only(s, business, monkeypatch):
    secret = "ghp_notARealToken0123456789"
    monkeypatch.setattr(metrics.secrets, "get", lambda name: secret if name == "github_token" else None)
    http = github()

    collect(business, http)

    for url, params, headers in http.calls:
        assert secret not in url and secret not in str(params)
        assert headers["Authorization"] == f"Bearer {secret}"


def test_no_token_means_no_authorization_header(s, business):
    http = github(authed=False)
    collect(business, http)
    assert all("Authorization" not in c[2] for c in http.calls)


def test_a_repo_github_does_not_know_is_an_error_the_owner_can_read(s, business):
    with pytest.raises(CollectError, match="404"):
        collect(business, FakeHttp({}))
    with pytest.raises(CollectError):
        metrics.check_repo("not a repo", lang="ko")


def test_a_rate_limit_waits_until_it_resets_and_stores_nothing(s, business):
    with pytest.raises(MustWait) as wait:
        collect(business, github(rate_limited=True))

    assert wait.value.reason == "github_rate_limit" and wait.value.until == NOW.replace(microsecond=0) + timedelta(hours=1)
    s.expire_all()
    assert s.scalar(select(func.count()).select_from(MetricPoint).where(MetricPoint.business_id == business.id)) == 0


def test_a_forbidden_call_that_is_not_a_rate_limit_is_a_gap_and_not_a_wait(s, business):
    http = github()
    http.routes[f"{API}/repos/owner/repo/traffic/views"] = Reply(403, {"message": "Must have push access"},
                                                                 {"x-ratelimit-remaining": "41"})
    done = collect(business, http)
    assert "github_views" in done.missing and "github_clones" not in done.missing


def run_job(kind, handler, payload):
    with session_scope() as s:
        job = queue.enqueue(s, kind, {"metrics_test": True, **payload}, max_attempts=1)
        jid = job.id
    with session_scope() as s:
        claimed = queue.claim(s, "worker-test", [kind])
        assert claimed.id == jid
        token = claimed.lease_token
    runner._execute(jid, kind, {"metrics_test": True, **payload}, {kind: handler}, runner.Runs(), queue.Lease(jid, token))
    with session_scope() as s:
        row = s.get(Job, jid)
        s.expunge(row)
        return row


def test_the_collector_job_goes_back_to_the_queue_for_the_reset_time_when_github_says_wait(s, business, monkeypatch):
    reset = int(datetime.now(UTC).timestamp()) + 3600
    monkeypatch.setattr(metrics, "http_fetch", lambda: nullcontext(github(rate_limited=True, reset_at=reset)))

    job = run_job("github_collect", growth.HANDLERS["github_collect"], {"business_id": business.id})

    assert (job.status, job.attempts, job.wait_reason) == ("queued", 0, "github_rate_limit")
    assert job.run_after.timestamp() == reset
    assert "GitHub" in job.last_error


def test_the_collector_job_also_snapshots_the_people_list(s, business, monkeypatch):
    install_people(first_value=4, repeat=1)
    monkeypatch.setattr(metrics, "http_fetch", lambda: nullcontext(github()))

    job = run_job("github_collect", growth.HANDLERS["github_collect"], {"business_id": business.id})

    s.expire_all()
    assert job.status == "done"
    assert list(points(s, business, "people_first_value").values()) == [4.0]
    assert list(points(s, business, "people_repeat").values()) == [1.0]


def test_without_a_people_list_first_value_is_unknown_not_zero(s, business):
    no_people()
    assert metrics.snapshot_people(s, business.id) == {}
    s.expire_all()
    assert points(s, business, "people_first_value") == {}
    seen = metrics.observed(s, business.id, NOW - timedelta(days=7), NOW, lang="ko")
    assert any("도움 됨 단계 숫자가 없어요" in m for m in seen.missing)


# ---------------------------------------------------------------- reading a window

def test_observed_reports_totals_as_change_daily_counts_as_sums_and_stars_stay_at_interest(s, business):
    people = install_people(first_value=2, repeat=0)
    collect(business, github(), now=NOW - timedelta(days=5))
    with session_scope() as x:
        metrics.snapshot_people(x, business.id, NOW - timedelta(days=5))
    people.counts.update(first_value=7, repeat=3)
    http = github()
    http.routes[f"{API}/repos/owner/repo"] = Reply(200, {"stargazers_count": 91500, "forks_count": 3755, "open_issues_count": 3000}, {})
    collect(business, http)
    with session_scope() as x:
        metrics.snapshot_people(x, business.id, NOW)

    s.expire_all()
    seen = metrics.observed(s, business.id, NOW - timedelta(days=7), NOW, lang="ko")
    interest = {o.name: o for o in seen.by_stage["interest"]}
    assert interest["GitHub 별 합계"].value == 9 and "지금 91500" in interest["GitHub 별 합계"].basis
    assert interest["GitHub 포크 합계"].value == 6
    assert interest["외부인이 연 이슈"].value == 11
    assert [o.value for o in seen.by_stage["first_value"]] == [5]
    assert [o.value for o in seen.by_stage["repeat"]] == [3]
    assert not any("star" in o.name.lower() for o in seen.by_stage["first_value"] + seen.by_stage["repeat"])
    assert interest["GitHub 별 합계"].evidence_id.startswith("mp-")


# ---------------------------------------------------------------- post reactions

HN_URL = "https://news.ycombinator.com/item?id=4242"
REDDIT_URL = "https://www.reddit.com/r/opensource/comments/abc123/my_cli/"


def done_post(s, business, link, channel="hacker_news"):
    task = owner_tasks.create_post_task(s, business.id, {"channel": channel}, "게시")
    owner_tasks.done(s, task.id, link=link)
    s.execute(text("UPDATE owner_tasks SET done_at = :t, created_at = :t WHERE id = :i"), {"t": NOW, "i": task.id})
    s.commit()
    return task


def test_a_hacker_news_post_is_read_from_the_firebase_api(s, business):
    done_post(s, business, HN_URL)
    http = FakeHttp({"https://hacker-news.firebaseio.com/v0/item/4242.json": Reply(200, {"score": 57, "descendants": 23, "id": 4242}, {})})

    with session_scope() as x:
        assert metrics.poll_reactions(x, lang="ko", fetch=http, now=NOW) == 1

    s.expire_all()
    assert points(s, business, "hn_score", ref=HN_URL) == {"2026-11-03": 57.0}
    assert points(s, business, "hn_comments", ref=HN_URL) == {"2026-11-03": 23.0}
    assert http.urls() == ["https://hacker-news.firebaseio.com/v0/item/4242.json"]


def test_a_reddit_post_is_read_from_its_url_plus_json(s, business):
    done_post(s, business, REDDIT_URL, channel="reddit")
    body = [{"data": {"children": [{"data": {"score": 31, "num_comments": 9}}]}}, {"data": {"children": []}}]
    http = FakeHttp({"https://www.reddit.com/r/opensource/comments/abc123/my_cli.json": Reply(200, body, {})})

    with session_scope() as x:
        metrics.poll_reactions(x, lang="ko", fetch=http, now=NOW)

    s.expire_all()
    assert points(s, business, "reddit_score", ref=REDDIT_URL) == {"2026-11-03": 31.0}
    assert points(s, business, "reddit_comments", ref=REDDIT_URL) == {"2026-11-03": 9.0}


def test_a_blocked_reddit_page_is_unknown_and_nothing_else_is_tried(s, business):
    done_post(s, business, REDDIT_URL, channel="reddit")
    http = FakeHttp({"https://www.reddit.com/r/opensource/comments/abc123/my_cli.json": Reply(403, None, {})})

    with session_scope() as x:
        metrics.poll_reactions(x, lang="ko", fetch=http, now=NOW)

    s.expire_all()
    row = s.scalar(select(MetricPoint).where(MetricPoint.business_id == business.id, MetricPoint.metric == "reddit_score"))
    assert row.value is None and "403" in row.ref
    assert len(http.calls) == 1
    seen = metrics.observed(s, business.id, NOW - timedelta(days=3), NOW, lang="ko")
    assert any("Reddit 점수" in m and "403" in m for m in seen.missing)


def test_a_post_older_than_two_weeks_and_a_link_elsewhere_are_not_read(s, business):
    old = done_post(s, business, HN_URL)
    done_post(s, business, "https://dev.to/me/post", channel="devto")
    with session_scope() as x:
        x.execute(text("UPDATE owner_tasks SET done_at = :t WHERE id = :i"), {"t": NOW - timedelta(days=20), "i": old.id})
    http = FakeHttp({})

    with session_scope() as x:
        assert metrics.poll_reactions(x, lang="ko", fetch=http, now=NOW) == 0
    assert http.calls == []


def test_the_http_helpers_recognise_only_their_own_links():
    assert metrics.hn_item_id(HN_URL) == 4242
    assert metrics.hn_item_id("https://evil.example/item?id=4242") is None
    assert metrics.reddit_json_url(REDDIT_URL) == "https://www.reddit.com/r/opensource/comments/abc123/my_cli.json"
    assert metrics.reddit_json_url("https://notreddit.com/r/x/comments/1/") is None


# ---------------------------------------------------------------- transactions, pages and the client

def test_a_people_error_in_the_snapshot_never_discards_the_github_points(s, business, monkeypatch):
    install_people(first_value=4)
    monkeypatch.setattr(metrics, "http_fetch", lambda: nullcontext(github()))

    def broken(*a, **kw):
        raise RuntimeError("people list down")
    monkeypatch.setattr(metrics, "snapshot_people", broken)

    job = run_job("github_collect", growth.HANDLERS["github_collect"], {"business_id": business.id})

    s.expire_all()
    assert job.status == "failed"
    assert points(s, business, "github_stars_total") == {metrics.day_of(datetime.now(UTC)).date().isoformat(): 91491.0}


def test_issues_are_read_page_by_page_while_github_says_there_is_a_next_one(s, business):
    people = install_people()
    http = github()
    page = f"{API}/repos/owner/repo/issues"
    first = http.routes[page]
    http.routes[f"{page}#1"] = Reply(200, first.body, {"link": f'<{page}?page=2>; rel="next", <{page}?page=3>; rel="last"'})
    late = [{**first.body[0], "number": 900, "created_at": "2026-11-03T05:00:00Z", "html_url": "https://github.com/owner/repo/issues/900",
             "user": {"login": "late-author", "type": "User"}, "author_association": "NONE"}]
    http.routes[f"{page}#2"] = Reply(200, late, {})

    collect(business, http)

    pages = [c[1]["page"] for c in http.calls if c[0] == page]
    assert pages == [1, 2]
    assert "late-author" in [handle for _, handle, _, _ in people.inbound]


def test_issue_pages_stop_at_the_cap_and_the_gap_says_so(s, business):
    http = github()
    page = f"{API}/repos/owner/repo/issues"
    forever = Reply(200, http.routes[page].body, {"link": f'<{page}?page=99>; rel="next"'})
    http.routes[page] = forever

    done = collect(business, http)

    assert len([c for c in http.calls if c[0] == page]) == metrics.MAX_ISSUE_PAGES == 5
    assert "5쪽까지만" in done.missing["github_issues_external_new"]


def test_a_later_issue_page_that_fails_keeps_what_was_read(s, business):
    http = github()
    page = f"{API}/repos/owner/repo/issues"
    http.routes[f"{page}#1"] = Reply(200, http.routes[page].body, {"link": f'<{page}?page=2>; rel="next"'})
    http.routes[f"{page}#2"] = Reply(500, {"message": "boom"}, {})

    done = collect(business, http)

    assert "2쪽을 읽지 못해" in done.missing["github_issues_external_new"]
    assert points(s, business, "github_issues_external_new")


def test_the_http_client_is_closed_when_the_block_ends(monkeypatch):
    closed = []

    class Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            closed.append(True)

        def get(self, *a, **kw):
            raise AssertionError("not called")

    monkeypatch.setattr(metrics.httpx, "Client", Client)

    with metrics.http_fetch() as fetch:
        assert callable(fetch) and not closed

    assert closed == [True]
    with metrics.using(None):
        pass
    assert closed == [True, True]
    with metrics.using(FakeHttp({})) as given:
        assert isinstance(given, FakeHttp)
    assert closed == [True, True], "a fetch that was given is not closed or replaced"


# ---------------------------------------------------------------- the owner's language

def test_in_english_the_numbers_and_their_gaps_carry_no_korean(s, business):
    no_people()
    done = metrics.collect_github(s, business.id, lang="en", fetch=github(authed=False), now=NOW)
    s.commit()

    seen = metrics.observed(s, business.id, NOW - timedelta(days=7), NOW, lang="en")
    stages = metrics.stage_trends(s, business.id, lang="en", now=NOW)

    texts = [*done.missing.values(), *seen.missing, *(f"{o.name} {o.basis}" for rows in seen.by_stage.values() for o in rows),
             *(f"{x['label']} {x['why_unknown'] or ''}" for x in stages)]
    assert texts and not [t for t in texts if HANGUL.search(t)]
    assert any(m.startswith("Repository views: Traffic needs a token") for m in seen.missing)
    assert [x["label"] for x in stages] == ["Interest", "Got value", "Came back"]
    assert stages[1]["why_unknown"] == "The People list isn't available"


def test_in_korean_the_stage_words_are_the_glossarys_and_the_reasons_are_plain_haeyo(s, business):
    no_people()
    done = collect(business, github(authed=False))

    stages = metrics.stage_trends(s, business.id, lang="ko", now=NOW)
    seen = metrics.observed(s, business.id, NOW - timedelta(days=7), NOW, lang="ko")

    assert [x["label"] for x in stages] == ["관심", "도움 됨", "재사용"]
    reasons = [*done.missing.values(), *seen.missing, *(x["why_unknown"] for x in stages if x["why_unknown"])]
    assert reasons and all("요" in r and not smells_in(r) for r in reasons), reasons


def test_every_metric_stage_and_reading_has_a_name_in_every_language():
    for lang in LANGS:
        assert all(metrics.metric_label(m, lang) for m in metrics.METRICS)
        assert all(metrics.stage_label(st, lang) for st in metrics.STAGES)
        assert all(msg(f"metrics.reading.{r}", lang) for r in get_args(metrics.Reading))


@pytest.mark.parametrize(("lang", "said"), [("en", "Reached the GitHub rate limit, so reading repository info waits"),
                                            ("ko", "GitHub 호출 한도에 닿아서 저장소 정보 읽기를 미뤄요")])
def test_a_rate_limit_tells_the_owner_in_their_language_what_waits(s, business, lang, said):
    with pytest.raises(MustWait) as wait, session_scope() as x:
        metrics.collect_github(x, business.id, lang=lang, fetch=github(rate_limited=True), now=NOW)
    assert wait.value.detail == said


def test_a_repo_name_that_is_not_owner_slash_name_is_refused_in_the_owners_language():
    with pytest.raises(CollectError, match="for example astral-sh/uv"):
        metrics.check_repo("not a repo", lang="en")
    with pytest.raises(CollectError, match="모양으로 적어 주세요"):
        metrics.check_repo("not a repo", lang="ko")
