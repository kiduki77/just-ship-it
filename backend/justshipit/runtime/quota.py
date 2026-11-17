"""Subscription quota handling.

When the CLI reports that the account's usage limit is reached, the provider is marked unavailable
until the reset time. Calls then fail until it resets. Every worker process of an install shares var/, so a mark is
one file per provider under var/state/, written whole by a rename: a reader never sees half of one, and two
providers never share a file.
"""
from __future__ import annotations

import os
import re
import time
import uuid
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from justshipit import fileperm
from justshipit.runtime import shared_state
from justshipit.settings import settings

# "You've hit your limit", "You've hit your 5-hour limit", "You've hit your weekly limit", "usage limit reached|<epoch>"
_MARKERS = re.compile(r"usage limit|hit your (?:[\w-]+ )?limit|limit reached|rate_limit_exceeded|quota", re.I)
_local_down: dict[str, float] = {}
_SHARED = "provider quota marks"
_FALLBACK = ("quota marks are kept in this process only, so other workers find a used-up quota by hitting it "
             "themselves")


def is_quota_error(text: str) -> bool:
    return bool(_MARKERS.search(text))


_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
# "resets 3am", "resets at 11pm", "resets 12:20am (Asia/Seoul)", "resets Nov 7, 3am", "resets Nov 4, 8pm (Asia/Seoul)"
_RESET = re.compile(r"resets?\s+(?:at\s+)?(?:(?P<mon>[a-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2}),?\s+(?:at\s+)?)?"
                    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<half>[ap]m)(?:\s*\((?P<zone>[^)]+)\))?", re.I)


def parse_reset(text: str, now: datetime | None = None) -> float:
    """Epoch seconds when the quota resets; one hour from now if the text names no time it can read. A time is read
    in the zone the CLI names after it, else in this machine's zone; a date with no year is the next one to come."""
    if m := re.search(r"\|(\d{10})\b", text):            # Claude: "...limit reached|1761789600"
        return float(m.group(1))
    if m := _RESET.search(text):
        try:
            zone = ZoneInfo(m["zone"].strip()) if m["zone"] else None
        except (ZoneInfoNotFoundError, ValueError):
            zone = None
        now = (now or datetime.now(timezone.utc)).astimezone(zone)
        hour = int(m["hour"]) % 12 + (12 if m["half"].lower() == "pm" else 0)
        month = _MONTHS.index(m["mon"].lower()) + 1 if m["mon"] and m["mon"].lower() in _MONTHS else None
        try:
            at = now.replace(hour=hour, minute=int(m["minute"] or 0), second=0, microsecond=0)
            if month:
                at = at.replace(month=month, day=int(m["day"]))
                if at < now - timedelta(days=180):  # "Jan 2" read on Dec 30
                    at = at.replace(year=at.year + 1)
            elif at <= now:
                at += timedelta(days=1)
            return at.timestamp()
        except ValueError:  # a day the month does not have
            pass
    return time.time() + 3600


def _path(provider: str) -> Path:
    return settings().var_dir / "state" / f"provider_down.{provider}"


def _replace(tmp: Path, path: Path, tries: int = 5) -> None:
    """os.replace, retried briefly: on Windows it fails while another worker has the old mark open for reading."""
    for attempt in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(0.05)


def mark_down(provider: str, until: float) -> None:
    _local_down[provider] = until
    path = _path(provider)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(repr(until), encoding="utf-8")
        fileperm.restrict(tmp)
        _replace(tmp, path)
    except OSError as exc:
        with suppress(OSError):
            tmp.unlink(missing_ok=True)
        shared_state.unavailable(_SHARED, _FALLBACK, exc)
        return
    shared_state.available(_SHARED)


def down_until(provider: str) -> float | None:
    """The later of the shared mark and this process's own, which still counts when the file is missing, unreadable
    or could not be written."""
    try:
        shared = float(_path(provider).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        shared = 0.0
    until = max(shared, _local_down.get(provider, 0.0))
    return until if until > time.time() else None
