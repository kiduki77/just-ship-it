"""Whether the state workers share (provider slots in Postgres, quota marks under var/) is shared, logged once per
transition.

Without it each worker falls back to state of its own, which changes how the company behaves; a log line per call
would bury that, no log line would hide it.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)
_down: set[str] = set()
_lock = threading.Lock()


def unavailable(shared: str, fallback: str, exc: Exception) -> None:
    with _lock:
        if shared in _down:
            return
        _down.add(shared)
    log.warning("Cannot share %s (%s: %s): %s", shared, type(exc).__name__, exc, fallback)


def available(shared: str) -> None:
    with _lock:
        if shared not in _down:
            return
        _down.discard(shared)
    log.info("Sharing again: %s are shared across workers again", shared)
