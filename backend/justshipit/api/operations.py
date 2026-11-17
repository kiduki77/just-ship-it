from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from justshipit.db.models import (
    ActionExecution, Business, CrmContact, CrmInteraction, Kpi, KpiValue, Project, Schedule,
)
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.operations import scheduler
from justshipit.services import app_settings, gate, owner_tasks, scope

router = APIRouter()


def _row(o) -> dict:
    return {c.name: getattr(o, c.name) for c in o.__table__.columns}


def _archived_projects(s) -> set[int]:
    return set(s.scalars(select(Project.id).where(Project.status == "ARCHIVED")))


@router.get("/schedules")
def schedules(business_id: int | None = None) -> list[dict]:
    """Every schedule but those of an archived project or business, which stay stopped and come back with it. Each
    row names the project and business it works for (None for the company's own). With `business_id`, that business's
    schedules and the company's own (services/scope.py). `admit` is what the schedule's next run would do now
    (gate.admit, not by the owner): `run` in the running stage, `park` until a stage starts, `skip` because no running
    stage lists it, or None for a kind the gate does not know."""
    with session_scope() as s:
        gone = _archived_projects(s)
        closed = set(s.scalars(select(Business.id).where(Business.status == "ARCHIVED")))
        out = []
        for x in s.scalars(select(Schedule).order_by(Schedule.id)):
            pid, bid = scope.owner_of(s, x.payload or {})
            if pid in gone or bid in closed or (business_id is not None and bid not in (None, business_id)):
                continue
            try:
                admit = gate.admit(s, x.kind, dict(x.payload or {})).verdict
            except gate.UndeclaredJobKind:
                admit = None
            out.append({**_row(x), "project_id": pid, "business_id": bid, "admit": admit})
        return out


@router.post("/schedules/{schedule_id}/toggle")
def toggle(schedule_id: int) -> dict:
    with session_scope() as s:
        sc = s.get(Schedule, schedule_id)
        if not sc:
            raise HTTPException(404, msg("console.no_schedule", app_settings.language(), id=schedule_id))
        sc.enabled = not sc.enabled
        return _row(sc)


@router.post("/schedules/{schedule_id}/run-now")
def run_now(schedule_id: int) -> dict:
    with session_scope() as s:
        if not s.get(Schedule, schedule_id):
            raise HTTPException(404, msg("console.no_schedule", app_settings.language(), id=schedule_id))
        run = scheduler.run_now(s, schedule_id)
        return {"ok": True, "job_id": run.job.id if run.job else None, "existing": run.existing, "outside_stage": run.outside_stage}


class NewKpi(BaseModel):
    project_id: int
    name: str
    unit: str | None = None
    target: float
    direction: str = "up"
    rationale: str | None = None


class NewValue(BaseModel):
    value: float
    source: str = "manual"
    note: str | None = None


@router.get("/kpis")
def kpis() -> list[dict]:
    with session_scope() as s:
        out = []
        for k in s.scalars(select(Kpi).where(Kpi.project_id.notin_(_archived_projects(s)))
                           .order_by(Kpi.project_id, Kpi.id)):
            d = _row(k)
            d["values"] = [_row(v) for v in s.scalars(select(KpiValue).where(KpiValue.kpi_id == k.id)
                                                       .order_by(KpiValue.at))]
            out.append(d)
        return out


@router.post("/kpis")
def create_kpi(body: NewKpi) -> dict:
    with session_scope() as s:
        if not s.get(Project, body.project_id):
            raise HTTPException(404, msg("console.no_project", app_settings.language(), id=body.project_id))
        k = Kpi(**body.model_dump(), source="manual")
        s.add(k)
        s.flush()
        return _row(k)


@router.post("/kpis/{kpi_id}/values")
def record_value(kpi_id: int, body: NewValue) -> dict:
    with session_scope() as s:
        if not s.get(Kpi, kpi_id):
            raise HTTPException(404, msg("console.no_kpi", app_settings.language(), id=kpi_id))
        v = KpiValue(kpi_id=kpi_id, **body.model_dump())
        s.add(v)
        s.flush()
        return _row(v)


@router.get("/actions")
def actions() -> list[dict]:
    """The latest outside actions. `sent_by_owner` is set on an outbox item the owner sent by hand (when, the link and
    note they left, the to-do it closed), else None."""
    with session_scope() as s:
        rows = list(s.scalars(select(ActionExecution).order_by(ActionExecution.id.desc()).limit(200)))
        sent = owner_tasks.sent_records(s, rows)
        return [{**_row(a), "sent_by_owner": sent.get(a.id)} for a in rows]


class ContactConsent(BaseModel):
    consent: bool


@router.get("/crm/contacts")
def contacts() -> list[dict]:
    with session_scope() as s:
        out = []
        for c in s.scalars(select(CrmContact).order_by(CrmContact.id)):
            d = _row(c)
            d["interactions"] = [_row(i) for i in s.scalars(select(CrmInteraction).where(
                CrmInteraction.contact_id == c.id).order_by(CrmInteraction.at))]
            out.append(d)
        return out


@router.post("/crm/contacts/{contact_id}/consent")
def set_consent(contact_id: int, body: ContactConsent) -> dict:
    """Consent is recorded by the human owner only (agents cannot set it)."""
    with session_scope() as s:
        c = s.get(CrmContact, contact_id)
        if not c:
            raise HTTPException(404, msg("console.no_contact", app_settings.language(), id=contact_id))
        c.consent = body.consent
        return _row(c)
