"""What the owner says in the kickoff stays the owner's, and the idea panel's findings reach the CEO only as checked
facts: a metric the owner stated is not marked a guess, a 맞아요 (with or without words) confirms what it lists, the
CEO and the panel write in the owner's language, the CEO reads the panel's substance on every turn, a source shows only
when its page backs it, and a way past a warning is something the owner can do alone."""
import pytest
from justshipit.contracts.idea_review import CritiqueOut, IdeaWarning, Similar, Source, owner_alone
from justshipit.contracts.kickoff import BriefPatchLine, KickoffTurnOut
from justshipit.db.models import IdeaReview
from justshipit.db.session import session_scope
from justshipit.services import evidence, idea_review, kickoff
from justshipit.settings import settings
from sqlalchemy import select
from test_kickoff import _settled_jobs, api, project_manager, kickoff_jobs, lines, open_session, out, play, reply, view  # noqa: F401

pytestmark = pytest.mark.usefixtures("strengths_answered")

C1_REPLY = ("두 개만 고침.\n1) 성공 지표에 \"견적 메일 도착→1차 답장 발송까지 걸린 시간\"을 넣어. 목표는 중앙값 2시간 이내"
            "(지금은 반나절~사흘). 보내기 횟수보다 이게 핵심.\n2) 출발 경로: 처음 3곳은 메일 연동 말고 전달(포워딩) 주소로 시작. "
            "나머지는 맞음. 승인할 테니 바로 반영해.")
C1_LINE = "성공 지표: 견적 메일 도착부터 1차 답장 발송까지 걸린 시간. 목표는 중앙값 2시간 이내(지금은 반나절~사흘)"
C1_QUOTE = "목표는 중앙값 2시간 이내(지금은 반나절~사흘)"


def said(section, text, quote):
    return (section, text, "said", quote)


# ---------------------------------------------------------------- 1. the owner's own metric is not a guess

def test_the_owners_metric_stays_said_when_the_line_keeps_a_number_from_beside_the_quote(api, project_manager):
    sid = open_session(api)
    play(api, project_manager, sid, out(section="goal_deadline"), answer={"text": C1_REPLY})
    project_manager.says(out(section="owner_time", patch=[said("goal_deadline", C1_LINE, C1_QUOTE)]))
    kickoff.run_turn(sid, 2)

    v = view(api, sid)
    [line] = lines(v, "goal_deadline")
    assert (line["provenance"], line["quote"], line["source"]) == ("said", C1_QUOTE, f"kickoff:{sid}:1")
    assert v["turns"][1]["patch_notes"] == []
    assert "목표와 기한" not in v["approval"]["blocked_by"]


def test_a_count_target_the_owner_typed_stays_said(api, project_manager):
    sid = open_session(api)
    play(api, project_manager, sid, out(section="goal_deadline"), answer={"text": "6주 안에 30명 신청, 15명 실사용이면 성공으로 볼게요"})
    project_manager.says(out(section="owner_time", patch=[said("goal_deadline", "6주 안에 사전 신청 30명, 실제로 쓰는 사람 15명", "30명 신청, 15명 실사용")]))
    kickoff.run_turn(sid, 2)

    assert [x["provenance"] for x in lines(view(api, sid), "goal_deadline")] == ["said"]


def test_a_number_the_owner_never_said_near_the_quote_is_still_a_guess(api, project_manager):
    sid = open_session(api)
    far = "주말엔 가게 문을 닫아요. " * 12 + "목표는 중앙값 2시간 이내로 줄이는 거예요"
    play(api, project_manager, sid, out(section="goal_deadline"), answer={"text": "작년에 견적 500건을 받았어요. " + far})
    project_manager.says(out(section="owner_time", patch=[
        said("goal_deadline", "목표는 중앙값 30분 이내", "목표는 중앙값 2시간 이내로 줄이는 거예요"),
        said("goal_deadline", "견적 500건, 목표는 중앙값 2시간 이내", "목표는 중앙값 2시간 이내로 줄이는 거예요")]))
    kickoff.run_turn(sid, 2)

    v = view(api, sid)
    assert [x["provenance"] for x in lines(v, "goal_deadline")] == ["guess", "guess"]
    notes = v["turns"][1]["patch_notes"]
    assert [(n["to"], n["reason"]) for n in notes] == [("guess", "not_backing"), ("guess", "not_backing")]
    assert "30" in notes[0]["why"] and "500" in notes[1]["why"], "a number 120+ characters away does not count"


def test_each_note_names_what_the_line_became_and_why(api, project_manager):
    sid = open_session(api)
    play(api, project_manager, sid, out(section="scope", chips=["문서화만 먼저 해요"]), answer={"choice": "문서화만 먼저 해요"})
    project_manager.says(out(section="owner_time", patch=[said("scope", "문서화만 먼저", "문서화만 먼저 해요"),
                                               said("path", "블로그로 알린다", "블로그에 올려서 알려요")]))
    kickoff.run_turn(sid, 2)

    notes = view(api, sid)["turns"][1]["patch_notes"]
    assert [(n["to"], n["reason"]) for n in notes] == [("confirmed", "chip"), ("guess", "no_quote")]


# ---------------------------------------------------------------- 2. a 맞아요 confirms what it lists

def test_the_closing_yes_confirms_a_line_from_a_proposal_the_owner_partly_corrected(api, project_manager):
    sid = open_session(api)
    play(api, project_manager, sid, out("이렇게 시작할까요?", kind="proposal", section="path",
                            patch=[("path", "앱 없이 구글폼 사전 신청으로 시작해요", "guess")]),
         answer={"text": "대체로 맞는데 폼에서 지불 의향 질문은 빼 주세요"})
    app_line = lines(view(api, sid), "path")[0]
    project_manager.says(out(section="owner_time"))
    kickoff.run_turn(sid, 2)
    assert api.post(f"/api/kickoff/{sid}/finish").status_code == 200
    project_manager.says(out("전체를 돌려 드려요", kind="playback", section="owner_words", done=True))
    kickoff.run_turn(sid, 3, True)

    assert app_line["id"] in [x["id"] for x in view(api, sid)["turns"][-1]["promote"]]
    v = reply(api, sid, choice="yes")
    assert v["session"]["status"] == "DONE"
    assert [x["provenance"] for x in lines(v, "path")] == ["confirmed"]


def test_a_yes_with_words_confirms_the_listed_lines_and_the_project_manager_takes_the_words_next(api, project_manager):
    sid = open_session(api)
    play(api, project_manager, sid, out(kind="proposal", patch=[("customer_problem", "신입 개발자로 좁힌다", "guess")]),
         answer={"choice": "yes", "text": "맞아요. 특히 입사 첫 달인 분이요"})

    v = view(api, sid)
    assert [x["provenance"] for x in lines(v, "customer_problem")] == ["confirmed"]
    assert [x["text"] for x in lines(v, "owner_words")] == ["맞아요. 특히 입사 첫 달인 분이요"]
    assert (v["turns"][0]["owner_choice"], v["session"]["status"]) == ("yes", "OPEN")
    project_manager.says(out())
    kickoff.run_turn(sid, 2)
    assert "(pressed \"That's right\") 맞아요. 특히 입사 첫 달인 분이요" in project_manager.prompts[-1]


def test_a_closing_yes_with_words_confirms_and_closes_once_more(api, project_manager):
    sid = open_session(api)
    project_manager.says(out(patch=[("scope", "문서화만", "guess")]))
    kickoff.run_turn(sid, 1)
    assert api.post(f"/api/kickoff/{sid}/finish").status_code == 200
    project_manager.says(out("전체", kind="playback", section="owner_words", done=True))
    kickoff.run_turn(sid, 2, True)

    v = reply(api, sid, choice="yes", text="시작은 다음 주 월요일부터요")
    assert v["session"]["status"] == "OPEN" and [x["provenance"] for x in lines(v, "scope")] == ["confirmed"]
    assert kickoff_jobs(sid)[-1].payload == {"session_id": sid, "n": 3, "close": True}
    project_manager.says(out("다시 정리했어요", kind="playback", section="owner_words", done=True))
    kickoff.run_turn(sid, 3, True)
    assert reply(api, sid, choice="yes")["session"]["status"] == "DONE"


# ---------------------------------------------------------------- 3. the owner's language

def test_the_project_manager_and_the_panel_write_in_the_language_the_owner_chose_in_settings():
    """The setting wins over the language the owner types in (docs/I18N.md): every field and prompt defers to the
    output-language rule each system prompt ends with."""
    turn = KickoffTurnOut.model_json_schema()
    line = BriefPatchLine.model_json_schema()
    critique = IdeaWarning.model_json_schema()
    for desc in (turn["properties"]["text"]["description"], turn["properties"]["chips"]["description"],
                 line["properties"]["text"]["description"], critique["properties"]["why"]["description"],
                 critique["properties"]["to_overcome"]["description"]):
        assert "output language" in desc.lower() and "오너가 쓰는 언어" not in desc, desc
    for prompt in ("kickoff.md", "agents/critic.md"):
        text = (settings().prompts_dir / prompt).read_text(encoding="utf-8")
        assert "모든 글은 한국어 해요체로" not in text and "output language" in text.lower()


# ---------------------------------------------------------------- 4. the CEO reads the panel's substance

PAGES = {
    "https://digifabster.example/rfq": "<title>DigiFabster</title><p>Upload a CAD file and get an instant quote within seconds.</p>",
    "https://blocked.example/x": 403,
    "https://dead.example/x": None,
    "https://unrelated.example/x": "<p>Our bakery opens at nine.</p>",
}


@pytest.fixture
def pages(monkeypatch):
    """The web as the lens job sees it, with no network: html, an HTTP status the site answers with, or None."""
    def fetch_page(url):
        page = PAGES.get(url)
        if page is None:
            return "url_unreachable", "ConnectError", None
        if isinstance(page, int):
            return "unverifiable", f"HTTP {page}", None
        return "url_ok", "HTTP 200", evidence._text(page)
    monkeypatch.setattr(evidence, "fetch_page", fetch_page)


def critique() -> CritiqueOut:
    return CritiqueOut(
        headline="견적 초안을 만드는 서비스가 이미 있어요",
        warnings=[IdeaWarning(
            title="견적 자동화가 이미 있어요", level="경고",
            why="DigiFabster가 CAD 파일로 바로 견적을 내 줘요. 초안만으로는 차별점이 약해요.",
            to_overcome="사장님 세 분께 DigiFabster를 안 쓰는 이유를 물어보세요. 투자자를 만나 자금을 먼저 확보하세요.",
            evidence=[
                Source(label="DigiFabster 소개", url="https://digifabster.example/rfq", quote="get an instant quote within seconds"),
                Source(label="없는 페이지", url="https://dead.example/x", quote="a page that does not exist"),
                Source(label="상관없는 페이지", url="https://unrelated.example/x", quote="instant quote for machine shops")]),
            IdeaWarning(
                title="메일 권한 심사가 길어요", level="주의", why="Gmail 읽기 권한은 외부 심사를 받아야 해요.",
                to_overcome="처음 세 곳은 전달 주소로 받아 보세요.",
                evidence=[Source(label="막힌 사이트", url="https://blocked.example/x", quote="anything at all on that page"),
                          Source(label="출처 없음: 내 짐작", url=None, quote="")])],
        similar=[Similar(name="DigiFabster", url="https://digifabster.example/rfq", how_it_differs="CAD로 견적을 내요"),
                 Similar(name="QuoteMagic", url="https://unrelated.example/x", how_it_differs="지어낸 이름이에요")])


def run_market(bid: int, monkeypatch) -> dict:
    monkeypatch.setattr(idea_review, "run_agent", lambda *a, **k: critique())
    with session_scope() as s:
        rid = s.scalar(select(IdeaReview.id).where(IdeaReview.business_id == bid, IdeaReview.lens == "market"))
    idea_review.run_lens(rid)
    with session_scope() as s:
        return s.get(IdeaReview, rid).result


def test_a_source_shows_only_when_its_page_holds_the_quote(api, pages, monkeypatch):
    sid = open_session(api)
    result = run_market(view(api, sid)["session"]["business_id"], monkeypatch)

    first, second = result["warnings"]
    assert [(e["label"], e["check"]) for e in first["evidence"]] == [("DigiFabster 소개", "found")]
    assert first["dropped"] == 2, "a dead page and a page without the quote back nothing"
    assert [(e["label"], e["url"], e["check"]) for e in second["evidence"]] == [
        ("막힌 사이트", "https://blocked.example/x", "blocked"), ("출처 없음: 내 짐작", None, "none")]
    assert [(x["name"], x["check"]) for x in result["similar"]] == [("DigiFabster", "found")]


def test_a_way_past_a_warning_keeps_only_what_the_owner_can_do_alone(api, pages, monkeypatch):
    sid = open_session(api)
    w = run_market(view(api, sid)["session"]["business_id"], monkeypatch)["warnings"][0]
    assert w["to_overcome"] == "사장님 세 분께 DigiFabster를 안 쓰는 이유를 물어보세요."


@pytest.mark.parametrize("step, kept", [
    ("개발자를 뽑아 앱을 만드세요.", ""),
    ("동물병원과 제휴를 맺으세요. 보호자 열 분께 물어보세요.", "보호자 열 분께 물어보세요."),
    ("광고 대행사에 맡기세요.", ""),
    ("Hire a designer first. Then ask five owners why they stopped.", "Then ask five owners why they stopped."),
    ("주 5시간을 투자해 사장님 세 분을 만나 보세요.", "주 5시간을 투자해 사장님 세 분을 만나 보세요."),
    ("산책 대행을 세 번 직접 해 보세요.", "산책 대행을 세 번 직접 해 보세요."),
])
def test_a_step_that_needs_hires_investors_partners_or_agencies_is_dropped(step, kept):
    assert owner_alone(step) == kept


def test_the_project_manager_reads_the_panels_reasons_steps_and_checked_sources_on_the_closing_turn_too(api, project_manager, pages, monkeypatch):
    sid = open_session(api)
    run_market(view(api, sid)["session"]["business_id"], monkeypatch)
    project_manager.says(out(patch=[("scope", "문서화만", "guess")]))
    kickoff.run_turn(sid, 1)
    assert api.post(f"/api/kickoff/{sid}/finish").status_code == 200
    project_manager.says(out("전체", kind="playback", section="owner_words", done=True))
    kickoff.run_turn(sid, 2, True)

    closing = project_manager.prompts[-1]
    panel = closing.split("<<<데이터 시작: Idea panel>>>", 1)[1].split("<<<데이터 끝: Idea panel>>>", 1)[0]
    assert "Why: DigiFabster가 CAD 파일로 바로 견적을 내 줘요" in panel
    assert "To get past it: 사장님 세 분께 DigiFabster를 안 쓰는 이유를 물어보세요." in panel
    assert 'DigiFabster 소개 (https://digifabster.example/rfq): "get an instant quote within seconds"' in panel
    assert "막힌 사이트 (https://blocked.example/x), content not checked" in panel
    assert "dead.example" not in panel and "투자자" not in panel
    assert "- DigiFabster (https://digifabster.example/rfq): CAD로 견적을 내요" in panel
