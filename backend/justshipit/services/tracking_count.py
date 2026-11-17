"""What each channel brought, counted from what is stored and never added across what overlaps (docs/design/tracking.md 2.6).

Pure: `standing` takes the facts the shell (`services/tracking.py`) read and returns the rows the Numbers tab shows. It
reads no database, no clock and no registry, so every counting rule below has a test that feeds it facts.

The rules, as the tests hold them:

1. A person counts once, in one row (`row_key`). A dropped or archived person leaves every count, as in `north_star`.
2. Users are got-value people. Posts, visits and sign-ups are never added to them or to each other.
3. Tallies (people not on the list) stay in their own column, never added to people, and are not in the milestones.
4. GitHub referrer points are 14-day totals saved daily, so they overlap and are never summed. A host shows its newest
   snapshot as "last 14 days, as of {date}" (`read`) and an "at least" total from snapshots at least 14 days apart
   (`at_least`), counted back from the newest. A missed day makes the gap larger, never the number.
5. A host maps to channels by their `domains`. A snapshot is credited to a channel only when that channel has a done post in
   the 14 days up to the snapshot's day. When two channels share the host, the visits stay on the host's one line, never
   split and never given to one. A host with no such post goes to `organic`.
6. A row's visits are the typed visits of its tags when the owner typed any (a site the owner's analytics reads), else the
   GitHub figure of the hosts only it claims. One cell never adds the two.
7. The milestones order got-value people by the day they first moved to first value or repeat, and by the day their row was
   last changed for a person moved before the log existed (`estimated`). Ties go by id.
8. The sum of `got_value` over every row is `north_star`'s first value: a mismatch is a bug, not rounding.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

Basis = Literal["read", "told", "typed", "at_least"]
WINDOW_DAYS = 14
STALE_DAYS = 2
MILESTONES = (10, 100)
GOT_VALUE = ("first_value", "repeat")
DROPPED = "dropped"
KNOWN, REACHED_OUT, NO_ANSWER, ORGANIC = "known", "reached_out", "no_answer", "organic"
REACHED_SOURCES = ("public_problem", "similar_tool", "participation")


def row_key(heard_via: str | None, source: str) -> str:
    """The row a person is in: the answer the owner recorded, or when there is none, what their source says (people the
    owner knows, people the company found and the owner wrote to, or people who came on their own and were never asked)."""
    if heard_via:
        return heard_via
    return KNOWN if source == "warm" else REACHED_OUT if source in REACHED_SOURCES else NO_ANSWER


@dataclass(frozen=True)
class Figure:
    """A number and what it rests on. `refs` are the records behind it, as a bet retro cites them (OT-41, PPL-7, mp-88),
    and `as_of` the day a read number was taken."""
    n: int
    basis: Basis
    refs: tuple[str, ...] = ()
    as_of: date | None = None


@dataclass(frozen=True)
class Post:
    ref: str
    channel: str
    done_on: date | None   # None while the owner has not posted it: it counts for nothing yet


@dataclass(frozen=True)
class Person:
    id: int
    ref: str
    source: str
    stage: str
    heard_via: str | None
    archived: bool
    updated_on: date       # when the row last changed: the milestone's day for a person with no move in the log


@dataclass(frozen=True)
class Point:
    """One stored number: `ref` the record it came from (mp-88), `key` what it is about (a host, a tag, a channel)."""
    ref: str
    key: str
    day: date
    value: float


@dataclass(frozen=True)
class Tag:
    tag: str
    channel: str
    post: str              # the post's ref, OT-41
    done: bool
    tagged: bool           # the card's link carries the tag, so the owner's analytics can show it


@dataclass(frozen=True)
class Facts:
    """Everything `standing` reads. `moved` is the day each person (by id) first moved to first value or repeat, from the
    activity log; `domains` the hosts of each channel."""
    posts: Sequence[Post]
    people: Sequence[Person]
    moved: Mapping[int, date]
    referrers: Sequence[Point]
    visits: Sequence[Point]
    signups: Sequence[Point]
    tallies: Sequence[Point]
    tags: Sequence[Tag]
    domains: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class TagCount:
    tag: str
    post: str
    visits: int | None
    signups: int | None


@dataclass(frozen=True)
class Row:
    key: str
    posts: int
    visits: Figure | None
    visits_at_least: Figure | None
    signups: Figure | None
    people: Figure
    got_value: Figure
    tallied: Figure | None
    tallied_today: int
    tags: tuple[TagCount, ...] = ()


@dataclass(frozen=True)
class Host:
    host: str
    channels: tuple[str, ...]   # who it is credited to as of its newest snapshot: none is organic, two or more is shared
    last_14d: Figure
    at_least: Figure


@dataclass(frozen=True)
class Mix:
    n: int
    have: int                   # got-value people so far, capped at n
    by_key: dict[str, int]      # sums to `have`; no_answer is a key like any other
    reached_on: date | None     # the day the n-th person got value
    estimated: int              # how many of the first n have a day estimated from their row's last change


@dataclass(frozen=True)
class Standing:
    rows: tuple[Row, ...]
    hosts: tuple[Host, ...]
    milestones: tuple[Mix, ...]
    answers: int                # people with an answer, and people tallied: what "too few" counts
    too_few: bool
    referrers_as_of: date | None
    referrers_stale: bool


def _on(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith(f".{d}") for d in domains)


def _latest(points: Sequence[Point]) -> dict[str, Point]:
    """The newest point of each key (a later point of the same day wins, as the points come in the order stored)."""
    out: dict[str, Point] = {}
    for p in points:
        if p.key not in out or p.day >= out[p.key].day:
            out[p.key] = p
    return out


def _hosts(facts: Facts) -> list[Host]:
    posted: dict[str, list[date]] = defaultdict(list)
    for p in facts.posts:
        if p.done_on is not None:
            posted[p.channel].append(p.done_on)
    by_host: dict[str, list[Point]] = defaultdict(list)
    for p in facts.referrers:
        by_host[p.key].append(p)
    out = []
    for host, points in sorted(by_host.items()):
        owners = [c for c, domains in facts.domains.items() if _on(host, domains)]

        def credited(day: date) -> tuple[str, ...]:
            return tuple(sorted(c for c in owners if any(day - timedelta(days=WINDOW_DAYS - 1) <= d <= day
                                                          for d in posted.get(c, []))))

        snaps = {p.day: p for p in sorted(points, key=lambda p: p.day)}
        newest = snaps[max(snaps)]
        who = credited(newest.day)
        chain = [newest]
        for day in sorted((d for d in snaps if credited(d) == who and d < newest.day), reverse=True):
            if day <= chain[-1].day - timedelta(days=WINDOW_DAYS):
                chain.append(snaps[day])
        out.append(Host(host, who, Figure(int(newest.value), "read", (newest.ref,), newest.day),
                        Figure(int(sum(p.value for p in chain)), "at_least", tuple(p.ref for p in chain), newest.day)))
    return out


@dataclass
class _Row:
    posts: int = 0
    people: list[Person] = field(default_factory=list)
    tallied: list[Point] = field(default_factory=list)
    typed_visits: list[Point] = field(default_factory=list)
    typed_signups: list[Point] = field(default_factory=list)
    read: list[Host] = field(default_factory=list)


def standing(facts: Facts, today: date, *, min_answers: int = 30) -> Standing:
    """The rows, hosts and milestones for one business as of `today`. `min_answers` is how many people must have an answer
    or a tally before the screen stops saying it cannot tell channels apart."""
    rows: dict[str, _Row] = defaultdict(_Row)
    for post in facts.posts:
        if post.done_on is not None:
            rows[post.channel].posts += 1
    live = [p for p in facts.people if not p.archived and p.stage != DROPPED]
    for person in live:
        rows[row_key(person.heard_via, person.source)].people.append(person)
    for point in facts.tallies:
        rows[point.key].tallied.append(point)
    channel_of = {t.tag: t.channel for t in facts.tags}
    for point in _latest(facts.visits).values():
        if point.key in channel_of:
            rows[channel_of[point.key]].typed_visits.append(point)
    for point in _latest(facts.signups).values():
        if point.key in channel_of:
            rows[channel_of[point.key]].typed_signups.append(point)
    hosts = _hosts(facts)
    for host in hosts:
        if len(host.channels) <= 1:
            rows[host.channels[0] if host.channels else ORGANIC].read.append(host)

    typed = {t.tag: (_latest(facts.visits).get(t.tag), _latest(facts.signups).get(t.tag)) for t in facts.tags}
    out = []
    for key, r in rows.items():
        got = [p for p in r.people if p.stage in GOT_VALUE]
        visits = (Figure(int(sum(p.value for p in r.typed_visits)), "typed", tuple(p.ref for p in r.typed_visits),
                         max(p.day for p in r.typed_visits)) if r.typed_visits
                  else Figure(sum(h.last_14d.n for h in r.read), "read", tuple(h.last_14d.refs[0] for h in r.read),
                              max(h.last_14d.as_of for h in r.read)) if r.read else None)
        at_least = (Figure(sum(h.at_least.n for h in r.read), "at_least",
                           tuple(ref for h in r.read for ref in h.at_least.refs), max(h.at_least.as_of for h in r.read))
                    if r.read and not r.typed_visits else None)
        signups = (Figure(int(sum(p.value for p in r.typed_signups)), "typed", tuple(p.ref for p in r.typed_signups),
                          max(p.day for p in r.typed_signups)) if r.typed_signups else None)
        tallied = (Figure(int(sum(p.value for p in r.tallied)), "told", tuple(p.ref for p in r.tallied))
                   if r.tallied else None)
        if not (r.posts or r.people or tallied or visits or signups):
            continue
        tags = tuple(TagCount(t.tag, t.post, None if typed[t.tag][0] is None else int(typed[t.tag][0].value),
                              None if typed[t.tag][1] is None else int(typed[t.tag][1].value))
                     for t in facts.tags if t.channel == key and t.done and t.tagged)
        out.append(Row(key, r.posts, visits, at_least, signups,
                       Figure(len(r.people), "told", tuple(p.ref for p in r.people)),
                       Figure(len(got), "told", tuple(p.ref for p in got)), tallied,
                       int(sum(p.value for p in r.tallied if p.day == today)), tags))
    out.sort(key=lambda r: (-r.got_value.n, -r.posts, r.key))

    order = sorted((p for p in live if p.stage in GOT_VALUE), key=lambda p: (facts.moved.get(p.id, p.updated_on), p.id))
    mixes = []
    for n in MILESTONES:
        first = order[:n]
        by_key: dict[str, int] = defaultdict(int)
        for p in first:
            by_key[row_key(p.heard_via, p.source)] += 1
        mixes.append(Mix(n, len(first), dict(by_key),
                         (facts.moved.get(first[-1].id, first[-1].updated_on) if len(first) == n else None),
                         sum(1 for p in first if p.id not in facts.moved)))
    answers = sum(1 for p in live if p.heard_via) + sum(int(p.value) for p in facts.tallies)
    seen = max((p.day for p in facts.referrers), default=None)
    return Standing(tuple(out), tuple(hosts), tuple(mixes), answers, answers < min_answers, seen,
                    seen is not None and (today - seen).days > STALE_DAYS)
