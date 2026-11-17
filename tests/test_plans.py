"""The owner approves a product plan before any spec is written: what the build is for, who it serves, what it does
and leaves out, how to tell it worked, and what every line rests on. The spec is written from the approved version."""
import pytest
from sqlalchemy import select

from justshipit.contracts.handoff import SpecStatus
from justshipit.contracts.plan import PlanDraftOut, PlanLineOut, PlanStatus, SuccessOut
from justshipit.contracts.requests import (
    AgentRequestDraft, HumanDecision, HumanResponse, PolicyMode, RequestType, RiskLevel,
)
from justshipit.db.models import AgentRequest, DevJob, Evidence, Job, Plan, Spec, Task
from justshipit.engineering import plans, specs
from justshipit.i18n import msg
from justshipit.services import app_settings, matters, policy, registry, requests
from test_spec_kit import _draft as spec_draft
from test_spec_kit import _repo
from ask_kit import contract, by_project_manager


@pytest.fixture(autouse=True)
def _english_owner(monkeypatch):
    monkeypatch.setattr(app_settings, "language", lambda: "en")


NEED = {"title": "Bean stock alerts", "problem": "Cafés run out of beans before they reorder",
        "why_software": "They check stock every morning", "task_ids": []}


def _out(**kw) -> PlanDraftOut:
    line = PlanLineOut(text="Cafés reorder beans by hand", basis="assumed", refs=[])
    return PlanDraftOut(**{
        "title": "Bean stock alerts", "goal": [line], "users": [line], "scope": [line], "not_doing": [line],
        "success": [SuccessOut(text="Three cafés use it weekly", basis="assumed", refs=[], check="Count weekly logins")],
        "evidence": [line], "risks": [line], **kw})


def _written(s, project, monkeypatch, out: PlanDraftOut | None = None) -> tuple[Plan, list[str]]:
    prompts: list[str] = []
    monkeypatch.setattr(plans, "run_agent",
                        lambda agent, purpose, prompt, model, **kw: prompts.append(prompt) or (out or _out()))
    plan = plans.begin(s, project, dict(NEED))
    s.commit()
    plans.write_plan(project.id, plan.id)
    s.expire_all()
    return s.get(Plan, plan.id), prompts


def _build_ask() -> AgentRequestDraft:
    return AgentRequestDraft(type=RequestType.DEVELOPMENT, title="Build: Bean stock alerts", summary="s", rationale="r",
                             options=[], recommended_option=None, action_kind="dev.implementation_start",
                             estimated_cost_usd=None, risk_level=RiskLevel.MEDIUM, context=contract())


def _ask(s, project, need: dict | None = None):
    return requests.create(s, _build_ask(), from_agent="product", project_id=project.id,
                           action_params={"need": dict(need or NEED)}, origin=by_project_manager(s, project.id))


def _text(plan: Plan) -> str:
    return registry.read_at(f"{plan.registry_path}/{plans.FILE}", plan.draft_commit_sha)


def test_the_plan_is_written_first_and_its_card_holds_up_the_work(s, project, monkeypatch):
    plan, prompts = _written(s, project, monkeypatch)

    assert plan.status == PlanStatus.IN_REVIEW.value and plan.plan_key.startswith("PLAN-")
    assert s.scalar(select(Spec.id).where(Spec.project_id == project.id)) is None, "no spec before the plan is approved"
    card = s.get(AgentRequest, plan.approval_request_id)
    assert (card.action_kind, card.type, card.status, card.blocking) == ("dev.plan_approve", "APPROVAL", "PENDING", False)
    assert card.action_params["commit_sha"] == plan.draft_commit_sha
    verdicts = {o["label"]: o["verdict"] for o in card.action_params["card"]["options"]}
    assert verdicts == {msg("plan.option.approve", "en"): "approve", msg("plan.option.changes", "en"): "reject",
                        msg("plan.option.hold", "en"): "defer", msg("plan.option.decline", "en"): "reject"}
    text = _text(plan)
    for _, heading in plans.SECTIONS:
        assert f"## {msg(heading, 'en')}" in text
    assert "Count weekly logins" in text and "Cafés run out of beans" in prompts[0]


def test_every_line_says_what_it_rests_on_never_firmer_than_the_ledger(s, project, monkeypatch):
    checked = Evidence(project_id=project.id, agent="research", claim="3 cafés use 2kg a day", kind="verified_fact",
                       verification_status="supported")
    task = Task(project_id=project.id, key="t", title="Café interviews", owner="research", objective="o",
                acceptance_criteria=[], status="DONE")
    s.add_all([checked, task])
    s.flush()
    lines = [PlanLineOut(text="Cafés use 2kg a day", basis="verified", refs=[f"EV-{checked.id}"]),
             PlanLineOut(text="Owners reorder on Fridays", basis="verified", refs=[f"T-{task.id}"]),
             PlanLineOut(text="Owners want a text message", basis="sourced", refs=[]),
             PlanLineOut(text="A weekly digest is enough", basis="assumed", refs=[f"EV-{checked.id}"])]
    plan, _ = _written(s, project, monkeypatch, _out(evidence=lines))

    text = _text(plan)
    def said(line: str) -> str:
        return next(x for x in text.splitlines() if x.startswith(f"- {line}"))
    assert f"**{msg('evidence.grade.firm', 'en')}** (EV-{checked.id})" in said("Cafés use 2kg a day")
    assert f"**{msg('evidence.grade.sourced', 'en')}** (T-{task.id})" in said("Owners reorder on Fridays")
    assert f"**{msg('evidence.grade.assumed', 'en')}**" in said("Owners want a text message")
    assert f"**{msg('evidence.grade.assumed', 'en')}**" in said("A weekly digest is enough")
    card = s.get(AgentRequest, plan.approval_request_id)
    assert f"{msg('evidence.grade.firm', 'en')} 1" in card.summary


def test_writing_the_plan_again_changes_nothing(s, project, monkeypatch):
    plan, prompts = _written(s, project, monkeypatch)
    plans.write_plan(project.id, plan.id)
    s.expire_all()
    assert len(prompts) == 1
    assert s.get(Plan, plan.id).draft_commit_sha == plan.draft_commit_sha
    assert len(list(s.scalars(select(AgentRequest).where(AgentRequest.action_kind == "dev.plan_approve",
                                                         AgentRequest.project_id == project.id)))) == 1
    assert len(list(s.scalars(select(Job).where(Job.idempotency_key == f"write_plan:{plan.id}")))) == 1


def test_the_plan_card_reads_the_plan_at_its_commit_without_its_key(s, project, monkeypatch):
    plan, _ = _written(s, project, monkeypatch)
    shown = plans.card_plan(s, s.get(AgentRequest, plan.approval_request_id))
    assert "Cafés reorder beans by hand" in shown["text"] and plan.plan_key not in shown["text"]


# ---------------------------------------------------------------- deciding the plan, and the spec written from it

def _decide(s, card_id: int, decision: HumanDecision, option: str | None = None, note: str | None = None):
    req = requests.respond(s, card_id, HumanResponse(decision=decision, note=note,
                                                     chosen_option=msg(f"plan.option.{option}", "en") if option else None))
    s.commit()
    return req


def _job(s, key: str) -> Job | None:
    return s.scalar(select(Job).where(Job.idempotency_key == key))


def _spec_from(s, project, monkeypatch, card_id: int) -> tuple[Spec, list[str]]:
    prompts: list[str] = []
    monkeypatch.setattr(specs, "run_agent", lambda agent, purpose, prompt, model, **kw: prompts.append(prompt)
                        or spec_draft(_repo()))
    spec_id = specs.write_spec(project.id, card_id)
    s.expire_all()
    return s.get(Spec, spec_id), prompts


def test_approving_the_plan_writes_the_spec_from_that_version_and_pins_it(s, project, monkeypatch):
    plan, _ = _written(s, project, monkeypatch)
    card_id = plan.approval_request_id
    _decide(s, card_id, HumanDecision.APPROVE, "approve")

    plan = s.get(Plan, plan.id)
    assert plan.status == PlanStatus.APPROVED.value and plan.approved_commit_sha == plan.draft_commit_sha
    assert _job(s, f"write_spec:{card_id}") is not None
    spec, prompts = _spec_from(s, project, monkeypatch, card_id)
    assert (spec.plan_id, spec.plan_commit_sha, spec.request_id) == (plan.id, plan.approved_commit_sha, card_id)
    assert "The product plan the owner approved" in prompts[0] and "Cafés reorder beans by hand" in prompts[0]


def test_request_changes_rewrites_the_plan_under_its_key_and_asks_again(s, project, monkeypatch):
    plan, prompts = _written(s, project, monkeypatch)
    first, was = plan.approval_request_id, plan.draft_commit_sha
    with pytest.raises(requests.ResponseRejected):
        requests.respond(s, first, HumanResponse(decision=HumanDecision.REJECT,
                                                 chosen_option=msg("plan.option.changes", "en")))
    s.rollback()
    _decide(s, first, HumanDecision.REJECT, "changes", note="Leave out the text messages")

    assert s.get(Plan, plan.id).status == PlanStatus.DRAFT.value and _job(s, f"write_spec:{first}") is None
    job = _job(s, f"redraft_plan:{first}")
    monkeypatch.setattr(plans, "run_agent", lambda agent, purpose, prompt, model, **kw: prompts.append(prompt)
                        or _out(title="Bean stock alerts without texts"))
    plans.redraft_plan(**{k: job.payload[k] for k in ("project_id", "plan_id", "request_id", "commit", "note")})
    s.expire_all()

    again = s.get(Plan, plan.id)
    assert (again.plan_key, again.status) == (plan.plan_key, PlanStatus.IN_REVIEW.value)
    assert again.draft_commit_sha != was and registry.read_at(f"{again.registry_path}/{plans.FILE}", was)
    card = s.get(AgentRequest, again.approval_request_id)
    assert card.id != first and card.status == "PENDING" and card.action_params["commit_sha"] == again.draft_commit_sha
    assert card.action_params["redraft_of"] == {"request": first, "commit": was}
    assert s.get(AgentRequest, first).response["superseded_by"] == f"REQ-{card.id}"
    assert "Leave out the text messages" in prompts[1] and "Your last version of this plan" in prompts[1]


def test_a_plan_card_the_policy_approves_as_it_is_filed_starts_its_spec(s, project, monkeypatch):
    real = policy.decide

    def decide(request_type, action_kind, *a, **kw):
        if action_kind == "dev.plan_approve":
            return policy.Verdict(PolicyMode.AUTO, {"id": "r-1", "action": action_kind, "decider": "project_manager"}, ())
        return real(request_type, action_kind, *a, **kw)
    monkeypatch.setattr(policy, "decide", decide)
    plan, _ = _written(s, project, monkeypatch)

    card = s.get(AgentRequest, plan.approval_request_id)
    assert card.status == "AUTO_APPROVED" and plan.status == PlanStatus.APPROVED.value
    assert _job(s, f"write_spec:{card.id}") is not None and not plans.moved_on(s, card)


@pytest.mark.parametrize("decision,option,plan_status", [(HumanDecision.DEFER, "hold", PlanStatus.IN_REVIEW),
                                                         (HumanDecision.REJECT, "decline", PlanStatus.REJECTED)])
def test_hold_and_decline_start_nothing(s, project, monkeypatch, decision, option, plan_status):
    plan, _ = _written(s, project, monkeypatch)
    card_id = plan.approval_request_id
    _decide(s, card_id, decision, option)

    assert s.get(Plan, plan.id).status == plan_status.value
    assert _job(s, f"write_spec:{card_id}") is None and _job(s, f"redraft_plan:{card_id}") is None
    assert requests.reopen_refusal(s, s.get(AgentRequest, card_id)) is None, "the owner can reopen it"


def test_a_held_plan_is_asked_again_when_the_build_is_asked_for_again(s, project, monkeypatch):
    plan, _ = _written(s, project, monkeypatch)
    held = plan.approval_request_id
    _decide(s, held, HumanDecision.DEFER, "hold")
    _ask(s, project)
    s.commit()

    again = s.get(Plan, plan.id)
    card = s.get(AgentRequest, again.approval_request_id)
    assert card.id != held and card.status == "PENDING" and card.action_params["commit_sha"] == plan.draft_commit_sha
    assert s.get(AgentRequest, held).status == "CANCELLED"
    assert len(list(s.scalars(select(Plan).where(Plan.project_id == project.id)))) == 1


def test_asking_for_the_build_while_its_plan_is_written_joins_that_plan(s, project, monkeypatch):
    first = _ask(s, project)
    again = _ask(s, project, {**NEED, "title": "Weekly digest"})
    s.commit()
    assert again.id == first.id and first.need["extra_needs"][0]["title"] == "Weekly digest"
    assert len(list(s.scalars(select(Job).where(Job.kind == "write_plan",
                                                Job.payload["plan_id"].as_integer() == first.id)))) == 1


def test_revising_the_plan_sends_its_unapproved_spec_back_and_rewrites_it_from_the_new_version(s, project, monkeypatch):
    plan, _ = _written(s, project, monkeypatch)
    _decide(s, plan.approval_request_id, HumanDecision.APPROVE, "approve")
    spec, _ = _spec_from(s, project, monkeypatch, plan.approval_request_id)
    spec_card, key, path = spec.approval_request_id, spec.spec_key, spec.registry_path

    plans.revise(s, plan.id, "Start with one café", "en")
    s.commit()
    assert s.get(Spec, spec.id).status == "DRAFT" and s.get(AgentRequest, spec_card).status == "CANCELLED"
    job = _job(s, f"redraft_plan:{plan.approval_request_id}")
    monkeypatch.setattr(plans, "run_agent", lambda *a, **kw: _out(title="Bean stock alerts for one café"))
    plans.redraft_plan(**{k: job.payload[k] for k in ("project_id", "plan_id", "request_id", "commit", "note")})
    s.expire_all()
    new_card = s.get(Plan, plan.id).approval_request_id
    _decide(s, new_card, HumanDecision.APPROVE, "approve")

    again, _ = _spec_from(s, project, monkeypatch, new_card)
    plan = s.get(Plan, plan.id)
    assert (again.id, again.spec_key, again.registry_path) == (spec.id, key, path)
    assert again.plan_commit_sha == plan.approved_commit_sha and again.request_id == new_card
    assert again.status == "IN_REVIEW" and again.approval_request_id not in (None, spec_card)


def test_a_plan_whose_spec_is_approved_is_frozen(s, project, monkeypatch):
    plan, _ = _written(s, project, monkeypatch)
    _decide(s, plan.approval_request_id, HumanDecision.APPROVE, "approve")
    spec, _ = _spec_from(s, project, monkeypatch, plan.approval_request_id)
    requests.respond(s, spec.approval_request_id, HumanResponse(decision=HumanDecision.APPROVE, chosen_option="Approve"))
    s.commit()
    specs.on_spec_approved(spec.id, spec.approval_request_id)
    s.expire_all()

    with pytest.raises(requests.ResponseRejected) as refused:
        plans.revise(s, plan.id, "Make it bigger", "en")
    assert refused.value.words("en") == msg("plan.revise.frozen", "en")
    assert set(plans.REVISION) == {None, *(st.value for st in SpecStatus)}, "every spec status says what a revision does"


def test_an_open_build_ask_from_before_plans_still_writes_its_spec_with_no_plan(s, project, monkeypatch):
    old = AgentRequest(project_id=project.id, from_agent="product", type="DEVELOPMENT", title="Build: Bean stock alerts",
                       summary="s", rationale="r", status="PENDING", policy_mode="approve",
                       action_kind="dev.implementation_start", options=["Approve and write the spec", "Hold", "Decline"],
                       action_params={"need": {"title": "Bean stock alerts", "problem": "Cafés run out"}})
    s.add(old)
    s.commit()
    _decide(s, old.id, HumanDecision.APPROVE)

    assert _job(s, f"write_spec:{old.id}") is not None
    spec, _ = _spec_from(s, project, monkeypatch, old.id)
    assert spec.plan_id is None and spec.plan_commit_sha is None
    assert s.scalar(select(Plan.id).where(Plan.project_id == project.id)) is None


def _no_dev_ask_card(s, project) -> None:
    assert s.scalar(select(AgentRequest.id).where(AgentRequest.project_id == project.id,
                                                  AgentRequest.action_kind == "dev.implementation_start",
                                                  AgentRequest.status != "CANCELLED")) is None
    assert s.scalar(select(Job.id).where(Job.kind == "write_spec",
                                         Job.payload["project_id"].as_integer() == project.id)) is None


@pytest.mark.parametrize("dedupe", [True, False])
def test_an_agents_development_ask_starts_a_plan_never_a_spec(s, project, dedupe):
    draft = _build_ask().model_copy(update={"action_kind": "dev.write_code", "title": "Bean stock alerts"})
    ids = requests.from_agent_drafts(s, [draft], agent="project_manager", project_id=project.id,
                                     origin=by_project_manager(s, project.id)) if dedupe else [
        requests.create(s, draft, from_agent="project_manager", project_id=project.id, dedupe=False,
                        origin=by_project_manager(s, project.id)).id]
    s.commit()

    plan = s.scalar(select(Plan).where(Plan.project_id == project.id))
    assert plan is not None and plan.status == PlanStatus.DRAFT.value and plan.need["title"] == "Bean stock alerts"
    assert _job(s, f"write_plan:{plan.id}") is not None
    if dedupe:
        assert ids == [], "a plan is not a card yet"
    _no_dev_ask_card(s, project)


def test_a_plan_replaces_a_held_build_ask_from_before_plans(s, project):
    old = AgentRequest(project_id=project.id, from_agent="product", type="DEVELOPMENT", title="Build: Bean stock alerts",
                       summary="s", rationale="r", status="DEFERRED", policy_mode="approve",
                       action_kind="dev.implementation_start",
                       action_params={"matter": f"dev:{matters.product_scope(s, project.id)[0]}",
                                      "need": {"title": "Bean stock alerts"}})
    s.add(old)
    s.commit()

    plan = _ask(s, project)
    s.commit()
    assert isinstance(plan, Plan)
    old = s.get(AgentRequest, old.id)
    assert old.status == "CANCELLED" and old.response["superseded_by"] == plan.plan_key
    assert requests.reopen_refusal(s, old) is not None, "the held card cannot come back beside the plan"


def test_approving_the_spec_freezes_its_plan_before_the_build_job_runs(s, project, monkeypatch):
    plan, _ = _written(s, project, monkeypatch)
    _decide(s, plan.approval_request_id, HumanDecision.APPROVE, "approve")
    spec, _ = _spec_from(s, project, monkeypatch, plan.approval_request_id)
    requests.respond(s, spec.approval_request_id, HumanResponse(decision=HumanDecision.APPROVE, chosen_option="Approve"))
    s.commit()

    assert s.get(Spec, spec.id).status == "IN_REVIEW", "the spec_approved job has not run"
    with pytest.raises(requests.ResponseRejected) as refused:
        plans.revise(s, plan.id, "Make it bigger", "en")
    assert refused.value.words("en") == msg("plan.revise.frozen", "en")


def test_the_build_is_refused_when_the_spec_no_longer_follows_an_approved_plan(s, project, monkeypatch):
    plan, _ = _written(s, project, monkeypatch)
    _decide(s, plan.approval_request_id, HumanDecision.APPROVE, "approve")
    spec, _ = _spec_from(s, project, monkeypatch, plan.approval_request_id)
    requests.respond(s, spec.approval_request_id, HumanResponse(decision=HumanDecision.APPROVE, chosen_option="Approve"))
    s.get(Plan, plan.id).status = PlanStatus.DRAFT.value
    s.commit()

    assert specs.on_spec_approved(spec.id, spec.approval_request_id) is None
    s.expire_all()
    assert s.get(Spec, spec.id).approved_commit_sha is None
    assert s.scalar(select(DevJob.id).where(DevJob.spec_id == spec.id)) is None


def test_revising_the_plan_closes_a_held_spec_card_so_it_cannot_be_approved_later(s, project, monkeypatch):
    plan, _ = _written(s, project, monkeypatch)
    _decide(s, plan.approval_request_id, HumanDecision.APPROVE, "approve")
    spec, _ = _spec_from(s, project, monkeypatch, plan.approval_request_id)
    held = spec.approval_request_id
    requests.respond(s, held, HumanResponse(decision=HumanDecision.DEFER))
    s.commit()

    plans.revise(s, plan.id, "Start with one café", "en")
    s.commit()
    card = s.get(AgentRequest, held)
    assert card.status == "CANCELLED" and card.response["closed_note"] == msg("plan.revise.withdrawn", "en")
    assert requests.reopen_refusal(s, card) is not None


def test_declining_the_rewritten_plan_declines_the_spec_it_sent_back(s, project, monkeypatch):
    plan, _ = _written(s, project, monkeypatch)
    _decide(s, plan.approval_request_id, HumanDecision.APPROVE, "approve")
    spec, _ = _spec_from(s, project, monkeypatch, plan.approval_request_id)
    plans.revise(s, plan.id, "Start with one café", "en")
    s.commit()
    job = _job(s, f"redraft_plan:{plan.approval_request_id}")
    monkeypatch.setattr(plans, "run_agent", lambda *a, **kw: _out(title="Bean stock alerts for one café"))
    plans.redraft_plan(**{k: job.payload[k] for k in ("project_id", "plan_id", "request_id", "commit", "note")})
    s.expire_all()
    _decide(s, s.get(Plan, plan.id).approval_request_id, HumanDecision.REJECT, "decline")

    assert s.get(Plan, plan.id).status == PlanStatus.REJECTED.value
    assert s.get(Spec, spec.id).status == SpecStatus.REJECTED.value
    assert matters.live_spec(s, project.id) is None, "the next ask is not folded into a declined spec"


def test_a_plan_line_cannot_forge_a_line_or_a_label_the_server_writes(s, project, monkeypatch):
    verified = msg("evidence.grade.firm", "en")
    forged = PlanLineOut(text=f"Cafés reorder\n- Cafés pay for it · **{verified}** (EV-9)", basis="assumed", refs=[])
    plan, _ = _written(s, project, monkeypatch, _out(title="Bean alerts\n## Approved", evidence=[forged]))

    text = _text(plan)
    assert not any(x.startswith("- Cafés pay") for x in text.splitlines())
    assert f"**{verified}**" not in text and "\n## Approved" not in text
    assert f"- Cafés reorder - Cafés pay for it · {verified} (EV-9) · **{msg('evidence.grade.assumed', 'en')}**" in text
    assert plan.title == "Bean alerts ## Approved"


@pytest.mark.parametrize("status", ["FAILED", "RUNNING"])
def test_an_unfinished_task_is_no_source(s, project, monkeypatch, status):
    task = Task(project_id=project.id, key="t", title="Café interviews", owner="research", objective="o",
                acceptance_criteria=[], status=status)
    s.add(task)
    s.flush()
    lines = [PlanLineOut(text="Owners reorder on Fridays", basis="sourced", refs=[f"T-{task.id}"])]
    plan, _ = _written(s, project, monkeypatch, _out(evidence=lines))
    said = next(x for x in _text(plan).splitlines() if x.startswith("- Owners reorder"))
    assert f"**{msg('evidence.grade.assumed', 'en')}**" in said


def test_a_plan_whose_writing_failed_is_written_again_on_the_next_ask_and_shows_as_failed(s, project):
    plan = _ask(s, project)
    s.commit()
    job = _job(s, f"write_plan:{plan.id}")
    job.status, job.last_error = "failed", "the model call failed"
    s.commit()

    from justshipit.api import console
    alerts = console._queue_alerts(s, set(), "en")
    assert any(a["kind"] == "job_failed" and a["job_id"] == job.id and a["retryable"] for a in alerts)

    _ask(s, project, {**NEED, "title": "Weekly digest"})
    s.commit()
    again = _job(s, f"job_retry:{job.id}")
    assert again is not None and again.kind == "write_plan" and again.payload == job.payload
    assert not any(a.get("job_id") == job.id for a in console._queue_alerts(s, set(), "en")), "the retry answers it"
    _ask(s, project, {**NEED, "title": "Monthly digest"})
    s.commit()
    assert len(list(s.scalars(select(Job).where(Job.kind == "write_plan",
                                                Job.payload["plan_id"].as_integer() == plan.id)))) == 2


def test_the_project_manager_reads_that_a_plan_is_under_way(s, project):
    from justshipit.orchestration import context
    p = s.get(type(project), project.id)
    p.plan = {"build": {"title": "Bean stock alerts", "request_id": None, "task_ids": []}}
    plan = _ask(s, project)
    p.plan = {"build": {**p.plan["build"], "plan_id": plan.id}}
    s.flush()
    assert "product plan is under way" in context.build_state(s, p)


# ---------------------------------------------------------------- what the cards show

@pytest.fixture
def api(api_token):
    from fastapi.testclient import TestClient
    from justshipit.api.app import app
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def test_the_spec_card_links_the_plan_it_follows_and_says_when_the_plan_changed(s, project, monkeypatch, api):
    plan, _ = _written(s, project, monkeypatch)
    approved, first = plan.approval_request_id, plan.draft_commit_sha
    shown = api.get(f"/api/requests/{approved}").json()
    assert "Cafés reorder beans by hand" in shown["plan_doc"]["text"] and shown["plan"] is None
    _decide(s, approved, HumanDecision.APPROVE, "approve")
    spec, _ = _spec_from(s, project, monkeypatch, approved)
    spec_card = spec.approval_request_id

    assert api.get(f"/api/requests/{spec_card}").json()["plan"] == {
        "request_id": approved, "plan_key": plan.plan_key, "commit_sha": first, "title": plan.title,
        "stale": False, "legacy": False}

    assert api.post(f"/api/plans/{plan.id}/revise", json={"note": "Start with one café"}).json()["status"] == "DRAFT"
    s.expire_all()
    job = _job(s, f"redraft_plan:{approved}")
    monkeypatch.setattr(plans, "run_agent", lambda *a, **kw: _out(title="Bean stock alerts for one café"))
    plans.redraft_plan(**{k: job.payload[k] for k in ("project_id", "plan_id", "request_id", "commit", "note")})
    got = api.get(f"/api/requests/{spec_card}").json()["plan"]
    assert got["stale"] is True and got["request_id"] == approved and got["commit_sha"] == first


def test_a_spec_with_no_plan_behind_it_says_so(s, project, monkeypatch, api):
    old = AgentRequest(project_id=project.id, from_agent="product", type="DEVELOPMENT", title="Build: Bean stock alerts",
                       summary="s", rationale="r", status="APPROVED", policy_mode="auto",
                       action_kind="dev.implementation_start", action_params={"need": {"title": "Bean stock alerts"}})
    s.add(old)
    s.commit()
    spec, _ = _spec_from(s, project, monkeypatch, old.id)
    got = api.get(f"/api/requests/{spec.approval_request_id}").json()
    assert got["plan"]["legacy"] is True and got["plan"]["request_id"] is None and got["plan_doc"] is None


def test_an_approved_plan_whose_spec_is_approved_is_refused_by_the_route(s, project, monkeypatch, api):
    plan, _ = _written(s, project, monkeypatch)
    _decide(s, plan.approval_request_id, HumanDecision.APPROVE, "approve")
    spec, _ = _spec_from(s, project, monkeypatch, plan.approval_request_id)
    requests.respond(s, spec.approval_request_id, HumanResponse(decision=HumanDecision.APPROVE, chosen_option="Approve"))
    s.commit()
    specs.on_spec_approved(spec.id, spec.approval_request_id)

    refused = api.post(f"/api/plans/{plan.id}/revise", json={"note": "Make it bigger"})
    assert refused.status_code == 409 and refused.json()["detail"] == msg("plan.revise.frozen", "en")
    assert api.post("/api/plans/999999999/revise", json={"note": "x"}).status_code == 404


def test_a_plan_of_an_archived_project_cannot_be_revised(s, project, monkeypatch, api):
    plan, _ = _written(s, project, monkeypatch)
    _decide(s, plan.approval_request_id, HumanDecision.APPROVE, "approve")
    s.get(type(project), project.id).status = "ARCHIVED"
    s.commit()

    refused = api.post(f"/api/plans/{plan.id}/revise", json={"note": "Start with one café"})
    assert refused.status_code == 409
    assert refused.json()["detail"] == msg("work.refuse.archived_project", "en", ref=f"P-{project.id}")
    s.expire_all()
    assert s.get(Plan, plan.id).status == PlanStatus.APPROVED.value
