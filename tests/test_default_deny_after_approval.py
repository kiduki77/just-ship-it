"""The observed failure, as a test: after the owner approved the plan, the scheduled find_people and find_threads ran on
their own and put "contact these people" to-dos in the owner's list. Now nothing scheduled runs and no to-do appears
until a stage the owner started lists it, and a stage that lists a weekly kind runs it in that stage."""
import pytest
from datetime import UTC, datetime, timedelta

import cycle_kit
from justshipit.db.models import Job, OwnerTask, Schedule
from justshipit.operations import scheduler
from justshipit.services import cycle, owner_tasks, projects, queue
from sqlalchemy import select


pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def _jobs(s, b, kind):
    return list(s.scalars(select(Job).where(Job.kind == kind, Job.business_id == b.id)))


def _schedule(s, b, kind) -> Schedule:
    return s.scalar(select(Schedule).where(Schedule.kind == kind, Schedule.payload["business_id"].as_integer() == b.id))


def test_after_approval_nothing_scheduled_runs_until_a_started_stage_lists_it(s):
    b = cycle_kit.business(s, "승인 뒤 조용히")
    c = projects.start_business(s, b.id, lang="en")
    scheduler.tick(s)
    assert _jobs(s, b, "find_people") == [] and _jobs(s, b, "find_threads") == []
    assert s.scalars(select(OwnerTask).where(OwnerTask.business_id == b.id)).all() == []
    assert not owner_tasks.allowed(s, b.id, "contact")
    assert _schedule(s, b, "find_people").next_run_at > datetime.now(UTC) + timedelta(days=6)

    stages = [cycle_kit.draft("validate", kinds=["find_people"], owner_work=["contact"]), *cycle_kit.keys(
        "plan", "build", "promote")]
    p = cycle.proposal(s, c.id)
    cycle.store_proposal(s, c.id, p.version, cycle_kit.proposal_out(stages), {}, lang="en")
    cycle.decide(s, c.id, cycle.StartCycle(version=p.version), by="owner", lang="en")
    st = cycle_kit.running(s, c)
    assert _schedule(s, b, "find_people").next_run_at <= datetime.now(UTC), "the stage runs its weekly kind now"
    scheduler.tick(s)
    assert [(j.gate, j.stage_id) for j in _jobs(s, b, "find_people")] == [("stage", st.id)]
    assert _jobs(s, b, "find_threads") == [], "a kind the stage did not list still does not run"
    assert owner_tasks.allowed(s, b.id, "contact") and not owner_tasks.allowed(s, b.id, "post")

    cycle.decide(s, c.id, cycle.StopStage(stage_id=st.id), by="owner", lang="en")
    assert queue.enqueue(s, "find_people", {"business_id": b.id}) is None
    assert not owner_tasks.allowed(s, b.id, "contact")
    s.rollback()
