"""Tool Gateway policy. Decides how much human involvement an action needs.

Policy is evaluated in application code from config files and owner settings the agents cannot write. A request
can only make policy stricter than the action's configured mode, never looser. The owner's per-action overrides
(Settings) replace the file's mode but never go below `app_settings.floor()`, which is checked here too in case a
stored row predates a floor.

A business's door rules (`Business.door_policy`) move the mode within what is reversible. An `owner` rule raises
auto to approve. A `project_manager` rule lowers approve to auto, so the CEO decides and the activity log records it, but only
for a kind in `PROJECT_MANAGER_DECIDABLE`. Everything else, including a kind this file does not know, is the owner's whatever
a rule says, so a new action kind is the owner's until someone adds it to that table.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from justshipit.agents import roles
from justshipit.contracts.requests import POLICY_ORDER, PolicyMode, RequestType, RiskLevel
from justshipit.i18n.said import Said, said
from justshipit.services import app_settings
from justshipit.settings import company_config, tool_policy


# The only action kinds a `project_manager` door rule may lower. A kind belongs here only when approving it sends nothing out,
# spends nothing and starts no follow-up job: it has no connector in the Tool Gateway and no entry in
# `requests._effect`. tests/test_door_policy.py fails when a member gains either.
PROJECT_MANAGER_DECIDABLE: frozenset[str] = frozenset({
    "research.web_search", "research.web_fetch", "internal.report_draft", "internal.task_create",
    "internal.meeting_call", "skill.create_candidate",
})
DECIDERS = ("owner", "project_manager")
# A question needs the owner's answer and an escalation is what the agents could not settle: no rule decides them. A
# development ask only starts the spec (`dev.implementation_start`); the build waits for the owner's `dev.spec_approve`.
ASKS_THE_OWNER = (RequestType.QUESTION, RequestType.ESCALATION)


def no_rule_spend(action_kind: str | None, estimated_cost_usd: float | None) -> bool:
    """Money goes out on an action the company has no rule of its own for: an unknown name collapses to `*.other`
    (`requests.normalize_action`), an agent's `spend.purchase` to `internal.other`. Approving one takes the decision's
    number typed in, and the payment becomes the owner's to-do."""
    return bool(action_kind) and action_kind.endswith(".other") and (estimated_cost_usd or 0) > 0


def matches(pattern: str, action_kind: str) -> bool:
    """A door rule names one action kind or a family: `internal.*` covers every kind that starts with `internal.`."""
    return action_kind == pattern or (pattern.endswith(".*") and action_kind.startswith(pattern[:-1]))


def kinds_covered(pattern: str) -> list[str]:
    return sorted(kind for kind in tool_policy() if matches(pattern, kind))


def project_manager_kinds_covered(pattern: str) -> list[str]:
    """The kinds a `project_manager` rule with this pattern can ever decide: a family reaches only its `PROJECT_MANAGER_DECIDABLE` members."""
    return [kind for kind in kinds_covered(pattern) if kind in PROJECT_MANAGER_DECIDABLE]


def current_rule(rule: Any) -> Any:
    """A stored rule with the decider's id of today, for a rule written before the Project Manager was renamed."""
    if isinstance(rule, dict) and rule.get("decider") == roles.FORMER_PROJECT_MANAGER:
        return {**rule, "decider": roles.PROJECT_MANAGER}
    return rule


def well_formed(rule: Any) -> bool:
    return (isinstance(rule, dict) and isinstance(rule.get("action"), str) and bool(rule["action"])
            and rule.get("decider") in DECIDERS)


def door_rule_problem(rule: dict) -> Said | None:
    """Why this door rule cannot be stored, to be put in the owner's words (`i18n.said.say`), or None."""
    rule = current_rule(rule)
    if not well_formed(rule):
        return said("door.problem.malformed")
    if not kinds_covered(rule["action"]):
        return said("door.problem.unknown_kind", action=rule["action"])
    if rule["decider"] == "project_manager" and not project_manager_kinds_covered(rule["action"]):
        return said("door.problem.not_ceo", action=rule["action"], kinds=", ".join(sorted(PROJECT_MANAGER_DECIDABLE)))
    return None


def _specificity(rule: dict) -> tuple[bool, int]:
    """An exact name over a family, and a longer family over a shorter one."""
    return (not rule["action"].endswith(".*"), len(rule["action"]))


def _door_rule_for(action_kind: str, rules: Sequence[Any], *,
                   project_manager_may_decide: bool) -> tuple[dict | None, list[tuple[Any, Said]]]:
    """The rule that governs the action, and the rules ignored on the way with the reason. Among every rule that
    covers the action, exact or family, an `owner` rule beats a `project_manager` rule; specificity only chooses between rules
    with the same decider. A `project_manager` rule governs only a `PROJECT_MANAGER_DECIDABLE` kind of a request nobody renamed."""
    rules = [current_rule(r) for r in rules]
    ignored: list[tuple[Any, Said]] = [(r, said("door.ignored.malformed")) for r in rules if not well_formed(r)]
    hits = [r for r in rules if well_formed(r) and matches(r["action"], action_kind)]
    if owners := [r for r in hits if r["decider"] == "owner"]:
        return max(owners, key=_specificity), ignored
    if project_managers := [r for r in hits if r["decider"] == "project_manager"]:
        best = max(project_managers, key=_specificity)
        if project_manager_may_decide and action_kind in PROJECT_MANAGER_DECIDABLE:
            return best, ignored
        ignored.append((best, said("door.ignored.renamed" if not project_manager_may_decide else "door.ignored.not_ceo")))
    return None, ignored


def _stricter(a: PolicyMode, b: PolicyMode) -> PolicyMode:
    return a if POLICY_ORDER.index(a) >= POLICY_ORDER.index(b) else b


def _resolve(action_kind: str, overrides: dict[str, PolicyMode]) -> PolicyMode:
    base = PolicyMode(tool_policy().get(action_kind, PolicyMode.STRONG_APPROVE))
    if action_kind not in tool_policy() or action_kind not in overrides:
        return base
    return _stricter(overrides[action_kind], app_settings.floor(base))


def action_mode(action_kind: str | None) -> PolicyMode | None:
    if not action_kind:
        return None
    return _resolve(action_kind, app_settings.effective().policy.overrides)


def modes(overrides: dict[str, PolicyMode] | None = None) -> dict[str, PolicyMode]:
    """Every configured action kind with the mode the server enforces for it now, or under these owner overrides."""
    overrides = app_settings.effective().policy.overrides if overrides is None else overrides
    return {kind: _resolve(kind, overrides) for kind in tool_policy()}


@dataclass(frozen=True)
class Verdict:
    mode: PolicyMode
    rule: dict | None = None                       # the door rule that changed the mode
    ignored: tuple[tuple[Any, Said], ...] = ()     # door rules with no effect, each with the reason


def decide(request_type: RequestType, action_kind: str | None, risk: RiskLevel, estimated_cost_usd: float | None,
           door_rules: Sequence[Any] = (), *, project_manager_may_decide: bool = True) -> Verdict:
    """`project_manager_may_decide` is False for a request whose action the server renamed: nothing the agent named is known."""
    defaults = company_config()["autonomy"]["default_by_request_type"]
    mode = PolicyMode(defaults.get(request_type.value, PolicyMode.APPROVE))
    if (am := action_mode(action_kind)) is not None:
        mode = _stricter(mode, am)
    applied = None
    ignored: list[tuple[Any, str]] = []
    if action_kind:
        rule, ignored = _door_rule_for(action_kind, door_rules, project_manager_may_decide=project_manager_may_decide)
        if rule and rule["decider"] == "owner":
            if mode == PolicyMode.AUTO:
                mode, applied = PolicyMode.APPROVE, rule
        elif rule and rule["decider"] == "project_manager":
            if (mode == PolicyMode.APPROVE and request_type not in ASKS_THE_OWNER
                    and action_kind not in app_settings.effective().policy.overrides):
                mode, applied = PolicyMode.AUTO, rule
    if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        mode = _stricter(mode, PolicyMode.APPROVE)
    if risk == RiskLevel.CRITICAL:
        mode = _stricter(mode, PolicyMode.STRONG_APPROVE)
    if estimated_cost_usd and estimated_cost_usd > 0:
        mode = _stricter(mode, PolicyMode.APPROVE)
    if applied and applied["decider"] == "project_manager" and mode != PolicyMode.AUTO:
        applied = None
    return Verdict(mode, applied, tuple(ignored))


def evaluate(request_type: RequestType, action_kind: str | None, risk: RiskLevel,
             estimated_cost_usd: float | None, door_rules: Sequence[Any] = ()) -> PolicyMode:
    return decide(request_type, action_kind, risk, estimated_cost_usd, door_rules).mode
