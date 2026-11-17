"""Start, resume and retry company runs on a Postgres-checkpointed LangGraph."""
from __future__ import annotations

import atexit
import threading
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from justshipit.db.models import Project
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.orchestration.company_graph import build_graph
from justshipit.services import app_settings, holds
from justshipit.services.budget import BudgetExceeded
from justshipit.settings import limits, settings

_lock = threading.Lock()
_compiled = None


def graph():
    global _compiled
    with _lock:
        if _compiled is None:
            pool = ConnectionPool(settings().checkpoint_dsn, max_size=10, open=True,
                                  kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
            atexit.register(pool.close)
            saver = PostgresSaver(pool)
            saver.setup()
            _compiled = build_graph().compile(checkpointer=saver)
        return _compiled


def _config(project_id: int) -> dict[str, Any]:
    return {"configurable": {"thread_id": f"project-{project_id}"},
            "max_concurrency": int(limits()["max_concurrent_tasks"]), "recursion_limit": 400}


def _invoke(project_id: int, payload: Any) -> dict[str, Any]:
    with session_scope() as s:
        p = s.get(Project, project_id)
        p.thread_id = f"project-{project_id}"
        p.error = None
        if p.status in ("CREATED", "FAILED"):
            p.status = "RUNNING"
    try:
        result = graph().invoke(payload, _config(project_id))
    except Exception as exc:
        with session_scope() as s:
            p = s.get(Project, project_id)
            p.status = "FAILED"
            p.error = _stopped_because(exc)
        raise
    return result


def _stopped_because(exc: Exception) -> str:
    """The reason the console shows for a stopped run. A budget refusal keeps its type name, which the budget-stop
    controls read; any other error is internal, and its text stays in the job's error and the worker log."""
    if isinstance(exc, BudgetExceeded):
        return f"{type(exc).__name__}: {exc}"[:4000]
    return msg("work.run.internal_error", app_settings.language())


def start(project_id: int) -> dict[str, Any]:
    return _invoke(project_id, {"project_id": project_id, "done": []})


def resume(project_id: int) -> dict[str, Any]:
    """Resume a run paused for the owner. Every answer may enqueue a resume, so stale or duplicate ones must not push a
    run past an interrupt that is still waiting: the gate re-runs on resume and its interrupt returns at once, so this
    check, the gate's own (`holds.park_reason`), is what holds it."""
    with session_scope() as s:
        p = s.get(Project, project_id)
        if p.status not in holds.PARKED:
            return {"skipped": f"project is {p.status}"}
        if (why := holds.park_reason(s, project_id)) == holds.ParkReason.PAUSED:
            return {"skipped": "paused by the owner"}
        if why is not None:
            return {"skipped": f"{why} held by blocking request(s) still open"}
    if not snapshot(project_id)["interrupts"]:
        return {"skipped": "run is not paused"}
    return _invoke(project_id, Command(resume={"resumed": True}))


def retry(project_id: int) -> dict[str, Any]:
    """Continue a failed run from its last checkpoint (completed nodes are not re-executed). With no checkpoint
    (lost, or never written) start over: the plan node rebuilds a planned project from the task DB."""
    if graph().checkpointer.get_tuple(_config(project_id)) is None:
        return start(project_id)
    return _invoke(project_id, None)


def snapshot(project_id: int) -> dict[str, Any]:
    st = graph().get_state(_config(project_id))
    return {"next": list(st.next), "interrupts": [i.value for t in st.tasks for i in t.interrupts]}
