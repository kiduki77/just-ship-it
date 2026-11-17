"""Rows written before the CEO became the Project Manager keep the old id (agents/roles.py). Each reader that
meets one still treats it as the Project Manager: door rules apply, a card it decided reads as its decision, an
owner's stop holds it, and the owner sees its name."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from justshipit.agents import roles
from justshipit.contracts.requests import PolicyMode, RequestType, RiskLevel
from justshipit.db.models import AgentRequest, Stop
from justshipit.i18n import glossary, msg
from justshipit.services import policy, requests, stops
from justshipit.services.roadmap import DoorRule

FORMER = roles.FORMER_PROJECT_MANAGER
# `response.responded_by` as rows decided under a decision rule before the rename hold it
FORMER_DECIDED = f"{FORMER} (결정 권한)"
WEB = Path(__file__).resolve().parents[1] / "web"


def test_an_old_actor_has_the_project_managers_name():
    for lang in ("en", "ko"):
        assert glossary.role(FORMER, lang) == glossary.role("project_manager", lang) != FORMER


def test_an_old_door_rule_still_lets_the_project_manager_decide():
    old = {"action": "research.web_search", "decider": FORMER}
    verdict = policy.decide(RequestType.APPROVAL, "research.web_search", RiskLevel.LOW, None, [old])
    assert (verdict.mode, verdict.rule["decider"], verdict.ignored) == (PolicyMode.AUTO, "project_manager", ())
    assert policy.door_rule_problem(old) is None
    assert DoorRule.model_validate(old).decider == "project_manager", "the owner's old rule outlives a new brief"


def test_a_card_decided_under_a_rule_before_the_rename_reads_as_the_project_managers_decision():
    old = AgentRequest(status="AUTO_APPROVED", response={"decision": "approve", "responded_by": FORMER_DECIDED})
    by_policy = AgentRequest(status="AUTO_APPROVED", response={"decision": "approve", "responded_by": "policy"})
    assert requests.decided_by_project_manager(old)
    assert not requests.decided_by_project_manager(by_policy)


def test_a_card_withdrawn_before_the_rename_says_the_project_manager_withdrew_it():
    old = AgentRequest(status="CANCELLED", response={"decision": "withdrawn", "by": FORMER, "note": "old copy"})
    assert requests.closed_why(old, "en") == msg("request.closed.withdrawn_ceo", "en", why="old copy")


def test_an_owners_stop_from_before_the_rename_holds_the_project_manager_until_resumed(s):
    s.add(Stop(target=f"agent:{FORMER}", stopped_by="owner"))
    s.flush()
    held = stops.read(s)
    assert held.holds("project_manager") and held.holds_kind("kickoff_turn")
    stops.resume_agent(s, "project_manager")
    assert not stops.read(s).agents, "resuming the Project Manager lifts the stop the owner put on the CEO"
    s.rollback()


@pytest.mark.skipif(shutil.which("node") is None or not (WEB / "node_modules" / "rolldown").exists(),
                    reason="needs node and web/node_modules")
def test_the_console_names_an_old_responder_and_actor_as_the_project_manager():
    ids = [FORMER, FORMER_DECIDED, "project_manager", roles.PROJECT_MANAGER + FORMER_DECIDED[len(FORMER):]]
    out = subprocess.run(["node", "../tests/console_run/run.mjs", "src/labels.ts", "agentName", *ids],
                         cwd=WEB, capture_output=True, text=True, encoding="utf-8", check=True)
    old_name, old_decided, name, decided = json.loads(out.stdout)
    assert (old_name, old_decided) == (name, decided)
    assert decided != FORMER_DECIDED, "the stored string is labelled, not shown as it is"
