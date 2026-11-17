"""What the readers pick for each business from its customer description, to read in a terminal before trusting it.

For each business that is not archived: the description it has, the weekly channels and people-finder places that
description picks, the one-time launches that wait for a said or confirmed buyer, and the lines nobody knows yet. Codes
are named as they are. A line the description does not know yet is marked, since a place that declares it waits instead
of missing. Read-only. The word-counting classifier this replaced is gone, so there is no old side to compare with; the
comparison was run before the readers switched, and `tests/test_profile_fit.py` keeps what it found."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.db.models import Business
from justshipit.i18n import Lang
from justshipit.services import audience, channels, people
from justshipit.services.profile import core, of
from justshipit.services.profile.core import Fit, Profile, Unsure
from justshipit.services.profile.vocab import LINES, Line, Provenance

# What one line says: each known value (the code, or the sentence in quotes) with how sure the company is of it.
Shown = tuple[tuple[str, Provenance], ...]


@dataclass(frozen=True)
class AuditRow:
    business_id: int
    name: str
    market: Lang
    description: dict[Line, Shown]
    unsure: tuple[Line, ...]
    channels: tuple[str, ...]
    waiting_launches: tuple[str, ...]
    searched: tuple[str, ...]
    by_hand: tuple[str, ...]


def _shown(prof: Profile) -> dict[Line, Shown]:
    return {line: tuple((v.code or f'"{v.sentence}"', v.provenance) for v in prof.lines[line].values
                        if v.provenance != "unknown") for line in LINES}


def _unsure(prof: Profile, market: Lang) -> tuple[Line, ...]:
    """The lines some weekly channel or place of this market declares and the description does not know."""
    declared = [*(c.fits for c in _weekly(market)),
                *(p.fits_in.get(market, p.fits) for p in people.PLACES.values() if market in p.roles)]
    waiting = {line for fits in declared if isinstance(result := core.fit(fits, prof), Unsure) for line in result.lines}
    return tuple(line for line in LINES if line in waiting)


def _weekly(market: Lang) -> list[channels.Channel]:
    return [c for c in channels.for_market(market) if not c.generic and c.weekly]


def _row(s: Session, business: Business) -> AuditRow:
    market = audience.market_of_business(s, business.id)
    prof = of(s, business.id)
    fitting = [c for c in _weekly(market) if isinstance(core.fit(c.fits, prof), Fit)]
    waiting = tuple(c.key for c in fitting if c.requires and not core.launch_ready(c.fits, prof))
    searched, by_hand = people.where(prof, market)
    return AuditRow(business.id, business.name, market, _shown(prof), _unsure(prof, market),
                    tuple(c.key for c in fitting if c.key not in waiting), waiting, searched, by_hand)


def compare(s: Session, business_ids: list[int] | None = None) -> list[AuditRow]:
    """One row per business that is not archived, or per the ids given, in id order."""
    q = select(Business).where(Business.status != "ARCHIVED").order_by(Business.id)
    if business_ids is not None:
        q = q.where(Business.id.in_(business_ids))
    return [_row(s, b) for b in s.scalars(q)]


def _list(keys: tuple[str, ...]) -> str:
    return ", ".join(keys) or "none"


def render(rows: list[AuditRow]) -> str:
    """Plain lines, one block per business: its description, then what the readers pick from it."""
    blocks = []
    for r in rows:
        lines = [f"B-{r.business_id} {r.name} (market {r.market})"]
        for line, values in r.description.items():
            lines.append(f"  {line}: " + ("; ".join(f"{text} ({prov})" for text, prov in values) or "unknown"))
        if r.unsure:
            lines.append(f"  waiting on: {', '.join(r.unsure)} (places that declare it are not picked until it is known)")
        lines.append(f"  channels: {_list(r.channels)}")
        if r.waiting_launches:
            lines.append(f"  one-time launches waiting for a said or confirmed buyer: {_list(r.waiting_launches)}")
        lines.append(f"  searched: {_list(r.searched)}")
        lines.append(f"  by hand: {_list(r.by_hand)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "No business to compare."
