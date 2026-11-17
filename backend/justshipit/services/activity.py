"""Activity log: one row per meaningful change, recorded by a session hook.

Services and graph nodes change statuses all over the code base; recording here means none of them
has to remember to log. The actor is whoever the current context says (`as_actor`), otherwise it is
inferred from the record (a task's owner starts it, a request's author raises it).
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy import and_, case, event, inspect, select
from sqlalchemy.orm import Session

from justshipit.db.models import (
    ActivityEvent, AgentRequest, Bet, Business, Decision, DevJob, OwnerTask, Person, Project, Spec, Task,
)
from justshipit.contracts import owner_text
from justshipit.services import scope

_actor: ContextVar[str | None] = ContextVar("ship_actor", default=None)

# Artifacts worth a line of their own; task outputs are already covered by the task's status change.
_ARTIFACT_KINDS = {"plan", "report", "minutes", "prd"}


@contextmanager
def as_actor(name: str) -> Iterator[None]:
    """Attribute every change made inside the block to `name` (e.g. "owner" for console actions)."""
    token = _actor.set(name)
    try:
        yield
    finally:
        _actor.reset(token)


def ref(table: str, obj: Any) -> str:
    """Display id of a record, as the console and agents write it."""
    return {
        "projects": lambda o: f"P-{o.id}", "tasks": lambda o: f"T-{o.id}", "agent_requests": lambda o: f"REQ-{o.id}",
        "decisions": lambda o: f"DEC-{o.id}", "meetings": lambda o: f"M-{o.id}", "dev_jobs": lambda o: f"J-{o.id}",
        "specs": lambda o: o.spec_key, "skills": lambda o: f"{o.name} v{o.version}", "artifacts": lambda o: f"A-{o.id}",
        "action_executions": lambda o: f"A-{o.id}", "businesses": lambda o: f"B-{o.id}", "bets": lambda o: f"BET-{o.id}",
        "cycles": lambda o: f"#{o.number}", "business_briefs": lambda o: f"v{o.version}",
    }[table](obj)


_ENTITY = {"projects": "project", "tasks": "task", "agent_requests": "request", "decisions": "decision",
           "meetings": "meeting", "dev_jobs": "dev_job", "specs": "spec", "skills": "skill",
           "artifacts": "artifact", "action_executions": "action", "businesses": "business", "bets": "bet",
           "cycles": "cycle", "business_briefs": "brief"}
TRACKED = {"projects": ("status", "phase"), "tasks": ("status",), "agent_requests": ("status",),
            "decisions": ("status",), "dev_jobs": ("status",), "specs": ("status",), "skills": ("status",),
            "action_executions": ("status",), "businesses": ("status",), "bets": ("status", "health")}


def _project_id(s: Session, table: str, obj: Any) -> int | None:
    """A dev job names its spec, and an action may name only its request: the project is theirs."""
    if table == "projects":
        return obj.id
    if table == "dev_jobs":
        return scope.owner_of(s, {"spec_id": obj.spec_id})[0]
    if table == "action_executions" and obj.project_id is None:
        return scope.owner_of(s, {"request_id": obj.request_id})[0]
    return getattr(obj, "project_id", None)


def project_column():
    """An event's project as the console scopes it. Dev job events written before _project_id read the spec have
    neither a project nor a business, so their spec's project is read here; nothing rewrites the stored rows."""
    spec_project = (select(Spec.project_id).join(DevJob, DevJob.spec_id == Spec.id)
                    .where(DevJob.id == ActivityEvent.entity_id).scalar_subquery())
    return case((and_(ActivityEvent.project_id.is_(None), ActivityEvent.entity == "dev_job"), spec_project),
                else_=ActivityEvent.project_id)


def _business_of(s: Session, project_id: int | None) -> int | None:
    p = s.get(Project, project_id) if project_id else None
    return p.business_id if p else None


def _business_id(s: Session, table: str, obj: Any) -> int | None:
    if table == "businesses":
        return obj.id
    if (bid := getattr(obj, "business_id", None)) is not None:
        return bid
    return _business_of(s, _project_id(s, table, obj))


def _title(table: str, obj: Any) -> str | None:
    if table == "meetings":
        return obj.agenda[:200]
    if table == "action_executions":
        return obj.action_kind
    if table in ("cycles", "business_briefs"):
        return None
    return getattr(obj, "title", None) or getattr(obj, "name", None)


def _inferred_actor(table: str, obj: Any, verb: str, new: str | None) -> str:
    if table == "tasks":
        if verb == "created":
            return "owner" if obj.origin == "owner" else "project_manager"
        return obj.owner if new in ("RUNNING", "REVIEW", "REWORK") else "system"
    if table == "agent_requests":
        if verb == "created":
            return obj.from_agent
        resp = obj.response or {}
        return resp.get("responded_by") or resp.get("by") or ("policy" if new in ("AUTO_APPROVED", "DENIED_BY_POLICY")
                                                               else "owner")
    if table == "bets" and verb == "created":
        return "project_manager" if obj.origin == "project_manager" else "owner"
    return {"decisions": "project_manager", "meetings": "project_manager", "dev_jobs": "engineer", "specs": "product",
            "skills": "skill_builder", "businesses": "owner", "cycles": "project_manager", "business_briefs": "owner"}.get(table, "system")


_TITLES = {"task": (Task, Task.title), "request": (AgentRequest, AgentRequest.title), "project": (Project, Project.name),
           "decision": (Decision, Decision.title), "bet": (Bet, Bet.title), "owner_task": (OwnerTask, OwnerTask.title),
           "business": (Business, Business.name), "person": (Person, Person.display_name)}


def named(s: Session, events: list[ActivityEvent]) -> list[dict]:
    """Each event's `data`, with the title of the record it is about where the event kept none (rows written before
    the title was stored, or by code that did not store it), so the log names the task, decision or build and never
    only its kind. A record that is gone stays unnamed; the stored data is not changed."""
    wanted: dict[str, set[int]] = {}
    for e in events:
        if e.entity_id is not None and not (e.data or {}).get("title"):
            wanted.setdefault(e.entity, set()).add(e.entity_id)
    found: dict[tuple[str, int], str] = {}
    for entity, ids in wanted.items():
        if entity == "dev_job":
            rows = s.execute(select(DevJob.id, Spec.title).join(Spec, Spec.id == DevJob.spec_id).where(DevJob.id.in_(ids)))
        elif entity in _TITLES:
            model, column = _TITLES[entity]
            rows = s.execute(select(model.id, column).where(model.id.in_(ids)))
        else:
            continue
        found.update({(entity, i): title for i, title in rows if title})
    return [{**(e.data or {}), **({} if (e.data or {}).get("title") or (e.entity, e.entity_id) not in found
                                  else {"title": owner_text.title(found[(e.entity, e.entity_id)], 300)})} for e in events]


def _event(s: Session, table: str, obj: Any, verb: str, field: str | None = None, old: Any = None,
           new: Any = None) -> dict:
    title = _title(table, obj)
    data: dict[str, Any] = {"ref": ref(table, obj)}
    if title:
        data["title"] = title[:300]
    if field:
        data.update({"field": field, "from": old, "to": new})
    if table == "artifacts":
        data["kind"] = obj.kind
    if table == "agent_requests":
        data.update({"type": obj.type, "blocking": bool(obj.blocking)})
        if verb == "created":
            data["status"] = obj.status
    if table == "tasks":
        data["owner"] = obj.owner
        if new == "FAILED" and obj.error:
            data["error"] = obj.error[:300]
    if table == "projects" and new == "FAILED" and obj.error:
        data["error"] = obj.error[:300]
    if table == "action_executions" and new == "done":
        # done means the connector ran, not that anything left: in outbox mode the message only went to the outbox
        data.update({"mode": obj.mode, "outcome": "sent" if obj.mode == "live" else "outbox"})
    change = f" {old or '-'} → {data.get('outcome', new)}" if field else ""
    summary = f"{data['ref']}{' ' + title[:120] if title else ''}: {verb if not field else field}{change}"
    return {"actor": _actor.get() or _inferred_actor(table, obj, verb, new), "verb": verb,
            "entity": _ENTITY[table], "entity_id": obj.id, "project_id": _project_id(s, table, obj),
            "business_id": _business_id(s, table, obj), "summary": summary, "data": data}


def record(s: Session, *, verb: str, entity: str, entity_id: int | None, project_id: int | None, summary: str,
           data: dict | None = None, actor: str | None = None, business_id: int | None = None) -> ActivityEvent:
    """An explicit entry for actions that are not a status change (budget, pause, directive, comment)."""
    ev = ActivityEvent(actor=actor or _actor.get() or "system", verb=verb, entity=entity, entity_id=entity_id,
                       project_id=project_id,
                       business_id=business_id if business_id is not None else _business_of(s, project_id),
                       summary=summary, data=data or {})
    s.add(ev)
    return ev


@event.listens_for(Session, "after_flush")
def _collect(session: Session, _ctx) -> None:
    # Ids are assigned and attribute history is still available here; rows are added after the flush.
    out = session.info.setdefault("ship_activity", [])
    for obj in session.new:
        table = getattr(obj, "__tablename__", None)
        if table not in _ENTITY or (table == "artifacts" and obj.kind not in _ARTIFACT_KINDS):
            continue
        out.append(_event(session, table, obj, "created"))
    for obj in session.dirty:
        table = getattr(obj, "__tablename__", None)
        if table not in TRACKED or obj in session.new:
            continue
        state = inspect(obj)
        for field in TRACKED[table]:
            hist = state.attrs[field].history
            if not hist.added:
                continue
            old = hist.deleted[0] if hist.deleted else None
            new = hist.added[0]
            if old != new:
                verb = "status" if field == "status" else field
                out.append(_event(session, table, obj, verb, field, old, new))


@event.listens_for(Session, "after_flush_postexec")
def _write(session: Session, _ctx) -> None:
    pending = session.info.pop("ship_activity", [])
    for e in pending:
        session.add(ActivityEvent(**e))


@event.listens_for(Session, "after_rollback")
def _drop(session: Session) -> None:
    session.info.pop("ship_activity", None)
