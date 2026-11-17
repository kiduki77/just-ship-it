"""The owner's decisions land once: a second click of the same card changes nothing, a click on a card the cycle has
moved past is refused, and two clicks at once start one next stage."""
import threading

import cycle_kit
import pytest
from justshipit.db.models import Cycle, CycleStage, Job
from justshipit.db.session import session_scope
from justshipit.services import cycle
from sqlalchemy import func, select


pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def _at_gate(s, c: Cycle) -> CycleStage:
    """The running stage's work is over: it closes, and the summary opens its gate."""
    st = cycle_kit.running(s, c)
    for p in cycle.stage_projects(s, st.id):
        p.status = "DONE"
    s.execute(Job.__table__.update().where(Job.stage_id == st.id).values(status="done"))
    cycle.tick(s, lang="en")
    assert cycle.store_summary(s, st.id, st.round, None, lang="en")
    return st


def test_a_second_start_next_changes_nothing_and_an_old_card_is_refused(s):
    b = cycle_kit.business(s, "두 번 누르기")
    c = cycle_kit.started(s, b)
    first = _at_gate(s, c)
    cycle.decide(s, c.id, cycle.StartNext(after_stage_id=first.id, version=cycle_kit.version(s, c.id)), by="owner", lang="en")
    second = cycle_kit.running(s, c)
    cycle.decide(s, c.id, cycle.StartNext(after_stage_id=first.id, version=cycle_kit.version(s, c.id)), by="owner", lang="en")
    assert cycle_kit.running(s, c).id == second.id and first.status == "done"
    with pytest.raises(cycle.StaleDecision):
        cycle.decide(s, c.id, cycle.StartNext(after_stage_id=second.id, version=cycle_kit.version(s, c.id)), by="owner", lang="en")
    with pytest.raises(cycle.StaleDecision):
        cycle.decide(s, c.id, cycle.StartCycle(version=2), by="owner", lang="en")
    cycle.decide(s, c.id, cycle.StartCycle(version=1), by="owner", lang="en")
    s.rollback()


def test_a_revision_while_the_project_manager_is_still_writing_one_is_refused_and_the_same_one_changes_nothing(s):
    b = cycle_kit.business(s, "고쳐 쓰기")
    c = cycle_kit.proposed(s, b)
    cycle.decide(s, c.id, cycle.Revise(note="validate first", base_version=1), by="owner", lang="en")
    cycle.decide(s, c.id, cycle.Revise(note="validate first", base_version=1), by="owner", lang="en")
    assert [p.status for p in s.scalars(select(cycle.CycleProposal).where(cycle.CycleProposal.cycle_id == c.id)
                                        .order_by(cycle.CycleProposal.version))] == ["ready", "drafting"]
    with pytest.raises(cycle.StaleDecision):
        cycle.decide(s, c.id, cycle.Revise(note="something else", base_version=1), by="owner", lang="en")
    with pytest.raises(cycle.StaleDecision):
        cycle.decide(s, c.id, cycle.StartCycle(version=1), by="owner", lang="en")
    s.rollback()


def test_two_clicks_at_once_start_one_next_stage():
    with session_scope() as s:
        b = cycle_kit.business(s, "동시에 누르기")
        c = cycle_kit.started(s, b)
        first = _at_gate(s, c)
        cid, fid = c.id, first.id
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def click():
        try:
            barrier.wait()
            with session_scope() as x:
                cycle.decide(x, cid, cycle.StartNext(after_stage_id=fid, version=cycle_kit.version(x, cid)), by="owner", lang="en")
        except BaseException as exc:  # noqa: BLE001 - collected and judged below
            errors.append(exc)

    threads = [threading.Thread(target=click) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(CycleStage).where(
            CycleStage.cycle_id == cid, CycleStage.status == "running")) == 1
        assert [st.status for st in cycle.stages_of(s, cid)] == ["done", "running", "planned", "planned"]
