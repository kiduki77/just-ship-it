"""Job worker: claims queued jobs and runs them in a thread pool.

Jobs sharing a lock key (one project's graph run, one dev job) never run at once; the queue enforces it
across worker processes at claim time, so a busy project never holds a pool slot.
"""
from __future__ import annotations

import logging
import math
import os
import signal
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from justshipit.db.models import Job
from justshipit.db.session import session_scope
from justshipit.runtime import children
from justshipit.sandbox import docker
from justshipit.services import budget, gate, queue
from justshipit.services.usage import MustWait
from justshipit.settings import HOST, MACHINE, PROCESS_ID

log = logging.getLogger("justshipit.worker")


def _handlers() -> dict[str, Callable[[dict[str, Any]], Any]]:
    from justshipit.capability import factory
    from justshipit.engineering import current_state, plans, revise, specs, usable, worker
    from justshipit.orchestration import audit, realign, runner
    from justshipit.operations import jobs as ops
    from justshipit.services.profile import infer
    from justshipit.services import (
        app_settings, chat_reply, control, cycle_project_manager, door_rules, idea_review, ideas, kickoff, people_finder,
    )
    return {
        "cycle_propose": lambda p: cycle_project_manager.propose(p["cycle_id"], p["version"], attempt=p["attempt"],
                                                     max_attempts=p["max_attempts"]),
        "cycle_revise": lambda p: cycle_project_manager.propose(p["cycle_id"], p["version"], attempt=p["attempt"],
                                                    max_attempts=p["max_attempts"]),
        "cycle_summarize": lambda p: cycle_project_manager.summarize(p["stage_id"], p["round"], attempt=p["attempt"],
                                                         max_attempts=p["max_attempts"]),
        "cycle_retro": lambda p: cycle_project_manager.retro(p["cycle_id"], attempt=p["attempt"], max_attempts=p["max_attempts"]),
        "skill_factory": lambda p: factory.run_factory(),
        "skill_activate": lambda p: factory.activate(p["skill_id"]),
        "raise_budget": lambda p: control.raise_for_request_id(p["request_id"]),
        **ops.HANDLERS,
        "kickoff_turn": lambda p: kickoff.run_turn(p["session_id"], p["n"], p.get("close", False),
                                                   attempt=p["attempt"], max_attempts=p["max_attempts"]),
        "triage_idea": lambda p: ideas.run_triage(p["idea_id"], attempt=p["attempt"], max_attempts=p["max_attempts"]),
        "chat_reply": lambda p: chat_reply.run(p["thread_id"], p["agent"], hop=p.get("hop", 1),
                                               escalation=p.get("escalation_id"), recall=p.get("recall_seq")),
        "idea_review": lambda p: idea_review.run_lens(p["review_id"]),
        "derive_door_rules": lambda p: door_rules.derive(p["brief_id"]),
        "profile_infer": lambda p: infer.run(p["business_id"], trigger=p["trigger"], answer_id=p.get("answer_id"),
                                             brief_id=p.get("brief_id"), lang=app_settings.language()),
        "find_people": lambda p: people_finder.find_people(p["business_id"], lang=app_settings.language()),
        "find_threads": lambda p: people_finder.find_threads(p["business_id"], lang=app_settings.language()),
        "run_project": lambda p: runner.start(p["project_id"]),
        "resume_project": lambda p: runner.resume(p["project_id"]),
        "retry_project": lambda p: runner.retry(p["project_id"]),
        "audit_task": lambda p: audit.run(p["task_id"]),
        "realign_project": lambda p: realign.realign(p["project_id"], p.get("request_id"), p.get("directive"),
                                                     p.get("owner_task"), resume_after=p.get("resume_after", False)),
        "write_plan": lambda p: plans.write_plan(p["project_id"], p["plan_id"]),
        "redraft_plan": lambda p: plans.redraft_plan(p["project_id"], p["plan_id"], p["request_id"], p["commit"],
                                                     p["note"]),
        "write_spec": lambda p: specs.write_spec(p["project_id"], p["request_id"]),
        "redraft_spec": lambda p: specs.redraft_spec(p["spec_id"], p["request_id"]),
        "spec_approved": lambda p: specs.on_spec_approved(p["spec_id"], p["request_id"]),
        "spec_questions": lambda p: specs.on_questions_answered(p["spec_id"]),
        "dev_job": lambda p: worker.run_dev_job(p["dev_job_id"]),
        "dev_resume": lambda p: worker.resume_after_answer(p["dev_job_id"]),
        "dev_deliver": lambda p: worker.run_delivery(p["dev_job_id"]),
        "product_report": lambda p: current_state.run(p["business_id"], p["attempt"], p["max_attempts"]),
        "acceptance": lambda p: worker.acceptance(p["dev_job_id"], p["attempt"], p["max_attempts"]),
        "dev_merge": lambda p: worker.merge(p["dev_job_id"], p.get("head")),
        "dev_merge_declined": lambda p: worker.merge_declined(p["dev_job_id"], p["request_id"]),
        "dev_fix_answered": lambda p: revise.from_request(p["dev_job_id"], p["request_id"]),
        "dev_fix": lambda p: revise.run(p["dev_job_id"], p["n"]),
        "dev_merged": lambda p: usable.after_merge(p["dev_job_id"]),
    }


def handles(kind: str) -> bool:
    """Whether a worker would run a job of this kind."""
    return kind in _handlers()


class Runs:
    """The jobs this process is executing. The claim loop adds and reads, handler threads pop and the
    heartbeat reads, so every access takes the lock. A lease is flagged lost in place, never replaced, so a
    beat that ends after its job cannot put the job back."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._leases: dict[int, queue.Lease] = {}

    def add(self, lease: queue.Lease) -> None:
        with self._lock:
            self._leases[lease.job_id] = lease

    def pop(self, job_id: int) -> None:
        with self._lock:
            self._leases.pop(job_id, None)

    def leases(self) -> list[queue.Lease]:
        with self._lock:
            return list(self._leases.values())

    def held(self) -> dict[int, str | None]:
        """What the queue's claim takes: job id to lease token, None once lost. A key stays until the handler's
        finish has committed, so the claim loop never hands that job out again."""
        return {lease.job_id: None if lease.lost.is_set() else lease.token for lease in self.leases()}


def _execute(job_id: int, kind: str, payload: dict[str, Any], handlers, runs: Runs, lease: queue.Lease) -> None:
    error, stopped, waiting, held, final, handed_back = None, False, None, None, False, False
    ctx = queue.current_lease.set(lease)
    try:
        log.info("job %s %s %s started", job_id, kind, payload)
        with session_scope() as s:
            job = s.get(Job, job_id)
            attempt, max_attempts = (job.attempts, job.max_attempts) if job else (1, 1)
        handlers[kind]({**payload, "attempt": attempt, "max_attempts": max_attempts})
        log.info("job %s %s done", job_id, kind)
    except queue.Stopping:
        handed_back = True
        log.info("job %s %s stopped for the worker's shutdown; it goes back to the queue", job_id, kind)
    except queue.LeaseLost:
        stopped = True
        log.error("job %s %s stopped at a lease check: another worker owns it now", job_id, kind)
    except MustWait as wait:
        waiting = wait
        log.info("job %s %s waits until %s (%s)", job_id, kind, wait.until, wait.reason)
    except queue.Hold as hold:
        held = hold
        log.info("job %s %s held for %ss: %s", job_id, kind, int(hold.seconds), hold.why)
    except queue.GiveUp as exc:
        error, final = str(exc), True
        log.error("job %s %s gave up: %s", job_id, kind, exc)
    except Exception as exc:  # noqa: BLE001 - recorded on the job
        error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-3000:]}"
        log.error("job %s %s failed: %s", job_id, kind, exc)
    finally:
        queue.current_lease.reset(ctx)
        lease.finishing = True
        if lease.lost.is_set():
            if not stopped:
                log.error("job %s %s finished after its lease was lost; its result is discarded", job_id, kind)
        else:
            try:
                with session_scope() as s:
                    if handed_back:
                        queue.release(s, job_id, lease.token)
                    elif waiting:
                        queue.defer(s, job_id, lease.token, waiting)
                    elif held:
                        queue.hold(s, job_id, lease.token, held)
                    elif final:
                        queue.finish(s, job_id, lease.token, error, final=True)
                    else:
                        queue.finish(s, job_id, lease.token, error)
            except Exception as exc:  # noqa: BLE001 - its lease lapses and the queue reclaims it
                log.error("job %s could not be recorded as finished: %s", job_id, exc)
        runs.pop(job_id)


def _beat(worker_id: str, runs: Runs) -> None:
    leases = [lease for lease in runs.leases() if not lease.lost.is_set()]
    try:
        with session_scope() as s:
            queue.worker_seen(s, worker_id)
            renewed = queue.heartbeat(s, {lease.job_id: lease.token for lease in leases})
    except Exception as exc:  # noqa: BLE001 - a missed beat is retried; the lease outlasts several
        log.error("worker heartbeat failed: %s", exc)
        return
    # A job that finished meanwhile was not reclaimed.
    still_running = {lease.job_id for lease in runs.leases() if not lease.finishing}
    for lease in leases:
        if lease.job_id in renewed:
            lease.confirmed_at = time.time()
        elif lease.job_id in still_running:
            lease.lost.set()
            log.error("job %s: LEASE LOST. Another worker reclaimed it after %ss without a heartbeat from this "
                      "process; this copy stops at its next lease check and its result is discarded",
                      lease.job_id, int(queue.lease_s()))


def _heartbeat(worker_id: str, runs: Runs, every_s: float, done: threading.Event) -> None:
    while not done.wait(every_s):
        _beat(worker_id, runs)


def _reap_orphans() -> None:
    """A killed worker leaves budget reservations and running engineer and test containers behind. Clear what no live
    lease owns; its jobs are reclaimed by the queue once their lease lapses."""
    try:
        if released := budget.release_orphaned_reservations():
            log.info("released %.2f USD-eq of budget reserved by a dead worker", released)
        # List before reading leases: a container seen here was started after its owner's claim committed.
        if boxes := docker.containers("ship-eng-", docker.TEST_CONTAINER_PREFIX):
            with session_scope() as s:
                owners = queue.live_lease_owners(s)
            if orphans := [name for name, owner in boxes.items() if owner not in owners]:
                docker.remove(orphans)
                log.info("removed engineer and test containers of dead workers: %s", orphans)
    except Exception as exc:  # noqa: BLE001 - retried on the next tick
        log.error("orphan cleanup failed: %s", exc)


def _pid_here(worker_id: str) -> int | None:
    """The pid of a worker that ran on this host, from its id (settings.PROCESS_ID: host:pid:random); None for another
    host's, whose process this one cannot see. The host part names the kernel too (settings.HOST)."""
    parts = worker_id.rsplit(":", 2)
    if len(parts) != 3 or parts[0] != HOST or not parts[1].isdigit():
        return None
    return int(parts[1])


def _gone_holders() -> set[str]:
    """Holders a restart killed: workers of this host that stopped reporting, whose process is gone, with every
    container they started removed. What a killed worker can still leave running is a `claude -p` call that only reads
    and answers to nobody, so nothing of theirs can change a job any more, and their jobs need not wait out a lease
    and a hold (queue.reclaim's `gone`). Not under host isolation, where the engineer is such a call and writes."""
    from justshipit.settings import company_config
    if company_config()["engineering"].get("isolation", "container") != "container" or not MACHINE:
        return set()  # no kernel id: another kernel's worker could share this host id, so its pid means nothing here
    with session_scope() as s:
        silent = queue.silent_holders(s)
    gone = set()
    for holder in silent:
        pid = _pid_here(holder)
        if holder == PROCESS_ID or pid is None or children.alive(pid):
            continue
        left = docker.started_by(holder)
        if left:
            docker.remove(left)
            left = docker.started_by(holder)
            log.info("removed the containers of stopped worker %s", holder)
        if left == []:
            gone.add(holder)
    return gone


def _stop_on_signals(stop: threading.Event) -> Callable[[], None]:
    """SIGTERM/SIGINT stop claiming; running jobs stop at their next lease check and go back to the queue (serve).
    A second signal exits at once: a thread cannot be killed, and a KeyboardInterrupt would leave the pool draining
    while the queue reclaims its jobs."""
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}

    def restore() -> None:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    def force(signum: int) -> None:
        log.warning("second signal %s: exiting now; running jobs are reclaimed when their lease lapses", signum)
        os._exit(128 + signum)

    on_signal = children.stop_handler(
        stop, lambda signum: log.info("signal %s: stopping running jobs and handing them back, claiming no new ones",
                                      signum), force)
    for sig in previous:
        signal.signal(sig, on_signal)
    return restore


def serve(concurrency: int = 3, poll_s: float = 3.0, once: bool = False,
          stop: threading.Event | None = None) -> None:
    handlers = _handlers()
    gate.check_handlers(handlers)
    worker_id = PROCESS_ID
    stop = stop or threading.Event()
    active: set = set()
    runs = Runs()
    from justshipit.operations import scheduler
    from justshipit.services import holds, requests
    from justshipit.settings import company_config
    ops = company_config().get("operations", {})
    tick_every = float(ops.get("scheduler_tick_s", 60))
    restore_signals = _stop_on_signals(stop)
    beats_done = threading.Event()
    _beat(worker_id, runs)
    threading.Thread(target=_heartbeat, args=(worker_id, runs, float(ops["job_heartbeat_s"]), beats_done),
                     name="job-heartbeat", daemon=True).start()
    last_tick = -math.inf
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            while not stop.is_set():
                active = {f for f in active if not f.done()}
                if time.monotonic() - last_tick >= tick_every:
                    last_tick = time.monotonic()
                    _beat(worker_id, runs)  # our own leases are fresh before anything judges them
                    try:
                        gone = _gone_holders()
                    except Exception as exc:  # noqa: BLE001 - the lease path below still runs
                        log.error("checking for stopped workers failed: %s", exc)
                        gone = set()
                    try:
                        with session_scope() as s:
                            queue.reclaim(s, runs.held(), gone)
                    except Exception as exc:  # noqa: BLE001 - retried on the next tick
                        log.error("reclaiming lapsed leases failed: %s", exc)
                    _reap_orphans()
                    try:
                        with session_scope() as s:
                            if fired := scheduler.tick(s):
                                log.info("scheduler fired %s", fired)
                    except Exception as exc:  # noqa: BLE001 - scheduler must not stop the worker
                        log.error("scheduler tick failed: %s", exc)
                    try:
                        with session_scope() as s:
                            if expired := requests.expire_due(s, None, datetime.now(timezone.utc)):
                                log.info("unanswered decisions expired by their rule: %s", expired)
                    except Exception as exc:  # noqa: BLE001 - retried on the next tick
                        log.error("expiring unanswered decisions failed: %s", exc)
                    try:
                        with session_scope() as s:
                            if woken := holds.reconcile_parked(s):
                                log.info("resuming parked runs %s", woken)
                    except Exception as exc:  # noqa: BLE001 - retried on the next tick
                        log.error("resuming parked runs failed: %s", exc)
                job = None
                if len(active) < concurrency:
                    try:
                        with session_scope() as s:
                            j = queue.claim(s, worker_id, list(handlers), held=runs.held())
                            if j:
                                job = (j.id, j.kind, dict(j.payload), j.lease_token, j.gate, j.stage_id)
                    except Exception as exc:  # noqa: BLE001 - a database outage must not stop the worker
                        log.error("claiming a job failed: %s", exc)
                        stop.wait(poll_s)
                        continue
                if job:
                    job_id, kind, payload, token, job_gate, stage_id = job
                    lease = queue.Lease(job_id, token, stop=stop, gate=job_gate, stage_id=stage_id, kind=kind)
                    runs.add(lease)
                    active.add(pool.submit(_execute, job_id, kind, payload, handlers, runs, lease))
                    continue
                if once and not active:
                    return
                stop.wait(poll_s)
            # Without this a handler would reach its next lease check only after its calls and containers end.
            for lease in runs.leases():
                lease.children.stop(docker.remove)
    finally:
        beats_done.set()
        restore_signals()
