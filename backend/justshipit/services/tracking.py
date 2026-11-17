"""Tracking: which channel brought the people, kept without a server of our own (roadmap 0.5, docs/design/tracking.md).

Three things live here. **The link on a post card.** Each new post card carries the owner's own places (`doors`) as an
address, tagged with `?ref=<tag>` only where the channel hides where visitors come from and the place keeps a query the
owner can read back (`resolve`). The server writes it (`attach`, in the one place a to-do is made and where a revision
replaces its card); the model never does, and `PostCardDraft` has no field for it. The tag is the channel's code and the
to-do's id in base 36, so it is unique with no counter and holds no name. **The answer.** The owner records how a person
heard about the product (`set_heard`) and taps a count for people who are not on the list (`set_tally`), and may type the
visits and sign-ups their own analytics shows for a tag (`set_tag_counts`). **The counts.** `standing_for` reads the rows
and hands them to the pure `tracking_count.standing`.

Nothing here posts, sends or reads a model's answer. An address, an answer's words and a count are checked on the way in,
and a count the owner types is kept as one the owner told us, never as one the company read.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from justshipit.db.models import ActivityEvent, Business, MetricPoint, OwnerTask, Person
from justshipit.i18n import Lang, msg
from justshipit.i18n.said import Refusal, said
from justshipit.services import activity, audience, channels, tracking_count

DOOR_KINDS = ("site", "store", "map", "package", "repo")
STORED_KINDS = DOOR_KINDS[:-1]   # the repository is derived from `github_repo`, never stored
PARAMS = ("ref", "utm_source")
DEFAULT_PARAM = {"site": "ref"}  # a place that drops or cannot report a query has none
MAX_DOORS = 8
HEARD_WORDS = ("friend", "search", "other")
MAX_WORDS = 120
MAX_TALLY = 1000
MAX_TAG_COUNT = 1_000_000
_TAG = re.compile(r"(?<![0-9a-z])([a-z]{2,4})-([0-9a-z]{1,7})(?![0-9a-z-])")
_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")


class TrackingError(Refusal):
    """A refusal the console shows, worded by the route in the owner's language."""


class TrackingNotFound(TrackingError):
    pass


@dataclass(frozen=True)
class Door:
    """One of the owner's own places a card can link to. `param` is the query name a tag rides in, or None when the place
    drops it or cannot report it. `derived` marks the repository, which is read from the business, not stored."""
    kind: str
    url: str
    param: str | None
    derived: bool = False

    def view(self, lang: Lang) -> dict[str, Any]:
        return {"kind": self.kind, "label": msg(f"tracking.door.{self.kind}", lang), "url": self.url, "param": self.param,
                "derived": self.derived}


# ------------------------------------------------------------------ the owner's places

def stored_doors(business: Business) -> list[Door]:
    """The places the owner saved, in the order they were saved. A row that is not one is skipped, not trusted."""
    out = []
    for row in business.doors or []:
        if (isinstance(row, dict) and row.get("kind") in STORED_KINDS and isinstance(row.get("url"), str)
                and row.get("param") in (None, *PARAMS)):
            out.append(Door(row["kind"], row["url"], row["param"]))
    return out


def repo_door(business: Business) -> Door | None:
    repo = (business.github_repo or "").strip()
    return Door("repo", f"https://github.com/{repo}", None, derived=True) if _REPO.fullmatch(repo) else None


def doors_of(business: Business) -> list[Door]:
    """Every place a card may link to, the primary first: the saved places in order, then the repository."""
    repo = repo_door(business)
    return [*stored_doors(business), *([repo] if repo else [])]


def own_places(business: Business | None) -> tuple[str, ...]:
    """The addresses of the owner's own places (the saved places and the repository), for a card that must say whether
    an address is the owner's own: one is, when it is under one of these (`channels.under_places`)."""
    return tuple(d.url for d in doors_of(business)) if business else ()


def _business(s: Session, business_id: int, *, lock: bool = False) -> Business:
    q = select(Business).where(Business.id == business_id)
    business = s.scalar(q.with_for_update() if lock else q)
    if business is None:
        raise TrackingNotFound(said("tracking.error.no_business", id=business_id))
    return business


def _contact_in_address(url: str) -> bool:
    """Whether an address holds an email address anywhere, or userinfo. A run of digits in its path or query is an id, not a
    phone number, so phone numbers are not looked for."""
    from justshipit.services import people
    return people.contact_info_problem(url) == "email" or "@" in urlparse(url).netloc


def set_doors(s: Session, business_id: int, doors: Sequence[Mapping[str, Any]]) -> list[Door]:
    """Replace the owner's saved places. Each address is checked as a post link is (http or https, one host, no escape
    that hides another host) and may hold no email address or userinfo. A place that names no `param` gets the
    kind's default (`ref` for a site, none for the rest); an explicit null means the place cannot report a tag. The list
    is replaced under the business row lock, so two writers leave one of the two lists whole. Raises TrackingError."""
    from justshipit.services import people
    kept: dict[tuple[str, str], Door] = {}
    for row in doors:
        kind = str(row.get("kind") or "")
        if kind == "repo":
            raise TrackingError(said("tracking.error.door_repo"))
        if kind not in STORED_KINDS:
            raise TrackingError(said("tracking.error.door_kind", options=", ".join(STORED_KINDS)))
        url = str(row.get("url") or "").strip()
        if len(url) > channels.MAX_URL:
            raise TrackingError(said("tracking.error.door_long", n=channels.MAX_URL))
        if problem := channels.link_problem(None, url):
            raise TrackingError(said("tracking.error.door_url", problem=problem))
        if _contact_in_address(url):
            raise TrackingError(said("tracking.error.door_contact"))
        param = row["param"] if "param" in row else DEFAULT_PARAM.get(kind)
        if param not in (None, *PARAMS):
            raise TrackingError(said("tracking.error.door_param", options=", ".join(PARAMS)))
        kept.setdefault((kind, url), Door(kind, url, param))
    if len(kept) > MAX_DOORS:
        raise TrackingError(said("tracking.error.door_many", n=MAX_DOORS))
    business = _business(s, business_id, lock=True)
    business.doors = [{"kind": d.kind, "url": d.url, "param": d.param} for d in kept.values()]
    return doors_of(business)


# ------------------------------------------------------------------ the link on a card

def _with_query(url: str, param: str, tag: str) -> str:
    """The address with `param=tag` in its query, in place of any value the address already held for `param`."""
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != param] + [(param, tag)]
    return urlunparse(parts._replace(path=parts.path or "/", query=urlencode(query)))


def resolve(link: channels.Link, tag: str | None, door: Door) -> str | None:
    """The address the card gives for one place, or None for no link. A `tagged` channel and a place with a `param` give
    the address with the tag in its query; a `plain` channel, or a place that keeps no tag, gives the address as it is; a
    `none` channel gives no link, and the card says a line to speak instead."""
    if link == "none":
        return None
    if link == "tagged" and door.param and tag:
        return _with_query(door.url, door.param, tag)
    return door.url


def links_for(business: Business, channel_key: str, tag: str | None = None) -> list[dict[str, Any]]:
    """The owner's own links for a channel, one per place, the primary first: `{door, url, param}`, where `param` is the
    query name the tag rides in, or None when the address carries none. Without a `tag` every address is the plain one,
    for a reader that has no post to tag (the first-10 kit)."""
    place = channels.get(channel_key)
    if place is None:
        return []
    out = []
    for door in doors_of(business):
        if (url := resolve(place.link, tag, door)) is not None:
            out.append({"door": door.kind, "url": url,
                        "param": door.param if place.link == "tagged" and tag and door.param else None})
    return out


def place_of(card: Mapping[str, Any], title: str = "") -> channels.Channel | None:
    """The place a post card is for: its channel, or for one filed under a generic channel the place its title or
    link names (as the owner reads it)."""
    place = channels.get(card.get("channel") or "")
    if place is not None and place.generic:
        what = card.get("what") if isinstance(card.get("what"), dict) else {}
        place = channels.named(f"{title} {what.get('title') or ''}", card.get("where_url")) or place
    return place


def _base36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = digits[r] + out
        if not n:
            return out


def tag_of(place: channels.Channel, task_id: int) -> str:
    """The channel's code and the to-do's id in base 36: unique with no counter, and no name, business or person in it."""
    return f"{place.tag}-{_base36(task_id)}"


def build_track(task: OwnerTask, business: Business | None) -> dict[str, Any] | None:
    """What a post to-do's card says about its link: `{tag, link, links}`. None for a to-do that is no post, has no
    business to link to, or has no channel."""
    card = task.card or {}
    if task.kind != "post" or task.id is None or business is None or not isinstance(card.get("channel"), str):
        return None
    place = place_of(card, task.title)
    if place is None:
        return None
    tag = tag_of(place, task.id)
    return {"tag": tag, "link": place.link, "links": links_for(business, place.key, tag)}


def attach(s: Session, task: OwnerTask) -> None:
    """Put the link on the post card of a to-do that has an id: where a to-do is made (`owner_tasks._insert`, after the
    flush, so a to-do a stage held has no card to carry one) and where a revision replaces its card (`_take_over`). The
    tag follows the channel, so a retry gives the same one and a revision to another channel gets that channel's."""
    business = s.get(Business, task.business_id) if task.business_id is not None else None
    track = build_track(task, business)
    if track is not None and (task.card or {}).get("track") != track:
        task.card = {**task.card, "track": track}


def _stored_track(card: Mapping[str, Any]) -> dict[str, Any] | None:
    track = card.get("track")
    return track if isinstance(track, dict) and isinstance(track.get("tag"), str) else None


def track_view(task: OwnerTask, lang: Lang) -> dict[str, Any] | None:
    """The link as the console reads it: `{tag, link, links, state, derived}`, and `say` for a place that gets no link.
    A card made before 0.5 that is still open has none stored, so it is derived from the business as it stands now and
    marked `derived`, with no write; a finished one keeps none, since no link was posted. An open card that has one
    stored keeps its tag and reads its addresses from the owner's places as they are now; a finished one shows what
    was stored when it was posted. `state` is `tagged`, `plain`,
    `say` (give the spoken line), or `no_door` (the owner has no place yet)."""
    track, derived = _stored_track(task.card or {}), False
    s = object_session(task)
    live = (build_track(task, s.get(Business, task.business_id))
            if task.kind == "post" and task.status == "OPEN" and s is not None and task.business_id is not None else None)
    if track is None:
        track, derived = live, True
        if track is None:
            return None
    elif live is not None:
        # Not posted yet, so the addresses are the owner's places as they stand now: a place added after the card was
        # made reaches it, with the tag it already has.
        track = {**live, "tag": track["tag"]}
    links = [x for x in track.get("links") or [] if isinstance(x, dict)]
    state = ("say" if track.get("link") == "none" else "no_door" if not links
             else "tagged" if any(x.get("param") for x in links) else "plain")
    out = {"tag": track["tag"], "link": track.get("link"), "links": links, "state": state, "derived": derived}
    if state == "say" and (place := place_of(task.card or {}, task.title)) is not None:
        out["say"] = msg("tracking.say", lang, place=place.names[lang])
    return out


# ------------------------------------------------------------------ the words for an answer

def heard_keys() -> list[str]:
    """What a person may say they heard about the product from: a channel, or friend or search."""
    return [*channels.keys(), *(w for w in HEARD_WORDS if w not in channels.registry())]


def label(key: str, lang: Lang) -> str:
    """The owner's name for a row or an answer: a word, a derived row, or a channel's name."""
    if key in HEARD_WORDS:
        return msg(f"tracking.heard.word.{key}", lang)
    if key in ("known", "reached_out", "no_answer", "organic"):
        return msg(f"tracking.row.{key}", lang)
    place = channels.get(key)
    return place.names[lang] if place else key


def choices(market: Lang, lang: Lang) -> list[dict[str, str]]:
    """What the owner may pick from for "how did they hear about you?": the channels of the business's market, then
    friend, search and other."""
    places = [c for c in channels.for_market(market) if c.key != "other"]
    return [*({"key": c.key, "kind": "channel", "label": c.names[lang]} for c in places),
            *({"key": w, "kind": "word", "label": label(w, lang)} for w in HEARD_WORDS)]


def task_of_tag(s: Session, business_id: int, tag: str) -> OwnerTask | None:
    """The business's post to-do a tag was made for, or None: the id is the base 36 part, and the channel code must still
    be the one its card names."""
    found = _TAG.fullmatch(tag.strip().lower())
    if found is None:
        return None
    task = s.get(OwnerTask, int(found[2], 36))
    if task is None or task.business_id != business_id or task.kind != "post":
        return None
    place = place_of(task.card or {}, task.title)
    return task if place is not None and place.tag == found[1] else None


def _channel_of_words(s: Session, business_id: int, words: str) -> str | None:
    """The channel a person's own words name: a tag the company made, or a place the registry knows by name."""
    for found in _TAG.finditer(words.lower()):
        if (task := task_of_tag(s, business_id, found[0])) and (place := place_of(task.card or {}, task.title)):
            return place.key
    place = channels.named(words)
    return place.key if place else None


def clean_heard(s: Session, business_id: int, via: str | None, words: str | None, *, lang: Lang,
                person: Person | None = None) -> tuple[str | None, str | None]:
    """An answer ready to store: `(heard_via, heard_words)`. The words pass the contact check or are refused whole. A pick
    wins; words with no pick propose the channel they name, or fall to other; neither clears the answer. The pick the
    person already has is kept as it is, even when its channel has since left channels.yaml; a new pick must be a current
    key."""
    from justshipit.services import people
    words = (words or "").strip() or None
    via = (via or "").strip() or None
    if words:
        if len(words) > MAX_WORDS:
            raise people.PeopleError(msg("tracking.error.heard_words_long", lang, n=MAX_WORDS))
        people.no_contact("heard", words, lang=lang)
    if via is not None and via not in heard_keys() and via != (person.heard_via if person else None):
        raise people.PeopleError(msg("tracking.error.heard_unknown", lang, options=", ".join(heard_keys())))
    if via is None and words:
        via = _channel_of_words(s, business_id, words) or "other"
    return via, words


def set_heard(s: Session, person_id: int, heard_via: str | None, heard_words: str | None, *, lang: Lang,
              by: str = "owner", person: Person | None = None) -> Person:
    """The owner's record of how a person heard about the product, replacing the earlier answer, with the move in the
    activity log (`person_heard`: from, to, words). Recording the same answer again changes nothing. Raises PeopleError."""
    from justshipit.services import people
    person = person or people.load(s, person_id, lang=lang, lock=True)
    if person.archived_at is not None:
        raise people.PeopleError(msg("people.removed", lang))
    via, words = clean_heard(s, person.business_id, heard_via, heard_words, lang=lang, person=person)
    if (person.heard_via, person.heard_words) == (via, words):
        return person
    before = person.heard_via
    person.heard_via, person.heard_words = via, words
    activity.record(s, verb="person_heard", entity="person", entity_id=person.id, project_id=None,
                    business_id=person.business_id, actor=by,
                    summary=msg("tracking.event.heard", lang, ref=people.ref(person.id), handle=person.handle,
                                to=label(via or "no_answer", lang)),
                    data={"ref": people.ref(person.id), "from": before, "to": via, "words": words})
    return person


# ------------------------------------------------------------------ counts the owner enters

def owner_today(now: datetime | None = None) -> date:
    """Today in the owner's time zone."""
    from justshipit.services import metrics
    return metrics.owner_date(now or datetime.now(UTC))


def _count(value: object, top: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= top:
        raise TrackingError(said("tracking.error.count", n=f"{top:,}"))
    return value


def _tally_day(day: object, now: datetime | None) -> date:
    """The day a tally is for: the owner's today or the day before, which a page left open past midnight still shows.
    Any other day is refused, and the owner is asked to reload."""
    today = owner_today(now)
    try:
        asked = date.fromisoformat(day) if isinstance(day, str) else None
    except ValueError:
        asked = None
    if asked is None or asked not in (today, today - timedelta(days=1)):
        raise TrackingError(said("tracking.error.tally_day"))
    return asked


def set_tally(s: Session, business_id: int, key: str, count: object, day: object, *,
              now: datetime | None = None) -> dict[str, Any]:
    """The count, for `day` (the day the page showed), of customers who are not on the list and said they came from `key`
    (a channel, friend, search or other). The day's value is replaced, so a double tap or a retry is harmless; it lives in
    `metric_points` as a count the owner told us. Raises TrackingError."""
    from justshipit.services import metrics
    _business(s, business_id)
    if key not in heard_keys():
        raise TrackingError(said("tracking.error.heard_unknown", options=", ".join(heard_keys())))
    n, when = _count(count, MAX_TALLY), _tally_day(day, now)
    metrics.record(s, business_id, "heard_tally", float(n), metrics.owner_midnight(when), source="owner", ref=key)
    s.flush()
    return {"key": key, "day": when.isoformat(), "count": n, "total": tallied(s, business_id).get(key, 0)}


def tallied(s: Session, business_id: int) -> dict[str, int]:
    """Every tally, summed over its days, by channel or word."""
    out: dict[str, int] = {}
    for p in s.scalars(select(MetricPoint).where(MetricPoint.business_id == business_id,
                                                 MetricPoint.metric == "heard_tally", MetricPoint.value.is_not(None))):
        out[p.ref] = out.get(p.ref, 0) + int(p.value)
    return out


def set_tag_counts(s: Session, business_id: int, tag: str, *, visits: object = None, signups: object = None,
                   now: datetime | None = None) -> dict[str, Any]:
    """The visits and sign-ups the owner's own analytics shows for one of this business's tags, as of today. Each is
    kept as typed, a count the company did not read (`tag_visits`, `tag_signups`), and replaces the day's value. Raises
    TrackingError."""
    from justshipit.services import metrics
    _business(s, business_id)
    if visits is None and signups is None:
        raise TrackingError(said("tracking.error.tag_counts"))
    if task_of_tag(s, business_id, tag) is None:
        raise TrackingNotFound(said("tracking.error.tag_unknown"))
    today, tag = owner_today(now), tag.strip().lower()
    day = metrics.owner_midnight(today)
    written: dict[str, int] = {}
    for metric, value in (("tag_visits", visits), ("tag_signups", signups)):
        if value is not None:
            written[metric] = _count(value, MAX_TAG_COUNT)
            metrics.record(s, business_id, metric, float(written[metric]), day, source="owner", ref=tag)
    s.flush()
    return {"tag": tag, "day": today.isoformat(), "visits": written.get("tag_visits"),
            "signups": written.get("tag_signups")}


# ------------------------------------------------------------------ the screen's read model

def _figure(f: tracking_count.Figure | None, lang: Lang) -> dict[str, Any] | None:
    if f is None:
        return None
    return {"n": f.n, "basis": f.basis, "basis_label": msg(f"tracking.basis.{f.basis}", lang), "refs": list(f.refs),
            "as_of": f.as_of.isoformat() if f.as_of else None}


def _kind(key: str) -> str:
    if key in HEARD_WORDS:
        return "word"
    if key == tracking_count.ORGANIC:
        return "organic"
    return "channel" if channels.get(key) else "derived"


def _homepage_offer(s: Session, business: Business, lang: Lang) -> dict[str, str] | None:
    """The site the repository lists, offered as the owner's site when none is saved yet. Nothing writes it: the owner's
    click does (`set_doors`)."""
    if any(d.kind == "site" for d in stored_doors(business)):
        return None
    point = s.scalar(select(MetricPoint).where(MetricPoint.business_id == business.id, MetricPoint.metric == "github_homepage")
                     .order_by(MetricPoint.observed_at.desc(), MetricPoint.id.desc()).limit(1))
    if point is None or not point.value or not point.ref:
        return None
    url = point.ref if "://" in point.ref else f"https://{point.ref}"
    if len(url) > channels.MAX_URL or channels.link_problem(None, url):
        return None
    host = urlparse(url).hostname or url
    return {"url": url, "host": host, "text": msg("tracking.offer.homepage", lang, host=host)}


def _traffic_state(s: Session, business: Business) -> dict[str, Any]:
    """Whether the repository's referrers could be read: `no_repo`, `none_yet`, `unreadable` (the newest read was a gap,
    with the reason as stored) or `ok`."""
    if not business.github_repo:
        return {"state": "no_repo", "reason": None}
    q = select(MetricPoint).where(MetricPoint.business_id == business.id, MetricPoint.metric == "github_referrer")
    order = (MetricPoint.observed_at.desc(), MetricPoint.id.desc())
    real = s.scalar(q.where(MetricPoint.value.is_not(None)).order_by(*order).limit(1))
    gap = s.scalar(q.where(MetricPoint.value.is_(None)).order_by(*order).limit(1))
    if gap is not None and (real is None or gap.observed_at > real.observed_at):
        return {"state": "unreadable", "reason": gap.ref or None}
    return {"state": "ok" if real is not None else "none_yet", "reason": None}


def _facts(s: Session, business: Business) -> tracking_count.Facts:
    from justshipit.services import metrics
    posts, tags = [], []
    for t in s.scalars(select(OwnerTask).where(OwnerTask.business_id == business.id, OwnerTask.kind == "post",
                                               OwnerTask.status.in_(("OPEN", "DONE"))).order_by(OwnerTask.id)):
        place = place_of(t.card or {}, t.title)
        if place is None:
            continue
        ref = f"OT-{t.id}"
        posts.append(tracking_count.Post(ref, place.key, t.done_at.astimezone(UTC).date() if t.status == "DONE" and t.done_at
                                         else None))
        track = _stored_track(t.card or {}) or (build_track(t, business) if t.status == "OPEN" else None)
        if track:
            tags.append(tracking_count.Tag(track["tag"], place.key, ref, t.status == "DONE",
                                           any(x.get("param") for x in track.get("links") or [] if isinstance(x, dict))))
    people = [tracking_count.Person(p.id, f"PPL-{p.id}", p.source, p.stage, p.heard_via, p.archived_at is not None,
                                    p.updated_at.astimezone(UTC).date())
              for p in s.scalars(select(Person).where(Person.business_id == business.id).order_by(Person.id))]
    moved: dict[int, date] = {}
    for ev in s.scalars(select(ActivityEvent).where(ActivityEvent.business_id == business.id, ActivityEvent.entity == "person",
                                                    ActivityEvent.verb == "person_moved").order_by(ActivityEvent.id)):
        if (ev.data or {}).get("to") in tracking_count.GOT_VALUE:
            moved.setdefault(ev.entity_id, ev.at.astimezone(UTC).date())
    by_metric: dict[str, list[tracking_count.Point]] = {m: [] for m in ("github_referrer", "tag_visits", "tag_signups",
                                                                       "heard_tally")}
    for p in s.scalars(select(MetricPoint).where(MetricPoint.business_id == business.id, MetricPoint.metric.in_(by_metric),
                                                 MetricPoint.value.is_not(None))
                       .order_by(MetricPoint.observed_at, MetricPoint.id)):
        key = p.ref.lower() if p.metric == "github_referrer" else p.ref
        by_metric[p.metric].append(tracking_count.Point(f"mp-{p.id}", key, metrics.day_label(p), p.value))
    return tracking_count.Facts(posts, people, moved, by_metric["github_referrer"], by_metric["tag_visits"],
                                by_metric["tag_signups"], by_metric["heard_tally"], tags,
                                {c.key: c.domains for c in channels.registry().values() if c.domains})


def standing_for(s: Session, business_id: int, *, lang: Lang, now: datetime | None = None) -> dict[str, Any]:
    """The "where people came from" section as the console reads it: the rows per channel, the host lines, the first 10 and
    first 100 by channel, the owner's places with the offer the repository makes, and the block for a sign-up form. Every
    figure is `{n, basis, basis_label, refs, as_of}`; a count is never a rate. Raises TrackingNotFound."""
    from justshipit.services import bet_retro
    business = _business(s, business_id)
    day = owner_today(now)
    st = tracking_count.standing(_facts(s, business), day, min_answers=bet_retro.MIN_PEOPLE)
    market = audience.market_of_business(s, business_id)
    traffic = {**_traffic_state(s, business), "as_of": st.referrers_as_of.isoformat() if st.referrers_as_of else None,
               "stale": st.referrers_stale}
    return {
        "business_id": business.id, "today": day.isoformat(),
        "rows": [{"key": r.key, "label": label(r.key, lang), "kind": _kind(r.key), "posts": r.posts,
                  "visits": _figure(r.visits, lang), "visits_at_least": _figure(r.visits_at_least, lang),
                  "signups": _figure(r.signups, lang), "people": _figure(r.people, lang),
                  "got_value": _figure(r.got_value, lang), "tallied": _figure(r.tallied, lang),
                  "tallied_today": r.tallied_today,
                  "tags": [{"tag": t.tag, "post": t.post, "visits": t.visits, "signups": t.signups} for t in r.tags]}
                 for r in st.rows],
        "hosts": [{"host": h.host, "channels": [{"key": c, "label": label(c, lang)} for c in h.channels],
                   "shared": len(h.channels) > 1, "organic": not h.channels, "last_14d": _figure(h.last_14d, lang),
                   "at_least": _figure(h.at_least, lang)} for h in st.hosts],
        "milestones": [{"n": m.n, "have": m.have, "reached_on": m.reached_on.isoformat() if m.reached_on else None,
                        "estimated": m.estimated,
                        "by_key": [{"key": k, "label": label(k, lang), "n": n}
                                   for k, n in sorted(m.by_key.items(), key=lambda kv: (-kv[1], kv[0]))]}
                       for m in st.milestones],
        "answers": st.answers, "too_few": st.too_few, "min_answers": bet_retro.MIN_PEOPLE, "referrers": traffic,
        "doors": [d.view(lang) for d in doors_of(business)], "homepage_offer": _homepage_offer(s, business, lang),
        "choices": choices(market, lang),
        "signup": {"title": msg("tracking.signup.title", lang), "question": msg("tracking.heard.question", lang),
                   "options": [x.strip() for x in msg("tracking.signup.options", lang).split(" / ")],
                   "note": msg("tracking.signup.note", lang)},
    }
