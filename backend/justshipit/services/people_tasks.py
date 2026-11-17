"""The owner tasks about people: contact, onboard, follow up, ask for a referral, and take part in a thread.

Each card gives the owner what to know and what to ask. The owner contacts people from the owner's own account in
the owner's own words: the card has the person, why this person, the questions to ask (about what they did, not what
they think), and a few points. A message the company drafted for someone reaches the owner on its own to-do, as a
request's handout. Everything the model
wrote for a card passes the same checks as an outline post card: short single-sentence points, no ask for votes,
stars or reviews, and no email address or phone number.

Finishing a task about a person takes the stage the meeting reached. That moves the person, adds what they said, and
makes the next follow-up task when the owner gave a date.

A card is stored, so the words the company puts on it (the checklist, the default questions and points, the labels)
are written in the owner's language when the task is made.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from justshipit.db.models import OwnerTask, Person
from justshipit.i18n import LANGS, Lang, msg
from justshipit.i18n.match.instruction import WORDS as INSTRUCTION_WORDS
from justshipit.services import activity, app_settings, channels, owner_tasks, people, profile, roadmap, tracking
from justshipit.services.owner_tasks import CardRefused, OwnerTaskError

log = logging.getLogger(__name__)

PERSON_KINDS = owner_tasks.PERSON_KINDS
REFERRAL_STAGES = ("first_value", "repeat")
PUBLIC_SOURCES = ("public_problem", "similar_tool", "participation")
WARM = "warm"  # a person the owner already knows, from the brief's known-people section
KNOWN_PEOPLE = "known_people"  # the key of the optional known-people section in roadmap.BRIEF_SECTIONS
POINTS_MAX = 3
MAX_OPEN_PARTICIPATE = 5
QUOTES_SHOWN = 3
WEEK_LEFT_MIN = 3  # days of the owner's week, today included, that "this week" needs, or it is next week

# How many default questions and points each kind has in the catalog: `people.ask.<kind>.<n>`, `people.point.<kind>.<n>`.
QUESTIONS = {"contact": 4, "contact_local": 4, "onboard": 3, "follow_up": 3, "referral": 2}
POINTS = {"contact": 2, "contact_warm": 2, "contact_local_warm": 2, "contact_ask": 2, "onboard": 2, "follow_up": 2,
          "referral": 2}


def _lines(prefix: str, kind: str, count: int, lang: Lang) -> list[str]:
    return [msg(f"{prefix}.{kind}.{i}", lang) for i in range(1, count + 1)]


def _check(name: str, lang: Lang) -> str:
    return msg(f"people.check.{name}", lang)


def _kind(kind: str, lang: Lang) -> str:
    return msg(f"owner_task.kind.{kind}", lang)


# ------------------------------------------------------------------ what the model may put on a card

Hostile = Literal["link", "command", "instruction"]

_URL = re.compile(r"https?://|www\.|(?<![a-z0-9-])[a-z0-9-]+\.(?:com|net|org|io|dev|app|ai|co|kr|me|sh|xyz|ly|gg|tv|info)(?![a-z])",
                  re.IGNORECASE)
_SHELL = re.compile(r"[|`]|\$\(|&&|<\s*script|>\s*/|(?<![a-z])(?:curl|wget|bash|zsh|powershell|sudo|chmod|eval|exec|sh)(?![a-z])"
                    r"|(?<![a-z])rm\s+-|(?<![a-z])(?:npm|npx|pip|pipx|brew|apt|apt-get|yum)\s+(?:install|i|run)(?![a-z])",
                    re.IGNORECASE)
_INSTRUCTION = re.compile("|".join(f for lang in LANGS for f in INSTRUCTION_WORDS[lang]), re.IGNORECASE)


def hostile_problem(text: str) -> Hostile | None:
    """Why text a web page's author could have steered may not go on a card, or None: it holds a link, reads as a shell
    command, or reads as an instruction to whoever reads it. The owner sees these cards, and so may an agent later, so
    a card carries what the company wrote about a person, never what a page told it to say."""
    if _URL.search(text):
        return "link"
    if _SHELL.search(text):
        return "command"
    if _INSTRUCTION.search(text):
        return "instruction"
    return None


def clean_points(items: list[str], limit: int) -> tuple[list[str], int]:
    """The model's questions or points that may go on a card, at most `limit`, and how many it wrote that may not:
    one that is not a short single sentence, asks for votes, stars or reviews, holds an email address or a phone number,
    or holds a link, a command or an instruction. A dropped item leaves the rest."""
    kept, dropped = [], 0
    for raw in items:
        item = " ".join(raw.split())
        if (not item or owner_tasks.point_problem(item) or people.contact_info_problem(item)
                or hostile_problem(item) or channels.forbidden_reason([item])):
            dropped += 1
        elif len(kept) < limit:
            kept.append(item)
    return kept, dropped


def check_texts(*texts: str, lang: Lang) -> None:
    """Refuse a card whose text asks for votes, stars or reviews, or holds a contact detail."""
    if why := channels.forbidden_reason(list(texts)):
        raise CardRefused(why, "forbidden_ask")
    for text in texts:
        if found := people.contact_info_problem(text):
            raise CardRefused(msg("people.card.contact", lang, found=people.contact_name(found, lang)), "contact_info")


def check_prose(*texts: str, lang: Lang) -> None:
    """Refuse a card whose own words hold a link, a command or an instruction."""
    for text in texts:
        if found := hostile_problem(text):
            raise CardRefused(msg(f"people.card.hostile.{found}", lang), "hostile_text")


# ------------------------------------------------------------------ cards

def _problem(s: Session, business_id: int) -> str | None:
    brief = roadmap.approved_brief(s, business_id)
    section = next((x for x in (brief.sections if brief else []) if x["key"] == "customer_problem"), None)
    line = next((x for x in (section["lines"] if section else []) if x["provenance"] != "blank"), None)
    return line["text"][:200] if line else None


def person_card(s: Session, person: Person, kind: str, *, lang: Lang) -> dict:
    """What the owner reads before contacting this person. Refuses a referral card that asks for what no channel may.
    A business whose customers are reached in person nearby asks them about the place and when they come by, not about
    a tool, and asks a regular whether their name may stay on its list."""
    prep = person.prep or {}
    warm = person.source == WARM
    local = kind == "contact" and profile.of(s, person.business_id).has("reach", "local")
    ask_kind = "contact_local" if local else kind
    questions = (prep.get("questions") if kind == "contact" else None) or _lines("people.ask", ask_kind,
                                                                                  QUESTIONS[ask_kind], lang)
    point_kind = ("contact_local_warm" if local else "contact_warm") if kind == "contact" and warm else kind
    points = ((prep.get("talking_points") if kind == "contact" else None)
              or _lines("people.point", point_kind, POINTS[point_kind], lang))
    quotes = [{"text": q["text"], "url": q["url"]} for q in person.quotes[-QUOTES_SHOWN:]
              if not channels.forbidden_reason([q["text"]])]
    name = person.handle if channels.forbidden_reason([person.display_name]) else person.display_name
    if person.source == "inbound" and person.heard_via is None and (ask := msg("tracking.heard.question", lang)) not in questions:
        questions = [*questions, ask]   # they came on their own, so nobody has asked them how yet
    check_texts(person.why, *questions, *points, lang=lang)
    check_prose(person.why, *questions, *points, lang=lang)
    return {"person_id": person.id, "kind": kind,
            "person": {"ref": people.ref(person.id), "handle": person.handle, "display_name": name,
                       "platform": person.platform, "source": person.source,
                       "source_ko": people.source_label(person.source, lang),
                       "where_found_url": person.where_found_url, "profile_url": person.profile_url,
                       "stage": person.stage, "stage_ko": people.stage_label(person.stage, lang)},
            "quotes": quotes, "why": person.why, "problem": _problem(s, person.business_id),
            "questions": questions, "talking_points": points,
            "checklist": [_check("own_words", lang),
                          *([_check("disclose_local" if local and warm else "disclose_warm" if warm else "disclose", lang)]
                            if kind == "contact" else []),
                          *([_check("public_first", lang)] if kind == "contact" and person.source in PUBLIC_SOURCES else []),
                          owner_tasks.fixed_check(lang)],
            "disclosed": kind == "contact",
            "next_stages": people.next_stages(person)}


@dataclass(frozen=True)
class Thread:
    platform: str
    url: str
    asker: str
    quote: str
    asks: str
    talking_points: list[str]


def thread_card(thread: Thread, *, lang: Lang) -> dict:
    """A public thread the owner can answer. The outline style: the link, what is asked, a few points the owner could
    share, no prose to post, and the line that says to help without links or promotion. Only the finder makes these
    and it keeps a refusal's rule, not its words, so the refusals are in English."""
    items = [thread.asks, *thread.talking_points]
    for item in items:
        if found := owner_tasks.point_problem(item):
            raise CardRefused(f"a reply card's items must be short points ({found[1]}): '{item[:30]}...'", found[1])
    if not thread.talking_points:
        raise CardRefused("a reply card needs at least one helpful point", "outline_empty")
    check_texts(thread.quote, thread.asker, *items, lang=lang)
    check_prose(*items, lang=lang)
    return {"thread_url": thread.url, "thread": {"platform": thread.platform, "url": thread.url, "asker": thread.asker},
            "quote": thread.quote, "asks": thread.asks, "talking_points": thread.talking_points,
            "checklist": [_check("help_only", lang), owner_tasks.fixed_check(lang)]}


# ------------------------------------------------------------------ making tasks

def _new_task(s: Session, business_id: int, kind: str, title: str, card: dict, *, due_at: datetime | None,
              actor: str) -> OwnerTask | None:
    task = owner_tasks._insert(s, OwnerTask(business_id=business_id, project_id=None, request_id=None, kind=kind,
                                            title=title[:300], card=card, status=owner_tasks.OPEN, due_at=due_at),
                               by_owner=actor == "owner")
    if task is None:
        return None
    activity.record(s, verb="owner_task_created", entity="owner_task", entity_id=task.id, project_id=None,
                    business_id=business_id, actor=actor,
                    summary=f"{owner_tasks.display_id(task.id)} {task.title[:120]}",
                    data={"ref": owner_tasks.display_id(task.id), "kind": kind})
    return task


def _open_task(s: Session, person_id: int, kind: str) -> OwnerTask | None:
    return s.scalar(select(OwnerTask).where(OwnerTask.kind == kind, OwnerTask.status == owner_tasks.OPEN,
                                            OwnerTask.card["person_id"].astext == str(person_id)).limit(1))


def _referral_refusal(person: Person, lang: Lang) -> OwnerTaskError:
    return OwnerTaskError(msg("people.task.referral_stage", lang, stage=people.stage_label(person.stage, lang)))


def create_task(s: Session, person_id: int, kind: str, *, lang: Lang, due_at: datetime | None = None,
                actor: str = "owner") -> OwnerTask | None:
    """The open task of this kind for this person: the one that exists, with its date updated, or a new one. A
    referral is for someone who got value or came back, and the people who dropped or asked to be forgotten get no
    task."""
    if kind not in PERSON_KINDS:
        raise OwnerTaskError(msg("people.task.kind_invalid", lang, kinds=", ".join(_kind(k, lang) for k in PERSON_KINDS)))
    person = people.load(s, person_id, lang=lang, lock=True)
    if person.archived_at is not None:
        raise OwnerTaskError(msg("people.removed", lang))
    if person.stage == people.DROPPED:
        raise OwnerTaskError(msg("people.task.dropped", lang))
    if kind == "referral" and person.stage not in REFERRAL_STAGES:
        raise _referral_refusal(person, lang)
    if (open_one := _open_task(s, person.id, kind)) is not None:
        open_one.due_at = due_at or open_one.due_at
        return open_one
    return _new_task(s, person.business_id, kind, task_title(person, kind, lang),
                     person_card(s, person, kind, lang=lang), due_at=due_at, actor=actor)


def task_title(person: Person, kind: str, lang: Lang) -> str:
    """The to-do's title: the action, said to the owner, with the person's name. Only a first contact with someone
    found in public names the place; the owner reaches someone they know the way they always do."""
    name = person.display_name or person.handle
    if kind != "contact":
        return msg(f"people.task.title.{kind}", lang, name=name)
    if person.source == WARM:
        return msg("people.task.title.contact_warm", lang, name=name)
    place = people.PLACES[person.platform].names[lang] if person.platform in people.PLACES else person.platform
    return msg("people.task.title.contact", lang, name=name, place=place)


def open_participate(s: Session, business_id: int) -> int:
    return s.scalar(select(func.count()).select_from(OwnerTask).where(
        OwnerTask.business_id == business_id, OwnerTask.kind == "participate", OwnerTask.status == owner_tasks.OPEN)) or 0


def create_participate(s: Session, business_id: int, thread: Thread, *, lang: Lang,
                       actor: str = "system") -> OwnerTask | None:
    """The task for a thread, or None when the owner has seen this thread already (open, done or skipped)."""
    if s.scalar(select(OwnerTask.id).where(OwnerTask.business_id == business_id, OwnerTask.kind == "participate",
                                           OwnerTask.card["thread_url"].astext == thread.url).limit(1)):
        return None
    return _new_task(s, business_id, "participate", f"{_kind('participate', lang)}: {thread.asks[:80]}",
                     thread_card(thread, lang=lang), due_at=None, actor=actor)


def _plain_why(text: str) -> bool:
    """Whether the plan line can stand as the card's why: short enough, and nothing a card refuses."""
    return (0 < len(text) <= people.MAX_WHY and not hostile_problem(text) and not people.contact_info_problem(text)
            and not channels.forbidden_reason([text]))


def import_warm(s: Session, business_id: int, *, lang: Lang) -> dict[str, int]:
    """The people the owner already knows, from the approved brief's known-people section, as people with a contact
    task each. Only a line that names one person (`person`, which the kickoff CEO fills) adds someone: a line about a
    group, a channel or nobody is part of the plan, not a person to contact. A brief without the section, or with it
    empty, adds no one and the finder is the start. Email addresses and phone numbers the owner typed are left out of
    the list. Running it again adds only new lines."""
    brief = roadmap.approved_brief(s, business_id)
    made = {"imported": 0, "existing": 0}
    for line in (roadmap.lines_by_key(brief.sections).get(KNOWN_PEOPLE, []) if brief else []):
        if (line["provenance"] not in roadmap.SETTLED
                or not (name := people.redact(line.get("person") or "", lang=lang).strip())):
            continue
        # The name becomes a to-do's title, and the plan runs this, so a name the list refuses ("@", a pasted
        # instruction) skips its line instead of failing the plan.
        if hostile_problem(name) or channels.forbidden_reason([name]):
            log.warning("B-%s: known-people line %s has no name a to-do can carry: %r", business_id, line["id"], name[:80])
            continue
        how = people.redact(line["text"], lang=lang).strip()
        try:
            person, outcome = people.insert(s, business_id, people.NewPerson(
                "direct", name[:people.MAX_HANDLE], f"brief:v{brief.version}:{line['id']}", WARM, name[:people.MAX_NAME],
                why=how if _plain_why(how) else msg("people.why.warm", lang, v=brief.version)),
                lang=lang, actor="system")
        except people.PeopleError as e:
            log.warning("B-%s: known-people line %s was not added: %s", business_id, line["id"], e)
            continue
        if outcome == "created":
            create_task(s, person.id, "contact", lang=lang, actor="system")
            made["imported"] += 1
        else:
            made["existing"] += 1
    return made


def first_talks_due(now: datetime | None = None) -> datetime:
    """When "this week" ends for the owner: the end of Sunday in the owner's time zone, or of the next Sunday when
    fewer than `WEEK_LEFT_MIN` days of the week are left (a plan made on a Saturday or a Sunday)."""
    zone = ZoneInfo(app_settings.effective().usage.timezone)
    today = (now or datetime.now(timezone.utc)).astimezone(zone).date()
    left = 7 - today.weekday()
    sunday = today + timedelta(days=left - 1 + (7 if left < WEEK_LEFT_MIN else 0))
    return datetime.combine(sunday, time(23, 59), zone).astimezone(timezone.utc)


def first_talks(s: Session, business_id: int, stop_rule: str, *, lang: Lang, asks: list[str] | None = None,
                now: datetime | None = None) -> list[OwnerTask]:
    """Round 1 opens with the owner talking to the people the plan names, this week, before any launch: each person the
    owner knows gets a contact to-do due when the owner's week ends (`first_talks_due`), carrying the CEO's rule for
    whether to keep going after. When the plan says what to ask them (`asks`: join the group chat and pre-order), the
    to-do asks that, with points for making an ask, in place of the standard interview that says nothing is for sale.
    A to-do that has a date, a rule or the plan's asks already keeps them, so a later plan never pushes the talks back
    or rewrites what the owner is preparing to ask."""
    import_warm(s, business_id, lang=lang)
    due = first_talks_due(now)
    rule = " ".join(stop_rule.split())
    if rule and (hostile_problem(rule) or people.contact_info_problem(rule)):
        log.warning("B-%s: the plan's stop rule was left off the to-dos: %r", business_id, rule[:80])
        rule = ""
    questions, dropped = clean_points(asks or [], QUESTIONS["contact"])
    if dropped:
        log.warning("B-%s: %d of the plan's asks were left off the to-dos", business_id, dropped)
    found = list(s.scalars(select(OwnerTask).where(
        OwnerTask.business_id == business_id, OwnerTask.kind == "contact", OwnerTask.status == owner_tasks.OPEN,
        OwnerTask.card["person"]["source"].astext == WARM).order_by(OwnerTask.id).with_for_update()))
    for task in found:
        task.due_at = task.due_at or due
        if rule and not task.card.get("stop_rule"):
            task.card = {**task.card, "stop_rule": rule}
        if questions and not task.card.get("plan_asks"):
            task.card = {**task.card, "questions": questions, "plan_asks": True,
                         "talking_points": _lines("people.point", "contact_ask", POINTS["contact_ask"], lang)}
    return found


# ------------------------------------------------------------------ finishing a task

@dataclass(frozen=True)
class Outcome:
    """What the owner reports when a task about a person is done."""
    stage: str | None = None
    quote: str | None = None
    next_action: str | None = None
    next_action_at: datetime | None = None
    # how the person heard about the product (services/tracking.py); both None leaves the answer as it is
    heard_via: str | None = None
    heard_words: str | None = None


@dataclass(frozen=True)
class Plan:
    person: Person | None
    outcome: Outcome


# `owner_tasks.done` and `skip` call the three functions below, in the owner's language.

def check_result(task: OwnerTask, *texts: str | None, lang: Lang) -> None:
    """A note, link or number name finishing or skipping a task about a person is stored with the task and in the
    activity log, so it passes the same contact check as every other field. Other tasks are the owner's own business."""
    if task.kind in owner_tasks.PEOPLE_KINDS:
        people.no_contact("note", *texts, lang=lang)


def _sent_heard(outcome: Outcome) -> Outcome:
    """The outcome with a heard field that came empty or blank taken as not sent, so a form that posts every field leaves
    the earlier answer alone. Clearing it is `PUT /people/{id}/heard`."""
    return replace(outcome, heard_via=(outcome.heard_via or "").strip() or None,
                   heard_words=(outcome.heard_words or "").strip() or None)


def plan_done(s: Session, task: OwnerTask, outcome: Outcome | None, *, lang: Lang) -> Plan | None:
    """Check what the owner reported against the person, before anything changes. Raises OwnerTaskError."""
    if task.status != owner_tasks.OPEN:
        return None
    if task.kind not in PERSON_KINDS:
        if outcome is not None and _sent_heard(outcome) != Outcome():
            raise OwnerTaskError(msg("people.done.no_outcome", lang))
        if task.kind != "participate":
            return None
        thread = task.card.get("thread") or {}
        if thread.get("asker") and people.is_forgotten(s, task.business_id, thread["platform"], thread["asker"]):
            raise OwnerTaskError(msg("people.done.forgotten_asker", lang))
        return Plan(None, Outcome())
    outcome = _sent_heard(outcome or Outcome())
    if outcome.stage is None:
        raise OwnerTaskError(msg("people.done.pick_stage", lang))
    person = people.load(s, int(task.card["person_id"]), lang=lang, lock=True)
    if person.archived_at is not None:
        raise OwnerTaskError(msg("people.done.removed", lang))
    if outcome.stage not in people.STAGES:
        raise OwnerTaskError(msg("people.unknown.stage", lang, options=", ".join(people.STAGES)))
    if outcome.stage != person.stage and (person.stage, outcome.stage) not in people.TRANSITIONS:
        raise OwnerTaskError(msg("people.move.refused", lang, frm=people.stage_label(person.stage, lang),
                                 to=people.stage_label(outcome.stage, lang)))
    if task.kind == "referral" and person.stage not in REFERRAL_STAGES:
        raise _referral_refusal(person, lang)
    if outcome.stage == people.DROPPED and outcome.next_action_at is not None:
        raise OwnerTaskError(msg("people.done.dropped_date", lang))
    if outcome.quote is not None:
        people.check_quote(outcome.quote, lang=lang)
    if outcome.next_action:
        people.check_next(outcome.next_action, lang=lang)
    if outcome.heard_via is not None or outcome.heard_words is not None:
        tracking.clean_heard(s, person.business_id, outcome.heard_via, outcome.heard_words, lang=lang, person=person)
    return Plan(person, outcome)


def apply_done(s: Session, task: OwnerTask, plan: Plan, *, lang: Lang) -> None:
    """Move the person to the stage reached, keep what they said, and make the next follow-up when a date was given. The
    date the finished task stood for ends, so only a new date keeps the person on the list for today."""
    if plan.person is None:
        _thread_asker(s, task, lang)
        return
    person, out = plan.person, plan.outcome
    if out.stage != person.stage:
        people.move(s, person.id, out.stage, lang=lang, person=person)
    if out.quote and out.quote.strip():
        people.add_quote(s, person.id, out.quote, lang=lang, person=person)
    if out.heard_via is not None or out.heard_words is not None:
        tracking.set_heard(s, person.id, out.heard_via, out.heard_words, lang=lang, person=person)
    person.next_action, person.next_action_at = None, None
    if out.next_action_at is not None:
        people.set_next(s, person.id, next_action=out.next_action or _kind("follow_up", lang),
                        next_action_at=out.next_action_at, lang=lang, person=person)
        create_task(s, person.id, "follow_up", lang=lang, due_at=person.next_action_at, actor="owner")


def _thread_asker(s: Session, task: OwnerTask, lang: Lang) -> None:
    """The person who asked in a thread the owner answered joins the list as someone met through taking part, and the
    card is stamped with their id from then on, so archiving them reaches it."""
    thread = task.card.get("thread")
    if not thread or not thread.get("asker"):
        return
    try:
        person, _ = people.insert(s, task.business_id, people.NewPerson(
            thread["platform"], thread["asker"], thread["url"], "participation", why=msg("people.why.asker", lang),
            quote=task.card.get("quote")), lang=lang, actor="owner")
    except people.PeopleError as exc:
        log.warning("%s: the asker was not added: %s", owner_tasks.display_id(task.id), exc)
        return
    if person is not None:
        task.card = {**task.card, "person_id": person.id}
