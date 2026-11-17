"""How much of the work path each decision card records (docs/design/work-path.md 1.6), and the one opt-in source
that can still add to it: the model call that wrote a CEO card."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.agents import roles
from justshipit.contracts.requests import AgentRequestDraft
from justshipit.contracts.work_path import Known, LinkSource, Rel, TargetKind
from justshipit.db.models import AgentRequest, LLMCallRecord, RequestLink
from justshipit.services import requests

UNKNOWN = "unknown"
FIELDS = ("origin", "reason", "holds", "answer use", "expiry rule")
LEVELS = (Known.RECORDED.value, Known.BACKFILLED.value, Known.MATCHED.value, UNKNOWN)
# The exact sources come first: a card with a recorded and a matched origin reads as recorded.
_ORDER = (Known.RECORDED, Known.BACKFILLED, Known.MATCHED)


def _best(links: list[RequestLink]) -> str:
    known = {link.known for link in links}
    return next((k.value for k in _ORDER if k.value in known), UNKNOWN)


def audit(s: Session) -> dict[str, Counter]:
    """For each field, how many cards have it recorded (written when the card was filed), backfilled (read from the
    old row), matched (found by comparing the card to its call) or unknown. A card filed under the work path
    (`context_version` 1) records every field."""
    links: dict[tuple[int, str], list[RequestLink]] = defaultdict(list)
    for link in s.scalars(select(RequestLink)):
        links[(link.request_id, link.rel)].append(link)
    out = {f: Counter({level: 0 for level in LEVELS}) for f in FIELDS}
    for r in s.scalars(select(AgentRequest)):
        filed = r.context_version >= 1
        recorded = Known.RECORDED.value
        out["origin"][recorded if filed else _best(links[(r.id, Rel.RAISED_BY.value)])] += 1
        out["reason"][recorded if r.ask_reason else UNKNOWN] += 1
        out["holds"][recorded if filed else Known.BACKFILLED.value] += 1  # every old card has a scope, from `blocking`
        out["answer use"][recorded if filed else _best(links[(r.id, Rel.ANSWER_USE.value)])] += 1
        out["expiry rule"][recorded if filed else Known.BACKFILLED.value if r.expiry_policy else UNKNOWN] += 1
    return out


_TITLE = TypeAdapter(AgentRequestDraft.model_fields["title"].rebuild_annotation())


@dataclass(frozen=True)
class Match:
    request_id: int
    call_ids: tuple[str, ...]  # the calls whose draft carries the card's title
    linked: bool               # exactly one: the card is linked to it


def _titles(call: LLMCallRecord) -> set[str]:
    """The titles the call's drafts had once the server's title check had run, or none when its output is not there to
    read (a call from before the folder was kept, a moved var/, a file that is not the model's JSON)."""
    if not call.raw_path:
        return set()
    try:
        drafts = json.loads(Path(call.raw_path, "output.json").read_text(encoding="utf-8")).get("requests")
    except (OSError, ValueError, AttributeError):
        return set()
    found = set()
    for d in drafts if isinstance(drafts, list) else []:
        try:
            found.add(_TITLE.validate_python(d["title"]))
        except (KeyError, TypeError, ValidationError):
            continue
    return found


def backfill_from_calls(s: Session, *, dry_run: bool = False) -> list[Match]:
    """Link each CEO card nothing else explains to the one model call that wrote it: by the same agent, in the same
    project, before the card, whose output holds a draft with the card's own title. Two such calls, none, or no readable
    output leave the card unknown. It only adds links, one per card, so running it again writes nothing more. Returns
    every card it looked at."""
    has_origin = {rid for rid in s.scalars(select(RequestLink.request_id).where(
        RequestLink.rel == Rel.RAISED_BY.value))}
    titles: dict[int, set[str]] = {}
    done = []
    for card in s.scalars(select(AgentRequest).where(
            AgentRequest.from_agent.in_(roles.stored_ids(roles.PROJECT_MANAGER)), AgentRequest.context_version == 0, AgentRequest.project_id.is_not(None))
            .order_by(AgentRequest.id)):
        if card.id in has_origin:
            continue
        calls = [c for c in s.scalars(select(LLMCallRecord).where(
            LLMCallRecord.agent == card.from_agent, LLMCallRecord.project_id == card.project_id,
            LLMCallRecord.status == "ok", LLMCallRecord.created_at <= card.created_at).order_by(LLMCallRecord.id))
            if card.title in titles.setdefault(c.id, _titles(c))]
        match = Match(card.id, tuple(c.call_id for c in calls), len(calls) == 1)
        if match.linked and not dry_run:
            s.add(RequestLink(request_id=card.id, rel=Rel.RAISED_BY.value, target_kind=TargetKind.CALL.value,
                              target_id=calls[0].id, revision=requests.revision(card), source=LinkSource.BACKFILL.value,
                              known=Known.MATCHED.value))
        done.append(match)
    return done
