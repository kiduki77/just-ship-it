"""The claim is the gate's guarantee: stage work starts only while its stage runs, a job that names no valid gate never
starts, and a running job stops at its next lease check once the owner stopped its stage."""
import cycle_kit
import pytest
from justshipit.db.models import CycleStage, Job
from justshipit.services import projects, queue, roadmap
from bet_loop_kit import make_business
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError


pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def claim_mine(s, job: Job) -> Job | None:
    """Claim with every other queued job held back, so a job another test left behind is not the one taken."""
    others = set(s.scalars(select(Job.id).where(Job.status == "queued", Job.id != job.id)))
    return queue.claim(s, "test-claim-gate", [job.kind], held=dict.fromkeys(others))


@pytest.fixture
def staged(s):
    """A business in a cycle with a running stage, and a run of a project of that stage, queued and stamped with it."""
    b = roadmap.create_business(s, name="게이트 클레임", one_liner="x")
    st = cycle_kit.stage(s, b, kinds=[], owner_work=[])
    p = projects.create(s, "g", name="Stage project", start=False, business=b)
    job = queue.enqueue(s, "run_project", {"project_id": p.id}, idempotency_key=f"claim-gate:{p.id}")
    s.commit()
    assert (job.gate, job.stage_id, job.status) == ("stage", st.id, "queued")
    return st, job


@pytest.mark.parametrize("status", ["closing", "awaiting_owner", "stopped", "done", "skipped", "planned"])
def test_stage_work_does_not_start_unless_its_stage_is_running(s, staged, status):
    st, job = staged
    st.status = status
    s.commit()
    assert claim_mine(s, job) is None
    st.status = "running"
    s.commit()
    got = claim_mine(s, job)
    assert got is not None and got.id == job.id
    queue.finish(s, got.id, got.lease_token)
    s.commit()


def test_free_and_owner_asked_jobs_start_with_no_stage_at_all(s):
    b = make_business(s, name="게이트 없이")
    s.execute(text("UPDATE cycle_stages SET status = 'done' WHERE business_id = :b"), {"b": b.id})
    for kind, payload, gate_ in (("notify", {"kind": "x"}, "none"), ("product_report", {"business_id": b.id}, "direct")):
        job = queue.enqueue(s, kind, payload, idempotency_key=f"claim-free:{kind}:{b.id}")
        s.commit()
        assert job.gate == gate_
        got = claim_mine(s, job)
        assert got is not None and got.id == job.id
        queue.finish(s, got.id, got.lease_token)
        s.commit()


def test_a_job_row_that_names_no_valid_gate_cannot_exist(s):
    with pytest.raises(IntegrityError):
        s.execute(text("INSERT INTO jobs (kind, payload, status, attempts, max_attempts) "
                       "VALUES ('run_project', '{}', 'queued', 0, 1)"))
    s.rollback()
    with pytest.raises(IntegrityError):
        s.execute(text("INSERT INTO jobs (kind, payload, status, attempts, max_attempts, gate) "
                       "VALUES ('run_project', '{}', 'queued', 0, 1, 'yes')"))
    s.rollback()


def test_a_running_job_stops_at_its_next_check_once_its_stage_stops_and_waits_for_the_resume(s, staged, monkeypatch):
    st, job = staged
    got = claim_mine(s, job)
    s.commit()
    monkeypatch.setattr(queue, "STAGE_CHECK_S", 0.0)
    lease = queue.Lease(got.id, got.lease_token, gate=got.gate, stage_id=got.stage_id)
    ctx = queue.current_lease.set(lease)
    try:
        st.status = "closing"
        s.commit()
        queue.check_lease()  # a closing stage lets what already runs finish
        st.status = "stopped"
        s.commit()
        with pytest.raises(queue.Stopping):
            queue.check_lease()
    finally:
        queue.current_lease.reset(ctx)
    queue.release(s, got.id, got.lease_token)
    s.commit()
    s.refresh(got)
    assert (got.kind, got.status, got.stage_id) == ("retry_project", "gated", st.id)
    st.status = "running"
    assert [j.id for j in queue.release_gated(s, st.business_id, st.id)] == [got.id]
    s.commit()
    assert claim_mine(s, got) is not None
    s.rollback()


def test_queued_work_of_a_stopped_stage_waits_as_parked(s, staged):
    st, job = staged
    st.status = "stopped"
    s.flush()
    queue.park_stranded(s)
    s.commit()
    s.refresh(job)
    assert (job.status, job.stage_id) == ("gated", st.id)
    assert s.get(CycleStage, st.id).status == "stopped"


def test_a_job_released_in_a_closing_stage_carries_on_there_but_a_job_that_never_began_does_not(s, staged):
    st, job = staged
    got = claim_mine(s, job)
    s.commit()
    st.status = "closing"
    s.commit()
    queue.release(s, got.id, got.lease_token)
    s.commit()
    s.refresh(got)
    assert got.status == "queued", "a worker restart in a closing stage does not park the work it had begun"
    assert queue.park_stranded(s) == 0
    again = claim_mine(s, got)
    assert again is not None and again.id == got.id
    queue.finish(s, again.id, again.lease_token)
    s.commit()


def test_a_job_released_after_its_stage_stopped_still_waits_for_it(s, staged):
    st, job = staged
    got = claim_mine(s, job)
    s.commit()
    st.status = "stopped"
    s.commit()
    queue.release(s, got.id, got.lease_token)
    s.commit()
    s.refresh(got)
    assert got.status == "gated"
