"""Working back names the plan's own goal. The section's title, in the console and in what the CEO reads, is the goal's
number and noun ("Working back from 5 stores", "10곳에서 거꾸로 계산"), so nobody works back from a number the owner never set.
Round 4 (Sam, B-17): the goal was 5 stores and the CEO wrote "For 100 stores we're still about 94 short"; round 3
(준호): a section titled "100명 역산" over a goal of 10 shops."""
import re

import pytest
from justshipit.contracts.goal import GoalTarget
from justshipit.contracts.kickoff import BriefPatchLine
from justshipit.services import kickoff, roadmap
from test_kickoff import _settled_jobs, api, project_manager, open_session, out, play  # noqa: F401

pytestmark = pytest.mark.usefixtures("strengths_answered")

STORES = "Success: 5 stores reading the digest 3 Mondays in a row, within 6 weeks of launch."
SHOPS = "8주 안에 동네 가게 10곳이 두 번 이상 주문하면 성공이에요"


def goal_line(text: str, provenance: str = "said", target: dict | None = None) -> dict:
    return {"id": "l-goal", "text": text, "provenance": provenance, "source": "kickoff:1:3", "target": target}


def title(sections: list[dict], lang) -> str:
    return next(x for x in roadmap.section_views(sections, lang=lang) if x["key"] == "reverse_100")["title"]


@pytest.mark.parametrize(("line", "en", "ko"), [
    (goal_line(STORES, target={"number": 5, "unit": "stores", "measure": "repeat"}),
     "Working back from 5 stores", "목표에서 거꾸로 계산"),
    (goal_line(SHOPS), "Working back from your goal", "10곳에서 거꾸로 계산"),
    (goal_line(STORES, provenance="guess"), "Working back from your goal", "목표에서 거꾸로 계산"),
])
def test_the_working_back_title_names_the_plans_own_goal_in_the_owners_language(line, en, ko):
    """A title is in the owner's language, so a goal the plan counts in the other one is "your goal" there."""
    sections = [{"key": "goal_deadline", "title": "목표와 기한", "lines": [line]}]
    assert (title(sections, "en"), title(sections, "ko")) == (en, ko)


@pytest.mark.parametrize(("idea", "line", "target", "names"), [
    (f"A Monday digest of a store's support emails. {STORES}", STORES, GoalTarget(number=5, unit="stores", measure="repeat"),
     "5 stores에서 거꾸로 계산 [reverse_100]"),
    (f"동네 가게 주문 정리 앱. {SHOPS}", SHOPS, None, "10곳에서 거꾸로 계산 [reverse_100]"),
])
def test_the_project_manager_reads_the_working_back_section_under_the_plans_goal(api, project_manager, idea, line, target, names):
    sid = open_session(api, name="Working back", one_liner=idea)
    said = BriefPatchLine(section="goal_deadline", text=line, provenance="said", owner_quote=line, replaces_line_id="",
                          target=target)
    play(api, project_manager, sid, out(section="goal_deadline").model_copy(update={"brief_patch": [said]}))
    project_manager.says(out(section="owner_time"))
    kickoff.run_turn(sid, 2)

    prompt = project_manager.prompts[-1]
    assert names in prompt
    assert "100명" not in prompt and not re.search(r"(?<!reverse_)\b100\b", prompt)


def test_the_kickoff_prompt_works_back_from_no_fixed_number():
    system = kickoff._system("en")
    assert not re.search(r"(?<!reverse_)100", system), "the CEO's rules name no number to work back from"
