"""Adversary probes for docs/design/cycle-stages. Not part of the suite. Every probe runs the REAL code of this tree;
where the design is not built yet, the design's own rule (copied from design.md 2.4 / 4.2) is applied to what the real
code produced, and the probe says so."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update

from justshipit.contracts.requests import HumanDecision, HumanResponse
from justshipit.db.models import Bet, Job, Project, Schedule, Task, DevJob, Spec
from justshipit.db.session import session_scope
from justshipit.services import bet_loop, control, projects, queue, requests, roadmap, scope
from bet_loop_kit import make_bet, make_business, reset_world, run_of, start
from test_one_card import dev, spec_for
from ask_kit import by_project_manager


def jobs_of(s, kind, **payload):
    out = []
    for j in s.scalars(select(Job).where(Job.kind == kind).order_by(Job.id)):
        if all(j.payload.get(k) == v for k, v in payload.items()):
            out.append(j)
    return out


# ----------------------------------------------------------------------------------------------------------- F1
def test_f1_requeue_with_the_same_key_after_a_failed_job_is_a_noop(s):
    """design 2.2: `tick` re-enqueues `cycle_summarize` (key cycle_summary:{stage}:{round}) / `cycle_propose`
    (cycle_propose:{cycle}:{version}) / `cycle_retro` (cycle_retro:{cycle}) when the first job is dead."""
    key = "cycle_summary:9001:1"
    first = queue.enqueue(s, "cycle_summarize", {"stage_id": 9001, "round": 1}, idempotency_key=key, max_attempts=1)
    s.commit()
    got = queue.claim(s, "probe-worker", kinds=["cycle_summarize"])
    assert got is not None and got.id == first.id
    queue.finish(s, got.id, got.lease_token, error="worker died / model failed", final=True)
    s.commit()
    s.refresh(first)
    print(f"\nF1 first job: id={first.id} status={first.status}")
    again = queue.enqueue(s, "cycle_summarize", {"stage_id": 9001, "round": 1}, idempotency_key=key, max_attempts=1)
    s.commit()
    print(f"F1 re-enqueue of the same logical job returned: {again!r}")
    live = s.scalars(select(Job).where(Job.kind == "cycle_summarize", queue.in_flight())).all()
    print(f"F1 live cycle_summarize jobs afterwards: {len(live)}")
    # for contrast: how the existing retro code avoids exactly this trap
    print(f"F1 existing code's own answer: roadmap.queue_retro keys 'bet_retro:{{bet}}:{{n+1}}' (roadmap.py:1056-1064)")
    assert again is None and not live


# ----------------------------------------------------------------------------------------------------------- F2
def test_f2_gated_jobs_are_invisible_to_the_existing_busy_checks(s):
    """design 5.2: 'in_flight() unchanged (gated is not in flight)'. `status='gated'` is the design's own value."""
    reset_world()
    b = make_business(s, "게이트 사업")
    pid = projects.create(s, "g", name="FailedRun", start=False, business=b).id
    p = s.get(Project, pid)
    p.status, p.error = "FAILED", "boom"
    s.commit()
    r1 = control.retry_project(s, pid)
    s.commit()
    j1 = jobs_of(s, "retry_project", project_id=pid)[-1]
    print(f"\nF2 baseline: first Retry queued job {j1.id} status={j1.status}")
    with pytest.raises(control.ControlError) as e:
        control.retry_project(s, pid)
    print(f"F2 baseline: second Retry while it is queued is REFUSED: {str(e.value)[:120]}")
    s.rollback()
    # now the design's park: the same job sits in status 'gated' (no stage is open)
    s.execute(update(Job).where(Job.id == j1.id).values(status="gated"))
    s.commit()
    clicks = 0
    for _ in range(3):
        try:
            control.retry_project(s, pid)
            s.execute(update(Job).where(Job.kind == "retry_project", Job.status == "queued").values(status="gated"))  # park each new one too
            s.commit()
            clicks += 1
        except control.ControlError as exc:
            print(f"F2 refused: {exc}")
            s.rollback()
    rows = jobs_of(s, "retry_project", project_id=pid)
    print(f"F2 gated: owner clicked Retry 3 more times, all accepted={clicks}; retry_project rows={len(rows)} "
          f"statuses={[j.status for j in rows]}")
    # when the next stage starts, release_gated flips every gated row to queued
    s.execute(update(Job).where(Job.kind == "retry_project", Job.status == "gated").values(status="queued"))
    s.commit()
    print(f"F2 after release: queued retry_project rows for this project="
          f"{len([j for j in jobs_of(s, 'retry_project', project_id=pid) if j.status == 'queued'])}")
    assert clicks == 3
    s.execute(update(Job).where(Job.kind == "retry_project").values(status="done"))
    s.commit()
    reset_world()


def test_f3_bet_start_with_a_parked_run_ticks_its_window_and_piles_up_retros(s):
    reset_world()
    b = make_business(s, "게이트 베팅")
    bet = make_bet(s, b)
    start(bet.id)
    with session_scope() as s2:
        run = run_of(s2, bet.id)
        rid = run.id
        j = jobs_of(s2, "run_project", project_id=rid)[-1]
        # the design parks this run (no stage is open): status 'gated'
        s2.execute(update(Job).where(Job.id == j.id).values(status="gated"))
        bt = s2.get(Bet, bet.id)
        bt.started_at = datetime.now(UTC) - timedelta(days=15)  # the bet's 14-day window, counted from the click
    with session_scope() as s2:
        bt = s2.get(Bet, bet.id)
        print(f"\nF3 before sweep: bet={bt.status} run={s2.get(Project, rid).status} run job={j.id} 'gated'")
        moved = bet_loop.sweep(s2, lang="en")
        print(f"F3 sweep 1 moved: {moved}")
    with session_scope() as s2:
        bt = s2.get(Bet, bet.id)
        p = s2.get(Project, rid)
        print(f"F3 after sweep 1: bet={bt.status} end_cause={bt.end_cause} run={p.status} paused={bool(p.context.get('paused'))}")
        rets = jobs_of(s2, "bet_retro", bet_id=bet.id)
        print(f"F3 retro jobs: {[(x.id, x.status) for x in rets]}")
        for x in rets:  # the design parks them too
            x.status = "gated"
    for i in range(4):
        with session_scope() as s2:
            bet_loop.sweep(s2, lang="en")
        with session_scope() as s2:
            for x in jobs_of(s2, "bet_retro", bet_id=bet.id):
                x.status = "gated"
            print(f"F3 sweep {i + 2}: gated retro rows for ONE bet = {len(jobs_of(s2, 'bet_retro', bet_id=bet.id))}")
    with session_scope() as s2:
        bt = s2.get(Bet, bet.id)
        print(f"F3 retro_stuck={roadmap.retro_stuck(s2, bt)}  status_reason={bt.status_reason!r}")
        try:
            bet_loop.redo_retro(s2, bet.id, lang="en")
            print("F3 owner 'redo retro' accepted: one more row")
        except Exception as exc:
            print("F3 redo refused", exc)
        n = len(jobs_of(s2, "bet_retro", bet_id=bet.id))
        print(f"F3 retro rows now={n}; release_gated would queue all {n} at the next Start; "
              f"lock_key of a bet_retro job={jobs_of(s2, 'bet_retro', bet_id=bet.id)[0].lock_key!r} (so they run in parallel)")
    reset_world()


# ----------------------------------------------------------------------------------------------------------- F4
def test_f4_scheduler_skip_burns_the_slot(s, monkeypatch):
    """design 2.4: an unlisted STAGE_RECURRING enqueue returns None; scheduler.tick only logs it."""
    from justshipit.operations import scheduler
    b = roadmap.create_business(s, name="스케줄 사업", one_liner="x")
    s.commit()
    scheduler.ensure(s, name=f"B-{b.id} weekly promotion", kind="promotion_week", hours=168,
                     payload={"business_id": b.id}, first_in_hours=-0.01)
    s.commit()
    real = queue.enqueue

    def skip(s_, kind, payload, **kw):
        if kind == "promotion_week" and payload.get("business_id") == b.id:
            print(f"\nF4 enqueue({kind}) -> None   (design: no stage lists it, skipped)")
            return None
        return real(s_, kind, payload, **kw)
    monkeypatch.setattr(queue, "enqueue", skip)
    scheduler.tick(s)
    s.commit()
    sc = s.scalar(select(Schedule).where(Schedule.kind == "promotion_week", Schedule.payload["business_id"].as_integer() == b.id))
    now = datetime.now(UTC)
    print(f"F4 after the skipped tick: next_run_at - now = {(sc.next_run_at - now).total_seconds() / 3600:.1f} h, "
          f"last_run_at set={sc.last_run_at is not None}")
    print("F4 a stage the owner starts one minute later that lists promotion_week sees no tick for a week")
    assert (sc.next_run_at - now).total_seconds() > 160 * 3600


# ----------------------------------------------------------------------------------------------------------- F5
def _task(pid, key, status, owner="research"):
    return Task(project_id=pid, key=key, title=f"t {key}", owner=owner, objective="o", acceptance_criteria=["c"], status=status)


def test_f5_agent_states_read_model(s):
    """design 2.6: board `working` comes from the same function as console._agent_states, extracted to work_now."""
    from justshipit.api.console import _agent_states
    b = roadmap.create_business(s, name="보드 사업", one_liner="x")
    p = projects.create(s, "g", name="Paused stage project", start=False, business=b)
    p.status = "RUNNING"
    s.add_all([_task(p.id, "a", "RUNNING"), _task(p.id, "b", "REVIEW", owner="marketing")])
    s.flush()
    control.pause(s, p.id, "owner paused it")
    spec = Spec(spec_key=f"PRB-{p.id}", project_id=p.id, title="clar", registry_path="specs/p", status="IN_DEVELOPMENT", handoff={})
    s.add(spec)
    s.flush()
    dj = DevJob(spec_id=spec.id, status="CLARIFICATION_REQUIRED", attempt=1)
    s.add(dj)
    f = projects.create(s, "g", name="Failed stage project", start=False, business=b)
    f.status, f.error = "FAILED", "worker stopped answering"
    s.add(_task(f.id, "stale", "RUNNING", owner="finance"))
    s.commit()
    hidden = scope.hidden_projects(s, business_id=b.id)
    st = _agent_states(s, hidden)
    live_jobs = s.scalars(select(Job).where(queue.in_flight(), Job.payload["project_id"].as_integer() == p.id)).all()
    print(f"\nF5 project status={s.get(Project, p.id).status} paused-ctx={control.is_paused(p)}; "
          f"jobs in flight for it={len(live_jobs)}; dev job status={dj.status} (waits on the owner's answer)")
    for k, v in st.items():
        if v["running"]:
            print(f"F5 agent {k:12s} state={v['state']:8s} running={[(r.get('role'), r.get('title'), r.get('status')) for r in v['running']]}")
    assert st["research"]["state"] == "running"


# ----------------------------------------------------------------------------------------------------------- F6
STAGE_WORK = {"run_project", "resume_project", "retry_project", "reassess_project", "write_spec", "spec_approved",
              "spec_questions", "dev_job", "dev_resume", "dev_deliver", "dev_fix", "dev_fix_answered", "acceptance",
              "dev_merged", "bet_retro"}  # design.md 2.4 table row 1
ASKED = {"kickoff_turn", "product_report", "triage_idea", "idea_review", "realign_project", "dev_merge",
         "dev_merge_declined", "skill_activate", "derive_door_rules"}


def verdict(kind, managed=True, open_stage=False):
    """design.md 4.2 admit(), STAGE_WORK / ASKED rows only."""
    if kind in ASKED:
        return "run"
    if kind in STAGE_WORK:
        if not managed:
            return "run"
        return "run" if open_stage else "PARKED (gated)"
    return "?"


def test_f6_owner_approvals_at_the_gate_are_parked(s):
    """What the owner's own clicks enqueue (real code), judged by the design's table while no stage is open."""
    b = roadmap.create_business(s, name="관문 사업", one_liner="x")
    p = projects.create(s, "g", name="Stage S1 project", start=False, business=b)
    p.status = "DONE"
    s.flush()
    before = s.scalar(select(Job.id).order_by(Job.id.desc()).limit(1)) or 0
    # (a) the spec card the CEO's dev ask leaves behind; the stage closed while it was pending (non-blocking)
    ask = requests.create(s, dev("견적봇 MVP"), from_agent="product", project_id=p.id, origin=by_project_manager(s, p.id))
    spec = spec_for(s, p, ask)
    card = s.get(type(ask), spec.approval_request_id)
    print(f"\nF6 spec approval card: blocking={card.blocking} status={card.status} while project is {p.status}")
    requests.respond(s, spec.approval_request_id, HumanResponse(decision=HumanDecision.APPROVE))
    # (b) owner Resume on a paused project, (c) owner Retry on a failed one, (d) text note on a card of a live one
    q = projects.create(s, "g2", name="Paused", start=False, business=b)
    q.status = "RUNNING"
    s.flush()
    control.pause(s, q.id, "wait")
    control.resume(s, q.id)
    r = projects.create(s, "g3", name="Failed", start=False, business=b)
    r.status, r.error = "FAILED", "x"
    s.flush()
    control.retry_project(s, r.id)
    s.commit()
    new = list(s.scalars(select(Job).where(Job.id > before).order_by(Job.id)))
    print("F6 job created by the owner click -> design verdict with no open stage (awaiting_owner):")
    for j in new:
        if j.kind in ("notify",):
            continue
        print(f"   {j.kind:16s} payload={j.payload}  -> {verdict(j.kind)}")
    assert any(verdict(j.kind).startswith("PARKED") for j in new)


# ----------------------------------------------------------------------------------------------------------- F7
def test_f7_an_owner_approved_card_and_an_auto_approved_card_reach_the_same_todo_constructor(s):
    """design 2.4: three OwnerTask constructors collapse into `owner_tasks._insert`, which asks
    `gate.owner_work_allowed(open_stage.allows_owner_work)`. owner_tasks.py:466 is the one the approval click reaches."""
    from owner_task_kit import draft as post_draft, approve
    from justshipit.db.models import OwnerTask, ActivityEvent
    b = roadmap.create_business(s, name="할일 사업", one_liner="x")
    p = projects.create(s, "g", name="Posting project", start=False, business=b)
    s.flush()
    req = requests.create(s, post_draft(), from_agent="marketing", project_id=p.id, origin=by_project_manager(s, p.id))
    print(f"F7 post card: status={req.status} policy_mode={req.policy_mode} (needs the owner)")
    assert req.status == "PENDING"
    n0 = s.query(OwnerTask).filter_by(request_id=req.id).count()
    approve(s, req)
    s.flush()
    ot = s.query(OwnerTask).filter_by(request_id=req.id).one()
    print(f"F7 before the click to-dos for it={n0}; after the OWNER's approve click: OwnerTask {ot.id} kind={ot.kind} status={ot.status} "
          f"(created inside requests.respond -> owner_tasks.create_for_request -> OwnerTask(...) at owner_tasks.py:466)")
    print(f"F7 the request's response.responded_by={req.response.get('responded_by')!r}; create_for_request(s, req) takes "
          f"no 'who approved' argument, and the same function runs from requests.create() for policy auto-approvals "
          f"(requests.py:349-350)")
    print("F7 design: `_insert(s, task)` -> allowed only if the OPEN stage lists the kind in allows_owner_work; "
          "with no stage open the row is not created and the card stays APPROVED")
    assert ot.kind == "post"


# ----------------------------------------------------------------------------------------------------------- F8
def test_f8_adopted_stage_at_the_gate_while_the_cycle_is_still_draft(s):
    """design 7 step 5 + 4.1 index. DDL copied from design.md 4.1 into a scratch table; no model code exists yet."""
    from sqlalchemy.exc import IntegrityError
    s.execute(text("DROP TABLE IF EXISTS probe_cycle_stages"))
    s.execute(text("CREATE TABLE probe_cycle_stages (id serial primary key, business_id int, status text, key text)"))
    s.execute(text("CREATE UNIQUE INDEX ix_probe_one_holding ON probe_cycle_stages (business_id) "
                   "WHERE status IN ('running','closing','awaiting_owner')"))
    s.execute(text("INSERT INTO probe_cycle_stages (business_id, status, key) VALUES (1,'awaiting_owner','adopted')"))
    s.commit()
    print("F8 adopted stage reached awaiting_owner (design 7.5: it ends by itself while cycle.state='draft')")
    try:
        s.execute(text("INSERT INTO probe_cycle_stages (business_id, status, key) VALUES (1,'running','validate')"))
        s.commit()
        print("F8 Start of stage 1 accepted")
    except IntegrityError as exc:
        s.rollback()
        print("F8 StartCycle -> _start_stage(first planned stage): IntegrityError", str(exc.orig).splitlines()[0])
    s.execute(text("DROP TABLE probe_cycle_stages"))
    s.commit()
