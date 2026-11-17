"""The owner's cycle. The CEO proposes the stages; the owner starts one at a time; the company stops after each stage with
a summary and waits for the owner. What a started stage lists runs by itself; nothing else does (services/gate.py).

The only writer of `cycles`, `cycle_stages` and `cycle_proposals`, and of when parked work is released or closed (the
job rows stay queue.py's). `decide` is the owner's whole write surface, `tick` the system's only driver, and the
`store_*` functions are where the CEO's answers land. Every move of a stage goes through `_move` and the transition
table, and every step is one transaction that also holds the jobs it enqueues, so a crash repeats harmlessly.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from justshipit.agents import roles
from justshipit.contracts import notify as notices
from justshipit.contracts.cycle import (
    CycleProposalOut,
    CycleRetroOut,
    CycleRevisionOut,
    StageDraft,
    StageSummaryOut,
)
from justshipit.contracts.handoff import DEV_JOB_SETTLED
from justshipit.db.models import (
    Bet,
    Business,
    Cycle,
    CycleProposal,
    CycleStage,
    DevJob,
    Evidence,
    Job,
    LLMCallRecord,
    Project,
    Schedule,
    Spec,
    Task,
)
from justshipit.i18n import Lang, msg
from justshipit.services import activity, app_settings, evidence, gate, queue, roadmap, scope
from justshipit.services.control import ControlError, NotFound

STAGE_TRANSITIONS: dict[str, frozenset[str]] = {
    "planned": frozenset({"running", "skipped"}),
    "running": frozenset({"closing", "stopped"}),
    "closing": frozenset({"awaiting_owner", "stopped"}),
    "awaiting_owner": frozenset({"done", "running"}),
    "stopped": frozenset({"running", "done"}),   # done: the owner stops the cycle there; nothing more is spent
    "done": frozenset(),
    "skipped": frozenset({"planned"}),   # planned: a stage the owner took out and then asked for again (_follow_planned)
}
_STAMP = {"running": "started_at", "closing": "closing_at", "awaiting_owner": "awaiting_at", "done": "finished_at",
          "skipped": "finished_at"}
SETTLED_PROJECT = ("DONE", "FAILED", "ARCHIVED")
LIVE_PROJECT = ("CREATED", "RUNNING", "WAITING_HUMAN", "PAUSED")
# How often the CEO is asked again for one proposal, summary or retro before the server writes its own.
PROJECT_MANAGER_ROUNDS = 3
# The CEO's own cycle jobs are stamped with a stage for their cost, but a revision written mid-stage holds no stage open.
_PROJECT_MANAGER_KINDS = tuple(k for k, r in gate.POLICY.items() if r.gate is gate.Gate.CYCLE)
_CONFIDENCE = ("low", "mid", "high")
_CAP = {evidence.Grade.FIRM: "high", evidence.Grade.SOURCED: "mid", evidence.Grade.ASSUMED: "low"}


class StaleDecision(ControlError):
    """The card the owner clicked is old: the cycle moved on since. The console answers 409 and reads the board again."""


# ------------------------------------------------------------------ the owner's decisions, as data

class StartCycle(BaseModel):
    kind: Literal["start_cycle"] = "start_cycle"
    version: int


class Revise(BaseModel):
    kind: Literal["revise"] = "revise"
    note: str = Field(min_length=1, max_length=4000)
    base_version: int


class RetryProposal(BaseModel):
    kind: Literal["retry_proposal"] = "retry_proposal"
    version: int


class StartNext(BaseModel):
    """`version`: the proposal version the gate card showed (GateCard.proposal_version). The next stage's row follows
    every new version, so a click on a card from before a revision would start what the owner did not read."""
    kind: Literal["start_next"] = "start_next"
    after_stage_id: int
    version: int


class FinishCycle(BaseModel):
    kind: Literal["finish_cycle"] = "finish_cycle"
    after_stage_id: int


class StopHere(BaseModel):
    kind: Literal["stop_here"] = "stop_here"
    after_stage_id: int


class RetryStage(BaseModel):
    kind: Literal["retry_stage"] = "retry_stage"
    stage_id: int


class StopStage(BaseModel):
    kind: Literal["stop_stage"] = "stop_stage"
    stage_id: int


class ResumeStage(BaseModel):
    kind: Literal["resume_stage"] = "resume_stage"
    stage_id: int


class CloseStageNow(BaseModel):
    kind: Literal["close_stage_now"] = "close_stage_now"
    stage_id: int


Decision = Annotated[StartCycle | Revise | RetryProposal | StartNext | FinishCycle | StopHere | RetryStage | StopStage
                     | ResumeStage | CloseStageNow, Field(discriminator="kind")]


def _now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ reads every module shares

def open_cycle_of(s: Session, business_id: int) -> Cycle | None:
    return s.scalar(select(Cycle).where(Cycle.business_id == business_id, Cycle.closed_at.is_(None)))


def stages_of(s: Session, cycle_id: int) -> list[CycleStage]:
    return list(s.scalars(select(CycleStage).where(CycleStage.cycle_id == cycle_id)
                          .order_by(CycleStage.position, CycleStage.id)))


def proposal(s: Session, cycle_id: int, *, status: tuple[str, ...] | None = None) -> CycleProposal | None:
    """The latest version, or the latest in one of `status`."""
    q = select(CycleProposal).where(CycleProposal.cycle_id == cycle_id)
    if status:
        q = q.where(CycleProposal.status.in_(status))
    return s.scalar(q.order_by(CycleProposal.version.desc()).limit(1))


def stage_projects(s: Session, stage_id: int) -> list[Project]:
    return list(s.scalars(select(Project).where(Project.stage_id == stage_id).order_by(Project.id)))


def _live_job(s: Session, kind: str, **payload: int) -> Job | None:
    q = select(Job).where(Job.kind == kind, queue.in_flight())
    for k, v in payload.items():
        q = q.where(Job.payload[k].as_integer() == v)
    return s.scalar(q.limit(1))


def _rounds(s: Session, kind: str, **payload: int) -> int:
    q = select(func.count()).select_from(Job).where(Job.kind == kind)
    for k, v in payload.items():
        q = q.where(Job.payload[k].as_integer() == v)
    return s.scalar(q) or 0


def _ask(s: Session, kind: str, payload: dict[str, int], key: str) -> Job | None:
    """Ask the CEO for one answer, unless a job for it is alive. Each round has a key of its own, so a round that died
    never blocks the next one (as roadmap.queue_retro does)."""
    if _live_job(s, kind, **payload) is not None:
        return None
    return queue.enqueue(s, kind, payload, idempotency_key=f"{key}:{_rounds(s, kind, **payload) + 1}", max_attempts=2)


def _ask_proposal(s: Session, cycle: Cycle, version: int) -> Job | None:
    kind = "cycle_revise" if proposal(s, cycle.id, status=("ready", "superseded")) else "cycle_propose"
    return _ask(s, kind, {"cycle_id": cycle.id, "version": version}, f"{kind}:{cycle.id}:{version}")


# ------------------------------------------------------------------ opening a cycle

def open_cycle(s: Session, business_id: int, *, mode: Literal["new", "adopt"] = "new", lang: Lang) -> Cycle:
    """Open the business's next cycle and ask the CEO for its first proposal. Returns the open cycle when there is one."""
    b = s.scalar(select(Business).where(Business.id == business_id).with_for_update())
    if b is None:
        raise NotFound(msg("cleanup.refuse.no_business", lang, business=f"B-{business_id}"))
    if (open_one := open_cycle_of(s, business_id)) is not None:
        return open_one
    number = (s.scalar(select(func.max(Cycle.number)).where(Cycle.business_id == business_id)) or 0) + 1
    start = date.today()
    c = Cycle(business_id=b.id, number=number, starts_on=start,
              ends_on=start + timedelta(days=max(roadmap.cycle_days(s, b), 1)), state="draft", mode=mode)
    s.add(c)
    s.flush()
    s.add(CycleProposal(cycle_id=c.id, version=1, status="drafting", stages=[], changes=[], basis={}))
    s.flush()
    _ask_proposal(s, c, 1)
    activity.record(s, verb="cycle_opened", entity="cycle", entity_id=c.id, project_id=None, business_id=b.id,
                    actor="system", summary=msg("cycle.activity.opened", lang, n=c.number), data={"n": c.number})
    return c


# ------------------------------------------------------------------ the stage machine

def _move(s: Session, st: CycleStage, to: str, *, lang: Lang, cause: str | None = None, by: str | None = None) -> bool:
    """The only assignment of a stage's status. A move to the status it has is nothing (False); a move the table does
    not allow is refused."""
    if st.status == to:
        return False
    if to not in STAGE_TRANSITIONS[st.status]:
        raise ControlError(msg("cycle.refuse.move", lang, title=st.title, was=msg(f"cycle.status.{st.status}", lang),
                               to=msg(f"cycle.status.{to}", lang)))
    was = st.status
    st.status = to
    if (stamp := _STAMP.get(to)) and not (to == "running" and st.started_at is not None):
        setattr(st, stamp, _now())
    if to in ("done", "skipped"):
        st.end_cause = cause
    elif to == "planned":
        st.end_cause = st.finished_at = None
    if by:
        st.decided_by = by
    s.flush()   # the one-holding-stage index judges each move on its own
    activity.record(s, verb=f"stage_{to}", entity="stage", entity_id=st.id, project_id=None, business_id=st.business_id,
                    actor=by or "system", summary=msg(f"cycle.activity.{to}", lang, title=st.title),
                    data={"from": was, "cause": cause, "title": st.title})
    return True


def _next_planned(s: Session, st: CycleStage) -> CycleStage | None:
    return s.scalar(select(CycleStage).where(CycleStage.cycle_id == st.cycle_id, CycleStage.status == "planned",
                                             CycleStage.position > st.position).order_by(CycleStage.position).limit(1))


def _successor(s: Session, st: CycleStage) -> CycleStage | None:
    return s.scalar(select(CycleStage).where(CycleStage.cycle_id == st.cycle_id, CycleStage.position > st.position,
                                             CycleStage.status != "skipped").order_by(CycleStage.position).limit(1))


def _draft_of(s: Session, st: CycleStage) -> dict[str, Any]:
    p = s.get(CycleProposal, st.proposal_id) if st.proposal_id else None
    return next((d for d in (p.stages if p else []) if d.get("key") == st.key), {})


def goal_for_stage(s: Session, st: CycleStage, lang: Lang) -> str:
    """The stage's project goal: what the stage does, then the plan the owner approved, which every run works inside."""
    b = s.get(Business, st.business_id)
    brief = roadmap.approved_brief(s, b.id)
    head = msg("cycle.goal", lang, title=st.title, doing=st.doing, owner_does=st.owner_does)
    return "\n\n".join([head, roadmap.goal_from_brief(brief, b, lang=lang)] if brief else [head])


def _start_stage(s: Session, st: CycleStage, *, lang: Lang, by: str | None) -> None:
    """planned (or stopped) -> running, in the transaction that also releases the business's parked work into it, brings
    the schedules of the recurring kinds it lists forward to now (a weekly kind skipped while the gate was shut would
    otherwise wait a week), and starts its project."""
    from justshipit.services import projects
    # Asked before parked work joins: a resumed stage, or one that took over running work, has its project already.
    has_project = s.scalar(select(Project.id).where(Project.stage_id == st.id).limit(1)) is not None
    _move(s, st, "running", lang=lang, by=by)
    now = _now()
    if st.ends_when == "window_closed":
        st.listen_until = now + timedelta(days=st.listen_days or 7)
    for job in queue.release_gated(s, st.business_id, st.id):
        pid = scope.owner_of(s, job.payload or {})[0]
        p = s.get(Project, pid) if pid is not None else None
        if p is None:
            continue
        if p.stage_id is None:
            p.stage_id = st.id
        if p.bet_id is not None and p.status == "CREATED" and (bet := s.get(Bet, p.bet_id)) is not None:
            bet.started_at = now   # its run starts now, so its window does
    _bring_forward(s, st, now)
    if has_project:
        return
    draft = _draft_of(s, st)
    if (exp := draft.get("experiment")) and st.bet_id is None:
        fields = {k: exp.get(k) for k in ("audience", "question", "appetite_days", "budget_usd", "exit_signal",
                                          "circuit_breaker")}
        bet = roadmap.create_bet(s, st.business_id, title=exp["title"], pitch=exp["pitch"], origin="project_manager", lang=lang,
                                 **fields)
        st.bet_id = bet.id
        roadmap.move_bet(s, bet.id, roadmap.BetStatus.PICKED, lang=lang)
        roadmap.move_bet(s, bet.id, roadmap.BetStatus.RUNNING, lang=lang)   # its run project joins the stage
        return
    projects.create(s, goal_for_stage(s, st, lang), name=st.title, business=s.get(Business, st.business_id), stage=st)


def _bring_forward(s: Session, st: CycleStage, now: datetime) -> None:
    for sc in s.scalars(select(Schedule).where(Schedule.enabled.is_(True), Schedule.kind.in_(st.allows_kinds or []))):
        if gate.POLICY[sc.kind].scope == "company" or scope.owner_of(s, sc.payload or {})[1] == st.business_id:
            sc.next_run_at = now


def _settled(s: Session, st: CycleStage, now: datetime) -> bool:
    """Whether a running stage's work is over by its own rule: nothing stamped with it in flight, every project of it
    finished (or paused when the owner asked to close it now) with no build still going, and for a listening stage its
    window out and its experiment settled. A stage the owner works ends when the owner says."""
    if st.ends_when == "owner_marks" and st.close_requested_at is None:
        return False
    if s.scalar(select(Job.id).where(Job.stage_id == st.id, Job.kind.notin_(_PROJECT_MANAGER_KINDS), queue.in_flight())
                .limit(1)) is not None:
        return False
    for p in stage_projects(s, st.id):
        paused_to_close = p.status == "PAUSED" and st.close_requested_at is not None
        if p.status not in SETTLED_PROJECT and not paused_to_close:
            return False
    busy_build = (select(DevJob.id).join(Spec, Spec.id == DevJob.spec_id)
                  .join(Project, Project.id == Spec.project_id)
                  .where(Project.stage_id == st.id, DevJob.status.notin_([x.value for x in DEV_JOB_SETTLED])).limit(1))
    if s.scalar(busy_build) is not None:
        return False
    if st.ends_when == "window_closed" and st.close_requested_at is None:
        if st.listen_until is not None and now < st.listen_until:
            return False
        bet = s.get(Bet, st.bet_id) if st.bet_id else None
        if bet is not None and bet.status in ("RUNNING", "LISTENING", "REVIEW") and bet.retro is None:
            return False
    return True


# ------------------------------------------------------------------ the owner's write path

def decide(s: Session, cycle_id: int, d: Decision, *, by: str, lang: Lang) -> None:
    """The owner's one write path (the console, the CLI, and Claude acting for the owner). Locks the cycle, applies the
    decision by its target state, so a second click of the same card changes nothing, and refuses a click on a card
    the cycle has moved past (StaleDecision)."""
    cycle = s.get(Cycle, cycle_id, with_for_update=True)
    if cycle is None:
        raise NotFound(msg("cycle.refuse.no_cycle", lang, id=cycle_id))
    handler = _DECISIONS[d.kind]
    handler(s, cycle, d, by=by, lang=lang)


def _stage_of(s: Session, cycle: Cycle, stage_id: int, lang: Lang) -> CycleStage:
    st = s.get(CycleStage, stage_id, with_for_update=True)
    if st is None or st.cycle_id != cycle.id:
        raise NotFound(msg("cycle.refuse.no_stage", lang, id=stage_id))
    return st


def _stale(lang: Lang) -> StaleDecision:
    return StaleDecision(msg("cycle.refuse.stale", lang))


def _start_cycle(s: Session, cycle: Cycle, d: StartCycle, *, by: str, lang: Lang) -> None:
    ready = proposal(s, cycle.id, status=("ready",))
    if cycle.state != "draft":
        if ready is not None and ready.version == d.version and cycle.state in ("running", "retro", "closed"):
            return
        raise _stale(lang)
    latest = proposal(s, cycle.id)
    if ready is None or ready.version != d.version or latest.status == "drafting":
        raise _stale(lang)   # a failed revision leaves the version before it startable
    past = set(ready.basis.get("past_stage_keys") or [])
    drafts = ready.stages
    target = ready.basis.get("current_stage_key") or next((x["key"] for x in drafts if x["key"] not in past), None)
    adopted = s.scalar(select(CycleStage).where(CycleStage.cycle_id == cycle.id, CycleStage.adopted.is_(True),
                                                CycleStage.status.in_(("running", "closing", "awaiting_owner",
                                                                       "stopped"))).with_for_update())
    if adopted is not None and adopted.status == "awaiting_owner":
        _move(s, adopted, "done", lang=lang, cause="adopted_past", by=by)   # settled before Start; frees the index
        adopted = None
    rows: list[CycleStage] = []
    for i, draft in enumerate(drafts):
        if adopted is not None and draft["key"] == target:
            _fill(adopted, draft, i, ready)   # the running work is this stage now: same row, every stamp stays valid
            rows.append(adopted)
            continue
        st = CycleStage(cycle_id=cycle.id, business_id=cycle.business_id, adopted=False, round=1, status="planned")
        _fill(st, draft, i, ready)
        if draft["key"] in past:
            st.status, st.end_cause, st.finished_at = "done", "adopted_past", _now()
        s.add(st)
        rows.append(st)
    s.flush()
    cycle.state, cycle.started_at = "running", _now()
    activity.record(s, verb="cycle_started", entity="cycle", entity_id=cycle.id, project_id=None,
                    business_id=cycle.business_id, actor=by, summary=msg("cycle.activity.started", lang, n=cycle.number),
                    data={"n": cycle.number})
    if adopted is not None:
        if adopted.status == "stopped":
            _start_stage(s, adopted, lang=lang, by=by)
        else:
            _bring_forward(s, adopted, _now())
        return
    first = next((st for st in rows if st.status == "planned"), None)
    if first is not None:
        _start_stage(s, first, lang=lang, by=by)


def _fill(st: CycleStage, draft: dict[str, Any], position: int, p: CycleProposal) -> None:
    st.key, st.position, st.proposal_id = draft["key"], position, p.id
    st.title, st.doing, st.owner_does = draft["title"][:120], draft["doing"], draft["owner_does"]
    st.decision_points = list(draft.get("decision_points") or [])
    st.est_minutes, st.est_usd = draft.get("est_minutes"), draft.get("est_usd")
    st.estimate_basis, st.ends_when = draft.get("estimate_basis") or "guess", draft.get("ends_when") or "run_settled"
    st.listen_days = draft.get("listen_days")
    st.allows_kinds = list(draft.get("allows_kinds") or [])
    st.allows_owner_work = list(draft.get("allows_owner_work") or [])


def _revise(s: Session, cycle: Cycle, d: Revise, *, by: str, lang: Lang) -> None:
    if cycle.state not in ("draft", "running"):
        raise _stale(lang)
    latest = proposal(s, cycle.id)
    if latest.status == "drafting":
        if latest.owner_note == d.note.strip():
            return
        raise _stale(lang)
    ready = proposal(s, cycle.id, status=("ready",))
    if ready is None or ready.version != d.base_version:
        raise _stale(lang)
    s.add(CycleProposal(cycle_id=cycle.id, version=latest.version + 1, status="drafting", owner_note=d.note.strip(),
                        stages=ready.stages, changes=[], basis=ready.basis))
    s.flush()
    _ask_proposal(s, cycle, latest.version + 1)
    activity.record(s, verb="cycle_revise", entity="cycle", entity_id=cycle.id, project_id=None,
                    business_id=cycle.business_id, actor=by, summary=msg("cycle.activity.revise", lang, n=cycle.number),
                    data={"n": cycle.number, "note": d.note.strip()[:500]})


def _retry_proposal(s: Session, cycle: Cycle, d: RetryProposal, *, by: str, lang: Lang) -> None:
    latest = proposal(s, cycle.id)
    if latest.status == "drafting" and latest.version == d.version + 1:
        return
    if latest.status != "failed" or latest.version != d.version or cycle.state not in ("draft", "running"):
        raise _stale(lang)
    s.add(CycleProposal(cycle_id=cycle.id, version=latest.version + 1, status="drafting", owner_note=latest.owner_note,
                        stages=latest.stages, changes=[], basis=latest.basis))
    s.flush()
    _ask_proposal(s, cycle, latest.version + 1)


def _start_next(s: Session, cycle: Cycle, d: StartNext, *, by: str, lang: Lang) -> None:
    st = _stage_of(s, cycle, d.after_stage_id, lang)
    if st.status == "done":
        if (nxt := _successor(s, st)) is not None and nxt.status != "planned":
            return
        raise _stale(lang)
    if st.status != "awaiting_owner" or proposal(s, cycle.id).status == "drafting":
        raise _stale(lang)
    ready = proposal(s, cycle.id, status=("ready",))
    if ready is None or ready.version != d.version:
        raise _stale(lang)
    nxt = _next_planned(s, st)
    if nxt is None:
        raise _stale(lang)
    _move(s, st, "done", lang=lang, cause="accepted", by=by)
    _start_stage(s, nxt, lang=lang, by=by)


def _finish_cycle(s: Session, cycle: Cycle, d: FinishCycle, *, by: str, lang: Lang) -> None:
    st = _stage_of(s, cycle, d.after_stage_id, lang)
    if st.status == "done" and cycle.state in ("retro", "closed"):
        return
    if st.status != "awaiting_owner" or _next_planned(s, st) is not None:
        raise _stale(lang)
    _move(s, st, "done", lang=lang, cause="accepted", by=by)
    cycle.state = "retro"
    _ask(s, "cycle_retro", {"cycle_id": cycle.id}, f"cycle_retro:{cycle.id}")


def _stop_here(s: Session, cycle: Cycle, d: StopHere, *, by: str, lang: Lang) -> None:
    st = _stage_of(s, cycle, d.after_stage_id, lang)
    if cycle.state == "stopped":
        return
    if st.status not in ("awaiting_owner", "stopped"):
        raise _stale(lang)
    _move(s, st, "done", lang=lang, cause="stopped_here", by=by)
    for rest in stages_of(s, cycle.id):
        if rest.status == "planned":
            _move(s, rest, "skipped", lang=lang, cause="cycle_stopped", by=by)
    cycle.state, cycle.closed_at = "stopped", _now()
    cycle.stop_reason = msg("cycle.stopped_here", lang, title=st.title)
    closed = queue.close_gated(s, cycle.business_id, msg("cycle.gated.closed", lang, n=cycle.number))
    activity.record(s, verb="cycle_stopped", entity="cycle", entity_id=cycle.id, project_id=None,
                    business_id=cycle.business_id, actor=by,
                    summary=msg("cycle.activity.cycle_stopped", lang, n=cycle.number, jobs=len(closed)),
                    data={"n": cycle.number, "closed_jobs": [j.id for j in closed]})


def _retry_stage(s: Session, cycle: Cycle, d: RetryStage, *, by: str, lang: Lang) -> None:
    st = _stage_of(s, cycle, d.stage_id, lang)
    if st.status == "running":
        return
    if st.status != "awaiting_owner":
        raise _stale(lang)
    _move(s, st, "running", lang=lang, by=by)
    st.round += 1
    st.close_requested_at = None
    queue.release_gated(s, st.business_id, st.id)
    for p in stage_projects(s, st.id):
        if p.status == "FAILED" and not queue.why_busy(s, f"project:{p.id}", queue.RUN_KINDS, lang=lang):
            queue.enqueue(s, "retry_project", {"project_id": p.id}, idempotency_key=f"retry:{p.id}:stage:{st.round}",
                          max_attempts=1)


def _stop_stage(s: Session, cycle: Cycle, d: StopStage, *, by: str, lang: Lang) -> None:
    st = _stage_of(s, cycle, d.stage_id, lang)
    if st.status == "stopped":
        return
    if st.status not in ("running", "closing"):
        raise _stale(lang)
    _move(s, st, "stopped", lang=lang, by=by)
    queue.park_stranded(s)


def _resume_stage(s: Session, cycle: Cycle, d: ResumeStage, *, by: str, lang: Lang) -> None:
    st = _stage_of(s, cycle, d.stage_id, lang)
    if st.status == "running":
        return
    if st.status != "stopped":
        raise _stale(lang)
    _start_stage(s, st, lang=lang, by=by)


def _close_stage_now(s: Session, cycle: Cycle, d: CloseStageNow, *, by: str, lang: Lang) -> None:
    from justshipit.services import control
    st = _stage_of(s, cycle, d.stage_id, lang)
    if st.close_requested_at is not None and st.status in ("running", "closing", "awaiting_owner"):
        return
    if st.status != "running":
        raise _stale(lang)
    st.close_requested_at = _now()
    for p in stage_projects(s, st.id):
        if p.status in ("CREATED", "RUNNING", "WAITING_HUMAN") and not control.is_paused(p):
            control.pause(s, p.id, msg("cycle.paused_to_close", lang, title=st.title), lang=lang)


def stop_for_archive(s: Session, business_id: int, *, lang: Lang, by: str) -> None:
    """The business is being archived: its open cycle stops with it, so no stage waits for a click and the CEO is not
    asked for a summary, a proposal or a retro of a business that spends nothing. A started stage closes (a running or
    closing one stops first), a planned one is skipped, and `unarchive_business` leaves the cycle stopped: the owner
    starts the next one."""
    c = open_cycle_of(s, business_id)
    if c is None:
        return
    for st in stages_of(s, c.id):
        if st.status in ("running", "closing"):
            _move(s, st, "stopped", lang=lang, by=by)
        if st.status in ("stopped", "awaiting_owner"):
            _move(s, st, "done", lang=lang, cause="archived", by=by)
        elif st.status == "planned":
            _move(s, st, "skipped", lang=lang, cause="archived", by=by)
    reason = msg("cycle.stopped_archived", lang)
    for p in s.scalars(select(CycleProposal).where(CycleProposal.cycle_id == c.id, CycleProposal.status == "drafting")):
        p.status, p.error = "failed", reason
    c.state, c.closed_at, c.stop_reason = "stopped", _now(), reason


_DECISIONS = {"start_cycle": _start_cycle, "revise": _revise, "retry_proposal": _retry_proposal,
              "start_next": _start_next, "finish_cycle": _finish_cycle, "stop_here": _stop_here,
              "retry_stage": _retry_stage, "stop_stage": _stop_stage, "resume_stage": _resume_stage,
              "close_stage_now": _close_stage_now}


# ------------------------------------------------------------------ the system's driver

def tick(s: Session, now: datetime | None = None, *, lang: Lang | None = None) -> list[str]:
    """Move stages on from what the database says: a running stage whose work settled closes and asks the CEO for its
    summary; a closing stage whose summary job died asks again, and after PROJECT_MANAGER_ROUNDS writes the server's own; queued
    work of a stage that no longer runs is parked; a proposal or retro nobody is writing is asked for again. Safe to
    repeat and to run on several workers: rows are taken with SKIP LOCKED and every move is a compare-and-set."""
    now = now or _now()
    lang = lang or app_settings.language()
    did: list[str] = []
    live = select(Business.id).where(Business.status != "ARCHIVED")   # an archived business spends nothing, the CEO included
    for st in s.scalars(select(CycleStage).where(CycleStage.status == "running", CycleStage.business_id.in_(live))
                        .order_by(CycleStage.id).with_for_update(skip_locked=True)):
        if _settled(s, st, now):
            _move(s, st, "closing", lang=lang)
            _ask(s, "cycle_summarize", {"stage_id": st.id, "round": st.round}, f"cycle_summary:{st.id}:{st.round}")
            did.append(f"stage {st.id} closing")
    for st in s.scalars(select(CycleStage).where(CycleStage.status == "closing", CycleStage.business_id.in_(live))
                        .order_by(CycleStage.id).with_for_update(skip_locked=True)):
        if _live_job(s, "cycle_summarize", stage_id=st.id, round=st.round) is not None:
            continue
        if _rounds(s, "cycle_summarize", stage_id=st.id, round=st.round) < PROJECT_MANAGER_ROUNDS:
            _ask(s, "cycle_summarize", {"stage_id": st.id, "round": st.round}, f"cycle_summary:{st.id}:{st.round}")
            did.append(f"stage {st.id} summary asked again")
        else:
            store_summary(s, st.id, st.round, None, lang=lang)
            did.append(f"stage {st.id} server summary")
    if parked := queue.park_stranded(s):
        did.append(f"{parked} jobs parked")
    for p in s.scalars(select(CycleProposal).join(Cycle, Cycle.id == CycleProposal.cycle_id)
                       .where(CycleProposal.status == "drafting", Cycle.closed_at.is_(None),
                              Cycle.business_id.in_(live))):
        kind = "cycle_revise" if proposal(s, p.cycle_id, status=("ready", "superseded")) else "cycle_propose"
        if _live_job(s, kind, cycle_id=p.cycle_id, version=p.version) is not None:
            continue
        if _rounds(s, kind, cycle_id=p.cycle_id, version=p.version) < PROJECT_MANAGER_ROUNDS:
            _ask_proposal(s, s.get(Cycle, p.cycle_id), p.version)
            did.append(f"cycle {p.cycle_id} v{p.version} asked")
        else:
            fail_proposal(s, p.cycle_id, p.version, msg("cycle.proposal.gave_up", lang))
    for c in s.scalars(select(Cycle).where(Cycle.state == "retro", Cycle.closed_at.is_(None),
                                           Cycle.business_id.in_(live))):
        if _live_job(s, "cycle_retro", cycle_id=c.id) is not None:
            continue
        if _rounds(s, "cycle_retro", cycle_id=c.id) < PROJECT_MANAGER_ROUNDS:
            _ask(s, "cycle_retro", {"cycle_id": c.id}, f"cycle_retro:{c.id}")
        else:
            store_retro(s, c.id, None, lang=lang)
    return did


# ------------------------------------------------------------------ where the CEO's answers land

_KEY = re.compile(r"[^a-z0-9-]+")


def _clean_drafts(drafts: list[StageDraft], frozen: list[CycleStage]) -> list[dict[str, Any]]:
    """The proposal's stages as stored. The server owns what the model cannot be trusted with: keys are slugs and unique;
    a listening window only on a listening stage; and the stages already started stay exactly as they were, first."""
    kept = [_frozen_draft(st) for st in frozen]
    seen = {d["key"] for d in kept}
    out = list(kept)
    for i, d in enumerate(drafts):
        if d.key in seen and d.key in {st.key for st in frozen}:
            continue
        base = _KEY.sub("-", d.key.lower()).strip("-")[:30] or f"stage-{i + 1}"
        key, n = base, 2
        while key in seen:
            key, n = f"{base[:27]}-{n}", n + 1
        seen.add(key)
        row = d.model_dump(mode="json")
        row["key"] = key
        if d.ends_when != "window_closed":
            row["listen_days"] = None
        elif not d.listen_days:
            row["listen_days"] = 7
        out.append(row)
    return out


def _frozen_draft(st: CycleStage) -> dict[str, Any]:
    return {"key": st.key, "title": st.title, "doing": st.doing, "owner_does": st.owner_does,
            "decision_points": st.decision_points, "est_minutes": st.est_minutes, "est_usd": st.est_usd,
            "estimate_basis": st.estimate_basis, "ends_when": st.ends_when, "listen_days": st.listen_days,
            "allows_kinds": st.allows_kinds, "allows_owner_work": st.allows_owner_work, "experiment": None,
            "maps_existing": []}


_COMPARED = ("title", "doing", "owner_does", "decision_points", "est_minutes", "est_usd", "ends_when", "listen_days",
             "allows_kinds", "allows_owner_work")


def diff_stages(prev: list[dict[str, Any]], new: list[dict[str, Any]], why: dict[str, str]) -> list[dict[str, Any]]:
    """What changed between two versions, keyed by stage key: the server's own diff. The CEO's reasons are attached to
    it, never the other way round."""
    before = {d["key"]: (i, d) for i, d in enumerate(prev)}
    out = []
    for i, d in enumerate(new):
        if d["key"] not in before:
            out.append({"stage_key": d["key"], "kind": "added", "fields": [], "why": why.get(d["key"])})
            continue
        j, old = before[d["key"]]
        fields = [f for f in _COMPARED if old.get(f) != d.get(f)]
        if fields:
            out.append({"stage_key": d["key"], "kind": "edited", "fields": fields, "why": why.get(d["key"])})
        elif i != j:
            out.append({"stage_key": d["key"], "kind": "moved", "fields": [], "why": why.get(d["key"])})
    out += [{"stage_key": k, "kind": "removed", "fields": [], "why": why.get(k)}
            for k in before if k not in {d["key"] for d in new}]
    return out


def store_proposal(s: Session, cycle_id: int, version: int, out: CycleProposalOut | CycleRevisionOut,
                   basis: dict[str, Any], *, lang: Lang) -> CycleProposal | None:
    """drafting -> ready, once (the first answer wins). Stages that already started stay as they are; in a cycle that
    is running, the planned rows follow the new version (new keys added, removed ones skipped). In adopt mode every
    piece of live work lands in exactly one stage: what the CEO left out joins the current stage."""
    p = s.scalar(select(CycleProposal).where(CycleProposal.cycle_id == cycle_id, CycleProposal.version == version)
                 .with_for_update())
    if p is None or p.status != "drafting":
        return p
    cycle = s.get(Cycle, cycle_id, with_for_update=True)
    # Before Start the only row is the adopted placeholder, which Start relabels as the current stage. A stage that was
    # started stays as it was whatever became of it (done, running, closing, awaiting the owner, stopped). One the
    # owner took out never started, so it is not frozen: it is gone from the proposal until they ask for it again.
    frozen = ([st for st in stages_of(s, cycle_id) if st.status not in ("planned", "skipped")]
              if cycle.state != "draft" else [])
    drafts = _clean_drafts(out.stages, frozen)
    keys = [d["key"] for d in drafts]
    past = [k for k in getattr(out, "past_stage_keys", []) if k in keys]
    current = getattr(out, "current_stage_key", None) or basis.get("current_stage_key")
    current = current if current in keys else next((k for k in keys if k not in past), keys[0])
    mapped = {(r["kind"], r["id"]) for d in drafts for r in d.get("maps_existing") or []}
    live = [(kind, i) for kind, ids in (basis.get("live") or {}).items() for i in ids]
    left = [{"kind": kind, "id": i} for kind, i in live if (kind, i) not in mapped]
    if left:
        target = next(d for d in drafts if d["key"] == current)
        target["maps_existing"] = [*(target.get("maps_existing") or []), *left]
    prev = proposal(s, cycle_id, status=("ready",))
    why = {n.stage_key: n.why for n in getattr(out, "why", [])}
    p.intro, p.reply = out.intro, getattr(out, "reply", None)
    p.stages, p.changes = drafts, diff_stages(prev.stages if prev else [], drafts, why) if prev else []
    p.basis = {**basis, "past_stage_keys": past, "current_stage_key": current if cycle.mode == "adopt" else None}
    p.status, p.error = "ready", None
    for old in s.scalars(select(CycleProposal).where(CycleProposal.cycle_id == cycle_id, CycleProposal.status == "ready",
                                                     CycleProposal.id != p.id)):
        old.status = "superseded"
    if cycle.state == "running":
        _follow_planned(s, cycle, p, lang)
    queue.notify(s, notices.cycle_proposal(cycle.id, p.version))
    return p


def _follow_planned(s: Session, cycle: Cycle, p: CycleProposal, lang: Lang) -> None:
    rows = {st.key: st for st in stages_of(s, cycle.id)}
    for i, draft in enumerate(p.stages):
        st = rows.get(draft["key"])
        if st is None:
            st = CycleStage(cycle_id=cycle.id, business_id=cycle.business_id, adopted=False, round=1, status="planned")
            s.add(st)
        elif st.status == "skipped" and st.end_cause == "revised_away":
            _move(s, st, "planned", lang=lang)   # asked for again; its row (the cycle's keys are unique) is the stage
        if st.status == "planned":
            _fill(st, draft, i, p)
    s.flush()
    for key, st in rows.items():
        if st.status == "planned" and key not in {d["key"] for d in p.stages}:
            _move(s, st, "skipped", lang=lang, cause="revised_away")


def fail_proposal(s: Session, cycle_id: int, version: int, error: str) -> None:
    """drafting -> failed. The version before stays the one the owner reads, with a line saying the change failed and a
    button to ask again (RetryProposal)."""
    p = s.scalar(select(CycleProposal).where(CycleProposal.cycle_id == cycle_id, CycleProposal.version == version)
                 .with_for_update())
    if p is not None and p.status == "drafting":
        p.status, p.error = "failed", error[:2000]


def stage_facts(s: Session, st: CycleStage, now: datetime | None = None) -> dict[str, Any]:
    """What the server knows about a stage, for its summary: tasks, spend and time from the database, never from the
    model. `refs` are the ids the CEO may cite."""
    now = now or _now()
    pids = [p.id for p in stage_projects(s, st.id)]
    tasks = list(s.scalars(select(Task).where(Task.project_id.in_(pids)).order_by(Task.id))) if pids else []
    cost = s.scalar(select(func.coalesce(func.sum(LLMCallRecord.cost_usd), 0.0))
                    .where(LLMCallRecord.stage_id == st.id)) or 0.0
    end = st.closing_at or now
    ev = [evidence.ev_id(i) for i in s.scalars(select(Evidence.id).where(
        Evidence.project_id.in_(pids), Evidence.superseded_by.is_(None)))] if pids else []
    return {"project_ids": pids,
            "tasks": {"done": sum(t.status == "DONE" for t in tasks), "failed": sum(t.status == "FAILED" for t in tasks),
                      "total": len(tasks)},
            "done_titles": [t.title for t in tasks if t.status == "DONE"],
            "failed_titles": [t.title for t in tasks if t.status == "FAILED"],
            "cost_usd": round(float(cost), 4),
            "elapsed_s": int((end - st.started_at).total_seconds()) if st.started_at else 0,
            "refs": ev + [f"T-{t.id}" for t in tasks]}


def _capped(s: Session, claims: list, facts: dict[str, Any]) -> list[dict[str, Any]]:
    """A claim's confidence is at most what the evidence it cites supports in the ledger: a checked fact, a sourced
    claim, or nothing firmer than a guess. Ids the server did not hand the model are dropped."""
    known = set(facts["refs"])
    out = []
    for c in claims:
        ids = [i for i in c.evidence_ids if i in known]
        evs = [i for i in ids if i.startswith("EV-")]
        grades = {evidence.support(s, pid, evs) for pid in facts["project_ids"]} if evs else set()
        cap = max((_CAP[g] for g in grades), key=_CONFIDENCE.index, default="low")
        conf = min(c.confidence, cap, key=_CONFIDENCE.index)
        out.append({"claim": c.claim, "evidence_ids": ids, "confidence": conf, "capped": conf != c.confidence})
    return out


def store_summary(s: Session, stage_id: int, round_: int, out: StageSummaryOut | None, *, lang: Lang) -> bool:
    """closing -> awaiting_owner, for this round only; the first writer wins (False after). With no answer (`out` None)
    the server writes the summary from its own numbers and says the CEO's write-up failed, so the gate always opens.
    Every number is the server's."""
    st = s.get(CycleStage, stage_id, with_for_update=True)
    if st is None or st.status != "closing" or st.round != round_:
        return False
    facts = stage_facts(s, st)
    numbers = {k: facts[k] for k in ("tasks", "cost_usd", "elapsed_s")}
    if out is None:
        st.summary = {"headline": msg("cycle.summary.server_headline", lang, title=st.title),
                      "done": facts["done_titles"], "not_done": facts["failed_titles"], "claims": [], "needs_owner": [],
                      "next_key": None, "next_reason": None, "change_suggestions": [],
                      "note": msg("cycle.summary.ceo_failed", lang), **numbers}
        st.summary_by = "server"
    else:
        keys = {x.key for x in stages_of(s, st.cycle_id) if x.status == "planned"}
        st.summary = {**out.model_dump(mode="json", exclude={"claims"}), "claims": _capped(s, out.claims, facts),
                      "next_key": out.next_key if out.next_key in keys else None, "note": None, **numbers}
        st.summary_by = roles.SUMMARY_BY
    _move(s, st, "awaiting_owner", lang=lang)
    queue.notify(s, notices.stage_gate(st.id, st.round, st.title))
    return True


def store_retro(s: Session, cycle_id: int, out: CycleRetroOut | None, *, lang: Lang) -> Cycle | None:
    """retro -> closed, then the next cycle opens and its proposal reads this retro. With no answer the server's numbers
    stand alone."""
    c = s.get(Cycle, cycle_id, with_for_update=True)
    if c is None or c.state != "retro":
        return None
    stages = stages_of(s, c.id)
    numbers = {"cost_usd": round(sum(float((x.summary or {}).get("cost_usd") or 0) for x in stages), 4),
               "stages_done": sum(x.status == "done" for x in stages)}
    c.retro = ({**out.model_dump(mode="json"), "by": "project_manager", **numbers} if out is not None
               else {"by": "server", "note": msg("cycle.retro.ceo_failed", lang), **numbers})
    c.state, c.closed_at = "closed", _now()
    activity.record(s, verb="cycle_closed", entity="cycle", entity_id=c.id, project_id=None, business_id=c.business_id,
                    actor="project_manager" if out is not None else "system", summary=msg("cycle.activity.closed", lang, n=c.number),
                    data={"n": c.number})
    return open_cycle(s, c.business_id, mode="new", lang=lang)
