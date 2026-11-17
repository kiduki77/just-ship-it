from __future__ import annotations

import random
from collections.abc import Callable

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from justshipit.db.models import Crew, CrewTurn, Job, Task
from justshipit.staffing.config import load_limits
from justshipit.staffing.crew import HISTORY_EXPLORE, Duty, Reason, Slot, SubjectKind, verifier_role
from justshipit.staffing.ledger import amend, task_crew, verifier

UNCHECKED = "unchecked"        # `tasks.review.verdict` of a task no second role checked, on purpose
OWNER_CARD = "check.owner_card"
CALCULATION = "check.calculation"
RESUMED = "check.resumed"      # the placement rule's own id: it would hold now, and the amendment records that it does
AMENDMENT_REASONS = (OWNER_CARD, CALCULATION, RESUMED)  # reasons for a slot added after the placement; each has a staffing.why sentence


def settle_solo(s: Session, crew: Crew, t: Task) -> Crew:
    """A crew with no checker gets one when its author's result asks the owner something, since a card reaches the
    owner only after an independent review (`AgentResultOut.requests`), or carries a financial model, which the
    finance engine turns into calculation evidence the way `check.numbers` checks finance's own."""
    result = t.result or {}
    if verifier(crew) is not None:
        return crew
    if asks := len(result.get("requests") or []):
        why = Reason(OWNER_CARD, {"requests": asks})
    elif scenarios := len((result.get("financial_model") or {}).get("scenarios") or []):
        why = Reason(CALCULATION, {"scenarios": scenarios})
    else:
        return crew
    return amend(s, crew, Slot(Duty.VERIFIER, verifier_role(t.owner), None), why)


def settle_retry(s: Session, t: Task) -> None:
    """A task run again after it failed has been authored, so `check.resumed` holds for it. The rule is not rerun: a
    crew placed alone gets the checker as an amendment, which is the record of the change."""
    crew = task_crew(s, t.id)
    if crew is not None and verifier(crew) is None:
        amend(s, crew, Slot(Duty.VERIFIER, verifier_role(t.owner), None), Reason(RESUMED, {"iteration": t.iteration}))


def enqueue_audit(s: Session, crew: Crew, t: Task, *, draw: Callable[[], float] = random.random) -> Job | None:
    """Queue the shadow check of a task that stays alone, for the sampled share (`Limits.audit_rate`), and always for
    one that ran on a model drawn to explore, whose sample counts only once a check judges it. An audit budget or rate
    of 0 turns the checks off. The job is keyed by the task, so a replay of `work` never queues a second one."""
    limits = load_limits()
    if verifier(crew) is not None or not limits.audit_budget_usd or not limits.audit_rate:
        return None
    if not any(r["rule"] == HISTORY_EXPLORE for r in crew.why) and draw() >= limits.audit_rate:
        return None
    from justshipit.services import queue   # the queue imports services that import this package
    return queue.enqueue(s, "audit_task", {"project_id": t.project_id, "task_id": t.id},
                         idempotency_key=f"audit_task:{t.id}", max_attempts=2)


def audited(s: Session, crew: Crew) -> bool:
    return s.scalar(select(CrewTurn.id).where(CrewTurn.crew_id == crew.id, CrewTurn.shadow).limit(1)) is not None


def alone_task_ids() -> Select:
    return select(Crew.subject_id).where(
        Crew.subject_kind == SubjectKind.TASK.value, ~Crew.slots.contains([{"duty": Duty.VERIFIER.value}]),
        ~Crew.amendments.contains([{"slot": {"duty": Duty.VERIFIER.value}}]))
