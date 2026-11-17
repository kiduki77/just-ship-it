"""The customer description's words: three lines and the codes a value may name on each.

Labels live only in the server catalog (`i18n/messages/profile.py`, `profile.code.<line>.<code>`). To grow the
vocabulary add a code here and its two labels there. A code is never renamed or deleted: retire it, so old rows still
render it and the picker and the model's schema stop offering it.
"""
from __future__ import annotations

from typing import Literal, get_args

Line = Literal["buyer", "reach", "has"]
LINES: tuple[Line, ...] = get_args(Line)

# The plan's own provenance words (`roadmap.Provenance`) minus `blank`: a description has no empty value.
Provenance = Literal["said", "confirmed", "guess", "unknown"]

CODES: dict[Line, tuple[str, ...]] = {
    "buyer": ("consumers", "small_business", "professionals", "companies", "developers"),
    "reach": ("search", "communities", "introductions", "local", "direct", "short_video"),
    "has": ("network", "customers", "audience", "registration", "place", "budget", "time", "face"),
}

RETIRED: frozenset[str] = frozenset()


def live_codes(line: Line) -> tuple[str, ...]:
    return tuple(c for c in CODES[line] if c not in RETIRED)


def is_code(line: str, code: str) -> bool:
    """Whether `code` is on `line`, retired or not."""
    return any(name == line and code in codes for name, codes in CODES.items())
