"""Adversarial probes of docs/design/cycle-stages (not a real test suite). Each probe emulates one rule of the
design on top of the current code and prints what happens."""
from datetime import UTC, datetime, timedelta

import pytest
from justshipit.db.models import Bet, Job, Schedule
from justshipit.db.session import session_scope
from justshipit.operations import scheduler
from justshipit.services import bet_loop, people_finder, queue, roadmap
from sqlalchemy import select

import bet_loop_kit as kit
from test_bet_retro import ended_bet

RECURRING = {"find_people", "find_threads", "promotion_week", "github_collect", "kpi_review", "evidence_watch",
             "routine_run", "portfolio_review", "skill_factory", "post_reactions"}


# ---- probe A: design 2.4 "unlisted STAGE_RECURRING -> enqueue returns None (skipped)" + scheduler.tick unchanged
def test_probe_skipped_tick_pushes_the_schedule_a_week_out(s, monkeypatch):
    from people_kit import make_business
    b = make_business(s, name="probe-sched")
    scheduler.sync(s)
    sc = s.scalar(select(Schedule).where(Schedule.kind == "find_people", Schedule.payload["business_id"].as_integer() == b.id))
    sc.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    s.flush()
    real = queue.enqueue

    def design_enqueue(x, kind, payload, **kw):   # no open stage lists the kind -> skip, as gate.admit says
        if kind in RECURRING:
            return None
        return real(x, kind, payload, **kw)

    monkeypatch.setattr(scheduler.queue, "enqueue", design_enqueue)
    fired = scheduler.tick(s)
    print(f"\nPROBE-A tick while gate shut: fired={sc.id in fired} next_run_at in "
          f"{(sc.next_run_at - datetime.now(UTC)).total_seconds() / 3600:.1f} h")
    monkeypatch.setattr(scheduler.queue, "enqueue", real)   # owner now starts a stage whose allows_kinds lists find_people
    fired2 = scheduler.tick(s)
    jobs = [j for j in s.scalars(select(Job).where(Job.kind == "find_people")) if j.payload.get("business_id") == b.id]
    print(f"PROBE-A tick after the stage that lists find_people started: fired={sc.id in fired2} "
          f"find_people jobs for B-{b.id}: {len(jobs)}")


# ---- probe B: design 2.4b point 4, check_lease raises GiveUp when lease.gate == 'none' (FREE kinds)
def test_probe_free_lease_gives_up_at_its_first_commit(monkeypatch):
    real_check = queue.check_lease

    def design_check_lease():
        lease = queue.current_lease.get()
        if lease is not None and getattr(lease, "gate", "direct") == "none":
            raise queue.GiveUp("a FREE job kind tried to spend model usage")
        real_check()

    monkeypatch.setattr(queue, "check_lease", design_check_lease)
    lease = queue.Lease(job_id=1, token="t")
    lease.gate = "none"          # what runner would pass for notify / backup / bet_sweep / cycle_tick ...
    ctx = queue.current_lease.set(lease)
    try:
        with session_scope():    # every FREE handler (bet_loop.run_sweep, retention, notify.deliver) commits like this
            pass
        print("\nPROBE-B FREE handler committed")
    except queue.GiveUp as exc:
        print(f"\nPROBE-B FREE handler's plain commit raised GiveUp: {exc}")
    finally:
        queue.current_lease.reset(ctx)


# ---- probe C: design 2.4 park (no open stage -> status 'gated') + `in_flight()` unchanged, then the FREE bet_sweep
@pytest.fixture
def _world():
    kit.reset_world()
    kit.set_bets(failed_grace_minutes=0)
    kit.clear_jobs("run_project", "bet_retro", "routine_run")
    yield
    kit.reset_world()


def test_probe_parked_retro_is_requeued_by_every_sweep(s, _world, monkeypatch):
    business = kit.make_business(s)
    bet = ended_bet(s, business)                       # REVIEW, one bet_retro queued by the sweep
    real = queue.enqueue

    def design_enqueue(x, kind, payload, **kw):        # managed business, gate shut -> STAGE_WORK parks
        job = real(x, kind, payload, **kw)
        if job is not None and kind == "bet_retro":
            job.status = "gated"
            x.flush()
        return job

    with session_scope() as x:                          # the retro that the first sweep queued is parked as well
        for j in x.scalars(select(Job).where(Job.kind == "bet_retro", Job.payload["bet_id"].as_integer() == bet.id)):
            j.status = "gated"
    monkeypatch.setattr(roadmap.queue, "enqueue", design_enqueue)
    for _ in range(4):                                  # bet_sweep runs every 15 minutes
        with session_scope() as x:
            bet_loop.sweep(x, lang="ko")
    s.expire_all()
    rows = list(s.scalars(select(Job).where(Job.kind == "bet_retro", Job.payload["bet_id"].as_integer() == bet.id)))
    b = s.get(Bet, bet.id)
    print(f"\nPROBE-C after 4 sweeps with the gate shut: bet_retro rows={[(j.id, j.status) for j in rows]} "
          f"retro_stuck={roadmap.retro_stuck(s, b)} status_reason={b.status_reason!r}")


# ---- probe D: the owner's own "find now" button and the console's job retry
def test_probe_owner_buttons_do_not_say_by_owner(s, monkeypatch):
    from people_kit import make_business
    b = make_business(s, name="probe-find-now")
    seen = []
    monkeypatch.setattr(people_finder.queue, "enqueue", lambda x, kind, payload, **kw: seen.append((kind, sorted(kw))))
    people_finder.request(s, b.id, lang="ko")
    print(f"\nPROBE-D people_finder.request (api/people.py:106, the owner's 'find now') enqueues: {seen}")


def test_probe_console_retry_of_a_failed_recurring_job(s, api_token, monkeypatch):
    from justshipit.api import console
    from justshipit.api.app import app
    from fastapi.testclient import TestClient
    from people_kit import make_business
    b = make_business(s, name="probe-retry")
    failed = Job(kind="find_people", payload={"business_id": b.id}, max_attempts=1, status="failed", attempts=1,
                 last_error="RuntimeError: x", finished_at=datetime.now(UTC))
    s.add(failed)
    s.commit()
    real = queue.enqueue
    monkeypatch.setattr(console.queue, "enqueue",
                        lambda x, kind, payload, **kw: None if kind in RECURRING else real(x, kind, payload, **kw))
    api = TestClient(app, base_url="http://127.0.0.1:8000", headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})
    r = api.post(f"/api/jobs/{failed.id}/retry")
    print(f"\nPROBE-H owner presses retry on failed find_people J-{failed.id}: HTTP {r.status_code} {r.json()}")
