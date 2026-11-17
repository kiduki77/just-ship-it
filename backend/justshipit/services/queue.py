"""PostgreSQL work queue (transactional outbox) using FOR UPDATE SKIP LOCKED.

A running job holds a lease: its worker's heartbeat keeps `locked_at` fresh, and a job whose lease has
gone stale is reclaimed under the claim lock. A stale lease does not prove its holder dead (a host suspend
or a database outage outlasts it with the handler still working), so a running row counts as in flight
until that reclaim, and a reclaimed job runs again only after the old holder has had time to stop. Every
claim gets its own `lease_token`; heartbeat and finish carry it, so a worker whose lease was reclaimed can
neither renew nor finish the job. Lease times come from the database clock, so a worker's skewed clock
cannot stretch or shorten a lease. Jobs sharing a `lock_key` never run at the same time.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Collection, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import and_, case, func, or_, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from justshipit.contracts import notify as notices
from justshipit.contracts.handoff import DevJobStatus
from justshipit.db.models import (
    CycleStage, DevJob, IdeaReview, Job, KickoffSession, OwnerIdea, Project, Spec, Worker,
)
from justshipit.db.session import session_scope
from justshipit.runtime import longest_call_s
from justshipit.runtime.children import Children
from justshipit.i18n import Lang, msg
from justshipit.services import app_settings, gate, scope, stops
from justshipit.services.usage import MustWait, say_at
from justshipit.settings import company_config

log = logging.getLogger(__name__)

NOTIFY_ATTEMPTS = 5
# A message for the owner is claimed ahead of every other job: it is short, and its worth is in when it arrives.
NOTICE_KINDS = ("notify", "daily_briefing")

# Serializes claims across worker processes, so two claims can't both see a lock key as free.
_CLAIM_LOCK = 0x61697465616D  # the ASCII of the app's name before the rename; kept so old and new builds exclude each other


def take_claim_lock(s: Session) -> None:
    """Lock order: the claim lock comes before any Project, DevJob, Spec or BudgetMonth row lock, the order a
    reclaim takes them (claim lock, job rows, then the subject rows in fail_subject) and the orphan release takes
    the budget rows. Code holding such a row never takes it, not even on another connection: PostgreSQL cannot see
    a wait that crosses two connections and never breaks it."""
    s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CLAIM_LOCK})


# Jobs whose failure the queue mirrors onto their project or dev job (see fail_subject).
RUN_KINDS = ("run_project", "resume_project", "retry_project")
# A reclaimed run gets this many attempts in all (one automatic retry from its checkpoint), whatever it was
# enqueued with.
RUN_ATTEMPTS_AFTER_RECLAIM = 2
# A build or a fix round carries on from the step it was in (engineering/worker.py, revise.py), so it survives this
# many worker deaths less one: a build runs for an hour or more and may see more than one restart, but one whose
# worker keeps dying stops. A usage wait (`defer`) returns its attempt, so waits and restarts never spend each other's.
BUILD_ATTEMPTS_AFTER_RECLAIM = 3
BUILD_KINDS = ("dev_job", "dev_resume", "dev_fix")
MIN_DEFER_S = 30
DEV_KINDS = ("dev_job", "dev_resume", "dev_deliver", "dev_fix")
DEV_ACTIVE = tuple(st.value for st in (DevJobStatus.QUEUED, DevJobStatus.INTAKE, DevJobStatus.PLAN_REVIEW,
                                       DevJobStatus.IMPLEMENTING, DevJobStatus.REVIEWING, DevJobStatus.QA,
                                       DevJobStatus.SHIPPING))


def _builds_resume() -> bool:
    """A reclaimed build resumes only in its own container; on the host it cannot pick up its step, so it gets no
    extra tries there."""
    return company_config()["engineering"].get("isolation", "container") == "container"


def lease_s() -> float:
    return float(company_config()["operations"]["job_lease_s"])


def heartbeat_s() -> float:
    return float(company_config()["operations"]["job_heartbeat_s"])


@dataclass(eq=False)
class Lease:
    """A claim this process holds. `lost` is raised by the heartbeat once the queue stops renewing the token
    and by `confirm_lease` when the row says so; handlers read it at every side-effect boundary. `confirmed_at`
    is the wall-clock time the row was last known to be ours (a renewal or a confirm): after a host suspend
    the beat thread slept too, so a flag older than a lease proves nothing. `finishing` is set once the handler
    returned: the lease is still renewed until its finish commits, but a renewal it misses then may be that
    commit, so it proves nothing either. `stop` is the worker's: once set, the job stops at its next check and goes
    back to the queue. `children` are the calls and containers it has running, which the stop ends. `gate`, `stage_id`
    and `kind` are the claimed row's: a `stage` job stops at its next check once its stage stops (services/gate.py), and
    any job once the owner stops the company or the agent its kind belongs to (services/stops.py)."""
    job_id: int
    token: str
    lost: threading.Event = field(default_factory=threading.Event)
    confirmed_at: float = field(default_factory=time.time)
    finishing: bool = False
    stop: threading.Event | None = None
    children: Children = field(default_factory=Children)
    gate: str = "direct"
    stage_id: int | None = None
    kind: str = ""
    stage_checked_at: float = field(default_factory=time.time)
    stops_checked_at: float = field(default_factory=time.time)
    owner_stopped: bool = False


# How stale a lease's "its stage is still open" and "the owner has not stopped it" may get before a check reads again.
STAGE_CHECK_S = 10.0


class LeaseLost(BaseException):
    """Unwinds a handler whose job another worker owns now. A BaseException, so that no `except Exception` on
    the way out records the job, its project or its dev job as failed: those rows are the new holder's."""


class Stopping(LeaseLost):
    """Unwinds a handler whose worker is shutting down. A LeaseLost, so every path that keeps a lost lease from
    recording or sending (a connector refunds its grant) does the same here; the worker then hands the job back."""


class Hold(BaseException):
    """Not an error: the job has nothing to do yet (quiet hours, a Slack that refused the token) and goes back in the
    queue for `seconds`, with its attempt returned. A BaseException, like MustWait, so no `except Exception` on the
    way out records the job as failed."""

    def __init__(self, seconds: float, why: str) -> None:
        super().__init__(why)
        self.seconds, self.why = seconds, why


class AgentStopped(Hold):
    """The job reached an agent the owner stopped: a model call of it, or a run whose remaining work is all its. The job
    goes back with its attempt returned and waits on that agent (`Job.waits_on`) until the owner resumes it. A run's
    task step catches it and leaves the task where it stood, so the rest of the run goes on."""

    def __init__(self, agent: str, why: str) -> None:
        super().__init__(0, why)
        self.agent = agent


def check_agent(*agents: str) -> None:
    """Stop here, under a job, when the owner stopped what this step is about to spend on. Read fresh, since a model
    call is the money: the company (a job that is not FREE) or the agent the job's own kind belongs to ends the job
    (`Stopping`), and a stopped one of these agents holds the step (`AgentStopped`). Outside any job (a CLI command run
    by hand) the owner is the one asking, so nothing is held."""
    lease = current_lease.get()
    if lease is None or not agents:
        return
    if lease.owner_stopped:
        raise Stopping(lease.job_id)
    with session_scope(lease_checked=False) as s:
        held = stops.read(s)
    lease.stops_checked_at = time.time()
    if stops.job_held(held, lease.kind, lease.gate) is not None:
        lease.owner_stopped = True
        raise Stopping(lease.job_id)
    if (agent := next((a for a in agents if held.holds(a)), None)) is not None:
        raise AgentStopped(agent, stops.waiting_text(agent, app_settings.language()))


class GiveUp(Exception):
    """The job fails now, whatever attempts it has left: a retry could do harm (a second copy of a message Slack
    may already have posted)."""


# The lease of the job this thread executes, set by the worker. LangGraph's executor and the meeting pool copy
# the context into the threads they start, so a check inside them sees the same lease.
current_lease: ContextVar[Lease | None] = ContextVar("current_lease", default=None)


def check_lease() -> None:
    """Stop here if this job's lease is gone or the owner stopped what it runs under: before a model call, a graph node,
    a dev-job step. A flag that nothing has refreshed for a whole lease is checked against the row before it is trusted.
    That read takes no claim lock, since the caller may hold row locks a reclaim waits for (see take_claim_lock)."""
    _check(owner_stops=True)


def check_commit() -> None:
    """The check every commit under a lease passes (db/session.py). The lease and the stage count; the owner's stops do
    not. They end a job at the start of a step, never at the commit that saves what a model call already cost: a result
    thrown away there would be paid for again after the resume."""
    _check(owner_stops=False)


def _check(*, owner_stops: bool) -> None:
    lease = current_lease.get()
    if lease is None:
        return
    if lease.stop is not None and lease.stop.is_set():
        raise Stopping(lease.job_id)
    if not lease.lost.is_set() and time.time() - lease.confirmed_at > lease_s():
        _read_lease(lease, claim_lock=False)
    if lease.lost.is_set():
        raise LeaseLost(lease.job_id)
    _check_owner_stops(lease, owner_stops=owner_stops)


def _check_owner_stops(lease: Lease, *, owner_stops: bool) -> None:
    """A job stops once the owner stopped what it runs under: its stage, the company, or the agent its kind belongs to.
    It goes back to wait (`release`). A FREE job goes on through a company stop. Once seen, an owner's stop holds for
    the whole lease: threads that share it (parallel graph nodes, the meeting pool) must not start paid calls on the
    strength of a cache that this thread's own raise had just refreshed."""
    if owner_stops and lease.owner_stopped:
        raise Stopping(lease.job_id)
    now = time.time()
    stage_due = lease.gate == "stage" and now - lease.stage_checked_at >= STAGE_CHECK_S
    stops_due = owner_stops and now - lease.stops_checked_at >= STAGE_CHECK_S
    if not (stage_due or stops_due):
        return
    with session_scope(lease_checked=False) as s:
        still = not stage_due or gate.stage_is_open(s, lease.stage_id)
        held = stops.job_held(stops.read(s), lease.kind, lease.gate) if stops_due else None
    if stage_due:
        lease.stage_checked_at = time.time()
    if stops_due:
        lease.stops_checked_at = time.time()
    if held is not None:
        lease.owner_stopped = True
    if not still or held is not None:
        raise Stopping(lease.job_id)


def confirm_lease() -> None:
    """`check_lease` plus the row itself under the claim lock, for the moment before an irreversible step
    (push, merge, connector send): a flag the heartbeat is seconds from raising is not enough there. Call it
    holding no row lock (see take_claim_lock)."""
    lease = current_lease.get()
    if lease is None:
        return
    if not lease.lost.is_set():
        _read_lease(lease, claim_lock=True)
    if lease.lost.is_set():
        raise LeaseLost(lease.job_id)


def _read_lease(lease: Lease, *, claim_lock: bool) -> None:
    with session_scope(lease_checked=False) as s:  # a read; the check on commit would recurse into here
        if claim_lock:
            take_claim_lock(s)
        held = s.scalar(select(Job.id).where(Job.id == lease.job_id, Job.status == "running",
                                             Job.lease_token == lease.token))
    if held is None:
        lease.lost.set()
    else:
        lease.confirmed_at = time.time()


def _lock_key(kind: str, payload: dict[str, Any]) -> str | None:
    if kind == "execute_action":
        # Its own key, not the project's: a send touches only its ledger row and grant, so it never has to wait
        # for the project run, and a reclaimed send holds just itself while its old holder may still be sending.
        return f"action:{payload['request_id']}"
    if kind == "product_report":
        return f"product_report:{payload['business_id']}"
    if kind == "triage_idea":
        return f"idea:{payload['idea_id']}"
    if kind == "profile_infer":
        return f"profile:{payload['business_id']}"  # one at a time, so a later answer is read with the earlier one
    if kind == "chat_reply":
        return f"chat:{payload['thread_id']}"  # a thread's replies run one at a time, in order
    if kind == "audit_task":
        # Beside the run, never after it: a shadow check must not hold the run's next step. One per project at a
        # time, so two checks never both fit the audit budget's last room.
        return f"audit:{payload['project_id']}"
    if kind == "realign_project":
        # Beside the run, not after it, but one at a time: two realigns 34 s apart each planned the same
        # 2단계 문안 task (T-97, T-99), neither seeing the other's.
        return f"realign:{payload['project_id']}"
    if kind in ("write_plan", "redraft_plan", "write_spec", "redraft_spec"):
        # Its own key: under the project's, an approved development waited for the whole run (1h42m) to get a
        # spec. One per project still, so two drafts never race for the next spec key, and a redraft never races a
        # draft of the same product.
        return f"spec:{payload['project_id']}"
    if kind == "kit_prepare":
        return f"kit:{payload['business_id']}"  # one write at a time per business, so two never replace each other's texts
    if kind == "promotion_week" and payload.get("business_id") is not None:
        year, week, _ = datetime.now(timezone.utc).isocalendar()
        return f"promotion:{payload['business_id']}:{year}-W{week:02d}"
    if payload.get("dev_job_id") is not None:
        return f"dev_job:{payload['dev_job_id']}"
    if payload.get("project_id") is not None:
        return f"project:{payload['project_id']}"
    return None


def enqueue(s: Session, kind: str, payload: dict[str, Any], *, idempotency_key: str | None = None,
            delay_s: int = 0, max_attempts: int = 3, by_owner: bool = False, approved: bool = False) -> Job | None:
    """Enqueue inside the caller's transaction, stamped with who approved it (gate.admit). Duplicate idempotency keys
    are ignored. Work the company starts while no stage runs waits as `gated` and joins the next stage the owner
    starts; a recurring kind no running stage lists is skipped (None), with nothing to catch up later. `by_owner`: the
    owner's own click put it here, so it runs whatever stage is open (tests/test_gate_sites.py pins those sites).
    `approved`: it follows work the owner approved, so it runs on that approval while no stage runs and belongs to the
    stage while one does."""
    adm = gate.admit(s, kind, payload, by_owner=by_owner, approved=approved)
    if adm.verdict == "skip":
        log.info("job %s %s skipped: no running stage lists it", kind, payload)
        return None
    if idempotency_key and s.scalar(select(Job).where(Job.idempotency_key == idempotency_key)):
        return None
    job = Job(kind=kind, payload=payload, idempotency_key=idempotency_key, max_attempts=max_attempts,
              lock_key=_lock_key(kind, payload), run_after=_after(delay_s), gate=adm.gate, stage_id=adm.stage_id,
              business_id=adm.business_id if adm.business_id is not None else scope.subject_business(s, payload),
              status="gated" if adm.verdict == "park" else "queued")
    s.add(job)
    try:
        with s.begin_nested():
            s.flush()
    except IntegrityError:
        return None
    if adm.stage_id is not None and adm.project_id is not None:
        # Work of a project that belongs to no stage yet joins the stage it runs in, so the stage's tasks show it.
        p = s.get(Project, adm.project_id)
        if p is not None and p.stage_id is None:
            p.stage_id = adm.stage_id
    return job


def notify(s: Session, notice: notices.Notice) -> Job | None:
    """Enqueue a message for the owner in the caller's transaction, so it exists exactly when the change does and
    Slack is never called inside that transaction. It is dropped here when the owner turned Slack off or did not
    choose its kind, and held until quiet hours end. A settings read that fails does not fail the change: the
    notice is queued as it is and the sender decides again."""
    delay = 0
    try:
        cfg = app_settings.effective()
        if not cfg.notifies(notice.kind):
            return None
        delay = cfg.notifications.quiet_wait_s(cfg.usage.timezone)
    except Exception as exc:  # noqa: BLE001 - the change this notice reports must not depend on reading settings
        log.warning("notice %s queued without the owner's preferences: %s", notice.dedupe_key, type(exc).__name__)
    job = enqueue(s, "notify", notice.model_dump(mode="json"), idempotency_key=notice.dedupe_key, delay_s=delay,
                  max_attempts=NOTIFY_ATTEMPTS)
    if job is not None and notice.about:
        # The payload names no subject, so the notice never waits on its subject's lock; its business is the one of
        # the record it is about, so a business's queue lists it and no other business's does.
        job.business_id = scope.owner_of(s, notice.about)[1]
    return job


def _after(seconds: float):
    """A moment on the database clock, so a worker's skewed clock never moves a job's schedule."""
    return func.now() + func.make_interval(0, 0, 0, 0, 0, 0, seconds)


def _cutoff(lease: float):
    return _after(-lease)


def _stale(lease: float):
    return or_(Job.locked_at.is_(None), Job.locked_at < _cutoff(lease))


def _reclaim_stale(s: Session, held: Mapping[int, str | None], gone: Collection[str] = ()) -> None:
    """Requeue or fail running jobs whose lease lapsed. A lease the caller itself still holds is left for its
    heartbeat to renew: the handler is alive, only its beats were late. The job's lock key stays held for
    `rerun_delay_s` whatever became of the old holder: a worker killed outright leaves its CLI calls and test
    containers running, so even a holder whose process is gone may still be working through them. `gone` names the
    holders the worker proved stopped, nothing they started still running (runner._gone_holders): their jobs are
    taken back now, whatever their lease, and may run again at once."""
    lease, delay = lease_s(), rerun_delay_s()
    lang = app_settings.language()
    lapsed = or_(_stale(lease), Job.locked_by.in_(list(gone))) if gone else _stale(lease)
    q = select(Job).where(Job.status == "running", lapsed).with_for_update()
    if mine := [(jid, tok) for jid, tok in held.items() if tok]:
        q = q.where(tuple_(Job.id, Job.lease_token).not_in(mine))
    for job in s.scalars(q):
        stopped = job.locked_by in gone
        if job.kind in RUN_KINDS and job.attempts < RUN_ATTEMPTS_AFTER_RECLAIM:
            # A graph run continues from its LangGraph checkpoint, so one worker death costs a step, not the run.
            job.kind, job.max_attempts = "retry_project", RUN_ATTEMPTS_AFTER_RECLAIM
        elif job.kind in BUILD_KINDS and _builds_resume():
            job.max_attempts = max(job.max_attempts, BUILD_ATTEMPTS_AFTER_RECLAIM)
        spent = job.attempts >= job.max_attempts
        again = ("queue.again.checkpoint" if job.kind in RUN_KINDS else "queue.again") + (".can" if spent else "")
        said = dict(worker=job.locked_by or "?", attempts=job.attempts, max=job.max_attempts,
                    spent=msg("queue.reclaimed.spent", lang) if spent else "", again=msg(again, lang))
        job.last_error = (msg("queue.reclaimed.stopped", lang, **said) if stopped else
                          msg("queue.reclaimed", lang, lease=int(lease), minutes=int(delay // 60), **said))
        job.status = "failed" if spent else "queued"
        if stopped:  # failed, it ended when its hold did, so reclaimed_out reads it as given up
            job.held_until = job.finished_at = _after(0) if spent else None
            job.run_after = func.now()
        else:
            job.finished_at = datetime.now(timezone.utc) if spent else None
            job.held_until = job.run_after = _after(delay)
        job.locked_by = job.lease_token = None
        if spent:
            fail_subject(s, job, cause="lost")


def reclaimed_out(job: Job) -> bool:
    """Whether the job ended when a reclaim gave up on it, its holder having stopped answering. A job that ran again
    after a reclaim ran only once its hold was over, so it finished after `held_until`."""
    return (job.status == "failed" and job.held_until is not None and job.finished_at is not None
            and job.held_until >= job.finished_at)


def rerun_delay_s() -> float:
    """How long a reclaimed job waits before it runs again. A holder whose lease looks stale may be alive
    (host suspend, database outage) and stops at its next lease check, so the wait is the longest stretch a
    handler spends between two checks: one model call with its retries, one engineering test run, or one
    analysis container run. Every other stretch (a download, a slot wait) checks the lease as it goes."""
    cfg = company_config()
    return max(longest_call_s(), float(cfg["engineering"]["test_timeout_s"]), float(cfg["analysis"]["run_timeout_s"]))


def in_flight():
    """The one definition of a job in flight: queued, parked until a stage starts (`gated`), running whatever its
    lease says, or reclaimed while its old holder may still be mid-call (`held_until`). A stale lease is the
    reclaimer's to settle under the claim lock (requeue or fail); until then its holder may be working. A parked job is
    in flight so that no "already queued?" check queues a second one beside it."""
    return or_(Job.status.in_(("queued", "running", "gated")), Job.held_until > func.now())


def local_time(at: datetime, lang: Lang | None = None) -> str:
    """A moment as the owner reads it in every message, e.g. "Nov 2, 19:33 (KST)": with its date, since the
    owner may read it the next day, and its zone, since the server's local zone is the only one we know. Without
    `lang`, in the language the owner reads now."""
    return say_at(at.astimezone(), lang)


def why_busy(s: Session, lock_key: str, kinds: tuple[str, ...], lang: Lang | None = None) -> str | None:
    """Why the subject behind this key cannot be started again right now, with the time that ends the wait
    when one is known; None when it can. The owner reads it as is, in `lang` (by default the one they read now)."""
    jobs = s.scalars(select(Job).where(Job.lock_key == lock_key, Job.kind.in_(kinds), in_flight())
                     .order_by(Job.id.desc())).all()
    if not jobs:
        return None
    lang = lang or app_settings.language()
    for j in jobs:
        if j.status == "running":
            return msg("queue.busy.running", lang, id=j.id, worker=j.locked_by)
    for j in jobs:
        if j.status == "queued":
            when = msg("queue.busy.queued.when", lang, at=local_time(j.run_after, lang)) \
                if j.run_after > datetime.now(timezone.utc) else ""
            return msg("queue.busy.queued", lang, id=j.id, when=when)
    for j in jobs:
        if j.status == "gated":
            return msg("queue.busy.gated", lang, id=j.id)
    return msg("queue.busy.held", lang, at=local_time(jobs[0].held_until, lang))


# Why a job ended without its work: it failed, a reclaim gave up on it, or the owner cancelled it before it started.
EndCause = Literal["failed", "lost", "cancelled"]

# Side effects the owner approved once: running one again is a new owner decision, made through a new request, so the
# owner's cancel does not take them back either.
NOT_RETRYABLE = frozenset({"execute_action", "dev_merge"})
UNSTARTED = ("queued", "gated")


def cancel_unstarted(s: Session, job: Job, lang: Lang) -> str:
    """The owner's cancel of a job locked by the caller that has not started (`UNSTARTED`, not `NOT_RETRYABLE`). The
    row stays as `cancelled` so the record keeps it, its idempotency key is let go so the same work can be asked for
    again, and what it would have moved ends as `fail_subject` leaves it. Returns the status it had."""
    from justshipit.services import activity  # activity imports what the queue imports
    from justshipit.services.work_now import job_label  # work_now imports the queue
    was, key = job.status, job.idempotency_key
    job.status, job.finished_at, job.idempotency_key = "cancelled", func.now(), None
    job.last_error = msg("console.job.cancelled_by_owner", lang)
    fail_subject(s, job, cause="cancelled")
    pid, bid = scope.owner_of(s, job.payload)
    activity.record(s, verb="cancelled", entity="job", entity_id=job.id, project_id=pid, business_id=bid,
                    actor="owner", summary=msg("console.event.cancelled", lang, job=job_label(job.kind, lang), id=job.id),
                    data={"kind": job.kind, "job_id": job.id, "was": was, "idempotency_key": key})
    return was


def fail_subject(s: Session, job: Job, *, cause: EndCause = "failed") -> None:
    """A run or dev job that gives up would leave its project or dev job looking busy forever. Mark it failed,
    which is the state the console's retry controls act on. A kickoff turn that gives up pauses its session, so
    the owner sees 이어하기 instead of a conversation that waits for a turn that will never come. A lens of the idea
    review panel that gives up shows as failed, and the kickoff goes on without it. So does the CPO's report on a
    product the owner brought. `lost`: a reclaim gave up on it, whose text (worker id, attempts) is an engineer's, so a
    run says only that its worker stopped answering. `cancelled`: the owner's cancel ends the subject the same way,
    says so, and sends no failure notice, since the owner did it."""
    why = (job.last_error or "").split("\n", 1)[0][:1000]
    lang = app_settings.language()
    cancelled = cause == "cancelled"
    if job.kind in RUN_KINDS:
        p = s.get(Project, job.payload["project_id"], with_for_update=True)
        if p is not None and p.status in ("CREATED", "RUNNING"):
            p.status, p.error = "FAILED", (msg("queue.cancelled.run", lang, id=job.id) if cancelled
                                           else msg("queue.gave_up.run_lost", lang) if cause == "lost"
                                           else msg("queue.gave_up.run", lang, id=job.id, why=why))
        if p is not None and p.status == "FAILED" and not cancelled:
            notify(s, notices.run_failed(p.id, p.name, p.error or why, job.id))
    elif job.kind in DEV_KINDS:
        from justshipit.engineering import revise  # the queue is imported by everything the worker uses
        said = (msg("queue.cancelled.dev", lang, id=job.id) if cancelled
                else msg("queue.gave_up.dev", lang, id=job.id, why=why))
        d = s.get(DevJob, job.payload["dev_job_id"], with_for_update=True)
        if d is not None and revise.stopped(s, d, said):
            return
        if d is not None and d.status in DEV_ACTIVE:
            d.status, d.error = "FAILED", said
            spec = s.get(Spec, d.spec_id)
            if spec.status == "IN_DEVELOPMENT":
                spec.status = "READY_FOR_DEV"
        if d is not None and d.status == "FAILED" and not cancelled:
            _tell_dev_failed(s, d, why)
    elif job.kind == "kickoff_turn":
        k = s.get(KickoffSession, job.payload["session_id"], with_for_update=True)
        if k is not None and k.status == "OPEN" and k.turns_used == job.payload["n"] - 1:
            k.status, k.note = "PAUSED", msg("kickoff.note.cancelled" if cancelled else "kickoff.note.stalled", lang)
    elif job.kind == "product_report":
        from justshipit.engineering import current_state  # the queue is imported by everything current_state uses
        bid = job.payload["business_id"]
        latest = s.scalar(select(func.max(Job.id)).where(Job.kind == "product_report",
                                                         Job.payload["business_id"].as_integer() == bid))
        if latest == job.id:  # a reading the owner asked for again is not this job's to fail
            current_state.settle(s, bid, None, None, lang)
    elif job.kind == "triage_idea":
        idea = s.get(OwnerIdea, job.payload["idea_id"], with_for_update=True)
        latest = s.scalar(select(func.max(Job.id)).where(Job.kind == "triage_idea",
                                                         Job.payload["idea_id"].as_integer() == job.payload["idea_id"]))
        if idea is not None and idea.status in ("NEW", "TRIAGING") and latest == job.id:
            idea.status = "FAILED"
            idea.note = msg("queue.cancelled.idea" if cancelled else "queue.gave_up.idea", lang)
    elif job.kind == "idea_review":
        r = s.get(IdeaReview, job.payload["review_id"], with_for_update=True)
        if r is not None and r.status == "queued":
            r.status, r.finished_at = "failed", datetime.now(timezone.utc)
            r.note = msg("idea_review.note.cancelled" if cancelled else "idea_review.note.failed", lang)
    elif job.kind in ("cycle_propose", "cycle_revise", "cycle_summarize", "cycle_retro"):
        # The owner must never be left at a spinner: the proposal says it failed (with a button to ask again), and a
        # summary or retro the CEO could not write is written by the server from its own numbers.
        from justshipit.services import cycle  # cycle imports the queue
        p = job.payload
        if job.kind == "cycle_summarize":
            cycle.store_summary(s, p["stage_id"], p["round"], None, lang=lang)
        elif job.kind == "cycle_retro":
            cycle.store_retro(s, p["cycle_id"], None, lang=lang)
        else:
            cycle.fail_proposal(s, p["cycle_id"], p["version"], why or msg("cycle.proposal.gave_up", lang))
    elif job.kind == "chat_reply":
        from justshipit.services import chat  # chat imports the queue
        chat.reply_gave_up(s, job)
    elif job.kind == "dev_merge":
        # The merge job itself is not retried (a new owner decision is needed); the dev job must show the
        # failure so the owner can open a new attempt or merge by hand.
        d = s.get(DevJob, job.payload["dev_job_id"], with_for_update=True)
        if d is not None and d.status != DevJobStatus.MERGED.value:
            d.status, d.error = "FAILED", msg("queue.gave_up.merge", lang, id=job.id, why=why)
            _tell_dev_failed(s, d, why, merge=True)


def _tell_dev_failed(s: Session, d: DevJob, why: str, *, merge: bool = False) -> None:
    notify(s, notices.dev_job_failed(d.id, s.get(Spec, d.spec_id).spec_key, d.error or why, merge=merge))


def busy_lock_keys(s: Session) -> set[str]:
    """Lock keys of projects and dev jobs that a run or dev job in flight will still move. Starting the subject
    again before the queue settles that job would run it twice."""
    return set(s.scalars(select(Job.lock_key).where(
        Job.lock_key.is_not(None), in_flight(), Job.kind.in_(RUN_KINDS + DEV_KINDS))))


def worker_seen(s: Session, worker_id: str) -> None:
    s.execute(insert(Worker).values(id=worker_id).on_conflict_do_update(
        index_elements=[Worker.id], set_={"seen_at": func.now()}))


def last_worker_seen(s: Session) -> tuple[datetime | None, bool]:
    """When a worker last reported in, and whether that is recent enough to count it alive."""
    seen = func.max(Worker.seen_at)
    last, alive = s.execute(select(seen, seen >= _cutoff(lease_s()))).one()
    return last, bool(alive)


def live_lease_owners(s: Session) -> set[str]:
    """Workers that hold a job in flight. Taken under the claim lock, so no claim or reclaim lands while the
    caller acts on the answer inside this transaction."""
    take_claim_lock(s)
    return set(s.scalars(select(Job.locked_by).distinct().where(in_flight(), Job.locked_by.is_not(None))))


def reclaim(s: Session, held: Mapping[int, str | None], gone: Collection[str] = ()) -> None:
    """Settle lapsed leases, and the leases of the `gone` holders whatever their age. Claims do this too; the worker's
    tick also calls it, so a job whose worker died is settled even while every pool slot is busy."""
    take_claim_lock(s)
    _reclaim_stale(s, held, gone)


def silent_holders(s: Session) -> set[str]:
    """Workers that hold a running job and have not reported in for two heartbeats: the ones a restart may have
    killed. Silence proves nothing on its own (runner._gone_holders)."""
    quiet = or_(Worker.seen_at.is_(None), Worker.seen_at < _after(-2 * heartbeat_s()))
    return set(s.scalars(select(Job.locked_by).distinct().outerjoin(Worker, Worker.id == Job.locked_by)
                         .where(Job.status == "running", Job.locked_by.is_not(None), quiet)))


def settle_lapsed() -> None:
    """`reclaim` in a transaction of its own, for an owner action that may refuse naming a reclaimed job's hold
    end: a refusal rolls its own transaction back, and a reclaim inside it would leave the next click to reclaim
    again and name a later end."""
    with session_scope() as s:
        reclaim(s, {})


def claim(s: Session, worker_id: str, kinds: list[str] | None = None, *,
          held: Mapping[int, str | None] | None = None) -> Job | None:
    """Take the oldest runnable job. `held` maps the jobs this worker is still executing to their lease
    tokens (None once lost): none of them is handed out again while its handler runs, and a held lease that
    merely looks stale is not reclaimed. The claim's `lease_token` is what heartbeat and finish must carry."""
    held = held or {}
    reclaim(s, held)
    holder = aliased(Job)
    key_busy = select(holder.id).where(or_(holder.status == "running", holder.held_until > func.now()),
                                       holder.lock_key == Job.lock_key).exists()
    first = case((Job.kind.in_(NOTICE_KINDS), 0), else_=1)
    # The gate's guarantee: a `stage` job starts only while its stage runs, or closes with the job already begun
    # (`_begun`). Any other gate value, or none, never starts.
    approved = or_(Job.gate.in_(("none", "direct")), and_(Job.gate == "stage", _stage_admits()))
    q = (select(Job).where(Job.status == "queued", Job.run_after <= func.now(), ~key_busy, approved)
         .order_by(first, Job.id).limit(1).with_for_update(skip_locked=True))
    q = _not_stopped(q, stops.read(s))
    if kinds:
        q = q.where(Job.kind.in_(kinds))
    if held:
        q = q.where(Job.id.not_in(list(held)))
    job = s.scalar(q)
    if job:
        job.status = "running"
        job.locked_by = worker_id
        job.lease_token = uuid.uuid4().hex
        job.locked_at = func.now()
        job.attempts += 1
        job.wait_reason = job.waits_on = None
        s.flush()
    return job


def _not_stopped(q, held: stops.Stops):
    """The owner's stops at the claim: while the company is stopped only housekeeping starts, and while an agent is
    stopped neither its own kinds nor work that already reached it and waits on it."""
    if held.company is not None:
        q = q.where(Job.kind.in_(sorted(stops.HOUSEKEEPING)))
    if held.agents:
        q = q.where(Job.kind.not_in(stops.held_kinds(held.agents)),
                    or_(Job.waits_on.is_(None), Job.waits_on.not_in(list(held.agents))))
    return q


def heartbeat(s: Session, leases: Mapping[int, str]) -> set[int]:
    """Renew the given leases (job id -> token) and return the ids still held. An id missing from the answer
    was reclaimed: its holder must treat that run as lost."""
    if not leases:
        return set()
    renewed = s.execute(update(Job).where(Job.status == "running",
                                          tuple_(Job.id, Job.lease_token).in_(list(leases.items())))
                        .values(locked_at=func.now()).returning(Job.id))
    return set(renewed.scalars())


def finish(s: Session, job_id: int, lease_token: str | None, error: str | None = None, *, final: bool = False) -> None:
    """Record how a job ended: done, queued to try again, or failed once its attempts are used up. `final` fails it
    at once."""
    job = s.get(Job, job_id, with_for_update=True)
    if job is None or job.status != "running" or lease_token is None or job.lease_token != lease_token:
        return  # lease lost: the job was reclaimed and now belongs to its new holder
    if error is None:
        job.status = "done"
    elif final or job.attempts >= job.max_attempts:
        job.status = "failed"
    else:
        job.status = "queued"
        job.run_after = _after(60 * job.attempts)
    job.last_error = error
    job.locked_by = job.lease_token = None
    job.finished_at = datetime.now(timezone.utc) if job.status in ("done", "failed") else None
    if job.status == "failed":
        fail_subject(s, job)


def max_deferrals() -> int:
    return int(company_config()["operations"].get("max_deferrals", 48))


def defer(s: Session, job_id: int, lease_token: str | None, wait: MustWait) -> None:
    """Put a running job back in the queue until `wait.until`. The attempt it used is returned: nothing failed.
    A run continues from its LangGraph checkpoint, whichever kind it started as. A job that has waited
    `max_deferrals` times fails instead, so a wait that never really ends (a reset time read wrong) cannot keep it,
    and its notice, coming back forever; the owner's retry starts it again from its checkpoint."""
    job = s.get(Job, job_id, with_for_update=True)
    if job is None or job.status != "running" or lease_token is None or job.lease_token != lease_token:
        return  # lease lost: the job was reclaimed and now belongs to its new holder
    if job.kind in RUN_KINDS:
        job.kind = "retry_project"
    job.deferrals += 1
    if job.deferrals > max_deferrals():
        job.status, job.finished_at = "failed", datetime.now(timezone.utc)
        job.last_error = msg("queue.deferred_out", app_settings.language(), n=max_deferrals(), why=wait.detail)
        job.locked_by = job.lease_token = None
        fail_subject(s, job)
        return
    job.status = "queued"
    job.run_after = func.greatest(wait.until, _after(MIN_DEFER_S))  # on the DB clock, so a skewed worker never spins
    job.attempts = max(0, job.attempts - 1)
    job.wait_reason, job.last_error = wait.reason, wait.detail
    job.locked_by = job.lease_token = None
    notify(s, _usage_wait_notice(wait))


def _stage_admits():
    """A stage job may start while its stage runs. A closing stage lets work that already began finish (`locked_at`
    is set once a worker has held the job, and stays through a release or reclaim), but starts none that never did."""
    running = select(CycleStage.id).where(CycleStage.status == "running")
    closing = select(CycleStage.id).where(CycleStage.status == "closing")
    return or_(Job.stage_id.in_(running), and_(Job.stage_id.in_(closing), Job.locked_at.is_not(None)))


def release(s: Session, job_id: int, lease_token: str | None) -> None:
    """Hand a job back after its handler stopped for a worker shutdown. Unlike a reclaim, this holder is known to be
    done: its handler has returned, and every call, test run and container it starts is waited for before the
    handler goes on (one the stop ended goes with everything it started). So the next worker may take the job now,
    and a run continues from its LangGraph checkpoint. The attempt it used is returned."""
    job = s.get(Job, job_id, with_for_update=True)
    if job is None or job.status != "running" or lease_token is None or job.lease_token != lease_token:
        return  # lease lost: the job was reclaimed and now belongs to its new holder
    if job.kind in RUN_KINDS:
        job.kind = "retry_project"
    stopped = job.gate == "stage" and not gate.stage_is_open(s, job.stage_id)
    job.status = "gated" if stopped else "queued"
    job.run_after = func.now()
    job.attempts = max(0, job.attempts - 1)
    job.last_error = _released_why(s, job, stopped)
    job.locked_by = job.lease_token = None


def _released_why(s: Session, job: Job, stage_stopped: bool) -> str:
    """What the owner reads on a job handed back: the owner's own stop that did it, or the worker's shutdown."""
    lang = app_settings.language()
    if stage_stopped:
        return msg("queue.parked_stage", lang)
    held = stops.job_held(stops.read(s), job.kind, job.gate)
    if held == stops.COMPANY:
        return msg("queue.parked_company", lang)
    if held is not None:
        return stops.waiting_text(held, lang)
    return msg("queue.released", lang)


def release_gated(s: Session, business_id: int, stage_id: int) -> list[Job]:
    """The owner started (or resumed) a stage: the business's parked work joins it and may start now. A job parked in
    another stopped stage stays where it is. One whose project was archived meanwhile is closed with a note instead."""
    lang = app_settings.language()
    out = []
    for job in s.scalars(select(Job).where(Job.status == "gated", Job.business_id == business_id,
                                           or_(Job.stage_id.is_(None), Job.stage_id == stage_id))
                         .order_by(Job.id).with_for_update()):
        pid = (job.payload or {}).get("project_id")
        if pid is not None and s.scalar(select(Project.status).where(Project.id == pid)) == "ARCHIVED":
            _close(job, msg("queue.gated.archived", lang))
            continue
        job.status, job.gate, job.stage_id, job.run_after = "queued", "stage", stage_id, func.now()
        out.append(job)
    return out


def park_stranded(s: Session) -> int:
    """Queued stage work whose stage no longer runs (the owner stopped it) waits as `gated` again, so the queue does
    not show it as about to start. It keeps its stage, so resuming that stage releases the same rows."""
    return s.execute(update(Job).where(Job.status == "queued", Job.gate == "stage",
                                       or_(Job.stage_id.is_(None), ~_stage_admits()))
                     .values(status="gated").execution_options(synchronize_session=False)).rowcount


def close_gated(s: Session, business_id: int, why: str) -> list[Job]:
    """Close the business's parked work with a reason the owner reads on each job: the owner stopped the cycle, or
    archived the business, so it will never run."""
    out = list(s.scalars(select(Job).where(Job.status == "gated", Job.business_id == business_id)
                        .order_by(Job.id).with_for_update()))
    for job in out:
        _close(job, why)
    return out


def close_queued(s: Session, business_id: int, kind: str, why: str) -> list[Job]:
    """Close the business's jobs of this kind that have not started, for work that is pointless once the business is
    archived and cheap enough that the owner should not have to wait for it."""
    out = list(s.scalars(select(Job).where(Job.status == "queued", Job.kind == kind, Job.business_id == business_id)
                        .order_by(Job.id).with_for_update()))
    for job in out:
        _close(job, why)
    return out


def _close(job: Job, why: str) -> None:
    job.status, job.last_error, job.finished_at = "failed", why, datetime.now(timezone.utc)


def hold(s: Session, job_id: int, lease_token: str | None, held: Hold) -> None:
    """Put a running job back in the queue for `held.seconds` on the database clock. The attempt it used is
    returned: nothing failed."""
    job = s.get(Job, job_id, with_for_update=True)
    if job is None or job.status != "running" or lease_token is None or job.lease_token != lease_token:
        return  # lease lost: the job was reclaimed and now belongs to its new holder
    if job.kind in RUN_KINDS:
        job.kind = "retry_project"   # a run held mid-graph carries on from its checkpoint
    waits_on = held.agent if isinstance(held, AgentStopped) else None
    job.status = "queued"
    job.run_after = func.now() if waits_on else _after(max(held.seconds, 1))   # the claim waits on the agent instead
    job.attempts = max(0, job.attempts - 1)
    job.last_error = held.why
    job.waits_on = waits_on
    job.locked_by = job.lease_token = None


def release_notices(s: Session) -> int:
    """Let the notices held for a Slack that refused the token try again now, once the owner has fixed it. A notice
    held for quiet hours is held again by the sender."""
    return s.execute(update(Job).where(Job.status == "queued", Job.kind.in_(NOTICE_KINDS), Job.run_after > func.now())
                     .values(run_after=func.now())).rowcount


def _usage_wait_notice(wait: MustWait) -> notices.Notice:
    """One notice per reason, provider and hour (UTC) of resuming, whatever number of jobs wait on it."""
    try:
        zone = app_settings.effective().usage.timezone
    except Exception:  # noqa: BLE001 - the zone only words the message
        zone = "UTC"
    return notices.usage_wait(wait.reason, wait.provider, wait.until, zone, wait.detail)


def usage_waiting(s: Session) -> list[Job]:
    """Queued jobs that wait on a usage cap or the subscription, the latest to resume first."""
    return list(s.scalars(select(Job).where(Job.status == "queued", Job.wait_reason.is_not(None),
                                            Job.run_after > func.now()).order_by(Job.run_after.desc(), Job.id)))


def release_usage_waits(s: Session) -> int:
    """Let the jobs waiting on a usage cap try again now. They check the caps as they start, so a job whose cap
    is still over goes back to waiting."""
    return s.execute(update(Job).where(Job.status == "queued", Job.wait_reason.in_(("usage_cap_day", "usage_cap_week")))
                     .values(run_after=func.now())).rowcount
