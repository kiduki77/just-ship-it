"""What a job's calls came to, read from the ledger (design sections 7 and 9): who made them, and its shadow checks on
a line of their own, so an alone task still shows one call. Read, never copied: `llm_calls` stays the one record of a
call."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from justshipit.db.models import LLMCallRecord
from justshipit.staffing.crew import SubjectKind

AUDIT = "audit"


@dataclass(frozen=True)
class Spend:
    calls: int
    cost_usd: float


NOTHING = Spend(0, 0.0)


@dataclass(frozen=True)
class Usage:
    """One role's calls on one job, on one model. A role whose calls fell back to another model has two."""
    role: str
    model: str | None
    calls: int
    failed_calls: int
    cost_usd: float
    busy_ms: int


@dataclass(frozen=True)
class Calls:
    """A job's own calls by role, in the order the roles first worked, and its shadow checks apart."""
    by_role: tuple[Usage, ...]
    audit: Spend


@dataclass(frozen=True)
class Outcome:
    calls: int
    failed_calls: int
    cost_usd: float
    busy_ms: int
    wall_ms: int | None           # start to end, None while the job runs or when nothing records its end
    verdict: str | None           # a task's review verdict, a build's acceptance; None when there is none yet
    reworks: int                  # a task's author passes after the first, a build's fix rounds
    fixed_path_calls: int | None  # what the path before staffing would have made for the same passes; tasks only
    audit: Spend                  # shadow checks, left out of the calls and cost above


def ledger_calls(s: Session, kind: SubjectKind, ids: list[int]) -> dict[int, Calls]:
    if not ids:
        return {}
    subject = LLMCallRecord.task_id if kind is SubjectKind.TASK else LLMCallRecord.dev_job_id
    shadow = LLMCallRecord.purpose == AUDIT
    rows = s.execute(
        select(subject, LLMCallRecord.agent, LLMCallRecord.model, shadow, func.count(),
               func.count().filter(LLMCallRecord.status != "ok"), func.sum(LLMCallRecord.cost_usd),
               func.sum(LLMCallRecord.duration_ms), func.min(LLMCallRecord.id))
        .where(subject.in_(ids)).group_by(subject, LLMCallRecord.agent, LLMCallRecord.model, shadow)
        .order_by(func.min(LLMCallRecord.id)))
    own: dict[int, list[Usage]] = defaultdict(list)
    audits: dict[int, Spend] = {}
    for sid, agent, model, is_audit, n, failed, cost, busy, _ in rows:
        if is_audit:
            was = audits.get(sid, NOTHING)
            audits[sid] = Spend(was.calls + n, was.cost_usd + float(cost or 0.0))
        else:
            own[sid].append(Usage(agent, model, n, failed, float(cost or 0.0), int(busy or 0)))
    return {i: Calls(tuple(own[i]), audits.get(i, NOTHING)) for i in ids}
