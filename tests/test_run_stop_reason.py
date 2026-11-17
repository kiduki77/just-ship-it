"""What a run that stopped on an error tells the owner. The console shows the project's error as the reason the run
stopped, so an internal error shows as a sentence from the catalog, not as the exception's text; a budget refusal
keeps the name the budget-stop controls read."""
import pytest
from justshipit.db.models import Project
from justshipit.db.session import session_scope
from justshipit.orchestration import runner
from justshipit.services.budget import BudgetExceeded


class _Graph:
    def __init__(self, exc: Exception):
        self.exc = exc

    def invoke(self, payload, config):
        raise self.exc


def _stop_with(monkeypatch, project_id: int, exc: Exception) -> str:
    monkeypatch.setattr(runner, "graph", lambda: _Graph(exc))
    with pytest.raises(type(exc)):
        runner.start(project_id)
    with session_scope() as s:
        p = s.get(Project, project_id)
        assert p.status == "FAILED"
        return p.error


def test_an_internal_error_stops_the_run_with_a_calm_reason(s, project, monkeypatch):
    s.commit()

    error = _stop_with(monkeypatch, project.id, RuntimeError(
        "(psycopg.errors.DeadlockDetected) deadlock detected\nDETAIL:  Process 17627 waits for ShareLock"))

    assert error and "deadlock" not in error.lower() and "Error" not in error and "psycopg" not in error


def test_a_budget_refusal_keeps_the_name_the_budget_stop_reads(s, project, monkeypatch):
    s.commit()

    error = _stop_with(monkeypatch, project.id, BudgetExceeded("project budget 10 USD-eq would be exceeded", "project"))

    assert error.startswith("BudgetExceeded")
