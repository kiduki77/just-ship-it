"""The build items the owner watches: the numbered `## Tasks` list the engineer ends `.ship/plan.md` with, in build order.

The file is the engineer's, so it is untrusted text: the list is read as plain words, never followed or rendered, and
anything past the caps or not shaped like a list is no list at all, never a short or guessed one."""
from __future__ import annotations

import re
import unicodedata

PLAN = ".ship/plan.md"
MAX_BYTES = 200_000
MAX_ITEMS = 60
MAX_TITLE = 140
MAX_DETAIL = 200

_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_TASKS = re.compile(r"^(?:\*\*|__)?tasks\b", re.IGNORECASE)
# "1. x", "1) x", "- [ ] 1. x", "**1.** x", "**1. x**": a top-level item only, as a nested one is a step of its task.
_ITEM = re.compile(r"^ ?(?:[-*+]\s+)?(?:\[[ xX]\]\s+)?(?:\*\*|__)?(\d{1,3})[.)](?:\*\*|__)?\s+(?:\[[ xX]\]\s+)?(.+)$")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_TAG = re.compile(r"<[^>]*>")
_MARKUP = re.compile(r"\*\*|__|~~|`|(?<!\w)[*_]|[*_](?!\w)")
# what follows the task's name: "Upload form - lets a user...", "Upload form: lets a user..."
_DESCRIBED = re.compile(r"\s+[-\u2013\u2014]\s+|:\s+")
# "**Upload form**: a user can send in a file \u00b7 upload.ts, US1.1": the name, what works, then the technical details.
_BOLD = re.compile(r"\*\*(.+?)\*\*(.*)")
_TECHNICAL = " \u00b7 "


def tasks_of(text: str) -> list[dict] | None:
    """The `## Tasks` list as [{"n", "title", "detail"?}] in its written order, each number once. None for a plan with no
    such section, no item in it, or more items than the cap."""
    lines = text.splitlines()
    start = level = None
    for i, line in enumerate(lines):
        if (h := _HEADING.match(line)) and _TASKS.match(h.group(2)):
            start, level = i + 1, len(h.group(1))
            break
    if start is None:
        return None
    items: list[dict] = []
    seen: set[int] = set()
    for line in lines[start:]:
        if (h := _HEADING.match(line)) and len(h.group(1)) <= level:
            break
        m = _ITEM.match(line)
        if not m or (n := int(m.group(1))) in seen or n == 0:
            continue
        title, detail = _named(m.group(2))
        if title:
            seen.add(n)
            items.append({"n": n, "title": title, **({"detail": detail} if detail else {})})
            if len(items) > MAX_ITEMS:
                return None
    return items or None


def _named(raw: str) -> tuple[str, str]:
    """The task's name and what works once it is done, as plain words. Only an item written as the plan prompt asks,
    `**name**: what works · technical details`, has the second: in any other shape the words after the name are the
    engineer's own notes, never shown as what the task does."""
    if bold := _BOLD.fullmatch(raw.strip()):
        name, rest = _plain(bold.group(1)), _plain(bold.group(2))
        said = name.endswith(":") or rest.startswith(":")
        if (name := name.removesuffix(":").strip()) and (said or not rest):
            detail = rest.removeprefix(":").split(_TECHNICAL, 1)[0].strip()
            return _capped(name, MAX_TITLE), _capped(detail, MAX_DETAIL)
    words = _plain(raw)
    return _capped(_DESCRIBED.split(words, maxsplit=1)[0].strip() or words, MAX_TITLE), ""


def _plain(raw: str) -> str:
    """No markup, links as their words, one line."""
    words = _MARKUP.sub("", _TAG.sub("", _LINK.sub(r"\1", raw)))
    words = "".join(c for c in words if unicodedata.category(c) not in ("Cc", "Cf") or c.isspace())
    return " ".join(words.split())


def _capped(words: str, cap: int) -> str:
    return words if len(words) <= cap else words[:cap - 1].rstrip() + "\u2026"
