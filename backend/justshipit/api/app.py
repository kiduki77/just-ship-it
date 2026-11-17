"""Company Control Plane API + web console host.

All JSON routes live under /api. The built React console (web/dist) is served at /.
"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from types import FrameType
from typing import Any

import psycopg
import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from sqlalchemy import func, or_, select

from justshipit import staffing
from justshipit.api import access
from justshipit.api.rows import project_row, row
from justshipit.contracts import owner_text
from justshipit.contracts.plan import PlanRevision
from justshipit.contracts.requests import HumanDecision, HumanResponse
from justshipit.db.models import (
    AgentRequest,
    Artifact,
    BudgetMonth,
    Business,
    Decision,
    DevJob,
    Evidence,
    LLMCallRecord,
    Meeting,
    Plan,
    Project,
    Spec,
    Task,
)
from justshipit.db.session import session_scope
from justshipit.engineering import status as dev_status
from justshipit.i18n import msg
from justshipit.i18n.match import responders
from justshipit.services import (
    activity,
    app_settings,
    budget,
    events,
    evidence,
    project_state,
    registry,
    requests,
    roadmap,
    scope,
    strengths,
    tasks,
    work_path,
)
from justshipit.services import projects as project_service
from justshipit.settings import agents_config, company_config, settings

# The API's description lives under /api, so it needs the token like every other route that describes or serves
# data. The pages that draw it (Swagger UI, ReDoc) load their code from another site into this origin, so neither is
# served.
app = FastAPI(title="Just Ship It", docs_url=None, openapi_url="/api/openapi.json", redoc_url=None,
              swagger_ui_oauth2_redirect_url=None)
app.add_middleware(access.AccessGuard)
# `ship api` clears it when it binds off loopback; while set, a Host other than a loopback name is refused.
app.state.loopback_only = True
api = APIRouter(prefix="/api")


class NewProject(BaseModel):
    goal: str
    name: str | None = None
    slug: str | None = None
    budget_usd: float | None = None
    business_id: int | None = None


@api.get("/dashboard")
def dashboard() -> dict:
    with session_scope() as s:
        live = select(Project.id).where(Project.status != "ARCHIVED", ~Project.name.startswith("[TEST]"))
        tasks_by_status = dict(s.execute(select(Task.status, func.count()).where(Task.project_id.in_(live))
                                         .group_by(Task.status)).all())
        month = s.scalar(select(BudgetMonth).order_by(BudgetMonth.month.desc()))
        running = s.execute(select(Task.owner, Task.title, Task.project_id).where(Task.status.in_(
            ["RUNNING", "REVIEW", "REWORK"]))).all()
        return {
            "company": company_config()["company"],
            "limits": company_config()["limits"],
            "projects": _project_summaries(s),
            "tasks_by_status": tasks_by_status,
            "pending_requests": len(requests.pending(s)),
            "budget": row(month) if month else None,
            "agents": [{"id": k, **{x: v.get(x) for x in ("title", "department", "provider", "model")},
                        "busy": [{"title": owner_text.title(t), "project_id": p} for o, t, p in running if o == k]}
                       for k, v in agents_config().items()],
        }


@api.post("/projects")
def create_project(body: NewProject) -> dict:
    """A project of a business the console names joins it, once its plan is approved. With no business named the
    project starts a business of its own (the command line's way): the console always names one, so it never makes a
    business without a plan by accident."""
    lang = app_settings.language()
    with session_scope() as s:
        business = None
        if body.business_id is not None:
            ref = f"B-{body.business_id}"
            business = s.get(Business, body.business_id)
            if business is None:
                raise HTTPException(404, msg("cleanup.refuse.no_business", lang, business=ref))
            if business.status == "ARCHIVED":
                raise HTTPException(409, msg("work.refuse.archived_business", lang, ref=ref))
            if roadmap.approved_brief(s, business.id) is None:
                raise HTTPException(409, msg("work.refuse.no_plan", lang, ref=ref))
        return row(project_service.create(s, body.goal, name=body.name, slug=body.slug, budget_usd=body.budget_usd,
                                          business=business, by_owner=True))


def _project_summaries(s, include_archived: bool = False, business_id: int | None = None) -> list[dict]:
    """Projects with the counts the console's project navigation needs; with `business_id`, that business's only.
    Without it, an archived business's projects stay out, archived or not (services/scope.py)."""
    tasks_by = {}
    for pid, st, n in s.execute(select(Task.project_id, Task.status, func.count()).group_by(Task.project_id, Task.status)):
        tasks_by.setdefault(pid, {})[st] = n
    pending = {}
    asks = {}
    for r in requests.pending(s):
        if r.project_id:
            c = pending.setdefault(r.project_id, {"pending": 0, "blocking": 0})
            c["pending"] += 1
            c["blocking"] += int(bool(r.blocking))
            if (ask := requests.budget_ask(r)) and r.project_id not in asks:
                asks[r.project_id] = ask
    dev = dev_status.by_project(s)
    out = []
    q = select(Project).order_by(Project.id.desc())
    if business_id is not None:
        q = q.where(Project.business_id == business_id)
    else:
        q = q.where(or_(Project.business_id.is_(None), ~scope.of_archived_business()))
    for p in s.scalars(q if include_archived else q.where(Project.status != "ARCHIVED")):
        t = tasks_by.get(p.id, {})
        waits = pending.get(p.id, {"pending": 0, "blocking": 0})
        paused = bool((p.context or {}).get("paused"))
        d = dev.get(p.id, dev_status.NO_DEV)
        stage = dev_status.project_stage(p.status, d)
        state, needs_worker = project_state.owner_state(p.status, stage, paused=paused, pending=waits["pending"])
        # Cancelled tasks are not work left to do, nor is work a later task redid, so neither is in the denominator.
        out.append({**row(p), "is_test": p.name.startswith("[TEST]"), "paused": paused,
                    "tasks_total": sum(n for st, n in t.items() if st not in ("CANCELLED", "REPLACED")),
                    "tasks_done": t.get("DONE", 0), "tasks_active": sum(t.get(k, 0) for k in ("RUNNING", "REVIEW", "REWORK")),
                    **waits, **d, "stage": stage, "owner_state": state, "needs_worker": needs_worker,
                    "dev_hold_usd": budget.dev_hold(s, p.id), "budget_short": budget.retry_blocked(s, p),
                    "budget_ask": asks.get(p.id)})
    return out




@api.get("/projects")
def list_projects(include_archived: bool = False, business_id: int | None = None) -> list[dict]:
    with session_scope() as s:
        return _project_summaries(s, include_archived, business_id)


class ArchiveAsk(BaseModel):
    """`cancel_waiting`: the owner's one confirm also cancels the project's jobs that have not started."""
    cancel_waiting: bool = False


@api.get("/projects/{project_id}/archive")
def archive_preview(project_id: int) -> dict:
    """What archiving the project with the one confirm would cancel now: its jobs that have not started."""
    with session_scope() as s:
        if s.get(Project, project_id) is None:
            raise HTTPException(404, msg("console.no_project", app_settings.language(), id=project_id))
        return {"cancels_jobs": [j.id for j in project_service.waiting_jobs(s, project_id)]}


@api.post("/projects/{project_id}/archive")
def archive_project(project_id: int, body: ArchiveAsk | None = None) -> dict:
    with session_scope() as s:
        try:
            return project_service.archive(s, project_id, cancel_waiting=bool(body and body.cancel_waiting))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc


_OWNER_DECIDED = ("APPROVED", "ANSWERED", "REJECTED", "DEFERRED")


def _decided(decisions: list[Decision], reqs: list[AgentRequest]) -> list[dict]:
    """Every decision made in the project, newest first: the owner's answers to requests, the requests the CEO decided
    under a door rule, and the CEO's recorded decisions. A policy's routine auto-approval, a withdrawn, expired or
    refused request is not a decision anyone made."""
    out = [{"kind": "request", "id": r.id, "ref": requests.display_id(r.id), "title": owner_text.title(r.title), "outcome": r.status,
            "chosen_option": (r.response or {}).get("chosen_option"),
            "by": "project_manager" if r.status == "AUTO_APPROVED" else "owner", "at": r.resolved_at}
           for r in reqs if r.status in _OWNER_DECIDED
           or (r.status == "AUTO_APPROVED" and requests.decided_by_project_manager(r))]
    out += [{"kind": "decision", "id": d.id, "ref": f"DEC-{d.id}", "title": owner_text.title(d.title), "outcome": d.decision,
             "chosen_option": None, "by": "project_manager", "at": d.created_at}
            for d in decisions if d.status != "SUPERSEDED"]
    return sorted(out, key=lambda x: (x["at"] is not None, x["at"] or 0, x["id"]), reverse=True)


@api.get("/projects/{project_id}")
def get_project(project_id: int) -> dict:
    lang, mine = app_settings.reader()
    with session_scope() as s:
        p = s.get(Project, project_id)
        if not p:
            raise HTTPException(404, msg("console.no_project", lang, id=project_id))
        decisions = list(s.scalars(select(Decision).where(Decision.project_id == p.id).order_by(Decision.id)))
        reqs = list(s.scalars(select(AgentRequest).where(AgentRequest.project_id == p.id).order_by(AgentRequest.id)))
        return {
            "project": project_row(s, p),
            "tasks": [row(t) for t in s.scalars(select(Task).where(Task.project_id == p.id).order_by(Task.id))],
            # cited: EV ids the rationale mentions, so a decision with no linked evidence still shows its basis.
            "decisions": [{**row(d), "cited_evidence_ids": evidence.cited(d.rationale)} for d in decisions],
            "decided": _decided(decisions, reqs),
            "meetings": [row(m) for m in s.scalars(select(Meeting).where(Meeting.project_id == p.id)
                                                   .order_by(Meeting.id))],
            "requests": [requests.read_model(s, r, lang, mine) for r in reqs],
            "artifacts": [row(a) for a in s.scalars(select(Artifact).where(Artifact.project_id == p.id)
                                                    .order_by(Artifact.id))],
            "usage": [dict(zip(("agent", "calls", "cost_usd", "input_tokens", "output_tokens"), r)) for r in s.execute(
                select(LLMCallRecord.agent, func.count(), func.sum(LLMCallRecord.cost_usd),
                       func.sum(LLMCallRecord.input_tokens), func.sum(LLMCallRecord.output_tokens))
                .where(LLMCallRecord.project_id == p.id).group_by(LLMCallRecord.agent)).all()],
        }


@api.get("/projects/{project_id}/evidence")
def get_evidence(project_id: int) -> list[dict]:
    with session_scope() as s:
        return [row(e) for e in s.scalars(select(Evidence).where(Evidence.project_id == project_id)
                                           .order_by(Evidence.id))]


@api.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: int) -> dict:
    with session_scope() as s:
        a = s.get(Artifact, artifact_id)
        if not a:
            raise HTTPException(404, msg("console.no_artifact", app_settings.language(), id=artifact_id))
        content = registry.read(a.path)
        if a.kind == "task_output":
            t = s.scalar(select(Task).where(Task.artifact_id == a.id))
            content = tasks.document(content, (t.result or {}).get("evidence_ids", []) if t else [])
        if a.kind == "report":
            p = s.get(Project, a.project_id) if a.project_id else None
            fit = strengths.report_fit(((p.context or {}).get("report_areas") or {}) if p else {},
                                       app_settings.reader()[1])
            return {**row(a), "content": content, "fit": fit}
        return {**row(a), "content": content}


@api.get("/inbox")
def inbox(include_resolved: bool = False, project_id: int | None = None, business_id: int | None = None) -> list[dict]:
    lang, mine = app_settings.reader()
    with session_scope() as s:
        if include_resolved:
            q = select(AgentRequest).order_by(AgentRequest.id.desc()).limit(200)
            if project_id is not None:
                q = q.where(AgentRequest.project_id == project_id)
            if business_id is not None:
                q = q.where(requests.in_business(business_id))
            return [requests.read_model(s, r, lang, mine) for r in s.scalars(q)]
        return [requests.read_model(s, r, lang, mine) for r in requests.pending(s, project_id, business_id)]


@api.get("/requests/{request_id}")
def get_request(request_id: int) -> dict:
    """One request by id, for deep links: the console must show exactly this request or say it is missing."""
    with session_scope() as s:
        r = s.get(AgentRequest, request_id)
        if r is None:
            raise HTTPException(404, msg("console.no_request", app_settings.language(), id=request_id))
        # The console switches to this business when the link came from another one; None is a company-wide request.
        p = s.get(Project, r.project_id) if r.project_id else None
        from justshipit.engineering import plans, wireflow
        from justshipit.engineering.specs import card_spec
        # `plan`: the plan behind a spec card's spec; `plan_doc`: the plan a plan card asks to approve.
        card = requests.read_model(s, r, *app_settings.reader())
        # The card never depends on its context: one that cannot be read says so, and the block offers to try again.
        ctx = work_path.card_or_none(s, r, says=card["report"]["on_approve"])
        return {**card, "business_id": p.business_id if p else None,
                "spec": card_spec(s, r), "screens": wireflow.for_card(s, r), "plan": plans.spec_plan(s, r),
                "plan_doc": plans.card_plan(s, r), "context": ctx.model_dump(mode="json") if ctx else {"error": True}}


class OwnerResponse(HumanResponse):
    """An answer as the API takes it. `responded_by` is the caller's word, so the API records only the owner or an
    answer made on the owner's behalf: the system's own answers and the Project Manager's under a decision rule are
    written by the server (services/requests.py), never claimed by a caller."""

    @field_validator("responded_by")
    @classmethod
    def _the_owner_or_on_the_owners_behalf(cls, by: str) -> str:
        if by not in responders.FROM_THE_API:
            raise ValueError(f"must be one of {', '.join(responders.FROM_THE_API)}")
        return by


@api.post("/requests/{request_id}/respond")
def respond(request_id: int, body: OwnerResponse) -> dict:
    # A yes from the console names the card revision it showed; one without it comes from a page built before
    # revisions, which cannot tell whether the card changed under the owner.
    lang, mine = app_settings.reader()
    if body.decision in (HumanDecision.APPROVE, HumanDecision.ANSWER) and body.revision is None:
        raise HTTPException(409, msg("request.screen_old", lang))
    with session_scope() as s:
        try:
            return requests.read_model(s, requests.respond(s, request_id, body), lang, mine)
        except requests.ResponseRejected as exc:
            raise HTTPException(409, exc.words(lang)) from exc


@api.post("/plans/{plan_id}/revise")
def revise_plan(plan_id: int, body: PlanRevision) -> dict:
    """The owner revises an approved product plan before its spec is approved: it is written again from the note."""
    from justshipit.engineering import plans
    lang = app_settings.language()
    with activity.as_actor("owner"), session_scope() as s:
        if s.get(Plan, plan_id) is None:
            raise HTTPException(404, msg("plan.not_found", lang))
        try:
            plan = plans.revise(s, plan_id, body.note, lang)
        except requests.ResponseRejected as exc:
            raise HTTPException(409, exc.words(lang)) from exc
        return {"id": plan.id, "plan_key": plan.plan_key, "status": plan.status}


@api.post("/requests/{request_id}/reopen")
def reopen_request(request_id: int) -> dict:
    """Put a decision the owner held or declined back to decide, when nothing has followed from it yet."""
    lang, mine = app_settings.reader()
    with activity.as_actor("owner"), session_scope() as s:
        try:
            return requests.read_model(s, requests.reopen(s, request_id), lang, mine)
        except requests.ResponseRejected as exc:
            raise HTTPException(409, exc.words(lang)) from exc


@api.post("/requests/{request_id}/drop-notes")
def drop_notes(request_id: int) -> dict:
    """Give up on a spec sent back with notes on its screens: the notes go and the spec stays declined."""
    from justshipit.engineering import specs
    lang, mine = app_settings.reader()
    with activity.as_actor("owner"), session_scope() as s:
        req = s.get(AgentRequest, request_id)
        if req is None:
            raise HTTPException(404, msg("console.no_request", lang, id=request_id))
        try:
            return requests.read_model(s, specs.drop_notes(s, req, lang), lang, mine)
        except requests.ResponseRejected as exc:
            raise HTTPException(409, exc.words(lang)) from exc


@api.get("/engineering")
def engineering(project_id: int | None = None, business_id: int | None = None) -> dict:
    from justshipit.engineering import status, team, worker
    from justshipit.services import app_settings
    lang = app_settings.language()
    with session_scope() as s:
        specs = select(Spec).order_by(Spec.id.desc())
        jobs = select(DevJob).order_by(DevJob.id.desc())
        if project_id is not None:
            specs = specs.where(Spec.project_id == project_id)
            jobs = jobs.where(DevJob.spec_id.in_(select(Spec.id).where(Spec.project_id == project_id)))
        else:
            mine = status.listed_projects(business_id)
            specs = specs.where(Spec.project_id.in_(mine))
            jobs = jobs.where(DevJob.spec_id.in_(select(Spec.id).where(Spec.project_id.in_(mine))))
        listed = list(s.scalars(jobs))
        teams = team.of_builds(s, [x.id for x in listed])
        return {"specs": [{**row(x), "state": status.spec_state(s, x)} for x in s.scalars(specs)],
                "jobs": [{**row(x), "steps": status.steps(x, lang), "owner_actions": worker.owner_actions(s, x),
                          "team": teams[x.id]} for x in listed],
                "board": status.board(s, project_id, business_id, lang=lang)}


def _business_calls(s, business_id: int):
    """The model calls one business's view shows, by the rule every console list applies (services/scope.py)."""
    hidden = scope.hidden_projects(s, business_id=business_id)
    return hidden, scope.rows(hidden, LLMCallRecord.project_id, LLMCallRecord.business_id, business_id)


@api.get("/crews")
def crews(project_id: int | None = None, business_id: int | None = None,
          state: staffing.Standing | None = None) -> dict:
    """Who worked each task and build and why (docs/design/staffing-structure.md section 9): one project's, one
    business's, or every business's but the archived ones, by the rule every console list applies."""
    with session_scope() as s:
        listed = [project_id] if project_id is not None else select(Project.id).where(
            Project.id.notin_(list(scope.hidden_projects(s, business_id=business_id))))
        return {"crews": [asdict(v) for v in staffing.crew_views(s, listed, app_settings.language(), standing=state)]}


@api.get("/usage")
def usage(business_id: int | None = None) -> dict:
    """Usage by agent; with business_id, the calls that business's view shows. The months stay the company's."""
    with session_scope() as s:
        q = select(LLMCallRecord.agent, LLMCallRecord.provider, func.count(),
                   func.count().filter(LLMCallRecord.status != "ok"),
                   func.sum(LLMCallRecord.input_tokens), func.sum(LLMCallRecord.output_tokens),
                   func.sum(LLMCallRecord.cost_usd))
        if business_id is not None:
            q = q.where(_business_calls(s, business_id)[1])
        by_agent = s.execute(q.group_by(LLMCallRecord.agent, LLMCallRecord.provider)).all()
        return {
            "months": [row(m) for m in s.scalars(select(BudgetMonth).order_by(BudgetMonth.month.desc()))],
            "by_agent": [dict(zip(("agent", "provider", "calls", "errors", "input_tokens", "output_tokens",
                                   "cost_usd"), r)) for r in by_agent],
        }


@api.get("/health")
def health() -> dict:
    """What is up: this API (it answered), the database, and a worker that reported within a lease. Each part
    answers for itself, so a database outage still returns 200 with `database: false`."""
    from justshipit.services import queue
    try:
        with session_scope(lease_checked=False) as s:
            seen, alive = queue.last_worker_seen(s)
        database = True
    except Exception:  # noqa: BLE001 - reported, not raised
        database, seen, alive = False, None, False
    return {"ok": database and alive, "api": True, "database": database,
            "worker": {"alive": alive, "last_seen": seen}}


_SSE_WAKE_S = 15
_SSE_SLICE_S = 0.5  # how soon an open stream notices the console is stopping
_streams_stop = threading.Event()
# Each open stream keeps one thread reading its LISTEN connection, so streams get threads of their own instead of
# filling the event loop's small default pool.
_SSE_POOL = ThreadPoolExecutor(max_workers=64, thread_name_prefix="sse")


class ConsoleServer(uvicorn.Server):
    """Uvicorn waits for every open connection before it stops, and a browser tab holds /api/events open for as long
    as it is open: Ctrl+C took 21 s and ended in a traceback. The first signal now ends the streams too."""

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        _streams_stop.set()
        super().handle_exit(sig, frame)


def server_config(**kwargs: Any) -> uvicorn.Config:
    _streams_stop.clear()
    # a connection that still will not close is cut after this many seconds
    return uvicorn.Config(app, timeout_graceful_shutdown=5, **kwargs)


@api.get("/events")
async def stream_events(request: Request) -> StreamingResponse:
    """Server-Sent Events relayed from Postgres NOTIFY, plus a `workers` change when a worker dies or a run
    stalls: those come from time passing, so no row change would announce them. The access guard checks the
    token once, at connect, so the stream checks it again at every wake and ends once `ship api-token rotate`
    has replaced it. While the database is unreachable the stream ends, and the browser reconnects.

    The listening connection is a sync one read in short thread slices: psycopg's async connection needs a selector
    event loop, which uvicorn on Windows may not run."""
    from justshipit.api.console import liveness

    def check():
        try:
            with session_scope() as s:
                return liveness(s)
        except Exception:  # noqa: BLE001 - a database outage must not cut the relay of events already listened for
            return None

    def listen() -> psycopg.Connection:
        conn = psycopg.connect(settings().checkpoint_dsn, autocommit=True, connect_timeout=5)
        conn.execute(f"LISTEN {events.CHANNEL}")
        return conn

    async def gen():
        loop = asyncio.get_running_loop()
        try:
            conn = await loop.run_in_executor(_SSE_POOL, listen)
        except psycopg.Error:
            return
        reading = threading.Lock()  # libpq must not be closed under a read still running on a pool thread

        def read(timeout: float) -> list:
            with reading:
                return list(conn.notifies(timeout=timeout, stop_after=1))

        def close() -> None:
            with reading:
                conn.close()

        try:
            yield "event: ready\ndata: {}\n\n"
            last = await loop.run_in_executor(_SSE_POOL, check)
            waited = 0.0
            while True:
                slice_s = min(_SSE_SLICE_S, _SSE_WAKE_S)
                notes = await loop.run_in_executor(_SSE_POOL, read, slice_s)
                if _streams_stop.is_set():
                    return
                if not notes:
                    waited += slice_s
                    if waited < _SSE_WAKE_S:
                        continue
                    waited = 0.0
                if not access.presents_token(request, access.current_token(settings().var_dir)):
                    return
                if not notes:
                    if (now := await loop.run_in_executor(_SSE_POOL, check)) != last:
                        last = now
                        yield 'data: {"kind": "change", "table": "workers"}\n\n'
                    else:
                        yield ": keepalive\n\n"
                    continue
                for note in notes:
                    yield f"data: {note.payload}\n\n"
                await asyncio.sleep(0)
        except psycopg.Error:
            return
        finally:
            # Not awaited: a cancelled stream's cleanup would be cancelled at its first await. The pool thread waits
            # for a read that the cancel left running, then closes.
            _SSE_POOL.submit(close)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _include_extensions() -> None:
    """Routers contributed by later modules (skills, schedules, kpis, actions)."""
    import importlib
    for mod in ("justshipit.api.capability", "justshipit.api.operations", "justshipit.api.console", "justshipit.api.kickoff",
                "justshipit.api.owner_tasks", "justshipit.api.ideas", "justshipit.api.people", "justshipit.api.growth",
                "justshipit.api.today", "justshipit.api.documents", "justshipit.api.cycles",
                "justshipit.api.looks", "justshipit.api.chat", "justshipit.api.profile",
                "justshipit.api.work_path", "justshipit.api.tracking", "justshipit.api.kit"):
        try:
            m = importlib.import_module(mod)
        except ModuleNotFoundError as exc:
            if exc.name != mod:
                raise
            continue
        api.include_router(m.router)


_include_extensions()
from justshipit.api import system as _system  # noqa: E402

api.include_router(_system.router)
app.include_router(api)

_dist = settings().root / "web" / "dist"
if (_dist / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

# web/dist is not committed, so a fresh checkout has an API but no console until it is built.
_NO_BUILD = """<!doctype html><html lang="en"><meta charset="utf-8"><title>Just Ship It</title>
<body style="font-family:system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;line-height:1.6">
<h1>The console isn't built yet</h1>
<p>The API is running, but the web console's files (<code>web/dist</code>) are missing. Run the command below in
the repository folder, then reload this page.</p>
<pre style="background:#f3f4f6;padding:.75rem 1rem;border-radius:6px">cd web &amp;&amp; npm ci &amp;&amp; npm run build</pre>
</body></html>"""


def _console_file(path: str) -> Path | None:
    """The file of the build a request path names, or None. The path is request-controlled, and joining it to the
    build can name another place before anything is looked up: on Windows `//host/share/x` is a UNC path, and looking
    it up logs this PC in to that host. So a path that is not plain names under the build is refused from its text
    alone. A plain one is resolved, symlinks included, and served only from inside the build."""
    if not path or path[0] in "/\\" or "\\" in path or ":" in path or ".." in path.split("/"):
        return None
    try:
        target = (_dist / path).resolve()
        return target if target.is_relative_to(_dist.resolve()) and target.is_file() else None
    except (OSError, ValueError):
        return None


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    # An address under /api that no route took is not a console page: a data call must never be answered with HTML,
    # which a browser may keep and replay for that address. That holds before the console is built too.
    if path == "api" or path.startswith("api/"):
        raise HTTPException(404, "No such address.")
    if not (_dist / "index.html").is_file():
        return HTMLResponse(_NO_BUILD, status_code=503)
    if (target := _console_file(path)) is not None:
        return FileResponse(target)
    return FileResponse(_dist / "index.html", headers={"Cache-Control": "no-cache"})
