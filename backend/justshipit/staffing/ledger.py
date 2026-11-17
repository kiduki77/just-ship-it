"""The one writer of `crews` and `crew_turns`. Every call is idempotent: a retry, a checkpoint replay and a concurrent
caller all leave the same rows (a unique constraint decides, then the row is read back)."""
from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from justshipit.db.models import Crew, CrewTurn, Task
from justshipit.staffing.config import load_limits
from justshipit.staffing.crew import RULES_VERSION, Duty, JobFacts, Limits, Reason, Slot, SubjectKind, place
from justshipit.staffing.facts import task_facts
from justshipit.staffing.stats import recorded_history
from justshipit.staffing.turns import Turn

_COLUMNS = {"role", "call_id", "round", "iteration"}


def ensure(s: Session, subject_id: int, facts: JobFacts, limits: Limits, *,
           draw: Callable[[], float] = random.random) -> Crew:
    """The crew of a task or build: placed by the rule when it has none, otherwise the one already made, whatever the
    facts or the statistics say now. The rule is not rerun, so a decision stays the decision that was made. A task's
    placement reads its family's statistics, and `draw` is the explore sample's draw."""
    if (crew := _stored(s, facts.kind, subject_id)) is not None:
        return crew
    history = recorded_history(s, limits, facts.family, draw()) if facts.kind is SubjectKind.TASK else None
    placed = place(facts, limits, history)
    s.execute(insert(Crew).values(
        subject_kind=facts.kind.value, subject_id=subject_id, family=facts.family,
        slots=[asdict(slot) for slot in placed.slots], facts=asdict(facts), why=[asdict(r) for r in placed.why],
        amendments=[], rules_version=RULES_VERSION,
    ).on_conflict_do_nothing(index_elements=["subject_kind", "subject_id"]))
    return _stored(s, facts.kind, subject_id)


def _stored(s: Session, kind: SubjectKind, subject_id: int) -> Crew | None:
    return s.scalars(select(Crew).where(Crew.subject_kind == kind.value, Crew.subject_id == subject_id)
                     .execution_options(populate_existing=True)).one_or_none()


def amend(s: Session, crew: Crew, slot: Slot, why: Reason) -> Crew:
    s.refresh(crew, with_for_update=True)
    if not any(h.duty is slot.duty and h.role == slot.role for h in held(crew)):
        crew.amendments = [*crew.amendments, {"slot": asdict(slot), "why": asdict(why),
                                              "at": datetime.now(UTC).isoformat()}]
        s.flush()
    return crew


def record_turn(s: Session, crew: Crew, turn: Turn, *, shadow: bool = False) -> CrewTurn:
    s.execute(insert(CrewTurn).values(
        crew_id=crew.id, call_id=turn.call_id, duty=turn.duty.value, role=turn.role, round=turn.round,
        iteration=turn.iteration, shadow=shadow, record=turn.model_dump(mode="json", exclude=_COLUMNS),
    ).on_conflict_do_nothing(index_elements=["crew_id", "call_id"]))
    return s.scalars(select(CrewTurn).where(CrewTurn.crew_id == crew.id, CrewTurn.call_id == turn.call_id)
                     .execution_options(populate_existing=True)).one()


def task_crew(s: Session, task_id: int) -> Crew | None:
    return s.scalar(select(Crew).where(Crew.subject_kind == SubjectKind.TASK.value, Crew.subject_id == task_id))


def held(crew: Crew) -> tuple[Slot, ...]:
    return tuple(Slot(Duty(h["duty"]), h["role"], h["model"]) for h in [*crew.slots, *(a["slot"] for a in crew.amendments)])


def author(crew: Crew) -> Slot:
    return next(h for h in held(crew) if h.duty is Duty.AUTHOR)


def verifier(crew: Crew) -> Slot | None:
    return next((h for h in held(crew) if h.duty is Duty.VERIFIER), None)


def verifier_of(s: Session, t: Task) -> str | None:
    """Who checks this task: its crew's checker, or before the task has a crew the one the rule would place now. None
    when the task runs alone."""
    crew = task_crew(s, t.id)
    if crew is None:
        facts, limits = task_facts(s, t), load_limits()
        placed = place(facts, limits, recorded_history(s, limits, facts.family))
        slot = next((x for x in placed.slots if x.duty is Duty.VERIFIER), None)
    else:
        slot = verifier(crew)
    return slot.role if slot is not None else None
