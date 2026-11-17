"""What enqueue does with a job the gate has not cleared: work the company starts with no stage running waits as parked,
a recurring kind no running stage lists is skipped, the owner's own clicks run, and a parked row counts as in flight so
nothing queues a second one beside it."""
from datetime import UTC, datetime, timedelta

import bet_loop_kit as kit
import cycle_kit
import pytest
from justshipit.db.models import Bet, CycleStage, Job, Project, Schedule
from justshipit.operations import scheduler
from justshipit.services import bet_loop, control, gate, projects, queue, roadmap
from sqlalchemy import select, update
from test_bet_retro import ended_bet


pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def _quiet(s, b) -> None:
    """The business's kit stage is over: no stage runs."""
    s.execute(update(CycleStage).where(CycleStage.business_id == b.id).values(status="done"))
    s.flush()


@pytest.fixture
def business(s):
    b = kit.make_business(s, name="입장 사업")
    _quiet(s, b)
    return b


def test_stage_work_with_no_running_stage_waits_parked_and_joins_the_next_stage(s, business):
    p = projects.create(s, "g", name="Parked run", start=False, business=business)
    job = queue.enqueue(s, "run_project", {"project_id": p.id}, idempotency_key=f"adm-park:{p.id}")
    assert (job.status, job.gate, job.stage_id, job.business_id) == ("gated", "stage", None, business.id)
    st = cycle_kit.stage(s, business, kinds=[], owner_work=[])
    released = queue.release_gated(s, business.id, st.id)
    assert [j.id for j in released] == [job.id] and (job.status, job.stage_id) == ("queued", st.id)


def test_a_closing_stage_admits_no_new_stage_work(s, business):
    cycle_kit.stage(s, business, status="closing", kinds=["find_people"], owner_work=[])
    p = projects.create(s, "g", name="Late run", start=False, business=business)
    assert queue.enqueue(s, "run_project", {"project_id": p.id}, idempotency_key=f"adm-closing:{p.id}").status == "gated"
    assert queue.enqueue(s, "find_people", {"business_id": business.id}, idempotency_key=f"adm-closing:{business.id}") is None


def test_stage_work_in_a_running_stage_is_stamped_with_it_and_its_project_joins(s, business):
    st = cycle_kit.stage(s, business, kinds=[], owner_work=[])
    p = projects.create(s, "g", name="Joined run", start=False, business=business)
    job = queue.enqueue(s, "run_project", {"project_id": p.id}, idempotency_key=f"adm-run:{p.id}")
    assert (job.status, job.gate, job.stage_id) == ("queued", "stage", st.id)
    assert s.get(Project, p.id).stage_id == st.id


def test_a_recurring_kind_runs_only_in_a_stage_that_lists_it(s, business):
    assert queue.enqueue(s, "find_people", {"business_id": business.id}) is None
    assert s.scalar(select(Job).where(Job.kind == "find_people", Job.business_id == business.id)) is None
    st = cycle_kit.stage(s, business, kinds=["find_threads"], owner_work=[])
    assert queue.enqueue(s, "find_people", {"business_id": business.id}) is None
    job = queue.enqueue(s, "find_threads", {"business_id": business.id})
    assert (job.gate, job.stage_id, job.status) == ("stage", st.id, "queued")


def test_the_owners_own_click_runs_a_recurring_kind_with_no_stage(s, business):
    job = queue.enqueue(s, "find_people", {"business_id": business.id}, by_owner=True)
    assert (job.status, job.gate, job.stage_id) == ("queued", "direct", None)


def test_owner_asked_kinds_run_with_no_stage_and_count_toward_the_running_one(s, business):
    job = queue.enqueue(s, "product_report", {"business_id": business.id}, idempotency_key=f"adm-asked:{business.id}")
    assert (job.status, job.gate, job.stage_id) == ("queued", "direct", None)
    st = cycle_kit.stage(s, business, kinds=[], owner_work=[])
    job = queue.enqueue(s, "product_report", {"business_id": business.id}, idempotency_key=f"adm-asked2:{business.id}")
    assert (job.gate, job.stage_id) == ("direct", st.id)


def test_a_business_outside_cycles_runs_its_stage_work_as_asked_but_no_recurring_kind(s):
    b = roadmap.create_business(s, name="CLI 사업", one_liner="x")
    p = projects.create(s, "g", name="CLI run", start=False, business=b)
    job = queue.enqueue(s, "run_project", {"project_id": p.id}, idempotency_key=f"adm-cli:{p.id}")
    assert (job.status, job.gate) == ("queued", "direct")
    assert queue.enqueue(s, "find_people", {"business_id": b.id}) is None


def test_a_company_wide_kind_runs_while_any_running_stage_lists_it(s, business):
    s.execute(update(CycleStage).where(CycleStage.status == "running").values(status="done"))
    assert queue.enqueue(s, "portfolio_review", {}, idempotency_key="adm-company:none") is None
    st = cycle_kit.stage(s, business, kinds=["portfolio_review"], owner_work=[])
    job = queue.enqueue(s, "portfolio_review", {}, idempotency_key="adm-company:one")
    assert (job.gate, job.stage_id) == ("stage", st.id)
    s.rollback()


def test_a_parked_retry_refuses_the_second_click(s, business):
    """Probe F2 of the review: a parked row was invisible to the busy checks, so four clicks queued four retries."""
    p = projects.create(s, "g", name="FailedRun", start=False, business=business)
    p.status, p.error = "FAILED", "boom"
    s.flush()
    control.retry_project(s, p.id)
    with pytest.raises(control.ControlError):
        control.retry_project(s, p.id)
    s.rollback()


def test_a_parked_retro_is_not_queued_again_by_every_sweep(s):
    """Probe C of the review: each sweep queued another retro beside the parked one."""
    kit.reset_world()
    b = kit.make_business(s, name="회고 사업")
    _quiet(s, b)
    s.commit()
    bet = ended_bet(s, b)
    for _ in range(4):
        bet_loop.sweep(s, lang="ko")
    rows = list(s.scalars(select(Job).where(Job.kind == "bet_retro", Job.payload["bet_id"].as_integer() == bet.id)))
    assert [j.status for j in rows] == ["gated"]
    assert not roadmap.retro_stuck(s, s.get(Bet, bet.id))
    s.rollback()
    kit.reset_world()


def test_a_skipped_schedule_moves_on_a_whole_interval(s, business):
    scheduler.ensure(s, name=f"B-{business.id} weekly promotion", kind="promotion_week", hours=168,
                     payload={"business_id": business.id}, first_in_hours=-0.01)
    scheduler.tick(s)
    sc = s.scalar(select(Schedule).where(Schedule.kind == "promotion_week",
                                         Schedule.payload["business_id"].as_integer() == business.id))
    assert sc.next_run_at - datetime.now(UTC) > timedelta(hours=160)
    assert s.scalar(select(Job).where(Job.kind == "promotion_week", Job.business_id == business.id)) is None
    s.rollback()


def test_owner_work_waits_for_a_stage_that_lists_it(s, business):
    assert not gate.owner_work_allowed(s, business.id, "contact", by_owner=False)
    assert gate.owner_work_allowed(s, business.id, "contact", by_owner=True)
    cycle_kit.stage(s, business, kinds=[], owner_work=["post"])
    assert not gate.owner_work_allowed(s, business.id, "contact", by_owner=False)
    assert gate.owner_work_allowed(s, business.id, "post", by_owner=False)


def test_a_bet_whose_run_is_parked_keeps_no_window_clock_until_its_stage_starts(s):
    """Probe F3 of the review: a bet started at a shut gate ran its window from the click, so the sweep ended it and
    piled up retros before its run ever started."""
    kit.reset_world()
    b = kit.make_business(s, name="멈춘 실험 창")
    _quiet(s, b)
    bet = kit.make_bet(s, b)
    roadmap.move_bet(s, bet.id, "RUNNING")
    run = kit.run_of(s, bet.id)
    assert bet_loop.run_parked(s, run)
    bet.started_at = datetime.now(UTC) - timedelta(days=30)
    assert bet_loop.sweep(s, lang="ko") == []
    assert s.get(Bet, bet.id).status == "RUNNING"
    assert s.scalar(select(Job).where(Job.kind == "bet_retro", Job.payload["bet_id"].as_integer() == bet.id)) is None
    from justshipit.services import cycle
    c = cycle_kit.cycle_of(s, b)
    st = CycleStage(cycle_id=c.id, business_id=b.id, key="promote", position=9, title="Promote", doing="d",
                    owner_does="o", decision_points=[], estimate_basis="guess", ends_when="run_settled",
                    allows_kinds=[], allows_owner_work=[], adopted=False, status="planned", round=1)
    s.add(st)
    s.flush()
    cycle._start_stage(s, st, lang="ko", by="owner")
    assert datetime.now(UTC) - s.get(Bet, bet.id).started_at < timedelta(minutes=1), "its window starts with its run"
    assert s.get(Project, run.id).stage_id == st.id and not bet_loop.run_parked(s, run)
    s.rollback()
    kit.reset_world()
