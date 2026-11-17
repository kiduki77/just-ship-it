"""A failed run the budget would only stop again is marked, so the console offers the budget before "Run again"
whatever error the run stored."""
import pytest
from justshipit.api.app import app
from justshipit.db.models import Project
from justshipit.services import budget
from fastapi.testclient import TestClient


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def failed(s, project, *, spent: float, error: str = "something broke") -> Project:
    project.status, project.error = "FAILED", error
    project.budget_usd, project.spent_usd, project.reserved_usd = 5.0, spent, 0.0
    return project


def test_a_failed_run_whose_budget_has_no_room_for_one_more_call_is_marked_so(s, project):
    assert budget.retry_blocked(s, failed(s, project, spent=4.9)) is True


def test_a_failed_run_with_budget_to_spare_is_not_marked(s, project):
    assert budget.retry_blocked(s, failed(s, project, spent=0.5)) is False


def test_a_running_project_is_never_marked(s, project):
    project.budget_usd, project.spent_usd, project.status = 5.0, 4.9, "RUNNING"
    assert budget.retry_blocked(s, project) is False


def test_the_project_list_carries_the_mark_whatever_error_the_run_stored(s, project, api):
    failed(s, project, spent=4.9, error="Stopped: this project's budget of $5.00 would be passed (spent $4.90).")
    s.commit()
    try:
        rows = {p["id"]: p for p in api.get("/api/projects").json()}
        assert rows[project.id]["budget_short"] is True
    finally:
        project.status, project.error, project.spent_usd = "CREATED", None, 0.0
        s.commit()
