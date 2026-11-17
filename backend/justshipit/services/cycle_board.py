"""The dashboard's read model for one business: which cycle and stage it is in, what waits for the owner, the strip of
stages, the current stage's tasks, and one card per agent at work. Read only; the sentence is the server's, in the
owner's language, and every count is counted, never a percent.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from justshipit.agents import roles
from justshipit.contracts.cycle import (
    ActionCard,
    ActionItem,
    ChangeView,
    CurrentStage,
    CycleBoardOut,
    CycleHead,
    CycleRow,
    DecisionCard,
    GateCard,
    ProposalCard,
    StageCard,
    StageChip,
    StageEstimate,
    StoppedCard,
    SummaryView,
    TaskRow,
    WaitingItem,
)
from justshipit.db.models import AgentRequest, Cycle, CycleProposal, CycleStage, Job, LLMCallRecord, Project, Task
from justshipit.i18n import Lang, msg
from justshipit.services import cycle, queue, scope, stops, work_now
from justshipit.settings import agents_config

_CURRENT = ("running", "closing", "awaiting_owner", "stopped")
# Board states whose sentence says the CEO is at work, which only a worker does.
_WORKER_MOVES = ("proposing", "revising", "closing", "retro")
_EPOCH = datetime.fromtimestamp(0, UTC)


def _iso(at: datetime | None) -> str | None:
    return at.isoformat() if at else None


def _card(draft: dict[str, Any], change: str | None = None) -> StageCard:
    return StageCard(key=draft["key"], title=draft["title"], doing=draft["doing"], owner_does=draft["owner_does"],
                     decision_points=list(draft.get("decision_points") or []),
                     estimate=StageEstimate(minutes=draft.get("est_minutes"), usd=draft.get("est_usd"),
                                            basis=draft.get("estimate_basis") or "guess"),
                     ends_when=draft.get("ends_when") or "run_settled",
                     recurring=list(draft.get("allows_kinds") or []), owner_work=list(draft.get("allows_owner_work") or []),
                     takes_over=list(draft.get("maps_existing") or []), change=change)


def _row_card(st: CycleStage) -> StageCard:
    return _card({**cycle._frozen_draft(st), "maps_existing": []})


def _proposal_card(c: Cycle, shown: CycleProposal, latest: CycleProposal) -> ProposalCard:
    changed = {x["stage_key"]: x["kind"] for x in shown.changes or [] if x["kind"] != "removed"}
    actions: list[str] = []
    if c.state == "draft" and shown.status == "ready" and latest.status != "drafting":
        actions = ["start_cycle", "revise"]
    if latest.status == "failed":
        actions.append("retry_proposal")
    return ProposalCard(cycle_id=c.id, version=shown.version, mode=c.mode, intro=shown.intro or "", reply=shown.reply,
                        stages=[_card(d, changed.get(d["key"])) for d in shown.stages or []],
                        changes=[ChangeView(**x) for x in shown.changes or []],
                        revising=latest.status == "drafting", revision_failed=latest.error if latest.status == "failed"
                        else None, since=_iso(latest.created_at), actions=actions)


def _summary(st: CycleStage) -> SummaryView | None:
    if not st.summary:
        return None
    return SummaryView(**{k: st.summary.get(k) for k in SummaryView.model_fields if k != "by"},
                       by=roles.current(st.summary_by or "server"))


def _waiting(s: Session, st: CycleStage, project_ids: list[int]) -> list[WaitingItem]:
    """What waits at the gate, by name: open decisions of the stage's work and work parked for the next stage."""
    out = [WaitingItem(kind="decision", id=r.id, title=r.title) for r in s.scalars(
        select(AgentRequest).where(AgentRequest.project_id.in_(project_ids), AgentRequest.status == "PENDING")
        .order_by(AgentRequest.id))] if project_ids else []
    from justshipit.services.work_now import job_label
    lang = _lang()
    out += [WaitingItem(kind="job", id=j.id, title=job_label(j.kind, lang)) for j in s.scalars(
        select(Job).where(Job.status == "gated", Job.business_id == st.business_id).order_by(Job.id))]
    return out


def _lang() -> Lang:
    from justshipit.services import app_settings
    return app_settings.language()


def _state(c: Cycle | None, latest: CycleProposal | None, ready: CycleProposal | None,
           current: CycleStage | None) -> str:
    if c is None:
        return "none"
    if c.closed_at is not None:
        return "stopped" if c.state == "stopped" else "none"
    if c.state == "retro":
        return "retro"
    if c.state == "running" and current is not None:
        return {"running": "running", "closing": "closing", "awaiting_owner": "gate",
                "stopped": "stage_stopped"}[current.status]
    if latest is not None and latest.status == "drafting":
        return "revising" if ready is not None else "proposing"
    if latest is not None and latest.status == "failed" and ready is None:
        return "proposal_failed"
    if c.state == "running":
        return "running"
    return "legacy" if c.mode == "adopt" else "proposal"


def _starting(s: Session, st: CycleStage) -> tuple[str | None, str | None]:
    """Why a running stage has no task yet, from its own jobs: one is running (the CEO is planning its tasks), or the
    oldest waits to be taken, which says nothing of a worker that has not taken it."""
    rows = s.execute(select(Job.status, func.min(Job.created_at)).where(
        Job.stage_id == st.id, Job.kind.in_(queue.RUN_KINDS), Job.status.in_(("queued", "running"))).group_by(Job.status)).all()
    by = {status: at for status, at in rows}
    if "running" in by:
        return "planning", _iso(by["running"])
    if "queued" in by:
        return "queued", _iso(by["queued"])
    return None, None


def board(s: Session, business_id: int, *, lang: Lang) -> CycleBoardOut:
    c = cycle.open_cycle_of(s, business_id) or s.scalar(
        select(Cycle).where(Cycle.business_id == business_id).order_by(Cycle.number.desc()).limit(1))
    latest = cycle.proposal(s, c.id) if c else None
    ready = cycle.proposal(s, c.id, status=("ready",)) if c else None
    stages = cycle.stages_of(s, c.id) if c else []
    current = next((st for st in stages if st.status in _CURRENT), None)
    state = _state(c, latest, ready, current)
    ids = [st.id for st in stages]
    counts: dict[int, dict[str, int]] = {}
    for sid, status, n in s.execute(select(Project.stage_id, Task.status, func.count()).join(Task, Task.project_id ==
                                                                                             Project.id)
                                    .where(Project.stage_id.in_(ids)).group_by(Project.stage_id, Task.status)) if ids else []:
        counts.setdefault(sid, {})[status] = n
    cost = stage_costs(s, ids)

    def chip(st: CycleStage) -> StageChip:
        n = counts.get(st.id, {})
        known = st.status != "planned"
        return StageChip(id=st.id, key=st.key, position=st.position, title=st.title, status=st.status,
                         tasks_done=n.get("DONE", 0) if known else None, tasks_total=sum(n.values()) if known else None,
                         is_current=current is not None and st.id == current.id, adopted=st.adopted)

    strip = [chip(st) for st in stages if st.end_cause != "revised_away"]
    waiting: list[Any] = []
    if latest is not None and (c.state == "draft" or latest.status in ("drafting", "failed")):
        shown = ready or latest
        if shown.status != "drafting" or ready is not None:
            waiting.append(_proposal_card(c, shown, latest))
    cur_view = None
    if current is not None:
        pids = [p.id for p in cycle.stage_projects(s, current.id)]
        tasks = list(s.scalars(select(Task).where(Task.project_id.in_(pids)).order_by(Task.id))) if pids else []
        if current.status == "awaiting_owner":
            nxt = cycle._next_planned(s, current)
            revising = latest is not None and latest.status == "drafting"
            acts = [] if revising else [("start_next" if nxt else "finish_cycle"), "revise", "retry_stage"]
            reply = ready.reply if ready is not None and current.awaiting_at and ready.created_at > current.awaiting_at \
                else None
            waiting.insert(0, GateCard(cycle_id=c.id, stage_id=current.id, stage_title=current.title, round=current.round,
                                       summary=_summary(current), next_stage=_row_card(nxt) if nxt else None,
                                       proposal_version=ready.version if ready is not None else 0,
                                       waiting=_waiting(s, current, pids), reply=reply, actions=[*acts, "stop_here"]))
        elif current.status == "stopped":
            waiting.insert(0, StoppedCard(cycle_id=c.id, stage_id=current.id, stage_title=current.title,
                                          waiting=_waiting(s, current, pids), actions=["resume_stage", "stop_here"]))
        waiting += [DecisionCard(request_id=r.id, title=r.title, project_id=r.project_id, blocking=r.blocking)
                    for r in s.scalars(select(AgentRequest).where(AgentRequest.project_id.in_(pids),
                                                                  AgentRequest.status == "PENDING")
                                       .order_by(AgentRequest.blocking.desc(), AgentRequest.id))] if pids else []
        starting, starting_since = (_starting(s, current) if not tasks and current.status == "running" else (None, None))
        acts = {"running": ["stop_stage", "close_stage_now"], "stopped": ["resume_stage"]}.get(current.status, [])
        if current.close_requested_at is not None:
            acts = [a for a in acts if a != "close_stage_now"]
        cur_view = CurrentStage(
            stage=chip(current), doing=current.doing, owner_does=current.owner_does,
            estimate=StageEstimate(minutes=current.est_minutes, usd=current.est_usd, basis=current.estimate_basis),
            recurring=list(current.allows_kinds or []), owner_work=list(current.allows_owner_work or []),
            tasks=[TaskRow(id=t.id, ref=f"T-{t.id}", title=t.title, agent=t.owner, status=t.status,
                           started_at=_iso(t.started_at), project_id=t.project_id) for t in tasks],
            tasks_done=sum(t.status == "DONE" for t in tasks), tasks_total=len(tasks),
            cost_usd=round(float(cost.get(current.id) or 0.0), 4), started_at=_iso(current.started_at),
            close_requested=current.close_requested_at is not None,
            summary=_summary(current) if current.status == "awaiting_owner" else None, actions=acts,
            starting=starting, starting_since=starting_since)

    by_stage = {st.id: st for st in stages}
    hidden = scope.hidden_projects(s, business_id=business_id)
    rows = [r for r in work_now.rows(s, hidden) if r.business_id == business_id]
    items: dict[str, list[ActionItem]] = {}
    for r in sorted(rows, key=lambda r: r.started_at or _EPOCH, reverse=True):
        items.setdefault(r.agent, []).append(ActionItem(
            cycle_number=c.number if c and r.stage_id in by_stage else None,
            stage_id=r.stage_id, stage_title=by_stage[r.stage_id].title if r.stage_id in by_stage else None,
            kind=r.kind, task_id=r.task_id, dev_job_id=r.dev_job_id, job_id=r.job_id,
            title=work_now.job_label(r.job_kind, lang) if r.kind == "job" else r.title,
            project_id=r.project_id, started_at=_iso(r.started_at), spent_usd=r.spent_usd))
    stopping, stopped = {r.agent for r in rows if r.stopping}, stops.read(s).agents
    working = [ActionCard(agent=agent, items=its, stopping=agent in stopping, stopped=agent in stopped)
               for agent, its in items.items()]
    idle_set = set(agents_config()) - set(items)
    idle = len(idle_set)
    since = {"proposing": latest and latest.created_at, "revising": latest and latest.created_at,
             "running": current and current.started_at, "closing": current and current.closing_at,
             "gate": current and current.awaiting_at}.get(state)
    # While no worker runs nobody is working, whatever the rows hold: the sentence says the company waits for it.
    alive = queue.last_worker_seen(s)[1]
    who = msg("cycle.working", lang, n=len(items)) if alive else msg("cycle.held", lang)
    sentence = msg(f"cycle.sentence.{state}", lang, n=c.number if c else 0,
                   stage=current.title if current else "", working=who)
    if not alive and state in _WORKER_MOVES:
        sentence = f"{sentence} {msg('cycle.sentence.held', lang)}"
    return CycleBoardOut(state=state, sentence=sentence, since=_iso(since) if isinstance(since, datetime) else None,
                         cycle=_head(c) if c else None, waiting=waiting, strip=strip, current=cur_view,
                         working=working, idle_agents=idle, idle_stopped=len(idle_set & set(stopped)), history=history(s, business_id, exclude=c.id if c and
                                                                             c.closed_at is None else None))


def stage_costs(s: Session, stage_ids: list[int]) -> dict[int, float]:
    """What each stage's calls cost at list price: the calls made for the stage, and the calls of the projects made
    for it, from the same ledger the agent cards, Agents and Costs read."""
    if not stage_ids:
        return {}
    sid = func.coalesce(LLMCallRecord.stage_id, Project.stage_id)
    return {k: float(v or 0.0) for k, v in s.execute(
        select(sid, func.sum(LLMCallRecord.cost_usd)).outerjoin(Project, Project.id == LLMCallRecord.project_id)
        .where(sid.in_(stage_ids)).group_by(sid))}


def _head(c: Cycle) -> CycleHead:
    return CycleHead(id=c.id, number=c.number, state=c.state, mode=c.mode, starts_on=c.starts_on.isoformat(),
                     ends_on=c.ends_on.isoformat(), started_at=_iso(c.started_at))


def history(s: Session, business_id: int, *, exclude: int | None = None) -> list[CycleRow]:
    """The business's cycles, newest first; the open one left out when `exclude` names it."""
    out = []
    for c in s.scalars(select(Cycle).where(Cycle.business_id == business_id).order_by(Cycle.number.desc())):
        if c.id == exclude:
            continue
        stages = cycle.stages_of(s, c.id)
        spent = sum(stage_costs(s, [st.id for st in stages]).values())
        out.append(CycleRow(id=c.id, number=c.number, state=c.state, started_at=_iso(c.started_at),
                            closed_at=_iso(c.closed_at), stages_done=sum(st.status == "done" for st in stages),
                            stages_total=sum(st.end_cause != "revised_away" for st in stages),
                            cost_usd=round(float(spent or 0.0), 4)))
    return out


def detail(s: Session, cycle_id: int, *, lang: Lang) -> dict[str, Any]:
    """One cycle in full for the Business page: every proposal version with what changed, and every stage with its
    summary."""
    c = s.get(Cycle, cycle_id)
    if c is None:
        raise cycle.NotFound(msg("cycle.refuse.no_cycle", lang, id=cycle_id))
    proposals = s.scalars(select(CycleProposal).where(CycleProposal.cycle_id == c.id).order_by(CycleProposal.version))
    return {"cycle": _head(c).model_dump(), "retro": c.retro, "stop_reason": c.stop_reason,
            "proposals": [{"version": p.version, "status": p.status, "owner_note": p.owner_note, "intro": p.intro,
                           "reply": p.reply, "stages": [_card(d).model_dump() for d in p.stages or []],
                           "changes": p.changes, "error": p.error, "created_at": _iso(p.created_at)} for p in proposals],
            "stages": [{**StageChip(id=st.id, key=st.key, position=st.position, title=st.title, status=st.status,
                                    tasks_done=None, tasks_total=None, is_current=False, adopted=st.adopted).model_dump(),
                        "end_cause": st.end_cause, "started_at": _iso(st.started_at),
                        "finished_at": _iso(st.finished_at), "summary": (s_ := _summary(st)) and s_.model_dump()}
                       for st in cycle.stages_of(s, c.id)]}
