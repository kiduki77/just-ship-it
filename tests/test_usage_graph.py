"""The graph does not turn a wait for usage into FAILED or 'unreviewed': the task stays where it stood and the
run resumes at the node that waited."""
from datetime import datetime, timedelta, timezone

import pytest
from justshipit.agents.executor import AgentFailed
from justshipit.contracts.planning import ProjectManagerReviewAction, ProjectManagerReviewOut, DecisionKind, DecisionOut
from justshipit.contracts.work import AgentResultOut, EvidenceKind, EvidenceOut, ReviewOut, ReviewVerdict
from justshipit.db.models import Evidence, Project, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import company_graph
from justshipit.orchestration import runner as graph_runner
from justshipit.services.usage import MustWait
from sqlalchemy import func, select


def authored() -> AgentResultOut:
    return AgentResultOut(
        summary="시장은 크다", findings_markdown="본문 [E1]", assumptions=[], risks=[], open_questions=[],
        proposed_follow_up_tasks=[], requests=[], capability_gaps=[], financial_model=None,
        evidence=[EvidenceOut(claim="가설", kind=EvidenceKind.HYPOTHESIS, source_url=None, source_title=None,
                              quote=None, published_at=None, confidence=0.4)])


def review(verdict=ReviewVerdict.PASS, instructions=None) -> ReviewOut:
    return ReviewOut(verdict=verdict, criteria_met=["c"],
                     criteria_unmet=[] if verdict == ReviewVerdict.PASS else ["c"], claim_checks=[],
                     counter_arguments=[], rework_instructions=instructions, contested=False)


def a_ready_task(s, project) -> int:
    """Two criteria, so its crew has a checker and the review step is there to wait in."""
    t = Task(project_id=project.id, key="t1", title="조사", owner="research", objective="o",
             acceptance_criteria=["c", "d"], status="READY")
    s.add(t)
    s.commit()
    return t.id


class Script:
    """A model that answers in order, per purpose; an entry that is an exception is raised."""

    def __init__(self, **steps):
        self.steps = {k: list(v) for k, v in steps.items()}
        self.prompts: list[tuple[str, str]] = []

    def __call__(self, agent, purpose, prompt, model, **kw):
        self.prompts.append((purpose, prompt))
        step = self.steps[purpose].pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def count(self, purpose: str) -> int:
        return sum(1 for p, _ in self.prompts if p == purpose)


def wait() -> MustWait:
    return MustWait(datetime.now(timezone.utc) + timedelta(hours=9), "usage_cap_day", "하루 사용 상한")


def row(model, tid, *cols):
    with session_scope() as s:
        return tuple(s.execute(select(*cols).where(model.id == tid)).one())


def evidence_rows(tid: int) -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(Evidence).where(Evidence.task_id == tid))


def test_a_wait_while_authoring_does_not_fail_the_task_and_costs_no_iteration(s, project, monkeypatch):
    tid = a_ready_task(s, project)
    monkeypatch.setattr(company_graph, "run_agent", Script(work=[wait(), authored()], review=[review()]))
    state = {"project_id": project.id, "task_id": tid}

    with pytest.raises(MustWait):
        company_graph.work(state)

    assert row(Task, tid, Task.status, Task.iteration, Task.error) == ("READY", 0, None)

    assert company_graph.work(state) == {"done": [tid]}
    assert row(Task, tid, Task.status, Task.iteration, Task.review["verdict"].astext) == ("DONE", 1, "pass")
    assert evidence_rows(tid) == 1


def test_a_wait_during_the_review_is_reviewed_later_without_authoring_again(s, project, monkeypatch):
    tid = a_ready_task(s, project)
    model = Script(work=[authored()], review=[wait(), review()])
    monkeypatch.setattr(company_graph, "run_agent", model)
    state = {"project_id": project.id, "task_id": tid}

    with pytest.raises(MustWait):
        company_graph.work(state)

    status, review_now, error = row(Task, tid, Task.status, Task.review, Task.error)
    assert status == "REVIEW" and review_now is None and error is None, "a missing review is not 'unreviewed'"
    assert evidence_rows(tid) == 1

    assert company_graph.work(state) == {"done": [tid]}
    status, verdict = row(Task, tid, Task.status, Task.review["verdict"].astext)
    assert (status, verdict) == ("DONE", "pass")
    assert model.count("work") == 1, "the author must not be paid twice"
    assert evidence_rows(tid) == 1, "and its evidence must not be recorded twice"


def test_a_review_that_fails_for_another_reason_is_still_unreviewed(s, project, monkeypatch):
    tid = a_ready_task(s, project)
    monkeypatch.setattr(company_graph, "run_agent", Script(work=[authored()], review=[AgentFailed("broken")]))
    company_graph.work({"project_id": project.id, "task_id": tid})
    assert row(Task, tid, Task.status, Task.review["verdict"].astext) == ("DONE", "unreviewed")


def test_rework_instructions_survive_a_wait_before_the_next_pass(s, project, monkeypatch):
    tid = a_ready_task(s, project)
    model = Script(work=[authored(), wait(), authored()],
                   review=[review(ReviewVerdict.REWORK, "출처를 더 찾아라"), review()])
    monkeypatch.setattr(company_graph, "run_agent", model)
    state = {"project_id": project.id, "task_id": tid}

    with pytest.raises(MustWait):
        company_graph.work(state)
    assert row(Task, tid, Task.status, Task.iteration) == ("READY", 1)

    company_graph.work(state)

    assert "출처를 더 찾아라" in [p for purpose, p in model.prompts if purpose == "work"][-1]
    assert row(Task, tid, Task.status, Task.iteration) == ("DONE", 2)


def test_a_run_that_must_wait_in_a_late_node_resumes_there_and_is_never_failed(s, project, monkeypatch):
    calls: list[str] = []
    waits = [wait()]

    def model(agent, purpose, prompt, out, **kw):
        calls.append(out.__name__)
        if out is ProjectManagerReviewOut:
            return ProjectManagerReviewOut(assessment="ok", unmet_success_criteria=[], action=ProjectManagerReviewAction.COMPLETE,
                                meeting_agenda=None, meeting_participants=[], follow_up_tasks=[], requests=[])
        if out is DecisionOut:
            return DecisionOut(title="보류", decision=list(DecisionKind)[0], rationale="r", supporting_evidence_ids=[],
                               dissenting_evidence_ids=[], dissent_summary="", open_questions=[], action_tasks=[],
                               approval_required=False, approval_reason="")
        if waits:
            raise waits.pop()
        raise RuntimeError("report step failed")
    monkeypatch.setattr(company_graph, "run_agent", model)
    project.plan = {"goal_interpretation": "계획됨"}
    s.add(Task(project_id=project.id, key="t1", title="조사", owner="research", objective="o",
               acceptance_criteria=["c"], status="DONE", finished_at=datetime.now(timezone.utc) - timedelta(hours=1)))
    s.commit()

    with pytest.raises(MustWait):
        graph_runner.start(project.id)
    assert calls == ["ProjectManagerReviewOut", "DecisionOut", "FinalReportOut"]
    assert row(Project, project.id, Project.status)[0] != "FAILED"

    calls.clear()
    with pytest.raises(RuntimeError, match="report step failed"):
        graph_runner.retry(project.id)
    assert calls == ["FinalReportOut"], "the finished nodes are not run again"


def test_a_wait_in_one_parallel_task_does_not_run_the_finished_sibling_again(s, project, monkeypatch):
    project.plan = {"goal_interpretation": "계획됨"}
    first = Task(project_id=project.id, key="first", title="첫째 조사", owner="research", objective="o",
                 acceptance_criteria=["c"], status="READY")
    second = Task(project_id=project.id, key="second", title="둘째 조사", owner="research", objective="o",
                  acceptance_criteria=["c"], status="READY")
    s.add_all([first, second])
    s.commit()
    seen: list[str] = []
    waits = [wait()]

    def model(agent, purpose, prompt, out, **kw):
        if out is ProjectManagerReviewOut:
            return ProjectManagerReviewOut(assessment="ok", unmet_success_criteria=[], action=ProjectManagerReviewAction.COMPLETE,
                                meeting_agenda=None, meeting_participants=[], follow_up_tasks=[], requests=[])
        if out is DecisionOut or purpose == "report":
            raise RuntimeError("stop after the work")
        if purpose == "work":
            seen.append("second" if "둘째 조사" in prompt else "first")
            if "둘째 조사" in prompt and waits:
                raise waits.pop()
            return authored()
        return review()
    monkeypatch.setattr(company_graph, "run_agent", model)

    with pytest.raises(MustWait):
        graph_runner.start(project.id)
    assert row(Task, first.id, Task.status)[0] != "FAILED" and row(Task, second.id, Task.status)[0] != "FAILED"

    seen.clear()
    with pytest.raises(RuntimeError, match="stop after the work"):
        graph_runner.retry(project.id)

    assert seen.count("first") == 0, "the task that finished before the wait is not authored again"
    assert seen.count("second") == 1
    assert row(Task, first.id, Task.status)[0] == "DONE" and row(Task, second.id, Task.status)[0] == "DONE"
