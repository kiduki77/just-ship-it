"""The bet loop's clock: a bet's window, and turning a bet that proved itself into a routine.

A bet lives for its window, its days (`appetite_days`, or `bets.window_days` when it was promised none) counted from
the start, because market response arrives after the run's work is out. It is RUNNING while its run works and
LISTENING once the run finished, collecting posts, owner tasks, reactions and numbers until the window closes. `sweep`
looks at every bet the loop started and moves it along: RUNNING to LISTENING when the run finished, and to REVIEW when
the window closed, the run failed, or its budget ran out; LISTENING to REVIEW when the window closed. The owner can end
either early by moving it to REVIEW. A bet that was already running when the loop shipped (`loop_started_at` null) is
legacy: the sweep leaves it alone, it holds no slot, and the owner ends it. A failed run waits
`bets.failed_grace_minutes`, and while a retry or resume of it is alive, since the owner may be fixing it. The budget
also stops the run itself, because the run's project carries the bet's budget and every model call is reserved against
it; `sweep` only notices. The window has no other enforcer, so the sweep pauses a run still working before it moves the
bet. REVIEW queues the retro (`bet_retro`), which settles the bet. A retro that never arrived is queued again, under a
new key, up to `roadmap.RETRO_ROUNDS` times, and then the bet is flagged for the owner, who can ask for another round.

A routine is a schedule that starts a fresh project for the bet's work every N days, so it spends money again and again.
That is the owner's call: a retro that says `routine` only files a decision card (`ROUTINE_CARD`). Approving it makes the
schedule and runs the first round. A routine holds no bet slot, a business keeps at most `bets.max_routines`, and the
owner can stop one at any time.

The status lines and activity the loop writes are in the owner's language: the job handlers here (`run_sweep`,
`run_routine`) read it once, and a route passes it in. A status line already stored keeps its language, so a check
that reads one back reads it in every language.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.contracts import owner_text
from justshipit.contracts.requests import RequestStatus
from justshipit.db.models import AgentRequest, Bet, Business, Job, Project, Schedule
from justshipit.db.session import session_scope
from justshipit.i18n import LANGS, Lang, msg
from justshipit.operations import scheduler
from justshipit.services import activity, app_settings, control, queue, roadmap
from justshipit.services.control import ControlError
from justshipit.services.requests import ROUTINE_CARD
from justshipit.settings import limits

log = logging.getLogger(__name__)

ROUTINE_KIND = "routine_run"
SETTLED_RUNS = ("DONE", "FAILED", "ARCHIVED")
LIVE_RUNS = ("CREATED", "RUNNING", "WAITING_HUMAN")
CAUSES = ("run_done", "run_failed", "time_limit", "budget_limit", "owner")


def cause_label(cause: str, lang: Lang) -> str:
    """Why an experiment ended, as the owner reads it."""
    return msg(f"bet.cause.{cause}", lang)


def _opens(reason: str | None, lead: Callable[[Lang], str]) -> bool:
    """Whether a stored status line opens with the sentence `lead` writes, in any language."""
    return (reason or "").startswith(tuple(lead(lang) for lang in LANGS))


def _stuck(lang: Lang) -> str:
    return msg("bet.reason.stuck", lang, n=roadmap.RETRO_ROUNDS)


def _budget_stop(lang: Lang) -> str:
    return msg("roadmap.retro.budget_stop", lang)


def run_retry_alive(s: Session, run: Project) -> bool:
    """Whether a run, retry or resume of the project is queued or running."""
    return s.scalar(select(Job.id).where(Job.lock_key == f"project:{run.id}", Job.kind.in_(queue.RUN_KINDS),
                                         queue.in_flight()).limit(1)) is not None


def run_parked(s: Session, run: Project) -> bool:
    """Whether the run's start waits for a stage the owner starts (services/gate.py)."""
    return s.scalar(select(Job.id).where(Job.lock_key == f"project:{run.id}", Job.kind.in_(queue.RUN_KINDS),
                                         Job.status == "gated").limit(1)) is not None


def due_move(s: Session, bet: Bet, run: Project | None, now: datetime) -> tuple[roadmap.BetStatus, str | None] | None:
    """The move a live loop bet is due now and, for a move to REVIEW, why the bet is over; None while none is due.
    A finished run starts the wait for market response instead of ending the bet. A failed run is not over while a
    retry of it is alive or the grace period has not passed."""
    if run is not None and run.status == "CREATED" and run_parked(s, run):
        return None  # its run waits for a stage the owner starts, so its window has not begun
    end = roadmap.window_end(s, bet)
    closed = end is not None and now >= end
    if bet.status == roadmap.BetStatus.LISTENING.value:
        return (roadmap.BetStatus.REVIEW, "time_limit") if closed else None
    if run is not None:
        if run.status == "DONE":
            return roadmap.BetStatus.LISTENING, None
        if run.status == "ARCHIVED":
            return roadmap.BetStatus.REVIEW, "owner"
        if run.status == "FAILED":
            grace = timedelta(minutes=app_settings.effective().bets.failed_grace_minutes)
            if run_retry_alive(s, run) or now - run.updated_at < grace:
                return None
            return roadmap.BetStatus.REVIEW, "budget_limit" if (run.error or "").startswith("BudgetExceeded") else "run_failed"
        if bet.budget_usd and run.spent_usd >= bet.budget_usd:
            return roadmap.BetStatus.REVIEW, "budget_limit"
    return (roadmap.BetStatus.REVIEW, "time_limit") if closed else None


def _loop_bets(s: Session, status: roadmap.BetStatus) -> list[int]:
    return list(s.scalars(select(Bet.id).where(Bet.status == status.value, Bet.loop_started_at.is_not(None))
                          .order_by(Bet.id)))


def _move(s: Session, bet_id: int, to: roadmap.BetStatus, lang: Lang, **kw) -> bool:
    try:
        with s.begin_nested():
            roadmap.move_bet(s, bet_id, to, lang=lang, **kw)
    except ControlError as exc:
        log.warning("sweep left BET-%s where it was: %s", bet_id, exc)
        return False
    return True


def sweep(s: Session, now: datetime | None = None, *, lang: Lang) -> list[tuple[int, str]]:
    """Move every live bet the loop started along by `due_move`, take a reviewed bet back to RUNNING when its failed
    run is being retried (a run that stopped at a limit stays in review, or raising the limit would flap the bet), and
    queue the retro again for a reviewed bet that has none. RUNNING bets go first, so a run that finished after its
    window closed passes through LISTENING to REVIEW in one sweep. Each move is `move_bet` under the bet's row lock,
    which does nothing for a bet already moved, so running it again, or alongside the owner's own move, finds nothing
    left to do. A bet that cannot move (it was changed meanwhile) is left for the next sweep. A legacy bet is never
    touched. Returns the bets moved to REVIEW."""
    now = now or datetime.now(UTC)
    moved: list[tuple[int, str]] = []
    for status in (roadmap.BetStatus.RUNNING, roadmap.BetStatus.LISTENING):
        for bet_id in _loop_bets(s, status):
            due = due_move(s, s.get(Bet, bet_id), s.scalar(select(Project).where(Project.bet_id == bet_id)), now)
            if due is None:
                continue
            to, cause = due
            reason = (msg("bet.reason.review", lang, cause=cause_label(cause, lang)) if cause
                      else msg("bet.reason.listening", lang))
            if _move(s, bet_id, to, lang, end_cause=cause, reason=reason) and cause:
                moved.append((bet_id, cause))
    for bet_id in _loop_bets(s, roadmap.BetStatus.REVIEW):
        _chase_retro(s, s.get(Bet, bet_id), lang)
    return moved


def _chase_retro(s: Session, bet: Bet, lang: Lang) -> None:
    """A bet in review: back to running if its failed run is being retried, else a retro round if none is alive, else
    a flag for the owner once every automatic round is spent."""
    if bet.retro is not None:
        return
    run = s.scalar(select(Project).where(Project.bet_id == bet.id))
    pid = run.id if run else None
    if bet.end_cause == "run_failed" and run is not None and not control.is_paused(run) and (
            run.status in LIVE_RUNS or run_retry_alive(s, run)):
        _move(s, bet.id, roadmap.BetStatus.RUNNING, lang, reason=msg("bet.reason.retry", lang))
    elif (refused := roadmap.retro_budget_stop(s, bet, lang=lang)) is not None:
        if not _opens(bet.status_reason, _budget_stop):
            bet.status_reason = refused["says"]
            activity.record(s, verb="bet_retro_budget", entity="bet", entity_id=bet.id, project_id=pid,
                            business_id=bet.business_id, actor="system",
                            summary=f"BET-{bet.id}: {bet.status_reason}")
    elif roadmap.queue_retro(s, bet.id) is not None:
        activity.record(s, verb="bet_retro_requeued", entity="bet", entity_id=bet.id, project_id=pid,
                        business_id=bet.business_id, actor="system",
                        summary=msg("bet.activity.requeued", lang, id=bet.id, n=roadmap.retro_rounds(s, bet.id)))
    elif roadmap.retro_stuck(s, bet) and not _opens(bet.status_reason, _stuck):
        bet.status_reason = msg("bet.reason.stuck_ask", lang, stuck=_stuck(lang))
        activity.record(s, verb="bet_retro_stuck", entity="bet", entity_id=bet.id, project_id=pid,
                        business_id=bet.business_id, actor="system",
                        summary=f"BET-{bet.id}: {bet.status_reason}")


def redo_retro(s: Session, bet_id: int, *, lang: Lang) -> Job:
    """The owner asks for another retro round on a bet that sits in review without one."""
    bet = s.get(Bet, bet_id)
    if bet is None:
        raise ControlError(msg("roadmap.refuse.no_bet", lang, id=bet_id))
    if bet.status != roadmap.BetStatus.REVIEW.value or bet.retro is not None:
        raise ControlError(msg("bet.refuse.not_waiting", lang, id=bet_id))
    if (refused := roadmap.retro_budget_stop(s, bet, lang=lang)) is not None:
        raise ControlError(refused["says"])
    job = roadmap.queue_retro(s, bet_id, owner=True)
    if job is None:
        raise ControlError(msg("bet.refuse.retro_running", lang, id=bet_id))
    activity.record(s, verb="bet_retro_redo", entity="bet", entity_id=bet_id, project_id=None,
                    business_id=bet.business_id, actor="owner", summary=msg("bet.activity.redo", lang, id=bet_id))
    return job


def run_sweep() -> None:
    lang = app_settings.language()
    with session_scope() as s, activity.as_actor("system"):
        sweep(s, lang=lang)


# ------------------------------------------------------------------ routines

def routine_name(bet_id: int) -> str:
    """The routine's schedule key, unique per (kind, name) and stored in its row, so it stays as old rows hold it."""
    return f"BET-{bet_id} 정기 업무"


def routine_schedule(s: Session, bet_id: int) -> Schedule | None:
    return s.scalar(select(Schedule).where(Schedule.kind == ROUTINE_KIND, Schedule.name == routine_name(bet_id)))


def _routine_bets(s: Session, business_id: int) -> set[int]:
    return set(s.scalars(select(Bet.id).where(Bet.business_id == business_id, Bet.verdict == "routine")))


def active_routines(s: Session, business_id: int) -> list[Schedule]:
    mine = {routine_name(i) for i in _routine_bets(s, business_id)}
    return [sc for sc in s.scalars(select(Schedule).where(Schedule.kind == ROUTINE_KIND, Schedule.enabled.is_(True)))
            if sc.name in mine]


def pending_routine_cards(s: Session, business_id: int) -> list[AgentRequest]:
    mine = _routine_bets(s, business_id)
    return [r for r in s.scalars(select(AgentRequest).where(AgentRequest.action_kind == ROUTINE_CARD,
                                                            AgentRequest.status == RequestStatus.PENDING.value))
            if (r.action_params or {}).get("bet_id") in mine]


def routine_state(s: Session, bet: Bet) -> str | None:
    """None for a bet that is no routine, else proposed (a card waits for the owner), active, stopped or declined."""
    if bet.verdict != "routine":
        return None
    if (sc := routine_schedule(s, bet.id)) is not None:
        return "active" if sc.enabled else "stopped"
    asked = [r for r in s.scalars(select(AgentRequest).where(AgentRequest.action_kind == ROUTINE_CARD)
                                  .order_by(AgentRequest.id.desc())) if (r.action_params or {}).get("bet_id") == bet.id]
    return "proposed" if asked and asked[0].status == RequestStatus.PENDING.value else "declined"


def make_routine(s: Session, bet: Bet, *, every_days: int, what: str) -> Schedule:
    """Schedule the bet's work to repeat. A second call for the same bet changes nothing."""
    scheduler.ensure(s, name=routine_name(bet.id), kind=ROUTINE_KIND, hours=every_days * 24,
                     payload={"bet_id": bet.id, "what": what[:1000]})
    s.flush()
    return routine_schedule(s, bet.id)


def start_routine(s: Session, bet: Bet, *, every_days: int, what: str, lang: Lang, by_owner: bool = False) -> bool:
    """What the approval of a routine card does: make the schedule and run its first round now, when the owner
    approved it (`by_owner`) or a running stage lists routines. False when the business already keeps
    `bets.max_routines`."""
    cap = app_settings.effective().bets.max_routines
    if routine_schedule(s, bet.id) is None and len(active_routines(s, bet.business_id)) >= cap:
        activity.record(s, verb="routine_refused", entity="bet", entity_id=bet.id, project_id=None,
                        business_id=bet.business_id, actor="system",
                        summary=msg("bet.activity.routine_refused", lang, id=bet.id, n=cap))
        return False
    make_routine(s, bet, every_days=every_days, what=what)
    queue.enqueue(s, "routine_run", {"bet_id": bet.id, "what": what[:1000]}, idempotency_key=f"routine_first:{bet.id}",
                  max_attempts=1, by_owner=by_owner)
    activity.record(s, verb="routine_started", entity="bet", entity_id=bet.id, project_id=None,
                    business_id=bet.business_id, actor="owner",
                    summary=msg("bet.activity.routine_started", lang, id=bet.id, n=every_days))
    return True


def stop_routine(s: Session, bet_id: int, *, lang: Lang) -> Schedule:
    """The owner stops a routine: its schedule is disabled and a round that is running is paused."""
    bet = s.get(Bet, bet_id)
    sc = routine_schedule(s, bet_id) if bet else None
    if sc is None:
        raise ControlError(msg("bet.refuse.no_routine", lang, id=bet_id))
    sc.enabled = False
    paused = []
    for p in s.scalars(select(Project).where(Project.business_id == bet.business_id,
                                             Project.context["routine_of_bet"].as_integer() == bet_id,
                                             Project.status.in_(LIVE_RUNS))):
        control.pause(s, p.id, msg("bet.routine.paused", lang), lang=lang)
        paused.append(p.id)
    activity.record(s, verb="routine_stopped", entity="bet", entity_id=bet_id, project_id=None,
                    business_id=bet.business_id, actor="owner", summary=msg("bet.activity.routine_stopped", lang, id=bet_id),
                    data={"paused": paused})
    return sc


def on_card_decided(s: Session, req: AgentRequest, *, lang: Lang, by_owner: bool = False) -> None:
    """A decision card bound to a bet was answered. Approving a routine starts it. Approving a next bet outside the
    brief's scope picks it, and rejecting one drops it. A deferral changes nothing."""
    params = req.action_params or {}
    bet = s.get(Bet, params["bet_id"]) if isinstance(params.get("bet_id"), int) else None
    if bet is None:
        return
    approved, rejected = req.status == RequestStatus.APPROVED.value, req.status == RequestStatus.REJECTED.value
    if req.action_kind == ROUTINE_CARD:
        if approved:
            start_routine(s, bet, every_days=int(params.get("every_days") or 7), what=str(params.get("what") or bet.pitch),
                          lang=lang, by_owner=by_owner)
    elif approved and bet.status == roadmap.BetStatus.PROPOSED.value:
        roadmap.move_bet(s, bet.id, roadmap.BetStatus.PICKED, lang=lang,
                         reason=msg("bet.reason.card_approved", lang, title=owner_text.title(req.title, 100)))
    elif rejected and bet.status in (roadmap.BetStatus.PROPOSED.value, roadmap.BetStatus.PICKED.value):
        roadmap.move_bet(s, bet.id, roadmap.BetStatus.DROPPED, lang=lang,
                         reason=msg("bet.reason.card_rejected", lang, title=owner_text.title(req.title, 100)))


def routine_goal(bet: Bet, what: str, lang: Lang) -> str:
    return "\n\n".join([msg("bet.routine.project", lang, title=bet.title), what or bet.pitch,
                        msg("bet.routine.why", lang, id=bet.id, pitch=bet.pitch)])


def run_routine(bet_id: int, what: str = "") -> int | None:
    """One round of a routine the owner approved: a fresh project for the work, unless the last round is still running
    or the owner stopped the routine. The project has no bet, so it takes no bet slot. Returns the new project's id."""
    lang = app_settings.language()
    with session_scope() as s:
        bet = s.get(Bet, bet_id)
        business = s.get(Business, bet.business_id) if bet else None
        schedule = routine_schedule(s, bet_id) if bet else None
        if (bet is None or bet.verdict != "routine" or schedule is None or not schedule.enabled or business is None
                or business.status == "ARCHIVED"):
            return None
        last = s.scalars(select(Project).where(Project.business_id == bet.business_id,
                                               Project.context["routine_of_bet"].as_integer() == bet_id)
                         .order_by(Project.id.desc()).limit(1)).first()
        if last is not None and last.status not in SETTLED_RUNS:
            return None
        run = Project(slug=roadmap.unique_slug(s, Project.slug, f"routine-{bet_id}-{datetime.now(UTC):%Y%m%d}"),
                      name=msg("bet.routine.project", lang, title=bet.title)[:200], goal=routine_goal(bet, what, lang),
                      status="CREATED",
                      budget_usd=bet.budget_usd or float(limits()["project_budget_usd"]),
                      context={"routine_of_bet": bet_id}, business_id=bet.business_id)
        s.add(run)
        s.flush()
        queue.enqueue(s, "run_project", {"project_id": run.id}, idempotency_key=f"run:{run.id}", max_attempts=1)
        return run.id
