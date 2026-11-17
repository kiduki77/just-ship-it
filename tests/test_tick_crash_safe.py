"""The cycle's driver is a function of the database: running it twice does nothing twice, a crash between noticing a
stage settled and asking for its summary is finished by the next tick, and a CEO job that died is asked for again
under a key of its own, so the owner is never left at a spinner with no button."""
import pytest
import cycle_kit
from justshipit.db.models import CycleProposal, Job
from justshipit.db.session import session_scope
from justshipit.services import cycle, queue
from sqlalchemy import func, select


pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def _settle(s, st) -> None:
    for p in cycle.stage_projects(s, st.id):
        p.status = "DONE"
    s.execute(Job.__table__.update().where(Job.stage_id == st.id, Job.kind.notin_(cycle._PROJECT_MANAGER_KINDS)).values(status="done"))


def _summaries(s, st) -> list[Job]:
    return list(s.scalars(select(Job).where(Job.kind == "cycle_summarize",
                                            Job.payload["stage_id"].as_integer() == st.id).order_by(Job.id)))


def test_a_second_tick_does_nothing_twice(s):
    b = cycle_kit.business(s, "틱 두 번")
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    _settle(s, st)
    cycle.tick(s, lang="en")
    cycle.tick(s, lang="en")
    assert st.status == "closing" and len(_summaries(s, st)) == 1
    s.rollback()


def test_a_crash_after_the_settle_check_is_finished_by_the_next_tick():
    with session_scope() as s:
        b = cycle_kit.business(s, "틱 충돌")
        c = cycle_kit.started(s, b)
        st = cycle_kit.running(s, c)
        _settle(s, st)
        sid = st.id
    with session_scope() as s:
        cycle.tick(s, lang="en")
        s.rollback()   # the worker died before the commit
    with session_scope() as s:
        assert s.get(cycle.CycleStage, sid).status == "running"
        cycle.tick(s, lang="en")
    with session_scope() as s:
        st = s.get(cycle.CycleStage, sid)
        assert st.status == "closing" and [j.status for j in _summaries(s, st)] == ["queued"]


def test_a_summary_job_that_died_is_asked_for_again_under_a_new_key(s):
    """Probe F1 of the review: re-enqueueing under the first round's key after it failed queued nothing."""
    b = cycle_kit.business(s, "다시 묻기")
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    _settle(s, st)
    cycle.tick(s, lang="en")
    [first] = _summaries(s, st)
    first.status = "failed"   # its worker vanished without the failure reaching the queue's repair
    s.flush()
    cycle.tick(s, lang="en")
    jobs = _summaries(s, st)
    assert [j.status for j in jobs] == ["failed", "queued"] and jobs[0].idempotency_key != jobs[1].idempotency_key
    for j in jobs:
        j.status = "failed"
    for _ in range(3):
        s.flush()
        cycle.tick(s, lang="en")
        for j in _summaries(s, st):
            j.status = "failed"
    assert st.status == "awaiting_owner" and st.summary_by == "server", "after its rounds the server writes the summary"
    s.rollback()


def test_a_summary_job_that_gives_up_opens_the_gate_with_the_servers_summary(s):
    b = cycle_kit.business(s, "요약 실패")
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    _settle(s, st)
    cycle.tick(s, lang="en")
    [job] = _summaries(s, st)
    s.commit()
    got = queue.claim(s, "tick-test", ["cycle_summarize"], held=dict.fromkeys(
        s.scalars(select(Job.id).where(Job.status == "queued", Job.id != job.id))))
    assert got.id == job.id
    queue.finish(s, got.id, got.lease_token, error="AgentFailed: the model timed out", final=True)
    s.refresh(st)
    assert (st.status, st.summary_by) == ("awaiting_owner", "server")
    assert st.summary["note"] and st.summary["tasks"]["total"] >= 0
    s.rollback()


def test_a_proposal_job_that_gives_up_leaves_a_failed_version_and_the_owner_can_ask_again(s):
    b = cycle_kit.business(s, "제안 실패")
    from justshipit.services import projects
    c = projects.start_business(s, b.id, lang="en")
    job = s.scalar(select(Job).where(Job.kind == "cycle_propose", Job.payload["cycle_id"].as_integer() == c.id))
    s.commit()
    got = queue.claim(s, "tick-test", ["cycle_propose"], held=dict.fromkeys(
        s.scalars(select(Job.id).where(Job.status == "queued", Job.id != job.id))))
    queue.finish(s, got.id, got.lease_token, error="AgentFailed: output failed validation", final=True)
    assert cycle.proposal(s, c.id).status == "failed"
    cycle.decide(s, c.id, cycle.RetryProposal(version=1), by="owner", lang="en")
    cycle.decide(s, c.id, cycle.RetryProposal(version=1), by="owner", lang="en")
    assert [p.status for p in s.scalars(select(CycleProposal).where(CycleProposal.cycle_id == c.id)
                                        .order_by(CycleProposal.version))] == ["failed", "drafting"]
    assert s.scalar(select(func.count()).select_from(Job).where(
        Job.kind == "cycle_propose", Job.payload["cycle_id"].as_integer() == c.id, queue.in_flight())) == 1
    s.rollback()


def test_a_retro_job_that_gives_up_still_closes_the_cycle_and_opens_the_next(s):
    b = cycle_kit.business(s, "회고 실패")
    c = cycle_kit.started(s, b)
    for st in cycle.stages_of(s, c.id):
        if st.status == "planned":
            st.status = "skipped"
    st = cycle_kit.running(s, c)
    _settle(s, st)
    cycle.tick(s, lang="en")
    cycle.store_summary(s, st.id, st.round, None, lang="en")
    cycle.decide(s, c.id, cycle.FinishCycle(after_stage_id=st.id), by="owner", lang="en")
    job = s.scalar(select(Job).where(Job.kind == "cycle_retro", Job.payload["cycle_id"].as_integer() == c.id))
    s.commit()
    got = queue.claim(s, "tick-test", ["cycle_retro"], held=dict.fromkeys(
        s.scalars(select(Job.id).where(Job.status == "queued", Job.id != job.id))))
    queue.finish(s, got.id, got.lease_token, error="AgentFailed: x", final=True)
    s.refresh(c)
    assert (c.state, c.retro["by"]) == ("closed", "server") and c.closed_at is not None
    nxt = cycle.open_cycle_of(s, b.id)
    assert (nxt.number, nxt.state, cycle.proposal(s, nxt.id).status) == (2, "draft", "drafting")
    s.rollback()
