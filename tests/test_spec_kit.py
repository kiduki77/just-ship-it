"""The spec the engineering team gets says what the founder wants, in the shape GitHub Spec Kit proved: prioritized
user stories with Given/When/Then and the empty day, at most three questions the owner answers before approving, a
per-product constitution, and an acceptance check that runs the P1 scenarios and names every gap on the merge card.
Replayed from persona round 3: Maya's "where will it be hosted" stayed an open question in the spec and never became a
decision; Alex's imported product grew beyond what he asked; the report said more was built than the code did."""
import uuid

import pytest
import yaml
from sqlalchemy import select
from story_kit import JOURNEYS

from justshipit.contracts.handoff import (
    AcceptanceOut, ClarifyOptionOut, ClarifyOut, CoreFlowOut, CriterionResultOut, Handoff, PrincipleChangeOut,
    PrincipleOut, RequirementOut, ScenarioOut, SpecDraftOut, StoryCheckOut, UserStoryOut,
)
from justshipit.contracts.requests import HumanDecision, HumanResponse
from justshipit.db.models import AgentRequest, DevJob, Job, Spec
from justshipit.engineering import constitution, specs, status, stories, worker
from justshipit.i18n import msg
from justshipit.services import app_settings, registry, requests
from ask_kit import contract, by_project_manager


@pytest.fixture(autouse=True)
def _english_owner(monkeypatch):
    monkeypatch.setattr(app_settings, "language", lambda: "en")


ORDER = UserStoryOut(
    title="Take today's orders", priority="P1", story="A shop owner sees today's orders in one list each morning.",
    why_priority="Without it nothing ships", independent_test="Load a day's order file and read the list",
    scenarios=[ScenarioOut(given="orders.csv with 12 rows (name, dish, qty)", when="she opens the app",
                           then="12 orders, grouped by dish")],
    empty_case=ScenarioOut(given="an orders.csv with only its header row", when="she opens the app",
                           then="'No orders yet today' and the upload button"))
EXPORT = UserStoryOut(
    title="Export the list", priority="P2", story="She prints the list for the kitchen.", why_priority="Saves copying",
    independent_test="Export and open the file",
    scenarios=[ScenarioOut(given="12 orders", when="she presses Print", then="one page per dish")],
    empty_case=ScenarioOut(given="no orders", when="she presses Print", then="a page that says no orders"))
HOSTING = ClarifyOut(question="Where will the app live?", why_it_matters="Who can open it and what it costs.",
                     options=[ClarifyOptionOut(answer="On this computer", means="Only you open it; free."),
                              ClarifyOptionOut(answer="Cloudflare Pages", means="Anyone with the link; free.")],
                     why_recommended="Only you use it for now.")
ACCESS = ClarifyOut(question="Who can see the orders?", why_it_matters="Decides whether it needs a sign-in.",
                    options=[ClarifyOptionOut(answer="Only me", means="No sign-in."),
                             ClarifyOptionOut(answer="My staff too", means="A shared code.")],
                    why_recommended="The plan says you run the shop alone.")


def _draft(repo: str, **kw) -> SpecDraftOut:
    return SpecDraftOut(**{
        "title": "Banchan orders", "prd_markdown": "The problem. Hosting: [NEEDS CLARIFICATION Q1: Where will it live?]",
        "design_notes_markdown": "d", "user_stories": [EXPORT, ORDER], "owner_data": "orders.csv: name, dish, qty",
        "assumptions": ["Orders stay on her computer"],
        "requirements": [RequirementOut(id="REQ-1", statement="Show today's orders to [NEEDS CLARIFICATION Q2: who?]",
                                        priority="must", rationale="r")],
        "acceptance_criteria": [], "owner_trace": [], "design_brief": None, "suggested_target_repository": repo,
        "suggested_test_command": "npm test", "suggested_test_image": "node:22",
        "journeys": JOURNEYS, **kw})


def _write(s, project, monkeypatch, out: SpecDraftOut) -> tuple[Spec, list[str]]:
    prompts: list[str] = []
    monkeypatch.setattr(specs, "run_agent", lambda agent, purpose, prompt, model, **kw: prompts.append(prompt) or out)
    req = AgentRequest(project_id=project.id, from_agent="product", type="DEVELOPMENT", title="Banchan orders",
                       summary="s", rationale="r", status="APPROVED", policy_mode="auto",
                       action_kind="dev.implementation_start", action_params={"need": {"title": "orders",
                                                                                      "problem": "Orders get lost"}})
    s.add(req)
    s.commit()
    spec = s.get(Spec, specs.write_spec(project.id, req.id))
    s.refresh(spec)
    return spec, prompts


def _approve(s, spec: Spec) -> Spec:
    card = s.get(AgentRequest, s.get(Spec, spec.id).approval_request_id)
    requests.respond(s, card.id, HumanResponse(decision=HumanDecision.APPROVE, chosen_option="Approve"))
    s.commit()
    specs.on_spec_approved(spec.id, card.id)
    s.expire_all()
    return s.get(Spec, spec.id)


def _repo() -> str:
    return f"banchan-{uuid.uuid4().hex[:8]}"


def _questions(s, spec: Spec) -> list[AgentRequest]:
    return list(s.scalars(select(AgentRequest).where(AgentRequest.action_params["spec_id"].as_integer() == spec.id,
                                                     AgentRequest.type == "QUESTION").order_by(AgentRequest.id)))


def _answer(s, card: AgentRequest, **kw) -> None:
    requests.respond(s, card.id, HumanResponse(**kw))
    s.commit()
    assert s.scalar(select(Job.id).where(Job.kind == "spec_questions", Job.idempotency_key == f"spec_questions:{card.id}"))
    specs.on_questions_answered(card.action_params["spec_id"])
    s.expire_all()


def _at(spec: Spec, name: str) -> str:
    return registry.read_at(f"{spec.registry_path}/{name}", spec.draft_commit_sha)


def test_the_spec_leads_with_its_stories_p1_first_each_with_the_day_nothing_has_happened(s, project, monkeypatch):
    spec, _ = _write(s, project, monkeypatch, _draft(_repo(), prd_markdown="The problem.",
                                                     requirements=[RequirementOut(id="REQ-1", statement="List orders",
                                                                                  priority="must", rationale="r")]))
    card = s.get(AgentRequest, spec.approval_request_id)

    assert card.action_params["stories"] == [
        {"id": "US1", "priority": "P1", "says": ORDER.story}, {"id": "US2", "priority": "P2", "says": EXPORT.story}]
    prd = _at(spec, "prd.md")
    assert prd.index("### US1 Take today's orders (P1)") < prd.index("### US2 Export the list (P2)") < prd.index("The problem.")
    assert ("US1.2. (nothing has happened yet) **Given** an orders.csv with only its header row, **When** she opens the "
            "app, **Then** 'No orders yet today' and the upload button") in prd
    assert "orders.csv: name, dish, qty" in prd and "## Assumptions\n- Orders stay on her computer" in prd
    assert "Open questions" not in prd, "an unknown is a decision or an assumption, never a list nobody answers"
    told = yaml.safe_load(_at(spec, stories.FILE))["stories"]
    assert [(sc["id"], sc["empty"]) for sc in told[0]["scenarios"]] == [("US1.1", False), ("US1.2", True)]


def test_maya_where_it_lives_is_a_decision_before_the_spec_card_and_the_answer_is_written_in(s, project, monkeypatch):
    """[평가3] Maya: 'where will the app be hosted' stayed an open question in the spec and never became a decision."""
    spec, _ = _write(s, project, monkeypatch, _draft(_repo(), clarifications=[HOSTING, ACCESS]))

    assert (spec.status, spec.approval_request_id) == ("CLARIFICATION_REQUIRED", None), "no spec card before the answers"
    assert status.spec_state(s, spec) == "CLARIFICATION_REQUIRED", "the build view shows it waiting on the owner"
    hosting, access = _questions(s, spec)
    shown = requests.report(s, hosting, "en")
    assert shown["headline"] == "Where will the app live?" and shown["why"] == "Only you use it for now."
    assert [(c["label"], c["recommended"], c["verdict"]) for c in shown["choices"]] == [
        ("On this computer", True, "answer"), ("Cloudflare Pages", False, "answer")]
    assert hosting.expires_at is None

    _answer(s, hosting, decision=HumanDecision.ANSWER, chosen_option="Cloudflare Pages")
    assert s.get(Spec, spec.id).approval_request_id is None, "one question is still open"

    _answer(s, access, decision=HumanDecision.DEFER)
    spec = s.get(Spec, spec.id)
    card = s.get(AgentRequest, spec.approval_request_id)
    assert (spec.status, card.action_kind, card.status) == ("IN_REVIEW", "dev.spec_approve", "PENDING")
    assert card.action_params["commit_sha"] == spec.draft_commit_sha
    assert card.action_params["clarified"] == [
        {"kind": "clarify", "question": "Where will the app live?", "answer": "Cloudflare Pages", "assumed": False},
        {"kind": "clarify", "question": "Who can see the orders?", "answer": "Only me", "assumed": True}]
    prd = _at(spec, "prd.md")
    assert "NEEDS CLARIFICATION" not in prd + _at(spec, "requirements.yaml")
    assert "[Decided by the owner: Cloudflare Pages]" in prd
    assert yaml.safe_load(_at(spec, "requirements.yaml"))["requirements"][0]["statement"] ==         "Show today's orders to [Assumed, the owner didn't pick: Only me]"
    assert "- Q: Where will the app live? → A: Cloudflare Pages\n" in prd.split("## Clarifications")[1]
    trace = {r["source"]: r for r in card.action_params["owner_trace"]}
    assert trace[requests.display_id(hosting.id)]["disposition"] == "included", "the answer is in the spec, not missed"

    specs.on_questions_answered(spec.id)
    assert len(list(s.scalars(select(AgentRequest).where(AgentRequest.action_kind == "dev.spec_approve",
                                                         AgentRequest.project_id == project.id)))) == 1


def test_the_answer_is_the_options_whole_words_and_a_note_on_a_put_off_card_stays_a_note(s, project, monkeypatch):
    laptop = "Only on the laptop at the counter"  # longer than an option label shows
    where = HOSTING.model_copy(update={"options": [ClarifyOptionOut(answer=laptop, means="Free."),
                                                   ClarifyOptionOut(answer="Cloudflare Pages", means="Link.")]})
    spec, _ = _write(s, project, monkeypatch, _draft(_repo(), clarifications=[where, ACCESS]))
    hosting, access = _questions(s, spec)
    _answer(s, hosting, decision=HumanDecision.ANSWER, chosen_option=hosting.action_params["card"]["options"][0]["label"])
    _answer(s, access, decision=HumanDecision.DEFER, note="ask me tomorrow")

    clarified = s.get(AgentRequest, s.get(Spec, spec.id).approval_request_id).action_params["clarified"]
    assert [(c["answer"], c["assumed"], c.get("note")) for c in clarified] == [
        (laptop, False, None), ("Only me", True, "ask me tomorrow")]


def test_a_spec_asks_at_most_three_questions(s, project, monkeypatch):
    many = [ClarifyOut(question=f"Question {n}?", why_it_matters="m", why_recommended="w",
                       options=[ClarifyOptionOut(answer="Yes", means="y"), ClarifyOptionOut(answer="No", means="n")])
            for n in range(5)]
    spec, _ = _write(s, project, monkeypatch, _draft(_repo(), clarifications=many))
    assert [q.action_params["card"]["headline"] for q in _questions(s, spec)] == ["Question 0?", "Question 1?",
                                                                                   "Question 2?"]


def _write_drafts(s, project, monkeypatch, outs: list[SpecDraftOut]) -> tuple[int, list[str]]:
    prompts: list[str] = []
    monkeypatch.setattr(specs, "run_agent", lambda agent, purpose, prompt, model, **kw: prompts.append(prompt)
                        or outs.pop(0))
    req = AgentRequest(project_id=project.id, from_agent="product", type="DEVELOPMENT", title="Banchan orders",
                       summary="s", rationale="r", status="APPROVED", policy_mode="auto",
                       action_kind="dev.implementation_start", action_params={"need": {"title": "orders"}})
    s.add(req)
    s.commit()
    return req.id, prompts


def test_a_draft_without_a_p1_story_is_drafted_again_then_fails_where_the_owner_sees_it(s, project, monkeypatch):
    no_p1 = _draft(_repo(), user_stories=[EXPORT])
    rid, prompts = _write_drafts(s, project, monkeypatch, [no_p1, _draft(_repo())])
    spec = s.get(Spec, specs.write_spec(project.id, rid))
    assert len(prompts) == 2 and "no P1 user story" in prompts[1]
    assert spec.status == "IN_REVIEW" and spec.approval_request_id, "the second draft carries on as any other"
    spec.status = "MERGED"
    s.commit()

    rid, prompts = _write_drafts(s, project, monkeypatch, [no_p1, _draft(_repo(), user_stories=[])])
    with pytest.raises(RuntimeError, match="no user story the build must do first"):
        specs.write_spec(project.id, rid)
    assert len(prompts) == 2 and s.scalar(select(Spec.id).where(Spec.request_id == rid)) is None


def test_a_marker_nobody_was_asked_reaches_the_engineer_as_an_open_point_not_a_marker(s, project, monkeypatch):
    long = "Show today's orders, grouped by dish and sorted by pickup time, to " * 3 + \
        "[NEEDS CLARIFICATION Q1: who can open the order list from the counter laptop?]"
    spec, _ = _write(s, project, monkeypatch, _draft(_repo(), clarifications=[], requirements=[
        RequirementOut(id="REQ-1", statement=long, priority="must", rationale="r")]))

    assert s.get(AgentRequest, spec.approval_request_id).action_kind == "dev.spec_approve", "nothing to ask: the card"
    text = _at(spec, "prd.md") + _at(spec, "requirements.yaml")
    assert "NEEDS" not in text and "[Not decided yet: Where will it live?]" in _at(spec, "prd.md")
    assert yaml.safe_load(_at(spec, "requirements.yaml"))["requirements"][0]["statement"].endswith(
        "[Not decided yet: who can open the order list from the counter laptop?]")


def test_a_long_requirement_folded_by_yaml_still_gets_the_owners_answer(s, project, monkeypatch):
    long = "Show today's orders, grouped by dish and sorted by pickup time, to " * 3 + \
        "[NEEDS CLARIFICATION Q2: who can open the order list from the counter laptop?]"
    spec, _ = _write(s, project, monkeypatch, _draft(_repo(), clarifications=[HOSTING, ACCESS], requirements=[
        RequirementOut(id="REQ-1", statement=long, priority="must", rationale="r")]))
    assert "Q2: who can open" not in _at(spec, "requirements.yaml"), "the dump folds the marker across lines"
    for card in _questions(s, spec):
        _answer(s, card, decision=HumanDecision.ANSWER, chosen_option=card.action_params["card"]["options"][1]["label"])
    spec = s.get(Spec, spec.id)
    assert yaml.safe_load(_at(spec, "requirements.yaml"))["requirements"][0]["statement"].endswith(
        "[Decided by the owner: My staff too]")


def test_the_build_reads_the_spec_with_the_owners_answers_in(s, project, monkeypatch):
    """The review's catch: the handoff kept the commit from before the answers, so the build refused to start."""
    spec, _ = _write(s, project, monkeypatch, _draft(_repo(), clarifications=[HOSTING]))
    [hosting] = _questions(s, spec)
    _answer(s, hosting, decision=HumanDecision.ANSWER, chosen_option="Cloudflare Pages")
    card = s.get(AgentRequest, s.get(Spec, spec.id).approval_request_id)
    requests.respond(s, card.id, HumanResponse(decision=HumanDecision.APPROVE, chosen_option="Approve"))
    s.commit()

    assert specs.on_spec_approved(spec.id, card.id)
    s.expire_all()
    spec = s.get(Spec, spec.id)
    h = Handoff.model_validate(spec.handoff)
    assert h.source.commit_sha == spec.approved_commit_sha, "worker._run refuses a handoff on another commit"
    pinned = yaml.safe_load(registry.read(f"{spec.registry_path}/handoff.yaml"))
    assert pinned["source"]["commit_sha"] == spec.approved_commit_sha
    assert "[Decided by the owner: Cloudflare Pages]" in registry.read_at(f"{spec.registry_path}/prd.md",
                                                                         h.source.commit_sha)


def test_the_build_view_says_the_answers_are_being_written_once_none_is_open(s, project, monkeypatch):
    spec, _ = _write(s, project, monkeypatch, _draft(_repo(), clarifications=[HOSTING]))
    [hosting] = _questions(s, spec)
    stage = status._of_spec(s, spec, "en")
    assert (stage.headline, stage.link.to) == ("The spec has 1 question for you.", f"/inbox?req={hosting.id}")

    requests.respond(s, hosting.id, HumanResponse(decision=HumanDecision.DEFER))
    s.commit()
    assert status._of_spec(s, s.get(Spec, spec.id), "en").headline == "Writing your answers into the spec."


def _pending_decision(s, project, text: str) -> str:
    from justshipit.db.models import Decision
    card = AgentRequest(project_id=project.id, from_agent="project_manager", type="APPROVAL", title="Decision", summary="s",
                        rationale="r", status="PENDING", policy_mode="approve", action_kind="project.direction_change")
    s.add(card)
    s.flush()
    d = Decision(project_id=project.id, title="Use React", decision="go", rationale=text, status="PENDING_APPROVAL",
                 approval_request_id=card.id)
    s.add(d)
    s.flush()
    return f"DEC-{d.id}"


def test_the_first_constitution_binds_only_once_the_owner_approves_the_spec_and_cites_only_his_words(
        s, project, monkeypatch):
    """A rule the agent wrote, or took from a decision the owner has not approved, was binding before the owner had
    approved anything, and outlived a rejected spec."""
    project.context = {"directives": [{"n": 1, "at": "t", "text": "No sign-up. She won't make an account."}]}
    pending = _pending_decision(s, project, "The CEO thinks React is best.")
    repo = _repo()
    rules = [PrincipleOut(rule="Nobody signs up to use it", why="She won't make an account", source="D-1"),
             PrincipleOut(rule="Use React", why="popular", source=pending),
             PrincipleOut(rule="Use Vue", why="w", source="made-up")]
    spec, prompts = _write(s, project, monkeypatch, _draft(repo, clarifications=[], constitution=rules))

    assert "It has none yet" in prompts[0]
    assert constitution.read(repo) is None, "nothing binds before the owner approves the spec"
    card = s.get(AgentRequest, spec.approval_request_id)
    assert [r["rule"] for r in card.action_params["rules"]] == ["Nobody signs up to use it"], "only the owner's words"
    assert "## C1. Nobody signs up to use it" in _at(spec, constitution.FILE)

    requests.respond(s, card.id, HumanResponse(decision=HumanDecision.REJECT))
    s.commit()
    again, prompts = _write(s, project, monkeypatch, _draft(repo, clarifications=[], constitution=rules[:1]))
    assert "It has none yet" in prompts[0] and constitution.read(repo) is None, "a rejected spec binds nothing"

    _approve(s, again)
    assert [r["rule"] for r in constitution.read(repo)["principles"]] == ["Nobody signs up to use it"]


@pytest.mark.parametrize("pick, note, expected", [
    ("Change the rule", None, "Staff sign in with a shared code"),
    ("Change the rule", "only for staff", "Staff sign in with a shared code (only for staff)"),
    (None, "ask me tomorrow", "Nobody signs up to use it")])
def test_a_later_spec_changes_a_rule_only_when_the_owner_says_so(s, project, monkeypatch, pick, note, expected):
    """[평가3] Alex: the product grew beyond what he asked. A rule holds until the owner changes it."""
    project.context = {"directives": [{"n": 1, "at": "t", "text": "No sign-up."}]}
    repo = _repo()
    first, _ = _write(s, project, monkeypatch, _draft(repo, clarifications=[], constitution=[
        PrincipleOut(rule="Nobody signs up to use it", why="No accounts", source="D-1")]))
    first = _approve(s, first)
    first.status = "MERGED"
    s.commit()

    second, prompts = _write(s, project, monkeypatch, _draft(repo, clarifications=[], constitution=[
        PrincipleOut(rule="Rewritten by a later spec", why="w", source="D-1")], constitution_changes=[
        PrincipleChangeOut(principle_id="C1", rule="Staff sign in with a shared code", why="Two staff now take orders")]))

    assert "C1: Nobody signs up to use it" in prompts[0], "the spec writer reads the rules"
    [amend] = _questions(s, second)
    assert second.approval_request_id is None
    shown = requests.report(s, amend, "en")
    assert "Two staff now take orders" not in shown["why"] and not amend.action_params["card"]["recommendation"], \
        "a rule change states both sides; it recommends neither"
    assert not any(c["recommended"] for c in shown["choices"])
    _answer(s, amend, **({"decision": HumanDecision.ANSWER, "chosen_option": pick} if pick
                         else {"decision": HumanDecision.DEFER}), **({"note": note} if note else {}))
    second = s.get(Spec, second.id)
    assert [r["rule"] for r in constitution.read(repo)["principles"]] == ["Nobody signs up to use it"], \
        "the rule changes only with the spec the owner approves"
    assert f"## C1. {expected}" in _at(second, constitution.FILE)
    card = s.get(AgentRequest, second.approval_request_id)
    assert [r["rule"] for r in card.action_params["rules"]] == [expected], "the spec card shows the rules it sets"
    _approve(s, second)
    assert [r["rule"] for r in constitution.read(repo)["principles"]] == [expected]


# ------------------------------------------------------------------ the acceptance check runs the P1 scenarios

def _told() -> list[dict]:
    return stories.number([ORDER, EXPORT])


def _cpo(**kw) -> AcceptanceOut:
    return AcceptanceOut(**{"accepted": True, "notes": "ok", "criteria_results": [
        CriterionResultOut(criterion="list", met=True, evidence="src/list.ts")], "core_flow": CoreFlowOut(
        realistic=True, quiet_day=True, evidence="fixtures/core-flow/orders.csv"), **kw})


def test_each_p1_scenario_is_a_criterion_met_only_when_the_builds_own_check_passed_it(monkeypatch):
    flow = {"ran": True, "ok": True, "log": "ok", "scenarios": {"US1.1": "pass", "US2.1": "pass"}}
    out = worker.held_to_core_flow(_cpo(), flow, "en", _told())
    scenario = [c for c in out.criteria_results if c.criterion.startswith("P1 scenario")]
    assert [(c.criterion, c.met) for c in scenario] == [
        ("P1 scenario: given orders.csv with 12 rows (name, dish, qty), when she opens the app, then 12 orders, "
         "grouped by dish", True),
        ("P1 scenario: given an orders.csv with only its header row, when she opens the app, then 'No orders yet "
         "today' and the upload button", False)]
    assert "never ran this scenario" in scenario[1].evidence and out.accepted is False
    assert "US1" not in " ".join(c.criterion for c in scenario), "the owner reads the scenario, not its id"

    silent = worker.held_to_core_flow(_cpo(), {"ran": False, "ok": False, "log": "[no scripts/core-flow.sh]"}, "en",
                                      _told())
    assert [c.criterion for c in silent.criteria_results if c.criterion.startswith("P1 scenario")] == [
        "P1 scenarios (2)"], "a check that never ran is one line, not every scenario failed"
    assert stories.gap_lines(stories.gaps(_told(), _cpo(), {"ran": False}), "en")[0] == \
        "Take today's orders (P1): nobody ran its scenarios."

    flow["scenarios"]["US1.2"] = "pass"
    built = [StoryCheckOut(story_id="US1", built="yes", evidence="src/list.ts")]
    assert worker.held_to_core_flow(_cpo(stories=built), flow, "en", _told()).accepted is True
    partly = [StoryCheckOut(story_id="US1", built="partly", evidence="no grouping")]
    assert worker.held_to_core_flow(_cpo(stories=partly), flow, "en", _told()).accepted is False, "P1 is built whole"
    assert worker.held_to_core_flow(_cpo(), {**flow, "scenarios": {"US1.1": "pass", "US1.2": "fail"}}, "en",
                                    _told()).accepted is False


def test_the_core_flow_reads_every_scenario_line_however_long_the_log(tmp_path, monkeypatch):
    from justshipit.sandbox import docker
    export = tmp_path / "export"
    (export / "scripts").mkdir(parents=True)
    (export / "scripts" / "core-flow.sh").write_text("true\n")
    out = "[scenario US1.1] pass\n" + "row ok\n" * 3000 + "[scenario US1.2] fail: an error page\n"
    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    monkeypatch.setattr(docker, "run", lambda spec, **kw: docker.ContainerResult(1, out.encode(), b"", False))
    assert worker.run_core_flow(export)["scenarios"] == {"US1.1": "pass", "US1.2": "fail"}
    assert stories.ran("[scenario US1.1] fail: empty\n[scenario US1.1] pass\n") == {"US1.1": "fail"}, \
        "a scenario that failed on one input failed"


def test_the_card_leads_with_where_the_build_and_the_stories_disagree(s, project, monkeypatch):
    """[평가3] the report said more was built than the code did, and 이준호's daily file was blocked: the owner reads
    the gap before the verdict. A P1 scenario that did not pass fails the check ([재평가] Sam), so the card is the one
    that offers fix or drop, never the merge."""
    from justshipit.contracts.handoff import FIX_QUESTION_KEY
    from justshipit.db.models import Project
    from test_engineering import _approved_job, _product_repo, _spec_in_registry
    repo = _product_repo(f"sk-merge-{project.id}")
    job, spec, h = _approved_job(s, project, repo.name)
    sha = _spec_in_registry({"prd.md": "# PRD\n", "acceptance-criteria.md": "- [ ] list\n",
                             stories.FILE: stories.dump(_told())})
    h.source.commit_sha = spec.approved_commit_sha = sha
    spec.handoff = h.model_dump(mode="json")
    job.branch, job.status = "main", "DELIVERED"
    s.get(Project, project.id).spent_usd = 9.0  # too little left for the fix round it would start on its own
    s.commit()
    monkeypatch.setattr(worker, "run_core_flow", lambda export: {
        "ran": True, "ok": False, "log": "exit 1", "scenarios": {"US1.1": "pass", "US1.2": "fail"}})
    seen = {}
    monkeypatch.setattr(worker, "run_agent", lambda agent, purpose, prompt, model, **kw: seen.update(prompt=prompt) or _cpo(
        stories=[StoryCheckOut(story_id="US1", built="yes", evidence="src/list.ts")],
        beyond_spec=["A sign-up page (src/signup.ts)"]))

    worker.acceptance(job.id)

    s.expire_all()
    assert s.get(DevJob, job.id).error is None, s.get(DevJob, job.id).error
    assert "stories.yaml" in seen["prompt"] and "beyond_spec" in seen["prompt"]
    assert s.scalar(select(AgentRequest).where(AgentRequest.action_kind == "dev.merge",
                                               AgentRequest.action_params["dev_job_id"].as_integer() == job.id)) is None
    card = s.scalar(select(AgentRequest).where(AgentRequest.action_params[FIX_QUESTION_KEY].as_integer() == job.id))
    lines = card.summary.split("\n")
    assert lines[:2] == [msg("build.failed.run", "en"), msg("build.failed.scenarios", "en", n=1, total=2)]
    assert lines[2:5] == ["Take today's orders (P1): didn't pass when an orders.csv with only its header row, she "
                          "opens the app: 'No orders yet today' and the upload button",
                          "Export the list (P2): the acceptance check didn't say whether it's built.",
                          "Built beyond the spec: A sign-up page (src/signup.ts)"]


def test_a_spec_from_before_stories_is_accepted_as_before(s, project, monkeypatch):
    out = worker.held_to_core_flow(_cpo(), {"ran": True, "ok": True, "log": "ok"}, "en", [])
    assert out.accepted is True and len(out.criteria_results) == 2
    assert stories.gaps([], _cpo(), {"ran": True}) == []


def test_a_development_ask_while_the_spec_waits_on_its_questions_reaches_its_card(s, project, monkeypatch):
    """L9 gap 3: an ask that arrived while the spec waited on its questions was only an activity note, and the spec
    card raised after the answers never mentioned it. It now waits on the spec like an ask on an open spec card."""
    from justshipit.contracts.requests import AgentRequestDraft, RequestType, RiskLevel
    from justshipit.services import matters
    spec, _ = _write(s, project, monkeypatch, _draft(_repo(), clarifications=[HOSTING]))
    first = s.get(AgentRequest, spec.request_id)
    read_already = {"title": "Weekly totals", "problem": "Asked before the spec was written", "by": "project_manager"}
    first.action_params = {**first.action_params, "matter": f"dev:{matters.product_scope(s, project.id)[0]}",
                           "extra_needs": [read_already]}
    s.commit()

    later = requests.create(s, AgentRequestDraft(
        type=RequestType.DEVELOPMENT, title="Print labels for the kitchen", summary="The kitchen wants a label per dish",
        rationale="r", options=[], recommended_option=None, action_kind="internal.other", estimated_cost_usd=None,
        risk_level=RiskLevel.LOW, context=contract()), from_agent="project_manager", project_id=project.id,
        origin=by_project_manager(s, project.id))
    s.commit()
    assert later.id == first.id, "no second card and no second spec"

    [hosting] = _questions(s, spec)
    _answer(s, hosting, decision=HumanDecision.ANSWER, chosen_option="Cloudflare Pages")
    card = s.get(AgentRequest, s.get(Spec, spec.id).approval_request_id)
    assert [n["title"] for n in card.action_params["extra_needs"]] == ["Print labels for the kitchen"]
    assert "Print labels for the kitchen" in card.rationale, "the owner reads it on the spec card"
    assert "[For the next version, not in this spec · Project Manager] Print labels" in card.rationale, "who asked, in owner words"
    assert "Weekly totals" not in card.rationale, "a need this spec already read is not a next-version candidate"


@pytest.mark.parametrize("model", [PrincipleOut, PrincipleChangeOut])
def test_a_rule_is_written_as_text_the_owner_reads_on_the_spec_card(model):
    """Round 6 (준호, P-10): the card read "… 리포트를 만든 뒤 지운다." under the stories' 해요체. The spec writer is told a
    rule is a sentence the owner reads on the card, so the Output language's style holds for it."""
    rule = model.model_json_schema()["properties"]["rule"]["description"]
    assert "the owner reads on the spec card" in rule and "Style" in rule
