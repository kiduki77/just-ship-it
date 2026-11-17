"""A spec carries journeys: how real people will use this version, written by the spec writer, approved with the spec.
A journey is not a test. The first draft of uxscope's spec had the owner "run generate twice" and "check nothing is
overwritten", which are the build's tests, and a non-developer cannot follow them from the merge card. The writer is
sent back once for a journey that reads like a test, and a draft that still has none says so on its spec card."""
import pytest
import yaml
from justshipit.contracts.handoff import AboutOut, JourneyOut, JourneyStepOut
from justshipit.contracts.requests import HumanDecision
from justshipit.db.models import AgentRequest, Spec
from justshipit.engineering import journeys, specs
from justshipit.services import app_settings, registry
from test_spec_kit import ACCESS, HOSTING, _answer, _at, _draft, _questions, _repo, _write_drafts


@pytest.fixture(autouse=True)
def _english_owner(monkeypatch):
    monkeypatch.setattr(app_settings, "language", lambda: "en")


ABOUT = AboutOut(what="uxscope turns a feature list into a flow spec for each screen", who="a solo founder planning an app",
                 problem="before building, she doesn't know which screens she needs")


def _step(do="She saves her feature list as a text file", see="A new folder named after her app appears"):
    return JourneyStepOut(do=do, why="so the tool has something to read", see=see)


def _journey(title="Turn a feature list into screen specs", priority="P1", steps=None, persona="Mina, planning an app"):
    return JourneyOut(title=title, persona=persona, why="she wants the screens before she pays a developer",
                      brings="6 features in a text file", success="she has one spec per screen", priority=priority,
                      steps=steps if steps is not None else [_step(), _step("She asks for the specs", "One file per screen")])


def _drafted(**kw):
    return _draft(_repo(), **{"prd_markdown": "The problem.", "clarifications": [], "about": ABOUT,
                              "journeys": [_journey()], **kw})


def _spec(s, project, monkeypatch, outs):
    rid, prompts = _write_drafts(s, project, monkeypatch, outs)
    spec = s.get(Spec, specs.write_spec(project.id, rid))
    s.refresh(spec)
    return spec, prompts


def test_a_step_that_says_generate_twice_goes_back_once_and_the_second_drafts_journeys_are_kept(s, project, monkeypatch):
    twice = _journey(steps=[_step(), _step("generate를 두 번 실행해요", "두 번 모두 같은 결과가 나와요")])
    spec, prompts = _spec(s, project, monkeypatch, [_drafted(journeys=[twice]), _drafted()])

    assert len(prompts) == 2 and "reads like a test" in prompts[1] and "reads like a test" not in prompts[0]
    doc = journeys.parse(_at(spec, journeys.FILE))
    assert doc["missing"] is None and [x["title"] for x in doc["journeys"]] == ["Turn a feature list into screen specs"]
    assert "generate" not in _at(spec, journeys.FILE)
    assert s.get(AgentRequest, spec.approval_request_id).action_params["journeys_missing"] is None


def test_a_second_failure_keeps_the_draft_and_the_spec_card_says_why_the_owner_decides(s, project, monkeypatch):
    twice = _journey(steps=[_step(), _step("generate를 두 번 실행해요", "두 번 모두 같은 결과가 나와요")])
    spec, prompts = _spec(s, project, monkeypatch, [_drafted(journeys=[twice]), _drafted(journeys=[twice])])

    assert len(prompts) == 2, "one more draft at most"
    assert spec.status == "IN_REVIEW" and spec.approval_request_id, "the card is raised, the owner decides"
    params = s.get(AgentRequest, spec.approval_request_id).action_params
    assert params["journeys_missing"] == "test"
    assert [x["id"] for x in params["journeys"]] == ["JN1"], "what the writer gave still shows"
    assert journeys.parse(_at(spec, journeys.FILE))["missing"] == "test"


def test_a_draft_with_no_p1_story_and_no_journey_is_drafted_once_more_with_both_notes(s, project, monkeypatch):
    spec, prompts = _spec(s, project, monkeypatch, [_draft(_repo(), user_stories=[], journeys=[]), _drafted()])

    assert len(prompts) == 2 and "no P1 user story" in prompts[1] and "journeys need work" in prompts[1]
    assert spec.approval_request_id


def test_a_draft_asked_again_for_its_journeys_alone_keeps_its_p1_story_when_the_second_draft_loses_it(
        s, project, monkeypatch):
    spec, prompts = _spec(s, project, monkeypatch, [_drafted(journeys=[]), _drafted(user_stories=[])])

    assert len(prompts) == 2, "asked once more"
    assert spec.approval_request_id, "the first draft, which had its P1 story, is kept rather than failing the spec"
    assert s.get(AgentRequest, spec.approval_request_id).action_params["journeys_missing"] == "none"


def test_a_spec_with_no_journey_after_the_second_draft_is_kept_and_says_none(s, project, monkeypatch):
    spec, prompts = _spec(s, project, monkeypatch, [_draft(_repo(), clarifications=[], journeys=[])] * 2)

    assert len(prompts) == 2
    params = s.get(AgentRequest, spec.approval_request_id).action_params
    assert (params["journeys"], params["journeys_missing"]) == ([], "none")


def test_journeys_number_like_stories_p1_first_and_their_steps_after_them(s, project, monkeypatch):
    later = _journey("Print the specs", "P2", [_step("She prints one", "A page"), _step("She pins it", "It hangs")])
    spec, _ = _spec(s, project, monkeypatch, [_drafted(journeys=[later, _journey()])])

    doc = journeys.parse(_at(spec, journeys.FILE))
    assert [(x["id"], x["priority"]) for x in doc["journeys"]] == [("JN1", "P1"), ("JN2", "P2")]
    assert [st["id"] for st in doc["journeys"][0]["steps"]] == ["JN1.1", "JN1.2"]
    assert doc["about"]["who"] == ABOUT.who


@pytest.mark.parametrize("reason, out", [
    ("none", _drafted(journeys=[])),
    ("too_many", _drafted(journeys=[_journey(f"Journey {n}") for n in range(4)])),
    ("steps", _drafted(journeys=[_journey(steps=[_step()])])),
    ("steps", _drafted(journeys=[_journey(steps=[_step() for _ in range(9)])])),
    ("see", _drafted(journeys=[_journey(steps=[_step(), _step(see=" ")])])),
    ("test", _drafted(journeys=[_journey(steps=[_step(), _step("She runs it again", "The same list")])])),
])
def test_one_to_three_journeys_of_two_to_eight_steps_each_with_something_to_see(reason, out):
    assert specs.journeys_missing(out) == reason
    assert specs.journeys_problem(out)


@pytest.mark.parametrize("n, do", [(1, "One journey"), (3, "Three journeys")])
def test_the_edges_of_the_limits_are_journeys(n, do):
    full = [_step() for _ in range(8)]
    out = _drafted(journeys=[_journey(f"{do} {i}", steps=full if i else [_step(), _step()]) for i in range(n)])
    assert specs.journeys_missing(out) is None and specs.journeys_problem(out) is None


@pytest.mark.parametrize("words", [
    "She does it twice", "Run it Again", "She compares the two folders", "It gives the same result", "Nothing is overwritten",
    "The file is unchanged", "No regression shows up", "generate를 두 번 실행해요", "다시 돌려 봐요", "똑같은 목록이 나와요",
    "기존 파일을 덮어쓰지 않아요", "원본이 그대로인지 봐요"])
def test_a_step_that_reads_like_a_test_is_one_in_either_language_in_do_or_see(words):
    assert specs.journeys_missing(_drafted(journeys=[_journey(steps=[_step(do=words), _step()])])) == "test"
    assert specs.journeys_missing(_drafted(journeys=[_journey(steps=[_step(), _step(see=words)])])) == "test"


@pytest.mark.parametrize("words", ["She argues against the plan", "She opens the second tab", "다시 열어요"])
def test_a_word_inside_another_word_is_not_a_test(words):
    assert specs.journeys_missing(_drafted(journeys=[_journey(steps=[_step(do=words), _step()])])) is None


def test_the_spec_card_carries_each_journeys_id_priority_title_persona_and_success(s, project, monkeypatch):
    spec, _ = _spec(s, project, monkeypatch, [_drafted()])

    params = s.get(AgentRequest, spec.approval_request_id).action_params
    assert params["journeys"] == [{"id": "JN1", "priority": "P1", "title": "Turn a feature list into screen specs",
                                   "persona": "Mina, planning an app", "success": "she has one spec per screen"}]
    assert params["journeys_missing"] is None
    assert params["stories"], "the stories still lead"


def _both_answered(s, spec) -> None:
    hosting, access = _questions(s, spec)
    _answer(s, hosting, decision=HumanDecision.ANSWER, chosen_option="Cloudflare Pages")
    _answer(s, access, decision=HumanDecision.DEFER)


def test_the_card_raised_after_the_owners_answers_reads_the_journeys_from_the_spec_commit(s, project, monkeypatch):
    spec, _ = _spec(s, project, monkeypatch, [_drafted(clarifications=[HOSTING, ACCESS])])
    assert spec.approval_request_id is None
    _both_answered(s, spec)

    params = s.get(AgentRequest, s.get(Spec, spec.id).approval_request_id).action_params
    assert [x["id"] for x in params["journeys"]] == ["JN1"] and params["journeys_missing"] is None


def test_a_spec_card_for_a_spec_written_before_journeys_is_unaffected_and_never_blocked(s, project, monkeypatch):
    spec, _ = _spec(s, project, monkeypatch, [_drafted(clarifications=[HOSTING, ACCESS])])
    base = spec.registry_path
    spec.draft_commit_sha = registry.write_files({}, "legacy: no journeys", remove=[f"{base}/{journeys.FILE}"])
    s.commit()
    assert journeys.at(base, spec.draft_commit_sha) == {"about": None, "journeys": [], "missing": None}
    _both_answered(s, spec)

    card = s.get(AgentRequest, s.get(Spec, spec.id).approval_request_id)
    assert (card.action_params["journeys"], card.action_params["journeys_missing"]) == ([], None)
    assert card.status == "PENDING" and card.action_kind == "dev.spec_approve"


def test_a_journeys_file_from_before_missing_reads_as_none():
    assert journeys.parse(yaml.safe_dump({"about": None, "journeys": []}))["missing"] is None
