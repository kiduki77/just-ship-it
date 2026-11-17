"""A to-do reads as the action in plain words, under a label for what the owner does, with no codes (persona round 4,
Sam, B-17): "Make monday-support-digest usable (Run it on my computer)" was labelled "Publish · You publish this
yourself", "Ask to Tom: one week of support emails (upload route)" kept the agent's aside, and cards showed REQ-93/94/95/99
chips and "From decision REQ-94"."""
import re

import pytest
from owner_task_kit import approve, tasks_of
from test_usable import _merged, _use_card

from justshipit.contracts.requests import AgentRequestDraft, Handout, HumanDecision, HumanResponse, RequestType, RiskLevel
from justshipit.services import owner_tasks, projects, requests, roadmap, today
from ask_kit import contract, by_project_manager

CODE = re.compile(r"\b(?:REQ|OT|T|EV)-\d+")


def _ask_tom(s, project):
    req = requests.create(s, AgentRequestDraft(
        type=RequestType.APPROVAL, title="Ask to Tom: one week of support emails (upload route)",
        summary="The same ask as Priya's, adjusted for Tom.", rationale="Tom tests the upload route.", options=[],
        recommended_option=None, action_kind="external.interview_outreach", estimated_cost_usd=None,
        risk_level=RiskLevel.LOW, context=contract(),
        handout=Handout(kind="message", to="Tom", body="Hi Tom,\n\nCould you share one week of support emails?")),
        from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    approve(s, req, confirm_text=f"REQ-{req.id}")
    s.commit()
    return req


def test_a_to_do_title_drops_the_agent_s_aside(s, project):
    req = _ask_tom(s, project)
    (task,) = tasks_of(req.id)
    assert task.title == "Ask to Tom: one week of support emails"


def _answer_use(s, name: str, option: str):
    b = roadmap.create_business(s, name=f"Biz {name}", one_liner="weekly drafts")
    project = projects.create(s, "goal", name="Weekly drafts", budget_usd=10, start=False, business=b)
    _merged(s, project, name)
    [card] = _use_card(s, project)
    requests.respond(s, card.id, HumanResponse(decision=HumanDecision.ANSWER, chosen_option=option,
                                               responded_by="owner"))
    s.commit()
    (task,) = tasks_of(card.id)
    return b, card, task


@pytest.mark.parametrize(("option", "label", "reason"), [
    ("Run it on my computer", "Run it yourself", "You run this on your computer"),
    ("Host it on Render", "Publish", "You publish this yourself"),
])
def test_the_to_do_for_the_way_the_owner_picked_is_labelled_for_that_way(s, project, option, label, reason):
    b, card, task = _answer_use(s, f"w-{option[:3].lower()}-{project.id}", option)
    assert task.title == card.title and "(" not in task.title
    assert owner_tasks.view(task, "en")["kind_label"] == label
    item = next(i for i in today.build(s, b.id, lang="en", everything=True)["items"] if i["ref"] == f"OT-{task.id}")
    assert (item["label"], item["meta"]["reason"]) == (label, reason)


def test_the_to_do_names_its_decision_by_title_not_by_code(s, project):
    req = _ask_tom(s, project)
    (task,) = tasks_of(req.id)
    shown = owner_tasks.get(s, task.id, lang="en")
    assert shown["request_title"] == "Ask to Tom: one week of support emails (upload route)"
    assert not [k for k, v in shown.items() if isinstance(v, str) and CODE.search(v) and k != "ref"]
