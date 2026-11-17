"""Metric points: the numbers a bet is judged on, at three stages (interest, first value, repeat use).

Only the collectors here write them. GitHub is read once a day because its traffic numbers last 14 days. Stars stay
at the interest stage. First value and repeat use come from the people list (`people_link`), where a person is put by
the owner, not counted from a click. A Hacker News or Reddit post the owner made is read for its score and comments.
A number the source would not give is stored as a point with no value and the reason, so a gap shows as a gap. The
reason is written in the owner's language of the day it was collected and stays so, like any stored text.

HTTP goes through `Fetch`, a function from a URL to a `Reply`, so every collector runs on recorded replies in tests.
A rate limit is `MustWait` until the limit resets: the job returns to the queue and nothing is lost, since the
collector writes only after it has read everything.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from justshipit.db.models import Business, MetricPoint, OwnerTask
from justshipit.i18n import Lang, msg
from justshipit.services import app_settings, channels, people_link, profile, secrets
from justshipit.services.usage import MustWait

log = logging.getLogger(__name__)

Stage = Literal["interest", "first_value", "repeat"]
Agg = Literal["delta", "sum", "latest"]


@dataclass(frozen=True)
class Metric:
    """How a metric is read. Its name for the owner is `metrics.metric.<key>` in the catalog. `from_owner` marks a count
    the owner told the company, which a bet retro words as told (`told`) or as typed from the owner's own analytics
    (`typed`) and never as one the company read. `hidden` keeps a stored setting out of the bet's observations."""
    stage: Stage
    agg: Agg
    from_owner: Literal["told", "typed"] | None = None
    hidden: bool = False


# delta: a running total, read as its change over the window. sum: a daily count, added over the window.
# latest: a number about one thing (a referrer, a post), read at its newest.
METRICS: dict[str, Metric] = {
    "github_stars_total": Metric("interest", "delta"),
    "github_forks_total": Metric("interest", "delta"),
    "github_open_items": Metric("interest", "delta"),
    "github_stars_new": Metric("interest", "sum"),
    "github_issues_external_new": Metric("interest", "sum"),
    "github_views": Metric("interest", "sum"),
    "github_clones": Metric("interest", "sum"),
    "github_views_uniques": Metric("interest", "latest"),
    "github_clones_uniques": Metric("interest", "latest"),
    "github_referrer": Metric("interest", "latest"),
    "github_path": Metric("interest", "latest"),
    "hn_score": Metric("interest", "latest"),
    "hn_comments": Metric("interest", "latest"),
    "reddit_score": Metric("interest", "latest"),
    "reddit_comments": Metric("interest", "latest"),
    "people_first_value": Metric("first_value", "delta"),
    "people_repeat": Metric("repeat", "delta"),
    # what the owner enters (services/tracking.py), source `owner`, ref the channel key, word or tag it is about
    "heard_tally": Metric("first_value", "sum", from_owner="told"),
    "tag_visits": Metric("interest", "latest", from_owner="typed"),
    "tag_signups": Metric("interest", "latest", from_owner="typed"),
    # the site the repository lists, read with the daily GitHub numbers (ref is the address, 0 when it lists none)
    "github_homepage": Metric("interest", "latest", hidden=True),
}
HIDDEN_METRICS = tuple(k for k, m in METRICS.items() if m.hidden)
STAGES: tuple[Stage, ...] = ("interest", "first_value", "repeat")


def stage_label(stage: Stage, lang: Lang) -> str:
    return msg(f"metrics.stage.{stage}", lang)


def metric_label(metric: str, lang: Lang) -> str:
    return msg(f"metrics.metric.{metric}", lang)


def day_of(at: datetime) -> datetime:
    return at.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def owner_date(at: datetime) -> date:
    """The calendar day `at` falls on in the owner's time zone."""
    return at.astimezone(ZoneInfo(app_settings.effective().usage.timezone)).date()


def owner_midnight(day: date) -> datetime:
    """The moment the owner's `day` begins. A count the owner enters is dated by it, so a count written today is never
    dated after now, in any time zone, and a repeat of the day's count lands on the same point and replaces it."""
    return datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(app_settings.effective().usage.timezone)).astimezone(UTC)


def day_label(p: MetricPoint) -> date:
    """The day a point stands for: the owner's day for a count the owner entered, the UTC day for what a collector read."""
    return owner_date(p.observed_at) if p.source == "owner" else p.observed_at.astimezone(UTC).date()


def record(s: Session, business_id: int, metric: str, value: float | None, at: datetime, *, source: str,
           ref: str = "") -> None:
    """Store one point. The same metric, source, ref and moment replace each other, so a run that repeats is harmless."""
    stage = METRICS[metric].stage
    stmt = insert(MetricPoint).values(business_id=business_id, metric=metric, stage=stage, value=value, observed_at=at,
                                      source=source, ref=ref[:300])
    s.execute(stmt.on_conflict_do_update(constraint="uq_metric_point", set_={"value": stmt.excluded.value,
                                                                              "stage": stage}))


# ------------------------------------------------------------------ http

@dataclass(frozen=True)
class Reply:
    status: int
    body: Any
    headers: Mapping[str, str]


Fetch = Callable[[str, Mapping[str, Any] | None, Mapping[str, str] | None], Reply]
UA = "ship-collector (+https://github.com)"


@contextmanager
def http_fetch(timeout: float = 20.0) -> Iterator[Fetch]:
    """The real network, with one client that is closed when the block ends; a body that is not JSON comes back as None."""
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        def fetch(url: str, params: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None) -> Reply:
            r = client.get(url, params=params, headers={"User-Agent": UA, **(headers or {})})
            try:
                body = r.json()
            except ValueError:
                body = None
            return Reply(r.status_code, body, {k.lower(): v for k, v in r.headers.items()})

        yield fetch


@contextmanager
def using(fetch: Fetch | None) -> Iterator[Fetch]:
    """The given fetch (a test's recorded replies), or the real network for the length of the block."""
    if fetch is not None:
        yield fetch
        return
    with http_fetch() as real:
        yield real


Reading = Literal["releases", "star_times", "traffic", "issues", "repo"]


def rate_limit_wait(reply: Reply, what: Reading, *, lang: Lang) -> None:
    """GitHub says a call was refused for the limit with a 403 or 429 and `x-ratelimit-remaining: 0` (or `retry-after`).
    Nothing else is a limit: a 403 for a missing permission is a gap, not a reason to wait. The wait's sentence names
    what was being read, for the owner's notice."""
    h = reply.headers
    if reply.status not in (403, 429) or not (h.get("x-ratelimit-remaining") == "0" or "retry-after" in h):
        return
    now = datetime.now(UTC)
    if "retry-after" in h and str(h["retry-after"]).isdigit():
        until = now + timedelta(seconds=int(h["retry-after"]))
    elif str(h.get("x-ratelimit-reset", "")).isdigit():
        until = datetime.fromtimestamp(int(h["x-ratelimit-reset"]), UTC)
    else:
        until = now + timedelta(hours=1)
    raise MustWait(until, "github_rate_limit", msg("metrics.wait", lang, what=msg(f"metrics.reading.{what}", lang)))


# ------------------------------------------------------------------ github

API = "https://api.github.com"
JSON_ACCEPT = "application/vnd.github+json"
STAR_ACCEPT = "application/vnd.github.star+json"
WINDOW_DAYS = 14
MAX_STAR_PAGES = 5
MAX_ISSUE_PAGES = 5
INSIDERS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_LAST_PAGE = re.compile(r'[?&]page=(\d+)[^>]*>;\s*rel="last"')
_HAS_NEXT = re.compile(r'rel="next"')


class CollectError(ValueError):
    pass


def check_repo(repo: str, *, lang: Lang) -> str:
    repo = repo.strip()
    if not _REPO.match(repo):
        raise CollectError(msg("metrics.error.repo_shape", lang))
    return repo


@dataclass
class Collected:
    points: int = 0
    inbound: int = 0
    missing: dict[str, str] = field(default_factory=dict)


@dataclass
class _Plan:
    """What one read of GitHub found, held until it has all been read."""
    points: list[tuple[str, float | None, datetime, str]]
    inbound: list[tuple[str, str, str]]
    missing: dict[str, str]


def github_get(fetch: Fetch, path: str, token: str | None, accept: str = JSON_ACCEPT, params: Mapping[str, Any] | None = None) -> Reply:
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", **({"Authorization": f"Bearer {token}"} if token else {})}
    return fetch(f"{API}{path}", params, headers)


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _star_page(fetch: Fetch, repo: str, token: str | None, page: int, lang: Lang) -> Reply:
    r = github_get(fetch, f"/repos/{repo}/stargazers", token, STAR_ACCEPT, {"per_page": 100, "page": page})
    rate_limit_wait(r, "star_times", lang=lang)
    return r


def _readable(r: Reply) -> bool:
    return r.status == 200 and isinstance(r.body, list)


def _recent_stars(fetch: Fetch, repo: str, token: str | None, since: datetime, lang: Lang,
                  ) -> tuple[list[datetime], str | None]:
    """Star times since `since`. The list is oldest first, so read the last page and walk back until a page reaches
    before the window. A repo with a huge history stops at `MAX_STAR_PAGES` and says the count may be short."""
    first = _star_page(fetch, repo, token, 1, lang)
    if not _readable(first):
        return [], msg("metrics.gap.stars_unreadable", lang, status=first.status)
    found = _LAST_PAGE.search(first.headers.get("link", ""))
    page = int(found.group(1)) if found else 1
    reply, kept = (first if page == 1 else _star_page(fetch, repo, token, page, lang)), []
    for _ in range(MAX_STAR_PAGES):
        if not _readable(reply):
            return kept, msg("metrics.gap.stars_partial", lang, page=page, status=reply.status)
        times = [_time(x["starred_at"]) for x in reply.body if x.get("starred_at")]
        kept += [t for t in times if t >= since]
        if page == 1 or not times or min(times) < since:
            return kept, None
        page -= 1
        reply = _star_page(fetch, repo, token, page, lang)
    return kept, msg("metrics.gap.stars_capped", lang, pages=MAX_STAR_PAGES)


def _per_day(times: list[datetime], today: datetime, days: int) -> dict[datetime, int]:
    counts = dict.fromkeys((today - timedelta(days=i) for i in range(days)), 0)
    for t in times:
        if (d := day_of(t)) in counts:
            counts[d] += 1
    return counts


def _traffic(fetch: Fetch, repo: str, token: str | None, today: datetime, plan: _Plan, lang: Lang) -> None:
    """Views, clones, referrers and paths. GitHub keeps 14 days, so each run stores what it sees and the table keeps
    what GitHub has dropped. The endpoints need push access, so a token without it leaves a gap."""
    for kind, daily, uniques in (("views", "github_views", "github_views_uniques"),
                                 ("clones", "github_clones", "github_clones_uniques")):
        r = github_get(fetch, f"/repos/{repo}/traffic/{kind}", token)
        rate_limit_wait(r, "traffic", lang=lang)
        if r.status != 200 or not isinstance(r.body, dict):
            plan.missing[daily] = msg("metrics.gap.traffic_access", lang, status=r.status)
            continue
        for day in r.body.get(kind, []):
            at = day_of(_time(day["timestamp"]))
            plan.points += [(daily, float(day["count"]), at, ""), (uniques, float(day["uniques"]), at, "")]
    for kind, metric in (("referrers", "github_referrer"), ("paths", "github_path")):
        r = github_get(fetch, f"/repos/{repo}/traffic/popular/{kind}", token)
        rate_limit_wait(r, "traffic", lang=lang)
        if r.status != 200 or not isinstance(r.body, list):
            plan.missing[metric] = msg("metrics.gap.traffic_access", lang, status=r.status)
            continue
        for row in r.body:
            name = row.get("referrer") if kind == "referrers" else row.get("path")
            if name:
                plan.points.append((metric, float(row["count"]), today, str(name)))


def _issues(fetch: Fetch, repo: str, token: str | None, since: datetime, lang: Lang,
            ) -> tuple[list[dict] | None, str | None]:
    """Issues and pull requests updated since `since`, read page by page up to `MAX_ISSUE_PAGES`. None when the first page
    cannot be read; a later page that fails, or more pages than the cap, leaves a reason with what was read."""
    found: list[dict] = []
    for page in range(1, MAX_ISSUE_PAGES + 1):
        reply = github_get(fetch, f"/repos/{repo}/issues", token,
                           params={"state": "all", "since": since.isoformat().replace("+00:00", "Z"), "per_page": 100,
                                   "sort": "created", "direction": "desc", "page": page})
        rate_limit_wait(reply, "issues", lang=lang)
        if not _readable(reply):
            if page == 1:
                return None, msg("metrics.gap.issues_unreadable", lang, status=reply.status)
            return found, msg("metrics.gap.issues_partial", lang, page=page, status=reply.status)
        found += reply.body
        if not _HAS_NEXT.search(reply.headers.get("link", "")):
            return found, None
    return found, msg("metrics.gap.issues_capped", lang, pages=MAX_ISSUE_PAGES)


def read_github(repo: str, token: str | None, fetch: Fetch, now: datetime, *, lang: Lang) -> _Plan:
    """Read everything for one run. Nothing is stored here: a rate limit raises `MustWait` and the run starts over."""
    today = day_of(now)
    since = today - timedelta(days=WINDOW_DAYS - 1)
    plan = _Plan([], [], {})
    info = github_get(fetch, f"/repos/{repo}", token)
    rate_limit_wait(info, "repo", lang=lang)
    if info.status != 200 or not isinstance(info.body, dict):
        raise CollectError(msg("metrics.error.repo_unread", lang, repo=repo, status=info.status))
    plan.points += [("github_stars_total", float(info.body["stargazers_count"]), today, ""),
                    ("github_forks_total", float(info.body["forks_count"]), today, ""),
                    ("github_open_items", float(info.body["open_issues_count"]), today, "")]
    homepage = str(info.body.get("homepage") or "").strip()
    plan.points.append(("github_homepage", 1.0 if homepage else 0.0, today, homepage))

    stars, why = _recent_stars(fetch, repo, token, since, lang)
    if why:
        plan.missing["github_stars_new"] = why
    if why is None or stars:
        plan.points += [("github_stars_new", float(n), d, "") for d, n in _per_day(stars, today, WINDOW_DAYS).items()
                        if why is None or n]

    found, why = _issues(fetch, repo, token, since, lang)
    if found is None:
        plan.missing["github_issues_external_new"] = why
    else:
        if why:
            plan.missing["github_issues_external_new"] = why
        outside = [i for i in found if "pull_request" not in i and _time(i["created_at"]) >= since
                   and i.get("author_association") not in INSIDERS and (i.get("user") or {}).get("type") == "User"]
        plan.points += [("github_issues_external_new", float(n), d, "")
                        for d, n in _per_day([_time(i["created_at"]) for i in outside], today, WINDOW_DAYS).items()]
        plan.inbound = [(i["user"]["login"], i["html_url"], i.get("title") or "") for i in outside]
    _traffic(fetch, repo, token, today, plan, lang)
    return plan


def collect_github(s: Session, business_id: int, *, lang: Lang, fetch: Fetch | None = None,
                   now: datetime | None = None) -> Collected:
    """One daily run for a business with a repo. The token is the `github_token` secret, optional: without it the
    public numbers are read and the traffic ones are gaps, each with its reason in `lang`. Authors of new outside
    issues go onto the people list."""
    business = s.get(Business, business_id)
    if business is None or not business.github_repo:
        raise CollectError(msg("metrics.error.no_repo", lang, id=business_id))
    now = now or datetime.now(UTC)
    with using(fetch) as http:
        plan = read_github(business.github_repo, secrets.get("github_token"), http, now, lang=lang)
    for metric, value, at, ref in plan.points:
        record(s, business_id, metric, value, at, source="github", ref=ref)
    for metric, why in plan.missing.items():
        record(s, business_id, metric, None, day_of(now), source="github", ref=why)
    inbound = 0
    for login, url, title in plan.inbound:
        inbound += people_link.provider().add_inbound(s, business_id, "github", login, url, title[:300] or None,
                                                          lang=lang)
    return Collected(len(plan.points), inbound, plan.missing)


def snapshot_people(s: Session, business_id: int, now: datetime | None = None) -> dict[str, int]:
    """First value and repeat use, as the people list counts them today. Stars are not these."""
    people = people_link.provider()
    if not people.available:
        return {}
    counts = people.north_star(s, business_id)
    at = day_of(now or datetime.now(UTC))
    record(s, business_id, "people_first_value", float(counts["first_value"]), at, source="people")
    record(s, business_id, "people_repeat", float(counts["repeat"]), at, source="people")
    return counts


# ------------------------------------------------------------------ post reactions

HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
REACTION_DAYS = 14


def hn_item_id(url: str) -> int | None:
    parts = urlparse(url)
    ids = parse_qs(parts.query).get("id", [])
    return int(ids[0]) if parts.hostname == "news.ycombinator.com" and ids and ids[0].isdigit() else None


def reddit_json_url(url: str) -> str | None:
    """The post's JSON address: its own URL plus `.json`. Only a link the Reddit channel accepts is read."""
    if channels.link_problem(channels.get("reddit"), url):
        return None
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}.json"


def read_reaction(link: str, fetch: Fetch, *, lang: Lang) -> tuple[str, float | None, float | None, str] | None:
    """(source, score, comments, why) for a post link, or None for a link that is neither Hacker News nor Reddit.
    A post the site will not show us is unknown, with the reason in `lang`. It is never scraped around."""
    if (item := hn_item_id(link)) is not None:
        r = fetch(HN_ITEM.format(id=item), None, None)
        if r.status == 200 and isinstance(r.body, dict) and "score" in r.body:
            return "hn", float(r.body["score"]), float(r.body.get("descendants", 0)), ""
        return "hn", None, None, msg("metrics.gap.hn", lang, item=item, status=r.status)
    if (url := reddit_json_url(link)) is not None:
        r = fetch(url, {"limit": 1, "raw_json": 1}, None)
        try:
            data = r.body[0]["data"]["children"][0]["data"]
            return "reddit", float(data["score"]), float(data["num_comments"]), ""
        except (KeyError, IndexError, TypeError):
            return "reddit", None, None, msg("metrics.gap.reddit", lang, status=r.status)
    return None


def poll_reactions(s: Session, *, lang: Lang, fetch: Fetch | None = None, now: datetime | None = None) -> int:
    """Today's score and comments for every post the owner made on Hacker News or Reddit in the last two weeks."""
    now = now or datetime.now(UTC)
    read = 0
    tasks = s.scalars(select(OwnerTask).where(OwnerTask.kind == "post", OwnerTask.status == "DONE",
                                              OwnerTask.business_id.is_not(None),
                                              OwnerTask.done_at >= now - timedelta(days=REACTION_DAYS))).all()
    with using(fetch) as http:
        for task in tasks:
            link = (task.result or {}).get("link")
            got = read_reaction(link, http, lang=lang) if link else None
            if got is None:
                continue
            source, score, comments, why = got
            at = day_of(now)
            record(s, task.business_id, f"{source}_score", score, at, source=source, ref=link if score is not None else why)
            record(s, task.business_id, f"{source}_comments", comments, at, source=source,
                   ref=link if comments is not None else why)
            read += 1
    return read


# ------------------------------------------------------------------ reading a window

class Obs(BaseModel):
    metric: str
    name: str
    value: float
    basis: str
    evidence_id: str


class Window(BaseModel):
    start: str
    end: str


class Observed(BaseModel):
    """What the numbers say for a window, stage by stage, and what could not be read. Built by the server from
    metric points, never written by a model."""
    by_stage: dict[str, list[Obs]]
    window: Window
    missing: list[str]
    gaps: list[str] = []  # the readings that failed, without the stages that have none yet


def eid(point_id: int) -> str:
    return f"mp-{point_id}"


def _ref_name(metric: str, ref: str, lang: Lang) -> str:
    """What a tally's ref is called for the reader: the channel's name or the word's label, the key itself when the key
    is no longer a channel."""
    if metric != "heard_tally":
        return ref
    from justshipit.services import tracking
    return tracking.label(ref, lang)


def observed(s: Session, business_id: int, start: datetime, end: datetime, *, lang: Lang) -> Observed:
    """The window's numbers, named in `lang`. A stored gap's reason stays in the language it was written in."""
    lo, owner_lo = day_of(start), owner_midnight(owner_date(start))
    rows = s.scalars(select(MetricPoint).where(MetricPoint.business_id == business_id, MetricPoint.observed_at <= end)
                     .order_by(MetricPoint.observed_at, MetricPoint.id))
    by_metric: dict[str, list[MetricPoint]] = defaultdict(list)
    for r in rows:
        by_metric[r.metric].append(r)
    out: dict[str, list[Obs]] = {st: [] for st in STAGES}
    missing: list[str] = []
    for name, points in sorted(by_metric.items()):
        spec = METRICS.get(name)
        if spec is None or spec.hidden:
            continue
        label = metric_label(name, lang)
        real = [p for p in points if p.value is not None]
        inside = [p for p in real if p.observed_at >= (owner_lo if p.source == "owner" else lo)]
        if not inside:
            if gaps := [p for p in points if p.value is None and p.observed_at >= lo]:
                missing.append(f"{label}: {gaps[-1].ref}")
            continue
        if spec.agg == "sum":
            # a tally is one point per channel per day, so a channel's days add and its name stays on the line
            by_ref: dict[str, list[MetricPoint]] = defaultdict(list)
            for p in inside:
                by_ref[p.ref if spec.from_owner else ""].append(p)
            basis_key = "metrics.basis.told" if spec.from_owner == "told" else "metrics.basis.sum"
            out[spec.stage] += [Obs(metric=name, name=f"{label} {_ref_name(name, ref, lang)}".strip(),
                                    value=sum(p.value for p in group),
                                    basis=msg(basis_key, lang, n=len(group)), evidence_id=eid(group[-1].id))
                                for ref, group in by_ref.items()]
        elif spec.agg == "delta":
            before = [p for p in real if p.observed_at <= lo]
            base = before[-1] if before else inside[0]
            basis = msg("metrics.basis.delta" if before else "metrics.basis.delta_from_first", lang,
                        now=f"{inside[-1].value:g}")
            out[spec.stage].append(Obs(metric=name, name=label, value=inside[-1].value - base.value, basis=basis,
                                       evidence_id=eid(inside[-1].id)))
        else:
            newest = {p.ref: p for p in inside}
            basis = msg(f"metrics.basis.{spec.from_owner or 'latest'}", lang)
            out[spec.stage] += [Obs(metric=name, name=f"{label} {ref}".strip(), value=p.value, basis=basis,
                                    evidence_id=eid(p.id))
                                for ref, p in newest.items()]
    gaps = list(missing)
    for stage in ("first_value", "repeat"):
        if not out[stage]:
            missing.append(msg("metrics.missing.stage", lang, stage=stage_label(stage, lang)))
    return Observed(by_stage=out, window=Window(start=start.isoformat(), end=end.isoformat()), missing=missing,
                    gaps=gaps)


def has_points(s: Session, business_id: int) -> bool:
    return bool(s.scalar(select(func.count()).select_from(MetricPoint).where(MetricPoint.business_id == business_id)))


# ------------------------------------------------------------------ the three numbers for the business page

STAGE_METRIC: dict[str, str] = {"interest": "github_stars_total", "first_value": "people_first_value", "repeat": "people_repeat"}


def stage_trends(s: Session, business_id: int, *, lang: Lang, days: int = 30, now: datetime | None = None,
                 ) -> list[dict[str, Any]]:
    """Interest, first value and repeat use as the owner reads them: the number now, one value per day for the last
    `days` days, and when the number is unknown the reason, in `lang`. First value and repeat use are counted from the
    people list as it stands, since the owner moves people by hand; stars are the newest star total GitHub gave, and
    only a business whose buyers are developers (`profile.of`) has that row at all: stars say nothing about a
    dog-walking service. That row carries `guess`, true while every developers value is only the CEO's guess. A reason
    the collector stored stays in the language it was written in."""
    business = s.get(Business, business_id)
    now = now or datetime.now(UTC)
    since = day_of(now - timedelta(days=days - 1))
    people = people_link.provider()
    counted = people.north_star(s, business_id) if people.available else {}
    prof = profile.of(s, business_id)
    stars = prof.has("buyer", "developers")
    stars_guess = all(v.provenance == "guess" for v in prof.lines["buyer"].values if v.code == "developers")
    out = []
    for stage in (st for st in STAGES if stars or st != "interest"):
        metric = STAGE_METRIC[stage]
        rows = list(s.scalars(select(MetricPoint).where(MetricPoint.business_id == business_id, MetricPoint.metric == metric,
                                                        MetricPoint.observed_at >= since, MetricPoint.observed_at <= now)
                              .order_by(MetricPoint.observed_at, MetricPoint.id)))
        real = {day_of(r.observed_at).date().isoformat(): r.value for r in rows if r.value is not None}
        if stage == "interest":
            latest = s.scalar(select(MetricPoint.value).where(MetricPoint.business_id == business_id, MetricPoint.metric == metric,
                                                              MetricPoint.value.is_not(None))
                              .order_by(MetricPoint.observed_at.desc(), MetricPoint.id.desc()).limit(1))
            value, why = (None if latest is None else int(latest)), None
            if value is None:
                gap = next((r.ref for r in reversed(rows) if r.value is None and r.ref), None)
                why = (msg("metrics.unknown.no_repo", lang) if business is None or not business.github_repo
                       else gap or msg("metrics.unknown.none_yet", lang))
        else:
            value = counted.get(stage) if people.available else None
            why = None if people.available else msg("metrics.unknown.no_people", lang)
        out.append({"stage": stage, "label": stage_label(stage, lang), "value": value, "why_unknown": why,
                    "series": [{"day": d, "value": v} for d, v in real.items()],
                    **({"guess": stars_guess} if stage == "interest" else {})})
    return out
