import pytest
from justshipit.db.models import AgentRequest, Project, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import company_graph, runner
from sqlalchemy import func, select


def test_retry_rebuilds_from_the_task_db_when_the_checkpoint_is_gone(s, project, monkeypatch):
    monkeypatch.setattr(company_graph, "run_agent", lambda *a, **kw: pytest.fail("planned again"))
    project.plan = {"goal_interpretation": "계획됨"}
    s.add(Task(project_id=project.id, key="t1", title="조사", owner="research", objective="o",
               acceptance_criteria=["c"], status="RUNNING"))
    s.add(AgentRequest(project_id=project.id, from_agent="project_manager", type="APPROVAL", title="막힘", summary="s",
                       rationale="r", status="PENDING", policy_mode="approve", hold_scope="run"))
    s.commit()
    runner.start(project.id)
    assert runner.snapshot(project.id)["interrupts"]

    runner.graph().checkpointer.delete_thread(f"project-{project.id}")
    with session_scope() as s2:
        s2.get(Project, project.id).status = "FAILED"
    runner.retry(project.id)

    with session_scope() as s2:
        assert s2.get(Project, project.id).status == "WAITING_HUMAN"
        assert s2.scalar(select(func.count()).select_from(Task).where(Task.project_id == project.id)) == 1
        assert s2.scalar(select(Task.status).where(Task.project_id == project.id)) == "READY"
    assert runner.snapshot(project.id)["interrupts"]
