"""Real-time change events: committed row changes are sent with Postgres NOTIFY for the web console.

Hooked into SQLAlchemy session events, so every service and graph node emits events without
explicit calls. Sending is best-effort; the database remains the source of truth. NOTIFY reaches only listeners on
the same database, so a test run on its own database never reaches the owner's console.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, SessionTransaction

CHANNEL = "ship_events"
_NOTIFY_MAX = 8000  # bytes: Postgres refuses a NOTIFY payload this long
_BULKY = ("data", "summary", "title")  # what an oversized event goes without; the console refetches by table anyway
_WATCHED = {"projects", "tasks", "agent_requests", "decisions", "meetings", "specs", "dev_jobs", "skills",
            "artifacts", "action_executions", "kpi_values", "activity_events", "businesses", "bets", "cycles", "jobs",
            "kickoff_sessions", "kickoff_turns", "owner_tasks", "owner_ideas", "people", "idea_reviews", "cycle_stages",
            "cycle_proposals", "agent_messages", "chat_threads"}
log = logging.getLogger(__name__)


def _payload(kind: str, data: dict[str, Any]) -> str:
    body = json.dumps({"kind": kind, **data}, ensure_ascii=False, default=str)
    if len(body.encode(errors="replace")) < _NOTIFY_MAX:
        return body
    lean = {k: v for k, v in data.items() if k not in _BULKY}
    return json.dumps({"kind": kind, **lean}, ensure_ascii=False, default=str)


def publish(engine: Engine, kind: str, rows: list[dict[str, Any]]) -> None:
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            for d in rows:
                c.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": _payload(kind, d)})
    except Exception as exc:  # noqa: BLE001 - best effort, after the commit: never fail work already committed
        log.debug("event publish failed: %s", exc)


def _describe(obj: Any) -> dict[str, Any] | None:
    table = getattr(obj, "__tablename__", None)
    if table not in _WATCHED:
        return None
    d = {"table": table, "id": getattr(obj, "id", None)}
    for attr in ("status", "phase", "project_id", "business_id", "owner", "type", "title", "verb", "entity",
                 "entity_id", "actor", "summary", "data", "session_id"):
        if hasattr(obj, attr):
            d[attr] = getattr(obj, attr)
    return d


@event.listens_for(Session, "after_flush")
def _collect(session: Session, _ctx) -> None:
    pending = session.info.setdefault("ship_events", {})
    for obj in list(session.new) + list(session.dirty):
        if (d := _describe(obj)) and d["id"] is not None:
            pending[(d["table"], d["id"])] = d


@event.listens_for(Session, "after_commit")
def _committed(session: Session) -> None:
    if not session.in_nested_transaction():  # a released savepoint is not a commit yet
        session.info["ship_committed"] = session.info.pop("ship_events", {})


@event.listens_for(Session, "after_transaction_end")
def _emit(session: Session, transaction: SessionTransaction) -> None:
    """Sent here rather than in after_commit: by now the session has given its connection back to the pool, so
    sending never holds two pool connections at once. A transaction that ended without a commit sends nothing."""
    if transaction.parent is not None:
        return
    session.info.pop("ship_events", None)
    if committed := session.info.pop("ship_committed", None):
        publish(session.get_bind().engine, "change", list(committed.values()))
