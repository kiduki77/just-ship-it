"""What the owner does to the description: say a line is right, not right, change it, or tell the company more. Each
edit is one `store` write by the owner and never files a decision card, because it can be undone."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.db.models import ActivityEvent, Business
from justshipit.i18n import Lang, msg
from justshipit.i18n.said import said
from justshipit.services import profile as profile_service
from justshipit.services import roadmap
from justshipit.services.control import NotFound
from justshipit.services.profile import infer, store, vocab
from justshipit.services.profile.core import Value
from justshipit.services.profile.store import NewValue, ProfileError
from justshipit.services.profile.vocab import Line

Action = Literal["right", "not_right", "change", "answer"]
ACTIONS: tuple[str, ...] = ("right", "not_right", "change", "answer")


def _last_event(s: Session, business_id: int) -> int | None:
    s.flush()
    return s.scalar(select(ActivityEvent.id).where(ActivityEvent.verb == "profile", ActivityEvent.entity == "business",
                                                   ActivityEvent.entity_id == business_id)
                    .order_by(ActivityEvent.id.desc()).limit(1))


def _confirmed(s: Session, business_id: int, line: Line) -> list[NewValue]:
    """The line's known values as the owner's: a guess, or a value the CEO read as said, becomes confirmed (keeping its
    quote); what the owner already confirmed or typed stays."""
    def mine(v) -> bool:
        return v.provenance == "confirmed" or (v.provenance == "said" and v.written_by == "owner")
    return [NewValue(v.code, v.sentence, v.provenance if mine(v) else "confirmed", v.source if mine(v) else "console",
                     v.quote)
            for v in profile_service.of(s, business_id).lines[line].values if v.provenance != "unknown"]


def _picked(codes: Sequence[str], sentence: str | None, live: Sequence[Value]) -> list[NewValue]:
    """The owner's picks as confirmed values, and a typed sentence as their own words. A sentence left as the editor
    showed it is not newly typed: the CEO's guess becomes confirmed, and the owner's own keeps its quote."""
    values = [NewValue(code, None, "confirmed", "console") for code in dict.fromkeys(codes)]
    if sentence is not None and sentence.strip():
        text = sentence.strip()
        kept = next((v for v in live if v.sentence and infer.same_words(v.sentence, text)), None)
        if kept is None:
            values.append(NewValue(None, text, "said", "console", text))
        elif kept.provenance == "said" and kept.written_by == "owner":
            values.append(NewValue(None, kept.sentence, "said", kept.source, kept.quote))
        else:
            values.append(NewValue(None, kept.sentence, "confirmed", "console", kept.quote))
    if not values:
        raise ProfileError(said("profile.error.nothing_picked"))
    return values


def apply(s: Session, business_id: int, line: str, action: str, *, if_live: Sequence[int], codes: Sequence[str] = (),
          sentence: str | None = None, text: str | None = None, lang: Lang) -> int | None:
    """Do one edit and return the activity event that records it, or None when nothing changed (a line already
    right, or already not known). `if_live` is the live row ids the owner saw: a line that changed since is refused."""
    if s.get(Business, business_id) is None:
        raise NotFound(msg("profile.error.no_business", lang, id=business_id))
    if line not in vocab.LINES:
        raise ProfileError(said("profile.error.bad_line"))
    if action not in ACTIONS:
        raise ProfileError(said("profile.error.bad_action"))
    plan = roadmap.approved_brief(s, business_id)
    brief_id = plan.id if plan else None
    seen = frozenset(if_live)

    def write(values: list[NewValue], cause: store.Cause) -> int | None:
        changed = store.set_line(s, business_id, line, values, cause=cause, written_by="owner",  # type: ignore[arg-type]
                                 brief_id=brief_id, if_live=seen, lang=lang)
        return _last_event(s, business_id) if changed else None

    if action == "right":
        prof = profile_service.of(s, business_id)
        if seen != {v.row_id for v in prof.lines[line].values}:  # type: ignore[index]
            raise store.StaleWrite(said("profile.error.stale_write"))   # also when there is nothing left to confirm
        if not prof.confirmable(line) and line not in profile_service.stale_lines(s, business_id):  # type: ignore[arg-type]
            return None
        return write(_confirmed(s, business_id, line), "owner_right")  # type: ignore[arg-type]
    if action == "not_right":
        return write([], "owner_not_right")
    if action == "change":
        live = [v for v in profile_service.of(s, business_id).lines[line].values]  # type: ignore[index]
        return write(_picked(codes, sentence, live), "owner_change")
    row = store.add_answer(s, business_id, text, asked_in="business")
    if row.text is None:
        raise ProfileError(said("profile.error.no_answer"))
    infer.enqueue_for_answer(s, business_id, row.id)
    return None
