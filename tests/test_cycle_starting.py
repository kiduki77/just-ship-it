"""A stage that was just started says what its work is doing instead of "all agents are idle": its first job waits for a
worker, or a worker is planning the tasks."""
import cycle_kit
import pytest
from justshipit.db.models import Job
from justshipit.services import cycle_board, queue
from sqlalchemy import select

pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def test_a_started_stage_reads_queued_until_a_worker_takes_its_job_and_planning_after(s):
    b = cycle_kit.business(s, "막 시작한 단계")
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    run = s.scalar(select(Job).where(Job.kind == "run_project", Job.stage_id == st.id))
    s.commit()

    now = cycle_board.board(s, b.id, lang="en").current
    assert (now.starting, now.tasks_total) == ("queued", 0) and now.starting_since

    others = set(s.scalars(select(Job.id).where(Job.status == "queued", Job.id != run.id)))
    assert queue.claim(s, "starting-test", ["run_project"], held=dict.fromkeys(others)).id == run.id
    s.commit()
    assert cycle_board.board(s, b.id, lang="en").current.starting == "planning"

    queue.finish(s, run.id, run.lease_token)
    s.commit()
    assert cycle_board.board(s, b.id, lang="en").current.starting is None
