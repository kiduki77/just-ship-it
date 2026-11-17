"""The spec card shows the planned screens as a wireflow, because the owner approved specs that arrived as text without
reading them (owner, 2026-11-11). The data is BUSINE10-0001's: a warehouse manager uploads the day's CSV, picks the
columns and gets a one-page exception report."""
import json
import random
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select
from story_kit import JOURNEYS

from justshipit.contracts.handoff import (
    BlockKind, ClarifyOptionOut, ClarifyOut, DesignBriefOut, Handoff, RequirementOut, ScenarioOut, ScreenStateOut,
    SpecDraftOut, UserStoryOut,
)
from justshipit.contracts.requests import HumanDecision, HumanResponse
from justshipit.contracts.schema import strict_json_schema
from justshipit.db.models import AgentRequest, Spec
from justshipit.engineering import questions, specs, stories, wireflow, worker
from justshipit.services import app_settings, registry, requests

FIXTURE = Path(__file__).parent / "fixtures" / "wireflow" / "plan.json"  # the payload the console is built against
ADDRESS_ASKED = {"question": "주소 열이 꼭 있어야 하나요?", "answer": "주소 열은 꼭 있어야 해요"}


@pytest.fixture(autouse=True)
def _english_owner(monkeypatch):
    monkeypatch.setattr(app_settings, "language", lambda: "en")


def _state(key: str, screen: str, state: str, shows: str, *sketch: tuple) -> dict:
    return {"key": key, "screen": screen, "state": state, "shows": shows,
            "sketch": [{"kind": kind, "text": text, "items": list(items)} for kind, text, items in sketch]}


# design-brief.md's states as the spec writer sends them now: each with its key and sketch, every state of one screen
# under one name, and the question about the address column still marked.
STATES = [
    _state("first-open", "파일 올리기", "empty",
           "'오늘 출고 CSV를 올려 주세요' 큰 영역과 '고객 이름·전화·주소는 이 브라우저 밖으로 나가지 않아요'. 버튼: '파일 고르기'.",
           ("nav", "출고 예외 리포트", []), ("title", "오늘 출고 CSV를 올려 주세요", []),
           ("upload", "CSV 파일을 여기로 끌어오세요", []), ("actions", "", ["파일 고르기"]),
           ("text", "고객 이름·전화·주소는 이 브라우저 밖으로 나가지 않아요", [])),
    _state("upload-error", "파일 올리기", "error",
           "'파일을 읽지 못했어요. 엑셀에서 CSV로 저장한 파일인지 확인하고 다시 올려 주세요.'",
           ("notice", "파일을 읽지 못했어요. 엑셀에서 CSV로 저장한 파일인지 확인하고 다시 올려 주세요.", []),
           ("actions", "", ["다시 올리기"])),
    _state("mapping-warning", "열 고르기", "warning",
           "'주소 열을 고르지 못했어요. 아래에서 직접 골라 주세요.' 필수 열이 비면 '리포트 보기'가 막혀 있어요. "
           "[NEEDS CLARIFICATION Q1: 주소 열이 꼭 있어야 하나요?]",
           ("notice", "주소 열을 고르지 못했어요. 아래에서 직접 골라 주세요.", []), ("field", "주문번호", ["주문번호"]),
           ("field", "주소", ["골라 주세요"]), ("field", "반품여부", ["반품"]), ("actions", "", ["리포트 보기"])),
    _state("report", "한 장 리포트", "success",
           "'오늘 출고 312건 중 확인할 예외 5건'. 주소 빈칸 2건, 중복 주문 2건, 반품 1건과 목록. 버튼: '문제 아님', '내보내기', '새 파일'.",
           ("title", "오늘 출고 312건 중 확인할 예외 5건", []), ("chips", "", ["주소 빈칸 2건", "중복 주문 2건", "반품 1건"]),
           ("table", "주소 빈칸", ["행 번호", "주문번호", "받는분", "처리"]),
           ("actions", "", ["내보내기", "새 파일", "문제 아님"])),
    _state("report-empty", "한 장 리포트", "empty", "'오늘 출고 312건에서 확인할 예외가 없어요.'",
           ("title", "오늘 출고 312건에서 확인할 예외가 없어요", []), ("actions", "", ["새 파일"])),
    _state("mapping-loading", "열 고르기", "loading", "'파일을 읽고 있어요…'"),
]


def _brief(states: list[dict]) -> DesignBriefOut:
    return DesignBriefOut(users_and_context="김포·이천 창고 관리자가 아침 출고 직전에 사무실 데스크톱에서 써요",
                          primary_task="출고 CSV를 올려 오늘 확인할 예외를 한 장으로 본다", primary_task_steps=3,
                          tone="차분함, 믿음직함, 빠름", first_glance="파일 올리는 큰 영역", states=states)


def _story(priority: str, title: str, then: str, empty_then: str, **kw) -> UserStoryOut:
    return UserStoryOut(title=title, priority=priority, story=f"{title}.", why_priority="w", independent_test="t",
                        scenarios=[ScenarioOut(given="g", when="w", then=then)],
                        empty_case=ScenarioOut(given="g", when="w", then=empty_then), **kw)


REPORT = _story(
    "P1", "CSV를 올려 한 장 예외 리포트 받기",
    "한 화면에 주소 빈칸 2건, 중복 주문 1묶음, 반품 1건이 행 번호·주문번호·가린 이름과 함께 나와요",
    "'오늘 출고 N건에서 확인할 예외가 없어요'라고 나와요",
    flow=[{"screen": "first-open", "do": "CSV를 올려요"},
          {"screen": "mapping-warning", "do": "열을 고르고 '리포트 보기'를 눌러요"}, {"screen": "report", "do": ""}],
    empty_screen="report-empty")
EXPORT = _story("P2", "경고 검토와 리포트 내보내기", "남은 2건만 담겨요", "'내보낼 예외가 없어요'라고 알려 줘요",
                flow=[{"screen": "report", "do": "내보내요"}])


def _plan(states: list[dict] = STATES, told: list[UserStoryOut] = (EXPORT, REPORT)) -> wireflow.Wireflow:
    return wireflow.plan(_brief(states), stories.ordered(list(told)))


def test_the_card_draws_busine10_in_story_order_with_each_branch_beside_its_step():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    expected["screens"]["mapping-warning"]["asks"] = [ADDRESS_ASKED]  # the fixture names the question by its id

    got = wireflow.card(_plan(), {"Q1": ADDRESS_ASKED})

    assert got == expected
    assert list(got["screens"]) == list(expected["screens"]), "the screens keep the brief's order"
    assert [r["story"] for r in got["rows"]] == ["US1"], "a P2 story has no row, whatever flow it sent"
    assert "NEEDS CLARIFICATION" not in wireflow.dump(_plan())


def test_a_question_the_owner_has_not_answered_shows_with_no_answer_and_an_unasked_one_not_at_all():
    w = _plan()
    waiting = {"Q1": {"question": ADDRESS_ASKED["question"], "answer": None}}
    assert wireflow.card(w, waiting)["screens"]["mapping-warning"]["asks"] == [waiting["Q1"]]
    assert wireflow.card(w, {})["screens"]["mapping-warning"]["asks"] == []


def test_a_frame_that_is_no_state_of_a_walked_screen_stays_loose():
    usage = _state("usage-error", "사용 기록 전송", "error", "전송 실패는 조용히 넘기고 리포트는 그대로 보여 줘요.")
    got = wireflow.card(_plan([*STATES, usage]), {})
    assert got["loose"] == ["usage-error"]
    assert [st["branches"] for st in got["rows"][0]["steps"]] == [["upload-error"], ["mapping-loading"], []], \
        "report-empty is the row's empty case, so it is no branch of the report step"


def test_a_screen_id_is_the_writers_key_made_safe_and_never_shared():
    keys = ["Report Empty", "report_empty", "리포트", "", "report-1440", "report-1280-1440", "x" * 40, "first-open",
            "first-open", "S4"]
    states = [ScreenStateOut(screen="s", state="st", shows="sh", key=k) for k in keys]

    assert wireflow.ids(states) == ["report-empty", "report-empty-2", "s3", "s4", "report", "report-2", "x" * 32,
                                    "first-open", "first-open-2", "s4-2"]
    long = [ScreenStateOut(screen="s", state="st", shows="sh", key="y" * 40) for _ in range(2)]
    assert wireflow.ids(long) == ["y" * 32, "y" * 30 + "-2"], "a numbered repeat still fits a capture's file name"


def test_a_flow_step_finds_its_screen_by_the_key_the_writer_gave_it():
    states = [_state("Mapping Warning", "열 고르기", "warning", "w"), _state("Report_1440", "한 장 리포트", "success", "r")]
    told = [_story("P1", "t", "then", "", flow=[{"screen": "mapping warning", "do": "a"},
                                               {"screen": "MAPPING-WARNING", "do": "b"},
                                               {"screen": "report_1440", "do": ""}], empty_screen="nowhere")]
    w = _plan(states, told)
    assert [(st.screen, st.do) for st in w.rows[0].steps] == [("mapping-warning", "b"), ("report", "")], \
        "two steps in a row on one screen are one"
    assert w.rows[0].empty is None, "an empty case with no Then has nothing to show"


def test_what_the_plan_leaves_out_is_counted():
    extra = [_state(f"extra-{n}", "기타", "st", "sh") for n in range(1, wireflow.MAX_SCREENS + 1)]
    told = [_story("P1", "t", "then", "empty then", empty_screen="nowhere",
                   flow=[{"screen": "first-open", "do": "a"}, {"screen": "no-such-screen", "do": "b"},
                         {"screen": f"extra-{wireflow.MAX_SCREENS}", "do": "c"}, {"screen": "report", "do": ""}])]
    w = _plan([*extra, *STATES], told)

    kept = [f"extra-{n}" for n in range(1, wireflow.MAX_SCREENS + 1)] + ["first-open"]
    assert list(w.screens) == kept, "the screen a user opens on is planned past the limit: the capture always takes it"
    assert w.dropped == 5 + 1, "five states past the limit and one step that named no screen"
    assert [st.screen for st in w.rows[0].steps] == ["first-open", f"extra-{wireflow.MAX_SCREENS}"], \
        "a step on a screen past the limit goes with its screen"
    assert w.rows[0].empty.model_dump() == {"screen": None, "then": "empty then"}
    assert wireflow.card(w, {})["dropped"] == 6

    alone = _plan(STATES, [_story("P1", "t", "then", "e", flow=[{"screen": "gone", "do": ""}])])
    assert (alone.rows, alone.dropped, alone.loose()) == ([], 1, list(alone.screens)), \
        "a story left with no step has no row, and every screen is still drawn"


def test_a_product_without_a_screen_has_no_plan():
    assert wireflow.plan(None, stories.ordered([REPORT])) is None
    assert wireflow.plan(_brief([]), stories.ordered([REPORT])) is None


def test_a_plan_survives_its_file():
    w = _plan()
    assert wireflow.Wireflow.model_validate(yaml.safe_load(wireflow.dump(w))) == w
    broken = yaml.safe_load(wireflow.dump(w))
    broken["rows"][0]["steps"][0]["screen"] = "no-such-screen"
    with pytest.raises(ValueError):
        wireflow.Wireflow.model_validate(broken)


_JUNK = [None, 0, -3, 2.5, True, "", "report", "first-open", "리포트", "x" * 5000, "\ud800", "a\x00b", [], {}, [[]],
         [None, 3, "x"], {"kind": "table"}, {"kind": 7, "text": ["a"], "items": "abc"}, {"screen": None},
         {"screen": "report", "do": {"a": 1}}, {"kind": "carousel", "text": "t", "items": [1, None, "ok", ["x"]]},
         [{"kind": "nav", "text": "t" * 400, "items": ["i" * 400] * 40}] * 40, b"bytes", object(),
         "[NEEDS CLARIFICATION Q2: who?]", "[NEEDS CLARIFICATION", {"kind": "text", "text": "\udfff[NEEDS CLARIFICATION Q1]"}]


def _junk(rng: random.Random, depth: int = 0):
    pick = rng.choice(_JUNK)
    if depth < 2 and rng.random() < 0.3:
        return [_junk(rng, depth + 1) for _ in range(rng.randrange(4))]
    if depth < 2 and rng.random() < 0.3:
        return {k: _junk(rng, depth + 1) for k in rng.sample(["kind", "text", "items", "screen", "do", "x"], 3)}
    return pick


def test_nothing_the_writer_sends_as_a_sketch_a_flow_or_a_key_fails_the_draft():
    """A draft that fails validation costs a 900-second call and its retries, so a sketch may only ever cost itself."""
    rng = random.Random(20261111)
    scenario = {"given": "g", "when": "w", "then": "t"}
    for _ in range(400):
        draft = {
            "title": "t", "prd_markdown": "p", "design_notes_markdown": "d", "requirements": [],
            "acceptance_criteria": [], "suggested_target_repository": "r", "suggested_test_command": None,
            "suggested_test_image": None,
            "design_brief": {"users_and_context": "u", "primary_task": "p", "primary_task_steps": 2, "tone": "t",
                             "first_glance": "f",
                             "states": [{"screen": "s", "state": "st", "shows": "sh", "key": _junk(rng),
                                         "sketch": _junk(rng)} for _ in range(rng.randrange(1, 16))]},
            "user_stories": [{"title": "t", "priority": "P1", "story": "s", "why_priority": "w",
                              "independent_test": "i", "scenarios": [scenario], "empty_case": scenario,
                              "flow": _junk(rng), "empty_screen": _junk(rng)} for _ in range(2)]}
        out = SpecDraftOut.model_validate(draft)

        w = wireflow.plan(out.design_brief, stories.ordered(out.user_stories))
        text = wireflow.dump(w)
        text.encode("utf-8")  # the registry writes it as UTF-8
        assert wireflow.Wireflow.model_validate(yaml.safe_load(text)) == w
        assert all(len(sid) <= 32 and len(x.blocks) <= 14 for sid, x in w.screens.items())
        wireflow.card(w, {})


def test_a_sketch_keeps_what_it_can_of_a_bad_block():
    state = ScreenStateOut(screen="s", state="st", shows="sh", key=7, sketch=[
        {"kind": "carousel", "text": "A slide", "items": ["one", 2, None, "three"]}, {"kind": "title"}, "not a block",
        {"kind": "chips", "items": ["c"] * 20}])
    assert state.key == ""
    assert [(b.kind, b.text, b.items) for b in state.sketch] == [
        (BlockKind.TEXT, "A slide", ["one", "three"]), (BlockKind.CHIPS, "", ["c"] * 8)]


def test_an_old_draft_without_sketches_still_validates():
    old = {"screen": "첫 화면", "state": "빈 상태", "shows": "'원두를 추가하면 주문할 날을 알려 드려요'"}
    assert ScreenStateOut.model_validate(old).sketch == []
    w = _plan([old], [REPORT.model_copy(update={"flow": [], "empty_screen": ""})])
    assert (list(w.screens), w.rows, w.screens["s1"].blocks) == (["s1"], [], []), "the frame draws `shows`"


def test_the_spec_drafts_schema_fits_a_windows_command_line_twice_over():
    """The schema goes to the CLI as an argument, and Windows refuses a command line past 32,767 characters. Half of
    that is the budget, so the field someone adds next fails here and not at a launch."""
    schema = json.dumps(strict_json_schema(SpecDraftOut), ensure_ascii=False)
    assert len(subprocess.list2cmdline(["--json-schema", schema])) <= 16_000


def test_the_spec_writer_is_taught_every_kind_and_the_rules_the_card_joins_on():
    section = wireflow.prompt_section()
    assert section in specs.SPEC_ASK
    assert all(f"- {kind.value}: " in section for kind in BlockKind)
    for rule in ("`first-open`", "Every state of one screen gets the same screen name", "On each P1 story, flow",
                 "exactly as the screen shows them", "never in a sketch"):
        assert rule in section


def _repo() -> str:
    return f"exceptions-{uuid.uuid4().hex[:8]}"


def _draft(**kw) -> SpecDraftOut:
    return SpecDraftOut(**{
        "title": "Exception report", "prd_markdown": "The problem.", "design_notes_markdown": "d",
        "user_stories": [EXPORT, REPORT], "requirements": [RequirementOut(id="REQ-1", statement="Report", priority="must",
                                                                          rationale="r")],
        "acceptance_criteria": [], "owner_trace": [], "design_brief": _brief(STATES),
        "suggested_target_repository": _repo(), "suggested_test_command": "npm test",
        "suggested_test_image": "node:22", "journeys": JOURNEYS, **kw})


def _write(s, project, monkeypatch, out: SpecDraftOut) -> Spec:
    monkeypatch.setattr(specs, "run_agent", lambda agent, purpose, prompt, model, **kw: out)
    req = AgentRequest(project_id=project.id, from_agent="product", type="DEVELOPMENT", title="Exception report",
                       summary="s", rationale="r", status="APPROVED", policy_mode="auto",
                       action_kind="dev.implementation_start",
                       action_params={"need": {"title": "report", "problem": "Exceptions get missed"}})
    s.add(req)
    s.commit()
    spec = s.get(Spec, specs.write_spec(project.id, req.id))
    s.refresh(spec)
    return spec


@pytest.fixture
def api(api_token):
    from justshipit.api.app import app
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def test_a_spec_card_carries_the_count_and_the_detail_fetch_the_screens_of_its_commit(s, project, monkeypatch, api):
    address = ClarifyOut(question="Must the file have an address column?", why_it_matters="It decides the warning.",
                         options=[ClarifyOptionOut(answer="Yes", means="No report without it."),
                                  ClarifyOptionOut(answer="No", means="The rule is skipped.")],
                         why_recommended="Every file so far has one.")
    spec = _write(s, project, monkeypatch, _draft(clarifications=[address]))
    drafted = spec.draft_commit_sha
    plan = registry.read_at(f"{spec.registry_path}/{wireflow.FILE}", drafted)
    assert Handoff.model_validate(spec.handoff).workflow.planned_screens is True
    brief = registry.read_at(f"{spec.registry_path}/design-brief.md", drafted)
    assert "| Id | Screen | State | What the user sees |" in brief and "| report-empty | 한 장 리포트 | empty |" in brief

    question = s.scalar(select(AgentRequest).where(AgentRequest.action_params["spec_id"].as_integer() == spec.id,
                                                   AgentRequest.type == "QUESTION"))
    requests.respond(s, question.id, HumanResponse(decision=HumanDecision.ANSWER, chosen_option="Yes"))
    s.commit()
    specs.on_questions_answered(spec.id)
    s.expire_all()
    spec = s.get(Spec, spec.id)
    card = s.get(AgentRequest, spec.approval_request_id)

    assert spec.draft_commit_sha != drafted, "the owner's answer is a new commit"
    assert "[Decided by the owner: Yes]" in registry.read_at(f"{spec.registry_path}/design-brief.md",
                                                             spec.draft_commit_sha)
    assert registry.read_at(f"{spec.registry_path}/{wireflow.FILE}", spec.draft_commit_sha) == plan, \
        "writing the answers into the spec's files leaves the planned screens as they were drawn"
    assert card.action_params["planned"] == len(STATES)
    assert "blocks" not in json.dumps(card.action_params), "the inbox lists every card: it never carries a sketch"

    got = api.get(f"/api/requests/{card.id}").json()["screens"]
    assert got["mode"] == "plan" and list(got["screens"]) == [x["key"] for x in STATES]
    assert got["screens"]["mapping-warning"]["asks"] == [
        {"question": "Must the file have an address column?", "answer": "Yes"}]
    assert got["rows"][0]["steps"][0] == {"screen": "first-open", "do": "CSV를 올려요", "branches": ["upload-error"]}
    assert api.get(f"/api/requests/{question.id}").json()["screens"] is None, "only a spec card has screens"

    again = specs.reask_approval(s, card)
    assert again.action_params["planned"] == len(STATES)


def test_a_spec_without_planned_screens_shows_the_card_it_always_did(s, project, monkeypatch, api):
    spec = _write(s, project, monkeypatch, _draft(design_brief=None))
    card = s.get(AgentRequest, spec.approval_request_id)

    assert wireflow.FILE not in "".join(registry.tree_at(spec.registry_path, spec.draft_commit_sha))
    assert Handoff.model_validate(spec.handoff).workflow.planned_screens is False
    assert card.action_params["planned"] == 0
    assert api.get(f"/api/requests/{card.id}").json()["screens"] is None


def _handoff(spec: Spec | None = None, **workflow) -> Handoff:
    return Handoff(spec_id="S", project="p", title="t", target={"repository": "x"}, workflow=workflow,
                   source={"path": spec.registry_path if spec else "p",
                           "commit_sha": spec.draft_commit_sha if spec else "a"})


def test_the_engineer_names_captures_by_screen_id_only_for_a_spec_that_planned_its_screens():
    planned = _handoff(screens=True, planned_screens=True)
    for told in (worker._ui_plan(planned, "DESIGN.md"), worker._ui_build(planned, "DESIGN.md"),
                 worker._ui_build(_handoff(screens=True, planned_screens=True, existing_code=True), None)):
        assert "screen id from `.ship/spec/screens.yaml`" in told and "first-open has no line" in told
        assert "design-brief.md's words win" in told
    old = _handoff(screens=True)
    assert "screens.yaml" not in worker._ui_plan(old, "DESIGN.md") + worker._ui_build(old, "DESIGN.md"), \
        "a spec from before planned screens has no such file to read"


def test_a_misnamed_capture_line_is_named_to_the_engineer():
    w = _plan()
    good = "\n".join(f"{sid} /?state={sid}" for sid in w.screens if sid != wireflow.FIRST)
    assert wireflow.lint_ui_states("# name path\n" + good + "\n", w) == []

    missing, unknown = wireflow.lint_ui_states("report /report\nreport_empty /report?empty\nfirst-open /\n", w)
    assert "upload-error, mapping-warning, report-empty, mapping-loading" in missing
    assert "report_empty, first-open" in unknown and "report," not in unknown
    late = "\n".join([*(f"mine-{n} /" for n in range(wireflow.MAX_SCREENS)), good])
    assert "report" in wireflow.lint_ui_states(late, w)[0], "the capture reads only the first lines"


def test_the_review_round_tells_the_engineer_which_lines_to_rename(s, project, monkeypatch, tmp_path):
    spec = _write(s, project, monkeypatch, _draft())
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "ui-states.txt").write_text("report-empty /?empty\nreport-nothing /?none\n", encoding="utf-8")

    told = worker._misnamed_screens(tmp_path, _handoff(spec, screens=True, planned_screens=True))

    assert told.startswith("\n\n## Screen names\n") and "report-nothing" in told and "mapping-warning" in told
    assert worker._misnamed_screens(tmp_path, _handoff(spec, screens=True)) == ""
    assert "has no line" in worker._misnamed_screens(tmp_path / "none", _handoff(spec, screens=True, planned_screens=True))


def test_the_console_draws_every_kind_the_writer_may_send():
    ts = (Path(__file__).parents[1] / "web/src/wireflow/types.ts").read_text(encoding="utf-8")
    union = ts.split("export type BlockKind =", 1)[1].split(";", 1)[0]
    assert set(union.replace('"', " ").replace("|", " ").split()) == {k.value for k in BlockKind}


@pytest.mark.parametrize(("written", "drawn"), [
    ("save-error", "error"), ("기본", ""), ("normal", ""), ("Warning", "warning"), ("빈 화면", "empty"),
    ("불러오는 중", "loading"), ("success", "success"), ("default", ""), ("확인", ""), ("결과 확인", ""),
    ("빈도 높음", "")])
def test_a_frame_says_one_of_five_states_or_none_whatever_the_writer_wrote(written, drawn):
    assert wireflow.state_word(written) == drawn


def test_an_answer_assumed_because_the_owner_put_the_question_off_is_no_answer(s):
    base = "projects/p/specs/ASSUMED-0001"
    sha = registry.write_files({f"{base}/{questions.FILE}": yaml.safe_dump({
        "questions": [{"id": "Q1", "question": "Must every row have an address?"},
                      {"id": "Q2", "question": "Show returns?"}],
        "answers": [{"id": "Q1", "answer": "Yes", "assumed": True}, {"id": "Q2", "answer": "No", "assumed": False}]})},
        "test: answers")

    got = questions.asked(base, sha)

    assert (got["Q1"]["answer"], got["Q2"]["answer"]) == (None, "No"),         "the card never shows a default as the owner's pick"
