"""A project's one state for the owner: the word every screen shows for it (the Projects list and board, the project
header, the task panel), decided here once so no two screens disagree.

`needs_worker` says the company would move the project now if a worker ran. While no worker runs (or the owner
stopped the company) the console shows such a project by the worker's word ("Waiting for the worker") instead of
Running, Not started or Waiting for you, since nothing moves whatever the rows say.
"""
from __future__ import annotations

from typing import Literal

from sqlalchemy.orm import Session

from justshipit.db.models import Project

State = Literal["archived", "paused", "pausing", "stopped", "waiting_you", "not_started", "running", "done"]

LIVE = ("CREATED", "RUNNING", "WAITING_HUMAN")
# A finished run whose build waits on the owner: a merge to decide, or a merged build nobody can use yet.
_BUILD_WAITS = ("DEV_DELIVERED", "MERGED")


def owner_state(status: str, stage: str, *, paused: bool, pending: int) -> tuple[State, bool]:
    """The state and whether it needs a worker to move. `stage` is engineering.status.project_stage of the run, and
    `pending` the project's decisions waiting on the owner. A stop outranks the owner's turn: the stop is what the
    owner fixes first, and its sentence names the decisions too."""
    if status == "ARCHIVED":
        return "archived", False
    if status == "PAUSED":
        return "paused", False
    if paused and status in LIVE:
        return "pausing", False
    if status == "FAILED" or stage == "DEV_STOPPED":
        return "stopped", False
    live = status in LIVE or stage == "DEV_ACTIVE"
    if pending or status == "WAITING_HUMAN" or stage in _BUILD_WAITS:
        return "waiting_you", live
    if status == "CREATED":
        return "not_started", True
    if live:
        return "running", True
    return "done", False


def of(s: Session, p: Project) -> dict:
    """The state fields of one project, for a screen that shows the project without the list's counts."""
    from justshipit.engineering import status as dev_status
    from justshipit.services import requests
    dev = dev_status.by_project(s, [p.id]).get(p.id, dev_status.NO_DEV)
    state, needs = owner_state(p.status, dev_status.project_stage(p.status, dev),
                               paused=bool((p.context or {}).get("paused")), pending=len(requests.pending(s, p.id)))
    return {"owner_state": state, "needs_worker": needs}
