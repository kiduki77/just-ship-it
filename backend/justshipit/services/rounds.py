"""How much one planning round may do. A round is the CEO's plan, or one follow-up the CEO's review adds.

Measured on the critic round (2026-11-04): the three projects spent $12-20 of their $40 on round 1 alone, ran 8-10
plan tasks and 2-5 realigned ones in it, and 견적봇 had $2 left when development started. A round now holds at most
`max_tasks_per_round` planned tasks and spends at most `round_budget_share` of what the project leaves for analysis:
its budget, less development's share when the project builds software (`budget.dev_need_usd`). A task the envelope
cannot fit is not started; the CEO's review sees what was left and may plan it again next round. A round never gets
more than earlier rounds left, less what the CEO needs to close the run. A task the owner added or retried is the
owner's call and always runs.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from justshipit.contracts.work import TaskStatus
from justshipit.contracts.work_path import HoldPhase
from justshipit.db.models import LLMCallRecord, Project, Task
from justshipit.i18n import Lang, msg
from justshipit import staffing
from justshipit.services import budget, usage
from justshipit.settings import limits

OWNER = "owner"
# The tasks a round counts against its cap: the ones the CEO planned. A realign answers the owner and the owner's own
# tasks are the owner's; both are held to the envelope only, and the owner's not even to that.
PLANNED = ("ceo_plan", "follow_up", "decision")


def builds(p: Project) -> bool:
    """Whether the owner's first deliverable is software the plan named (`ProjectManagerPlanOut.build`)."""
    return bool((p.plan or {}).get("build"))


def analysis_usd(p: Project) -> float:
    """The budget less development's share, when the project builds and the budget can cover it (`budget.dev_hold`)."""
    dev = budget.dev_need_usd()
    return p.budget_usd - dev if builds(p) and p.budget_usd > dev else p.budget_usd


def max_tasks() -> int:
    return int(limits()["max_tasks_per_round"])


def planned_in(s: Session, p: Project, plan_round: int) -> int:
    return int(s.scalar(select(func.count(Task.id)).where(
        Task.project_id == p.id, Task.plan_round == plan_round, Task.origin.in_(PLANNED))))


@dataclass(frozen=True)
class Envelope:
    limit_usd: float
    spent_usd: float
    per_task_usd: float   # what one more task is expected to cost: this project's mean so far

    def room(self, started: int) -> bool:
        """Whether one more task fits after `started` others were admitted in the same step."""
        return self.spent_usd + self.per_task_usd * started < self.limit_usd


def _closing_usd() -> float:
    """What the run needs after its last round: the CEO's review, its decision and the report."""
    return sum(usage.purpose(x).reserve_usd for x in ("plan", "decision", "report"))


def _audits_usd() -> float:
    cfg = staffing.load_limits()
    return 0.0 if cfg.always_check or not cfg.audit_rate else cfg.audit_budget_usd


def envelope(s: Session, p: Project) -> Envelope:
    """The round's share of the analysis budget, never more than what earlier rounds and the CEO's other calls left
    of it, less what the run needs to close, and never more than the project's budget has left for it."""
    spent = float(s.scalar(select(func.coalesce(func.sum(Task.spent_usd), 0.0)).where(
        Task.project_id == p.id, Task.plan_round == p.plan_round)))
    dev = float(s.scalar(select(func.coalesce(func.sum(LLMCallRecord.cost_usd), 0.0)).where(
        LLMCallRecord.project_id == p.id,
        LLMCallRecord.dev_job_id.is_not(None) | LLMCallRecord.purpose.in_(budget.DEV_PURPOSES))))
    # Shadow checks are paid from their own budget (staffing.audit_budget_usd), kept back whole, so one never shrinks a
    # round.
    elsewhere = p.spent_usd + p.reserved_usd - spent - dev - budget.audit_usd(s, p.id)
    left = analysis_usd(p) - elsewhere - _audits_usd() - _closing_usd()
    # What a call that is not development may still take (`budget.reserve`): checks that cost more than their budget
    # spent the project's money all the same.
    real = spent + p.budget_usd - p.spent_usd - p.reserved_usd - budget.dev_hold(s, p.id) - _closing_usd()
    # The mean leaves out tasks that ran alone: one cheap call each would pull it down and widen the round.
    n, total = s.execute(select(func.count(Task.id), func.coalesce(func.sum(Task.spent_usd), 0.0)).where(
        Task.project_id == p.id, Task.spent_usd > 0, Task.id.notin_(staffing.alone_task_ids()))).one()
    per_task = float(total) / n if n else float(limits()["task_budget_usd"]) / 2
    share = float(limits()["round_budget_share"]) * analysis_usd(p)
    return Envelope(round(max(0.0, min(share, left, real)), 2), spent, per_task)


def _the_owners(t: Task) -> bool:
    """A task the owner added, or one that ran before and is ready again because the owner retried it."""
    return t.origin == OWNER or t.iteration > 0


def admit(s: Session, p: Project, ready: list[Task]) -> tuple[list[Task], list[Task]]:
    """The ready tasks to start now, and the ones the round's envelope leaves out. The owner's run first, then the
    tasks the build waits on, then the rest in plan order."""
    env = envelope(s, p)
    needed = set(((p.plan or {}).get("build") or {}).get("task_ids") or [])
    start, left, planned = [], [], 0
    for t in sorted(ready, key=lambda t: (not _the_owners(t), t.id not in needed, t.id)):
        if _the_owners(t) or env.room(planned):
            start.append(t)
            planned += not _the_owners(t)
        else:
            left.append(t)
    return start, left


def defer(s: Session, p: Project, left: list[Task], *, lang: Lang) -> None:
    """Close what the envelope left out, and the round's planned tasks that would wait on it, so the round ends."""
    from justshipit.services import holds, tasks   # tasks imports this module for the round's task cap
    note = msg("work.task.round_spent", lang, limit=f"{envelope(s, p).limit_usd:g}")
    asked = holds.held(s, p.id, HoldPhase.START)   # a task an open card holds waits for its answer, not the envelope
    for t in left:
        if t.id not in asked:
            tasks.transition(t, TaskStatus.CANCELLED, error=note)
    for t in s.scalars(select(Task).where(Task.project_id == p.id, Task.plan_round == p.plan_round,
                                          Task.status == TaskStatus.PROPOSED.value, Task.origin != OWNER,
                                          Task.iteration == 0)):
        if t.id not in asked:
            tasks.transition(t, TaskStatus.CANCELLED, error=note)
