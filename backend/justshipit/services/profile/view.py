"""The customer description as the console renders it, every word chosen on the server so the console holds no logic."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from justshipit.db.models import Business
from justshipit.i18n import Lang, msg
from justshipit.services import profile as profile_service
from justshipit.services import roadmap
from justshipit.services.control import NotFound
from justshipit.services.profile import infer, store, vocab
from justshipit.services.profile.core import LineView, Value

_FROM_THE_COMPANY = ("backfill:", "plan:")


def _label(v: Value, lang: Lang) -> str | None:
    return msg(f"profile.code.{v.line}.{v.code}", lang) if v.code else None


def _value(v: Value, lang: Lang) -> dict[str, Any]:
    return {"code": v.code, "label": _label(v, lang), "sentence": v.sentence, "provenance": v.provenance,
            "provenance_label": roadmap.provenance_label(v.provenance, lang)}


def _line(view: LineView, prof: profile_service.Profile, stale: bool, since: Any, lang: Lang) -> dict[str, Any]:
    known = [v for v in view.values if v.provenance != "unknown"]
    return {"line": view.line, "label": msg(f"profile.line.{view.line}", lang),
            "sentence": profile_service.compose(view, lang), "values": [_value(v, lang) for v in known],
            "weakest": view.weakest, "weakest_label": roadmap.provenance_label(view.weakest, lang),
            "known": view.known, "asked": view.asked, "guessed": prof.guessed(view.line),
            "confirmable": prof.confirmable(view.line), "stale": stale,
            "stale_since": since if stale else None, "live_ids": sorted(v.row_id for v in view.values),
            "uses": [_label(v, lang) for v in known if v.code]}


def _backfilled(prof: profile_service.Profile, acted: bool) -> bool:
    """Every known value is the company's own reading of the plan, and the owner has not touched the description."""
    known = [v for view in prof.lines.values() for v in view.values if v.provenance != "unknown"]
    return bool(known) and not acted and all(v.written_by == "project_manager" and v.source.startswith(_FROM_THE_COMPANY)
                                             for v in known)


def build(s: Session, business_id: int, *, lang: Lang) -> dict[str, Any]:
    if s.get(Business, business_id) is None:
        raise NotFound(msg("profile.error.no_business", lang, id=business_id))
    prof = profile_service.of(s, business_id)
    stale = profile_service.stale_lines(s, business_id)
    plan = roadmap.approved_brief(s, business_id)
    asked = any(view.asked for view in prof.lines.values())
    return {"business_id": business_id, "state": "ready" if asked else "not_written",
            "reading": infer.reading(s, business_id),
            "backfilled": _backfilled(prof, store.owner_acted(s, business_id)),
            "question": msg("kickoff.profile.question", lang),
            "lines": [_line(prof.lines[line], prof, line in stale, plan.approved_at if plan else None, lang)
                      for line in vocab.LINES],
            "choices": {line: [{"code": code, "label": msg(f"profile.code.{line}.{code}", lang)}
                               for code in vocab.live_codes(line)] for line in vocab.LINES}}
