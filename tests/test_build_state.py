"""What the CEO and the engineers know about the build (persona round 3, 반품 예측): the owner's note on approving the
spec ("일곱 항목으로 갑니다… 사유 먼저") reached no one, so the engineer wrote "clarifications.md는 없어요" and built five
fields. The CEO's review and realign saw no spec state and planned three tasks to rewrite or fill in a spec the
engineers had already started from (T-95, T-98, T-102)."""
from justshipit.contracts.handoff import Handoff
from justshipit.contracts.planning import RealignOut
from justshipit.contracts.requests import AgentRequestDraft, HumanDecision, HumanResponse, RequestType, RiskLevel
from justshipit.db.models import AgentRequest, Spec
from justshipit.db.session import session_scope
from justshipit.engineering import specs
from justshipit.orchestration import realign
from justshipit.services import registry, requests
from ask_kit import contract, by_project_manager

NOTE = "승인합니다. 주문번호·주문일 추가는 앞 결정대로 일곱 항목으로 갑니다. 점수보다 사유와 할 일을 먼저 보여 주세요."


def _spec(s, project, *, approved: bool) -> Spec:
    path = f"projects/p{project.id}/specs/ret-0001"
    sha = registry.write_files({f"{path}/prd.md": "# Return alerts\nFive fields per order.\n"}, "spec draft")
    h = Handoff(spec_id="RET-0001", project="Test", title="Return alerts", source={"path": path, "commit_sha": sha},
                target={"repository": f"return-alerts-{project.id}"})
    spec = Spec(spec_key=f"RET-{project.id}", project_id=project.id, title="Return alerts", registry_path=path,
                draft_commit_sha=sha, approved_commit_sha=sha if approved else None, handoff=h.model_dump(mode="json"),
                status="READY_FOR_DEV" if approved else "IN_REVIEW")
    s.add(spec)
    s.flush()
    return spec


def _clarifications(spec: Spec) -> str:
    path = f"{spec.registry_path}/clarifications.md"
    return registry.read(path) if (registry.root() / path).exists() else ""


def test_the_owners_note_on_approving_the_spec_reaches_the_engineers(s, project):
    spec = _spec(s, project, approved=False)
    card = requests.create(s, AgentRequestDraft(
        type=RequestType.APPROVAL, title="Approve the spec", summary="s", rationale="r", options=[],
        recommended_option=None, action_kind="dev.spec_approve", estimated_cost_usd=None, risk_level=RiskLevel.MEDIUM,
        context=contract()), from_agent="product", project_id=project.id,
        action_params={"spec_id": spec.id, "commit_sha": spec.draft_commit_sha}, system=True, dedupe=False,
        origin=by_project_manager(s, project.id))
    spec.approval_request_id = card.id
    requests.respond(s, card.id, HumanResponse(decision=HumanDecision.APPROVE, note=NOTE))
    s.commit()

    specs.on_spec_approved(spec.id, card.id)
    assert NOTE in _clarifications(spec)
    with session_scope() as s2:
        specs.add_clarification(s2.get(Spec, spec.id), f"Owner, approving the spec ({requests.display_id(card.id)})",
                                NOTE)
    assert _clarifications(spec).count(NOTE) == 1


def test_a_realign_during_development_sees_the_approved_spec_and_routes_the_change_to_its_clarifications(
        s, project, monkeypatch):
    project.status = "RUNNING"
    project.plan = {"build": {"title": "Return alerts", "problem": "p", "why_software": "w", "task_ids": [],
                              "request_id": 1}}
    spec = _spec(s, project, approved=True)
    trigger = AgentRequest(project_id=project.id, from_agent="risk", type="QUESTION", title="Add order number?",
                           summary="s", rationale="r", status="ANSWERED", policy_mode="approve",
                           response={"answer": "Seven fields: add order number and order date"})
    s.add(trigger)
    s.commit()
    prompts = []

    def project_manager(agent, purpose, prompt, model, **kw):
        prompts.append(prompt)
        return RealignOut(assessment="a", goal_interpretation="g", out_of_scope_additions=[], cancel_task_ids=[],
                          revise_tasks=[], new_tasks=[], withdraw_requests=[],
                          spec_clarification="Upload takes seven fields: order number and order date are required.")
    monkeypatch.setattr(realign, "run_agent", project_manager)
    rec = realign.realign(project.id, trigger.id)

    assert f"Spec {spec.spec_key} is approved and in development" in prompts[0]
    assert "Don't plan tasks that write, rewrite or fill in this spec" in prompts[0]
    assert rec["added"] == []
    assert "order number and order date are required" in _clarifications(spec)


def test_the_build_state_says_a_merged_spec_is_merged(s, project):
    from justshipit.orchestration import context as ctx
    project.plan = {"build": {"title": "Return alerts", "problem": "p", "why_software": "w", "task_ids": [],
                              "request_id": 1}}
    spec = _spec(s, project, approved=True)
    spec.status = "MERGED"
    s.flush()
    assert ctx.build_state(s, project) == f"## The build: Return alerts\nSpec {spec.spec_key} is merged."
