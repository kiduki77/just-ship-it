"""The people finder searches where the business's people are: 당근 and 네이버 for a 망원동 dog-walking service,
GitHub and Hacker News for a CLI, and its city's subreddit for an English-language dog walker, whose Nextdoor and Facebook
groups the owner looks at. It never pretends to search a place behind a login; it names that place to the owner."""
from pathlib import Path

import pytest
from justshipit.api.app import app
from justshipit.contracts.people import FindPeopleOut, FindThreadsOut
from justshipit.db.models import Job, Person
from justshipit.db.session import session_scope
from itertools import product

from justshipit.services import app_settings, people, people_finder, roadmap
from justshipit.services.profile import core, vocab
from fastapi.testclient import TestClient
from people_kit import Researcher, Web, candidate, line, page, thread
from people_kit import make_business as make_dev_business
from profile_kit import describe
from sqlalchemy import delete, select

POST = "https://www.daangn.com/kr/community/mangwon-dog-walk-123"
QUOTE = "퇴근이 늦어서 강아지 산책을 거의 못 시켜요 같은 동네에서 저녁에 산책해 주실 분 있을까요"


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000", headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def walks(s):
    b = roadmap.create_business(s, name="동네산책", one_liner="맞벌이 보호자와 동네 산책 도우미를 잇는 동네 기반 산책 대행")
    sections = roadmap.empty_sections()
    for sec in sections:
        if sec["key"] == "customer_problem":
            sec["lines"] = [line("망원동 일대 맞벌이 반려견 보호자, 평일 저녁 산책을 못 시킨다")]
        if sec["key"] == "path":
            sec["lines"] = [line("당근 동네생활과 동네 단톡방에서 시작")]
        if sec["key"] == "goal_deadline":
            sec["lines"] = [line("6주 안에 보호자 30명")]
    brief = roadmap.new_brief_version(s, b.id, sections=sections, assumptions=[], author="project_manager", approved=True)
    s.execute(delete(Job).where(Job.kind == "derive_door_rules", Job.payload["brief_id"].as_integer() == brief.id))
    s.commit()
    describe(s, b.id, ["consumers"], ["local"])
    return b


def local_candidate(**kw):
    return candidate("산책맘", POST, QUOTE, platform="daangn", why="퇴근이 늦어 산책을 못 시킨다고 동네생활에 직접 썼다",
                     questions=["마지막으로 산책을 못 시킨 날 어떻게 하셨나요?"], talking_points=[], **kw)


def find(monkeypatch, b, candidates, web):
    agent = Researcher(FindPeopleOut(candidates=candidates, searched="당근 동네생활을 찾았다"))
    monkeypatch.setattr(people_finder, "run_agent", agent)
    return people_finder.find_people(b.id, fetch=web, lang="ko"), agent


def search_block(prompt: str) -> str:
    return prompt.split("## Where to search")[1].split("\n## ")[0]


def test_a_local_business_is_searched_on_local_places_and_never_on_developer_sites(s, monkeypatch):
    b = walks(s)
    _, agent = find(monkeypatch, b, [], Web({}))
    prompt, system = agent.calls[0]["prompt"], agent.calls[0]["system"]
    block = search_block(prompt)
    assert "`daangn`" in block and "당근 동네생활" in block and "네이버 카페" in block
    for dev in ("GitHub", "Hacker News", "Stack Overflow", "Reddit"):
        assert dev not in block and dev not in system, dev
    assert "카카오 오픈채팅" in prompt and "login" in prompt, "a place the finder cannot open is named, not searched"


def test_a_local_post_that_checks_out_is_kept_and_a_github_find_is_dropped(s, monkeypatch):
    b = walks(s)
    web = Web({POST: page(f"산책맘 · 망원동 · 3시간 전 {QUOTE} 댓글 2")})
    gh = candidate("alice", "https://github.com/acme/logtool/issues/41")
    report, _ = find(monkeypatch, b, [local_candidate(), gh], web)
    assert (report.added, dict(report.dropped)) == (1, {"platform": 1})
    with session_scope() as x:
        kept = x.scalar(select(Person).where(Person.business_id == b.id))
        assert (kept.platform, kept.handle, kept.where_found_url) == ("daangn", "산책맘", POST)


def test_a_local_post_whose_quote_is_not_on_the_page_is_still_dropped(s, monkeypatch):
    b = walks(s)
    report, _ = find(monkeypatch, b, [local_candidate()], Web({POST: page("로그인하고 동네 이야기를 확인해 보세요")}))
    assert report.added == 0 and dict(report.dropped) == {"quote_not_found": 1}


def test_a_local_candidate_with_a_phone_number_is_dropped(s, monkeypatch):
    b = walks(s)
    web = Web({POST: page(f"산책맘 {QUOTE}")})
    report, _ = find(monkeypatch, b, [local_candidate(display_name="산책맘 010-1234-5678")], web)
    assert report.added == 0 and dict(report.dropped) == {"contact_info": 1}


def test_a_developer_tool_is_still_searched_on_developer_sites(s, monkeypatch):
    b = make_dev_business(s)
    _, agent = find(monkeypatch, b, [], Web({}))
    block = search_block(agent.calls[0]["prompt"])
    assert "`github`" in block and "`hn`" in block and "당근" not in block


def test_threads_for_a_local_business_come_from_local_places(s, monkeypatch):
    b = walks(s)
    gh = thread()
    agent = Researcher(FindThreadsOut(threads=[gh], searched="찾았다"))
    monkeypatch.setattr(people_finder, "run_agent", agent)
    report = people_finder.find_threads(b.id, fetch=Web({}), lang="ko")
    assert dict(report.dropped) == {"platform": 1}
    assert "당근 동네생활" in search_block(agent.calls[0]["prompt"])


def places_of(api, monkeypatch, b, lang):
    monkeypatch.setattr(app_settings, "language", lambda: lang)
    return api.get(f"/api/businesses/{b.id}/people").json()["places"]


def test_the_people_list_tells_the_owner_where_the_company_looks_and_where_only_the_owner_can(s, api, monkeypatch):
    b = walks(s)
    places = places_of(api, monkeypatch, b, "ko")
    assert places["searched"] == ["당근 동네생활", "네이버 카페", "네이버 블로그"]
    assert places["by_hand"] == ["카카오 오픈채팅", "인스타그램"]
    assert places["platforms"][0] == "daangn" and "github" in places["platforms"], "every platform stays addable"
    english = places_of(api, monkeypatch, b, "en")
    assert english["searched"] == ["Daangn community", "Naver Cafe", "Naver Blog"], "the same places, named in English"
    assert english["by_hand"] == ["KakaoTalk Open Chat", "Instagram"]


# ------------------------------------------------------------------ an English-language business

def english(s, name, one_liner, customer, buyer=("consumers",), reach=("local",)):
    b = roadmap.create_business(s, name=name, one_liner=one_liner)
    sections = roadmap.empty_sections()
    for sec in sections:
        if sec["key"] == "customer_problem":
            sec["lines"] = [line(customer)]
        if sec["key"] == "goal_deadline":
            sec["lines"] = [line("30 users in 6 weeks")]
    brief = roadmap.new_brief_version(s, b.id, sections=sections, assumptions=[], author="project_manager", approved=True)
    s.execute(delete(Job).where(Job.kind == "derive_door_rules", Job.payload["brief_id"].as_integer() == brief.id))
    s.commit()
    describe(s, b.id, buyer, reach)
    return b


def dog_walks(s):
    return english(s, "Walkies", "Dog walks for busy neighbors in the same town",
                   "Working dog owners in our town can't walk their dogs on weekday evenings")


def test_an_english_local_business_is_searched_on_reddit_and_leaves_nextdoor_and_facebook_to_the_owner(s, api,
                                                                                                        monkeypatch):
    b = dog_walks(s)
    places = places_of(api, monkeypatch, b, "en")
    assert places["searched"] == ["Reddit"]
    assert places["by_hand"] == ["Nextdoor", "Facebook groups", "Instagram"]
    assert places["platforms"][:4] == ["reddit", "nextdoor", "facebook_groups", "instagram"]
    assert places_of(api, monkeypatch, b, "ko")["by_hand"] == ["Nextdoor", "페이스북 그룹", "인스타그램"]

    _, agent = find(monkeypatch, b, [], Web({}))
    prompt = agent.calls[0]["prompt"]
    block = search_block(prompt)
    assert "`reddit`" in block and "in English" in block
    for elsewhere in ("daangn", "naver", "github", "hn"):
        assert f"`{elsewhere}" not in block, elsewhere
    assert "Nextdoor, Facebook groups, Instagram: their posts need a login" in prompt


def test_an_english_local_business_drops_a_find_on_a_korean_place(s, monkeypatch):
    b = dog_walks(s)
    report, _ = find(monkeypatch, b, [local_candidate()], Web({POST: page(f"산책맘 {QUOTE}")}))
    assert report.added == 0 and dict(report.dropped) == {"platform": 1}


def test_an_english_general_business_is_searched_on_reddit_and_leaves_facebook_instagram_and_x_to_the_owner(
        s, api, monkeypatch):
    b = english(s, "Pocket Budget", "A budgeting app for first-time renters",
                "First-time renters lose track of what they spend each month", reach=("communities",))
    places = places_of(api, monkeypatch, b, "en")
    assert (places["searched"], places["by_hand"]) == (["Reddit"], ["Facebook groups", "Instagram", "X"])


def test_the_places_owners_add_by_hand_have_no_host_and_no_place_gained_one():
    hosts = {key: place.hosts for key, place in people.PLACES.items() if place.hosts}
    assert hosts == {
        "github": ("github.com", "www.github.com"),
        "reddit": ("reddit.com", "www.reddit.com", "old.reddit.com"),
        "hn": ("news.ycombinator.com",),
        "stackoverflow": ("stackoverflow.com", "serverfault.com", "superuser.com", "askubuntu.com",
                          "unix.stackexchange.com", "softwareengineering.stackexchange.com", "dba.stackexchange.com",
                          "devops.stackexchange.com", "security.stackexchange.com"),
        "daangn": ("daangn.com", "www.daangn.com"),
        "naver_cafe": ("cafe.naver.com", "m.cafe.naver.com"),
        "naver_blog": ("blog.naver.com", "m.blog.naver.com"),
    }, "the finder opens only these hosts"
    for buyer, reach, market in product(vocab.live_codes("buyer"), vocab.live_codes("reach"), ("en", "ko")):
        searched, by_hand = people.where(_described(buyer, reach), market)
        assert all(people.PLACES[k].hosts or k == "forum" for k in searched), (buyer, reach, market)
        assert not any(people.PLACES[k].hosts for k in by_hand), (buyer, reach, market)


def _described(buyer, reach):
    from datetime import UTC, datetime

    now = datetime(2026, 11, 13, tzinfo=UTC)
    return core.assemble(1, [core.Value(i, line_, code, None, "confirmed", "console", None, None, "owner", now)
                             for i, (line_, code) in enumerate((("buyer", buyer), ("reach", reach)), 1)])


def test_a_business_nobody_has_described_is_not_searched_and_the_model_is_not_asked(s, monkeypatch):
    b = make_dev_business(s, buyer=(), reach=())
    agent = Researcher(FindPeopleOut(candidates=[], searched="nothing"))
    monkeypatch.setattr(people_finder, "run_agent", agent)
    report = people_finder.find_people(b.id, fetch=Web({}), lang="ko")
    assert agent.calls == [] and report.notes == [people_finder.NOTHING_TO_SEARCH]
    threads = Researcher(FindThreadsOut(threads=[], searched="nothing"))
    monkeypatch.setattr(people_finder, "run_agent", threads)
    assert people_finder.find_threads(b.id, fetch=Web({}), lang="ko").notes == [people_finder.NOTHING_TO_SEARCH]
    assert threads.calls == []


def test_the_owner_can_add_someone_met_on_kakao_or_instagram(s, api):
    b = walks(s)
    for platform in ("kakao_openchat", "instagram"):
        r = api.post(f"/api/businesses/{b.id}/people", json={"handle": f"견주-{platform}", "platform": platform,
                                                             "source": "warm", "url": None, "quote": None, "why": None})
        assert r.status_code == 200, r.text
    with session_scope() as x:
        got = set(x.scalars(select(Person.platform).where(Person.business_id == b.id)))
    assert got == {"kakao_openchat", "instagram"}


def test_the_console_names_every_place_a_person_can_be_met():
    catalog = (Path(__file__).resolve().parents[1] / "web/src/i18n/areas/business.ts").read_text(encoding="utf-8")
    assert [k for k in people.PLACES if f'"business.platform.{k}"' not in catalog] == []
