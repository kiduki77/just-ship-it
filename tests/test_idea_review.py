"""The idea review panel: opening a business asks three lenses once, each lens stores its critique, a failed lens
shows as failed while the kickoff goes on, asking again opens the next round, and the kickoff CEO reads the panel's
warnings once they exist."""
import pytest
from justshipit.agents.executor import AgentFailed
from justshipit.api.app import app
from justshipit.contracts.idea_review import CritiqueOut, IdeaWarning, Similar, Source
from justshipit.contracts.kickoff import KickoffTurnOut
from justshipit.db.models import IdeaReview, Job, KickoffSession
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import evidence, idea_review, kickoff, queue
from justshipit.workers import runner
from fastapi.testclient import TestClient
from sqlalchemy import select, text

pytestmark = pytest.mark.usefixtures("strengths_answered")

IDEA = "혼자 카페를 하는 사장님이 원두가 떨어지기 전에 알림을 받는 앱"
QUOTE = "재고 알림 기능을 기본으로 넣었어요"
PAGES = {"https://example.com/a": f"<p>카페 POS 새 소식: {QUOTE}</p>", "https://stock.example.com": "<title>재고노트</title>"}


@pytest.fixture(autouse=True)
def _offline_pages(monkeypatch):
    """The lens job opens every cited link. Here the web is `PAGES`, and any other link does not open."""
    def fetch_page(url):
        if url not in PAGES:
            return "url_unreachable", "ConnectError", None
        return "url_ok", "HTTP 200", evidence._text(PAGES[url])
    monkeypatch.setattr(evidence, "fetch_page", fetch_page)


@pytest.fixture(autouse=True)
def _no_stray_jobs():
    def clear():
        with session_scope() as s:
            s.execute(text("DELETE FROM jobs WHERE kind IN ('idea_review', 'kickoff_turn')"))
    clear()
    yield
    clear()


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


class Critic:
    """Stands in for `claude -p`: answers each lens from a script and keeps what it was asked."""

    def __init__(self):
        self.script, self.calls = [], []

    def says(self, *items):
        self.script += items
        return self

    def __call__(self, agent, purpose, prompt, output_model, *, project_id, system_override=None, **kw):
        self.calls.append({"agent": agent, "purpose": purpose, "prompt": prompt, "system": system_override,
                           "model": output_model, "project_id": project_id})
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def critic(monkeypatch):
    fake = Critic()
    monkeypatch.setattr(idea_review, "run_agent", fake)
    return fake


def warning(title, level="주의", url="https://example.com/a"):
    return IdeaWarning(title=title, why="비슷한 앱이 이미 여럿 있어요. 사장님이 앱을 하나 더 열 이유가 약해요.", level=level,
                       evidence=[Source(label="검색한 페이지", url=url, quote=QUOTE)],
                       to_overcome="원두 주문을 놓친 사장님 다섯 분께 지금 어떻게 챙기는지 들어 보세요.")


def critique(headline="POS에 재고 알림이 이미 붙어 있어요", warnings=None, similar=()):
    return CritiqueOut(headline=headline, warnings=warnings or [warning("POS 재고 기능과 겹쳐요")], similar=list(similar))


def open_business(api, one_liner=IDEA) -> tuple[int, int]:
    r = api.post("/api/businesses", json={"name": "원두 알림", "one_liner": one_liner})
    assert r.status_code == 200, r.text
    session = r.json()["session"]
    return session["business_id"], session["id"]


def panel(api, bid: int) -> dict:
    r = api.get(f"/api/businesses/{bid}/idea-review")
    assert r.status_code == 200, r.text
    return r.json()


def review_jobs(bid: int) -> list[Job]:
    with session_scope() as s:
        ids = set(s.scalars(select(IdeaReview.id).where(IdeaReview.business_id == bid)))
        rows = [j for j in s.scalars(select(Job).where(Job.kind == "idea_review").order_by(Job.id))
                if j.payload["review_id"] in ids]
        s.expunge_all()
        return rows


def lens_id(bid: int, lens: str, rnd: int = 1) -> int:
    with session_scope() as s:
        return s.scalar(select(IdeaReview.id).where(IdeaReview.business_id == bid, IdeaReview.round == rnd,
                                                    IdeaReview.lens == lens))


def run_round(bid: int, critic_, rnd: int = 1) -> None:
    for lens in idea_review.LENSES:
        critic_.says(critique(headline=f"{lens} 걱정"))
        idea_review.run_lens(lens_id(bid, lens, rnd))


def test_opening_a_business_asks_three_lenses_once(api):
    bid, _ = open_business(api)

    jobs = review_jobs(bid)
    assert len(jobs) == 3 and {j.status for j in jobs} == {"queued"}
    view = panel(api, bid)
    assert view["round"] == 1 and view["can_rerun"] is False
    assert [(x["key"], x["status"], x["result"]) for x in view["lenses"]] == [
        ("market", "queued", None), ("timing", "queued", None), ("demand", "queued", None)]
    assert [x["title"] for x in view["lenses"]] == ["Market and competition", "Timing and feasibility", "Demand and reach"]
    with session_scope() as s:
        assert [x["title"] for x in idea_review.view(s, bid, lang="ko")["lenses"]] == ["시장·경쟁", "시점·실현 가능성", "수요·도달"]

    with session_scope() as s:
        idea_review.start(s, bid)
    assert len(review_jobs(bid)) == 3, "a business that has a round is not asked again"


def test_a_lens_job_belongs_to_its_business(api):
    bid, _ = open_business(api)

    assert {j.business_id for j in review_jobs(bid)} == {bid}


def test_a_lens_run_stores_its_critique_with_only_web_links(api, critic):
    bid, _ = open_business(api)
    five = [warning(f"걱정 {i}") for i in range(4)] + [warning("나쁜 링크", url="javascript:alert(1)")]
    critic.says(critique(warnings=five[::-1], similar=[
        Similar(name="재고노트", url="https://stock.example.com", how_it_differs="재고 전체를 다뤄요"),
        Similar(name="지어낸 서비스", url="없음", how_it_differs="주소가 없어요")]))

    idea_review.run_lens(lens_id(bid, "market"))

    call = critic.calls[0]
    assert (call["agent"], call["purpose"], call["model"], call["project_id"]) == ("critic", "idea_review", CritiqueOut, None)
    assert "idea review panel" in call["system"] and "Market and competition [market]" in call["prompt"]
    assert ": owner idea>>>" in call["prompt"] and IDEA in call["prompt"]
    market = panel(api, bid)["lenses"][0]
    assert market["status"] == "done" and market["finished_at"]
    result = market["result"]
    assert result["headline"] == "POS에 재고 알림이 이미 붙어 있어요"
    assert [w["title"] for w in result["warnings"]] == ["나쁜 링크", "걱정 3", "걱정 2", "걱정 1"]
    assert (result["warnings"][0]["evidence"], result["warnings"][0]["dropped"]) == ([], 1)
    assert result["warnings"][1]["evidence"] == [
        {"label": "검색한 페이지", "url": "https://example.com/a", "quote": QUOTE, "check": "found"}]
    assert set(result["warnings"][0]) == {"title", "why", "level", "evidence", "to_overcome", "dropped"}
    assert result["similar"] == [{"name": "재고노트", "url": "https://stock.example.com", "how_it_differs": "재고 전체를 다뤄요",
                                  "check": "found"}]

    critic.says(critique(similar=[Similar(name="재고노트", url="https://stock.example.com", how_it_differs="같아요")]))
    idea_review.run_lens(lens_id(bid, "timing"))
    assert panel(api, bid)["lenses"][1]["result"]["similar"] == [], "only the market lens lists similar services"

    idea_review.run_lens(lens_id(bid, "market"))
    assert len(critic.calls) == 2, "a settled lens is not asked again"


def test_a_failed_lens_shows_failed_and_the_kickoff_goes_on(api, critic, monkeypatch):
    bid, sid = open_business(api)
    market = lens_id(bid, "market")
    with session_scope() as s:
        job = next(j for j in s.scalars(select(Job).where(Job.kind == "idea_review")) if j.payload["review_id"] == market)
        job.max_attempts = 1
        jid = job.id
    critic.says(AgentFailed("claude -p exited 1"))

    with session_scope() as s:
        claimed = queue.claim(s, "worker-test", ["idea_review"])
        assert claimed.id == jid
        payload, token = dict(claimed.payload), claimed.lease_token
    runner._execute(jid, "idea_review", payload, {"idea_review": lambda p: idea_review.run_lens(p["review_id"])},
                    runner.Runs(), queue.Lease(job_id=jid, token=token))

    view = panel(api, bid)
    assert [x["status"] for x in view["lenses"]] == ["failed", "queued", "queued"]
    assert view["lenses"][0]["note"] == msg("idea_review.note.failed", "en") and view["lenses"][0]["result"] is None

    turn = KickoffTurnOut(kind="question", section="customer_problem", text="그 사장님은 지난주에 원두를 어떻게 챙기셨어요?",
                          chips=[], brief_patch=[], done=False, done_reason="")
    monkeypatch.setattr(kickoff, "run_agent", lambda *a, **k: turn)
    kickoff.run_turn(sid, 1)
    with session_scope() as s:
        assert s.get(KickoffSession, sid).status == "WAITING_OWNER"


def test_rereview_opens_round_two_once_the_last_round_settled(api, critic):
    bid, _ = open_business(api)

    busy = api.post(f"/api/businesses/{bid}/idea-review")
    assert busy.status_code == 409 and "still reviewing" in busy.json()["detail"]

    run_round(bid, critic)
    assert panel(api, bid)["can_rerun"] is True
    r = api.post(f"/api/businesses/{bid}/idea-review")
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["round"] == 2 and view["can_rerun"] is False
    assert [x["status"] for x in view["lenses"]] == ["queued", "queued", "queued"]
    assert len(review_jobs(bid)) == 6
    with session_scope() as s:
        assert s.scalar(select(IdeaReview.result).where(IdeaReview.business_id == bid, IdeaReview.round == 1,
                                                        IdeaReview.lens == "market"))["headline"] == "market 걱정"


def test_the_panel_api_names_a_missing_business(api):
    assert api.get("/api/businesses/999999/idea-review").status_code == 404
    assert api.post("/api/businesses/999999/idea-review").status_code == 404


def test_the_kickoff_project_manager_reads_the_panels_warnings_once_they_exist(api, critic, monkeypatch):
    bid, sid = open_business(api)
    prompts = []

    def project_manager(agent, purpose, prompt, output_model, **kw):
        prompts.append(prompt)
        return KickoffTurnOut(kind="question", section="customer_problem", text="지난주에는 어땠어요?", chips=[],
                              brief_patch=[], done=False, done_reason="")
    monkeypatch.setattr(kickoff, "run_agent", project_manager)

    kickoff.run_turn(sid, 1)
    assert "Idea panel" not in prompts[0]

    critic.says(critique(headline="POS에 재고 알림이 이미 붙어 있어요",
                         warnings=[warning("POS 재고 기능과 겹쳐요", "경고"), warning("원두 업체 앱이 알림을 줘요", "참고")]))
    idea_review.run_lens(lens_id(bid, "market"))
    r = api.post(f"/api/kickoff/{sid}/answer", json={"text": "지난주에 원두가 떨어져서 마트에서 샀어요"})
    assert r.status_code == 200, r.text
    kickoff.run_turn(sid, 2)

    fenced = prompts[1].split("<<<데이터 시작: Idea panel>>>", 1)[1].split("<<<데이터 끝: Idea panel>>>", 1)[0]
    assert "Market and competition: POS에 재고 알림이 이미 붙어 있어요" in fenced
    assert "[경고] POS 재고 기능과 겹쳐요" in fenced and "[참고] 원두 업체 앱이 알림을 줘요" in fenced
    assert "Timing and feasibility" not in fenced, "a lens still out is not summarized"


@pytest.mark.parametrize("lang, expected", [
    ("en", "The daily usage cap was reached, so the critics pick this up at"),
    ("ko", "하루 사용량 상한에 닿아서"),
])
def test_a_lens_that_waits_for_usage_says_so_in_the_owners_language(api, critic, monkeypatch, lang, expected):
    from datetime import UTC, datetime, timedelta

    from justshipit.services import app_settings
    from justshipit.services.usage import MustWait
    bid, _ = open_business(api)
    monkeypatch.setattr(app_settings, "language", lambda: lang)
    critic.says(MustWait(datetime.now(UTC) + timedelta(hours=3), "usage_cap_day", "detail the owner never reads"))

    with pytest.raises(MustWait):
        idea_review.run_lens(lens_id(bid, "market"))

    with session_scope() as s:
        note = s.get(IdeaReview, lens_id(bid, "market")).note
    assert note.startswith(expected) and "detail the owner never reads" not in note


def test_rereview_refusals_are_in_the_owners_language(api):
    from justshipit.services.control import ControlError, NotFound
    bid, _ = open_business(api)
    with session_scope() as s:
        for lang, busy, missing in (("en", "The critics are still reviewing", "Business B-999999 not found."),
                                    ("ko", "평가단이 아직 평가하고 있어요", "B-999999 사업을 찾지 못했어요.")):
            with pytest.raises(ControlError, match=busy):
                idea_review.rereview(s, bid, lang=lang)
            with pytest.raises(NotFound, match=missing):
                idea_review.view(s, 999999, lang=lang)


@pytest.mark.parametrize("step, kept", [
    ("Hire a designer first. Then ask five owners why they stopped.", "Then ask five owners why they stopped."),
    ("Raise a seed round to build it.", ""),
    ("Partner with a roaster. Call ten cafes this week.", "Call ten cafes this week."),
    ("Outsource the app to an agency.", ""),
    ("Find a co-founder who codes.", ""),
    ("Build a team of three.", ""),
    ("Invest five hours in talking to owners.", "Invest five hours in talking to owners."),
    ("Ask the stakeholders at two cafes what feels urgent.", "Ask the stakeholders at two cafes what feels urgent."),
    ("개발자를 뽑아 앱을 만드세요. 사장님 세 분께 물어보세요.", "사장님 세 분께 물어보세요."),
])
def test_a_way_past_that_needs_other_people_is_dropped_in_either_language(step, kept):
    from justshipit.contracts.idea_review import owner_alone
    assert owner_alone(step) == kept
