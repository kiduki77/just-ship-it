"""The crews screen's read (design section 9): one `CrewView` per task and per build of the listed projects, read from
`crews`, `crew_turns`, `llm_calls` and the subject's own row. Nothing is derived that no record holds: a job with no
crew says so, and has no team."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from justshipit.contracts import owner_text
from justshipit.contracts.handoff import DEV_JOB_SETTLED, DevJobStatus
from justshipit.contracts.work import TERMINAL_TASK_STATUSES, TaskStatus
from justshipit.db.models import Crew, CrewTurn, DevJob, Project, Spec, Task
from justshipit.i18n import Lang, msg
from justshipit.i18n.glossary import role
from justshipit.staffing.crew import Duty, Flow, JobFacts, Shape, Slot, SubjectKind, fixed_path
from justshipit.staffing.ledger import held, verifier
from justshipit.staffing.outcome import Calls, Outcome, Usage, ledger_calls


class Standing(StrEnum):
    WAITING = "waiting"
    WORKING = "working"
    DISSOLVED = "dissolved"
    CANCELLED_BEFORE_START = "cancelled_before_start"
    NO_RECORD = "no_record"


_UNSTAFFED = (Standing.WAITING, Standing.CANCELLED_BEFORE_START, Standing.NO_RECORD)
_ROLE_FACTS = ("owner", "checker")


@dataclass(frozen=True)
class Subject:
    kind: SubjectKind
    id: int
    key: str
    title: str
    status: str
    project_id: int
    project: str


@dataclass(frozen=True)
class Said:
    rule: str
    facts: dict[str, int | float | str]
    text: str
    at: str | None = None   # when an amendment added a slot; None for the placement's own reasons


@dataclass(frozen=True)
class Team:
    family: str
    shape: Shape
    flow: Flow
    after: tuple[int, ...]
    alongside: tuple[int, ...]
    slots: tuple[Slot, ...]   # the decision, then the slots amendments added
    idle: tuple[Slot, ...]    # slots whose role made no call on the job
    why: tuple[Said, ...]     # every rule that held, then every addition
    rules_version: str
    assembled_at: datetime


@dataclass(frozen=True)
class CrewView:
    subject: Subject
    standing: Standing
    team: Team | None
    note: str | None
    members: tuple[Usage, ...]    # who called a model on the job, from the ledger
    outcome: Outcome


def crew_views(s: Session, projects: Select | Iterable[int], lang: Lang, *,
               standing: Standing | None = None) -> list[CrewView]:
    """Every task and build of `projects` (ids, or a select of ids), newest project first, a project's tasks before its
    builds."""
    tasks = s.execute(select(Task, Project.name).join(Project, Project.id == Task.project_id)
                      .where(Task.project_id.in_(projects)).order_by(Task.project_id.desc(), Task.id)).all()
    builds = s.execute(select(DevJob, Spec, Project.name).join(Spec, Spec.id == DevJob.spec_id)
                       .join(Project, Project.id == Spec.project_id)
                       .where(Spec.project_id.in_(projects)).order_by(Spec.project_id.desc(), DevJob.id)).all()
    views = [*_task_views(s, tasks, lang), *_build_views(s, builds, lang)]
    views.sort(key=lambda v: (-v.subject.project_id, v.subject.kind is SubjectKind.BUILD, v.subject.id))
    return [v for v in views if standing is None or v.standing is standing]


def _task_views(s: Session, rows, lang: Lang) -> list[CrewView]:
    ids = [t.id for t, _ in rows]
    crews, turns, calls = _crews(s, SubjectKind.TASK, ids), _turns(s, SubjectKind.TASK, ids), ledger_calls(
        s, SubjectKind.TASK, ids)
    out = []
    for t, project in rows:
        crew = crews.get(t.id)
        passes, _ = turns.get(crew.id if crew else -1, (0, 0))
        ended = TaskStatus(t.status) in TERMINAL_TASK_STATUSES
        wall = t.finished_at - t.started_at if t.started_at and t.finished_at else None
        outcome = _outcome(calls[t.id], wall, (t.review or {}).get("verdict"), max(passes - 1, 0),
                           passes * len(fixed_path(crew.facts["owner"])) if crew else None)
        subject = Subject(SubjectKind.TASK, t.id, t.key, owner_text.title(t.title), t.status, t.project_id, project)
        standing = _standing(crew, ended, cancelled=t.status == TaskStatus.CANCELLED)
        out.append(_view(subject, standing, crew, calls[t.id], outcome, lang))
    return out


def _build_views(s: Session, rows, lang: Lang) -> list[CrewView]:
    ids = [job.id for job, _, _ in rows]
    crews, turns, calls = _crews(s, SubjectKind.BUILD, ids), _turns(s, SubjectKind.BUILD, ids), ledger_calls(
        s, SubjectKind.BUILD, ids)
    out = []
    for job, spec, project in rows:
        crew = crews.get(job.id)
        _, rounds = turns.get(crew.id if crew else -1, (0, 0))
        ended = DevJobStatus(job.status) in DEV_JOB_SETTLED
        last = job.steps[-1]["at"] if job.steps else None
        wall = datetime.fromisoformat(last) - crew.assembled_at if crew and ended and last else None
        acceptance = (job.result or {}).get("acceptance")
        verdict = None if acceptance is None else "accepted" if acceptance.get("accepted") else "not_accepted"
        outcome = _outcome(calls[job.id], wall, verdict, rounds, None)
        subject = Subject(SubjectKind.BUILD, job.id, spec.spec_key, owner_text.title(spec.title), job.status,
                          spec.project_id, project)
        out.append(_view(subject, _standing(crew, ended, cancelled=False), crew, calls[job.id], outcome, lang))
    return out


def _crews(s: Session, kind: SubjectKind, ids: list[int]) -> dict[int, Crew]:
    if not ids:
        return {}
    return {c.subject_id: c for c in s.scalars(select(Crew).where(Crew.subject_kind == kind.value,
                                                                 Crew.subject_id.in_(ids)))}


def _turns(s: Session, kind: SubjectKind, ids: list[int]) -> dict[int, tuple[int, int]]:
    """{crew id: (author passes, highest fix round)}, leaving out shadow checks."""
    if not ids:
        return {}
    rows = s.execute(select(CrewTurn.crew_id, func.count().filter(CrewTurn.duty == Duty.AUTHOR.value),
                            func.max(CrewTurn.round))
                     .join(Crew, Crew.id == CrewTurn.crew_id)
                     .where(Crew.subject_kind == kind.value, Crew.subject_id.in_(ids), ~CrewTurn.shadow)
                     .group_by(CrewTurn.crew_id))
    return {crew_id: (passes, top or 0) for crew_id, passes, top in rows}


def _outcome(calls: Calls, wall, verdict: str | None, reworks: int, fixed_path_calls: int | None) -> Outcome:
    own = calls.by_role
    return Outcome(calls=sum(u.calls for u in own), failed_calls=sum(u.failed_calls for u in own),
                   cost_usd=sum(u.cost_usd for u in own), busy_ms=sum(u.busy_ms for u in own),
                   wall_ms=None if wall is None else int(wall.total_seconds() * 1000), verdict=verdict,
                   reworks=reworks, fixed_path_calls=fixed_path_calls, audit=calls.audit)


def _standing(crew: Crew | None, ended: bool, *, cancelled: bool) -> Standing:
    if crew is not None:
        return Standing.DISSOLVED if ended else Standing.WORKING
    if not ended:
        return Standing.WAITING
    return Standing.CANCELLED_BEFORE_START if cancelled else Standing.NO_RECORD


def _view(subject: Subject, standing: Standing, crew: Crew | None, calls: Calls, outcome: Outcome,
          lang: Lang) -> CrewView:
    note = msg(f"staffing.crew.{standing.value}", lang) if standing in _UNSTAFFED else None
    return CrewView(subject, standing, None if crew is None else _team(crew, calls, lang), note, calls.by_role,
                    outcome)


def _team(crew: Crew, calls: Calls, lang: Lang) -> Team:
    facts = JobFacts(**{**crew.facts, "after": tuple(crew.facts["after"]),
                        "alongside": tuple(crew.facts["alongside"]), "kind": SubjectKind(crew.subject_kind)})
    slots = held(crew)
    worked = {u.role for u in calls.by_role}
    shape = (Shape.CHAIN if facts.kind is SubjectKind.BUILD else
             Shape.CHECKED if verifier(crew) is not None else Shape.SOLO)
    why = (*(_said(r["rule"], r["facts"], lang) for r in crew.why),
           *(_said(a["why"]["rule"], a["why"]["facts"], lang, a["at"]) for a in crew.amendments))
    return Team(crew.family, shape, facts.flow, facts.after, facts.alongside, slots,
                tuple(x for x in slots if x.role not in worked), why, crew.rules_version, crew.assembled_at)


def _said(rule: str, facts: dict[str, int | float | str], lang: Lang, at: str | None = None) -> Said:
    words = {k: role(str(v), lang) if k in _ROLE_FACTS else v for k, v in facts.items()}
    return Said(rule, facts, msg(f"staffing.why.{rule}", lang, **words), at)
