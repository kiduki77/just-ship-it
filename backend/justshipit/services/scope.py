"""Which rows an owner screen shows: the one rule every console list applies.

The console shows one business at a time. A row belongs to the business of its project. A row with no project belongs
to the business it names, and a row that names neither is the company's own (the worker, the monthly budget, Slack): it
shows under every business, and the console labels it 회사 전체. Archived projects never show.

Without a business the view spans every business but the archived ones, and [TEST] projects stay out unless asked.
With one, the business decides: a [TEST] business is the one the owner picked, so its projects show, and an archived
business named by id shows its own.
"""
from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from justshipit.db.models import Business, Project


def of_archived_business():
    """SQL for a project whose business is archived: a view of every business leaves it out."""
    return Project.business_id.in_(select(Business.id).where(Business.status == "ARCHIVED"))


def hidden_projects(s: Session, *, business_id: int | None = None, include_test: bool = False) -> set[int]:
    """The project ids a view leaves out."""
    if business_id is not None:
        out = Project.status == "ARCHIVED", Project.business_id.is_distinct_from(business_id)
    else:
        out = (Project.status == "ARCHIVED", of_archived_business()) + (
            () if include_test else (Project.name.startswith("[TEST]"),))
    return set(s.scalars(select(Project.id).where(or_(*out))))


def owner_of(s: Session, ids: dict) -> tuple[int | None, int | None]:
    """The project and the business a record belongs to, from the subject ids it names (a queue job's payload, a
    dev job's spec): its project decides both; without one, the bet, owner task or business it names decides the
    business. (None, None) is the company's own. gate.admit reads this to place work in a stage, so it stays as it was;
    `subject_business` also knows the owner's own records and is only for stamping and listing."""
    from justshipit.db.models import AgentRequest, Bet, Cycle, CycleStage, DevJob, OwnerTask, Spec, Task
    pid = ids.get("project_id")
    spec_id = ids.get("spec_id")
    if pid is None and spec_id is None and ids.get("dev_job_id") is not None and (d := s.get(DevJob, ids["dev_job_id"])):
        spec_id = d.spec_id
    for model, key in ((Spec, spec_id), (AgentRequest, ids.get("request_id")), (Task, ids.get("task_id")),
                       (OwnerTask, ids.get("owner_task_id"))):
        if pid is None and key is not None and (row := s.get(model, key)) is not None:
            pid = row.project_id
            if pid is None and isinstance(row, OwnerTask):
                return None, row.business_id
    if pid is not None:
        p = s.get(Project, pid)
        return pid, p.business_id if p else None
    if ids.get("bet_id") is not None and (bet := s.get(Bet, ids["bet_id"])) is not None:
        return None, bet.business_id
    for model, key in ((CycleStage, ids.get("stage_id")), (Cycle, ids.get("cycle_id"))):
        if key is not None and (row := s.get(model, key)) is not None:
            return None, row.business_id
    return None, ids.get("business_id")


def subject_business(s: Session, ids: dict) -> int | None:
    """The business a queue job is about, for its stamp and the run log: owner_of's answer, or else the kickoff chat,
    plan, idea or idea review it names. Kept apart from owner_of so placing a job in a stage does not change."""
    from justshipit.db.models import BusinessBrief, IdeaReview, KickoffSession, OwnerIdea
    bid = owner_of(s, ids)[1]
    if bid is not None:
        return bid
    for model, key in ((KickoffSession, ids.get("session_id")), (BusinessBrief, ids.get("brief_id")),
                       (OwnerIdea, ids.get("idea_id")), (IdeaReview, ids.get("review_id"))):
        if key is not None and (row := s.get(model, key)) is not None:
            return row.business_id
    return None


def rows(hidden: set[int], project_col, business_col=None, business_id: int | None = None):
    """SQL for the rows a view shows, given its `hidden` projects. A row with no project shows when it is the company's
    or, with `business_col`, names the view's business."""
    loose = project_col.is_(None)
    if business_col is not None and business_id is not None:
        loose = and_(loose, or_(business_col.is_(None), business_col == business_id))
    return or_(loose, project_col.notin_(hidden)) if hidden else or_(loose, project_col.is_not(None))
