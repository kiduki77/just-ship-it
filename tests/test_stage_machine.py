"""A stage's life: the moves the table allows and no others, one stage holding a business at a time, and the adopted
stage that carried pre-cycle work never blocking the owner's first Start."""
import cycle_kit
import pytest
from justshipit.db.models import CycleProposal, CycleStage, Job, Project
from justshipit.services import cycle, projects
from justshipit.services.control import ControlError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def _row(s, b, status: str) -> CycleStage:
    st = cycle_kit.stage(s, b, status=status, kinds=[], owner_work=[])
    return st


@pytest.mark.parametrize("was,to", [(a, b) for a, nxt in cycle.STAGE_TRANSITIONS.items() for b in nxt])
def test_every_move_in_the_table_is_allowed(s, was, to):
    b = cycle_kit.business(s, f"이동 {was} {to}")
    st = _row(s, b, was)
    assert cycle._move(s, st, to, lang="en", cause="accepted" if to == "done" else None) is True
    assert st.status == to
    s.rollback()


@pytest.mark.parametrize("was,to", [(a, b) for a in cycle.STAGE_TRANSITIONS for b in cycle.STAGE_TRANSITIONS
                                    if b != a and b not in cycle.STAGE_TRANSITIONS[a]])
def test_every_other_move_is_refused(s, was, to):
    b = cycle_kit.business(s, f"막힘 {was} {to}")
    st = _row(s, b, was)
    with pytest.raises(ControlError):
        cycle._move(s, st, to, lang="en")
    s.rollback()


def test_a_move_to_the_status_it_has_changes_nothing(s):
    b = cycle_kit.business(s, "제자리")
    st = _row(s, b, "running")
    assert cycle._move(s, st, "running", lang="en") is False
    s.rollback()


@pytest.mark.parametrize("second", ["running", "closing", "awaiting_owner"])
def test_one_stage_holds_a_business_at_a_time(s, second):
    b = cycle_kit.business(s, f"하나만 {second}")
    _row(s, b, "awaiting_owner")
    with pytest.raises(IntegrityError):
        _row(s, b, second)
    s.rollback()


def test_a_stopped_stage_can_end_the_cycle_without_running_again(s):
    """Amendment 5: stop here from a stopped stage spends nothing."""
    b = cycle_kit.business(s, "멈춘 단계")
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    cycle.decide(s, c.id, cycle.StopStage(stage_id=st.id), by="owner", lang="en")
    cycle.decide(s, c.id, cycle.StopHere(after_stage_id=st.id), by="owner", lang="en")
    assert (st.status, st.end_cause, c.state) == ("done", "stopped_here", "stopped")
    assert [x.status for x in cycle.stages_of(s, c.id)][1:] == ["skipped"] * 3
    s.rollback()


def test_start_moves_a_settled_adopted_stage_out_of_the_way_first(s):
    """Probe F8 of the review: the adopted stage reached the gate before the owner pressed Start, and the index refused
    the first stage."""
    b = cycle_kit.business(s, "이어받은 사업")
    c = cycle_kit.cycle_of(s, b, state="draft")
    c.mode = "adopt"
    adopted = cycle_kit.stage(s, b, status="awaiting_owner", kinds=[], owner_work=[], key="adopted")
    adopted.adopted = True
    s.add(CycleProposal(cycle_id=c.id, version=1, status="drafting", stages=[], changes=[], basis={}))
    s.flush()
    cycle.store_proposal(s, c.id, 1, cycle_kit.proposal_out(past=["define"], current="plan"),
                         {"live": {"project": [], "bet": [], "schedule": []}}, lang="en")
    cycle.decide(s, c.id, cycle.StartCycle(version=1), by="owner", lang="en")
    rows = {st.key: st for st in cycle.stages_of(s, c.id)}
    assert (adopted.status, adopted.end_cause) == ("done", "adopted_past")
    assert rows["define"].status == "done" and rows["plan"].status == "running" and c.state == "running"
    s.rollback()


def test_start_takes_over_running_adopted_work_in_place(s):
    b = cycle_kit.business(s, "진행 중 이어받기")
    c = cycle_kit.cycle_of(s, b, state="draft")
    c.mode = "adopt"
    adopted = cycle_kit.stage(s, b, status="running", kinds=["find_people"], owner_work=[], key="adopted")
    adopted.adopted = True
    p = projects.create(s, "g", name="Old run", start=False, business=b)
    p.stage_id, p.status = adopted.id, "RUNNING"
    s.add(CycleProposal(cycle_id=c.id, version=1, status="drafting", stages=[], changes=[], basis={}))
    s.flush()
    cycle.store_proposal(s, c.id, 1, cycle_kit.proposal_out(
        [cycle_kit.draft("define"), cycle_kit.draft("validate", owner_work=["contact"]), cycle_kit.draft("build"),
         cycle_kit.draft("promote")], past=["define"], current="validate"),
        {"live": {"project": [p.id], "bet": [], "schedule": []}}, lang="en")
    stored = cycle.proposal(s, c.id)
    assert {"kind": "project", "id": p.id} in next(d for d in stored.stages if d["key"] == "validate")["maps_existing"]
    cycle.decide(s, c.id, cycle.StartCycle(version=1), by="owner", lang="en")
    assert (adopted.key, adopted.status, adopted.allows_owner_work) == ("validate", "running", ["contact"])
    assert s.scalar(select(Project.stage_id).where(Project.id == p.id)) == adopted.id
    assert s.scalar(select(Job).where(Job.kind == "run_project", Job.business_id == b.id)) is None, "nothing new starts"
    s.rollback()


def test_the_next_stage_takes_in_parked_work_and_still_starts_its_own(s):
    b = cycle_kit.business(s, "대기 작업 합류")
    c = cycle_kit.started(s, b)
    first = cycle_kit.running(s, c)
    for p in cycle.stage_projects(s, first.id):
        p.status = "DONE"
    s.execute(Job.__table__.update().where(Job.stage_id == first.id).values(status="done"))
    cycle.tick(s, lang="en")
    cycle.store_summary(s, first.id, first.round, None, lang="en")
    late = projects.create(s, "g", name="Started at the gate", business=b)
    assert s.scalar(select(Job.status).where(Job.payload["project_id"].as_integer() == late.id)) == "gated"
    cycle.decide(s, c.id, cycle.StartNext(after_stage_id=first.id, version=cycle_kit.version(s, c.id)), by="owner", lang="en")
    second = cycle_kit.running(s, c)
    names = {p.name: p for p in cycle.stage_projects(s, second.id)}
    assert set(names) == {"Started at the gate", second.title}
    assert "The company does plan." in names[second.title].goal
    assert s.scalar(select(Job.status).where(Job.payload["project_id"].as_integer() == late.id)) == "queued"
    s.rollback()
