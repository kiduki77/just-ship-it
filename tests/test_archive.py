from datetime import datetime, timezone

import pytest

from justshipit.db.models import AgentRequest, Job, Schedule
from justshipit.services import projects


def test_archive_cancels_requests_and_schedules_but_keeps_data(s, project, korean_owner):
    s.add(AgentRequest(project_id=project.id, from_agent="project_manager", type="APPROVAL", title="t", summary="s",
                       rationale="r", status="PENDING", hold_scope="run", policy_mode="approve"))
    s.add(Schedule(name=f"P-{project.id} 변화 감시", kind="evidence_watch", interval_hours=168,
                   next_run_at=datetime.now(timezone.utc),
                   payload={"project_id": project.id}, enabled=True))
    s.flush()
    done = projects.archive(s, project.id)
    s.flush()
    assert project.status == "ARCHIVED" and project.context["archived"]["previous_status"] == "CREATED"
    req = s.get(AgentRequest, done["cancelled_requests"][0])
    assert req.status == "CANCELLED" and req.response["note"] == "프로젝트 보관"
    assert s.get(Schedule, done["disabled_schedules"][0]).enabled is False


def test_archive_refuses_while_a_job_is_queued(s, project):
    s.add(Job(gate="direct", kind="run_project", payload={"project_id": project.id}, status="queued"))
    s.flush()
    with pytest.raises(ValueError):
        projects.archive(s, project.id)


def test_archive_command_refuses_in_korean_without_a_traceback(s, project, korean_owner):
    from typer.testing import CliRunner

    from justshipit import cli

    s.add(Job(gate="direct", kind="run_project", payload={"project_id": project.id}, status="queued"))
    s.commit()
    out = CliRunner().invoke(cli.app, ["project", "archive", str(project.id)])
    assert out.exit_code == 1
    assert f"P-{project.id}" in out.output and "보관할 수 없어요" in out.output
    assert "Traceback" not in out.output and "ValueError" not in out.output
