"""Builders for the bet loop tests: a business with an approved brief, running bets, a people list that holds what a test
tells it, and a GitHub that answers from recorded replies."""
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from justshipit.contracts.requests import HumanDecision, HumanResponse
from justshipit.db.models import AgentRequest, AppSetting, Bet, Business, Job, Project
from justshipit.db.session import session_scope
from justshipit.services import app_settings, people_link, requests, roadmap
from justshipit.services.metrics import Reply
from justshipit.services.people_link import Quote, UsedTwice
from sqlalchemy import delete, text, update

import cycle_kit
from profile_kit import describe

FIXTURES = Path(__file__).parent / "fixtures" / "github"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
SCOPE_LINE, GOAL_LINE, RED_LINE, PROBLEM_LINE = "l-scope", "l-goal", "l-red", "l-problem"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakePeople:
    """A people list that holds whatever a test sets. It records who the collector put on it."""
    available = True

    def __init__(self, contacted=0, talked=0, first_value=0, repeat=0, quotes=(), used=(0, 0)):
        self.counts = {"contacted": contacted, "talked": talked, "first_value": first_value, "repeat": repeat}
        self._quotes, self._used = list(quotes), used
        self.inbound: list[tuple[str, str, str, str | None]] = []
        self.participation = 0

    def north_star(self, s, business_id):
        return dict(self.counts)

    def add_inbound(self, s, business_id, platform, handle, url, quote=None, *, lang):
        self.inbound.append((platform, handle, url, quote))
        return True

    def quotes(self, s, business_id, limit):
        return self._quotes[:limit]

    def used_twice(self, s, business_id):
        return UsedTwice(*self._used)

    def request_participation(self, s, business_id):
        self.participation += 1
        return True


def quote(person_id: int, text_: str, stage="first_value") -> Quote:
    return Quote(person_id, f"PERSON-{person_id}", text_, f"https://github.com/o/r/issues/{person_id}", stage)


def install_people(**kw) -> FakePeople:
    people = FakePeople(**kw)
    people_link.use(people)
    return people


def no_people() -> None:
    """Install a list that holds nobody, the way a deploy without a usable people list behaves."""
    people_link.use(people_link.NoPeople())


LOOP_JOBS = ("run_project", "derive_door_rules", "routine_run", "find_threads", "github_collect", "post_reactions",
             "promotion_week", "bet_sweep")


def reset_world() -> None:
    """Back to F11's people list, default settings, no post tasks and none of the jobs the loop queues, for the next
    test. The post reactions are read for every business's done posts, so a post another test file left behind would be
    polled by this one. A leftover job would match another file's `%:{request id}` job lookups once the id sequences
    meet or appear in the door-rule derivation count, so those are removed. A retro job is only settled, not removed:
    the sweep finds an earlier test's bet in review again and would queue its retro anew, and a failed one would show
    in the alerts of another file. A bet an earlier test left running or listening is stopped: the sweep reads every
    business's live bets against the real clock, and a window a test counted from NOW has closed by the day the suite
    runs, so the leftover would be moved in the next test's sweep and show up in what that test asserts."""
    people_link.use(None)
    with session_scope() as s:
        s.execute(delete(AppSetting).where(AppSetting.section == "bets"))
        s.execute(text("DELETE FROM owner_tasks WHERE kind = 'post'"))
        s.execute(delete(Job).where(Job.kind.in_(LOOP_JOBS)))
        s.execute(update(Job).where(Job.kind == "bet_retro", Job.status != "done").values(status="done"))
        s.execute(update(Bet).where(Bet.status.in_(roadmap.LIVE_BET_STATUSES), Bet.loop_started_at.is_not(None))
                  .values(status=roadmap.BetStatus.STOPPED.value))


def set_bets(**body) -> None:
    with session_scope() as s:
        app_settings.save(s, "bets", body, actor="test")


def line(id_: str, text_: str, provenance="said") -> dict:
    return {"id": id_, "text": text_, "provenance": provenance, "source": "test"}


def make_business(s, name="터미널 문서화", repo: str | None = None, *, buyer=("developers",),
                  reach=("communities",)) -> Business:
    """A developer CLI whose owner has confirmed who buys and how they are reached; `buyer`/`reach` change that."""
    b = roadmap.create_business(s, name=name, one_liner="개발자가 쓴 터미널 명령을 자동으로 문서화하는 오픈소스 CLI")
    sections = [
        {"key": "customer_problem", "title": "대상 고객과 문제", "lines": [line(PROBLEM_LINE, "터미널에서 한 일을 문서로 남기지 못하는 개발자")]},
        {"key": "scope", "title": "범위", "lines": [line(SCOPE_LINE, "오픈소스 CLI와 그 문서, 개발자 커뮤니티에서의 알리기")]},
        {"key": "goal_deadline", "title": "목표와 기한", "lines": [line(GOAL_LINE, "3개월 안에 첫 가치에 닿은 사람 30명")]},
        {"key": "red_lines", "title": "금지선", "lines": [line(RED_LINE, "유료 광고는 하지 않는다")]},
    ]
    roadmap.new_brief_version(s, b.id, sections=sections, assumptions=[], author="owner", approved=True)
    b.github_repo = repo
    cycle_kit.stage(s, b)
    s.commit()
    describe(s, b.id, buyer, reach)
    return b


def make_bet(s, business: Business, status="PICKED", **kw) -> Bet:
    fields = {"title": "Show HN 없이 서브레딧 두 곳", "pitch": "서브레딧 두 곳에 설치 방법을 올려 반응을 본다",
              "question": "개발자가 설치까지 가는가", "audience": "터미널을 매일 쓰는 개발자",
              "exit_signal": "첫 가치 5명", "circuit_breaker": "2주 안에 첫 가치 0명이면 멈춘다",
              "appetite_days": 14, "budget_usd": 6.0, **kw}
    bet = roadmap.create_bet(s, business.id, **fields)
    bet.status = status
    s.commit()
    return bet


def start(bet_id: int) -> Bet:
    """Move a picked bet to RUNNING the way the owner does."""
    with session_scope() as s:
        return roadmap.move_bet(s, bet_id, "RUNNING")


def run_of(s, bet_id: int) -> Project | None:
    return s.query(Project).filter(Project.bet_id == bet_id).one_or_none()


def end_run(s, run: Project, status: str, error: str | None = None) -> None:
    """The run ends the way a worker ends one: the project settles and the job that ran it is done."""
    run.status, run.error = status, error
    s.execute(text("UPDATE jobs SET status = 'done' WHERE lock_key = :k AND status IN ('queued', 'running')"),
              {"k": f"project:{run.id}"})
    s.commit()


def card_of(s, bet_id: int, kind: str = "bet.routine") -> AgentRequest | None:
    """The newest decision card the loop filed for a bet."""
    s.expire_all()
    rows = s.query(AgentRequest).filter(AgentRequest.action_kind == kind).order_by(AgentRequest.id.desc()).all()
    return next((r for r in rows if (r.action_params or {}).get("bet_id") == bet_id), None)


def answer(req_id: int, decision: HumanDecision = HumanDecision.APPROVE) -> None:
    """The owner answers a card."""
    with session_scope() as s:
        requests.respond(s, req_id, HumanResponse(decision=decision))


def jobs_of(s, kind: str, **payload) -> list[Job]:
    rows = s.query(Job).filter(Job.kind == kind).all()
    return [j for j in rows if all(j.payload.get(k) == v for k, v in payload.items())]


def clear_jobs(*kinds: str) -> None:
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL, lease_token = NULL "
                       "WHERE status = 'queued' AND kind = ANY(:kinds)"), {"kinds": list(kinds)})


class FakeHttp:
    """Answers a Fetch from a table of routes keyed by URL (a stargazers page by `url#page`). Records every call."""

    def __init__(self, routes: dict[str, Reply | Callable[[], Reply]]):
        self.routes = routes
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, url, params=None, headers=None):
        params, headers = dict(params or {}), dict(headers or {})
        self.calls.append((url, params, headers))
        key = f"{url}#{params['page']}" if "page" in params else url
        route = self.routes.get(key, self.routes.get(url))
        if route is None:
            return Reply(404, {"message": "Not Found"}, {})
        return route() if callable(route) else route

    def urls(self) -> list[str]:
        return [c[0] for c in self.calls]


API = "https://api.github.com"


def github(repo="owner/repo", *, authed=True, rate_limited=False, reset_at: int | None = None) -> FakeHttp:
    """The recorded replies for a repo. Without `authed` the stargazers list and the traffic endpoints answer 401 as GitHub
    does for a call with no token. `rate_limited` makes every call a rate limit refusal that resets at `reset_at` (epoch
    seconds, an hour after NOW by default). A test that runs the job on the real clock passes its own."""
    if rate_limited:
        reset = reset_at if reset_at is not None else int(NOW.timestamp()) + 3600
        limited = Reply(403, {"message": "API rate limit exceeded"},
                        {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)})
        return FakeHttp({f"{API}/repos/{repo}": limited})
    base = f"{API}/repos/{repo}"
    unauth = Reply(401, fixture("stargazers.unauth.json"), {})
    routes: dict[str, Reply | Callable[[], Reply]] = {
        base: Reply(200, fixture("repo.json"), {}),
        f"{base}/issues": Reply(200, fixture("issues.json"), {}),
        f"{base}/releases": Reply(200, fixture("releases.json"), {}),
    }
    if authed:
        link = f'<{base}/stargazers?per_page=100&page=2>; rel="next", <{base}/stargazers?per_page=100&page=2>; rel="last"'
        routes |= {f"{base}/stargazers#1": Reply(200, fixture("stargazers_page1.json"), {"link": link}),
                   f"{base}/stargazers#2": Reply(200, fixture("stargazers_page2.json"), {}),
                   f"{base}/traffic/views": Reply(200, fixture("traffic_views.json"), {}),
                   f"{base}/traffic/clones": Reply(200, fixture("traffic_clones.json"), {}),
                   f"{base}/traffic/popular/referrers": Reply(200, fixture("referrers.json"), {}),
                   f"{base}/traffic/popular/paths": Reply(200, fixture("paths.json"), {})}
    else:
        routes |= {f"{base}/stargazers#1": unauth, f"{base}/traffic/views": unauth, f"{base}/traffic/clones": unauth,
                   f"{base}/traffic/popular/referrers": unauth, f"{base}/traffic/popular/paths": unauth}
    return FakeHttp(routes)
