"""The only writer of the customer description. A line is written whole: its live rows are retired and the new set
inserted in one transaction, under a lock on the business row, so two writers never interleave. The caller owns the
commit. Every real change is one activity row holding the codes before and after, which is also what undo reads."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, get_args

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.agents import roles
from justshipit.db.models import ActivityEvent, Business, CustomerProfileAnswer, CustomerProfileValue
from justshipit.i18n import Lang, msg
from justshipit.i18n.said import Refusal, said
from justshipit.services import activity
from justshipit.services.profile import vocab
from justshipit.services.profile.core import Value
from justshipit.services.profile.vocab import Line, Provenance

Writer = Literal["owner", "project_manager"]
Cause = Literal["kickoff_answer", "plan", "plan_changed", "backfill", "owner_right", "owner_not_right",
                "owner_change", "owner_undo"]
CAUSES: tuple[str, ...] = get_args(Cause)
# A write by one of these turned a value down: the inference must not offer it again.
REJECTING: tuple[str, ...] = ("owner_not_right", "owner_change")

MAX_SENTENCE = 400
MAX_SOURCE = 80
MAX_ANSWER = 2000


class ProfileError(Refusal):
    """A refusal the console shows, worded by the route in the owner's language."""


class StaleWrite(ProfileError):
    """The line changed since the caller read it."""


class OwnerValueHeld(ProfileError):
    """The company may not overwrite what the owner said or confirmed."""


@dataclass(frozen=True)
class NewValue:
    """One row to write: a vocabulary code or the line's sentence, never both."""
    code: str | None
    sentence: str | None
    provenance: Provenance
    source: str
    quote: str | None = None


def _key(v: NewValue | Value) -> tuple[str, str, str]:
    """What the owner sees of a value. A later call that only finds the same values again changes nothing."""
    return (v.code or "", v.sentence or "", v.provenance)


def _facts(v: NewValue | Value) -> dict[str, Any]:
    out: dict[str, Any] = {"code": v.code, "sentence": v.sentence, "provenance": v.provenance, "source": v.source,
                           "quote": v.quote}
    if isinstance(v, Value):
        out["brief_id"] = v.brief_id
        out["written_by"] = v.written_by
    return out


def _value(row: CustomerProfileValue) -> Value:
    return Value(row.id, row.line, row.code, row.sentence, row.provenance, row.source,  # type: ignore[arg-type]
                 row.quote, row.brief_id, roles.current(row.written_by), row.written_at)


def _live(s: Session, business_id: int, line: Line | None = None) -> list[CustomerProfileValue]:
    q = select(CustomerProfileValue).where(CustomerProfileValue.business_id == business_id,
                                           CustomerProfileValue.replaced_at.is_(None))
    if line is not None:
        q = q.where(CustomerProfileValue.line == line)
    return list(s.scalars(q.order_by(CustomerProfileValue.id)))


def live_rows(s: Session, business_id: int) -> list[Value]:
    return [_value(r) for r in _live(s, business_id)]


def owner_acted(s: Session, business_id: int) -> bool:
    """Whether the owner ever wrote the description: a right, a not right, a change or an undo."""
    return s.scalar(select(CustomerProfileValue.id).where(CustomerProfileValue.business_id == business_id,
                                                          CustomerProfileValue.written_by == "owner").limit(1)) is not None


def has_answer(s: Session, business_id: int) -> bool:
    """Whether the owner answered the customer question or skipped it, in the kickoff or on the business screen."""
    return s.scalar(select(CustomerProfileAnswer.id).where(CustomerProfileAnswer.business_id == business_id)
                    .limit(1)) is not None


def has_rows(s: Session, business_id: int) -> bool:
    """Whether anything was ever written for the business, live or retired."""
    return s.scalar(select(CustomerProfileValue.id).where(CustomerProfileValue.business_id == business_id)
                    .limit(1)) is not None


def rejected(s: Session, business_id: int) -> list[Value]:
    """Values the owner turned down: retired by a write that said not right or changed the line, and not live again.
    The inference reads them so a later call does not offer them back."""
    rows = list(s.scalars(select(CustomerProfileValue).where(CustomerProfileValue.business_id == business_id)
                          .order_by(CustomerProfileValue.id)))
    live = {(r.line, r.code, r.sentence) for r in rows if r.replaced_at is None}
    turned_down = {(r.line, r.written_at) for r in rows if r.cause in REJECTING}
    return [_value(r) for r in rows
            if r.replaced_at is not None and r.provenance != "unknown"
            and (r.line, r.replaced_at) in turned_down and (r.line, r.code, r.sentence) not in live]


def _checked(line: str, values: Sequence[NewValue], held: set[str | None]) -> None:
    """`held` is the codes the line holds now: a retired one may stay, so a line that holds one can still be edited."""
    if line not in vocab.LINES:
        raise ValueError(f"no such line: {line!r}")
    live_codes = {*vocab.live_codes(line), *(c for c in held if c and vocab.is_code(line, c))}  # type: ignore[arg-type]
    seen: set[str | None] = set()
    for v in values:
        if v.provenance == "unknown" or (v.code is None) == (v.sentence is None):
            raise ProfileError(said("profile.error.bad_value"))
        if v.code is not None and v.code not in live_codes:
            raise ProfileError(said("profile.error.bad_code"))
        empty_sentence = v.sentence is not None and not v.sentence.strip()
        if empty_sentence or len(v.sentence or "") > MAX_SENTENCE or len(v.source) > MAX_SOURCE or v.code in seen:
            raise ProfileError(said("profile.error.bad_value"))
        seen.add(v.code)


def _marker_source(written_by: str, brief_id: int | None) -> str:
    if written_by == "owner":
        return "console"
    return f"plan:{brief_id}" if brief_id is not None else "project_manager"


def _write(s: Session, business_id: int, line: Line, new: list[NewValue], *, cause: str, written_by: str,
           brief_id: int | None, if_live: frozenset[int] | None, call_id: str | None, lang: Lang,
           actor: str | None = None) -> bool:
    """`written_by` is whose values the rows are; `actor` is who made this write when that differs (an undo by the
    owner that puts the company's values back)."""
    actor = actor or written_by
    if cause not in CAUSES:
        raise ValueError(f"no such cause: {cause!r}")
    if s.get(Business, business_id, with_for_update=True) is None:
        raise ValueError(f"no business {business_id}")
    live = _live(s, business_id, line)
    if if_live is not None and if_live != {r.id for r in live}:
        raise StaleWrite(said("profile.error.stale_write"))
    if actor == "project_manager" and any(r.provenance in ("said", "confirmed") for r in live):
        raise OwnerValueHeld(said("profile.error.owner_value_held"))
    before = [_value(r) for r in live]
    # The same values read against a newer plan are still a write: they carry the new plan, so the line is no longer stale.
    restamped = brief_id is not None and any(r.brief_id != brief_id for r in live)
    if sorted(map(_key, before)) == sorted(map(_key, new)) and not restamped:
        return False
    now = datetime.now(UTC)
    for row in live:
        row.replaced_at = now
    s.flush()
    for v in new:
        s.add(CustomerProfileValue(business_id=business_id, line=line, code=v.code, sentence=v.sentence,
                                   provenance=v.provenance, source=v.source, quote=v.quote, brief_id=brief_id,
                                   written_by=written_by, cause=cause, call_id=call_id, written_at=now))
    s.flush()
    activity.record(s, verb="profile", entity="business", entity_id=business_id, business_id=business_id,
                    project_id=None, actor=actor,
                    summary=msg(f"profile.activity.{cause}", lang, line=msg(f"profile.line.{line}", lang)),
                    data={"line": line, "cause": cause, "before": [_facts(v) for v in before],
                          "after": [_facts(v) for v in new]})
    return True


def set_line(s: Session, business_id: int, line: Line, values: Sequence[NewValue], *, cause: Cause,
             written_by: Writer, brief_id: int | None, if_live: frozenset[int] | None, call_id: str | None = None,
             lang: Lang) -> bool:
    """Replace the line's live rows with `values`, or with its unknown marker when `values` is empty. `if_live` is the
    live row ids the caller read: a line that changed since is refused (`StaleWrite`). The company (`project_manager`) is refused a
    line that holds a said or confirmed value (`OwnerValueHeld`). Returns False, writing nothing, when the line
    already says exactly this."""
    _checked(line, values, {r.code for r in _live(s, business_id, line)} if line in vocab.LINES else set())
    new = list(values) or [NewValue(None, None, "unknown", _marker_source(written_by, brief_id))]
    return _write(s, business_id, line, new, cause=cause, written_by=written_by, brief_id=brief_id, if_live=if_live,
                  call_id=call_id, lang=lang)


def undo(s: Session, business_id: int, event_id: int, *, lang: Lang) -> bool:
    """Put the line back as it was before the activity event `event_id` changed it. False when it already is. The
    values come back as whose they were: a value the company read from the plan is not the owner's own words because
    the owner undid a change to it. Events from before the writer was recorded tell it by the source: only the owner's
    writes are from the console."""
    event = s.get(ActivityEvent, event_id)
    if event is None or event.verb != "profile" or event.business_id != business_id:
        raise ProfileError(said("profile.error.not_undoable"))
    before = [NewValue(f["code"], f["sentence"], f["provenance"], f["source"], f["quote"])
              for f in event.data["before"]]
    brief_id = next((f["brief_id"] for f in event.data["before"] if f.get("brief_id") is not None), None)
    facts = event.data["before"]
    writer = next((f["written_by"] for f in facts if f.get("written_by")),
                  "project_manager" if any(f["source"] != "console" for f in facts) else "owner")
    return _write(s, business_id, event.data["line"], before, cause="owner_undo", written_by=writer, actor="owner",
                  brief_id=brief_id, if_live=None, call_id=None, lang=lang)


def add_answer(s: Session, business_id: int, text: str | None, *, asked_in: Literal["kickoff", "business"],
               kickoff_session_id: int | None = None) -> CustomerProfileAnswer:
    """Keep the owner's words whole. Nothing or only spaces is a skipped question, stored as null."""
    text = (text or "").strip() or None
    if text is not None and len(text) > MAX_ANSWER:
        raise ProfileError(said("profile.error.answer_too_long", max=MAX_ANSWER))
    row = CustomerProfileAnswer(business_id=business_id, text=text, asked_in=asked_in,
                                kickoff_session_id=kickoff_session_id)
    s.add(row)
    s.flush()
    return row
