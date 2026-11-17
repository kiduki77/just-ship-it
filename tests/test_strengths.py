"""What the owner is good at changes what the company asks, shows, hands over and recommends (persona round 3: Alex
read the diffs himself, 박정숙 could not follow "CPO", Maya rewrote the copy herself). The owner answers once in
Settings > About you; every agent call reads it, and each decision card opens its detail where the owner is strong."""
from collections.abc import Iterator

import pytest
from justshipit.agents import executor
from justshipit.contracts.kickoff import KickoffTurnOut
from justshipit.contracts.planning import ProjectManagerPlanOut, FinalReportOut, SectionAreaOut
from justshipit.db.models import AppSetting, Project, Task
from justshipit.db.session import session_scope
from justshipit.runtime.base import LLMResult
from justshipit.services import app_settings, kickoff, requests
from pydantic import ValidationError
from sqlalchemy import delete
from test_analysis_shape import _report
from ask_kit import by_project_manager
from test_decision_card import api, card, draft, read  # noqa: F401 (api is a fixture)

pytestmark = pytest.mark.usefixtures("owner_asked")


KICKOFF_TURN = {"kind": "question", "section": "path", "text": "q", "chips": [], "brief_patch": [], "done": False,
                "done_reason": ""}
PLAN = {"goal_interpretation": "g", "deliverables": [], "success_criteria": [], "out_of_scope": [],
        "key_uncertainties": [], "stop_rule": "", "build": None, "tasks": [], "requests": []}


@pytest.fixture
def owner_is() -> Iterator:
    """Settings > About you for one test, back to never asked after it."""
    def wipe():
        with session_scope(lease_checked=False) as s:
            s.execute(delete(AppSetting).where(AppSetting.section == "owner"))

    def choose(strengths: list[str] | None) -> None:
        with session_scope() as s:
            app_settings.save(s, "owner", {"strengths": strengths}, actor="owner")
    wipe()
    yield choose
    wipe()


@pytest.fixture
def sent(monkeypatch) -> list[str]:
    """The system prompts the agents were called with; each call answers with `output`."""
    prompts: list[str] = []

    def run(call):
        prompts.append(call.system_prompt)
        out = KICKOFF_TURN if call.agent == "project_manager" and "running the kickoff chat" in call.system_prompt else PLAN
        return LLMResult(call_id=call.call_id, provider=call.provider, model="m", output=out, input_tokens=1,
                         output_tokens=1, cached_tokens=0, cost_usd=0.0, duration_ms=1, session_id=None)
    monkeypatch.setattr(executor, "run_with_retry", run)
    return prompts


def kickoff_turn(sent: list[str]) -> str:
    executor.run_agent("project_manager", "kickoff", "## Progress\n- turn 2", KickoffTurnOut, project_id=None,
                       system_override=kickoff._system("en"))
    return sent[-1]


def test_the_owner_answers_once_and_none_yet_is_an_answer(owner_is):
    assert app_settings.reader()[1] is None, "never asked"
    owner_is(["design", "software", "software"])
    assert app_settings.reader()[1] == ["software", "design"], "kept once each, in the table's order"
    owner_is([])
    assert app_settings.reader()[1] == [], "none of these yet is not never asked"
    with session_scope() as s, pytest.raises(ValidationError):
        app_settings.save(s, "owner", {"strengths": ["accounting"]}, actor="owner")


def test_the_kickoff_project_manager_asks_a_developer_deeper_and_gives_a_newcomer_defaults(owner_is, sent):
    owner_is(["software"])
    developer = kickoff_turn(sent)
    owner_is(["marketing"])
    marketer = kickoff_turn(sent)

    assert "- Strong, building software: ask what they have built and where they like to host" in developer
    assert "its code report answers the stack" in developer, "an imported product's stack is not asked again"
    assert "never ask them a technical question" not in developer
    assert "- New to building software: pick the stack and hosting yourself" in marketer
    assert "never ask them a technical question" in marketer
    assert "- Strong, marketing and sales: ask which channels they have run, what a customer cost them" in marketer
    assert "in the kickoff, the default is the first example answer" in marketer, "a default fits the chips"
    assert developer.index("What the owner is good at") < developer.index("## Output language"), \
        "the language rule stays last"


def test_an_owner_never_asked_gets_the_prompt_as_it_was(owner_is, sent):
    assert "What the owner is good at" not in kickoff_turn(sent)


def test_a_build_in_the_engineers_container_hands_the_owner_nothing(owner_is):
    owner_is(["software"])
    built = executor.with_owner_rules("engineer prompt", engineering=True)
    assert "What the owner is good at" not in built and "## Output language" in built


def test_the_plan_gives_a_marketer_the_outreach_and_drafts_it_for_everyone_else(owner_is, sent):
    owner_is(["marketing"])
    executor.run_agent("project_manager", "plan", "p", ProjectManagerPlanOut, project_id=None)
    marketer = sent[-1]
    owner_is([])
    executor.run_agent("project_manager", "plan", "p", ProjectManagerPlanOut, project_id=None)
    newcomer = sent[-1]

    assert "give them the outreach and the copy to write or rewrite" in marketer
    assert "choose the channels and draft every message yourself" in newcomer
    assert "The owner said they are new to all of these areas." in newcomer
    for prompt in (marketer, newcomer):
        assert "leave real choices open with their trade-offs" in prompt, "strong: options with trade-offs"
        assert "recommend one option with a one-line why" in prompt, "new: one recommendation"


def merge_card(s, project):
    """A merge card as the build raises it: the server's own action, which no agent draft may carry."""
    req = requests.create(s, draft(card=card(), title="머지"), from_agent="engineer", project_id=project.id,
                          origin=by_project_manager(s, project.id))
    req.action_kind = "dev.merge"
    s.commit()
    return req


def test_a_merge_card_opens_its_code_for_a_developer_and_folds_it_for_a_shop_owner(s, project, api, owner_is):
    req = merge_card(s, project)
    assert read(api, req)["fit"] == {"strong": [], "weak": []}, "never asked: the card reads as it always did"

    owner_is(["software"])
    assert read(api, req)["fit"] == {"strong": ["software"], "weak": ["design"]}

    owner_is(["operations"])
    assert read(api, req)["fit"] == {"strong": [], "weak": ["software", "design"]}


def test_a_post_card_is_a_marketers_strong_area(s, project, api, owner_is):
    owner_is(["marketing"])
    req = requests.create(s, draft(card=card(), action_kind="external.social_post", title="게시"),
                          from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    assert read(api, req)["fit"] == {"strong": ["marketing"], "weak": []}
    assert requests.read_model(s, req, "en", ["software"])["fit"] == {"strong": [], "weak": ["marketing"]}


def test_a_project_manager_question_belongs_to_no_area(s, project):
    req = requests.create(s, draft(card=card(), action_kind=None, title="질문"), from_agent="project_manager",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    assert requests.read_model(s, req, "en", ["marketing"])["fit"] == {"strong": [], "weak": []}


# ------------------------------------------------------------------ the task page and the report follow the same rule

def _task(s, project, owner: str) -> int:
    t = Task(project_id=project.id, key=f"k-{owner}", title="시장 조사", owner=owner, objective="동네 반찬 수요를 알아봐요",
             acceptance_criteria=["가게 세 곳"], status="DONE",
             result={"summary": "수요가 있어요. 근거는 아래에 있어요.", "findings_markdown": "## 찾은 것\n세 곳이 주 2회 사요."})
    s.add(t)
    s.commit()
    return t.id


def test_a_task_page_opens_its_detail_in_the_owners_strong_area(s, project, api, owner_is):
    build, research = _task(s, project, "engineer"), _task(s, project, "research")

    def fit(tid):
        r = api.get(f"/api/tasks/{tid}")
        assert r.status_code == 200, r.text
        return r.json()["fit"]
    assert fit(build) == {"strong": [], "weak": []}, "never asked: the page reads as it always did"
    owner_is(["software"])
    assert (fit(build), fit(research)) == ({"strong": ["software"], "weak": []}, {"strong": [], "weak": ["marketing"]})
    owner_is(["marketing"])
    assert fit(research) == {"strong": ["marketing"], "weak": []}, "market research is a marketer's"


def test_the_report_opens_the_sections_in_the_owners_strong_area_and_folds_the_rest(s, project, api, owner_is,
                                                                                   korean_owner, monkeypatch):
    from justshipit.services import registry
    out = FinalReportOut(
        title="반찬 예약", executive_summary="해 볼 만해요.", recommendation="진행: 단골 세 분이 예약했어요",
        report_markdown="## 단골과 채널\n당근 소식 글이 잘 맞아요.\n\n## 만들 것\n예약 페이지 하나면 돼요.\n\n## 남은 질문\n없어요.",
        section_areas=[SectionAreaOut(heading="단골과 채널", area="marketing"),
                       SectionAreaOut(heading="만들 것", area="software"), SectionAreaOut(heading="남은 질문", area="none")],
        validation_plan=["2주 동안 예약 수 세기"], development_needs=[], kpis=[])
    written = _report(s, project, monkeypatch, out)
    monkeypatch.setattr(registry, "read", lambda path: written["report"])
    s.expire_all()
    art = s.get(Project, project.id).context["report_artifact_id"]

    def fit():
        r = api.get(f"/api/artifacts/{art}")
        assert r.status_code == 200, r.text
        return r.json()["fit"]
    assert all(f == {"strong": [], "weak": []} for f in fit().values()), "never asked: the report reads as it did"
    owner_is(["marketing"])
    got = fit()
    assert got["단골과 채널"] == {"strong": ["marketing"], "weak": []}
    assert got["고객에게 확인할 것"] == {"strong": ["marketing"], "weak": []}, "checking with customers is outreach"
    assert got["만들 것"] == {"strong": [], "weak": ["software"]}
    assert "남은 질문" not in got and "요약" not in got, "a section of no area reads as it always did"
