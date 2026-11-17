"""When a project run waits for the owner (docs/design/work-path.md 1.5 and 7): the gate and every waker read one
decision, a held result is judged by the cards that held it, and a run whose card was answered while the gate parked it
is resumed."""
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from ask_kit import by_project_manager, by_task, contract
from justshipit.contracts.planning import ProjectManagerReviewAction, ProjectManagerReviewOut
from justshipit.contracts.requests import AgentAskDraft, HumanDecision, HumanResponse, RequestType, RiskLevel
from justshipit.contracts.work import TaskDraft, TaskStatus
from justshipit.contracts.work_path import HoldKind
from justshipit.db.models import ActivityEvent, AgentRequest, Job, Project, RequestLink, Task
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.orchestration import company_graph, runner
from justshipit.orchestration import context as ctx
from justshipit.services import control, holds, requests, tasks, work_path
from sqlalchemy import select, text

pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def card(s, project, scope: str, status: str = "PENDING") -> AgentRequest:
    r = AgentRequest(project_id=project.id, from_agent="project_manager", type="QUESTION", title=f"{scope} card", summary="s",
                     rationale="r", policy_mode="approve", status=status, hold_scope=scope)
    s.add(r)
    s.flush()
    return r


def task(s, project, key: str, status: str = "PROPOSED", depends_on: tuple[int, ...] = ()) -> Task:
    t = Task(project_id=project.id, key=key, title=key, owner="research", objective="o", acceptance_criteria=["c"],
             status=status, depends_on=list(depends_on))
    s.add(t)
    s.flush()
    return t


def hold(s, req: AgentRequest, t: Task, phase: str = "start") -> AgentRequest:
    s.add(RequestLink(request_id=req.id, rel="holds", target_kind="task", target_id=t.id, phase=phase, revision=1,
                      source="agent", known="recorded"))
    s.flush()
    return req


def ask(type_=RequestType.QUESTION, **kw) -> AgentAskDraft:
    base = dict(type=type_, title=f"{type_.value.title()} for the owner", summary="s", rationale="r", options=[],
                recommended_option=None, action_kind="internal.other" if type_ == RequestType.APPROVAL else None,
                estimated_cost_usd=None, risk_level=RiskLevel.LOW, context=contract(owner_work=False))
    return AgentAskDraft(**{**base, **kw})


class Parked(Exception):
    """What LangGraph's interrupt does to the node: it ends there."""


@pytest.fixture
def gate(monkeypatch):
    def interrupt(value):
        raise Parked(value)
    monkeypatch.setattr(company_graph, "interrupt", interrupt)

    def run(project_id: int) -> bool:
        """Whether the gate parked the run."""
        try:
            company_graph.human_gate({"project_id": project_id})
        except Parked:
            return True
        return False
    return run


def resume_jobs(project_id: int) -> list[Job]:
    with session_scope() as x:
        return list(x.scalars(select(Job).where(Job.kind == "resume_project",
                                                Job.payload["project_id"].as_integer() == project_id)))


def verdicts(task_id: int) -> list[str]:
    """The statuses a task went through, as the activity log recorded them."""
    with session_scope() as x:
        return [e.data["to"] for e in x.scalars(select(ActivityEvent).where(
            ActivityEvent.entity == "task", ActivityEvent.entity_id == task_id, ActivityEvent.verb == "status")
            .order_by(ActivityEvent.id))]


def finish(s, project, t: Task, *drafts: AgentAskDraft, monkeypatch) -> Task:
    """The task's review passed with these requests, which are raised as the review settles (`_finish`)."""
    monkeypatch.setattr(company_graph, "_write_task_artifact", lambda *a, **k: None)
    t.iteration = 1
    t.result = {"summary": "s", "findings_markdown": "f", "evidence_ids": [], "request_ids": [],
                "requests": [d.model_dump(mode="json") for d in drafts]}
    s.flush()
    company_graph._finish(s, s.get(Project, project.id), t, company_graph.PASSED, "en")
    return t


def held_result(s, project, *drafts: AgentAskDraft, monkeypatch) -> Task:
    return finish(s, project, s.get(Task, by_task(s, project.id).task_id), *drafts, monkeypatch=monkeypatch)


def waiting_on_a_lock(timeout: float = 10.0) -> None:
    """Until another connection of this database waits on a row lock."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session_scope() as x:
            if x.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                             "AND wait_event_type = 'Lock'")):
                return
        time.sleep(0.05)
    raise AssertionError("nothing waited on the lock")


# ---------------------------------------------------------------- one decision

def _paused(s, project) -> None:
    project.context = {"paused": {"by": "owner"}}


def _beside_held(s, project) -> AgentRequest:
    task(s, project, "beside", "READY")
    return hold(s, card(s, project, "tasks"), task(s, project, "held"))


def _beside_planning(s, project) -> AgentRequest:
    task(s, project, "planned", "READY")
    return card(s, project, "planning")


# What each case leaves open in a running project, and whether the run must wait. A card that holds tasks parks it only
# when nothing else can start, and one that holds the next plan step only once no task is left to go on.
CASES = {
    "none": (lambda s, p: card(s, p, "none"), False),
    "tasks, nothing else to start": (lambda s, p: hold(s, card(s, p, "tasks"), task(s, p, "held")), True),
    "tasks, a sibling can start": (_beside_held, False),
    "tasks it no longer holds": (lambda s, p: hold(s, card(s, p, "tasks"), task(s, p, "gone", "CANCELLED")), False),
    "planning, nothing left to run": (lambda s, p: card(s, p, "planning"), True),
    "planning, a planned task can start": (_beside_planning, False),
    "run": (lambda s, p: card(s, p, "run"), True),
    "paused": (_paused, True),
}


@pytest.mark.parametrize("case", CASES)
def test_the_gate_the_wakers_and_the_card_say_the_same_thing(s, project, gate, monkeypatch, case):
    make, waits = CASES[case]
    project.status = "RUNNING"
    left = make(s, project)
    answered = card(s, project, "run", status="ANSWERED")  # the card whose answer would wake the run
    s.commit()

    assert gate(project.id) == waits
    with session_scope() as x:
        assert x.get(Project, project.id).status == (
            "RUNNING" if not waits else "PAUSED" if case == "paused" else "WAITING_HUMAN")
        if left is not None:
            assert work_path.card_for(x, x.get(AgentRequest, left.id), now=datetime.now(UTC)).parks_project == waits

    invoked = []
    monkeypatch.setattr(runner, "snapshot", lambda pid: {"next": ["human_gate"], "interrupts": [{"project_id": pid}]})
    monkeypatch.setattr(runner, "_invoke", lambda pid, payload: invoked.append(pid) or {})
    with session_scope() as x:
        x.get(Project, project.id).status = "WAITING_HUMAN"
        requests._maybe_resume(x, x.get(AgentRequest, answered.id))
    runner.resume(project.id)
    assert (bool(resume_jobs(project.id)), bool(invoked)) == (not waits, not waits)


@pytest.mark.parametrize("case", CASES)
def test_the_sweep_resumes_exactly_the_waiting_runs_nothing_holds(s, project, case):
    make, waits = CASES[case]
    project.status = "WAITING_HUMAN"
    make(s, project)
    s.commit()

    assert (project.id in holds.reconcile_parked(s)) == (not waits)


# ---------------------------------------------------------------- a held result is judged by what held it

def test_a_later_card_from_the_same_task_does_not_decide_its_held_result(s, project, gate, monkeypatch):
    """human_gate read "the latest request with this task_id": a question the task filed after its approval, still
    open and holding nothing, rejected the result the owner had approved."""
    t = held_result(s, project, ask(RequestType.APPROVAL, title="Buy the domain", context=contract(HoldKind.THIS_TASK)),
                    ask(title="Which audience first"), monkeypatch=monkeypatch)
    approval, later = (s.get(AgentRequest, i) for i in t.result["request_ids"])
    assert t.status == "APPROVAL_REQUIRED" and later.id > approval.id and later.task_id == t.id
    requests.respond(s, approval.id, HumanResponse(decision=HumanDecision.APPROVE))
    s.commit()

    assert not gate(project.id)
    assert verdicts(t.id)[-2:] == ["APPROVED", "DONE"]


def test_a_held_result_the_owner_declined_is_rejected_and_never_read_as_done(s, project, gate, monkeypatch):
    """It used to go on to DONE, so the tasks after it and the CEO's review read a declined result as done."""
    t = held_result(s, project, ask(RequestType.APPROVAL, title="Buy the domain", context=contract(HoldKind.THIS_TASK)),
                    monkeypatch=monkeypatch)
    after = task(s, project, "after_the_domain", depends_on=(t.id,))
    requests.respond(s, t.result["request_ids"][0], HumanResponse(decision=HumanDecision.REJECT))
    s.commit()

    assert not gate(project.id)
    assert verdicts(t.id)[-1] == "REJECTED"
    with session_scope() as x:
        p, dep = x.get(Project, project.id), x.get(Task, after.id)
        assert f"T-{t.id} [REJECTED]" in ctx.results_overview(x, p)
        assert "status REJECTED" in ctx.dependency_outputs(x, dep)
        assert dep.id in {r.id for r in tasks.promote_ready(x, project.id)}, "a rejected result ends; what follows runs"


def test_a_result_held_before_the_records_is_judged_by_the_approval_it_raised(s, project, gate):
    """The backfill wrote a finish hold only for an open card. A task whose approval was answered before it has none:
    its own approval decides, and the later card the backfill also linked to the task does not."""
    t = task(s, project, "old_approval", "APPROVAL_REQUIRED")
    approval, later = card(s, project, "run", status="APPROVED"), card(s, project, "none")
    approval.type = "APPROVAL"
    for r in (approval, later):
        r.task_id = t.id
        s.add(RequestLink(request_id=r.id, rel="raised_by", target_kind="task", target_id=t.id, revision=1,
                          source="backfill", known="backfilled"))
    s.commit()

    assert not gate(project.id)
    assert verdicts(t.id)[-2:] == ["APPROVED", "DONE"]


def test_a_task_waits_for_the_owner_exactly_when_a_card_holds_its_result(s, project, monkeypatch):
    """APPROVAL_REQUIRED is the finish hold: a question that holds the task's result keeps it there, and an approval
    that holds only the tasks after it lets it finish while they wait."""
    asked = held_result(s, project, ask(title="Which audience first", context=contract(HoldKind.THIS_TASK,
                                                                                         owner_work=False)),
                        monkeypatch=monkeypatch)
    assert asked.status == "APPROVAL_REQUIRED"

    first = s.get(Task, by_task(s, project.id).task_id)
    after = task(s, project, "after_first", depends_on=(first.id,))
    finish(s, project, first, ask(RequestType.APPROVAL, title="Post the flyer", context=contract(HoldKind.MY_DEPENDENTS)),
           monkeypatch=monkeypatch)
    assert first.status == "DONE"
    assert [(h.task_id, h.phase) for h in holds.live_holds(s, project.id) if h.task_id == after.id] == [
        (after.id, "start")]


# ---------------------------------------------------------------- holds on tasks

def test_a_task_whose_start_a_card_holds_is_not_started_until_it_is_answered(s, project):
    done = task(s, project, "groundwork", "DONE")
    after, ready = task(s, project, "after_groundwork", depends_on=(done.id,)), task(s, project, "ready_now", "READY")
    asked = hold(s, hold(s, card(s, project, "tasks"), after), ready)

    assert tasks.promote_ready(s, project.id) == [] and after.status == "PROPOSED"

    asked.status = "ANSWERED"
    assert {t.id for t in tasks.promote_ready(s, project.id)} >= {after.id, ready.id}


def test_a_held_task_that_is_cancelled_frees_the_run(s, project):
    held = task(s, project, "held_then_cancelled")
    hold(s, card(s, project, "tasks"), held)
    assert holds.park_reason(s, project.id) == holds.ParkReason.STARVED

    control.cancel_task(s, held.id, lang="en")
    assert holds.park_reason(s, project.id) is None


def test_a_task_that_redoes_a_held_one_is_held_in_its_place(s, project):
    failed = task(s, project, "first_try", "FAILED")
    asked = hold(s, card(s, project, "tasks"), failed)
    assert holds.park_reason(s, project.id) is None, "a failed task waits on nothing"

    (again,), _ = tasks.create_from_drafts(s, project, [TaskDraft(
        key="second_try", title="second try", owner="research", objective="o", acceptance_criteria=["c"],
        depends_on=[], redoes=f"T-{failed.id}")], origin="follow_up", plan_round=1)

    assert failed.status == "REPLACED"
    assert [(h.request_id, h.task_id, h.phase) for h in holds.live_holds(s, project.id)] == [
        (asked.id, again.id, "start")]
    assert holds.park_reason(s, project.id) == holds.ParkReason.STARVED
    assert again not in tasks.promote_ready(s, project.id)


# ---------------------------------------------------------------- the stranded wait

def test_an_answer_that_lands_while_the_gate_parks_the_run_is_resumed_by_the_sweep(s, project, gate, monkeypatch):
    """The gate read the open card, the owner answered it, and only then did the gate commit WAITING_HUMAN: the
    answer's waker saw a running project and queued nothing, so the run waited on a card nobody could answer again."""
    project.status = "RUNNING"
    card = requests.create(s, ask(context=contract(HoldKind.RUN, owner_work=False)), from_agent="project_manager",
                           project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    real = company_graph.session_scope
    answered = []

    @contextmanager
    def gate_session(**kw):
        with real(**kw) as x:
            yield x
            if not answered:  # the gate has read the card and set the status; its commit is next
                with real() as owner:
                    requests.respond(owner, card.id, HumanResponse(decision=HumanDecision.ANSWER, answer="Cafés"))
                answered.append(card.id)
    monkeypatch.setattr(company_graph, "session_scope", gate_session)

    assert gate(project.id) and answered
    with session_scope() as x:
        assert (x.get(Project, project.id).status, x.get(AgentRequest, card.id).status) == ("WAITING_HUMAN", "ANSWERED")
    assert resume_jobs(project.id) == [], "the strand: nothing will wake the run"

    with session_scope() as x:
        # The answer re-plans the run it read as running; the sweep resumes the parked run only once that is done.
        [replan] = x.scalars(select(Job).where(Job.kind == "realign_project",
                                               Job.payload["project_id"].as_integer() == project.id)).all()
        assert project.id not in holds.reconcile_parked(x)
        replan.status = "done"
    with session_scope() as x:
        assert project.id in holds.reconcile_parked(x)
    [resume] = resume_jobs(project.id)
    with session_scope() as x:
        assert project.id not in holds.reconcile_parked(x), "its resume is in flight"
        x.get(Job, resume.id).status = "done"
    with session_scope() as x:
        assert project.id not in holds.reconcile_parked(x), "a resume that found nothing to do is not queued again"


def test_a_waiting_run_whose_held_task_is_cancelled_is_resumed_by_the_sweep(s, project):
    held = task(s, project, "held_then_dropped")
    hold(s, card(s, project, "tasks"), held)
    project.status = "WAITING_HUMAN"
    s.commit()
    assert project.id not in holds.reconcile_parked(s)

    control.cancel_task(s, held.id, lang="en")
    s.commit()
    assert project.id in holds.reconcile_parked(s)


def test_the_sweep_asks_again_for_a_run_a_skipped_resume_left_parked(s, project):
    """A swept resume skipped because a card held the run then writes no project row, and neither does that card's
    task ending: the sweep asks again in its next window instead of finding its key spent."""
    from datetime import UTC, datetime
    project.status = "WAITING_HUMAN"
    s.commit()
    now = datetime.now(UTC)
    with session_scope() as x:
        assert project.id in holds.reconcile_parked(x, now)
    with session_scope() as x:
        for j in x.scalars(select(Job).where(Job.kind == "resume_project",
                                             Job.payload["project_id"].as_integer() == project.id)):
            j.status = "done"
    with session_scope() as x:
        assert project.id not in holds.reconcile_parked(x, now), "one resume per window"
    with session_scope() as x:
        assert project.id in holds.reconcile_parked(x, now + holds.SWEEP_RETRY)


# ---------------------------------------------------------------- siblings of a held task (design 1.5, unit 9)

OPTIONS = ["Cafés", "Gyms"]


def schedule(project_id: int) -> dict:
    return company_graph.schedule({"project_id": project_id})


def status(task_id: int) -> str:
    with session_scope() as x:
        return x.get(Task, task_id).status


def moved(task_id: int, to: str) -> None:
    """The run's work step left the task in this status."""
    with session_scope() as x:
        x.get(Task, task_id).status = to


def held_by_the_project_manager(s, project, *keys: str) -> AgentRequest:
    return requests.create(s, ask(title="Which audience first", options=OPTIONS, recommended_option="Cafés",
                                  context=contract(HoldKind.TASKS, keys=keys, owner_work=False)),
                           from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))


def test_a_sibling_runs_while_the_task_beside_it_is_held(s, project):
    """A question about one feature stopped the research that did not need it: any open card parked the whole run."""
    project.status = "RUNNING"
    feature, research = task(s, project, "the_feature"), task(s, project, "the_research", "READY")
    held_by_the_project_manager(s, project, "the_feature")
    s.commit()

    assert schedule(project.id) == {"batch": [research.id], "next": "work"}
    assert status(feature.id) == "PROPOSED"


def test_the_gate_counts_a_result_whose_card_is_decided_and_leaves_the_one_still_held(s, project, gate, monkeypatch):
    approved = held_result(s, project, ask(RequestType.APPROVAL, title="Buy the domain",
                                           context=contract(HoldKind.THIS_TASK)), monkeypatch=monkeypatch)
    waiting = held_result(s, project, ask(title="Which audience first", context=contract(HoldKind.THIS_TASK,
                                                                                           owner_work=False)),
                          monkeypatch=monkeypatch)
    requests.respond(s, approved.result["request_ids"][0], HumanResponse(decision=HumanDecision.APPROVE))
    project.status = "RUNNING"
    s.commit()

    assert schedule(project.id) == {"batch": [], "next": "human_gate"}
    assert not gate(project.id), "a decided result is settled while another still waits"
    assert (status(approved.id), status(waiting.id)) == ("DONE", "APPROVAL_REQUIRED")
    assert schedule(project.id) == {"batch": [], "next": "human_gate"}
    assert gate(project.id), "only the held result is left: the run waits for its card"
    assert status(waiting.id) == "APPROVAL_REQUIRED", "a result its card still holds is never judged"


def _after_the_held_one(s, project, held: Task) -> None:
    task(s, project, "after", depends_on=(held.id,))


@pytest.mark.parametrize("beside, waits", [
    ("RUNNING", False), ("REVIEW", False), ("READY", False), ("PROPOSED", False), ("DONE", True), ("FAILED", True),
    (_after_the_held_one, True)])
def test_the_run_parks_only_when_no_task_can_start_one_is_held_and_none_runs(s, project, beside, waits):
    held = task(s, project, "held")
    hold(s, card(s, project, "tasks"), held)
    if callable(beside):
        beside(s, project, held)
    else:
        task(s, project, "beside", beside)

    assert holds.park_reason(s, project.id) == (holds.ParkReason.STARVED if waits else None)


def test_the_project_manager_never_reviews_a_round_while_a_held_task_is_open(s, project, gate):
    project.status = "RUNNING"
    task(s, project, "groundwork", "DONE")
    hold(s, card(s, project, "tasks"), task(s, project, "held"))
    s.commit()

    assert schedule(project.id) == {"batch": [], "next": "human_gate"}
    assert gate(project.id)


def test_a_planning_card_lets_planned_work_go_on_and_holds_the_next_plan_step(s, project, gate):
    project.status = "RUNNING"
    planned = task(s, project, "planned", "READY")
    card(s, project, "planning")
    s.commit()

    assert schedule(project.id) == {"batch": [planned.id], "next": "work"}
    moved(planned.id, "RUNNING")
    with session_scope() as x:
        assert holds.park_reason(x, project.id) is None, "work already started goes on"
    moved(planned.id, "DONE")
    assert schedule(project.id) == {"batch": [], "next": "human_gate"}, "the CEO's next review waits"
    assert gate(project.id)
    with session_scope() as x:
        assert holds.park_reason(x, project.id) == holds.ParkReason.PLANNING


@pytest.mark.parametrize("asks, then", [(HoldKind.PLANNING, "human_gate"), (HoldKind.RUN, "human_gate"),
                                        (HoldKind.NOTHING, "decide")])
def test_a_review_whose_card_holds_the_next_plan_step_waits_before_deciding(s, project, monkeypatch, asks, then):
    task(s, project, "groundwork", "DONE")
    s.commit()
    review = ProjectManagerReviewOut(assessment="a", unmet_success_criteria=[], action=ProjectManagerReviewAction.COMPLETE,
                          meeting_agenda=None, meeting_participants=[], follow_up_tasks=[],
                          requests=[ask(title="Which market next", context=contract(asks, owner_work=False))])
    monkeypatch.setattr(company_graph, "run_agent", lambda *a, **k: review)

    assert company_graph.ceo_review({"project_id": project.id}) == {"next": then}


def test_an_answer_that_frees_the_last_held_task_resumes_the_run_once(s, project):
    task(s, project, "groundwork", "DONE")
    held = task(s, project, "visit_cafes")
    asked = held_by_the_project_manager(s, project, "visit_cafes")
    project.status = "WAITING_HUMAN"
    s.commit()
    assert holds.park_reason(s, project.id) == holds.ParkReason.STARVED

    with session_scope() as x:
        requests.respond(x, asked.id, HumanResponse(decision=HumanDecision.ANSWER, chosen_option="Cafés"))
    [resume] = resume_jobs(project.id)
    with session_scope() as x:
        assert project.id not in holds.reconcile_parked(x), "its resume is in flight"
        x.get(Job, resume.id).status = "done"
        x.get(Project, project.id).status = "RUNNING"   # the resumed run went on
    with session_scope() as x:
        assert project.id not in holds.reconcile_parked(x)
        assert [t.id for t in tasks.promote_ready(x, project.id)] == [held.id]
    assert len(resume_jobs(project.id)) == 1


def test_a_card_says_answering_it_starts_the_run_again_when_it_alone_keeps_a_task_from_starting(s, project):
    """Two cards each held a task of a run with nothing else to start: answering either starts the run again, and the
    card said it would only once the other was settled."""
    task(s, project, "groundwork", "DONE")
    first = hold(s, card(s, project, "tasks"), task(s, project, "visit_cafes"))
    second = hold(s, card(s, project, "tasks"), task(s, project, "visit_gyms"))
    project.status = "WAITING_HUMAN"
    s.commit()
    resumes, waits = msg("request.effect.resumes", "en"), msg("request.effect.still_waits", "en")

    assert all(resumes in requests.outcome(s, r, "en").says for r in (first, second))
    card(s, project, "run")
    assert all(waits in requests.outcome(s, r, "en").says for r in (first, second))


# ---------------------------------------------------------------- a re-plan beside the running siblings

def test_a_task_a_re_plan_cancels_as_the_run_promotes_it_is_never_started(s, project):
    """A re-plan beside a running run cancels unstarted work under the project's row lock and then the task's; the
    run's scheduling step read the task without either and wrote READY over the cancel, so the cancelled task ran."""
    planned = task(s, project, "planned")
    project.status = "RUNNING"
    s.commit()
    routed = []
    run = threading.Thread(target=lambda: routed.append(schedule(project.id)))
    with session_scope() as replan:
        replan.scalar(select(Project).where(Project.id == project.id).with_for_update())
        tasks.transition(replan.scalar(select(Task).where(Task.id == planned.id).with_for_update()),
                         TaskStatus.CANCELLED)
        replan.flush()
        run.start()
        waiting_on_a_lock()
    run.join(10)

    assert status(planned.id) == "CANCELLED"
    assert routed and routed[0]["batch"] == []


def test_a_task_a_re_plan_cancels_as_the_run_starts_it_is_never_run(s, project, monkeypatch):
    """The run's work step moved a scheduled task to RUNNING without its row lock, so a re-plan's cancel landing between
    the step's checks and the start was written over, and the cancelled task ran."""
    planned = task(s, project, "planned", "READY")
    s.commit()
    ran, done = [], []
    monkeypatch.setattr(company_graph, "run_agent", lambda *a, **k: ran.append(a))
    checked, cancelled = threading.Event(), threading.Event()
    real = company_graph._ruled_out

    def checks_then_waits(*a):
        out = real(*a)
        checked.set()
        cancelled.wait(10)
        return out
    monkeypatch.setattr(company_graph, "_ruled_out", checks_then_waits)
    run = threading.Thread(target=lambda: done.append(company_graph.work({"project_id": project.id,
                                                                          "task_id": planned.id})))
    run.start()
    assert checked.wait(10)
    with session_scope() as replan:
        tasks.transition(replan.scalar(select(Task).where(Task.id == planned.id).with_for_update()),
                         TaskStatus.CANCELLED)
        replan.flush()
        cancelled.set()
        waiting_on_a_lock()
    run.join(10)

    assert (done, ran, status(planned.id)) == ([{"done": [planned.id]}], [], "CANCELLED")


@pytest.mark.parametrize("asks_for_build", [True, False])
def test_an_approval_while_schedule_asks_for_the_build_never_deadlocks(s, project, monkeypatch, asks_for_build):
    """`schedule` holds the project row while it asks for the build. An approval's Grant insert takes the row's key
    share and then waits on the row; an upgrade to FOR UPDATE in the build ask would wait on that share."""
    project.status = "RUNNING"
    if asks_for_build:
        project.plan = {**(project.plan or {}), "build": {"title": "App", "problem": "p", "why_software": "w",
                                                          "task_ids": []}}
    r = AgentRequest(project_id=project.id, from_agent="project_manager", type="APPROVAL", title="Buy the domain", summary="s",
                     rationale="r", policy_mode="approve", status="PENDING", hold_scope="none",
                     action_kind="internal.other")
    s.add(r)
    s.commit()
    locked, go = threading.Event(), threading.Event()
    admit = company_graph.rounds.admit

    def admit_after_the_answer_waits(*a, **k):
        locked.set()
        go.wait(10)
        return admit(*a, **k)
    monkeypatch.setattr(company_graph.rounds, "admit", admit_after_the_answer_waits)
    errors = []

    def run():
        try:
            company_graph.schedule({"project_id": project.id})
        except Exception as exc:  # noqa: BLE001
            errors.append(("schedule", type(exc).__name__))

    def answer():
        try:
            with session_scope() as x:
                requests.respond(x, r.id, HumanResponse(decision=HumanDecision.APPROVE, note="go ahead"))
        except Exception as exc:  # noqa: BLE001
            errors.append(("respond", type(exc).__name__))

    a = threading.Thread(target=run)
    a.start()
    assert locked.wait(10)
    b = threading.Thread(target=answer)
    b.start()
    waiting_on_a_lock()
    go.set()
    a.join(20)
    b.join(20)
    assert errors == []


def test_a_spent_round_leaves_a_task_a_card_holds_for_its_answer_and_parks(s, project, monkeypatch):
    project.status = "RUNNING"
    beside = task(s, project, "beside", "READY")
    held = task(s, project, "held")
    hold(s, card(s, project, "tasks"), held)
    s.commit()
    monkeypatch.setattr(company_graph.rounds, "admit", lambda s_, p, ready: ([], ready))

    routed = company_graph.schedule({"project_id": project.id})

    with session_scope() as x:
        assert (x.get(Task, beside.id).status, x.get(Task, held.id).status) == ("CANCELLED", "PROPOSED")
        assert holds.park_reason(x, project.id) == holds.ParkReason.STARVED
    assert routed["next"] == "human_gate"
