"""The owner stops a stage while its work runs: the running job stops at its next lease check and waits, queued work is
parked, resuming the stage carries on from where it was, and stopping the cycle there closes what waited, saying why."""
import cycle_kit
import pytest
from justshipit.db.models import Job
from justshipit.services import cycle, queue
from sqlalchemy import select


pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def _mine(s, job):
    others = s.scalars(select(Job.id).where(Job.status == "queued", Job.id != job.id))
    return queue.claim(s, "stop-test", [job.kind], held=dict.fromkeys(others))


def test_a_stopped_stage_stops_its_running_job_and_resume_carries_it_on(s, monkeypatch):
    monkeypatch.setattr(queue, "STAGE_CHECK_S", 0.0)
    b = cycle_kit.business(s, "멈춤과 재개")
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    run = s.scalar(select(Job).where(Job.kind == "run_project", Job.stage_id == st.id))
    s.commit()
    got = _mine(s, run)
    s.commit()
    cycle.decide(s, c.id, cycle.StopStage(stage_id=st.id), by="owner", lang="en")
    s.commit()
    lease = queue.Lease(got.id, got.lease_token, gate=got.gate, stage_id=got.stage_id)
    ctx = queue.current_lease.set(lease)
    try:
        with pytest.raises(queue.Stopping):
            queue.check_lease()
    finally:
        queue.current_lease.reset(ctx)
    queue.release(s, got.id, got.lease_token)
    s.commit()
    s.refresh(got)
    assert (got.kind, got.status, got.stage_id) == ("retry_project", "gated", st.id)
    assert _mine(s, got) is None
    cycle.decide(s, c.id, cycle.ResumeStage(stage_id=st.id), by="owner", lang="en")
    cycle.decide(s, c.id, cycle.ResumeStage(stage_id=st.id), by="owner", lang="en")
    s.commit()
    s.refresh(got)
    assert got.status == "queued" and _mine(s, got) is not None
    s.rollback()


def test_stopping_the_cycle_closes_the_work_that_waited_and_says_why(s):
    b = cycle_kit.business(s, "여기서 멈추기")
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    cycle.decide(s, c.id, cycle.StopStage(stage_id=st.id), by="owner", lang="en")
    run = s.scalar(select(Job).where(Job.kind == "run_project", Job.stage_id == st.id))
    assert run.status == "gated"
    cycle.decide(s, c.id, cycle.StopHere(after_stage_id=st.id), by="owner", lang="en")
    assert (run.status, c.state) == ("failed", "stopped") and "ended cycle 1" in run.last_error
    assert c.closed_at is not None and cycle.open_cycle_of(s, b.id) is None
    s.rollback()
