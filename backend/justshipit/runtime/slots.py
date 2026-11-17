"""Provider concurrency slots shared by every worker process (subscription limits are per account).

Slot i of a provider is the Postgres session advisory lock (provider key, i), held on a connection of its own for
the length of the call. The lock is only ever tried, never waited on, and its connection runs no transaction, so
db/session.py's promise that no app transaction holds a lock across a model call still holds. A crashed holder's
connection dies with it and frees its slot. Falls back to an in-process semaphore when the database is unreachable
or drops while a call waits for a slot.
"""
from __future__ import annotations

import threading
import time
import zlib
from contextlib import contextmanager

import psycopg

from justshipit.runtime import shared_state
from justshipit.services import queue
from justshipit.settings import company_config, settings

_local: dict[str, threading.BoundedSemaphore] = {}
_local_lock = threading.Lock()
_SHARED = "provider slots"
_FALLBACK = ("each worker process now limits provider concurrency on its own (in-process semaphore of "
             "max_concurrency), so several workers together can exceed the account's limit")


def _limit(provider: str) -> int:
    return int(company_config()["providers"].get(provider, {}).get("max_concurrency", 2))


def _key(provider: str) -> int:
    """A stable signed int4 for the provider. The two-int4 lock form never collides with the one-bigint locks of
    services/queue.py and services/matters.py."""
    n = zlib.crc32(b"ship:slot:" + provider.encode())
    return n - (1 << 32) if n >= 1 << 31 else n


def _connect() -> psycopg.Connection | None:
    try:
        return psycopg.connect(settings().checkpoint_dsn, autocommit=True, connect_timeout=3)
    except psycopg.Error as exc:
        shared_state.unavailable(_SHARED, _FALLBACK, exc)
        return None


def _acquire(conn: psycopg.Connection, provider: str) -> bool:
    """Wait for a shared slot. False when the database drops while waiting. A job whose lease is lost stops waiting:
    the wait is unbounded, so it is checked every tick."""
    key, limit = _key(provider), _limit(provider)
    try:
        while not any(conn.execute("SELECT pg_try_advisory_lock(%s, %s)", (key, i)).fetchone()[0]
                      for i in range(limit)):
            queue.check_lease()
            time.sleep(2)
    except psycopg.Error as exc:
        shared_state.unavailable(_SHARED, _FALLBACK, exc)
        return False
    shared_state.available(_SHARED)
    return True


@contextmanager
def provider_slot(provider: str):
    conn = _connect()
    try:
        if conn is not None and _acquire(conn, provider):
            queue.check_lease()
            yield
            return
    finally:
        if conn is not None:
            conn.close()  # frees the slot
    with _local_lock:
        sem = _local.setdefault(provider, threading.BoundedSemaphore(_limit(provider)))
    with sem:
        queue.check_lease()
        yield
