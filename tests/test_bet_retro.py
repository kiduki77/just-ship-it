"""The retro of an ended bet: bet to run to retro, the two modes, what the server checks in the CEO's answer, next bets
inside and outside the brief's scope, the routine, and a usage wait that leaves the bet in review."""
import re
from datetime import UTC, datetime, timedelta

import pytest
from justshipit.api.app import app
from justshipit.contracts.retro import (
    Cause,
    NextBet,
    PeopleRetroDraft,
    PeopleRetroOut,
    RetroDraft,
    RoutineSpec,
    UserQuote,
)
from justshipit.db.models import ActivityEvent, AgentRequest, Bet, Job, MetricPoint, Project, Schedule
from justshipit.db.session import session_scope
from justshipit.i18n import Lang
from justshipit.operations import growth, scheduler
from justshipit.services import bet_loop, bet_retro, metrics, promotion, queue, roadmap, usage
from justshipit.services.usage import MustWait
from justshipit.workers import runner
from bet_loop_kit import (
    GOAL_LINE,
    RED_LINE,
    SCOPE_LINE,
    answer,
    card_of,
    clear_jobs,
    end_run,
    install_people,
    jobs_of,
    make_bet,
    make_business,
    quote,
    reset_world,
    run_of,
    set_bets,
    start,
)
from fastapi.testclient import TestClient
from i18n_kit import HANGUL
from sqlalchemy import select, text
from test_korean_style import smells_in

pytestmark = pytest.mark.usefixtures("outside_cycles")


REAL_NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    set_bets(failed_grace_minutes=0)
    clear_jobs("run_project", "bet_retro", "routine_run")
    with session_scope() as s:
        s.execute(text("DELETE FROM channel_drops"))
    yield
    reset_world()


@pytest.fixture
def business(s):
    return make_business(s)


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000", headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


class ScriptedProjectManager:
    def __init__(self, answer):
        self.answer, self.prompts, self.models, self.calls = answer, [], [], 0

    def __call__(self, agent, purpose, prompt, output_model, *, project_id, system_override=None, **kw):
        assert (agent, purpose) == ("project_manager", "retro")
        assert system_override and "looking back on an experiment" in system_override
        self.calls += 1
        self.prompts.append(prompt)
        self.models.append(output_model)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer(prompt) if callable(self.answer) else self.answer


def next_bet(title="서브레딧 세 곳으로", *, scope=True, lines=(SCOPE_LINE,), **kw) -> NextBet:
    base = dict(title=title, pitch="이번에는 설치 영상을 붙여 올린다", audience="터미널 개발자", question="영상이 설치를 늘리는가",
                appetite_days=14, budget_usd=5.0, exit_signal="첫 가치 5명", circuit_breaker="2주 안에 0명이면 멈춘다",
                in_scope=scope, brief_line_ids=list(lines) if scope else [], reason="방문은 있었는데 설치가 없었다")
    return NextBet(**{**base, **kw})


def draft(*, verdict="continue", causes=None, bets=None, **kw) -> RetroDraft:
    base = dict(causes=causes if causes is not None else [Cause(cause="설치 과정이 길다", evidence_ids=[], confidence="mid")],
                counter_evidence=[], verdict=verdict, verdict_reason="방문은 늘었지만 첫 가치가 없다", routine=None,
                drop_channels=[], next_bets=bets or [next_bet()])
    return RetroDraft(**{**base, **kw})


def people_draft(*, quotes=(), verdict="continue", bets=None, **kw) -> PeopleRetroDraft:
    base = dict(did="서브레딧 두 곳에 설치 방법을 올렸다", users_said=list(quotes), next="설치 영상을 만든다", verdict=verdict,
                verdict_reason="말해 준 사람이 있다", routine=None, drop_channels=[], next_bets=bets or [next_bet()])
    return PeopleRetroDraft(**{**base, **kw})


def ended_bet(s, business, cause="time_limit", **kw) -> Bet:
    """A bet that ended six days after it started, swept into REVIEW: by default its run finished and its six-day window
    closed, else its run failed or ran out of budget."""
    if cause == "time_limit":
        kw.setdefault("appetite_days", 6)
    bet = make_bet(s, business, **kw)
    start(bet.id)
    s.expire_all()
    bet = s.get(Bet, bet.id)
    bet.started_at = REAL_NOW - timedelta(days=6)
    run = run_of(s, bet.id)
    end_run(s, run, *{"time_limit": ("DONE", None), "run_failed": ("FAILED", "RuntimeError: x"),
                      "budget_limit": ("FAILED", "BudgetExceeded: project budget")}[cause])
    with session_scope() as x:
        bet_loop.sweep(x, lang="ko")
    s.expire_all()
    assert s.get(Bet, bet.id).status == "REVIEW"
    return s.get(Bet, bet.id)


def settle(bet_id: int, project_manager: ScriptedProjectManager, monkeypatch, *, lang: Lang = "ko") -> Bet:
    monkeypatch.setattr(bet_retro, "run_agent", project_manager)
    bet_retro.run(bet_id, lang=lang)
    with session_scope() as s:
        bet = s.get(Bet, bet_id)
        s.expunge(bet)
        return bet


def cards_for(s, bet_ids: list[int]) -> list[AgentRequest]:
    """The decision cards the retro filed for these next bets."""
    rows = s.scalars(select(AgentRequest).where(AgentRequest.action_kind == "project.direction_change"))
    return [r for r in rows if (r.action_params or {}).get("bet_id") in bet_ids]


def bets_from(s, bet_id: int) -> list[Bet]:
    s.expire_all()
    return list(s.scalars(select(Bet).where(Bet.source_bet_id == bet_id).order_by(Bet.id)))


# ---------------------------------------------------------------- bet to run to retro

def test_a_bet_runs_listens_out_its_window_and_is_settled_by_one_retro_that_proposes_the_next_bet(s, business, monkeypatch):
    install_people(first_value=40)
    bet = make_bet(s, business)
    start(bet.id)
    s.expire_all()
    run = run_of(s, bet.id)
    assert run.status == "CREATED" and s.get(Bet, bet.id).status == "RUNNING"
    with session_scope() as x:
        x.get(Project, run.id).status = "DONE"
    with session_scope() as x:
        bet_loop.sweep(x, lang="ko")
    s.expire_all()
    assert s.get(Bet, bet.id).status == "LISTENING" and not jobs_of(s, "bet_retro", bet_id=bet.id)
    with session_scope() as x:
        x.get(Bet, bet.id).started_at = REAL_NOW - timedelta(days=15)
        bet_loop.sweep(x, lang="ko")
    s.expire_all()
    assert s.get(Bet, bet.id).status == "REVIEW" and len(jobs_of(s, "bet_retro", bet_id=bet.id)) == 1

    project_manager = ScriptedProjectManager(draft())
    settled = settle(bet.id, project_manager, monkeypatch)

    assert settled.status == "DONE" and settled.verdict == "continue" and settled.retro["mode"] == "stats"
    assert settled.finished_at is not None and settled.outcome == "방문은 늘었지만 첫 가치가 없다"
    [nxt] = bets_from(s, bet.id)
    assert nxt.origin == "project_manager" and nxt.status == "PICKED" and nxt.title == "서브레딧 세 곳으로"
    assert project_manager.calls == 1 and project_manager.models == [RetroDraft]
    assert usage.purpose("retro").owner_facing, "the retro is a call the owner is waiting on"


def test_the_retro_is_made_once_even_if_the_job_runs_twice(s, business, monkeypatch):
    install_people(first_value=40)
    bet = ended_bet(s, business)
    project_manager = ScriptedProjectManager(draft())

    settle(bet.id, project_manager, monkeypatch)
    settle(bet.id, project_manager, monkeypatch)

    assert project_manager.calls == 1 and len(bets_from(s, bet.id)) == 1


def test_the_stored_hypothesis_and_numbers_are_the_servers_not_the_models(s, business, monkeypatch):
    install_people(first_value=40)
    bet = ended_bet(s, business)
    with session_scope() as x:
        metrics.record(x, business.id, "github_stars_total", 100.0, REAL_NOW - timedelta(days=7), source="github")
        metrics.record(x, business.id, "github_stars_total", 112.0, metrics.day_of(REAL_NOW), source="github")
    project_manager = ScriptedProjectManager(draft())

    settled = settle(bet.id, project_manager, monkeypatch)

    obs = settled.retro["observed"]
    assert settled.retro["hypothesis"] == "개발자가 설치까지 가는가"
    star = next(o for o in obs["by_stage"]["interest"] if o["name"] == "GitHub 별 합계")
    assert star["value"] == 12 and star["evidence_id"].startswith("mp-")
    assert obs["window"]["start"] and obs["window"]["end"]
    assert "Mode: stats" in project_manager.prompts[0] and f"[{star['evidence_id']}]" in project_manager.prompts[0]
    assert "Stars and visits are the interest stage" in bet_retro._system()


def test_a_cause_that_cites_an_id_the_model_was_not_shown_loses_it_and_drops_to_low(s, business, monkeypatch):
    install_people(first_value=40)
    bet = ended_bet(s, business)
    with session_scope() as x:
        metrics.record(x, business.id, "github_stars_total", 5.0, metrics.day_of(REAL_NOW), source="github")
    real_id = f"mp-{s.scalar(select(MetricPoint.id).where(MetricPoint.business_id == business.id))}"
    causes = [Cause(cause="진짜 근거", evidence_ids=[real_id], confidence="high"),
              Cause(cause="지어낸 근거", evidence_ids=["mp-999999", real_id], confidence="high"),
              Cause(cause="근거 없음", evidence_ids=[], confidence="high")]

    settled = settle(bet.id, ScriptedProjectManager(draft(causes=causes)), monkeypatch)

    got = settled.retro["causes"]
    assert (got[0]["evidence_ids"], got[0]["confidence"]) == ([real_id], "high")
    assert (got[1]["evidence_ids"], got[1]["confidence"]) == ([real_id], "high")
    assert (got[2]["evidence_ids"], got[2]["confidence"]) == ([], "low")
    assert any("mp-999999" not in n and "지어낸 근거" in n for n in settled.retro["notes"])


# ---------------------------------------------------------------- the two modes

@pytest.mark.parametrize("reached,mode", [(0, "people_quotes"), (29, "people_quotes"), (30, "stats"), (200, "stats")])
def test_thirty_people_at_first_value_or_beyond_switch_the_retro_from_quotes_to_numbers(s, business, reached, mode):
    install_people(first_value=reached)
    with session_scope() as x:
        assert bet_retro.mode_for(x, business.id) == mode


def test_under_thirty_people_the_retro_is_three_lines_of_what_people_said(s, business, monkeypatch):
    install_people(first_value=3, quotes=[quote(5, "설치가 3분이면 끝나서 놀랐다"), quote(6, "문서가 자동으로 생기는 건 좋은데 이름은 어렵다")])
    bet = ended_bet(s, business)
    answer = people_draft(quotes=[UserQuote(person_ref="PERSON-5", quote="설치가 3분이면 끝나서 놀랐다"),
                                  UserQuote(person_ref="PERSON-6", quote="이름은 어렵다")])
    project_manager = ScriptedProjectManager(answer)

    settled = settle(bet.id, project_manager, monkeypatch)

    retro = settled.retro
    assert retro["mode"] == "people_quotes" and project_manager.models == [PeopleRetroDraft]
    assert retro["did"] and retro["next"] and [q["person_ref"] for q in retro["users_said"]] == ["PERSON-5", "PERSON-6"]
    assert "observed" not in retro and "causes" not in retro, "no statistics while there are fewer than 30 people"
    assert "Mode: people_quotes" in project_manager.prompts[0] and "[PERSON-5]" in project_manager.prompts[0] and "설치가 3분이면" in project_manager.prompts[0]
    assert PeopleRetroOut.model_validate(retro)


def test_a_quote_the_person_did_not_say_is_dropped_and_no_quote_is_made_up(s, business, monkeypatch):
    install_people(first_value=2, quotes=[quote(5, "설치가 3분이면 끝나서 놀랐다")])
    bet = ended_bet(s, business)
    answer = people_draft(quotes=[UserQuote(person_ref="PERSON-5", quote="정말 최고의 도구입니다"),
                                  UserQuote(person_ref="PERSON-9", quote="설치가 3분이면 끝나서 놀랐다"),
                                  UserQuote(person_ref="PERSON-5", quote="설치가 3분이면 끝나서 놀랐다")])

    settled = settle(bet.id, ScriptedProjectManager(answer), monkeypatch)

    assert settled.retro["users_said"] == [{"person_ref": "PERSON-5", "quote": "설치가 3분이면 끝나서 놀랐다"}]
    assert len([n for n in settled.retro["notes"] if "기록에 없어서 뺐어요" in n]) == 2


def test_with_nobody_to_quote_the_retro_says_so_instead_of_inventing_users(s, business, monkeypatch):
    bet = ended_bet(s, business)
    settled = settle(bet.id, ScriptedProjectManager(people_draft(quotes=[UserQuote(person_ref="PERSON-1", quote="좋아요")])), monkeypatch)
    assert settled.retro["users_said"] == [] and "아직 사용자가 한 말이 없어요." in settled.retro["notes"]


# ---------------------------------------------------------------- the verdict

def test_a_stop_verdict_ends_the_bet_as_stopped(s, business, monkeypatch):
    install_people(first_value=40)
    bet = ended_bet(s, business)
    settled = settle(bet.id, ScriptedProjectManager(draft(verdict="stop")), monkeypatch)
    assert (settled.status, settled.verdict) == ("STOPPED", "stop")


def routine_settled(s, business, monkeypatch, spec: RoutineSpec) -> Bet:
    """A bet settled with the routine verdict and the owner's approval of its card."""
    install_people(first_value=40)
    bet = ended_bet(s, business)
    settle(bet.id, ScriptedProjectManager(draft(verdict="routine", routine=spec)), monkeypatch)
    answer(card_of(s, bet.id).id)
    clear_jobs("run_project", "routine_run")
    return bet


def test_an_approved_routine_schedules_the_work_and_frees_the_slot(s, business, monkeypatch):
    spec = RoutineSpec(every_days=7, what="매주 새 이슈를 정리하고 답한다")
    bet = routine_settled(s, business, monkeypatch, spec)

    s.expire_all()
    sched = s.scalar(select(Schedule).where(Schedule.kind == "routine_run", Schedule.name == bet_loop.routine_name(bet.id)))
    settled = s.get(Bet, bet.id)
    assert settled.status == "DONE" and settled.verdict == "routine"
    assert sched.interval_hours == 168 and sched.enabled and sched.payload["bet_id"] == bet.id and "이슈" in sched.payload["what"]
    assert roadmap.running_count(s, business.id) == 0
    other = make_bet(s, business, title="다음")
    start(other.id)
    s.expire_all()
    assert s.get(Bet, other.id).status == "RUNNING", "the routine holds no bet slot"


def test_a_routine_round_starts_a_fresh_project_with_no_bet_and_waits_for_the_last_round(s, business, monkeypatch):
    bet = routine_settled(s, business, monkeypatch, RoutineSpec(every_days=3, what="이슈 정리"))

    first = bet_loop.run_routine(bet.id, "이슈 정리")
    second = bet_loop.run_routine(bet.id, "이슈 정리")

    s.expire_all()
    run = s.get(Project, first)
    assert second is None and run.bet_id is None and run.business_id == business.id
    assert run.context["routine_of_bet"] == bet.id and "이슈 정리" in run.goal and run.name.startswith("[Routine]")
    assert len(jobs_of(s, "run_project", project_id=first)) == 1
    run.status = "DONE"
    s.commit()
    assert bet_loop.run_routine(bet.id, "이슈 정리") not in (None, first)


def test_the_scheduler_fires_the_routine_when_its_time_comes(s, business, monkeypatch):
    bet = routine_settled(s, business, monkeypatch, RoutineSpec(every_days=2, what="정리"))
    with session_scope() as x:
        sched = x.scalar(select(Schedule).where(Schedule.name == bet_loop.routine_name(bet.id)))
        sched.next_run_at = REAL_NOW - timedelta(minutes=1)
        sid = sched.id
    with session_scope() as x:
        fired = scheduler.tick(x)

    s.expire_all()
    assert sid in fired
    queued = [j for j in s.scalars(select(Job).where(Job.kind == "routine_run")) if j.payload.get("bet_id") == bet.id and j.status == "queued"]
    assert len(queued) == 1 and queued[0].payload["what"] == "정리"
    assert "routine_run" in growth.HANDLERS


@pytest.mark.parametrize("cause", ["run_failed", "budget_limit"])
def test_a_run_that_failed_or_ran_out_of_budget_is_not_turned_into_a_routine(s, business, monkeypatch, cause):
    install_people(first_value=40)
    bet = ended_bet(s, business, cause=cause)
    spec = RoutineSpec(every_days=7, what="정리")

    settled = settle(bet.id, ScriptedProjectManager(draft(verdict="routine", routine=spec)), monkeypatch)

    s.expire_all()
    assert settled.verdict == "change" and settled.status == "DONE"
    assert any("정기 업무 대신 방향을 바꾸기로" in n for n in settled.retro["notes"])
    assert s.scalar(select(Schedule.id).where(Schedule.name == bet_loop.routine_name(bet.id))) is None


# ---------------------------------------------------------------- next bets and the brief's scope

def test_next_bets_in_scope_are_picked_up_to_the_free_slots_and_the_rest_wait(s, business, monkeypatch):
    install_people(first_value=40)
    bet = ended_bet(s, business)
    bets = [next_bet("하나", lines=[SCOPE_LINE]), next_bet("둘", lines=[GOAL_LINE])]

    settle(bet.id, ScriptedProjectManager(draft(bets=bets)), monkeypatch)

    first, second = bets_from(s, bet.id)
    assert (first.status, second.status) == ("PICKED", "PROPOSED"), "one slot is free, so the CEO picks one"
    assert "자리가 없어" in second.status_reason
    assert not [c for c in cards_for(s, [first.id, second.id])]


def test_with_three_slots_all_three_in_scope_bets_are_picked(s, business, monkeypatch):
    set_bets(max_running=3)
    install_people(first_value=40)
    bet = ended_bet(s, business)
    settle(bet.id, ScriptedProjectManager(draft(bets=[next_bet(f"도전 {i}") for i in range(3)])), monkeypatch)
    assert [b.status for b in bets_from(s, bet.id)] == ["PICKED"] * 3


def test_a_next_bet_outside_the_brief_becomes_a_decision_card_and_is_not_picked(s, business, monkeypatch):
    install_people(first_value=40)
    bet = ended_bet(s, business)
    out_of_scope = next_bet("유료로 바꾸기", scope=False, reason="무료 사용자가 많아서")
    cites_a_red_line = next_bet("광고를 산다", scope=True, lines=[RED_LINE])
    cites_nothing_real = next_bet("없는 줄", scope=True, lines=["l-never-shown"])

    settle(bet.id, ScriptedProjectManager(draft(bets=[out_of_scope, cites_a_red_line, cites_nothing_real])), monkeypatch)

    made = bets_from(s, bet.id)
    assert [b.status for b in made] == ["PROPOSED"] * 3, "none of them is in scope, so none is picked"
    cards = cards_for(s, [b.id for b in made])
    assert len(cards) == 3 and {c.status for c in cards} == {"PENDING"}
    card = next(c for c in cards if "유료로 바꾸기" in c.title).action_params["card"]
    assert card["headline"].startswith("'") and "후보로 올릴까요" in card["headline"] and card["reversible"] is True
    assert [o["verdict"] for o in card["options"]] == ["approve", "reject"]
    assert [o["label"] for o in card["options"]] == ["후보로 올리기", "하지 않기"] and card["recommendation"] == "무료 사용자가 많아서"
    assert all("결정으로 올렸어요" in b.status_reason and "REQ-" not in b.status_reason for b in made)


def test_the_citation_decides_scope_not_the_models_claim(s, business, monkeypatch):
    install_people(first_value=40)
    bet = ended_bet(s, business)
    claims_scope_without_a_line = next_bet("그냥 범위 안", scope=True, lines=[])
    claims_scope_without_a_line.brief_line_ids = []

    settle(bet.id, ScriptedProjectManager(draft(bets=[claims_scope_without_a_line])), monkeypatch)

    [only] = bets_from(s, bet.id)
    assert only.status == "PROPOSED" and "결정으로 올렸어요" in only.status_reason and "REQ-" not in only.status_reason


def test_the_project_manager_picks_but_never_starts_a_bet(s, business, monkeypatch):
    install_people(first_value=40)
    bet = ended_bet(s, business)
    settle(bet.id, ScriptedProjectManager(draft()), monkeypatch)
    [nxt] = bets_from(s, bet.id)
    assert nxt.status == "PICKED" and run_of(s, nxt.id) is None and nxt.started_at is None


def test_at_most_three_next_bets_are_kept(s, business):
    with pytest.raises(ValueError):
        draft(bets=[next_bet(f"b{i}") for i in range(4)])
    with pytest.raises(ValueError):
        draft(next_bets=[])


# ---------------------------------------------------------------- dropping a channel

def test_a_channel_is_dropped_only_when_it_earned_the_judgement(s, business, monkeypatch):
    from justshipit.services import owner_tasks
    install_people(first_value=40)
    bet = ended_bet(s, business)
    with session_scope() as x:
        for i in range(3):
            t = owner_tasks.create_post_task(x, business.id, {"channel": "reddit", "week": f"w{i}", "material_keys": [f"m{i}"]}, "글")
            owner_tasks.done(x, t.id, link="https://www.reddit.com/r/opensource/comments/a/b/")
        metrics.record(x, business.id, "people_first_value", 0.0, metrics.day_of(REAL_NOW), source="people")

    settled = settle(bet.id, ScriptedProjectManager(draft(drop_channels=["reddit", "hacker_news", "nonexistent"])), monkeypatch)

    s.expire_all()
    with session_scope() as x:
        assert promotion.dropped(x, business.id) == {"reddit"}
    notes = " ".join(settled.retro["notes"])
    assert "hacker_news 채널은 그만두지 않았어요" in notes and "올린 글이 없어" in notes and "nonexistent" in notes


# ---------------------------------------------------------------- waiting

def run_retro_job(bet_id: int, monkeypatch, project_manager: ScriptedProjectManager) -> Job:
    monkeypatch.setattr(bet_retro, "run_agent", project_manager)
    with session_scope() as x:
        x.execute(text("UPDATE jobs SET status='done' WHERE kind='bet_retro' AND payload->>'bet_id' = :b AND status='queued'"),
                  {"b": str(bet_id)})
        jid = queue.enqueue(x, "bet_retro", {"bet_id": bet_id}, max_attempts=3, idempotency_key=f"retro-test:{bet_id}:{project_manager.calls}:{id(project_manager)}").id
    with session_scope() as x:
        claimed = queue.claim(x, "worker-test", ["bet_retro"])
        assert claimed.id == jid
        token = claimed.lease_token
    runner._execute(jid, "bet_retro", {"bet_id": bet_id}, growth.HANDLERS | {}, runner.Runs(), queue.Lease(jid, token)) if False else \
        runner._execute(jid, "bet_retro", {"bet_id": bet_id}, {"bet_retro": growth.HANDLERS["bet_retro"]}, runner.Runs(),
                        queue.Lease(jid, token))
    with session_scope() as x:
        row = x.get(Job, jid)
        x.expunge(row)
        return row


def test_a_usage_wait_in_the_retro_call_leaves_the_bet_in_review_and_the_job_for_later(s, business, monkeypatch):
    install_people(first_value=40)
    bet = ended_bet(s, business)
    until = REAL_NOW + timedelta(hours=6)

    job = run_retro_job(bet.id, monkeypatch, ScriptedProjectManager(MustWait(until, "usage_cap_day", "하루 사용 상한에 닿았습니다")))

    s.expire_all()
    got = s.get(Bet, bet.id)
    assert (job.status, job.attempts, job.wait_reason) == ("queued", 0, "usage_cap_day") and job.run_after == until
    assert got.status == "REVIEW" and got.retro is None and bets_from(s, bet.id) == []

    again = run_retro_job(bet.id, monkeypatch, ScriptedProjectManager(draft()))
    s.expire_all()
    assert again.status == "done" and s.get(Bet, bet.id).status == "DONE"


# ---------------------------------------------------------------- reading it back

def test_the_api_says_a_retro_is_waiting_and_then_returns_it(s, business, api, monkeypatch):
    install_people(first_value=40)
    bet = ended_bet(s, business)

    waiting = api.get(f"/api/bets/{bet.id}/retro").json()
    assert waiting["waiting"] is True and waiting["retro"] is None and waiting["end_cause"] == "time_limit"

    settle(bet.id, ScriptedProjectManager(draft()), monkeypatch)
    done = api.get(f"/api/bets/{bet.id}/retro").json()
    assert done["waiting"] is False and done["verdict"] == "continue" and done["retro"]["mode"] == "stats"
    assert api.get("/api/bets/999999/retro").status_code == 409


# ---------------------------------------------------------------- the owner's language

def english_bet(title: str, **kw) -> NextBet:
    return next_bet(title, pitch="Post the install video this time", audience="terminal developers",
                    question="Does a video lead to installs?", exit_signal="5 people got value",
                    circuit_breaker="Stop at zero after two weeks", reason=MODEL_REASON, **kw)


MODEL_REASON = "Visits came in but nobody installed"
# A 해요체 sentence, with a count after it in parentheses: "시작해요(3명)."
HAEYO_END = re.compile(r"요(?:\([^()]*\))?[.?]$")
# A record code the owner can't read: the retro's notes and a next experiment's reason name the experiment or say
# "your decision" instead (round 7).
RECORD = re.compile(r"\b(?:REQ|BET|OT|DEC)-\d+")


def server_words(s, bet_id: int) -> tuple[list[str], list[str]]:
    """What the server wrote for the owner in one retro, as (sentences, labels): the notes, the next experiments'
    reasons, the activity line, and the words of every card it filed. What the model wrote is English in these
    fixtures, so any Hangul here came from the server."""
    settled, made = s.get(Bet, bet_id), bets_from(s, bet_id)
    ids = [bet_id, *(b.id for b in made)]
    cards = [r for r in s.scalars(select(AgentRequest)) if (r.action_params or {}).get("bet_id") in ids]
    sentences = [*settled.retro["notes"], *(b.status_reason for b in made)]
    labels = [e.summary for e in s.scalars(select(ActivityEvent).where(ActivityEvent.verb == "bet_retro",
                                                                       ActivityEvent.entity_id == bet_id))]
    for r in cards:
        card = r.action_params["card"]
        sentences += [card["owner_does"], card["why"], *(o["summary"] for o in card["options"]),
                      *(risk for o in card["options"] for risk in o["risks"])]
        labels += [r.title, r.summary, card["headline"], *(o["label"] for o in card["options"]),
                   *(m[k] for m in card["metrics"] for k in ("label", "value", "basis"))]
    return sentences, labels


def settle_in(s, business, monkeypatch, lang: Lang, scenario: str) -> int:
    """`failed`: a failed run the CEO wants as a routine, a cause citing an unseen id, and three next experiments (one
    picked, one waiting for a slot, one beyond the plan). `routine`: a routine verdict the server lets through."""
    install_people(first_value=40)
    bet = ended_bet(s, business, cause="run_failed" if scenario == "failed" else "time_limit", title="Show HN launch",
                    pitch="Post a launch thread on Show HN", question="Do developers install it?")
    routine = RoutineSpec(every_days=7, what="Answer new issues")
    if scenario == "failed":
        answer = draft(verdict="routine", routine=routine, verdict_reason="Visits rose but nobody got value",
                       causes=[Cause(cause="Install takes too long", evidence_ids=["mp-999999"], confidence="high")],
                       bets=[english_bet("Three subreddits", lines=[SCOPE_LINE]), english_bet("A demo", lines=[GOAL_LINE]),
                             english_bet("Charge for it", scope=False)])
    else:
        answer = draft(verdict="routine", routine=routine, verdict_reason="People came back", causes=[],
                       bets=[english_bet("Keep going")])
    settle(bet.id, ScriptedProjectManager(answer), monkeypatch, lang=lang)
    return bet.id


@pytest.mark.parametrize("scenario", ["failed", "routine"])
def test_in_english_everything_the_retro_writes_for_the_owner_is_english(s, business, monkeypatch, scenario):
    bet_id = settle_in(s, business, monkeypatch, "en", scenario)

    sentences, labels = server_words(s, bet_id)

    assert len(sentences) >= 4 and labels
    assert [t for t in sentences + labels if HANGUL.search(t)] == []
    assert [t for t in sentences if RECORD.search(t)] == []
    if scenario == "failed":
        notes = " ".join(s.get(Bet, bet_id).retro["notes"])
        assert "changes direction instead of making a routine" in notes and "Charge for it" in notes
        assert any("Every slot is taken" in t for t in sentences)
    else:
        card = card_of(s, bet_id).action_params["card"]
        assert card["headline"] == "Make this experiment a routine?" and "40 got value" in card["why"]
        assert {m["label"]: m["value"] for m in card["metrics"]}["How often"] == "Every 7 days"


@pytest.mark.parametrize("scenario", ["failed", "routine"])
def test_in_korean_the_retro_writes_plain_haeyo_korean(s, business, monkeypatch, scenario):
    bet_id = settle_in(s, business, monkeypatch, "ko", scenario)

    sentences, labels = server_words(s, bet_id)

    assert [(t, smells_in(t)) for t in sentences + labels if smells_in(t)] == []
    assert [t for t in sentences if not (HAEYO_END.search(t) or t.endswith(MODEL_REASON))] == []
    assert all(HANGUL.search(t) for t in sentences)
    assert [t for t in sentences if RECORD.search(t)] == []
