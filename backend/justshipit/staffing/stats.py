"""Placement statistics (design section 7): what each role's tasks of a family came to on each model, counted from
`crews`, `crew_turns` and `llm_calls`. A query, never a table, so a statistic always matches the records it counts.

A task counts once, under the role and model of its first author pass, and only when a check has judged it: its own
checker's first review, or the shadow check of a task that ran alone. Its unmet count is whether that first check
found an unmet criterion. Cost is its own calls (shadow checks apart), not dollars."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from justshipit.db.models import Crew, CrewTurn, LLMCallRecord
from justshipit.settings import agents_config
from justshipit.staffing.crew import Duty, History, Limits, Stat, SubjectKind, Tally
from justshipit.staffing.outcome import AUDIT


def ladder_name(model: str | None, ladder: tuple[str, ...]) -> str:
    """The ladder's name for a model: the CLI reports the full name ("claude-sonnet-4-5-..."), while the roster and
    the ladder say "sonnet". A model off the ladder keeps its own name, so the rule never picks it."""
    low = (model or "").lower()
    return next((m for m in ladder if m in low), model or "")


@dataclass
class _Task:
    task_id: int
    family: str
    author: tuple[str, str] | None = None   # the first pass's role and ledger call
    passes: int = 0
    first_check: tuple[bool, bool] | None = None   # (a shadow check, found an unmet criterion)


def placement_stats(s: Session, ladder: tuple[str, ...], family: str | None = None) -> tuple[Stat, ...]:
    """One `Stat` per family, role and model, for every family or for `family` alone."""
    unmet = func.jsonb_array_length(CrewTurn.record["review"]["criteria_unmet"])
    rows = (select(Crew.id, Crew.subject_id, Crew.family, CrewTurn.duty, CrewTurn.role, CrewTurn.shadow,
                   CrewTurn.call_id, unmet)
            .join(CrewTurn, CrewTurn.crew_id == Crew.id).where(Crew.subject_kind == SubjectKind.TASK.value)
            .order_by(CrewTurn.id))
    if family is not None:
        rows = rows.where(Crew.family == family)
    tasks: dict[int, _Task] = {}
    for crew_id, task_id, fam, duty, role, shadow, call_id, misses in s.execute(rows):
        t = tasks.setdefault(crew_id, _Task(task_id, fam))
        if duty == Duty.AUTHOR.value and not shadow:
            t.passes += 1
            t.author = t.author or (role, call_id)
        elif duty == Duty.VERIFIER.value and t.first_check is None:
            t.first_check = (shadow, bool(misses))
    judged = [t for t in tasks.values() if t.author is not None and t.first_check is not None]
    if not judged:
        return ()
    models = dict(s.execute(select(LLMCallRecord.call_id, LLMCallRecord.model)
                            .where(LLMCallRecord.call_id.in_([t.author[1] for t in judged]))).all())
    own = dict(s.execute(select(LLMCallRecord.task_id, func.count())
                         .where(LLMCallRecord.task_id.in_([t.task_id for t in judged]), LLMCallRecord.purpose != AUDIT)
                         .group_by(LLMCallRecord.task_id)).all())
    sums: dict[tuple[str, str, str], list] = defaultdict(lambda: [Tally(), Tally(), 0, 0])
    for t in judged:
        role, call_id = t.author
        shadow, missed = t.first_check
        acc = sums[(t.family, role, ladder_name(models.get(call_id), ladder))]
        acc[0 if shadow else 1] += Tally(1, int(missed))
        acc[2] += own.get(t.task_id, 0)
        acc[3] += t.passes - 1
    return tuple(Stat(fam, role, model, alone, checked, calls, reworks)
                 for (fam, role, model), (alone, checked, calls, reworks) in sorted(sums.items()))


def recorded_history(s: Session, limits: Limits, family: str, draw: float = 1.0) -> History:
    """What version 2 of the rule reads for a task of `family`. `draw` is the explore sample's draw, taken by the
    caller; 1.0 never samples."""
    configured = {r: ladder_name(cfg.get("model"), limits.model_ladder) or None for r, cfg in agents_config().items()}
    return History(placement_stats(s, limits.model_ladder, family), configured, draw)
