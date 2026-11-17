"""Who is working on what right now: one list that Overview's agent states and the dashboard's action cards both read,
so the two never disagree.

Working means working. A task of a paused, failed, finished or archived project is not work in progress, and neither is
a build that waits for the owner's answer or merge: those show where the owner's decisions are, or not at all. The same
goes for anything of a stage the owner stopped, and for a build whose job waits `gated` for a stage to start. While the
owner has stopped the company, or an agent (services/stops.py), only what is still finishing shows, marked `stopping`:
a step whose job a worker runs now. What waits for the resume is not work in progress.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from justshipit import staffing
from justshipit.contracts import owner_text
from justshipit.contracts.handoff import DEV_JOB_SETTLED, DevJobStatus
from justshipit.db.models import CycleStage, DevJob, Job, LLMCallRecord, Project, Spec, Task
from justshipit.i18n import Lang, catalog, msg
from justshipit.services import gate, queue, stops
from justshipit.settings import agents_config

ACTIVE_TASK = ("RUNNING", "REVIEW", "REWORK")
WORKING_PROJECT = ("CREATED", "RUNNING", "WAITING_HUMAN")
# A build in one of these waits on the owner (an answer, a merge), so no agent is working on it.
OWNER_HOLDS_BUILD = (DevJobStatus.CLARIFICATION_REQUIRED.value,)


@dataclass(frozen=True)
class Row:
    agent: str
    kind: Literal["task", "review", "dev", "job"]
    title: str
    project_id: int | None
    project: str | None
    stage_id: int | None
    business_id: int | None
    task_id: int | None = None
    dev_job_id: int | None = None
    job_id: int | None = None
    job_kind: str | None = None
    status: str | None = None
    started_at: datetime | None = None
    spent_usd: float = 0.0
    stopping: bool = False   # the owner stopped this agent; this is the step it finishes first


def job_label(kind: str, lang: Lang) -> str:
    """A queue job's kind as the owner reads it; a kind the catalog does not name is shown as its code."""
    key = f"console.job.{kind}"
    return msg(key, lang) if key in catalog() else kind


def _working(p: Project) -> bool:
    return p.status in WORKING_PROJECT and not (p.context or {}).get("paused")


def rows(s: Session, hidden: set[int]) -> list[Row]:
    """Every agent's work in progress the view shows (`hidden` are the projects it leaves out): tasks being done or
    reviewed, builds an engineer is on, and the running jobs of the agent a gate.POLICY row names."""
    cfg = agents_config()
    out: list[Row] = []
    projects: dict[int, Project] = {}
    stopped = set(s.scalars(select(CycleStage.id).where(CycleStage.status == "stopped")))
    parked = set(s.scalars(select(Job.payload["dev_job_id"].as_integer())
                           .where(Job.status == "gated", Job.kind.in_(queue.DEV_KINDS))))
    held = stops.read(s)
    halted = held.company is not None
    runs, building = _in_flight(s) if halted or held.agents else (set(), set())
    for t, p in s.execute(select(Task, Project).join(Project, Project.id == Task.project_id)
                          .where(Task.status.in_(ACTIVE_TASK)).order_by(Task.id)):
        projects[p.id] = p
        if p.id in hidden or not _working(p) or p.stage_id in stopped:
            continue
        review = t.status == "REVIEW"
        who = (staffing.verifier_of(s, t) or t.owner) if review else t.owner
        stopping = halted or held.holds(who)
        if stops.waiting_on(t, held) is not None or (stopping and p.id not in runs):
            continue
        out.append(Row(who, "review" if review else "task", owner_text.title(t.title), p.id, p.name, p.stage_id,
                       p.business_id, task_id=t.id, status=t.status, started_at=t.started_at, spent_usd=t.spent_usd,
                       stopping=stopping))
    engineers = [k for k, v in cfg.items() if v.get("runtime") == "engineering"]
    settled = [st.value for st in DEV_JOB_SETTLED] + list(OWNER_HOLDS_BUILD)
    for d, spec, p in s.execute(select(DevJob, Spec, Project).join(Spec, Spec.id == DevJob.spec_id)
                                .join(Project, Project.id == Spec.project_id)
                                .where(DevJob.status.notin_(settled)).order_by(DevJob.id)):
        if (p.id in hidden or p.status in ("FAILED", "ARCHIVED") or (p.context or {}).get("paused")
                or p.stage_id in stopped or d.id in parked):
            continue
        for who in engineers:
            stopping = halted or held.holds(who)
            if stopping and d.id not in building:
                continue
            out.append(Row(who, "dev", owner_text.title(spec.title), p.id, p.name, p.stage_id, p.business_id,
                           dev_job_id=d.id, status=d.status, started_at=d.created_at, spent_usd=d.cost_usd,
                           stopping=stopping))
    named = {k: r.agent for k, r in gate.POLICY.items() if r.agent and k not in queue.RUN_KINDS + queue.DEV_KINDS}
    for j in s.scalars(select(Job).where(Job.status == "running", Job.kind.in_(list(named))).order_by(Job.id)):
        pid = (j.payload or {}).get("project_id")
        if pid in hidden or named[j.kind] not in cfg or j.stage_id in stopped:
            continue
        p = projects.get(pid) or (s.get(Project, pid) if pid else None)
        out.append(Row(named[j.kind], "job", j.kind, pid, p.name if p else None, j.stage_id, j.business_id, job_id=j.id,
                       job_kind=j.kind, status=j.status, started_at=j.locked_at,
                       stopping=halted or held.holds(named[j.kind])))
    return _spent_from_ledger(s, out)


def _spent_from_ledger(s: Session, out: list[Row]) -> list[Row]:
    """Each task's and build's spend at list price, from the call ledger the stage card, Agents and Costs add up,
    so a card never shows money those screens do not count."""
    def by(col, ids: set[int]) -> dict[int, float]:
        if not ids:
            return {}
        return {k: float(v or 0.0) for k, v in s.execute(
            select(col, func.sum(LLMCallRecord.cost_usd)).where(col.in_(ids)).group_by(col))}
    tasks = by(LLMCallRecord.task_id, {r.task_id for r in out if r.task_id is not None})
    builds = by(LLMCallRecord.dev_job_id, {r.dev_job_id for r in out if r.dev_job_id is not None})
    return [replace(r, spent_usd=tasks.get(r.task_id, 0.0) if r.task_id is not None
                    else builds.get(r.dev_job_id, 0.0) if r.dev_job_id is not None else r.spent_usd) for r in out]


def _in_flight(s: Session) -> tuple[set[int], set[int]]:
    """The projects whose run and the dev jobs whose build a worker holds now: what a stop lets finish its step."""
    def held(subject: str, kinds: tuple[str, ...]) -> set[int]:
        return set(s.scalars(select(Job.payload[subject].as_integer())
                             .where(Job.status == "running", Job.kind.in_(kinds))))
    return held("project_id", queue.RUN_KINDS), held("dev_job_id", queue.DEV_KINDS)
