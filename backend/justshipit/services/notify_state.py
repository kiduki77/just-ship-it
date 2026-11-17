"""Small facts about the owner's notices: that Slack refused the bot token, that the owner's connection test passed
with it, and when the last briefing went out (the worker writes them, the console reads them), and which worker outage
the console process already announced. Each is one JSON file under var/state/, next to var/secrets/, written whole by
a rename, so a reader never sees half of one and no two writers share a file."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from justshipit.services import app_settings
from justshipit.settings import settings

ROLLUP_EVERY = timedelta(days=7)


def _path(name: str) -> Path:
    return settings().var_dir / "state" / f"{name}.json"


def _read(name: str) -> dict[str, Any]:
    try:
        data = json.loads(_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(name: str, data: dict[str, Any]) -> None:
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{name}.{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _time(value: Any) -> datetime | None:
    try:
        at = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def slack_refused() -> dict[str, Any] | None:
    """`{"since": datetime, "why": str}` while Slack is known to refuse the bot token, else None."""
    data = _read("slack_auth")
    since = _time(data.get("since"))
    return None if since is None else {"since": since, "why": str(data.get("why", ""))}


def slack_down() -> dict[str, Any] | None:
    """`slack_refused()` while the owner's Slack connector is live: a refused token matters only when Slack is in use."""
    return slack_refused() if app_settings.effective().connectors.slack.mode == "live" else None


def mark_slack_refused(why: str) -> bool:
    """Record the refusal; True when this is the first one, so a caller can tell once. A test that passed before no
    longer counts."""
    _path("slack_test").unlink(missing_ok=True)
    if slack_refused() is not None:
        return False
    _write("slack_auth", {"since": datetime.now(timezone.utc).isoformat(), "why": why})
    return True


def clear_slack_refused() -> bool:
    try:
        _path("slack_auth").unlink()
    except FileNotFoundError:
        return False
    return True


def _fingerprint(token: str) -> str:
    """Which token a test passed with, kept as a short hash so the state file never holds the token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def slack_tested(token: str) -> str | None:
    """The channel the owner's connection test last passed with on this token, or None: a new token needs its own
    test, and so does a pass kept before the channel was."""
    data = _read("slack_test")
    return data.get("channel") if data.get("token") == _fingerprint(token) else None


def mark_slack_tested(token: str, channel: str) -> None:
    _write("slack_test", {"at": datetime.now(UTC).isoformat(), "token": _fingerprint(token),
                          "channel": channel.strip()})


def worker_down_announced() -> datetime | None:
    """The last worker report whose silence the owner was told about: one outage, one message."""
    return _time(_read("worker_down").get("seen_at"))


def mark_worker_down_announced(seen_at: datetime) -> None:
    _write("worker_down", {"seen_at": seen_at.isoformat()})


@dataclass(frozen=True)
class BriefingWindow:
    """What a briefing counts. `since` is when the last one went out: only failures and items newer than that are
    news. `everything` is set for the first briefing and then once a week, which lists what has stayed open."""
    since: datetime | None
    everything: bool

    def fresh(self, at: datetime | None) -> bool:
        return self.everything or at is None or at > self.since


def briefing_window(now: datetime) -> BriefingWindow:
    data = _read("briefing")
    since, rollup = _time(data.get("at")), _time(data.get("rollup_at"))
    return BriefingWindow(since, since is None or rollup is None or now - rollup >= ROLLUP_EVERY)


def briefing_sent(now: datetime, window: BriefingWindow) -> None:
    data = _read("briefing")
    data["at"] = now.isoformat()
    if window.everything:
        data["rollup_at"] = now.isoformat()
    _write("briefing", data)
