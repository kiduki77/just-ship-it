"""Decision rights: the CEO decides the few calls the business's door rules hand it and only reports them, the owner
keeps everything else, and the rules come from the approved brief."""
import pytest
from justshipit.contracts.door import DoorRuleOut, DoorRulesOut
from justshipit.contracts.requests import (
    AgentRequestDraft, HumanDecision, HumanResponse, PolicyMode, RequestStatus, RequestType, RiskLevel,
)
from justshipit.db.models import ActivityEvent, AgentRequest, AppSetting, Business, Job, Plan
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.operations import gateway
from justshipit.services import app_settings, door_rules, policy, projects, requests, roadmap
from justshipit.services.control import ControlError
from justshipit.services.usage import MustWait
from justshipit.settings import tool_policy
from sqlalchemy import delete, select
from test_usage_defer import run_once, settle_queue
from ask_kit import contract, by_project_manager


def draft(**kw) -> AgentRequestDraft:
    base = dict(type=RequestType.APPROVAL, title="내부 정리", summary="s", rationale="r", options=[],
                recommended_option=None, action_kind="internal.task_create", estimated_cost_usd=None,
                risk_level=RiskLevel.LOW, context=contract())
    return AgentRequestDraft(**{**base, **kw})


def rule(action: str, decider: str, **kw) -> dict:
    return {"id": f"d-{action}", "action": action, "decider": decider, **kw}


@pytest.fixture
def firm(s, project):
    """The business the project belongs to, whose door rules reach the project's requests."""
    s.commit()
    return s.get(Business, project.business_id)


def set_rules(business: Business, *rules: dict) -> None:
    """Store rules without the write checks: what an older row, or a hand edit, can hold."""
    with session_scope() as s:
        s.get(Business, business.id).door_policy = list(rules)


@pytest.fixture(autouse=True)
def _clean_settings():
    yield
    with session_scope() as s:
        s.execute(delete(AppSetting))


def events_of(request_id: int) -> list[ActivityEvent]:
    with session_scope() as s:
        found = list(s.scalars(select(ActivityEvent).where(ActivityEvent.entity == "request",
                                                           ActivityEvent.entity_id == request_id)
                               .order_by(ActivityEvent.id)))
        s.expunge_all()
        return found


# ------------------------------------------------------------------ the policy engine

def test_without_rules_the_policy_is_what_it_was(s, project):
    req = requests.create(s, draft(), from_agent="research", project_id=project.id, origin=by_project_manager(s, project.id))
    assert req.status == RequestStatus.PENDING.value and req.policy_mode == "approve"


def test_a_project_manager_rule_lowers_approve_to_auto_and_the_owner_can_read_who_decided(s, firm, project):
    set_rules(firm, rule("internal.*", "project_manager", note="오너가 맡긴 일", source="brief:v1:l-aa11"))

    req = requests.create(s, draft(), from_agent="research", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()

    assert (req.status, req.policy_mode) == (RequestStatus.AUTO_APPROVED.value, "auto")
    assert req.response["responded_by"] == "project_manager (결정 권한)"
    assert req.response["door_rule"]["action"] == "internal.*" and req.response["door_rule"]["source"] == "brief:v1:l-aa11"
    assert "internal.*" in req.response["note"] and "오너가 맡긴 일" in req.response["note"]
    seen = {(e.verb, e.actor) for e in events_of(req.id)}
    assert ("ceo_decided", "project_manager") in seen
    assert ("status", "project_manager (결정 권한)") in seen, "the status change is attributed to the rule, not to the policy"
    cited = next(e for e in events_of(req.id) if e.verb == "ceo_decided")
    assert cited.data["door_rule"]["action"] == "internal.*" and cited.project_id == project.id


def test_a_policy_auto_approval_is_still_attributed_to_the_policy(s, project):
    req = requests.create(s, draft(type=RequestType.SKILL, action_kind="skill.create_candidate"),
                          from_agent="research", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    assert req.status == RequestStatus.AUTO_APPROVED.value and req.response is None
    assert {e.actor for e in events_of(req.id) if e.verb == "status"} == {"policy"}


def test_the_project_manager_does_not_decide_when_the_request_is_risky_or_costs_money(s, firm, project):
    set_rules(firm, rule("internal.*", "project_manager"))
    risky = requests.create(s, draft(risk_level=RiskLevel.HIGH), from_agent="research", project_id=project.id,
                            dedupe=False, origin=by_project_manager(s, project.id))
    costly = requests.create(s, draft(estimated_cost_usd=3.0), from_agent="research", project_id=project.id,
                             dedupe=False, origin=by_project_manager(s, project.id))
    assert (risky.status, costly.status) == (RequestStatus.PENDING.value, RequestStatus.PENDING.value)
    assert not [e for e in events_of(risky.id) if e.verb == "ceo_decided"]


@pytest.mark.parametrize("type_", [RequestType.QUESTION, RequestType.ESCALATION])
def test_a_question_and_an_escalation_always_wait_for_the_owner(s, firm, project, type_):
    set_rules(firm, rule("internal.*", "project_manager"))
    req = requests.create(s, draft(type=type_), from_agent="research", project_id=project.id,
                          origin=by_project_manager(s, project.id))
    s.flush()
    assert req.status == RequestStatus.PENDING.value and req.policy_mode == "approve"
    assert not queued_for(s, req)


def test_an_owner_rule_on_development_makes_spec_writing_wait_for_the_owner(s, firm, project):
    """A development ask starts a product plan the owner approves on its card before any spec is written."""
    set_rules(firm, rule("dev.*", "owner"))
    plan = requests.create(s, draft(type=RequestType.DEVELOPMENT), from_agent="product", project_id=project.id,
                           origin=by_project_manager(s, project.id))
    s.flush()
    assert isinstance(plan, Plan) and plan.status == "DRAFT"
    assert [j.kind for j in s.scalars(select(Job).where(Job.payload["plan_id"].as_integer() == plan.id))] == [
        "write_plan"]


def test_a_project_manager_rule_on_an_action_the_project_manager_may_not_decide_is_ignored_and_recorded(s, firm, project):
    set_rules(firm, rule("dev.merge", "project_manager"), rule("external.email_send", "project_manager"))

    merge = requests.create(s, draft(action_kind="dev.merge"), from_agent="engineer", project_id=project.id,
                            system=True, origin=by_project_manager(s, project.id))
    mail = requests.create(s, draft(action_kind="external.email_send"), from_agent="marketing", project_id=project.id,
                           origin=by_project_manager(s, project.id))
    s.commit()

    assert (merge.status, merge.policy_mode) == (RequestStatus.PENDING.value, "strong_approve")
    assert (mail.status, mail.policy_mode) == (RequestStatus.PENDING.value, "approve")
    ignored = [e for e in events_of(merge.id) if e.verb == "door_rule_ignored"]
    assert len(ignored) == 1 and ignored[0].data["door_rules"][0]["action"] == "dev.merge"
    assert ignored[0].data["door_rules"][0]["why"] == msg("door.ignored.not_ceo", "en")


def test_an_owner_rule_raises_auto_to_approve(s, firm, project):
    plain = requests.create(s, draft(type=RequestType.SKILL, action_kind="skill.create_candidate"),
                            from_agent="research", project_id=project.id, dedupe=False, origin=by_project_manager(s, project.id))
    assert plain.status == RequestStatus.AUTO_APPROVED.value

    set_rules(firm, rule("skill.create_candidate", "owner"))
    asked = requests.create(s, draft(type=RequestType.SKILL, action_kind="skill.create_candidate"),
                            from_agent="research", project_id=project.id, dedupe=False, origin=by_project_manager(s, project.id))

    assert (asked.status, asked.policy_mode) == (RequestStatus.PENDING.value, "approve")


def decide_with(kind: str, *rules: dict) -> policy.Verdict:
    return policy.decide(RequestType.APPROVAL, kind, RiskLevel.LOW, None, rules)


def test_among_rules_with_the_same_decider_the_most_specific_one_governs():
    both = [rule("internal.*", "project_manager"), rule("internal.task_create", "project_manager", note="이것")]
    assert decide_with("internal.task_create", *both).rule["note"] == "이것"
    assert decide_with("internal.task_create", *both).mode == PolicyMode.AUTO
    assert decide_with("internal.report_draft", *both).rule["action"] == "internal.*"


@pytest.mark.parametrize("owner_rule, project_manager_rule", [
    (rule("internal.*", "owner"), rule("internal.task_create", "project_manager")),
    (rule("internal.task_create", "owner"), rule("internal.*", "project_manager")),
    ({**rule("research.*", "owner"), "origin": "brief", "source": "brief:v1:l-x1"},
     {**rule("research.web_search", "project_manager"), "origin": "stock", "source": "default"}),
])
def test_an_owner_rule_beats_a_project_manager_rule_however_specific_either_is(owner_rule, project_manager_rule):
    kind = project_manager_rule["action"] if not project_manager_rule["action"].endswith("*") else "internal.task_create"
    verdict = decide_with(kind, project_manager_rule, owner_rule)
    assert verdict.mode == PolicyMode.APPROVE and verdict.rule is None
    assert decide_with(kind, owner_rule, project_manager_rule).mode == PolicyMode.APPROVE


def test_a_project_manager_rule_beside_an_owner_rule_for_another_action_still_decides():
    assert decide_with("internal.task_create", rule("research.*", "owner"), rule("internal.*", "project_manager")).mode \
        == PolicyMode.AUTO


def test_a_business_without_rules_and_a_request_without_a_project_have_none(s, project):
    assert requests.door_rules(s, project.id) == [] and requests.door_rules(s, None) == []


def test_the_settings_floors_still_win(s, firm, project):
    app_settings.save(s, "policy", {"overrides": {"skill.activate_executable": "approve",
                                                  "internal.task_create": "approve"}}, actor="test")
    s.commit()
    set_rules(firm, rule("skill.activate_executable", "project_manager"), rule("internal.task_create", "project_manager"))

    executable = requests.create(s, draft(action_kind="skill.activate_executable"), from_agent="skill_builder",
                                 project_id=project.id, system=True, origin=by_project_manager(s, project.id))
    pinned = requests.create(s, draft(), from_agent="research", project_id=project.id, origin=by_project_manager(s, project.id))

    assert executable.status == RequestStatus.PENDING.value and executable.policy_mode == "approve", \
        "the owner relaxed it to the floor and no rule goes below"
    assert pinned.status == RequestStatus.PENDING.value, "an action the owner pinned in Settings is not the CEO's to lower"


def test_a_denied_action_stays_denied_under_a_project_manager_rule(s, firm, project):
    set_rules(firm, rule("internal.*", "project_manager"))
    deny = requests.create(s, draft(action_kind="external.pii_share"), from_agent="marketing", project_id=project.id,
                           origin=by_project_manager(s, project.id))
    assert deny.status == RequestStatus.DENIED_BY_POLICY.value


# ------------------------------------------------------------------ what may be written

NOT_PROJECT_MANAGER_DECIDABLE = sorted(k for k in tool_policy() if k not in policy.PROJECT_MANAGER_DECIDABLE)


def test_the_project_manager_decidable_kinds_are_the_ones_with_nothing_to_send_and_nothing_to_start():
    assert policy.PROJECT_MANAGER_DECIDABLE == {
        "research.web_search", "research.web_fetch", "internal.report_draft", "internal.task_create",
        "internal.meeting_call", "skill.create_candidate"}
    assert policy.PROJECT_MANAGER_DECIDABLE <= set(tool_policy()), "a kind the policy file does not know is never decidable"
    for kind in ("internal.other", "external.other", "dev.spec_approve", "dev.plan_approve", "project.reassess",
                 "skill.activate_knowledge", "crm.interaction_log", "budget.spend", "made.up"):
        assert kind not in policy.PROJECT_MANAGER_DECIDABLE, kind


@pytest.mark.parametrize("kind", sorted(policy.PROJECT_MANAGER_DECIDABLE))
def test_a_project_manager_decidable_kind_has_no_connector_and_no_follow_up_job(kind):
    assert kind not in gateway.CONNECTORS
    everything = {"payload": {"to": ["a@b.c"]}, "dev_job_id": 1, "spec_id": 1, "skill_id": 1}
    # A development request starts its spec whatever kind it names: normalize_action files every new one under
    # dev.implementation_start, which the policy runs on its own, and an approved old one still starts it.
    for type_ in set(RequestType) - set(policy.ASKS_THE_OWNER) - {RequestType.DEVELOPMENT}:
        for params in ({}, everything):
            req = AgentRequest(id=1, project_id=1, type=type_.value, action_kind=kind, action_params=params)
            for status in (RequestStatus.APPROVED, RequestStatus.AUTO_APPROVED):
                assert requests._effect(req, status) is None, (kind, type_, params, status)


@pytest.mark.parametrize("kind", NOT_PROJECT_MANAGER_DECIDABLE)
def test_no_project_manager_rule_can_be_written_for_a_kind_the_project_manager_may_not_decide(s, firm, kind):
    with pytest.raises(ControlError) as exc:
        roadmap.update_lists(s, firm.id, lang="ko", door_policy=[{"action": kind, "decider": "project_manager"}])
    assert msg("door.problem.not_ceo", "ko", action=kind, kinds=", ".join(sorted(policy.PROJECT_MANAGER_DECIDABLE))) in str(
        exc.value)
    roadmap.update_lists(s, firm.id, lang="ko", door_policy=[{"action": kind, "decider": "owner"}])


@pytest.mark.parametrize("pattern", ["external.*", "dev.*", "budget.*", "project.*", "data.*", "crm.*"])
def test_a_family_with_no_decidable_member_cannot_go_to_the_project_manager(s, firm, pattern):
    with pytest.raises(ControlError, match="프로젝트 매니저가 정할 수 없어서"):
        roadmap.update_lists(s, firm.id, lang="ko", door_policy=[{"action": pattern, "decider": "project_manager"}])


@pytest.mark.parametrize("pattern", ["internal.*", "research.*", "skill.*"])
def test_a_family_rule_for_the_project_manager_reaches_only_its_decidable_members(s, firm, project, pattern):
    roadmap.update_lists(s, firm.id, lang="ko", door_policy=[{"action": pattern, "decider": "project_manager"}])
    s.commit()
    reached = {k for k in tool_policy() if policy.matches(pattern, k)}
    for kind in reached:
        verdict = policy.decide(RequestType.APPROVAL, kind, RiskLevel.LOW, None, requests.door_rules(s, project.id))
        assert (verdict.mode == PolicyMode.AUTO) == (kind in policy.PROJECT_MANAGER_DECIDABLE), kind


def test_an_unknown_action_kind_cannot_be_ruled(s, firm):
    with pytest.raises(ControlError, match="모르는 행동 종류예요"):
        roadmap.update_lists(s, firm.id, lang="ko", door_policy=[{"action": "made.up", "decider": "owner"}])


def test_rules_the_project_manager_may_hold_are_stored(s, firm):
    roadmap.update_lists(s, firm.id, lang="ko", door_policy=[{"action": "internal.*", "decider": "project_manager"},
                                                  {"action": "research.web_search", "decider": "project_manager"}])
    assert [r["action"] for r in s.get(Business, firm.id).door_policy] == ["internal.*", "research.web_search"]


def test_every_default_rule_is_one_the_project_manager_may_hold():
    for r in door_rules.default_rules("en"):
        assert policy.door_rule_problem(r) is None, r


# ------------------------------------------------------------------ rules from the brief

def line(id, text, provenance="said"):
    return {"id": id, "text": text, "provenance": provenance, "source": "kickoff:1:3"}


def make_brief(s, business, *, rights=(), red=(), approve=True):
    sections = roadmap.empty_sections()
    for sec in sections:
        if sec["key"] == "decision_rights":
            sec["lines"] = list(rights)
        if sec["key"] == "red_lines":
            sec["lines"] = list(red)
        if sec["key"] in ("customer_problem", "goal_deadline"):
            sec["lines"] = [line(f"l-gate-{sec['key']}", "승인에 필요한 오너의 줄")]
    brief = roadmap.new_brief_version(s, business.id, sections=sections, assumptions=[], author="project_manager")
    if approve:
        roadmap.approve_brief(s, brief.id, lang="ko")
    s.commit()
    return brief


class ScriptedProjectManager:
    def __init__(self, *rules: DoorRuleOut):
        self.rules, self.calls = list(rules), []

    def __call__(self, agent, purpose, prompt, output_model, *, project_id, system_override=None, **kw):
        assert (agent, purpose, output_model, project_id) == ("project_manager", "plan", DoorRulesOut, None)
        assert system_override and "decision rights" in system_override
        self.calls.append(prompt)
        return DoorRulesOut(rules=self.rules)


@pytest.fixture
def project_manager(monkeypatch):
    def install(*rules: DoorRuleOut) -> ScriptedProjectManager:
        scripted = ScriptedProjectManager(*rules)
        monkeypatch.setattr(door_rules, "run_agent", scripted)
        return scripted
    return install


@pytest.fixture(autouse=True)
def _own_queue():
    settle_queue("derive_door_rules")
    yield
    settle_queue("derive_door_rules")


def stored(business) -> dict[str, dict]:
    with session_scope() as s:
        return {r["action"]: r for r in s.get(Business, business.id).door_policy}


def test_approving_a_brief_queues_the_derivation_and_changes_nothing_else(s, firm):
    brief = make_brief(s, firm, rights=[line("l-r1", "회의록 정리는 알아서 하세요")])

    jobs = s.scalars(select(Job).where(Job.kind == "derive_door_rules")).all()
    assert [j.payload for j in jobs] == [{"brief_id": brief.id}]
    assert stored(firm) == {}, "the rules wait for the CEO's reading, approval itself stays fast"


def test_the_project_manager_reads_the_rights_and_the_red_lines_into_rules_that_cite_their_line(s, firm, project_manager):
    brief = make_brief(s, firm,
                       rights=[line("l-r1", "내부 문서 정리와 조사는 알아서 하세요"), line("l-r2", "가격은 제가 정해요")],
                       red=[line("l-x1", "내 이름으로 글을 올리지 마세요")])
    scripted = project_manager(DoorRuleOut(action="internal.*", decider="project_manager", line_id="l-r1", reason="문서 정리를 맡겼습니다"),
                   DoorRuleOut(action="research.*", decider="project_manager", line_id="l-r1", reason="조사를 맡겼습니다"),
                   DoorRuleOut(action="budget.spend", decider="owner", line_id="l-r2", reason="가격은 오너가 정합니다"),
                   DoorRuleOut(action="external.social_post", decider="owner", line_id="l-x1", reason="금지선입니다"))

    door_rules.derive(brief.id)

    rules = stored(firm)
    assert set(rules) == {"internal.*", "research.*", "budget.spend", "external.social_post"}
    assert rules["internal.*"]["decider"] == "project_manager" and rules["internal.*"]["source"] == "brief:v1:l-r1"
    assert "내부 문서 정리와 조사는 알아서 하세요" in rules["internal.*"]["note"]
    assert rules["external.social_post"]["source"] == "brief:v1:l-x1"
    assert "l-r1" in scripted.calls[0] and "l-x1" in scripted.calls[0]
    assert "default" not in {r["source"] for r in rules.values()}, "the brief spoke, so no stock rules"
    with session_scope() as x:
        assert x.scalar(select(ActivityEvent).where(ActivityEvent.verb == "door_rules_derived",
                                                    ActivityEvent.business_id == firm.id)) is not None


def test_whatever_the_model_writes_a_kind_the_project_manager_may_not_decide_is_the_owners(s, firm, project_manager):
    brief = make_brief(s, firm, rights=[line("l-r1", "메일 보내기와 정리는 알아서 하세요")],
                       red=[line("l-x1", "유료 광고는 하지 마세요")])
    project_manager(DoorRuleOut(action="external.email_send", decider="project_manager", line_id="l-r1", reason="메일을 맡겼습니다"),
        DoorRuleOut(action="dev.*", decider="project_manager", line_id="l-r1", reason="개발을 맡겼습니다"),
        DoorRuleOut(action="internal.other", decider="project_manager", line_id="l-r1", reason="그 밖의 일을 맡겼습니다"),
        DoorRuleOut(action="skill.activate_knowledge", decider="project_manager", line_id="l-r1", reason="스킬을 맡겼습니다"),
        DoorRuleOut(action="internal.report_draft", decider="project_manager", line_id="l-x1", reason="금지선 줄에서 읽었습니다"))

    door_rules.derive(brief.id)

    rules = stored(firm)
    for owned in ("external.email_send", "dev.*", "internal.other", "skill.activate_knowledge", "internal.report_draft"):
        assert rules[owned]["decider"] == "owner", owned
    assert rules["internal.report_draft"]["source"].endswith("l-x1"), "a red line never hands the CEO anything"
    assert all(policy.door_rule_problem(r) is None for r in rules.values())
    assert all(policy.project_manager_kinds_covered(r["action"]) for r in rules.values() if r["decider"] == "project_manager")


def test_rules_that_cite_an_unknown_line_or_kind_are_dropped(s, firm, project_manager):
    brief = make_brief(s, firm, rights=[line("l-r1", "정리는 알아서 하세요")])
    project_manager(DoorRuleOut(action="internal.*", decider="project_manager", line_id="l-made-up", reason="지어낸 줄"),
        DoorRuleOut(action="made.up", decider="project_manager", line_id="l-r1", reason="없는 종류"),
        DoorRuleOut(action="internal.task_create", decider="project_manager", line_id="l-r1", reason="맡겼습니다"))

    door_rules.derive(brief.id)

    assert set(stored(firm)) == {"internal.task_create"}


def test_a_guess_or_unknown_line_gives_the_project_manager_nothing(s, firm, project_manager):
    brief = make_brief(s, firm, rights=[line("l-r1", "아마 알아서 해도 될 것 같아요", "guess"),
                                        line("l-r2", "아직 모릅니다", "unknown")])
    scripted = project_manager()

    door_rules.derive(brief.id)

    assert scripted.calls == []
    assert {r["source"] for r in stored(firm).values()} == {"default"}


def test_a_brief_that_says_nothing_about_decision_rights_gets_the_stock_rules_without_a_model_call(s, firm, project_manager):
    brief = make_brief(s, firm)
    scripted = project_manager()

    door_rules.derive(brief.id)

    rules = stored(firm)
    assert set(rules) == {"internal.*", "research.*"}
    assert {r["decider"] for r in rules.values()} == {"project_manager"} and {r["source"] for r in rules.values()} == {"default"}
    assert {r["origin"] for r in rules.values()} == {"stock"}
    assert scripted.calls == []


def test_red_lines_alone_still_reach_the_model_and_the_stock_rules_fill_the_rest(s, firm, project_manager):
    brief = make_brief(s, firm, red=[line("l-x1", "유료 광고는 하지 마세요")])
    scripted = project_manager(DoorRuleOut(action="external.ads_run", decider="owner", line_id="l-x1", reason="금지선입니다"))

    door_rules.derive(brief.id)

    rules = stored(firm)
    assert len(scripted.calls) == 1
    assert rules["external.ads_run"]["decider"] == "owner" and rules["internal.*"]["source"] == "default"


def test_the_stock_rules_decide_for_the_project_and_nothing_more(s, firm, project, project_manager):
    door_rules.derive(make_brief(s, firm).id)

    internal = requests.create(s, draft(action_kind="internal.task_create", type=RequestType.APPROVAL),
                               from_agent="research", project_id=project.id, dedupe=False, origin=by_project_manager(s, project.id))
    research = requests.create(s, draft(action_kind="research.web_search"), from_agent="research",
                               project_id=project.id, dedupe=False, origin=by_project_manager(s, project.id))
    activation = requests.create(s, draft(action_kind="skill.activate_knowledge", type=RequestType.SKILL),
                                 from_agent="skill_builder", project_id=project.id, dedupe=False, system=True,
                                 origin=by_project_manager(s, project.id))
    spec = requests.create(s, draft(action_kind="dev.spec_approve"), from_agent="product", project_id=project.id,
                           dedupe=False, system=True, origin=by_project_manager(s, project.id))
    mail = requests.create(s, draft(action_kind="external.email_send"), from_agent="marketing",
                           project_id=project.id, dedupe=False, origin=by_project_manager(s, project.id))

    assert internal.status == research.status == RequestStatus.AUTO_APPROVED.value
    assert spec.status == mail.status == activation.status == RequestStatus.PENDING.value


def test_deriving_again_keeps_the_owners_own_rules_and_replaces_the_derived_ones(s, firm, project_manager):
    first = make_brief(s, firm, rights=[line("l-r1", "정리는 알아서 하세요")])
    project_manager(DoorRuleOut(action="internal.*", decider="project_manager", line_id="l-r1", reason="맡겼습니다"))
    door_rules.derive(first.id)
    roadmap.update_lists(s, firm.id, lang="ko", door_policy=[*s.get(Business, firm.id).door_policy,
                                                  {"action": "research.*", "decider": "owner"}])
    s.commit()

    second = make_brief(s, firm, rights=[line("l-r9", "조사는 알아서 하세요")])
    project_manager(DoorRuleOut(action="research.*", decider="project_manager", line_id="l-r9", reason="맡겼습니다"),
        DoorRuleOut(action="internal.task_create", decider="project_manager", line_id="l-r9", reason="맡겼습니다"))
    door_rules.derive(second.id)

    rules = stored(firm)
    assert set(rules) == {"research.*", "internal.task_create"}
    assert rules["research.*"]["decider"] == "owner" and rules["research.*"].get("source") is None, \
        "the owner's own rule wins over the brief's for the same action"
    assert rules["internal.task_create"]["source"] == "brief:v2:l-r9"


def test_an_older_brief_does_not_overwrite_a_newer_ones_rules(s, firm, project_manager):
    old = make_brief(s, firm, rights=[line("l-r1", "정리는 알아서 하세요")])
    new = make_brief(s, firm, rights=[line("l-r2", "조사는 알아서 하세요")])
    project_manager(DoorRuleOut(action="research.*", decider="project_manager", line_id="l-r2", reason="맡겼습니다"))
    door_rules.derive(new.id)

    project_manager(DoorRuleOut(action="internal.*", decider="project_manager", line_id="l-r1", reason="맡겼습니다"))
    door_rules.derive(old.id)

    assert set(stored(firm)) == {"research.*"}


def test_deriving_twice_stores_the_same_rules(s, firm, project_manager):
    brief = make_brief(s, firm, rights=[line("l-r1", "정리는 알아서 하세요")])
    project_manager(DoorRuleOut(action="internal.*", decider="project_manager", line_id="l-r1", reason="맡겼습니다"))
    door_rules.derive(brief.id)
    first = [(r["action"], r["decider"], r["source"]) for r in stored(firm).values()]
    door_rules.derive(brief.id)
    assert [(r["action"], r["decider"], r["source"]) for r in stored(firm).values()] == first


def test_a_wait_for_usage_defers_the_derivation_and_stores_nothing(s, firm, monkeypatch):
    from datetime import datetime, timedelta, timezone
    brief = make_brief(s, firm, rights=[line("l-r1", "정리는 알아서 하세요")])

    def capped(*a, **kw):
        raise MustWait(datetime.now(timezone.utc) + timedelta(hours=9), "usage_cap_day", "하루 사용 상한")

    monkeypatch.setattr(door_rules, "run_agent", capped)
    job = run_once("derive_door_rules", lambda p: door_rules.derive(p["brief_id"]))

    assert (job.status, job.attempts, job.wait_reason) == ("queued", 0, "usage_cap_day")
    assert job.payload == {"brief_id": brief.id} and stored(firm) == {}


def test_the_worker_knows_the_derivation_job():
    from justshipit.workers import runner
    assert "derive_door_rules" in runner._handlers()


def test_a_business_rules_reach_only_its_own_projects(s, firm, project):
    other_project = projects.create(s, "다른 목표", name="Other", budget_usd=10, start=False)
    s.commit()
    assert other_project.business_id != firm.id
    set_rules(firm, rule("internal.*", "project_manager"))

    mine = requests.create(s, draft(), from_agent="research", project_id=project.id, dedupe=False,
                           origin=by_project_manager(s, project.id))
    theirs = requests.create(s, draft(), from_agent="research", project_id=other_project.id, dedupe=False,
                             origin=by_project_manager(s, other_project.id))

    assert mine.status == RequestStatus.AUTO_APPROVED.value and theirs.status == RequestStatus.PENDING.value


# ------------------------------------------------------------------ the owner is never skipped

def test_the_stock_rules_do_not_reach_a_kind_nobody_listed(s, firm, project):
    set_rules(firm, *door_rules.default_rules("en"))
    kinds = ("payment.charge", "Budget.Spend", "EXTERNAL.EMAIL_SEND", "dev.merge", "ads.run", "external_email.send")

    made = {k: requests.create(s, draft(action_kind=k, title=k), from_agent="marketing", project_id=project.id,
                               dedupe=False, origin=by_project_manager(s, project.id)) for k in kinds}

    for kind, req in made.items():
        assert req.status == RequestStatus.PENDING.value, kind
        assert req.action_kind == "internal.other" and req.action_params["proposed_action_kind"] == kind
        assert (req.response or {}).get("responded_by") != "project_manager (결정 권한)"


def test_internal_other_is_never_decided_by_the_project_manager(s, firm, project):
    set_rules(firm, rule("internal.*", "project_manager"), rule("internal.other", "project_manager"))

    req = requests.create(s, draft(action_kind="internal.other"), from_agent="research", project_id=project.id,
                          origin=by_project_manager(s, project.id))
    s.commit()

    assert (req.status, req.policy_mode) == (RequestStatus.PENDING.value, "approve")
    assert [e for e in events_of(req.id) if e.verb == "door_rule_ignored"]
    assert not [e for e in events_of(req.id) if e.verb == "ceo_decided"]


def test_a_request_the_server_renamed_is_never_the_project_managers_whatever_the_table_allows(s, firm, project, monkeypatch):
    monkeypatch.setattr(policy, "PROJECT_MANAGER_DECIDABLE", policy.PROJECT_MANAGER_DECIDABLE | {"internal.other"})
    set_rules(firm, rule("internal.*", "project_manager"))

    named = requests.create(s, draft(action_kind="internal.other"), from_agent="research", project_id=project.id,
                            dedupe=False, origin=by_project_manager(s, project.id))
    renamed = requests.create(s, draft(action_kind="made.up"), from_agent="research", project_id=project.id,
                              dedupe=False, origin=by_project_manager(s, project.id))
    s.commit()

    assert named.status == RequestStatus.AUTO_APPROVED.value
    assert renamed.status == RequestStatus.PENDING.value and renamed.action_kind == "internal.other"
    ignored = next(e for e in events_of(renamed.id) if e.verb == "door_rule_ignored")
    assert ignored.data["door_rules"][0]["why"] == msg("door.ignored.renamed", "en")


def test_a_project_manager_rule_cannot_approve_work_that_starts_a_follow_up(s, firm, project):
    set_rules(firm, rule("dev.spec_approve", "project_manager"), rule("dev.plan_approve", "project_manager"), rule("project.reassess", "project_manager"),
              rule("skill.activate_knowledge", "project_manager"))
    for kind in ("dev.spec_approve", "dev.plan_approve", "project.reassess", "skill.activate_knowledge"):
        assert policy.door_rule_problem(rule(kind, "project_manager")), kind

    spec = requests.create(s, draft(action_kind="dev.spec_approve"), from_agent="product", project_id=project.id,
                           action_params={"spec_id": 999, "commit_sha": "x"}, system=True, origin=by_project_manager(s, project.id))
    plan = requests.create(s, draft(action_kind="dev.plan_approve"), from_agent="product", project_id=project.id,
                           action_params={"plan_id": 999, "commit_sha": "x"}, system=True, origin=by_project_manager(s, project.id))
    reassess = requests.create(s, draft(action_kind="project.reassess"), from_agent="project_manager", project_id=project.id,
                               system=True, origin=by_project_manager(s, project.id))
    s.flush()

    assert spec.status == plan.status == reassess.status == RequestStatus.PENDING.value
    assert queued_for(s, spec) == [] and queued_for(s, plan) == [] and queued_for(s, reassess) == []


FOLLOW_UPS = [
    ("skill.activate_knowledge", RequestType.SKILL, {"skill_id": 4242}, "skill_activate"),
    ("external.email_send", RequestType.SKILL, {}, "execute_action"),
]


def queued_for(s, req) -> list[str]:
    """The jobs queued after this request was made whose key ends in its id. A job an earlier test left behind can end
    in the same number, since briefs, projects and requests count their ids separately."""
    return [j.kind for j in s.scalars(select(Job).where(Job.idempotency_key.like(f"%:{req.id}"),
                                                        Job.created_at >= req.created_at))]


@pytest.mark.parametrize("kind,type_,params,job", FOLLOW_UPS)
def test_an_approval_by_policy_queues_the_same_work_as_the_owners(s, project, kind, type_, params, job):
    by_owner = requests.create(s, draft(action_kind=kind, type=type_, title="오너가 승인"), from_agent="engineer",
                               project_id=project.id, system=True, action_params=params, dedupe=False,
                               origin=by_project_manager(s, project.id))
    requests.respond(s, by_owner.id, HumanResponse(decision=HumanDecision.APPROVE))
    app_settings.save(s, "policy", {"overrides": {kind: "auto"}}, actor="test")
    s.commit()

    by_policy = requests.create(s, draft(action_kind=kind, type=type_, title="정책이 승인"), from_agent="engineer",
                                project_id=project.id, system=True, action_params=params, dedupe=False,
                                origin=by_project_manager(s, project.id))
    s.flush()

    assert by_policy.status == RequestStatus.AUTO_APPROVED.value
    assert queued_for(s, by_owner) == [job] and queued_for(s, by_policy) == [job]


@pytest.mark.parametrize("kind,type_,params", [
    ("dev.spec_approve", RequestType.APPROVAL, {"spec_id": 4242}),
    ("dev.plan_approve", RequestType.APPROVAL, {"plan_id": 4242, "commit_sha": "x"}),
    ("project.reassess", RequestType.PROPOSAL, {}),
])
def test_work_that_starts_a_follow_up_is_never_approved_by_policy_in_its_production_type(s, project, kind, type_, params):
    app_settings.save(s, "policy", {"overrides": {kind: "auto"}}, actor="test")
    s.commit()

    req = requests.create(s, draft(action_kind=kind, type=type_), from_agent="product", project_id=project.id,
                          system=True, action_params=params, dedupe=False, origin=by_project_manager(s, project.id))
    s.flush()

    assert req.status == RequestStatus.PENDING.value and queued_for(s, req) == []


# ------------------------------------------------------------------ rules kept by hand

@pytest.mark.parametrize("bad", [
    {"id": "d-1", "decider": "project_manager"},
    {"id": "d-2", "action": "internal.task_create", "decider": "Owner"},
    {"id": "d-3", "action": "internal.task_create", "decider": "CEO"},
    {"id": "d-4", "action": "internal.task_create", "decider": None},
    {"id": "d-5", "action": 5, "decider": "project_manager"},
    "internal.task_create",
])
def test_a_malformed_stored_rule_is_ignored_and_recorded_and_never_lowers_the_mode(s, firm, project, bad):
    set_rules(firm, bad)

    req = requests.create(s, draft(), from_agent="research", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()

    assert (req.status, req.policy_mode) == (RequestStatus.PENDING.value, "approve")
    assert [e for e in events_of(req.id) if e.verb == "door_rule_ignored"]


def test_a_door_policy_that_is_not_a_list_is_no_rules(s, firm, project):
    with session_scope() as x:
        x.get(Business, firm.id).door_policy = {"internal.*": "project_manager"}

    assert requests.door_rules(s, project.id) == []
    assert requests.create(s, draft(), from_agent="research", project_id=project.id,
                           origin=by_project_manager(s, project.id)).status == RequestStatus.PENDING.value


# ------------------------------------------------------------------ who wrote a rule

def stored_rows(business) -> list[dict]:
    with session_scope() as s:
        return list(s.get(Business, business.id).door_policy)


def derived(action="internal.task_create", decider="project_manager", origin="brief", source="brief:v1:l-1") -> dict:
    return {"id": f"d-{action}", "action": action, "decider": decider, "note": "n", "source": source, "origin": origin}


NEW_BRIEF = type("NewBrief", (), {"id": 0, "version": 2})


def test_a_rule_the_owner_edits_stays_the_owners_when_the_brief_is_derived_again(s, firm):
    set_rules(firm, derived())
    s.expire_all()

    roadmap.update_lists(s, firm.id, lang="ko", door_policy=[{**derived(), "decider": "owner"}])
    s.flush()
    roadmap.set_derived_door_policy(s, firm.id, [derived(source="brief:v2:l-1")], brief=NEW_BRIEF)
    s.flush()

    (kept,) = s.get(Business, firm.id).door_policy
    assert (kept["decider"], kept["origin"], kept["source"]) == ("owner", "owner", None)


def test_the_client_cannot_name_the_origin_of_a_rule(s, firm):
    roadmap.update_lists(s, firm.id, lang="ko", door_policy=[
        {"action": "research.*", "decider": "owner", "origin": "stock", "source": "default"},
        {"action": "internal.*", "decider": "owner", "origin": "brief", "source": "brief:v9:l-9"}])

    assert {(r["origin"], r["source"]) for r in s.get(Business, firm.id).door_policy} == {("owner", None)}


def test_rules_sent_back_unchanged_stay_derived_and_the_next_derivation_replaces_them(s, firm):
    set_rules(firm, derived("internal.*"), derived("research.*", origin="stock", source="default"))
    s.expire_all()

    roadmap.update_lists(s, firm.id, lang="ko", door_policy=[*stored_rows(firm), {"action": "crm.*", "decider": "owner"}])
    s.flush()
    assert [(r["action"], r["origin"]) for r in s.get(Business, firm.id).door_policy] == [
        ("internal.*", "brief"), ("research.*", "stock"), ("crm.*", "owner")]

    roadmap.set_derived_door_policy(s, firm.id, [], brief=NEW_BRIEF)
    s.flush()
    assert [r["action"] for r in s.get(Business, firm.id).door_policy] == ["crm.*"]


def test_a_row_stored_before_origin_existed_is_read_from_its_source():
    assert roadmap.origin_of({"action": "a", "source": "brief:v1:l-1"}) == "brief"
    assert roadmap.origin_of({"action": "a", "source": "default"}) == "stock"
    assert roadmap.origin_of({"action": "a"}) == "owner"
    assert roadmap.origin_of("not a rule") == "owner"


# ------------------------------------------------------------------ deriving from lines

def test_one_line_hands_the_project_manager_at_most_four_rules(s, firm, project_manager):
    brief = make_brief(s, firm, rights=[line("l-r1", "다 알아서 하세요")])
    project_manager(*(DoorRuleOut(action=k, decider="project_manager", line_id="l-r1", reason="맡겼습니다") for k in sorted(policy.PROJECT_MANAGER_DECIDABLE)))

    door_rules.derive(brief.id)

    assert sum(r["decider"] == "project_manager" for r in stored(firm).values()) == door_rules.MAX_PROJECT_MANAGER_RULES_PER_LINE == 4
    with session_scope() as x:
        problem = x.scalar(select(ActivityEvent).where(ActivityEvent.verb == "door_rules_problem",
                                                       ActivityEvent.business_id == firm.id))
        assert len(problem.data["problems"]) == len(policy.PROJECT_MANAGER_DECIDABLE) - 4
        assert all("at most 4 rules" in p for p in problem.data["problems"])


def test_a_red_line_and_an_unknown_line_leave_a_problem_not_a_rule(s, firm, project_manager):
    brief = make_brief(s, firm, rights=[line("l-r1", "정리는 알아서 하세요")], red=[line("l-x1", "광고는 묻고 하세요")])
    project_manager(DoorRuleOut(action="internal.*", decider="project_manager", line_id="l-made-up", reason="지어낸 줄"),
        DoorRuleOut(action="research.*", decider="project_manager", line_id="l-x1", reason="금지선"))

    door_rules.derive(brief.id)

    assert {a: r["decider"] for a, r in stored(firm).items() if r["source"] != "default"} == {"research.*": "owner"}
    with session_scope() as x:
        problem = x.scalar(select(ActivityEvent).where(ActivityEvent.verb == "door_rules_problem",
                                                       ActivityEvent.business_id == firm.id))
        assert len(problem.data["problems"]) == 2


def test_a_new_brief_takes_back_the_project_managers_derived_rules_until_its_own_are_derived(s, firm, project, project_manager):
    first = make_brief(s, firm, rights=[line("l-r1", "정리는 알아서 하세요")])
    project_manager(DoorRuleOut(action="internal.*", decider="project_manager", line_id="l-r1", reason="맡겼습니다"),
        DoorRuleOut(action="research.*", decider="owner", line_id="l-r1", reason="조사는 오너가 정합니다"))
    door_rules.derive(first.id)
    s.expire_all()
    roadmap.update_lists(s, firm.id, lang="ko", door_policy=[*stored_rows(firm), {"action": "research.web_fetch", "decider": "project_manager"}])
    s.commit()
    assert requests.create(s, draft(), from_agent="research", project_id=project.id,
                           dedupe=False, origin=by_project_manager(s, project.id)).status == RequestStatus.AUTO_APPROVED.value

    second = make_brief(s, firm, rights=[line("l-r9", "조사는 알아서 하세요")])

    rules = stored(firm)
    assert set(rules) == {"research.*", "research.web_fetch"}, "the derived ceo rule went, the owner's rules stayed"
    assert rules["research.*"]["origin"] == "brief" and rules["research.web_fetch"]["origin"] == "owner"
    assert requests.create(s, draft(), from_agent="research", project_id=project.id,
                           dedupe=False, origin=by_project_manager(s, project.id)).status == RequestStatus.PENDING.value
    with session_scope() as x:
        assert x.scalar(select(ActivityEvent).where(ActivityEvent.verb == "door_rules_withdrawn",
                                                    ActivityEvent.business_id == firm.id)) is not None

    project_manager(DoorRuleOut(action="internal.task_create", decider="project_manager", line_id="l-r9", reason="맡겼습니다"))
    door_rules.derive(second.id)
    assert "internal.task_create" in stored(firm) and "internal.*" not in stored(firm)


def test_an_owner_rule_the_policy_no_longer_accepts_does_not_stop_the_derivation(s, firm, project_manager):
    odd = [{**rule("gone.kind", "owner"), "origin": "owner"}, {**rule("dev.merge", "project_manager"), "origin": "owner"},
           {"id": "d-x", "decider": "project_manager"}]
    set_rules(firm, *odd)
    brief = make_brief(s, firm, rights=[line("l-r1", "정리는 알아서 하세요")])
    project_manager(DoorRuleOut(action="internal.task_create", decider="project_manager", line_id="l-r1", reason="맡겼습니다"))

    door_rules.derive(brief.id)

    rows = stored_rows(firm)
    assert {"gone.kind", "dev.merge", "internal.task_create"} <= {r.get("action") for r in rows}
    assert odd[2] in rows, "a row that is not a rule at all is kept as it was"
    with session_scope() as x:
        problem = x.scalar(select(ActivityEvent).where(ActivityEvent.verb == "door_rules_problem",
                                                       ActivityEvent.business_id == firm.id))
        assert len(problem.data["problems"]) == 3


def test_a_skill_the_policy_activates_is_activated_once_by_the_queued_job(s, monkeypatch):
    from justshipit.capability import factory
    from justshipit.contracts.skills import SkillPackageOut, SkillSafetyOut, SkillFileOut
    from justshipit.db.models import Skill

    app_settings.save(s, "policy", {"overrides": {"skill.activate_knowledge": "auto"}}, actor="test")
    prop = dict(name="door-skill", kind="knowledge", target_agents=["research"], problem="p", expected_benefit="b",
                risks=[], evaluation_plan="e")
    skill = Skill(name="door-skill", kind="knowledge", status="CANDIDATE", owner_agent="skill_builder", version=1,
                  target_agents=["research"], proposal=prop)
    s.add(skill)
    s.commit()
    sid = skill.id
    summary = {"n": 1, "promote": True, "baseline_pass": 1, "candidate_pass": 1, "baseline_unmet": 0,
               "candidate_unmet": 0, "baseline_supported": 1, "candidate_supported": 1}

    def agent(role, purpose, prompt, schema, **kw):
        if schema is SkillPackageOut:
            return SkillPackageOut(name="door-skill", description="d", eval_cases=[],
                                   files=[SkillFileOut(path="SKILL.md", content="---\nname: door-skill\n---\n")])
        return SkillSafetyOut(safe=True, issues=[])
    monkeypatch.setattr(factory, "run_agent", agent)
    monkeypatch.setattr(factory, "_sample_tasks", lambda targets, n: [])
    monkeypatch.setattr(factory, "_summarize", lambda cases: summary)
    settle_queue("skill_activate")

    factory.build_and_evaluate(sid)
    run_once("skill_activate", lambda p: factory.activate(p["skill_id"]))

    s.expire_all()
    assert s.get(Skill, sid).status == "ACTIVE"
    jobs = list(s.scalars(select(Job).where(Job.kind == "skill_activate", Job.payload["skill_id"].astext == str(sid))))
    assert [j.status for j in jobs] == ["done"]
