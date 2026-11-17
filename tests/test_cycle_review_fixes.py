"""Four defects the review of the cycle found, each reproduced before it was fixed: a stopped stage still shown as
working, an archived business still asked for a summary, a stage the owner removed coming back in the next proposal,
and a Start on a stage the owner had not read."""
import cycle_kit
import pytest
from justshipit.db.models import Business, Cycle, CycleStage, DevJob, Job, Spec, Task
from justshipit.services import cycle, cycle_board, projects, queue, work_now
from sqlalchemy import select

pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def _working(s, b):
    return [(r.agent, r.kind, r.title) for r in work_now.rows(s, set()) if r.business_id == b.id]


def _stage_with_work(s, name: str):
    b = cycle_kit.business(s, name)
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    [p] = cycle.stage_projects(s, st.id)
    p.status = "RUNNING"
    s.add(Task(project_id=p.id, key="a", title="Interview", owner="research", objective="o",
               acceptance_criteria=["c"], status="RUNNING"))
    spec = Spec(spec_key=f"W-{p.id}", project_id=p.id, title="Parked build", registry_path="specs/w",
                status="READY_FOR_DEV", handoff={})
    s.add(spec)
    s.flush()
    dj = DevJob(spec_id=spec.id, status="QUEUED", attempt=1, steps=[])
    s.add(dj)
    s.flush()
    return b, c, st, dj


# ------------------------------------------------------------------ 2. what is shown as working

def test_a_running_stage_shows_its_task_and_its_build_as_working(s):
    b, _, _, dj = _stage_with_work(s, "working")
    queue.enqueue(s, "dev_job", {"dev_job_id": dj.id}, idempotency_key=f"w_dev:{dj.id}", max_attempts=1)
    s.flush()
    assert [(a, k) for a, k, _ in _working(s, b)] == [("research", "task"), ("engineer", "dev")]


def test_a_stopped_stage_and_a_parked_build_show_nothing_as_working(s):
    """Probe B: the stopped stage's RUNNING task and a build whose job sat `gated` were two cards of work in progress."""
    b, c, st, dj = _stage_with_work(s, "stopped")
    cycle.decide(s, c.id, cycle.StopStage(stage_id=st.id), by="owner", lang="en")
    queue.enqueue(s, "dev_job", {"dev_job_id": dj.id}, idempotency_key=f"s_dev:{dj.id}", max_attempts=1)
    s.flush()
    parked = s.scalar(select(Job).where(Job.kind == "dev_job", Job.payload["dev_job_id"].as_integer() == dj.id))
    assert parked.status == "gated"
    assert _working(s, b) == []
    assert cycle_board.board(s, b.id, lang="en").working == []


def test_a_build_whose_job_waits_gated_is_not_working_even_in_a_running_stage(s):
    b, _, _, dj = _stage_with_work(s, "parked")
    job = queue.enqueue(s, "dev_job", {"dev_job_id": dj.id}, idempotency_key=f"p_dev:{dj.id}", max_attempts=1)
    job.status = "gated"
    s.flush()
    assert [(a, k) for a, k, _ in _working(s, b)] == [("research", "task")]


# ------------------------------------------------------------------ 3. archiving a business

def _settled_stage(s, name: str):
    b = cycle_kit.business(s, name)
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    for p in cycle.stage_projects(s, st.id):
        p.status = "DONE"
    s.execute(Job.__table__.update().where(Job.business_id == b.id).values(status="done"))
    s.execute(Job.__table__.update().where(Job.kind.in_(cycle._PROJECT_MANAGER_KINDS)).values(status="done"))
    s.flush()
    return b, c, st


def test_archiving_a_business_stops_its_cycle_and_stage_and_the_project_manager_is_not_asked(s):
    """Probe C: an archived business still got a summary call at its next tick."""
    b, c, st = _settled_stage(s, "archived")
    projects.archive_business(s, b.id, lang="en")
    s.flush()
    s.refresh(st)
    s.refresh(c)
    assert (st.status, st.end_cause) == ("done", "archived")
    assert (c.state, c.closed_at is not None) == ("stopped", True)
    assert c.stop_reason
    assert {(x.status, x.end_cause) for x in cycle.stages_of(s, c.id)} == {("done", "archived"), ("skipped", "archived")}
    assert not [x for x in cycle.tick(s, lang="en") if f"stage {st.id} " in x]
    assert s.scalar(select(Job).where(Job.kind == "cycle_summarize", Job.payload["stage_id"].as_integer() == st.id)) is None
    assert cycle_board.board(s, b.id, lang="en").state == "stopped"


def test_tick_skips_an_archived_business_whose_cycle_was_left_open(s):
    """A business archived before the archive stopped cycles still has an open one."""
    b, c, st = _settled_stage(s, "left open")
    s.get(Business, b.id).status = "ARCHIVED"
    s.flush()
    assert not [x for x in cycle.tick(s, lang="en") if f"stage {st.id} " in x]
    assert s.scalar(select(Job).where(Job.kind == "cycle_summarize", Job.payload["stage_id"].as_integer() == st.id)) is None
    assert s.get(CycleStage, st.id).status == "running"


def test_an_archived_business_is_not_asked_again_for_a_proposal(s):
    b = cycle_kit.business(s, "no proposal")
    c = projects.start_business(s, b.id, lang="en")
    s.execute(Job.__table__.update().where(Job.business_id == b.id).values(status="done"))
    s.get(Business, b.id).status = "ARCHIVED"
    s.flush()
    assert not [x for x in cycle.tick(s, lang="en") if f"cycle {c.id} " in x]
    assert s.scalar(select(Job).where(Job.kind == "cycle_propose", Job.payload["cycle_id"].as_integer() == c.id,
                                      Job.status == "queued")) is None


def test_archiving_a_business_with_a_proposal_waiting_closes_the_cycle(s):
    b = cycle_kit.business(s, "waiting")
    c = cycle_kit.proposed(s, b)
    s.execute(Job.__table__.update().where(Job.business_id == b.id).values(status="done"))
    s.flush()
    projects.archive_business(s, b.id, lang="en")
    s.flush()
    s.refresh(c)
    assert (c.state, c.closed_at is not None) == ("stopped", True)


# ------------------------------------------------------------------ 4. a stage the owner removed stays removed

def test_a_stage_the_owner_removed_does_not_come_back_in_a_later_proposal(s):
    """Probe E: the removed stage's row was frozen like a stage that ran, so the next version listed it again."""
    b = cycle_kit.business(s, "ghost")
    c = cycle_kit.started(s, b)
    keys = [x["key"] for x in cycle.proposal(s, c.id, status=("ready",)).stages]
    cycle.decide(s, c.id, cycle.Revise(note="drop promote", base_version=1), by="owner", lang="en")
    cycle.store_proposal(s, c.id, 2, cycle_kit.revision_out([cycle_kit.draft(k) for k in [*keys, "extra"] if k != "promote"]),
                         {}, lang="en")
    assert [x.status for x in cycle.stages_of(s, c.id) if x.key == "promote"] == ["skipped"]
    cycle.decide(s, c.id, cycle.Revise(note="one more tweak", base_version=2), by="owner", lang="en")
    later = [x["key"] for x in cycle.proposal(s, c.id, status=("ready",)).stages if x["key"] != "promote"]
    cycle.store_proposal(s, c.id, 3, cycle_kit.revision_out([cycle_kit.draft(k) for k in later]), {}, lang="en")
    assert "promote" not in [x["key"] for x in cycle.proposal(s, c.id, status=("ready",)).stages]


def test_a_stage_that_ran_stays_in_every_later_proposal(s):
    b = cycle_kit.business(s, "ran")
    c = cycle_kit.started(s, b)
    first = cycle_kit.running(s, c).key
    cycle.decide(s, c.id, cycle.Revise(note="tweak", base_version=1), by="owner", lang="en")
    others = [x["key"] for x in cycle.proposal(s, c.id, status=("ready",)).stages if x["key"] != first]
    cycle.store_proposal(s, c.id, 2, cycle_kit.revision_out([cycle_kit.draft(k) for k in [*others, "extra"]]), {},
                         lang="en")
    assert first in [x["key"] for x in cycle.proposal(s, c.id, status=("ready",)).stages]


def test_a_removed_stage_the_owner_asks_for_again_can_be_started(s):
    b = cycle_kit.business(s, "revive")
    c = cycle_kit.started(s, b)
    keys = [x["key"] for x in cycle.proposal(s, c.id, status=("ready",)).stages]
    cycle.decide(s, c.id, cycle.Revise(note="drop promote", base_version=1), by="owner", lang="en")
    cycle.store_proposal(s, c.id, 2, cycle_kit.revision_out([cycle_kit.draft(k) for k in [*keys, "extra"] if k != "promote"]),
                         {}, lang="en")
    cycle.decide(s, c.id, cycle.Revise(note="promote after all", base_version=2), by="owner", lang="en")
    cycle.store_proposal(s, c.id, 3, cycle_kit.revision_out([cycle_kit.draft(k) for k in keys]), {}, lang="en")
    back = next(x for x in cycle.stages_of(s, c.id) if x.key == "promote")
    assert (back.status, back.end_cause, back.finished_at) == ("planned", None, None)


# ------------------------------------------------------------------ 5. Start on the stage the owner read

def _at_the_gate(s, name: str):
    b = cycle_kit.business(s, name)
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    for p in cycle.stage_projects(s, st.id):
        p.status = "DONE"
    s.execute(Job.__table__.update().where(Job.stage_id == st.id, Job.kind.notin_(cycle._PROJECT_MANAGER_KINDS)).values(status="done"))
    cycle.tick(s, lang="en")
    cycle.store_summary(s, st.id, st.round, None, lang="en")
    return b, c, st


def _gate(s, b):
    return cycle_board.board(s, b.id, lang="en").waiting[0]


def test_start_next_carries_the_version_the_owner_read(s):
    b, c, st = _at_the_gate(s, "version")
    gate = _gate(s, b)
    assert gate.proposal_version == cycle.proposal(s, c.id, status=("ready",)).version
    cycle.decide(s, c.id, cycle.StartNext(after_stage_id=st.id, version=gate.proposal_version), by="owner", lang="en")
    assert cycle_kit.running(s, c).key == gate.next_stage.key


def test_a_revision_that_landed_after_the_board_loaded_makes_start_next_stale(s):
    """Probe D: the stale click started a stage whose recurring jobs and to-dos the owner had not read."""
    b, c, st = _at_the_gate(s, "stale")
    read = _gate(s, b)
    cycle.decide(s, c.id, cycle.Revise(note="more", base_version=read.proposal_version), by="owner", lang="en")
    keys = [x["key"] for x in cycle.proposal(s, c.id, status=("ready",)).stages]
    stages = [cycle_kit.draft(k, kinds=("find_people", "promotion_week") if k == read.next_stage.key else (),
                              owner_work=("post",) if k == read.next_stage.key else ()) for k in keys]
    cycle.store_proposal(s, c.id, 2, cycle_kit.revision_out(stages), {}, lang="en")
    with pytest.raises(cycle.StaleDecision):
        cycle.decide(s, c.id, cycle.StartNext(after_stage_id=st.id, version=read.proposal_version), by="owner",
                     lang="en")
    assert cycle_kit.running(s, c) is None
    assert s.get(CycleStage, st.id).status == "awaiting_owner"
    fresh = _gate(s, b)
    assert fresh.proposal_version == 2 and fresh.next_stage.recurring == ["find_people", "promotion_week"]
    cycle.decide(s, c.id, cycle.StartNext(after_stage_id=st.id, version=fresh.proposal_version), by="owner", lang="en")
    assert cycle_kit.running(s, c).allows_kinds == ["find_people", "promotion_week"]


def test_a_second_start_next_with_the_old_version_still_changes_nothing(s):
    b, c, st = _at_the_gate(s, "twice")
    version = _gate(s, b).proposal_version
    cycle.decide(s, c.id, cycle.StartNext(after_stage_id=st.id, version=version), by="owner", lang="en")
    started = cycle_kit.running(s, c).id
    cycle.decide(s, c.id, cycle.StartNext(after_stage_id=st.id, version=version), by="owner", lang="en")
    assert cycle_kit.running(s, c).id == started
