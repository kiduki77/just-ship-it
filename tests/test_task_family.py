"""A task's family: the Project Manager names one in the plan, and the server keeps a name off the list as `other`
(docs/design/staffing-structure.md, section 2). The task keeps it (`Task.family`) until its crew is made, and the
crew's facts read it from there."""
import logging
import re
from pathlib import Path

import pytest
from justshipit.contracts.planning import ProjectManagerPlanOut
from justshipit.contracts.work import AgentResultOut, TaskDraft
from justshipit.db.models import Task
from justshipit.db.session import session_scope
from justshipit.services import control, tasks
from justshipit.settings import staffing_config
from justshipit.staffing import checked_family, default_family, task_facts, task_families

PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "agents" / "project_manager.md"


def draft(key="t", owner="research", family=None):
    return TaskDraft(key=key, title=key, owner=owner, objective="o", acceptance_criteria=["c"], depends_on=[],
                     family=family)


def warned(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "family" in r.getMessage()]


# ------------------------------------------------------------------ the draft

def test_a_draft_needs_no_family_so_every_other_author_of_one_still_builds_it():
    assert TaskDraft(key="k", title="t", owner="research", objective="o", acceptance_criteria=[],
                     depends_on=[]).family is None
    assert draft(family="risk_review").family == "risk_review"


def test_the_family_is_optional_in_what_an_author_and_the_plan_may_return():
    for model in (AgentResultOut, ProjectManagerPlanOut):
        assert "family" in str(model.model_json_schema())
    assert "family" not in TaskDraft.model_json_schema()["required"]


# ------------------------------------------------------------------ the boundary

@pytest.mark.parametrize("origin", ["ceo_plan", "follow_up", "decision", "realign"])
def test_a_listed_family_the_project_manager_names_is_kept_and_not_warned_about(s, project, caplog, origin):
    d = draft(family="regulation_check")

    (made,), _ = tasks.create_from_drafts(s, project, [d], origin=origin, plan_round=1)

    assert (made.family, d.family) == ("regulation_check", "regulation_check") and warned(caplog) == []


@pytest.mark.parametrize("origin", ["ceo_plan", "follow_up", "decision", "realign"])
@pytest.mark.parametrize("given", [None, "", "  ", "made_up_family", "Market_Research"])
def test_a_missing_or_unknown_family_from_the_project_manager_becomes_other_with_a_warning(s, project, caplog, origin,
                                                                                         given):
    d = draft(family=given)

    with caplog.at_level(logging.WARNING):
        (made,), _ = tasks.create_from_drafts(s, project, [d], origin=origin, plan_round=1)

    assert (made.family, d.family) == ("other", "other")
    assert len(warned(caplog)) == 1


@pytest.mark.parametrize("origin", ["owner", "idea"])
def test_a_task_from_the_owner_or_an_idea_gets_its_roles_default_without_a_warning(s, project, caplog, origin):
    d = draft(owner="finance")

    with caplog.at_level(logging.WARNING):
        (made,), _ = tasks.create_from_drafts(s, project, [d], origin=origin, plan_round=1)

    assert made.family == d.family == "financial_model" and warned(caplog) == []


def test_an_unknown_family_is_other_even_on_a_path_that_has_no_family_to_give(caplog):
    with caplog.at_level(logging.WARNING):
        assert checked_family("made_up_family", "research", asked=False) == "other"

    assert len(warned(caplog)) == 1


def test_the_family_of_a_task_that_falls_back_to_research_is_judged_with_the_owner_it_got(s, project):
    d = draft(owner="cfo_typo")

    tasks.create_from_drafts(s, project, [d], origin="owner", plan_round=1)

    assert d.family == default_family("research") == "market_research"


def test_the_owner_adding_a_task_still_builds_a_draft_and_gets_a_task(s, project, monkeypatch):
    project.status, project.phase = "RUNNING", "executing"
    s.commit()
    seen = []
    real = tasks.create_from_drafts
    monkeypatch.setattr(tasks, "create_from_drafts", lambda x, p, drafts, **kw: (seen.extend(drafts), real(x, p, drafts, **kw))[1])

    with session_scope() as x:
        t = control.create_task(x, project.id, title="가격표", objective="가격표 수집", owner="marketing",
                                acceptance_criteria=[], depends_on=[])
        made = (t.origin, t.owner)

        stored = x.get(Task, t.id).family

    assert made == ("owner", "marketing")
    assert [(d.owner, d.family) for d in seen] == [("marketing", "customer_channels")]
    assert stored == "customer_channels"


# ------------------------------------------------------------------ the crew reads it

def test_the_crews_facts_read_the_family_the_task_kept(s, project):
    (made,), _ = tasks.create_from_drafts(s, project, [draft(family="regulation_check")], origin="ceo_plan",
                                          plan_round=1)

    assert task_facts(s, made).family == "regulation_check"


def test_a_task_made_with_no_family_reads_as_other(s, project):
    t = Task(project_id=project.id, key="bare", title="t", owner="research", objective="o", acceptance_criteria=["c"])
    s.add(t)
    s.flush()

    assert t.family is None and task_facts(s, t).family == "other"


# ------------------------------------------------------------------ the list

def test_the_plan_prompt_names_every_family_of_the_list_and_nothing_else():
    line = next(x for x in PROMPT.read_text(encoding="utf-8").splitlines() if "a `family`:" in x)
    named = re.split(r",\s*|\s+or\s+", re.search(r"`family`: (.+?)\. Pick", line).group(1))

    assert sorted(named) == sorted(task_families()), "config/staffing.yaml and prompts/agents/project_manager.md name the same list"


def test_every_assignable_role_has_a_default_and_an_unknown_role_gets_other():
    assert {default_family(o) for o in tasks._assignable_owners()} <= set(staffing_config()["families"])
    assert default_family("someone-new") == "other"
