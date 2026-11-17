"""Whether a review counts as independent of its author.

A review is independent when a different agent role ran it as its own call with a fresh context. The models
that ran are recorded for the reader and never decide promotion, since an owner may have one model available.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Served:
    """What actually ran a call. `model` is None when the CLI reported none and none was configured."""
    provider: str
    model: str | None

    def __str__(self) -> str:
        return f"{self.provider}/{self.model or 'unknown'}"


@dataclass(frozen=True)
class Independence:
    independent: bool
    label: str


def assess(author_role: str, author_served: Served | None, reviewer_role: str,
           reviewer_served: Served | None) -> Independence:
    """Independent unless the reviewer is the author's own role. The label names both roles and models."""
    who = f"{reviewer_role} ({reviewer_served or 'unknown'}) of {author_role} ({author_served or 'unknown'})"
    if reviewer_role == author_role:
        return Independence(False, f"not independent: review by the author's own role, {who}")
    return Independence(True, f"review by {who}")

