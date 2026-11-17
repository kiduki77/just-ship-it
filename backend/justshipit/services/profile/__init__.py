"""A business's customer description: who buys, how they are reached, and what the founder already has.

Readers call `of` and match with `fit`; nothing else reads the rows. Writers go through `store`. See
docs/design/customer-profile.md."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.db.models import BusinessBrief
from justshipit.i18n import Lang, msg
from justshipit.services.fence import fenced
from justshipit.services.profile import store
from justshipit.services.profile.core import (
    WATCHED_SECTIONS,
    Fit,
    LineView,
    Miss,
    Profile,
    Unsure,
    assemble,
    firm,
    fit,
    launch_ready,
    stale,
)
from justshipit.services.profile.vocab import Line

__all__ = ["Fit", "LineView", "Miss", "Profile", "Unsure", "compose", "firm", "fit", "launch_ready", "of",
           "prompt_block", "stale_lines", "watched_lines"]


def of(s: Session, business_id: int) -> Profile:
    """The live description, read fresh every time."""
    return assemble(business_id, store.live_rows(s, business_id))


def compose(view: LineView, lang: Lang, *, marks: bool = False) -> str:
    """The line as one sentence from its code labels and its own sentence, or "" when nothing is known. With `marks`
    each value carries how sure the company is of it."""
    items = [(msg(f"profile.code.{v.line}.{v.code}", lang) if v.code else v.sentence or "", v.provenance)
             for v in view.values if v.provenance != "unknown"]
    parts = [f"{text} ({msg(f'roadmap.provenance.{p}', lang)})" if marks else text for text, p in items]
    return msg(f"profile.compose.{view.line}", lang, list="; ".join(parts)) if parts else ""


def prompt_block(prof: Profile, lang: Lang) -> str:
    """The description for an agent's prompt, fenced as data. A line nobody knows says so, so the agent does not fill it."""
    lines = [compose(view, lang, marks=True)
             or f"{msg(f'profile.line.{line}', lang)}: {msg('profile.prompt.unknown', lang)}"
             for line, view in prof.lines.items()]
    return fenced(msg("profile.prompt.label", lang), "\n".join(lines))


def watched_lines(sections: list[dict]) -> tuple[str, ...]:
    """The text of a plan's lines that the description is read from, in a fixed order, to compare two plans."""
    from justshipit.services import roadmap

    by_key = roadmap.lines_by_key(sections)
    return tuple(f"{key}:{x['text']}" for key in WATCHED_SECTIONS for x in by_key.get(key, []))


def stale_lines(s: Session, business_id: int) -> frozenset[Line]:
    """The lines written against an earlier plan than the approved one, whose watched lines have changed since."""
    from justshipit.services import roadmap

    current = roadmap.approved_brief(s, business_id)
    if current is None:
        return frozenset()
    prof = of(s, business_id)
    ids = {current.id} | {v.brief_id for view in prof.lines.values() for v in view.values if v.brief_id is not None}
    plans = {b.id: watched_lines(b.sections) for b in s.scalars(select(BusinessBrief).where(BusinessBrief.id.in_(ids)))}
    return stale(prof, plans, current.id)
