"""The bet loop's view of the people list.

The list is F11's (`services/people.py`). The loop needs five things from it and nothing else: how many people have
reached each stage, a way to put a newcomer on it, the words people said, how many people used the product twice and
how many of those the owner talked with, and a request for public threads to take part in. Everything goes through
`PeopleProvider`, so the loop is tested without F11 and does not change when F11 lands: `provider()` returns the
F11-backed one when `justshipit.services.people` exists and matches the contract the adapter relies on, and a provider
that holds nobody when it does not exist or does not match (a warning says which).
"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from types import ModuleType
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from justshipit.i18n import Lang

log = logging.getLogger(__name__)

STAGES = ("contacted", "talked", "first_value", "repeat")
CONTACT_KINDS = ("contact", "onboard", "follow_up")


@dataclass(frozen=True)
class Quote:
    person_id: int
    ref: str
    text: str
    url: str | None
    stage: str


@dataclass(frozen=True)
class UsedTwice:
    """People at repeat use, and how many of them the owner has spoken with directly."""
    users: int
    talked: int


class PeopleProvider(Protocol):
    available: bool

    def north_star(self, s: Session, business_id: int) -> dict[str, int]:
        """People who reached each stage or went beyond it: contacted, talked, first_value, repeat."""

    def add_inbound(self, s: Session, business_id: int, platform: str, handle: str, url: str,
                    quote: str | None = None, *, lang: Lang) -> bool:
        """Put someone who came to us on the list. True when they are on it afterwards, new or already there; False
        for a person who asked to be forgotten or whom the list refused."""

    def quotes(self, s: Session, business_id: int, limit: int) -> list[Quote]:
        """What people said, newest first, only from people at first value or beyond."""

    def used_twice(self, s: Session, business_id: int) -> UsedTwice: ...

    def request_participation(self, s: Session, business_id: int) -> bool:
        """Ask F11 for this week's public threads the owner can answer. False when it cannot."""


class NoPeople:
    available = False

    def north_star(self, s: Session, business_id: int) -> dict[str, int]:
        return dict.fromkeys(STAGES, 0)

    def add_inbound(self, s: Session, business_id: int, platform: str, handle: str, url: str,
                    quote: str | None = None, *, lang: Lang) -> bool:
        return False

    def quotes(self, s: Session, business_id: int, limit: int) -> list[Quote]:
        return []

    def used_twice(self, s: Session, business_id: int) -> UsedTwice:
        return UsedTwice(0, 0)

    def request_participation(self, s: Session, business_id: int) -> bool:
        return False


CONTRACT_CALLS = (("north_star", (None, 1), {}),
                  ("add_inbound", (None, 1, "github", "handle", "https://x", "quote"), {"lang": "en"}),
                  ("ref", (1,), {}))
CONTRACT_COLUMNS = (("Person", ("id", "business_id", "archived_at", "stage", "quotes")),
                    ("OwnerTask", ("business_id", "status", "kind", "card")))


def contract_problems(people: ModuleType, models: ModuleType) -> list[str]:
    """Where F11 differs from what the adapter calls: a missing function, a signature the adapter's call does not fit, a
    missing refusal class or a missing column. Empty when it matches."""
    problems = []
    for name, args, kwargs in CONTRACT_CALLS:
        fn = getattr(people, name, None)
        if not callable(fn):
            problems.append(f"people.{name} is missing")
            continue
        try:
            inspect.signature(fn).bind(*args, **kwargs)
        except TypeError:
            problems.append(f"people.{name}{inspect.signature(fn)} does not take the adapter's call")
    refusal = getattr(people, "PeopleError", None)
    if not (isinstance(refusal, type) and issubclass(refusal, Exception)):
        problems.append("people.PeopleError is missing")
    for model, columns in CONTRACT_COLUMNS:
        cls = getattr(models, model, None)
        problems += [f"{model}.{c} is missing" for c in columns if cls is None or not hasattr(cls, c)]
    return problems


@lru_cache
def _f11() -> ModuleType | None:
    """F11's module when it exists and matches the contract, checked once. A mismatch is logged and the loop runs with
    nobody on the list, rather than failing later in the middle of a retro."""
    try:
        from justshipit.db import models
        from justshipit.services import people
    except ImportError:
        return None
    if problems := contract_problems(people, models):
        log.warning("services/people.py does not match what the bet loop calls, so the loop runs without a people list: %s",
                    "; ".join(problems))
        return None
    return people


class F11People:
    """F11's list. Its names are the contract (`CONTRACT_CALLS`, `CONTRACT_COLUMNS`): `people.north_star`,
    `people.add_inbound`, `people.ref`, the `Person` row with `quotes` as `[{text, url, at}]`, and owner tasks of kind
    contact, onboard and follow_up whose card names the `person_id`."""
    available = True

    def __init__(self, people: ModuleType) -> None:
        self.people = people

    def north_star(self, s: Session, business_id: int) -> dict[str, int]:
        found = self.people.north_star(s, business_id)
        return {stage: int(found.get(stage, 0)) for stage in STAGES}

    def add_inbound(self, s: Session, business_id: int, platform: str, handle: str, url: str,
                    quote: str | None = None, *, lang: Lang) -> bool:
        try:
            with s.begin_nested():
                return self.people.add_inbound(s, business_id, platform, handle, url, quote, lang=lang) is not None
        except self.people.PeopleError as exc:
            log.warning("the people list refused %s/%s: %s", platform, handle, exc)
            return False
        except SQLAlchemyError:
            log.exception("the people list could not store %s/%s", platform, handle)
            return False

    def quotes(self, s: Session, business_id: int, limit: int) -> list[Quote]:
        from justshipit.db.models import Person
        rows = s.scalars(select(Person).where(Person.business_id == business_id, Person.archived_at.is_(None),
                                              Person.stage.in_(("first_value", "repeat"))).order_by(Person.id.desc()))
        out: list[Quote] = []
        for person in rows:
            for q in reversed(person.quotes or []):
                out.append(Quote(person.id, self.people.ref(person.id), q["text"], q.get("url"), person.stage))
        return out[:limit]

    def used_twice(self, s: Session, business_id: int) -> UsedTwice:
        from justshipit.db.models import OwnerTask, Person
        repeat = {p for p in s.scalars(select(Person.id).where(
            Person.business_id == business_id, Person.archived_at.is_(None), Person.stage == "repeat"))}
        spoken = {int(pid) for pid in s.scalars(select(OwnerTask.card["person_id"].astext).where(
            OwnerTask.business_id == business_id, OwnerTask.status == "DONE", OwnerTask.kind.in_(CONTACT_KINDS)))
            if pid is not None and str(pid).isdigit()}
        return UsedTwice(len(repeat), len(repeat & spoken))

    def request_participation(self, s: Session, business_id: int) -> bool:
        from justshipit.services import queue
        from justshipit.workers import runner
        if not runner.handles("find_threads"):
            log.warning("no worker handler for find_threads, so this week's public threads were not requested")
            return False
        queue.enqueue(s, "find_threads", {"business_id": business_id},
                      idempotency_key=f"find_threads:{business_id}:{date.today().isoformat()}", max_attempts=2)
        return True


_override: PeopleProvider | None = None


def use(provider: PeopleProvider | None) -> None:
    """Install a provider, or None to go back to the default. Tests use it."""
    global _override
    _override = provider


def provider() -> PeopleProvider:
    if _override is not None:
        return _override
    people = _f11()
    return F11People(people) if people is not None else NoPeople()
