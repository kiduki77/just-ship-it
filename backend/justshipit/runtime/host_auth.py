"""Claude subscription auth for the engineer container, taken from the owner's own Claude Code login.

Only the short-lived access token goes into the container (as an env var, by name). The refresh token
stays on the host, so the container can never rotate it and log the owner's Claude Code out. A long-lived
`claude setup-token` token is never used: a leaked one stays valid for a year. Refreshing
is left to the host CLI: when the access token is too close to expiry for a call, we wait until it
lapses and run a one-turn host `claude -p`, which refreshes the login the normal way.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from justshipit.i18n import Lang, msg
from justshipit.runtime.base import LLMError, SetupRefused, resolve_bin, run_process
from justshipit.settings import settings

log = logging.getLogger(__name__)
_lock = threading.Lock()


def credentials_path() -> Path:
    return Path.home() / ".claude" / ".credentials.json"


def _read() -> tuple[str, float] | None:
    try:
        o = json.loads(credentials_path().read_text(encoding="utf-8"))["claudeAiOauth"]
        return o["accessToken"], o["expiresAt"] / 1000 - time.time()
    except (OSError, ValueError, KeyError, TypeError):
        return None


def login_present() -> bool:
    """Whether the host login file is there and readable. Says nothing of the token itself."""
    return _read() is not None


def status(lang: Lang) -> str:
    """Human-readable auth source for `ship sandbox check` (never includes the token)."""
    cur = _read()
    if cur is None:
        return msg("build.sandbox.login_none", lang, path=credentials_path())
    return msg("build.sandbox.login_ok", lang, n=max(0, int(cur[1] // 60)))


def _refresh_host() -> None:
    bin_ = resolve_bin("claude", settings().claude_bin)
    run_process([bin_, "-p", "--model", "haiku", "--max-turns", "1", "--tools", "", "--strict-mcp-config",
                 "--disable-slash-commands", "--setting-sources", "", "--output-format", "json"],
                "ok", credentials_path().parent, 120)


def access_token(min_valid_s: int) -> str:
    cur = _read()
    if cur is None:
        raise SetupRefused("build.sandbox.login_missing", path=credentials_path())
    token, remaining = cur
    if remaining >= min_valid_s:
        return token
    if remaining > 0:
        # Outside the lock: calls the current token still covers must not wait behind this one.
        log.info("claude access token expires in %ds; waiting to refresh before the engineer call", remaining)
        time.sleep(remaining + 5)
    with _lock:
        cur = _read()
        if cur is None or cur[1] < min_valid_s:
            _refresh_host()
            cur = _read()
        if cur is None or cur[1] < min_valid_s:
            raise LLMError("could not refresh the host Claude login for the engineer sandbox", transient=True)
        return cur[0]
