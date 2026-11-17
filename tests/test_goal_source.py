"""The business's goal has one source, the approved plan's goal line: its typed target when it has one, else the
numbers its words state, else the line itself for the owner to check. An answer to a card that amends a plan line
writes the next plan version, so the dashboard, the numbers and the project's goal follow what the owner chose."""
import pytest
from justshipit.api.app import app
from justshipit.contracts.kickoff import BriefPatchLine, KickoffTurnOut
from justshipit.contracts.requests import (
    AgentRequestDraft,
    CardOption,
    DecisionCard,
    HumanDecision,
    HumanResponse,
    RequestType,
    RiskLevel,
)
from justshipit.db.models import BusinessBrief, KickoffSession
from justshipit.db.session import session_scope
from justshipit.services import completion, kickoff, projects, requests, roadmap, today
from bet_loop_kit import install_people, line, reset_world
from fastapi.testclient import TestClient
from sqlalchemy import select, text

import cycle_kit
from ask_kit import contract, by_project_manager

pytestmark = pytest.mark.usefixtures("strengths_answered")

MAIN = "Main number: 500 signups in the first month after launch · your target"
NEW_MAIN = "Main number: 150–250 signups in the first month after launch, 500 as a stretch · your choice"
CAFES = ("Main number: 5 cafés you don't know personally that are still opening the app and recounting at least once "
         "a week after 4 weeks (the came-back step) · your own goal")


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    yield
    reset_world()
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL, lease_token = NULL "
                       "WHERE kind = 'run_project' AND status = 'queued'"))
        s.execute(text("DELETE FROM jobs WHERE kind IN ('kickoff_turn', 'derive_door_rules', 'idea_review')"))


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def business(s, *goal_lines: dict):
    b = roadmap.create_business(s, name="Day planner", one_liner="A simple daily planner for people who juggle tools")
    sections = [{"key": "customer_problem", "title": "Who and what", "lines": [line("l-p", "People who juggle five tools")]},
                {"key": "goal_deadline", "title": "Goal and deadline", "lines": list(goal_lines)}]
    roadmap.new_brief_version(s, b.id, sections=sections, assumptions=[], author="project_manager", approved=True, lang="en")
    s.commit()
    return b


def option(label: str, **kw) -> CardOption:
    return CardOption(**{"label": label, "verdict": "answer", "summary": f"We judge the launch by {label}",
                         "evidence_ids": [], "risks": [], "recommended": False, **kw})


def target_question(amends: str | None, **kw) -> AgentRequestDraft:
    card = DecisionCard(
        headline="Keep 500 signups as the target, or plan for 150–250?", owner_does="Pick one (about 1 min)",
        why="Our middle estimate is about 190 signups", metrics=[], recommendation="I'd plan for 150–250.",
        reversible=True, amends=amends,
        options=[option("Keep 500 as the target"),
                 option("Plan for 150–250", recommended=True,
                        line={"text": NEW_MAIN, "target": {"number": 150, "unit": "signups", "measure": None}})])
    return AgentRequestDraft(**{"type": RequestType.QUESTION, "title": "Signup target for the first month",
                                "summary": "Keep 500 or plan for 150–250?", "rationale": "The estimate is about 190",
                                "options": [], "recommended_option": None, "action_kind": None,
                                "estimated_cost_usd": None, "risk_level": RiskLevel.LOW, "context": contract(),
                                "card": card, **kw})


def versions(s, bid: int) -> list[int]:
    return list(s.scalars(select(BusinessBrief.version).where(BusinessBrief.business_id == bid)
                          .order_by(BusinessBrief.version)))


def goal_line(s, bid: int) -> dict:
    return next(x for x in roadmap.lines_by_key(roadmap.approved_brief(s, bid).sections)["goal_deadline"]
                if x["id"] == "l-main")


def test_the_owners_answer_to_a_card_that_amends_the_goal_line_is_the_dashboards_goal(s, api):
    """Daniel answered "Plan for 150–250" to REQ-61 and the dashboard still said 500 signups was his target: an
    answer only went into the project's notes, and nothing wrote it into the plan the goal is read from."""
    b = business(s, line("l-main", MAIN))
    p = cycle_kit.first_project(s, b.id, lang="en")
    req = requests.create(s, target_question("l-main"), from_agent="project_manager", project_id=p.id, origin=by_project_manager(s, p.id))
    s.commit()
    assert "500" in today.number(s, b.id, "en")["goal"]["text"]
    choices = api.get(f"/api/requests/{req.id}").json()["report"]["choices"]
    assert [c["plan_line"] for c in choices] == [None, NEW_MAIN], "the card shows what the plan line becomes"

    requests.respond(s, req.id, HumanResponse(decision=HumanDecision.ANSWER, chosen_option="Plan for 150–250"))
    s.commit()

    goal = today.number(s, b.id, "en")["goal"]
    assert (goal["text"], goal["number"], goal["unit"]) == (NEW_MAIN, 150, "signups")
    assert api.get(f"/api/businesses/{b.id}/numbers").json()["completion"]["goals"][0]["text"] == NEW_MAIN
    assert versions(s, b.id) == [1, 2] and roadmap.approved_brief(s, b.id).version == 2
    amended = goal_line(s, b.id)
    assert (amended["source"], amended["provenance"], amended["quote"]) == (
        f"request:{req.id}", "said", "Plan for 150–250")
    s.refresh(p)
    assert "150–250" in p.goal and "500 signups in the first" not in p.goal, "the project's goal is the plan's, not a copy"

    with pytest.raises(requests.ResponseRejected):
        requests.respond(s, req.id, HumanResponse(decision=HumanDecision.ANSWER, chosen_option="Plan for 150–250"))
    s.rollback()
    roadmap.amend_line(s, b.id, "l-main", text=NEW_MAIN, target={"number": 150, "unit": "signups", "measure": None},
                       source=f"request:{req.id}", quote="Plan for 150–250", lang="en")
    s.commit()
    assert versions(s, b.id) == [1, 2], "the same answer never writes another plan version"


def test_picking_the_option_that_keeps_the_line_leaves_the_plan_alone(s):
    b = business(s, line("l-main", MAIN))
    p = cycle_kit.first_project(s, b.id, lang="en")
    req = requests.create(s, target_question("l-main"), from_agent="project_manager", project_id=p.id, origin=by_project_manager(s, p.id))
    requests.respond(s, req.id, HumanResponse(decision=HumanDecision.ANSWER, chosen_option="Keep 500 as the target"))
    s.commit()
    assert versions(s, b.id) == [1]
    assert today.number(s, b.id, "en")["goal"]["number"] == 500


def test_a_pick_the_plan_cannot_take_now_is_refused_not_recorded_as_answered(s):
    """The owner picked "Plan for 150-250" while the kickoff chat was still open: the plan refused the new version,
    the refusal went to a log, and the card read as answered while the plan and goal still said 500."""
    b = business(s, line("l-main", MAIN))
    p = cycle_kit.first_project(s, b.id, lang="en")
    req = requests.create(s, target_question("l-main"), from_agent="project_manager", project_id=p.id, origin=by_project_manager(s, p.id))
    s.add(KickoffSession(business_id=b.id, status="WAITING_OWNER"))
    s.commit()

    with pytest.raises(requests.ResponseRejected) as refused:
        requests.respond(s, req.id, HumanResponse(decision=HumanDecision.ANSWER, chosen_option="Plan for 150–250"))
    s.rollback()

    words = refused.value.words("en")
    assert "Plan for 150–250" in words and "kickoff chat isn't finished" in words, words
    assert s.get(type(req), req.id).status == "PENDING" and versions(s, b.id) == [1]
    requests.respond(s, req.id, HumanResponse(decision=HumanDecision.ANSWER, answer="Let's talk about it first"))
    s.commit()
    assert s.get(type(req), req.id).status == "ANSWERED", "an answer in the owner's own words still goes through"


def test_a_pick_whose_plan_line_is_gone_is_refused(s):
    b = business(s, line("l-main", MAIN))
    p = cycle_kit.first_project(s, b.id, lang="en")
    req = requests.create(s, target_question("l-main"), from_agent="project_manager", project_id=p.id, origin=by_project_manager(s, p.id))
    sections = [{"key": "customer_problem", "title": "Who and what", "lines": [line("l-p", "People who juggle five tools")]},
                {"key": "goal_deadline", "title": "Goal and deadline", "lines": [line("l-other", MAIN)]}]
    roadmap.new_brief_version(s, b.id, sections=sections, assumptions=[], author="owner", approved=True, lang="en")
    s.commit()

    with pytest.raises(requests.ResponseRejected) as refused:
        requests.respond(s, req.id, HumanResponse(decision=HumanDecision.ANSWER, chosen_option="Plan for 150–250"))
    s.rollback()

    assert "no longer in your approved plan" in refused.value.words("en")
    assert s.get(type(req), req.id).status == "PENDING" and versions(s, b.id) == [1, 2]


@pytest.mark.parametrize("amends", ["l-nope", "l-guess"])
def test_a_card_can_amend_only_a_settled_line_of_the_approved_plan(s, amends):
    b = business(s, line("l-main", MAIN), line("l-guess", "About 300 from Product Hunt", "guess"))
    p = cycle_kit.first_project(s, b.id, lang="en")
    req = requests.create(s, target_question(amends), from_agent="project_manager", project_id=p.id, origin=by_project_manager(s, p.id))
    card = req.action_params["card"]
    assert card["amends"] is None and [o["line"] for o in card["options"]] == [None, None]
    requests.respond(s, req.id, HumanResponse(decision=HumanDecision.ANSWER, chosen_option="Plan for 150–250"))
    s.commit()
    assert versions(s, b.id) == [1]


def test_a_typed_target_is_the_goal_whatever_words_the_line_counts_in(s):
    """Alex's plan said "5 cafés ... recounting at least once a week (the came-back step)", and the dashboard showed
    the company's 10 outside users: "cafés" is not a word the goal parser knows."""
    b = business(s, {**line("l-main", CAFES), "target": {"number": 5, "unit": "cafés", "measure": "repeat"}})
    install_people(used=(2, 0))
    got = completion.check(s, b.id, lang="en")
    assert got["targets_from"] == "brief"
    assert [(g["text"], g["number"], g["unit"], g["measure"], g["current"]) for g in got["goals"]] == [
        (CAFES, 5, "cafés", "repeat", 2)]


def test_a_settled_goal_line_with_no_number_the_company_can_read_is_still_the_plans_goal(s, api):
    b = business(s, line("l-main", "Main number: a handful of cafés still using it after a month · your own goal"),
                 line("l-dl", "Deadline: by mid-December, before the holiday rush · your own goal"))
    install_people(used=(2, 0))
    got = completion.check(s, b.id, lang="en")
    assert got["targets_from"] == "brief", "never 'the plan has none' when the plan has a goal line"
    assert [(g["text"], g["number"], g["current"]) for g in got["goals"]] == [
        ("Main number: a handful of cafés still using it after a month · your own goal", None, None)]
    assert got["done"] is False
    number = today.number(s, b.id, "en")
    assert number["goal_from_brief"] is True and number["goal"]["current"] is None
    assert number["goal"]["text"].startswith("Main number: a handful")


def test_a_business_without_an_approved_plan_gets_the_companys_default(s):
    b = roadmap.create_business(s, name="No plan yet", one_liner="x")
    s.commit()
    assert completion.check(s, b.id, lang="en")["targets_from"] == "default"
    assert today.number(s, b.id, "en")["goal_from_brief"] is False


def test_a_line_without_a_target_keeps_the_numbers_its_words_state(s):
    b = business(s, line("l-main", "12 users used it twice or more, and talk to 6 of them"))
    got = completion.check(s, b.id, lang="en")
    assert [(g["number"], g["measure"]) for g in got["goals"]] == [(12, "repeat"), (6, "repeat_talked")]


# ---------------------------------------------------------------- the kickoff writes the target

class _ProjectManager:
    def __init__(self, out: KickoffTurnOut):
        self.out = out

    def __call__(self, *a, **kw):
        return self.out


IDEA = "A stock timer for small cafés. Success is 5 cafés still recounting every week after 4 weeks"


def _kickoff_turn(api, monkeypatch, target: dict | None) -> list[dict]:
    r = api.post("/api/businesses", json={"name": "Bean timer", "one_liner": IDEA})
    assert r.status_code == 200, r.text
    sid = r.json()["session"]["id"]
    patch = BriefPatchLine(section="goal_deadline", text=CAFES, provenance="said",
                           owner_quote="Success is 5 cafés still recounting every week after 4 weeks",
                           replaces_line_id="", target=target)
    monkeypatch.setattr(kickoff, "run_agent", _ProjectManager(KickoffTurnOut(
        kind="question", section="owner_time", text="How many hours a week can you give it?", chips=[],
        brief_patch=[patch], done=False, done_reason="")))
    kickoff.run_turn(sid, 1)
    view = api.get(f"/api/kickoff/{sid}").json()
    return next(sec for sec in view["brief"]["sections"] if sec["key"] == "goal_deadline")["lines"]


def test_the_kickoff_keeps_the_target_the_project_manager_types_on_the_goal_line(api, monkeypatch):
    lines = _kickoff_turn(api, monkeypatch, {"number": 5, "unit": "cafés", "measure": "repeat"})
    assert [x.get("target") for x in lines] == [{"number": 5, "unit": "cafés", "measure": "repeat"}]


def test_the_kickoff_drops_a_target_whose_number_the_line_does_not_say(api, monkeypatch):
    lines = _kickoff_turn(api, monkeypatch, {"number": 50, "unit": "cafés", "measure": "repeat"})
    assert [x.get("target") for x in lines] == [None]
