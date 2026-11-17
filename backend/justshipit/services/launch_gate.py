"""The launch gate: facts that must hold before a card for a one-shot, high-stakes channel is made.

A channel names its gate items in `requires` (`config/channels.yaml`). An owner item (a recorded demo, outsiders who
installed it) is true once the owner confirms it. `first_value_N` is computed from the people list, so the company
cannot claim it. `owner_tasks.build_post_card` refuses a card for a channel whose gate is not met, whoever proposed
it: an agent's request and the weekly promotion pass the same check.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.db.models import LaunchFact
from justshipit.i18n import Lang, msg
from justshipit.services import channels, people_link

Gate = Callable[[channels.Channel], list[str]]

OWNER_GATE_ITEMS: tuple[str, ...] = ("demo", "outside_install_3", "readme_checked")
_COMPUTED = re.compile(r"^first_value_(\d+)$")


def gate_label(item: str, *, lang: Lang) -> str:
    if m := _COMPUTED.match(item):
        return msg("promotion.gate.first_value", lang, n=int(m.group(1)))
    return msg(f"promotion.gate.{item}", lang)


def gate_known(item: str) -> bool:
    return item in OWNER_GATE_ITEMS or bool(_COMPUTED.match(item))


@lru_cache
def checked_registry() -> None:
    bad = {item for c in channels.registry().values() for item in c.requires if not gate_known(item)}
    if bad:
        raise ValueError(f"channels.yaml requires an unknown launch gate item: {sorted(bad)}")


def gate_state(s: Session, business_id: int, items: tuple[str, ...] | list[str]) -> dict[str, bool]:
    """Whether each gate item holds. An owner item is true once the owner confirmed it; `first_value_N` is computed from
    the people list, so the company cannot claim it."""
    confirmed = set(s.scalars(select(LaunchFact.item).where(LaunchFact.business_id == business_id)))
    first_value = None
    out: dict[str, bool] = {}
    for item in items:
        if m := _COMPUTED.match(item):
            if first_value is None:
                first_value = people_link.provider().north_star(s, business_id)["first_value"]
            out[item] = first_value >= int(m.group(1))
        else:
            out[item] = item in confirmed
    return out


def unmet(s: Session, business_id: int, channel: channels.Channel) -> list[str]:
    state = gate_state(s, business_id, channel.requires)
    return [item for item in channel.requires if not state[item]]


def gate_for(s: Session, business_id: int | None) -> Gate | None:
    """The check for one business: a channel in, the gate items it still lacks out. With no business nothing can be
    proven, so there is no check, and a gated channel is refused."""
    if business_id is None:
        return None
    return lambda channel: unmet(s, business_id, channel)
