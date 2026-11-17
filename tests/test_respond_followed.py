"""What a card says "after you answer" is what the answer runs (docs/design/work-path.md 2.3 and 7): respond runs each
answer use the card declared and the server's own work for the decision, records each as followed, and a run the
answer frees and re-plans resumes only once the re-plan is done."""
import threading
from datetime import UTC, datetime, timedelta

import pytest
from ask_kit import by_project_manager, contract
from gate_sites import sites
from justshipit.contracts.handoff import FIX_QUESTION_KEY, SPEC_QUESTION_KEY
from justshipit.contracts.planning import RequestWithdrawOut
from justshipit.contracts.requests import HumanDecision, HumanResponse
from justshipit.contracts.work_path import AnswerEffect, AnswerUse, ExpiryPolicy, HoldKind
from justshipit.db.models import AgentRequest, Job, Project, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import context as ctx
from justshipit.orchestration import realign
from justshipit.services import holds, requests
from sqlalchemy import select, text
from test_park import ask, resume_jobs, task, waiting_on_a_lock
from test_request_expiry import as_is, followed

pytestmark = pytest.mark.usefixtures("own_jobs_settled")

OPTIONS = ["Cafés", "Gyms"]


def by_the_project_manager(s, project, draft) -> AgentRequest:
    return requests.create(s, draft, from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))


def card_using(s, project, *uses: AnswerUse, holds_: HoldKind = HoldKind.NOTHING, title: str = "Which audience first",
               expiry: ExpiryPolicy = ExpiryPolicy.CLOSE) -> AgentRequest:
    return by_the_project_manager(s, project, ask(title=title, options=OPTIONS, recommended_option="Cafés", context=contract(
        holds_, on_answer=uses, expiry=expiry, default="Cafés" if expiry == ExpiryPolicy.DEFAULT else None,
        owner_work=False)))


def use(effect: AnswerEffect, option: str | None = None, *keys: str) -> AnswerUse:
    return AnswerUse(effect=effect, option=option, task_keys=list(keys))


def picks(option: str = "Cafés") -> HumanResponse:
    """The owner picks an option and writes nothing: only what the card declared re-plans."""
    return HumanResponse(decision=HumanDecision.ANSWER, chosen_option=option)


def answer(card_id: int, resp: HumanResponse) -> None:
    with session_scope() as x:
        requests.respond(x, card_id, resp)


def realigns(project_id: int) -> list[Job]:
    with session_scope() as x:
        return list(x.scalars(select(Job).where(Job.kind == "realign_project",
                                                Job.payload["project_id"].as_integer() == project_id).order_by(Job.id)))


def run_realign(monkeypatch, job: Job) -> None:
    """The worker claims the re-plan and runs it; the CEO changes nothing."""
    monkeypatch.setattr(realign, "run_agent", lambda *a, **k: as_is())
    with session_scope() as x:
        x.get(Job, job.id).status = "running"
    p = job.payload
    realign.realign(p["project_id"], p["request_id"], resume_after=p.get("resume_after", False))
    with session_scope() as x:
        x.get(Job, job.id).status = "done"


# ---------------------------------------------------------------- the regression

def test_an_answer_with_a_note_on_a_waiting_project_re_plans_it(s, project):
    """respond re-planned on the owner's own words only while the project was RUNNING, so a note on the card that held
    the waiting run never reached the CEO."""
    project.status = "WAITING_HUMAN"
    card = by_the_project_manager(s, project, ask(context=contract(HoldKind.RUN, owner_work=False)))
    s.commit()

    with session_scope() as x:
        requests.respond(x, card.id, HumanResponse(decision=HumanDecision.ANSWER, answer="Cafés",
                                                   note="Start with the ones near the station"))

    assert [j.payload["request_id"] for j in realigns(project.id)] == [card.id]


# ---------------------------------------------------------------- each answer use runs and is recorded

def _informs(x, project, card):
    assert x.get(Project, project.id).context["owner_answers"][-1]["request"] == f"REQ-{card.id}"
    return {("informs", "project", project.id, None)}


def _replans(x, project, card):
    [job] = realigns(project.id)
    assert job.payload == {"project_id": project.id, "request_id": card.id}, "a running project re-plans beside its run"
    return {("replans", "job", job.id, None)}


def _feeds(x, project, card):
    t = x.scalar(select(Task).where(Task.project_id == project.id, Task.key == "visit_cafes"))
    assert f"[REQ-{card.id}] {card.title} → ANSWERED: Cafés" in ctx.fed_answers(x, t)
    return {("feeds", "task", t.id, None)}


USES = {AnswerEffect.INFORMS: _informs, AnswerEffect.REPLANS: _replans, AnswerEffect.FEEDS: _feeds}


def test_the_cases_walk_every_answer_use():
    assert set(USES) == set(AnswerEffect)


@pytest.mark.parametrize("effect", USES)
def test_each_answer_use_the_card_declared_runs_and_is_recorded_as_followed(s, project, effect):
    project.status = "RUNNING"
    task(s, project, "visit_cafes")
    card = card_using(s, project, use(effect, None, *(["visit_cafes"] if effect == AnswerEffect.FEEDS else [])))
    s.commit()

    answer(card.id, picks())

    with session_scope() as x:
        assert followed(x, card.id) == USES[effect](x, project, card)


def test_only_the_uses_of_the_option_picked_run(s, project):
    cafes, gyms = task(s, project, "visit_cafes"), task(s, project, "visit_gyms")
    card = card_using(s, project, use(AnswerEffect.FEEDS, "Cafés", "visit_cafes"),
                      use(AnswerEffect.FEEDS, "Gyms", "visit_gyms"))
    s.commit()

    answer(card.id, picks("Gyms"))

    with session_scope() as x:
        assert followed(x, card.id) == {("feeds", "task", gyms.id, "Gyms")}
        assert ctx.fed_answers(x, x.get(Task, cafes.id)) == ""


def test_a_hold_runs_nothing_the_card_said_answering_does(s, project):
    project.status = "RUNNING"
    task(s, project, "visit_cafes")
    card = card_using(s, project, use(AnswerEffect.REPLANS), use(AnswerEffect.FEEDS, "Cafés", "visit_cafes"))
    s.commit()

    answer(card.id, HumanResponse(decision=HumanDecision.DEFER))

    assert realigns(project.id) == []
    with session_scope() as x:
        assert followed(x, card.id) == set()


def test_a_default_runs_what_its_option_said_answering_does(s, project):
    planned = task(s, project, "visit_cafes")
    card = card_using(s, project, use(AnswerEffect.FEEDS, "Cafés", "visit_cafes"), expiry=ExpiryPolicy.DEFAULT)
    card.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    s.commit()

    with session_scope() as x:
        assert requests.expire_due(x, project.id, datetime.now(UTC)) == [card.id]

    with session_scope() as x:
        assert ("feeds", "task", planned.id, "Cafés") in followed(x, card.id)
        fed = ctx.fed_answers(x, x.get(Task, planned.id))
        assert "nobody answered by the deadline" in fed and "not the owner's answer): Cafés" in fed


# ---------------------------------------------------------------- the server's work for every decision

# Each kind of work `requests._effect` queues, and a card that sets it going.
EFFECTS = {
    "dev_fix_answered": ("QUESTION", None, {FIX_QUESTION_KEY: 9}),
    "dev_resume": ("QUESTION", None, {"dev_job_id": 9}),
    "spec_questions": ("QUESTION", None, {SPEC_QUESTION_KEY: True, "spec_id": 9}),
    "dev_merge": ("APPROVAL", "dev.merge", {"dev_job_id": 9}),
    "write_spec": ("APPROVAL", requests.PLAN_CARD, {"plan_id": 9}),
    "spec_approved": ("APPROVAL", "dev.spec_approve", {"spec_id": 9}),
    "execute_action": ("APPROVAL", "external.email_send", {"payload": {"to": "a@example.com", "subject": "s",
                                                                       "body": "b"}}),
    "raise_budget": ("RESOURCE", requests.BUDGET_RAISE, {}),
    "reassess_project": ("APPROVAL", "project.reassess", {}),
    "skill_activate": ("SKILL", "skill.activate", {"skill_id": 9}),
}


def test_the_cases_walk_every_kind_of_work_a_decision_queues():
    assert set(EFFECTS) == {x.kind for x in sites() if x.where.startswith("services/requests:") and x.by_owner == "effect"}


@pytest.mark.parametrize("kind", EFFECTS)
def test_the_work_a_decision_sets_going_is_queued_and_recorded_as_followed(s, project, kind):
    type_, action_kind, params = EFFECTS[kind]
    card = AgentRequest(project_id=project.id, from_agent="product", type=type_, title=f"Sets going {kind}",
                        summary="s", rationale="r", action_kind=action_kind, action_params=params,
                        estimated_cost_usd=5.0 if kind == "raise_budget" else None, policy_mode="approve",
                        status="PENDING", hold_scope="none")
    s.add(card)
    s.commit()

    answer(card.id, HumanResponse(decision=HumanDecision.ANSWER, answer="Keep the old header")
           if type_ == "QUESTION" else HumanResponse(decision=HumanDecision.APPROVE, confirm_text=f"REQ-{card.id}"))

    with session_scope() as x:
        [(job_id,)] = [(f[2],) for f in followed(x, card.id) if f[:2] == ("queued", "job")]
        assert x.get(Job, job_id).kind == kind


# ---------------------------------------------------------------- order on resume

def test_an_answer_that_frees_and_re_plans_a_waiting_run_resumes_it_only_after_the_re_plan(s, project, monkeypatch):
    project.status = "WAITING_HUMAN"
    card = card_using(s, project, use(AnswerEffect.REPLANS), holds_=HoldKind.RUN)
    s.commit()

    answer(card.id, picks())

    [replan] = realigns(project.id)
    assert replan.payload == {"project_id": project.id, "request_id": card.id, "resume_after": True}
    assert resume_jobs(project.id) == [], "respond queues the re-plan, not the resume"
    with session_scope() as x:
        assert holds.park_reason(x, project.id) is None
        assert project.id not in holds.reconcile_parked(x), "nor does the sweep resume it first"

    run_realign(monkeypatch, replan)

    [resume] = resume_jobs(project.id)
    assert resume.idempotency_key == f"resume:{project.id}:{card.id}"
    with session_scope() as x:
        assert followed(x, card.id) == {("replans", "job", replan.id, None), ("resumes", "job", resume.id, None)}


def test_two_answers_that_re_plan_a_waiting_run_resume_it_after_the_second_re_plan(s, project, monkeypatch):
    project.status = "WAITING_HUMAN"
    first = card_using(s, project, use(AnswerEffect.REPLANS), holds_=HoldKind.RUN, title="Which city first")
    second = card_using(s, project, use(AnswerEffect.REPLANS), holds_=HoldKind.RUN, title="Which price to test")
    s.commit()

    answer(first.id, picks())
    answer(second.id, picks())
    one, two = realigns(project.id)

    run_realign(monkeypatch, one)
    assert resume_jobs(project.id) == [], "the second answer's re-plan is still to run"
    run_realign(monkeypatch, two)
    assert [j.idempotency_key for j in resume_jobs(project.id)] == [f"resume:{project.id}:{second.id}"]


# ---------------------------------------------------------------- an answer and the CEO's withdrawal on one card

def withdraw(x, project_id: int, card_id: int) -> list[int]:
    return requests.consolidate(x, project_id, [], [RequestWithdrawOut(id=card_id, reason="The plan moved on")])[
        "withdrawn"]


def test_an_answer_holding_the_card_wins_over_the_project_managers_withdrawal(s, project):
    card = card_using(s, project)
    s.commit()

    with session_scope() as owner:
        requests.respond(owner, card.id, picks())
        with session_scope() as project_manager:
            assert withdraw(project_manager, project.id, card.id) == [], "the cleanup skips a card the answer holds"

    with session_scope() as x:
        assert x.get(AgentRequest, card.id).status == "ANSWERED"


def test_a_withdrawal_holding_the_card_wins_and_the_answer_is_refused_as_already_decided(s, project):
    card = card_using(s, project)
    s.commit()
    refused: list[requests.ResponseRejected] = []

    def owner() -> None:
        try:
            answer(card.id, picks())
        except requests.ResponseRejected as exc:
            refused.append(exc)

    late = threading.Thread(target=owner)
    with session_scope() as project_manager:
        assert withdraw(project_manager, project.id, card.id) == [card.id]
        late.start()
        waiting_on_a_lock()
    late.join(10)

    assert [r.what.key for r in refused] == ["request.already_decided"]
    with session_scope() as x:
        assert x.get(AgentRequest, card.id).status == "CANCELLED"


def test_an_answer_on_a_paused_run_re_plans_and_the_owners_resume_waits_for_it(s, project, monkeypatch):
    """The owner answers a card with a note while the run is paused, then resumes before the re-plan ran: the run waits
    for the re-plan and resumes once it is done, never on the plan the answer is changing."""
    from justshipit.services import control
    project.status, project.context = "PAUSED", {**(project.context or {}), "paused": True}
    card = by_the_project_manager(s, project, ask(context=contract(HoldKind.RUN, owner_work=False)))
    s.commit()

    answer(card.id, HumanResponse(decision=HumanDecision.ANSWER, answer="Cafés", note="Near the station first"))
    [replan] = realigns(project.id)
    assert replan.payload.get("resume_after") is True
    with session_scope() as x:
        control.resume(x, project.id)
    assert resume_jobs(project.id) == [], "the owner's resume waits for the re-plan"
    with session_scope() as x:
        assert x.get(Project, project.id).status == "WAITING_HUMAN"
        assert project.id not in holds.reconcile_parked(x)

    run_realign(monkeypatch, replan)

    assert len(resume_jobs(project.id)) == 1


def test_the_sweep_waits_for_a_re_plan_queued_before_the_gate_wrote_waiting(s, project):
    """An answer in the window before the gate wrote WAITING_HUMAN queued a re-plan beside a run it read as running;
    the sweep must not resume the parked run before that re-plan is done."""
    project.status = "RUNNING"
    card = by_the_project_manager(s, project, ask(context=contract(HoldKind.RUN, owner_work=False)))
    s.commit()
    answer(card.id, HumanResponse(decision=HumanDecision.ANSWER, answer="Cafés", note="Near the station first"))
    [replan] = realigns(project.id)
    assert "resume_after" not in replan.payload
    with session_scope() as x:
        x.get(Project, project.id).status = "WAITING_HUMAN"

    with session_scope() as x:
        assert holds.park_reason(x, project.id) is None
        assert project.id not in holds.reconcile_parked(x)
    with session_scope() as x:
        x.get(Job, replan.id).status = "done"
    with session_scope() as x:
        assert project.id in holds.reconcile_parked(x)


def test_a_link_written_under_a_project_lock_never_waits_on_a_card_being_answered(s, project):
    """respond holds the card and then waits for the project; a realign holds the project and writes a hold link on
    that card. The card's lock must not block the link's foreign key, or the two deadlock."""
    from justshipit.contracts.work_path import HoldPhase, LinkSource, Rel, TargetKind
    from justshipit.services import request_contract
    card = by_the_project_manager(s, project, ask(context=contract(HoldKind.RUN, owner_work=False)))
    t = task(s, project, "redo")
    s.commit()

    with session_scope() as answering:
        requests.respond(answering, card.id, picks())  # holds the card's lock until this session commits
        with session_scope() as replanning:
            replanning.execute(text("SET LOCAL lock_timeout = '2s'"))
            request_contract.link(replanning, card.id, (request_contract.Link(
                Rel.HOLDS, TargetKind.TASK, t.id, LinkSource.AGENT, phase=HoldPhase.START),), 1)
