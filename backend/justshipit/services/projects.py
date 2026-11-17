from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from justshipit.contracts.requests import RequestStatus
from justshipit.db.models import AgentRequest, Bet, Business, Cycle, CycleStage, Job, Project, Schedule
from justshipit.i18n import Lang, msg
from justshipit.services import queue, roadmap
from justshipit.services.control import ControlError, NotFound, owner_lang
from justshipit.settings import limits


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "project"


def create(s: Session, goal: str, *, name: str | None = None, slug: str | None = None,
           budget_usd: float | None = None, start: bool = True, parent_id: int | None = None,
           business: Business | None = None, stage: CycleStage | None = None, by_owner: bool = False) -> Project:
    """A project and, unless `start` is False, its run. `stage`: the stage the owner just started; its project gets no
    experiment of its own (services/cycle.py), and its run joins the stage. `by_owner`: the owner's own command made
    it (the console's New project, `ship project new`), so its run is not parked for a stage."""
    name = (name or "").strip() or goal[:60]
    base = slug or slugify(name)
    if base == "project":
        last = s.scalar(select(Project.id).order_by(Project.id.desc()).limit(1)) or 0
        base = f"project-{last + 1}"
    p = Project(slug=roadmap.unique_slug(s, Project.slug, base), name=name, goal=goal, status="CREATED",
                budget_usd=budget_usd or float(limits()["project_budget_usd"]),
                context={"parent_project_id": parent_id} if parent_id else {})
    roadmap.adopt_project(s, p, running=start, business=business, stage=stage)
    s.add(p)
    s.flush()
    if start:
        queue.enqueue(s, "run_project", {"project_id": p.id}, idempotency_key=f"run:{p.id}", max_attempts=1,
                      by_owner=by_owner)
    return p


def start_business(s: Session, business_id: int, *, lang: Lang | None = None) -> Cycle:
    """The business's first cycle, from its approved plan: the CEO is asked to propose its stages, and nothing else
    starts until the owner starts a stage. A second call returns the open cycle."""
    from justshipit.services import cycle
    lang = owner_lang(lang)
    ref = f"B-{business_id}"
    business = s.scalar(select(Business).where(Business.id == business_id).with_for_update())
    if business is None:
        raise NotFound(msg("cleanup.refuse.no_business", lang, business=ref))
    if (started := cycle.open_cycle_of(s, business_id)) is not None:
        return started
    if business.status == "ARCHIVED":
        raise ControlError(msg("work.refuse.archived_business", lang, ref=ref))
    roadmap.refuse_while_kickoff_live(s, business_id, lang=lang)
    brief = roadmap.approved_brief(s, business_id)
    if brief is None:
        roadmap.refuse_abandoned_draft(s, business_id, lang=lang)
        raise ControlError(msg("work.refuse.no_plan", lang, ref=ref))
    if blockers := roadmap.approval_blockers(brief.sections, lang=lang):
        raise ControlError(msg("work.refuse.plan_gaps", lang, ref=ref, sections=", ".join(blockers)))
    ever = s.scalar(select(Project.id).where(Project.business_id == business_id).limit(1)) is not None
    return cycle.open_cycle(s, business_id, mode="adopt" if ever else "new", lang=lang)


def waiting_jobs(s: Session, project_id: int, *, lock: bool = False) -> list[Job]:
    """The project's jobs that have not started and that the owner may cancel (`queue.cancel_unstarted`): what the
    archive's one confirm cancels, which it reads `lock`ed and the console reads to say how many."""
    q = select(Job).where(Job.status.in_(queue.UNSTARTED), Job.kind.notin_(queue.NOT_RETRYABLE),
                          Job.payload["project_id"].as_integer() == project_id).order_by(Job.id)
    return list(s.scalars(q.with_for_update() if lock else q))


def archive(s: Session, project_id: int, *, by: str = "owner", business_id: int | None = None,
            note: str | None = None, lang: Lang | None = None, cancel_waiting: bool = False) -> dict:
    """Take a project off the company's books without destroying its record: pending requests are
    cancelled (nothing resumes it), its schedules stop, and it disappears from the console lists.
    Evidence, decisions and artifacts stay in the database and registry. `business_id` marks a project archived with
    its business, which `unarchive_business` brings back, and `note` is the cancelled requests' reason in place of the
    project's own. A queued or running job refuses it; `cancel_waiting` (the owner's one confirm) first cancels the jobs
    that have not started (`waiting_jobs`), so only running work or an approved send or merge still refuses."""
    lang = owner_lang(lang)
    p = s.get(Project, project_id)
    if p is None:
        raise ValueError(msg("work.refuse.no_project", lang, ref=f"P-{project_id}"))
    waiting = waiting_jobs(s, project_id, lock=True) if cancel_waiting else []
    if s.scalar(select(Job.id).where(Job.status.in_(["queued", "running"]), Job.id.notin_([j.id for j in waiting]),
                                     Job.payload["project_id"].as_integer() == project_id).limit(1)):
        raise ValueError(msg("work.refuse.archive_busy", lang, ref=f"P-{project_id}"))
    for j in waiting:
        queue.cancel_unstarted(s, j, lang)
    now = datetime.now(timezone.utc)
    cancelled = []
    for r in s.scalars(select(AgentRequest).where(AgentRequest.project_id == project_id,
                                                  AgentRequest.status == RequestStatus.PENDING.value)):
        r.status, r.resolved_at = RequestStatus.CANCELLED.value, now
        r.response = {"decision": "withdrawn", "by": by, "responded_by": by,
                      "note": note or msg("work.archived_note", lang)}
        cancelled.append(r.id)
    stopped = []
    for sc in s.scalars(select(Schedule).where(Schedule.payload["project_id"].as_integer() == project_id,
                                               Schedule.enabled.is_(True))):
        sc.enabled = False
        stopped.append(sc.id)
    p.context = {**(p.context or {}), "archived": {"at": now.isoformat(), "by": by, "previous_status": p.status,
                                                   "previous_phase": p.phase,
                                                   **({"with_business": business_id} if business_id else {})}}
    p.status, p.phase = "ARCHIVED", "archived"
    return {"cancelled_requests": cancelled, "disabled_schedules": stopped, "cancelled_jobs": [j.id for j in waiting]}


def _business_schedules(s: Session, business_id: int):
    """The schedules that work for the business itself (promotion, GitHub, people) and for its bets (routines)."""
    bets = select(Bet.id).where(Bet.business_id == business_id)
    return select(Schedule).where(or_(Schedule.payload["business_id"].as_integer() == business_id,
                                      Schedule.payload["bet_id"].as_integer().in_(bets)))


def archive_business(s: Session, business_id: int, *, lang: Lang, by: str = "owner") -> dict:
    """Take a business off the books so it spends nothing more: each project is archived (its pending requests
    cancelled, its schedules stopped), the business's and its routines' schedules stop, and the business leaves the
    switcher and every list. Refused while one of its jobs is queued or running, as a project is. Nothing is deleted;
    `unarchive_business` brings it back. Running it again changes nothing."""
    from justshipit.services import scope
    b = s.scalar(select(Business).where(Business.id == business_id).with_for_update())
    if b is None:
        raise ValueError(msg("cleanup.refuse.no_business", lang, business=f"B-{business_id}"))
    queue.close_gated(s, business_id, msg("cleanup.note.gated_closed", lang))
    queue.close_queued(s, business_id, "profile_infer", msg("cleanup.note.queued_closed", lang))
    for j in s.scalars(select(Job).where(queue.in_flight())):
        if scope.owner_of(s, j.payload or {})[1] == business_id:
            raise ValueError(msg("cleanup.refuse.busy", lang, business=f"B-{business_id}", job=j.id, kind=j.kind))
    note = msg("cleanup.note.business_archived", lang)
    done = {"projects": [], "cancelled_requests": [], "disabled_schedules": []}
    for p in s.scalars(select(Project).where(Project.business_id == business_id, Project.status != "ARCHIVED")
                       .order_by(Project.id)):
        one = archive(s, p.id, by=by, business_id=business_id, note=note, lang=lang)
        done["projects"].append(p.id)
        done["cancelled_requests"] += one["cancelled_requests"]
        done["disabled_schedules"] += one["disabled_schedules"]
    for sc in s.scalars(_business_schedules(s, business_id).where(Schedule.enabled.is_(True))):
        sc.enabled = False
        done["disabled_schedules"].append(sc.id)
    bets = select(Bet.id).where(Bet.business_id == business_id)
    for r in s.scalars(select(AgentRequest).where(AgentRequest.project_id.is_(None),
                                                  AgentRequest.action_params["bet_id"].as_integer().in_(bets),
                                                  AgentRequest.status == RequestStatus.PENDING.value)):
        r.status, r.resolved_at = RequestStatus.CANCELLED.value, datetime.now(timezone.utc)
        r.response = {"decision": "withdrawn", "by": by, "responded_by": by, "note": note}
        done["cancelled_requests"].append(r.id)
    from justshipit.services import cycle
    cycle.stop_for_archive(s, business_id, lang=lang, by=by)
    b.status = "ARCHIVED"
    return done


def unarchive_business(s: Session, business_id: int, *, lang: Lang) -> dict:
    """Bring an archived business back: the projects archived with it return as `control.unarchive` brings a
    project back, and the business's own schedules run again. Cancelled requests stay cancelled, and a routine or a
    project schedule stays stopped until the owner starts it, as after a project's unarchive."""
    from justshipit.services import control
    b = s.scalar(select(Business).where(Business.id == business_id).with_for_update())
    if b is None:
        raise ValueError(msg("cleanup.refuse.no_business", lang, business=f"B-{business_id}"))
    if b.status != "ARCHIVED":
        raise ValueError(msg("cleanup.refuse.not_archived", lang, business=f"B-{business_id}"))
    b.status = "ACTIVE"
    back = [control.unarchive(s, p.id, lang=lang).id for p in s.scalars(
        select(Project).where(Project.business_id == business_id, Project.status == "ARCHIVED",
                              Project.context["archived"]["with_business"].as_integer() == business_id)
        .order_by(Project.id))]
    mine = [sc for sc in s.scalars(_business_schedules(s, business_id).where(Schedule.enabled.is_(False)))
            if "business_id" in (sc.payload or {})]
    for sc in mine:
        sc.enabled = True
    return {"projects": back, "enabled_schedules": [sc.id for sc in mine]}
