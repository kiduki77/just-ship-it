"""People: who the owner may meet, and where each one stands on the way to a first value.

The company finds people and prepares what to ask. The owner contacts them, from the owner's own account and in the
owner's own words, and records what happened. This module holds the list. It stores no email address and no phone
number: `contact_info_problem` refuses a field that holds one, and there is no column for either.

A person's stage moves along `TRANSITIONS`, a table of edges and who may take them, like an owner task's status. Only
the owner moves a person, so the company cannot count a user it has not met.

Every sentence here is in the owner's language (`lang`, read once by the route or the job). An activity summary keeps
to facts (ref, platform, handle, stage names), since the console already names the event in the reader's language.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from justshipit.db.models import ActivityEvent, Business, OwnerTask, Person
from justshipit.i18n import LANGS, Lang, msg
from justshipit.i18n.match.contact import WORDS as CONTACT_WORDS
from justshipit.i18n.match.names import AFTER_NAME, AFTER_SHORT_NAME, SHORT_NAME
from justshipit.i18n.said import say
from justshipit.services import activity, audience, channels, owner_tasks, profile, tracking, tracking_count
from justshipit.services.profile import core
from justshipit.services.profile.core import Fits, Profile

LADDER = ("found", "contacted", "talked", "first_value", "repeat")
DROPPED = "dropped"
STAGES = (*LADDER, DROPPED)
SOURCES = ("warm", "public_problem", "similar_tool", "participation", "inbound")


Role = Literal["searched", "by_hand"]


@dataclass(frozen=True)
class Place:
    """Where a person was met. `hosts` are the exact hosts the finder may confirm a quote on; a place with none is not
    searched (a login hides its posts, or it is no website), and the owner adds people from it by hand. `hint` tells the
    research agent what to look at there. Its names are the catalog's `people.place.<key>`.

    `fits` says what the place is, in the customer description's codes (`core.fit`). `roles` says, per market, whether
    the finder searches it or only the owner can look (`by_hand`); a market it lacks is one the place is not offered in.
    `fits_in` replaces `fits` in a market where the place is a different kind of place (Reddit is a topic community in
    Korea and a city's subreddit in English). `rank` orders the places `where` offers."""
    key: str
    hosts: tuple[str, ...] = ()
    hint: str = ""
    fits: Fits = field(default_factory=dict)
    roles: dict[Lang, Role] = field(default_factory=dict)
    fits_in: dict[Lang, Fits] = field(default_factory=dict)
    rank: int = 0

    @property
    def names(self) -> dict[Lang, str]:
        return {lang: msg(f"people.place.{self.key}", lang) for lang in LANGS}


_NOT_DEV = ("consumers", "small_business", "professionals", "companies")
_DEVELOPERS: Fits = {"buyer": ("developers",)}
_NEIGHBORS: Fits = {"buyer": ("consumers",), "reach": ("local",)}
_GROUPS: Fits = {"buyer": _NOT_DEV, "reach": ("communities", "local")}
_EVERYWHERE: dict[Lang, Role] = {"ko": "searched", "en": "searched"}
_BY_HAND_EVERYWHERE: dict[Lang, Role] = {"ko": "by_hand", "en": "by_hand"}


def _check_fits(table: dict[str, Place]) -> None:
    """A tag that names a line or code the description lacks fails at import, as a channel's does at load."""
    for place in table.values():
        for fits in (place.fits, *place.fits_in.values()):
            if problem := channels.fits_problem(fits):
                raise ValueError(f"people.PLACES: {place.key} fits {problem}")


# Exact hosts, never a suffix: a name that merely ends in a platform's domain is not that platform's. A forum's hosts
# are the developer forums `company.yaml` lists. `email` and `direct` are where people the owner already knows are added
# by hand. No market offers them yet, so `where` leaves them out.
PLACES: dict[str, Place] = {p.key: p for p in (
    Place("github", ("github.com", "www.github.com"),
          "issues and discussions in repositories about the same problem, or of competing and alternative tools",
          _DEVELOPERS, _EVERYWHERE, rank=1),
    Place("reddit", ("reddit.com", "www.reddit.com", "old.reddit.com"),
          "posts and comments in subreddits on the topic; for a local business, its city's or neighborhood's subreddit",
          {"reach": ("communities",)}, _EVERYWHERE, {"en": {"reach": ("communities", "local")}}, rank=5),
    Place("hn", ("news.ycombinator.com",), "Ask HN posts and comments on related stories", _DEVELOPERS, _EVERYWHERE,
          rank=6),
    Place("stackoverflow", ("stackoverflow.com", "serverfault.com", "superuser.com", "askubuntu.com",
                            "unix.stackexchange.com", "softwareengineering.stackexchange.com", "dba.stackexchange.com",
                            "devops.stackexchange.com", "security.stackexchange.com"), "questions and answers",
          _DEVELOPERS, _EVERYWHERE, rank=7),
    Place("forum", (), "questions and comments on public developer forums", _DEVELOPERS, _EVERYWHERE, rank=8),
    Place("daangn", ("daangn.com", "www.daangn.com"),
          "neighborhood-board posts and comments in that area, only those that open on the web without a login",
          _NEIGHBORS, {"ko": "searched"}, rank=2),
    Place("naver_cafe", ("cafe.naver.com", "m.cafe.naver.com"),
          "posts in local or interest cafes, only those open to non-members", _GROUPS, {"ko": "searched"}, rank=3),
    Place("naver_blog", ("blog.naver.com", "m.blog.naver.com"), "public blog posts about living with the problem",
          _GROUPS, {"ko": "searched"}, rank=4),
    Place("discord", fits={**_DEVELOPERS, "reach": ("communities",)}, roles=_BY_HAND_EVERYWHERE, rank=9),
    Place("nextdoor", fits=_NEIGHBORS, roles={"en": "by_hand"}, rank=10),
    Place("facebook_groups", fits=_GROUPS, roles={"en": "by_hand"}, rank=11),
    Place("kakao_openchat", fits=_GROUPS, roles={"ko": "by_hand"}, rank=12),
    Place("instagram", fits={"buyer": _NOT_DEV, "reach": ("communities", "local", "short_video")},
          roles=_BY_HAND_EVERYWHERE, rank=13),
    Place("x", fits={"reach": ("communities",)}, roles=_BY_HAND_EVERYWHERE, rank=14),
    Place("email", fits={"reach": ("introductions",)}),
    Place("direct", fits={"reach": ("direct",)}),
    Place("other"),
)}
_check_fits(PLACES)
PLATFORMS = tuple(PLACES)


def where(prof: Profile, market: Lang) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The places for this description in this market: the keys the finder searches, then the keys only the owner can
    look at, each in `rank` order. A place is offered only when the description fits it, so one it cannot say yet
    about (`Unsure`) or that does not suit it (`Miss`) is left out."""
    offered = sorted((p for p in PLACES.values()
                      if market in p.roles and isinstance(core.fit(p.fits_in.get(market, p.fits), prof), core.Fit)),
                     key=lambda p: p.rank)
    return (tuple(p.key for p in offered if p.roles[market] == "searched"),
            tuple(p.key for p in offered if p.roles[market] == "by_hand"))


def places(prof: Profile, market: Lang, *, lang: Lang) -> dict[str, list[str]]:
    """For the people tab: where the company looks, where only the owner can, named in `lang`, and every platform with
    this business's own places first. `searched` and `by_hand` are both empty when no place fits the description, which
    is when it does not say yet how the customers are reached or who they are, or says no place reaches them."""
    searched, by_hand = where(prof, market)
    first = (*searched, *by_hand)
    return {"searched": [PLACES[k].names[lang] for k in searched], "by_hand": [PLACES[k].names[lang] for k in by_hand],
            "platforms": [*first, *(k for k in PLATFORMS if k not in first)]}


OWNER = frozenset({"owner"})
TRANSITIONS: dict[tuple[str, str], frozenset[str]] = {
    **{(a, b): OWNER for i, a in enumerate(LADDER) for b in LADDER[i + 1:]},
    **{(a, DROPPED): OWNER for a in LADDER},
    (DROPPED, "found"): OWNER,
}


def stage_label(stage: str, lang: Lang) -> str:
    return msg(f"people.stage.{stage}", lang)


def source_label(source: str, lang: Lang) -> str:
    return msg(f"people.source.{source}", lang)


# Who to answer first: someone who wrote to us, then someone the owner already knows, then people we found.
NEW_ORDER = {"inbound": 0, "warm": 1, "public_problem": 2, "similar_tool": 3, "participation": 4}

MAX_HANDLE, MAX_NAME, MAX_URL, MAX_QUOTE, MAX_WHY, MAX_NEXT = 100, 200, 500, 400, 300, 300
MAX_QUOTES = 20
TODAY_LIMIT = 3


def archived_note(lang: Lang) -> str:
    """What stands in for a forgotten person's words, on their tasks and in the activity log."""
    return msg("people.archived_note", lang)


class PeopleError(owner_tasks.OwnerTaskError):
    """A refusal whose message, in the owner's language, the console shows as is. It is an owner task refusal too,
    because finishing a task about a person can raise it."""


class PersonNotFound(PeopleError, owner_tasks.OwnerTaskNotFound):
    pass


class InvalidQuery(PeopleError, owner_tasks.InvalidQuery):
    pass


def ref(person_id: int) -> str:
    return f"PPL-{person_id}"


# ------------------------------------------------------------------ no contact details

def _words(key: str) -> str:
    return "|".join(f for lang in LANGS for f in CONTACT_WORDS[lang][key])


_AT = rf"(?:@|{_words('at')})"
_DOT = rf"(?:\.|{_words('dot')})"
_EMAIL = re.compile(rf"[\w.%+-]+\s?{_AT}\s?[\w-]+(?:\s?{_DOT}\s?[\w-]+)+", re.IGNORECASE)
# "john at gmail dot com", "john at gmail.com": the bare word `at` needs the dot spelled out or a real top-level name,
# or ordinary sentences ("we stopped at step.sh") would read as addresses.
_TLD = r"(?:com|net|org|io|co|kr|dev|app|ai|me|edu|gov|info|biz|xyz|us|uk|de|jp|cn|fr|ru)\b"
_AT_WORD = re.compile(rf"[\w.%+-]{{2,}}\s+at\s+[\w-]+(?:\s*(?:{_words('dot')}|{_words('dot_word')})\s*[\w-]+|\.{_TLD})+",
                      re.IGNORECASE)
_SEP = r"[\s.-]*"
_PHONE = re.compile(r"(?<![0-9A-Za-z_.])(?:\+\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]\d{3,4}[\s.-]\d{3,4}(?![0-9A-Za-z_])"
                    r"|(?<![0-9A-Za-z_.])\+\d{8,15}(?![0-9A-Za-z_])|(?<![0-9A-Za-z_.])0\d{9,10}(?![0-9A-Za-z_])")
_KR_MOBILE = re.compile(rf"(?<![0-9A-Za-z_.])(?:\+?82{_SEP}(?:\(0\))?{_SEP}|0)1[016-9]{_SEP}\d{{3,4}}{_SEP}\d{{4}}(?!\d)")
_KR_MOBILE_JOINED = re.compile(r"(?<![0-9A-Za-z_.])(?:\+?82)?0?1[016-9]\d{7,8}(?!\d)|(?<![0-9A-Za-z_.])(?:\+?82)?0[2-6]\d{8,9}(?!\d)")
_KR_SPOKEN = re.compile(_words("spoken_phone"))
_MESSENGER = re.compile(rf"(?:{_words('messenger')})\s*(?:{_words('id')})?\s*[:=]\s*@?[\w.-]{{3,}}", re.IGNORECASE)
_NOT_PHONE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?|\b\d{1,3}(?:\.\d{1,3}){3}\b")
_DASHES = re.compile("[‐-―−﹘﹣]")
Contact = Literal["email", "phone", "messenger"]
# What held a contact detail, as `no_contact` names it: `people.field.<key>`.
Where = Literal["entry", "quote", "next_action", "note", "heard"]


def _canon(text: str) -> str:
    """The text as a reader sees it: compatibility forms folded (a full-width `＠` is `@`) and every dash a hyphen."""
    return _DASHES.sub("-", unicodedata.normalize("NFKC", text))


def contact_info_problem(text: str) -> Contact | None:
    """Why a text may not be stored: it holds something that reads as an email address, a phone number or a messenger
    id. Spelled-out forms (`at`, `dot`, Korean `골뱅이`, spoken digits) count, separators and width do not matter."""
    text = _canon(text)
    if _EMAIL.search(text) or _AT_WORD.search(text):
        return "email"
    plain = _NOT_PHONE.sub(" ", text)
    if (_PHONE.search(plain) or _KR_MOBILE.search(plain) or _KR_SPOKEN.search(plain)
            or _KR_MOBILE_JOINED.search(re.sub(r"(?<=\d)[\s.-]+(?=\d)", "", plain))):
        return "phone"
    if _MESSENGER.search(text):
        return "messenger"
    return None


def contact_name(found: Contact, lang: Lang) -> str:
    return msg(f"people.contact.{found}", lang)


def redact(text: str, *, lang: Lang) -> str:
    """The text with contact-like parts replaced, for what the owner typed into a brief. A line that still reads as one
    after that is replaced whole."""
    omitted = msg("people.contact_omitted", lang)
    out = _canon(text)
    for pattern in (_EMAIL, _AT_WORD, _KR_MOBILE, _MESSENGER):
        out = pattern.sub(omitted, out)
    out = _PHONE.sub(lambda m: m.group() if _NOT_PHONE.fullmatch(m.group()) else omitted, out)
    return omitted if contact_info_problem(out.replace(omitted, "")) else out


def no_contact(where: Where, *texts: str | None, lang: Lang) -> None:
    """Refuse a text that holds a contact detail, without repeating it."""
    for text in texts:
        if text and (found := contact_info_problem(text)):
            raise PeopleError(msg("people.contact.refused", lang, field=msg(f"people.field.{where}", lang),
                                  found=contact_name(found, lang)))


# ------------------------------------------------------------------ one new person

def normalize_handle(platform: str, handle: str) -> str:
    """Lower case without the prefix a platform shows (`@`, `u/`), so one person is one row however it was typed."""
    h = " ".join(handle.split())
    for prefix in ("/u/", "u/", "@"):
        if h.lower().startswith(prefix):
            h = h[len(prefix):]
    return h.lower()


@dataclass(frozen=True)
class NewPerson:
    platform: str
    handle: str
    where_found_url: str
    source: str
    display_name: str | None = None
    why: str = ""
    quote: str | None = None
    quote_url: str | None = None
    profile_url: str | None = None
    prep: dict[str, Any] = field(default_factory=dict)


LinkLabel = Literal["page", "profile", "quote"]


def _link(label: LinkLabel, url: str, *, lang: Lang, local: bool = False) -> None:
    if local and url.startswith(("brief:", "owner:")):
        return
    name = msg(f"people.link_label.{label}", lang)
    if len(url) > MAX_URL:
        raise PeopleError(msg("people.link.too_long", lang, label=name, n=MAX_URL))
    if problem := channels.link_problem(None, url):
        raise PeopleError(msg("people.link.invalid", lang, label=name, problem=say(problem, lang)))


def _unknown(what: str, options: tuple[str, ...], lang: Lang) -> str:
    return msg(f"people.unknown.{what}", lang, options=", ".join(options))


def validate(new: NewPerson, *, lang: Lang) -> NewPerson:
    """The person as the list will keep it, or a PeopleError. This is the boundary: a candidate from the model, a
    collector or the owner's form passes here, and nothing past it checks again."""
    if new.platform not in PLATFORMS:
        raise PeopleError(_unknown("platform", PLATFORMS, lang))
    if new.source not in SOURCES:
        raise PeopleError(_unknown("source", SOURCES, lang))
    handle = normalize_handle(new.platform, new.handle)
    if not handle or len(handle) > MAX_HANDLE:
        raise PeopleError(msg("people.handle_length", lang, n=MAX_HANDLE))
    name = " ".join((new.display_name or new.handle).split())[:MAX_NAME]
    why, quote = new.why.strip(), (new.quote or "").strip()
    if len(why) > MAX_WHY or len(quote) > MAX_QUOTE:
        raise PeopleError(msg("people.text_length", lang, why=MAX_WHY, quote=MAX_QUOTE))
    no_contact("entry", handle, name, why, quote, new.where_found_url, *_prep_texts(new.prep), lang=lang)
    for url in (new.where_found_url, new.quote_url, new.profile_url):
        if url and _EMAIL.search(url):
            raise PeopleError(msg("people.contact.in_link", lang))
    _link("page", new.where_found_url, lang=lang, local=True)
    for label, url in (("profile", new.profile_url), ("quote", new.quote_url)):
        if url:
            _link(label, url, lang=lang)
    return NewPerson(new.platform, handle, new.where_found_url, new.source, name, why, quote or None,
                     new.quote_url or new.where_found_url, new.profile_url, new.prep)


def _prep_texts(prep: dict[str, Any]) -> list[str]:
    return [t for key in ("questions", "talking_points") for t in prep.get(key, [])]


def _quote(text: str, url: str) -> dict[str, str]:
    return {"text": text, "url": url, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _business(s: Session, business_id: int, *, lang: Lang) -> Business:
    if (b := s.get(Business, business_id)) is None:
        raise PersonNotFound(msg("people.business_not_found", lang, id=business_id))
    return b


def insert(s: Session, business_id: int, new: NewPerson, *, lang: Lang,
           actor: str = "system") -> tuple[Person | None, str]:
    """Add a person, or say why not. `created`, or `duplicate` (the person is on the list already; a quote from a new
    link is added to them), or `archived` (they asked to be forgotten, and are never added again)."""
    _business(s, business_id, lang=lang)
    new = validate(new, lang=lang)
    found = s.scalar(select(Person).where(
        Person.business_id == business_id,
        or_((Person.platform == new.platform) & (Person.handle == new.handle),
            Person.where_found_url == new.where_found_url)).with_for_update().limit(1))
    if found is not None:
        if found.archived_at is not None:
            return None, "archived"
        if new.quote and all(q["url"] != new.quote_url for q in found.quotes) and len(found.quotes) < MAX_QUOTES:
            found.quotes = [*found.quotes, _quote(new.quote, new.quote_url)]
        return found, "duplicate"
    person = Person(business_id=business_id, handle=new.handle, display_name=new.display_name, platform=new.platform,
                    profile_url=new.profile_url, where_found_url=new.where_found_url, source=new.source,
                    why=new.why, stage="found", quotes=[_quote(new.quote, new.quote_url)] if new.quote else [],
                    prep=new.prep)
    try:
        with s.begin_nested():
            s.add(person)
    except IntegrityError:
        return insert(s, business_id, new, lang=lang, actor=actor)
    activity.record(s, verb="people_found", entity="person", entity_id=person.id, project_id=None,
                    business_id=business_id, actor=actor, summary=f"{ref(person.id)} {person.platform} {person.handle}",
                    data={"ref": ref(person.id), "source": person.source, "platform": person.platform})
    return person, "created"


def add_inbound(s: Session, business_id: int, platform: str, handle: str, url: str, quote: str | None = None,
                *, lang: Lang, display_name: str | None = None) -> Person | None:
    """Someone who came to us (a GitHub issue, a comment, a signup) goes on the list as `inbound`, for the owner to
    answer first. Collectors call this. Pass the person's public handle or name, never an email address: one that
    looks like an address, or a quote that holds one, raises PeopleError. Returns None for a person who asked to be
    forgotten."""
    person, _ = insert(s, business_id, NewPerson(platform, handle, url, "inbound", display_name,
                                                 why=msg("people.why.inbound", lang), quote=quote),
                       lang=lang, actor="system")
    return person


# ------------------------------------------------------------------ reading

def load(s: Session, person_id: int, *, lang: Lang, lock: bool = False) -> Person:
    q = select(Person).where(Person.id == person_id)
    person = s.scalar(q.with_for_update() if lock else q)
    if person is None:
        raise PersonNotFound(msg("people.not_found", lang, ref=ref(person_id)))
    return person


def next_stages(person: Person) -> list[str]:
    return [to for (frm, to) in TRANSITIONS if frm == person.stage] if person.archived_at is None else []


def view(person: Person, tasks: list[dict] | None = None, *, lang: Lang) -> dict:
    """The person as the console reads them. `stage_ko` and `source_ko` keep their names and hold the labels in
    `lang`."""
    row = {c.name: getattr(person, c.name) for c in person.__table__.columns}
    heard = tracking_count.row_key(person.heard_via, person.source)   # what the "how did they hear" pick shows as it stands
    return {**row, "ref": ref(person.id), "stage_ko": stage_label(person.stage, lang),
            "source_ko": source_label(person.source, lang), "next_stages": next_stages(person), "open_tasks": tasks or [],
            "heard_row": heard, "heard_row_label": tracking.label(heard, lang)}


def _open_tasks(s: Session, person_ids: list[int], lang: Lang) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for t in s.scalars(select(OwnerTask).where(OwnerTask.status == owner_tasks.OPEN,
                                               OwnerTask.card["person_id"].astext.in_([str(i) for i in person_ids]))
                       .order_by(OwnerTask.id)):
        out.setdefault(int(t.card["person_id"]), []).append({"ref": owner_tasks.display_id(t.id), "id": t.id,
                                                             "kind": t.kind,
                                                             "kind_ko": msg(f"owner_task.kind.{t.kind}", lang)})
    return out


def get(s: Session, person_id: int, *, lang: Lang) -> dict:
    person = load(s, person_id, lang=lang)
    return view(person, _open_tasks(s, [person.id], lang).get(person.id), lang=lang)


def counts(s: Session, business_id: int) -> dict:
    live = [Person.business_id == business_id, Person.archived_at.is_(None)]
    by_stage = dict(s.execute(select(Person.stage, func.count()).where(*live).group_by(Person.stage)).all())
    by_source = dict(s.execute(select(Person.source, func.count()).where(*live).group_by(Person.source)).all())
    return {"by_stage": {st: by_stage.get(st, 0) for st in STAGES}, "by_source": {so: by_source.get(so, 0) for so in SOURCES},
            "total": sum(by_stage.values())}


def north_star(s: Session, business_id: int) -> dict[str, int]:
    """How many people reached a stage or went beyond it. `first_value` counts those who got value or came back,
    `repeat` those who came back, `talked` those the owner has spoken with. A person who dropped no longer counts:
    the number errs low, and it is the one the first-100 judgment reads."""
    by_stage = counts(s, business_id)["by_stage"]
    return {stage: sum(by_stage[st] for st in LADDER[LADDER.index(stage):]) for stage in LADDER[1:]}


def listing(s: Session, business_id: int, *, lang: Lang, stage: str | None = None, source: str | None = None,
            include_archived: bool = False) -> dict:
    _business(s, business_id, lang=lang)
    if stage is not None and stage not in STAGES:
        raise InvalidQuery(_unknown("stage", STAGES, lang))
    if source is not None and source not in SOURCES:
        raise InvalidQuery(_unknown("source", SOURCES, lang))
    q = select(Person).where(Person.business_id == business_id)
    if not include_archived:
        q = q.where(Person.archived_at.is_(None))
    if stage:
        q = q.where(Person.stage == stage)
    if source:
        q = q.where(Person.source == source)
    rows = list(s.scalars(q.order_by(Person.id.desc())))
    tasks = _open_tasks(s, [p.id for p in rows], lang)
    return {"items": [view(p, tasks.get(p.id), lang=lang) for p in rows], **counts(s, business_id),
            "north_star": north_star(s, business_id),
            "places": places(profile.of(s, business_id), audience.market_of_business(s, business_id), lang=lang),
            "heard_choices": tracking.choices(audience.market_of_business(s, business_id), lang)}


def today(s: Session, business_id: int, *, lang: Lang, now: datetime | None = None,
          limit: int = TODAY_LIMIT) -> list[dict]:
    """Up to `limit` people the owner should reach today: those whose next step is due, the longest overdue first, then
    people not yet contacted, inbound first. A dropped or archived person never appears."""
    _business(s, business_id, lang=lang)
    now = now or datetime.now(timezone.utc)
    live = [Person.business_id == business_id, Person.archived_at.is_(None), Person.stage != DROPPED]
    due = list(s.scalars(select(Person).where(*live, Person.next_action_at <= now)
                         .order_by(Person.next_action_at, Person.id).limit(limit)))
    fresh = [p for p in s.scalars(select(Person).where(*live, Person.stage == "found", Person.next_action_at.is_(None)))]
    fresh.sort(key=lambda p: (NEW_ORDER[p.source], p.id))
    picked = [(p, "overdue") for p in due] + [(p, "new") for p in fresh[:max(0, limit - len(due))]]
    tasks = _open_tasks(s, [p.id for p, _ in picked], lang)
    return [{**view(p, tasks.get(p.id), lang=lang), "reason": why} for p, why in picked]


def today_line(s: Session, lang: Lang) -> str | None:
    """The daily Slack briefing's line: how many people the owner should reach today, across the active businesses.
    Names stay out of Slack; the console lists them."""
    n = sum(len(today(s, bid, lang=lang)) for bid in s.scalars(select(Business.id).where(Business.status == "ACTIVE")))
    return msg("notify.briefing.people_today", lang, n=n) if n else None


# ------------------------------------------------------------------ what the owner does to a person

def _move(person: Person, to: str, by: str, lang: Lang) -> None:
    allowed = TRANSITIONS.get((person.stage, to))
    if allowed is None:
        raise PeopleError(msg("people.move.refused", lang, frm=stage_label(person.stage, lang), to=stage_label(to, lang)))
    if by not in allowed:
        raise PeopleError(msg("people.move.owner_only", lang))
    person.stage = to


def _live(person: Person, lang: Lang) -> Person:
    if person.archived_at is not None:
        raise PeopleError(msg("people.removed", lang))
    return person


def move(s: Session, person_id: int, to: str, *, lang: Lang, by: str = "owner", person: Person | None = None) -> Person:
    """The owner's stage move. Dropping someone ends their follow-up date."""
    if to not in STAGES:
        raise PeopleError(_unknown("stage", STAGES, lang))
    person = _live(person or load(s, person_id, lang=lang, lock=True), lang)
    before = person.stage
    _move(person, to, by, lang)
    if to == DROPPED:
        person.next_action, person.next_action_at = None, None
    activity.record(s, verb="person_moved", entity="person", entity_id=person.id, project_id=None,
                    business_id=person.business_id, actor=by,
                    summary=f"{ref(person.id)} {person.handle}: {stage_label(before, lang)} → {stage_label(to, lang)}",
                    data={"ref": ref(person.id), "from": before, "to": to})
    return person


def set_next(s: Session, person_id: int, *, next_action: str | None, next_action_at: datetime | None, lang: Lang,
             person: Person | None = None) -> Person:
    person = _live(person or load(s, person_id, lang=lang, lock=True), lang)
    text = check_next(next_action or "", lang=lang)
    if next_action_at is not None and next_action_at.tzinfo is None:
        next_action_at = next_action_at.replace(tzinfo=timezone.utc)
    person.next_action, person.next_action_at = text, next_action_at
    return person


def check_quote(text: str, *, lang: Lang) -> str:
    text = text.strip()
    if not text or len(text) > MAX_QUOTE:
        raise PeopleError(msg("people.quote_length", lang, n=MAX_QUOTE))
    no_contact("quote", text, lang=lang)
    return text


def check_next(text: str, *, lang: Lang) -> str | None:
    text = text.strip()
    if len(text) > MAX_NEXT:
        raise PeopleError(msg("people.next_length", lang, n=MAX_NEXT))
    no_contact("next_action", text, lang=lang)
    return text or None


def add_quote(s: Session, person_id: int, text: str, url: str | None = None, *, lang: Lang,
              person: Person | None = None) -> Person:
    """What the person said, in the owner's note or a link. Reassigned, not appended in place, so the column saves."""
    person = _live(person or load(s, person_id, lang=lang, lock=True), lang)
    text = check_quote(text, lang=lang)
    if url:
        _link("quote", url, lang=lang)
    if len(person.quotes) >= MAX_QUOTES:
        raise PeopleError(msg("people.quote_limit", lang, n=MAX_QUOTES))
    person.quotes = [*person.quotes, _quote(text, url or person.where_found_url)]
    return person


def create_by_hand(s: Session, business_id: int, *, platform: str, handle: str, display_name: str | None,
                   url: str | None, quote: str | None, why: str | None, lang: Lang, source: str = "warm") -> Person:
    """The owner adds someone. A duplicate is a refusal, so the owner learns the person is on the list already."""
    where = (url or "").strip() or f"owner:{normalize_handle(platform, handle)}"
    person, outcome = insert(s, business_id, NewPerson(platform, handle, where, source, display_name, (why or "").strip(),
                                                       quote=quote), lang=lang, actor="owner")
    if outcome == "archived":
        raise PeopleError(msg("people.add.forgotten", lang))
    if outcome == "duplicate":
        raise PeopleError(msg("people.add.duplicate", lang, ref=ref(person.id)))
    return person


def stub(s: Session, business_id: int, platform: str, handle: str) -> Person | None:
    """The archived row that keeps this person's handle, if they asked to be forgotten."""
    return s.scalar(select(Person).where(Person.business_id == business_id, Person.platform == platform,
                                         Person.handle == normalize_handle(platform, handle),
                                         Person.archived_at.is_not(None)))


def is_forgotten(s: Session, business_id: int, platform: str, handle: str) -> bool:
    return stub(s, business_id, platform, handle) is not None


def _name_words(text: str) -> set[str]:
    return set(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def _after(table: dict[Lang, tuple[str, ...]]) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for lang in LANGS for p in table[lang]))


_AFTER_NAME, _AFTER_SHORT_NAME = _after(AFTER_NAME), _after(AFTER_SHORT_NAME)


def _says(words: set[str], name: set[str]) -> bool:
    """Whether every word of a name is a word of the text, alone or with an honorific or particle after it."""
    def said(w: str) -> bool:
        after = _AFTER_SHORT_NAME if len(w) <= SHORT_NAME else _AFTER_NAME
        return w in words or any(t.startswith(w) and after.fullmatch(t[len(w):]) for t in words)
    return bool(name) and all(said(w) for w in name)


def named(s: Session, business_id: int, text: str) -> list[Person]:
    """The business's people a text names: someone whose whole name, or handle, is words of the text ("Lena" in "Ask
    to Lena: one week of support emails", "Tom" in "Tom (coffee gear)", "김민수" in "김민수님께"). People the owner
    removed are left out."""
    words = _name_words(text)
    return [p for p in s.scalars(select(Person).where(Person.business_id == business_id, Person.archived_at.is_(None))
                                 .order_by(Person.id))
            if any(_says(words, _name_words(n)) for n in (p.display_name, p.handle))]


def tasks_about(s: Session, person: Person) -> list[OwnerTask]:
    """Every task whose card names this person, locked: the cards stamped with their id, and the `participate` cards
    whose asker is this platform and handle, which carry no id until the owner answers."""
    thread = OwnerTask.card["thread"]
    asked = ((OwnerTask.business_id == person.business_id) & (OwnerTask.kind == "participate")
             & (thread["platform"].astext == person.platform) & (func.lower(thread["asker"].astext) == person.handle))
    return list(s.scalars(select(OwnerTask).where(or_(OwnerTask.card["person_id"].astext == str(person.id), asked))
                          .order_by(OwnerTask.id).with_for_update()))


def archive(s: Session, person_id: int, *, by: str = "owner", lang: Lang) -> Person:
    """Forget a person at the owner's request. What identifies them or quotes them goes: the quotes, the name, the
    links, the prepared questions, the cards, results and activity words of every task that names them (found by
    id, and by platform and handle for a thread they asked). The handle stays, as a stub, so no later search lists
    them again. Archiving twice changes nothing. Tasks are locked before the person, the order finishing a task
    takes. Open tasks are skipped as the owner, the only one who may move a task. The texts the First 10 kit wrote for
    them go too (`kit.forget_person`)."""
    tasks = tasks_about(s, load(s, person_id, lang=lang))
    person = load(s, person_id, lang=lang, lock=True)
    if person.archived_at is not None:
        return person
    note = archived_note(lang)
    for task in tasks:
        if task.status == owner_tasks.OPEN:
            owner_tasks.skip(s, task.id, note=note)
        task.title, task.card, task.result = note, {"person_id": person.id, "archived": True}, (
            {"link": None, "numbers": {}, "note": None} if task.result is not None else None)
    ids = [t.id for t in tasks]
    for ev in s.scalars(select(ActivityEvent).where(
            or_((ActivityEvent.entity == "person") & (ActivityEvent.entity_id == person.id),
                (ActivityEvent.entity == "owner_task") & ActivityEvent.entity_id.in_(ids or [0])))):
        ev.summary, ev.data = note, {}
    person.quotes, person.prep = [], {}
    person.display_name, person.profile_url, person.why = person.handle, None, ""
    person.where_found_url = f"archived:{person.id}"
    person.next_action, person.next_action_at = None, None
    person.archived_at = datetime.now(timezone.utc)
    from justshipit.services import kit  # kit reads people, so it is imported here
    kit.forget_person(s, person.id)
    activity.record(s, verb="person_archived", entity="person", entity_id=person.id, project_id=None,
                    business_id=person.business_id, actor=by, summary=f"{ref(person.id)}: {note}",
                    data={"ref": ref(person.id)})
    return person
