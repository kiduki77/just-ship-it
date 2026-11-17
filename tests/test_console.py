"""Owner controls and console read models: pause/resume, budget, directives, task actions, activity log."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from justshipit.api import console
from justshipit.db.models import ActivityEvent, AgentRequest, Job, LLMCallRecord, Project, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import context as ctx
from justshipit.services import activity, control, projects


def _task(pid: int, key: str, status: str, **kw) -> Task:
    return Task(project_id=pid, key=key, title=f"업무 {key}", owner="research", objective="o",
                acceptance_criteria=["c"], status=status, **kw)


def _jobs(s, kind: str, pid: int) -> list[Job]:
    return list(s.scalars(select(Job).where(Job.kind == kind, Job.payload["project_id"].as_integer() == pid)))


def _events(s, entity: str, entity_id: int) -> list[ActivityEvent]:
    return list(s.scalars(select(ActivityEvent).where(ActivityEvent.entity == entity,
                                                      ActivityEvent.entity_id == entity_id).order_by(ActivityEvent.id)))


# ---------------------------------------------------------------- activity log

def test_status_changes_are_logged_with_actor(s, project):
    t = _task(project.id, "a", "READY")
    s.add(t)
    s.commit()
    t.status = "RUNNING"
    s.commit()
    with activity.as_actor("owner"):
        t.status = "FAILED"
        s.commit()
    rows = _events(s, "task", t.id)
    assert [(e.verb, e.data.get("to"), e.actor) for e in rows] == [
        ("created", None, "project_manager"), ("status", "RUNNING", "research"), ("status", "FAILED", "owner")]
    assert rows[1].data["from"] == "READY" and rows[1].project_id == project.id


def test_request_resolution_is_logged_as_responder(s, project):
    r = AgentRequest(project_id=project.id, from_agent="finance", type="APPROVAL", title="결제", summary="s",
                     rationale="r", status="PENDING", policy_mode="approve")
    s.add(r)
    s.commit()
    r.status, r.response = "APPROVED", {"responded_by": "claude (오너 위임)"}
    s.commit()
    rows = _events(s, "request", r.id)
    assert [e.actor for e in rows] == ["finance", "claude (오너 위임)"]


# ---------------------------------------------------------------- pause / resume

def test_pause_holds_a_waiting_run_and_resume_releases_it(s, project):
    project.status = "WAITING_HUMAN"
    s.commit()
    with session_scope() as x:
        control.pause(x, project.id, "방향 점검")
    s.refresh(project)
    assert project.status == "PAUSED" and control.is_paused(project)
    with session_scope() as x:
        control.resume(x, project.id)
    s.refresh(project)
    assert not control.is_paused(project)
    assert _jobs(s, "resume_project", project.id), "unpausing with nothing open must resume the run"


def test_resume_with_open_blocking_request_waits_for_the_owner(s, project):
    project.status = "WAITING_HUMAN"
    s.add(AgentRequest(project_id=project.id, from_agent="project_manager", type="APPROVAL", title="막힘", summary="s",
                       rationale="r", status="PENDING", policy_mode="approve", hold_scope="run"))
    s.commit()
    with session_scope() as x:
        control.pause(x, project.id)
    with session_scope() as x:
        control.resume(x, project.id)
    s.refresh(project)
    assert project.status == "WAITING_HUMAN"
    assert not _jobs(s, "resume_project", project.id)


def test_runner_resume_skips_a_paused_project(s, project):
    from justshipit.orchestration import runner
    project.status = "PAUSED"
    project.context = {"paused": {"by": "owner"}}
    s.commit()
    assert "paused" in runner.resume(project.id)["skipped"]


def test_schedule_routes_a_paused_run_to_the_gate(s, project):
    from justshipit.orchestration import company_graph
    project.status = "RUNNING"
    project.context = {"paused": {"by": "owner"}}
    s.add(_task(project.id, "a", "READY"))
    s.commit()
    assert company_graph.schedule({"project_id": project.id}) == {"batch": [], "next": "human_gate"}


def test_pause_refuses_finished_projects(s, project):
    project.status = "DONE"
    s.commit()
    with pytest.raises(control.ControlError), session_scope() as x:
        control.pause(x, project.id)


# ---------------------------------------------------------------- budget

def test_budget_must_exceed_spend(s, project):
    project.spent_usd = 9.5
    s.commit()
    with pytest.raises(control.ControlError), session_scope() as x:
        control.set_budget(x, project.id, 9.0)


def test_raise_budget_and_resume_restarts_a_budget_stop(s, project):
    project.status, project.error, project.spent_usd = "FAILED", "BudgetExceeded: project budget 10 USD-eq", 9.9
    t = _task(project.id, "a", "FAILED", error="BudgetExceeded: project budget 10 USD-eq would be exceeded")
    other = _task(project.id, "b", "FAILED", error="AgentFailed: schema")
    s.add_all([t, other])
    s.commit()
    with session_scope() as x:
        control.set_budget(x, project.id, 30, "P-5처럼 예산 증액", resume_run=True)
    s.refresh(project), s.refresh(t), s.refresh(other)
    assert project.budget_usd == 30 and project.context["budget_changes"][-1]["to"] == 30
    assert _jobs(s, "retry_project", project.id)
    assert (t.status, other.status) == ("READY", "FAILED"), "only budget-stopped tasks restart with the run"


def test_month_limit_cannot_drop_below_spend(s):
    with session_scope() as x:
        m = control.set_month_limit(x, 500)
        used = m.spent_usd + m.reserved_usd
    with pytest.raises(control.ControlError), session_scope() as x:
        control.set_month_limit(x, used)


# ---------------------------------------------------------------- directives

def test_directive_reaches_every_agent_and_realigns(s, project):
    project.status = "RUNNING"
    s.commit()
    with session_scope() as x:
        d = control.add_directive(x, project.id, "한국 정부 지원사업은 제외")
    assert d["realigning"] and d["n"] == 1
    s.refresh(project)
    assert "[Owner instruction #1] 한국 정부 지원사업은 제외" in ctx.project_brief(s, project)
    job = _jobs(s, "realign_project", project.id)[0]
    assert job.payload["directive"] == 1


# ---------------------------------------------------------------- tasks

def test_retry_failed_task_gets_a_fresh_attempt(s, project):
    project.status, project.phase = "RUNNING", "executing"
    t = _task(project.id, "a", "FAILED", iteration=3, max_iterations=2, error="boom", spent_usd=4.0, budget_usd=3.0)
    s.add(t)
    s.commit()
    with session_scope() as x:
        control.retry_task(x, t.id)
    s.refresh(t)
    assert t.status == "READY" and t.error is None
    assert t.max_iterations > t.iteration
    assert t.budget_usd * 1.5 >= t.spent_usd + 1.5, "the retried attempt must be able to reserve budget"


def test_no_new_work_after_the_final_decision(s, project):
    project.status, project.phase = "WAITING_HUMAN", "deciding"
    t = _task(project.id, "a", "FAILED")
    s.add(t)
    s.commit()
    with pytest.raises(control.ControlError), session_scope() as x:
        control.retry_task(x, t.id)
    with pytest.raises(control.ControlError), session_scope() as x:
        control.create_task(x, project.id, title="추가", objective="o", owner="research", acceptance_criteria=[],
                            depends_on=[])


def test_owner_task_creation_and_edits(s, project):
    project.status, project.phase = "RUNNING", "executing"
    dep = _task(project.id, "dep", "DONE")
    s.add(dep)
    s.commit()
    with session_scope() as x:
        t = control.create_task(x, project.id, title="경쟁사 가격", objective="가격표 수집", owner="marketing",
                                acceptance_criteria=["표 1개"], depends_on=[dep.id])
        tid = t.id
    t = s.get(Task, tid)
    s.refresh(t)
    assert (t.origin, t.owner, t.depends_on, t.status) == ("owner", "marketing", [dep.id], "PROPOSED")
    with session_scope() as x:
        control.update_task(x, tid, owner="finance", acceptance_criteria=["표 1개", " ", "출처 링크"])
    s.refresh(t)
    assert t.owner == "finance" and t.acceptance_criteria == ["표 1개", "출처 링크"]
    with pytest.raises(control.ControlError), session_scope() as x:
        control.update_task(x, tid, owner="project_manager")
    with session_scope() as x:
        control.cancel_task(x, tid, "중복")
    s.refresh(t)
    assert t.status == "CANCELLED"


def test_an_edit_records_the_task_title_as_text_and_the_title_it_replaced(s, project):
    t = _task(project.id, "a", "READY")
    s.add(t)
    s.commit()
    with session_scope() as x:
        control.update_task(x, t.id, title="새 제목")
    with session_scope() as x:
        control.update_task(x, t.id, objective="다른 목표")
    with session_scope() as x:
        renamed, other = [e.data for e in _events(x, "task", t.id) if e.verb == "edited"]
    assert (renamed["title"], renamed["title_was"], renamed["fields"]) == ("새 제목", "업무 a", ["title"])
    assert (other["title"], other["fields"]) == ("새 제목", ["objective"]) and "title_was" not in other


def test_started_tasks_cannot_be_edited_or_cancelled(s, project):
    t = _task(project.id, "a", "RUNNING")
    s.add(t)
    s.commit()
    for fn in (lambda x: control.update_task(x, t.id, title="새 제목"), lambda x: control.cancel_task(x, t.id)):
        with pytest.raises(control.ControlError), session_scope() as x:
            fn(x)


def test_owner_comment_reaches_the_task_prompt(s, project):
    t = _task(project.id, "a", "READY")
    s.add(t)
    s.commit()
    with session_scope() as x:
        control.comment_task(x, t.id, "2025년 이후 자료만 쓰세요")
    assert "2025년 이후 자료만 쓰세요" in ctx.owner_comments(s, t)


# ---------------------------------------------------------------- read models

def test_overview_and_activity_endpoints(s, project):
    s.add(_task(project.id, "a", "RUNNING"))
    s.commit()
    data = console.overview(tz="Asia/Seoul")
    assert len(data["series"]["runs"]) == 14 and len(data["series"]["tasks"]) == 14
    research = next(a for a in data["agents"] if a["id"] == "research")
    assert research["state"] == "running"
    feed = console.activity_feed(project_id=project.id)
    assert feed["events"] and all(e["project_id"] == project.id for e in feed["events"])


def test_overview_hides_test_projects(s):
    test = projects.create(s, "검증", name="[TEST] 콘솔", budget_usd=1, start=False)
    s.add(_task(test.id, "a", "RUNNING"))
    s.commit()
    hidden = console.overview()
    shown = console.overview(include_test=True)
    assert all(p["id"] != test.id for p in hidden["projects"])
    assert any(p["id"] == test.id for p in shown["projects"])


def test_search_jumps_by_id(s, project):
    s.commit()
    assert console.search(f"P-{project.id}")[0]["id"] == project.id
    assert console.search("REQ-999999999") == []


def test_run_files_only_from_the_runtime_folder(s, project, tmp_path):
    (tmp_path / "prompt.md").write_text("secret elsewhere", encoding="utf-8")
    r = LLMCallRecord(call_id="c-guard", project_id=project.id, agent="research", purpose="work", provider="claude",
                      status="ok", raw_path=str(tmp_path))
    s.add(r)
    s.commit()
    assert console.run_detail(r.id)["files"] == {}
    with pytest.raises(HTTPException):
        console.run_detail(10**9)


def test_project_action_refusals_are_409(s, project):
    project.status = "DONE"
    s.commit()
    with pytest.raises(HTTPException) as exc:
        console.retry_project(project.id)
    assert exc.value.status_code == 409
    row = console.project_directive(project.id, console.TextIn(text="다음엔 B2B부터"))
    assert row["n"] == 1 and row["realigning"] is False
    assert s.scalar(select(ActivityEvent).where(ActivityEvent.verb == "directive",
                                                ActivityEvent.project_id == project.id)).actor == "owner"
    assert s.get(Project, project.id) is not None


def test_csv_export_neutralizes_formulas(s, project):
    s.commit()
    with session_scope() as x:
        activity.record(x, verb="comment", entity="task", entity_id=None, project_id=project.id,
                        summary='=HYPERLINK("http://evil","x")', actor="research")
    import asyncio

    async def read(resp):
        return "".join([c if isinstance(c, str) else c.decode() async for c in resp.body_iterator])
    body = asyncio.run(read(console.activity_csv(project_id=project.id)))
    assert "'=HYPERLINK" in body
