"""What the rule reads about a task, taken from the task, its project's plan and the step that started it. The one
place a task becomes `JobFacts`, so `crews.facts` always means the same thing."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from justshipit.contracts.work import TaskStatus
from justshipit.db.models import Project, Task
from justshipit.settings import agents_config
from justshipit.staffing.config import OTHER
from justshipit.staffing.crew import JobFacts

# A dependent in these states no longer waits on anything.
_GONE = (TaskStatus.CANCELLED.value, TaskStatus.REPLACED.value)


def task_facts(s: Session, t: Task, *, alongside: Iterable[int] = ()) -> JobFacts:
    """`alongside` is the batch the scheduling step started this task with; the plan graph gives the rest."""
    plan = s.scalar(select(Project.plan).where(Project.id == t.project_id)) or {}
    dependents = s.scalar(select(func.count(Task.id)).where(
        Task.project_id == t.project_id, Task.depends_on.contains([t.id]), Task.status.notin_(_GONE)))
    return JobFacts(
        owner=t.owner, family=t.family or OTHER, criteria=len(t.acceptance_criteria or []),
        dependents=int(dependents or 0), after=tuple(sorted(t.depends_on or [])),
        alongside=tuple(sorted(set(alongside) - {t.id})),
        feeds_build=t.id in ((plan.get("build") or {}).get("task_ids") or []), origin=t.origin,
        analysed=agents_config().get(t.owner, {}).get("runtime") == "analysis", resumed=_resumed(t))


def _resumed(t: Task) -> Literal["fresh", "in_review", "reworked"]:
    if t.status == TaskStatus.REVIEW.value:
        return "in_review"
    return "reworked" if t.status == TaskStatus.REWORK.value or t.iteration > 0 else "fresh"
