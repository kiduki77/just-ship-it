"""Shared builders for the people tests."""
from justshipit.contracts.people import CandidateOut, FindPeopleOut, FindThreadsOut, ThreadOut
from justshipit.db.models import Job, Person
from justshipit.services import people, roadmap
from sqlalchemy import delete

import cycle_kit
from profile_kit import describe

PROBLEM = "작은 백엔드 팀이 긴 서버 로그에서 장애 원인을 찾느라 몇 시간을 쓴다"
ISSUE_URL = "https://github.com/acme/logtool/issues/41"
ISSUE_QUOTE = "I lose hours grepping through 2GB of server logs just to find the root cause of one crash"


def line(text, provenance="said", id=None, person=None):
    return {"id": id or "", "text": text, "provenance": provenance, "source": "kickoff:1:2", "person": person}


def make_business(s, *, known=None, name="로그 도구", in_stage=True, buyer=("developers",), reach=("communities",)):
    """A business whose brief is approved. `known` lines go in an `이미 아는 사람` section; None leaves it out. Its owner has
    confirmed who buys and how they are reached (a developer CLI by default)."""
    b = roadmap.create_business(s, name=name, one_liner="서버 로그에서 원인을 찾는 CLI")
    sections = roadmap.empty_sections()
    for sec in sections:
        if sec["key"] == "known_people" and known is not None:
            sec["lines"] = known
        if sec["key"] == "customer_problem":
            sec["lines"] = [line(PROBLEM), line("지금은 grep과 Datadog을 쓴다", "confirmed")]
        if sec["key"] == "one_liner":
            sec["lines"] = [line("서버 로그에서 원인을 찾는 오픈소스 CLI")]
        if sec["key"] == "goal_deadline":
            sec["lines"] = [line("석 달 안에 첫 사용자 100명")]
    brief = roadmap.new_brief_version(s, b.id, sections=sections, assumptions=[], author="project_manager", approved=True)
    s.execute(delete(Job).where(Job.kind == "derive_door_rules", Job.payload["brief_id"].as_integer() == brief.id))
    if in_stage:
        cycle_kit.stage(s, b)
    s.commit()
    describe(s, b.id, buyer, reach)
    return b


def add(s, b, handle="alice", *, source="public_problem", platform="github", stage="found", quote=ISSUE_QUOTE,
        url=None, prep=None) -> Person:
    person, outcome = people.insert(s, b.id, people.NewPerson(
        platform, handle, url or f"https://github.com/acme/logtool/issues/by-{handle}", source,
        why="로그에서 원인을 찾는 데 시간을 쓴다고 씀", quote=quote, prep=prep or {}), lang="ko")
    assert outcome == "created"
    person.stage = stage
    s.flush()
    return person


def candidate(handle="alice", url=ISSUE_URL, quote=ISSUE_QUOTE, **kw) -> CandidateOut:
    base = dict(platform="github", handle=handle, display_name=handle.title(), where_found_url=url, quote=quote,
                why="로그에서 장애 원인을 찾느라 시간을 쓴다고 직접 썼다", from_alternative=False,
                questions=["마지막으로 원인을 못 찾아 헤맨 때가 언제였나요?", "그때 어떤 도구를 쓰셨나요?"],
                talking_points=["오픈소스 CLI를 만들고 있다고 먼저 밝힌다"])
    return CandidateOut(**{**base, **kw})


def thread(url="https://stackoverflow.com/questions/77/how-to-trace-a-crash", asker="bob", **kw) -> ThreadOut:
    base = dict(platform="stackoverflow", where_found_url=url, asker_handle=asker,
                quote="How do I find which request caused the crash in a 3GB nginx log file",
                asks="큰 nginx 로그에서 장애를 일으킨 요청을 찾는 방법을 묻는다",
                talking_points=["요청 id로 묶어서 본다", "시간대를 먼저 좁힌다"])
    return ThreadOut(**{**base, **kw})


class Researcher:
    """A stand-in for the research agent that returns what the test scripted and keeps what it was asked."""

    def __init__(self, out):
        self.out, self.calls = out, []

    def __call__(self, agent, purpose, prompt, output_model, *, project_id, system_override=None, **kw):
        assert (agent, purpose, project_id) == ("research", "people", None)
        assert output_model in (FindPeopleOut, FindThreadsOut)
        self.calls.append({"prompt": prompt, "system": system_override})
        return self.out


def page(*texts):
    return ("url_ok", "HTTP 200", " ".join(texts))


class Web:
    """Pages by link. A link it does not know is unreachable."""

    def __init__(self, pages: dict):
        self.pages, self.opened = pages, []

    def __call__(self, url):
        self.opened.append(url)
        return self.pages.get(url, ("url_unreachable", "HTTP 404", None))


def issue_page(handle="alice", quote=ISSUE_QUOTE, extra=""):
    return page(f"{handle} opened this issue on Nov 2 {quote} {extra} 3 comments")
