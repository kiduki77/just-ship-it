"""Console summaries: progress never counts cancelled work, and test projects stay out of company totals."""
import pytest
from fastapi import HTTPException

from justshipit.api import app as api
from justshipit.db.models import AgentRequest, Task
from justshipit.services import projects


def _task(pid: int, key: str, status: str) -> Task:
    return Task(project_id=pid, key=key, title=key, owner="research", objective="o", acceptance_criteria=[], status=status)


def test_progress_excludes_cancelled_tasks(s, project):
    s.add_all([_task(project.id, "a", "DONE"), _task(project.id, "b", "CANCELLED"), _task(project.id, "c", "RUNNING")])
    s.flush()
    row = next(p for p in api._project_summaries(s) if p["id"] == project.id)
    assert (row["tasks_done"], row["tasks_total"], row["tasks_active"]) == (1, 2, 1)


def test_company_task_counts_skip_test_projects(s, project):
    test = projects.create(s, "검증", name="[TEST] 하네스", budget_usd=1, start=False)
    s.commit()
    before = api.dashboard()["tasks_by_status"].get("DONE", 0)
    s.add_all([_task(project.id, "a", "DONE"), _task(test.id, "b", "DONE")])
    s.commit()
    assert api.dashboard()["tasks_by_status"].get("DONE", 0) == before + 1


def test_request_deep_link_returns_exactly_that_request(s, project):
    r = AgentRequest(project_id=project.id, from_agent="research", type="APPROVAL", title="딥링크", summary="s",
                     rationale="r", status="APPROVED", policy_mode="approve")
    s.add(r)
    s.commit()
    assert api.get_request(r.id)["title"] == "딥링크"
    with pytest.raises(HTTPException):
        api.get_request(10**9)
