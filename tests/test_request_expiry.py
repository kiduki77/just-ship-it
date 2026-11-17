"""What a card's "if you don't answer" says is what happens (docs/design/work-path.md 7, Expiry): an unanswered card
expires by its rule, a question may go on with its recommended option, recorded as the default and never as the owner's
answer, an approval only closes, and the expiry is recorded in the card's `followed` links."""
import threading
from datetime import UTC, datetime, timedelta

import pytest
from ask_kit import by_project_manager, contract
from justshipit.contracts.notify import Notice
from justshipit.contracts.planning import RealignOut
from justshipit.contracts.requests import HumanDecision, HumanResponse
from justshipit.contracts.work_path import ExpiryPolicy, HoldKind
from justshipit.db.models import AgentRequest, Grant, Job, OwnerTask, Project, RequestLink, Task
from justshipit.db.session import session_scope
from justshipit.engineering import owner_trace
from justshipit.orchestration import context as ctx
from justshipit.orchestration import realign
from justshipit.services import holds, requests
from notify_kit import choose, notice_jobs, wipe
from sqlalchemy import select
from test_park import ask, gate, held_result, resume_jobs, task, waiting_on_a_lock  # noqa: F401 (fixtures)

pytestmark = pytest.mark.usefixtures("own_jobs_settled")

OPTIONS = ["Cafés", "Gyms"]


def past_due(s, req: AgentRequest, ago: timedelta = timedelta(minutes=1)) -> AgentRequest:
    req.expires_at = datetime.now(UTC) - ago
    s.flush()
    return req


def question(holds_: HoldKind, expiry: ExpiryPolicy, *, title: str = "Which audience first", keys=()):
    return ask(title=title, options=OPTIONS, recommended_option="Cafés", context=contract(
        holds_, keys=tuple(keys), expiry=expiry, default="Cafés" if expiry == ExpiryPolicy.DEFAULT else None,
        owner_work=False))


def by_the_project_manager(s, project, draft) -> AgentRequest:
    return requests.create(s, draft, from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))


def followed(x, request_id: int) -> set[tuple]:
    return {(x_.effect, x_.target_kind, x_.target_id, x_.option_key) for x_ in x.scalars(select(RequestLink).where(
        RequestLink.request_id == request_id, RequestLink.rel == "followed"))}


def realigns(project_id: int) -> list[Job]:
    with session_scope() as x:
        return list(x.scalars(select(Job).where(Job.kind == "realign_project",
                                                Job.payload["project_id"].as_integer() == project_id).order_by(Job.id)))


def as_is() -> RealignOut:
    """The CEO's re-plan that changes nothing."""
    return RealignOut(assessment="The plan still holds", goal_interpretation="g", out_of_scope_additions=[],
                      cancel_task_ids=[], revise_tasks=[], new_tasks=[], withdraw_requests=[])


def expire(project_id: int | None, now: datetime | None = None) -> list[int]:
    with session_scope() as x:
        return requests.expire_due(x, project_id, now or datetime.now(UTC))


# ---------------------------------------------------------------- the regression

def test_a_card_past_its_deadline_no_longer_holds_its_project_forever(s, project, gate):
    """The owner can't answer a card past its deadline (respond refuses a yes), and nothing ever closed it, so a card
    that held the run held it for good."""
    project.status = "RUNNING"
    card = past_due(s, requests.create(s, ask(context=contract(HoldKind.RUN, owner_work=False)), from_agent="project_manager",
                                       project_id=project.id, origin=by_project_manager(s, project.id)))
    s.commit()
    with pytest.raises(requests.ResponseRejected), session_scope() as x:
        requests.respond(x, card.id, HumanResponse(decision=HumanDecision.ANSWER, answer="Cafés"))

    assert not gate(project.id), "the gate closes what is past due before it decides whether the run waits"
    with session_scope() as x:
        assert x.get(AgentRequest, card.id).status == "EXPIRED"


# ---------------------------------------------------------------- each rule

def test_a_question_nobody_answers_goes_on_with_its_default_recorded_as_the_default(s, project):
    project.status = "WAITING_HUMAN"
    card = past_due(s, by_the_project_manager(s, project, question(HoldKind.RUN, ExpiryPolicy.DEFAULT)))
    s.commit()

    assert card.id in expire(None), "the worker's sweep covers every project"
    with session_scope() as x:
        r, p = x.get(AgentRequest, card.id), x.get(Project, project.id)
        assert r.status == "EXPIRED"
        assert r.response == {"decision": "answer", "chosen_option": "Cafés", "by": "expiry"}
        assert p.context["owner_answers"][-1] == {"request": f"REQ-{card.id}", "title": card.title,
                                                  "decision": "EXPIRED", "answer": "Cafés", "note": None,
                                                  "by": "expiry"}
        line = next(x_ for x_ in ctx.project_brief(x, p).splitlines() if f"[REQ-{card.id}]" in x_)
        assert "not the owner's answer" in line and "Cafés" in line
        assert f"REQ-{card.id}" not in {o.id for o in owner_trace.owner_sources(x, p, "en")}, \
            "a default is never a source of the owner's words for a spec"
        assert "default, not as your answer" in requests.closed_why(r, "en")
        assert holds.park_reason(x, project.id) is None
        [resume] = resume_jobs(project.id)
        assert followed(x, card.id) == {("defaulted", "request", card.id, "Cafés"), ("resumes", "job", resume.id, None)}


def test_a_held_result_counts_when_its_question_goes_on_with_its_default(s, project, gate, monkeypatch):
    t = held_result(s, project, question(HoldKind.THIS_TASK, ExpiryPolicy.DEFAULT), monkeypatch=monkeypatch)
    assert t.status == "APPROVAL_REQUIRED"
    past_due(s, s.get(AgentRequest, t.result["request_ids"][0]))
    s.commit()

    assert not gate(project.id)
    with session_scope() as x:
        assert x.get(Task, t.id).status == "DONE"


def test_a_held_result_is_rejected_when_its_card_closes_unanswered(s, project, gate, monkeypatch):
    t = held_result(s, project, question(HoldKind.THIS_TASK, ExpiryPolicy.CLOSE), monkeypatch=monkeypatch)
    card = past_due(s, s.get(AgentRequest, t.result["request_ids"][0]))
    s.commit()

    assert not gate(project.id)
    with session_scope() as x:
        assert x.get(Task, t.id).status == "REJECTED"
        assert ("rejected", "task", t.id, None) in followed(x, card.id)


def test_a_card_that_closes_cancels_what_it_held_and_the_run_resumes_only_after_the_re_plan(s, project, monkeypatch):
    planned = task(s, project, "visit_cafes")
    project.status = "WAITING_HUMAN"
    card = past_due(s, by_the_project_manager(s, project, question(HoldKind.TASKS, ExpiryPolicy.CLOSE, keys=["visit_cafes"])))
    s.commit()
    assert holds.park_reason(s, project.id) == holds.ParkReason.STARVED

    assert expire(project.id) == [card.id]
    [replan] = realigns(project.id)
    with session_scope() as x:
        t = x.get(Task, planned.id)
        assert (t.status, t.error) == (
            "CANCELLED", f"Cancelled: it waited for REQ-{card.id}, which closed with no answer by its deadline")
        assert x.get(AgentRequest, card.id).response == {"by": "expiry"}
        assert followed(x, card.id) == {("closed", "request", card.id, None), ("cancelled", "task", planned.id, None),
                                        ("replans", "job", replan.id, None)}
        assert replan.payload == {"project_id": project.id, "request_id": card.id, "resume_after": True}
        assert holds.park_reason(x, project.id) is None
        assert resume_jobs(project.id) == [] and project.id not in holds.reconcile_parked(x), \
            "nothing resumes the run before its re-plan is done"

    prompts = []
    monkeypatch.setattr(realign, "run_agent", lambda role, purpose, prompt, *a, **k: prompts.append(prompt) or as_is())
    with session_scope() as x:
        x.get(Job, replan.id).status = "running"   # the worker claimed it
    realign.realign(project.id, card.id, resume_after=True)

    assert f"## A decision nobody answered\n- REQ-{card.id}" in prompts[0]
    [resume] = resume_jobs(project.id)
    with session_scope() as x:
        assert ("resumes", "job", resume.id, None) in followed(x, card.id)


def test_a_card_that_keeps_waiting_never_expires(s, project):
    card = by_the_project_manager(s, project, question(HoldKind.NOTHING, ExpiryPolicy.KEEP_WAITING))
    assert card.expires_at is None
    past_due(s, card)   # even a row given a date
    s.commit()

    assert expire(project.id) == []
    with session_scope() as x:
        assert x.get(AgentRequest, card.id).status == "PENDING"


def test_a_card_from_before_the_work_path_is_left_alone(s, project):
    old = AgentRequest(project_id=project.id, from_agent="project_manager", type="QUESTION", title="Old card", summary="s",
                       rationale="r", policy_mode="approve", status="PENDING", hold_scope="run",
                       expires_at=datetime.now(UTC) - timedelta(days=3))
    s.add(old)
    s.commit()

    assert expire(project.id) == []
    with session_scope() as x:
        assert x.get(AgentRequest, old.id).status == "PENDING"


# What could go on with a default if its row said so: none of these may, so each only closes.
NEVER_DEFAULTS = {
    "an approval": dict(type="APPROVAL"),
    "a question that sends": dict(type="QUESTION", action_kind="external.email_send"),
    "a question that spends": dict(type="QUESTION", estimated_cost_usd=5.0),
}


@pytest.mark.parametrize("case", NEVER_DEFAULTS)
def test_a_card_that_approves_spends_or_sends_only_closes_whatever_its_row_says(s, project, case):
    bad = AgentRequest(project_id=project.id, from_agent="project_manager", title=f"Bad row: {case}", summary="s", rationale="r",
                       options=OPTIONS, recommended_option="Cafés", policy_mode="approve", status="PENDING",
                       hold_scope="none", context_version=1, ask_reason="GOAL_UNCLEAR", expiry_policy="default",
                       default_option="Cafés", expires_at=datetime.now(UTC) - timedelta(minutes=1),
                       **NEVER_DEFAULTS[case])
    s.add(bad)
    s.commit()

    assert expire(project.id) == [bad.id]
    with session_scope() as x:
        r = x.get(AgentRequest, bad.id)
        assert (r.status, r.response) == ("EXPIRED", {"by": "expiry"}) and not requests.defaulted(r)
        assert x.scalar(select(Grant).where(Grant.request_id == bad.id)) is None
        assert x.scalar(select(OwnerTask).where(OwnerTask.request_id == bad.id)) is None
        assert ("closed", "request", bad.id, None) in followed(x, bad.id)


# ---------------------------------------------------------------- an answer and the sweep on one row

def test_an_answer_holding_the_row_wins_and_the_sweep_leaves_it(s, project):
    card = by_the_project_manager(s, project, question(HoldKind.RUN, ExpiryPolicy.DEFAULT))
    s.commit()
    at_deadline = card.expires_at + timedelta(seconds=1)

    with session_scope() as owner:
        requests.respond(owner, card.id, HumanResponse(decision=HumanDecision.ANSWER, chosen_option="Gyms"))
        assert expire(project.id, at_deadline) == [], "the sweep skips a row the answer holds"
    assert expire(project.id, at_deadline) == [], "and finds it answered once the answer commits"

    with session_scope() as x:
        r = x.get(AgentRequest, card.id)
        assert (r.status, r.response["chosen_option"], r.response["responded_by"]) == ("ANSWERED", "Gyms", "owner")
        assert not {f for f in followed(x, card.id) if f[0] in ("defaulted", "closed")}


def test_a_sweep_holding_the_row_wins_and_the_answer_is_refused_as_already_decided(s, project):
    card = by_the_project_manager(s, project, question(HoldKind.RUN, ExpiryPolicy.DEFAULT))
    s.commit()
    refused: list[requests.ResponseRejected] = []

    def answer() -> None:
        try:
            with session_scope() as owner:
                requests.respond(owner, card.id, HumanResponse(decision=HumanDecision.ANSWER, chosen_option="Gyms"))
        except requests.ResponseRejected as exc:
            refused.append(exc)

    owner = threading.Thread(target=answer)
    with session_scope() as sweep:
        assert requests.expire_due(sweep, project.id, card.expires_at + timedelta(seconds=1)) == [card.id]
        owner.start()
        waiting_on_a_lock()
    owner.join(10)

    assert [r.what.key for r in refused] == ["request.already_decided"]
    with session_scope() as x:
        r = x.get(AgentRequest, card.id)
        assert (r.status, r.response["by"]) == ("EXPIRED", "expiry")


# ---------------------------------------------------------------- the day before

@pytest.fixture
def slack_notices():
    wipe()
    choose()
    yield
    wipe()


def test_the_owner_hears_once_the_day_before_a_card_expires(s, project, slack_notices):
    soon = by_the_project_manager(s, project, question(HoldKind.RUN, ExpiryPolicy.DEFAULT, title="Which city first"))
    closing = by_the_project_manager(s, project, question(HoldKind.NOTHING, ExpiryPolicy.CLOSE, title="Which price to test"))
    later = by_the_project_manager(s, project, question(HoldKind.NOTHING, ExpiryPolicy.CLOSE, title="Which logo to use"))
    soon.expires_at = datetime.now(UTC) + timedelta(hours=6)
    closing.expires_at = datetime.now(UTC) + timedelta(hours=20)
    s.commit()

    for _ in range(3):
        expire(project.id)

    told = {j.idempotency_key: Notice.model_validate(j.payload).words("en") for j in notice_jobs()
            if j.idempotency_key.endswith(":expiring")}
    assert set(told) == {f"notify:decision_needed:REQ-{soon.id}:expiring",
                         f"notify:decision_needed:REQ-{closing.id}:expiring"}, f"not REQ-{later.id}, due in days"
    title, body = told[f"notify:decision_needed:REQ-{soon.id}:expiring"]
    assert title == "Expires soon: Which city first" and "the team goes with “Cafés”" in body
    assert "this decision closes" in told[f"notify:decision_needed:REQ-{closing.id}:expiring"][1]


def test_a_row_that_defaults_to_a_decline_only_closes_and_its_result_never_counts(s, project, gate, monkeypatch):
    """A row whose default is a decline (filed before the contract refused one, or written by hand) closes: the result
    it held is rejected, never counted as a yes."""
    t = held_result(s, project, question(HoldKind.THIS_TASK, ExpiryPolicy.DEFAULT), monkeypatch=monkeypatch)
    card = s.get(AgentRequest, t.result["request_ids"][0])
    card.options, card.recommended_option, card.default_option = ["Ship it", "Reject this draft"], *(
        ["Reject this draft"] * 2)
    card.action_params = {k: v for k, v in (card.action_params or {}).items() if k != "card"}
    past_due(s, card)
    s.commit()

    assert not gate(project.id)
    with session_scope() as x:
        assert x.get(Task, t.id).status == "REJECTED"
        assert x.get(AgentRequest, card.id).response == {"by": "expiry"}
