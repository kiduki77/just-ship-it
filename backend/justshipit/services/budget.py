"""Budget control with reservation before a call and settlement after it.

Concurrent agents make "check then spend" unsafe; reserving under row locks guarantees the caps.
Amounts are list-price USD equivalents reported by the CLIs (subscriptions are not billed per call).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, true
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

from justshipit.contracts.handoff import DEV_JOB_DELIVERED, SpecStatus
from justshipit.contracts.plan import PlanStatus
from justshipit.contracts.requests import RequestStatus, RequestType
from justshipit.db.models import (
    AgentRequest, BudgetMonth, BudgetReservation, DevJob, Job, LLMCallRecord, Plan, Project, Spec, Task,
)
from justshipit.db.session import session_scope
from justshipit.i18n import DEFAULT, Lang, msg
from justshipit.services import app_settings, matters, queue, usage
from justshipit.settings import company_config, limits
from justshipit.staffing import AUDIT, load_limits


class BudgetExceeded(RuntimeError):
    """`scope` names the cap that refused: month, project or task."""

    def __init__(self, message: str, scope: str | None = None) -> None:
        super().__init__(message)
        self.scope = scope


# The calls that build what the owner approved: the spec, the engineer, the code review and the CPO's acceptance.
# A call with a dev job is one too, whatever its purpose.
DEV_PURPOSES = frozenset({"product_plan", "spec", "dev", "acceptance"})
_DEV_OPEN = (RequestStatus.PENDING.value, RequestStatus.APPROVED.value, RequestStatus.AUTO_APPROVED.value)


def dev_need_usd() -> float:
    return float(company_config()["engineering"].get("dev_budget_usd", 12))


def dev_hold(s: Session, project_id: int) -> float:
    """What the project's other calls must leave free for development: what the development under way still needs,
    dev_need_usd less what it has spent, while one is wanted, else nothing."""
    since = _dev_wanted_since(s, project_id)
    if since is None:
        return 0.0
    spent = float(s.scalar(select(func.coalesce(func.sum(LLMCallRecord.cost_usd), 0.0)).where(
        LLMCallRecord.project_id == project_id, LLMCallRecord.created_at >= since,
        LLMCallRecord.dev_job_id.is_not(None) | LLMCallRecord.purpose.in_(DEV_PURPOSES))))
    return max(0.0, dev_need_usd() - spent)


def _dev_wanted_since(s: Session, project_id: int) -> datetime | None:
    """When the development the project still wants was asked for, or None when it wants none. It is wanted while
    the project's current spec waits for the owner or is approved with no branch delivered, and before a spec, while
    a development request newer than that spec waits for the owner or has its spec being written. A spec the owner
    rejected, merged or never got a card for again, and a spec writing that ended without a spec, want nothing."""
    specs = list(s.scalars(select(Spec).where(Spec.project_id == project_id).order_by(Spec.id)))
    spec = specs[-1] if specs else None
    # A rejected later spec leaves the approved one being built current, and its build keeps its share.
    if (current := matters.current_spec(s, specs)) is not None:
        card = s.get(AgentRequest, current.approval_request_id) if current.approval_request_id else None
        delivered = s.scalar(select(DevJob.id).where(
            DevJob.spec_id == current.id, DevJob.status.in_([st.value for st in DEV_JOB_DELIVERED])).limit(1))
        # A spec waiting on the owner's answers to its questions has no card yet (engineering/questions.py).
        asking = card is None and current.status == SpecStatus.CLARIFICATION_REQUIRED.value \
            and not current.approved_commit_sha
        if ((asking or (card is not None and card.status in _DEV_OPEN)) and current.status != SpecStatus.MERGED.value
                and delivered is None):
            asked = s.get(AgentRequest, current.request_id) if current.request_id else None
            return asked.created_at if asked is not None else current.created_at
    ask = s.scalar(select(AgentRequest).where(
        AgentRequest.project_id == project_id, AgentRequest.type == RequestType.DEVELOPMENT.value,
        AgentRequest.status.in_(_DEV_OPEN), AgentRequest.id > ((spec.request_id or 0) if spec else 0))
        .order_by(AgentRequest.id.desc()).limit(1))
    if ask is None:
        # The plan named software as the owner's first deliverable and the run has not asked for it yet: its share is
        # kept from the start, so the analysis before the ask cannot spend it (견적봇 spent $37.97 of $40 first).
        p = s.get(Project, project_id)
        build = (p.plan or {}).get("build") if p is not None and spec is None else None
        # A budget that cannot cover development keeps nothing back: holding it would stop the run, not fund the build.
        fundable = p is not None and p.budget_usd > dev_need_usd()
        # The plan the build began keeps the share until the owner declines it.
        plan = s.get(Plan, build["plan_id"]) if build and build.get("plan_id") else None
        declined = plan is not None and plan.status == PlanStatus.REJECTED.value
        if build and not build.get("request_id") and fundable and not declined:
            return p.created_at
        from justshipit.engineering import plans  # plans imports what imports the budget
        # A plan an agent's ask started keeps the share on its way to a spec, as the ask it took the place of did.
        live = plans.live(s, project_id)
        return live.created_at if live is not None and live.project_id == project_id else None
    if ask.status == RequestStatus.PENDING.value or s.scalar(select(Job.id).where(
            Job.idempotency_key == f"write_spec:{ask.id}", queue.in_flight())) is not None:
        return ask.created_at
    return None


def dev_shortfall(s: Session, project_id: int, need_usd: float | None = None) -> dict | None:
    """How much the project's budget lacks for one development (or for `need_usd`, a fix round's share), for a
    warning before the owner approves one, or None when what is left covers it."""
    p = s.get(Project, project_id)
    left = p.budget_usd - p.spent_usd - p.reserved_usd
    need = dev_need_usd() if need_usd is None else need_usd
    if left >= need:
        return None
    return {"need_usd": need, "left_usd": round(max(0.0, left), 2), "short_usd": round(need - max(0.0, left), 2),
            "budget_usd": p.budget_usd, "spent_usd": round(p.spent_usd, 2)}


# A refusal as `reserve` words it, read back from the error a job, task or run stored.
_REFUSED = re.compile(r"\b(project|monthly) budget [\d.]+ USD-eq would be exceeded")
# Room a retried job needs before its first call: the largest hold of a call that is not development.
RETRY_ROOM_USD = max(p.reserve_usd for name, p in usage.PURPOSES.items() if name not in DEV_PURPOSES)


def refused_by(error: str | None) -> str | None:
    """The cap whose refusal stopped the work, "project" or "month", from its stored error; None when no budget did."""
    m = _REFUSED.search(error or "")
    return None if m is None else "month" if m.group(1) == "monthly" else "project"


def still_short(s: Session, cap: str, project_id: int | None) -> bool:
    """Whether `cap` still lacks the room one more call holds, so work it refused would be refused again."""
    if cap == "month":
        month = s.get(BudgetMonth, _month())
        return month is not None and month.spent_usd + month.reserved_usd + RETRY_ROOM_USD > month.limit_usd
    p = s.get(Project, project_id) if project_id is not None else None
    return p is not None and p.spent_usd + p.reserved_usd + RETRY_ROOM_USD + dev_hold(s, p.id) > p.budget_usd


def retry_blocked(s: Session, p: Project | None) -> bool:
    """Whether running a failed project again would only be refused again, because its budget lacks the room one more
    call holds. The console then offers the budget before 'Run again', whatever error the run stored."""
    return p is not None and p.status == "FAILED" and still_short(s, "project", p.id)


def stop_line(s: Session, cap: str, project_id: int | None, lead: str | None = None, *, lang: Lang = DEFAULT) -> str:
    """The owner's sentence for work a budget stopped: `lead` says what stopped, then what the budget is and the
    owner's way on, as it is now."""
    if not still_short(s, cap, project_id):
        return msg("budget.room_now", lang)
    lead = lead or msg("budget.stopped", lang)
    if cap == "month":
        return msg("budget.month", lang, lead=lead)
    p = s.get(Project, project_id)
    return msg("budget.project", lang, lead=lead, budget=f"${p.budget_usd:g}", used=f"${p.spent_usd + p.reserved_usd:.2f}")


@dataclass(frozen=True)
class Reservation:
    id: int
    amount: float


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _previous_month() -> str:
    year, month = map(int, _month().split("-"))
    return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"


def _live(s: Session) -> float:
    """Reservations held now. A call that started in the last minutes of a month settles into the next month's row,
    so the previous month's row still counts."""
    return float(s.scalar(select(func.coalesce(func.sum(BudgetMonth.reserved_usd), 0.0)).where(
        BudgetMonth.month.in_([_month(), _previous_month()]))))


TASK_OVERRUN = 1.5   # how far past its budget a task may run before its next call is refused


def task_cap(t: Task) -> float:
    return t.budget_usd * TASK_OVERRUN


def audit_usd(s: Session, project_id: int) -> float:
    """What the project's shadow checks spent and hold now (design section 7). They run as `audit_task` jobs, so a
    hold is known by its job."""
    spent = s.scalar(select(func.coalesce(func.sum(LLMCallRecord.cost_usd), 0.0)).where(
        LLMCallRecord.project_id == project_id, LLMCallRecord.purpose == AUDIT))
    held = s.scalar(select(func.coalesce(func.sum(BudgetReservation.amount_usd), 0.0))
                    .join(Job, Job.id == BudgetReservation.job_id)
                    .where(BudgetReservation.project_id == project_id, Job.kind == "audit_task"))
    return float(spent) + float(held)


def reserve(project_id: int | None, task_id: int | None, purpose: str, *, dev: bool = False) -> Reservation:
    """Hold the call's conservative cost against the budgets and the usage caps. A budget refusal is final
    (BudgetExceeded); a usage cap refusal is MustWait, the work resumes when the window rolls over. A call that is
    not development (`dev`) also leaves the project's dev_hold free. A shadow check (`AUDIT`) also fits the project's
    audit budget; the caller names no task for it, since it is never charged to the task it checks."""
    amount = usage.purpose(purpose).reserve_usd
    eff = app_settings.effective()
    cfg = eff.usage
    with session_scope() as s:
        s.execute(insert(BudgetMonth).values(
            month=_month(), limit_usd=float(limits()["monthly_budget_usd"]), spent_usd=0.0, reserved_usd=0.0,
        ).on_conflict_do_nothing())
        month = s.get(BudgetMonth, _month(), with_for_update=True)
        if month.spent_usd + month.reserved_usd + amount > month.limit_usd:
            raise BudgetExceeded(f"monthly budget {month.limit_usd} USD-eq would be exceeded", "month")
        if project_id is not None:
            p = s.scalar(select(Project).where(Project.id == project_id).with_for_update())
            held = 0.0 if dev else dev_hold(s, project_id)
            if p.spent_usd + p.reserved_usd + amount + held > p.budget_usd:
                kept = f" ({held:g} USD-eq of it is kept for development)" if held else ""
                raise BudgetExceeded(f"project budget {p.budget_usd} USD-eq would be exceeded{kept}", "project")
            if purpose == AUDIT:
                audit_cap = load_limits().audit_budget_usd
                if audit_usd(s, project_id) + amount > audit_cap:
                    raise BudgetExceeded(f"audit budget {audit_cap} USD-eq would be exceeded", "audit")
            p.reserved_usd += amount
        if task_id is not None:
            t = s.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if t.spent_usd + amount > task_cap(t):
                raise BudgetExceeded(f"task budget {t.budget_usd} USD-eq exhausted", "task")
        usage.check(s, cfg, purpose, _live(s), lang=eff.general.language)
        month.reserved_usd += amount
        lease = queue.current_lease.get()
        row = BudgetReservation(month=month.month, project_id=project_id, amount_usd=amount,
                                job_id=lease.job_id if lease else None, lease_token=lease.token if lease else None)
        s.add(row)
        s.flush()
        return Reservation(row.id, amount)


def settle(project_id: int | None, task_id: int | None, held: Reservation, actual: float,
           record: LLMCallRecord | None = None) -> None:
    """Release the reservation and book the actual cost. The call's ledger row goes in the same transaction, so no
    other call ever sees it as neither reserved nor spent. A reservation the orphan release already gave back (its
    holder woke after its job was reclaimed) is not taken off again: that would release another call's."""
    with session_scope(lease_checked=False) as s:  # the money was spent whoever owns the job now
        reserved_in = s.scalar(delete(BudgetReservation).where(BudgetReservation.id == held.id)
                               .returning(BudgetReservation.month))
        if reserved_in is not None and (m := s.get(BudgetMonth, reserved_in, with_for_update=True)):
            m.reserved_usd = max(0.0, m.reserved_usd - held.amount)
        if month := s.get(BudgetMonth, _month(), with_for_update=True):
            month.spent_usd += actual
        if project_id is not None:
            p = s.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if reserved_in is not None:
                p.reserved_usd = max(0.0, p.reserved_usd - held.amount)
            p.spent_usd += actual
            if record is not None:
                record.business_id = p.business_id
        if task_id is not None:
            t = s.scalar(select(Task).where(Task.id == task_id).with_for_update())
            t.spent_usd += actual
        # Last: its insert locks the project and task rows it references, so it must come after the month lock, the
        # order reserve takes them in.
        if record is not None:
            s.add(record)


def release_orphaned_reservations() -> float:
    """Reservations live only in the calling process; a worker killed mid-call leaks them. One taken under a job
    lease is released once that lease is gone (reclaimed, finished, handed back), whatever else runs. One taken
    outside any job, and any amount counted before reservations had rows, only when no job holds a live lease."""
    with session_scope() as s:
        idle = not queue.live_lease_owners(s)  # takes the claim lock, before the budget rows (take_claim_lock)
        held = select(Job.id).where(Job.id == BudgetReservation.job_id, Job.status == "running",
                                    Job.lease_token == BudgetReservation.lease_token).exists()
        orphans = s.scalars(select(BudgetReservation).where(
            true() if idle else (BudgetReservation.job_id.is_not(None) & ~held))
            .order_by(BudgetReservation.id).with_for_update()).all()
        if not idle and not orphans:
            return 0.0
        by_month: dict[str, float] = defaultdict(float)
        by_project: dict[int, float] = defaultdict(float)
        for r in orphans:
            by_month[r.month] += r.amount_usd
            if r.project_id is not None:
                by_project[r.project_id] += r.amount_usd
            s.delete(r)
        released = 0.0
        months = select(BudgetMonth).where(BudgetMonth.reserved_usd > 0 if idle
                                           else BudgetMonth.month.in_(list(by_month)))
        for month in s.scalars(months.order_by(BudgetMonth.month).with_for_update()):
            gone = month.reserved_usd if idle else min(month.reserved_usd, by_month[month.month])
            month.reserved_usd -= gone
            released += gone
        projects = select(Project).where(Project.reserved_usd > 0 if idle else Project.id.in_(list(by_project)))
        for p in s.scalars(projects.order_by(Project.id).with_for_update()):
            p.reserved_usd = 0.0 if idle else max(0.0, p.reserved_usd - by_project[p.id])
        return released


def gate(purpose: str, amount: float | None = None, step: str | None = None) -> None:
    """MustWait if a call of this purpose would be refused now. For a job that must not start at all while the
    window is closed, since it cannot be resumed once it has changed state. `amount` is what the whole job
    would hold, when it is a `step` that makes more than one call."""
    eff = app_settings.effective()
    with session_scope(lease_checked=False) as s:
        usage.check(s, eff.usage, purpose, _live(s), amount=amount, step=step, lang=eff.general.language)


def gate_step(name: str, units: int = 1) -> None:
    """MustWait unless the whole step, of `units` units (see usage.STEPS), fits under the caps now."""
    step = usage.STEPS[name]
    gate(step.purpose, step.usd(units), name)


def usage_view() -> dict:
    """Today and this week against the owner's caps, for the console."""
    cfg = app_settings.effective().usage
    w = usage.windows(datetime.now(timezone.utc), cfg.timezone)
    with session_scope(lease_checked=False) as s:
        live = _live(s)
        day, week = usage.spent(s, w)
    return {"subscription": cfg.subscription, "timezone": cfg.timezone, "owner_reserve_pct": cfg.owner_reserve_pct,
            "day": usage.window_view(cfg.day_usd_eq, day, live, w.day_start, w.next_day, cfg.owner_reserve_pct),
            "week": usage.window_view(cfg.week_usd_eq, week, live, w.week_start, w.next_week, cfg.owner_reserve_pct)}
