"""Prompt rules adapted from gstack (see THIRD_PARTY_NOTICES.md), pinned the way the agent is told them: the planning
CEO checks whether the owner's number measures the pain and leads with the demand assumption, and the risk reviewer
sorts every gap into the field the server acts on."""
from pathlib import Path

from justshipit.agents.executor import system_prompt
from justshipit.contracts.work import ReviewOut

ROOT = Path(__file__).resolve().parents[1]


def _rule(text: str, start: str) -> str:
    line = text[text.index(start):]
    return line[:line.index("\n")]


def test_the_planning_project_manager_checks_whether_the_owners_number_measures_the_pain_and_keeps_the_summary_short():
    rule = _rule(system_prompt("project_manager"), "- Check the premise before the tasks.")
    assert "whether the owner's main number measures that pain or only stands in for it" in rule
    assert "Signups, views and likes stand in; use, repeat use and payment measure it" in rule
    assert "Keep the owner's number" in rule and "one success criterion that counts the behavior, with a date" in rule
    assert "one or two sentences with the conclusion first" in rule, "goal_interpretation still leads with the answer"


def test_the_planning_project_manager_leads_the_uncertainties_with_the_weakest_demand_assumption_and_gives_a_reason_per_cut():
    text = system_prompt("project_manager")
    first = _rule(text, "- The first `key_uncertainties` line")
    assert "the rest of the work useless if wrong" in first and "names the task or talk that tests it first" in first
    cuts = _rule(text, "- Scope the first version")
    assert "Put each cut in `out_of_scope` with a short reason" in cuts
    assert "Plan no research whose answer would not change what we build or whom we ask" in cuts


def test_the_risk_reviewer_sorts_gaps_into_the_fields_the_server_acts_on():
    text = system_prompt("risk")
    sort = _rule(text, "- Sort every gap before you write it.")
    assert "Blockers go in criteria_unmet, and a blocker makes the verdict rework" in sort
    assert "Shoulds and nits go in notes" in sort and "A tool limit is a note and never a blocker" in sort
    assert "a criterion quietly reworded to fit the work" in sort and "an assumption presented as checked" in sort
    assert "A pass with nothing unmet is a valid review" in sort
    group = _rule(text, "- One item per kind of mistake")
    assert "lists the places" in group and "what goes wrong for the owner if it stays, and the fix" in group
    assert 'marked "(tool limit)", not in criteria_unmet' in text, "the earlier tool-limit rule stays"


def test_the_review_fields_the_sorting_names_exist_on_the_review_contract():
    fields = ReviewOut.model_json_schema()["properties"]
    assert "criteria_unmet" in fields and "notes" in fields


def test_the_gstack_notice_carries_the_mit_text_and_names_both_prompts():
    notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "Copyright (c) 2026 Garry Tan" in notice and "Permission is hereby granted, free of charge" in notice
    assert "prompts/agents/project_manager.md" in notice and "prompts/agents/risk.md" in notice
