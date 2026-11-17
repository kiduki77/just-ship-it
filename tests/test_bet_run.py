"""A bet and its run: moving a bet to RUNNING starts the run, the run's end or a limit moves the bet to REVIEW, and at
most `bets.max_running` bets of a business run at once."""
from datetime import timedelta

import pytest
from justshipit.db.models import Bet, Project, Schedule
from justshipit.db.session import session_scope
from justshipit.operations import scheduler
from justshipit.services import app_settings, bet_loop, budget, control, roadmap
from justshipit.services.control import ControlError
from bet_loop_kit import (
    NOW,
    clear_jobs,
    end_run,
    jobs_of,
    make_bet,
    make_business,
    reset_world,
    run_of,
    set_bets,
    start,
)
from pydantic import ValidationError
from sqlalchemy import select


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    set_bets(failed_grace_minutes=0)
    clear_jobs("run_project", "bet_retro")
    yield
    reset_world()


@pytest.fixture
def business(s):
    return make_business(s)


def test_moving_a_bet_to_running_starts_its_run_with_the_hypothesis_and_limits(s, business):
    bet = make_bet(s, business, appetite_days=10, budget_usd=7.5)

    moved = start(bet.id)

    s.expire_all()
    run = run_of(s, bet.id)
    assert moved.status == "RUNNING" and moved.started_at is not None
    assert run.business_id == business.id and run.status == "CREATED"
    assert run.budget_usd == 7.5, "the bet's budget is the run's budget, so the budget check that stops every run stops this one"
    for expected in ("개발자가 설치까지 가는가", "첫 가치 5명", "2주 안에 첫 가치 0명이면 멈춘다", "기간 10일", "예산 $7.5"):
        assert expected in run.goal
    queued = jobs_of(s, "run_project", project_id=run.id)
    assert [j.status for j in queued] == ["queued"] and queued[0].max_attempts == 1


def test_a_second_move_to_running_starts_nothing_twice(s, business):
    bet = make_bet(s, business)
    start(bet.id)
    start(bet.id)

    s.expire_all()
    assert s.scalar(select(Project.id).where(Project.bet_id == bet.id)) is not None
    assert len(s.scalars(select(Project).where(Project.bet_id == bet.id)).all()) == 1
    assert len(jobs_of(s, "run_project", project_id=run_of(s, bet.id).id)) == 1


def test_a_bet_the_owner_runs_by_hand_starts_no_run(s, business):
    bet = make_bet(s, business, run_mode="owner")
    start(bet.id)

    s.expire_all()
    assert run_of(s, bet.id) is None and s.get(Bet, bet.id).status == "RUNNING"


def test_a_picked_bet_that_already_has_a_project_reuses_it(s, business):
    bet = make_bet(s, business)
    p = Project(slug="own-run", name="own", goal="g", status="CREATED", budget_usd=3, business_id=business.id, bet_id=bet.id)
    s.add(p)
    s.commit()
    start(bet.id)

    s.expire_all()
    assert len(s.scalars(select(Project).where(Project.bet_id == bet.id)).all()) == 1
    assert [j.payload["project_id"] for j in jobs_of(s, "run_project", project_id=p.id)] == [p.id]


def test_one_bet_runs_at_a_time_by_default_and_the_refusal_leaves_the_second_untouched(s, business):
    first, second = make_bet(s, business), make_bet(s, business, title="둘째")
    start(first.id)

    with pytest.raises(ControlError, match="상한 1개"):
        start(second.id)

    s.expire_all()
    assert s.get(Bet, second.id).status == "PICKED" and run_of(s, second.id) is None
    assert roadmap.running_count(s, business.id) == 1


def test_raising_the_cap_to_three_lets_three_run_and_refuses_the_fourth(s, business):
    set_bets(max_running=3)
    bets = [make_bet(s, business, title=f"도전 {i}") for i in range(4)]
    for b in bets[:3]:
        start(b.id)

    with pytest.raises(ControlError, match="상한 3개"):
        start(bets[3].id)

    s.expire_all()
    assert roadmap.running_count(s, business.id) == 3
    assert [s.get(Bet, b.id).status for b in bets] == ["RUNNING"] * 3 + ["PICKED"]


def test_the_cap_counts_one_business_at_a_time(s, business):
    other = make_business(s, name="다른 사업")
    a, b = make_bet(s, business), make_bet(s, other)
    start(a.id)
    start(b.id)

    s.expire_all()
    assert s.get(Bet, a.id).status == s.get(Bet, b.id).status == "RUNNING"


@pytest.mark.parametrize("bad", [0, 4, -1])
def test_the_cap_setting_stays_between_one_and_three(bad):
    with pytest.raises(ValidationError), session_scope() as s:
        app_settings.save(s, "bets", {"max_running": bad}, actor="test")
    assert app_settings.effective().bets.max_running == 1


# ---------------------------------------------------------------- a run's end and the bet's limits

def running(s, business, **kw) -> tuple[Bet, Project]:
    bet = make_bet(s, business, **kw)
    start(bet.id)
    s.expire_all()
    return s.get(Bet, bet.id), run_of(s, bet.id)


@pytest.mark.parametrize("status,error,cause", [
    ("FAILED", "RuntimeError: boom", "run_failed"),
    ("FAILED", "BudgetExceeded: project budget 6.0 USD-eq would be exceeded", "budget_limit"),
])
def test_a_run_that_fails_moves_its_bet_to_review_with_the_cause_and_queues_one_retro(s, business, status, error, cause):
    bet, run = running(s, business)
    end_run(s, run, status, error)

    with session_scope() as x:
        moved = bet_loop.sweep(x, lang="ko")
    with session_scope() as x:
        bet_loop.sweep(x, lang="ko")

    s.expire_all()
    got = s.get(Bet, bet.id)
    assert (bet.id, cause) in moved
    assert got.status == "REVIEW" and got.end_cause == cause and got.finished_at is not None
    assert [j.status for j in jobs_of(s, "bet_retro", bet_id=bet.id)] == ["queued"], "a second sweep must not queue a second retro"


def test_a_run_still_working_inside_its_limits_is_left_alone(s, business):
    bet, run = running(s, business, appetite_days=14, budget_usd=6.0)
    run.status, run.spent_usd = "RUNNING", 2.0
    s.commit()

    with session_scope() as x:
        moved = bet_loop.sweep(x, NOW, lang="ko")

    assert bet.id not in [b for b, _ in moved]


def test_the_days_limit_pauses_the_run_and_ends_the_bet(s, business):
    bet, run = running(s, business, appetite_days=3)
    bet.started_at = NOW - timedelta(days=4)
    run.status = "RUNNING"
    s.commit()

    with session_scope() as x:
        moved = bet_loop.sweep(x, NOW, lang="ko")

    s.expire_all()
    got, paused = s.get(Bet, bet.id), s.get(Project, run.id)
    assert (bet.id, "time_limit") in moved and got.status == "REVIEW" and got.end_cause == "time_limit"
    assert control.is_paused(paused), "the run stops at its next gate instead of working on after the days ran out"


def test_the_days_limit_is_counted_from_the_start_not_from_the_pick(s, business):
    bet, run = running(s, business, appetite_days=3)
    bet.picked_at, bet.started_at = NOW - timedelta(days=30), NOW - timedelta(days=2)
    run.status = "RUNNING"
    s.commit()

    with session_scope() as x:
        assert bet.id not in [b for b, _ in bet_loop.sweep(x, NOW, lang="ko")]


def test_spending_the_bets_budget_ends_the_bet_and_the_run_cannot_start_another_call(s, business):
    bet, run = running(s, business, budget_usd=2.0)
    run.status, run.spent_usd = "RUNNING", 1.2
    s.commit()

    with pytest.raises(budget.BudgetExceeded):
        budget.reserve(run.id, None, "work")
    run.spent_usd = 2.0
    s.commit()
    with session_scope() as x:
        moved = bet_loop.sweep(x, NOW, lang="ko")

    s.expire_all()
    assert (bet.id, "budget_limit") in moved and s.get(Bet, bet.id).end_cause == "budget_limit"


def test_a_bet_the_owner_runs_by_hand_still_ends_when_its_days_run_out(s, business):
    bet = make_bet(s, business, run_mode="owner", appetite_days=2)
    start(bet.id)
    s.expire_all()
    s.get(Bet, bet.id).started_at = NOW - timedelta(days=3)
    s.commit()

    with session_scope() as x:
        assert (bet.id, "time_limit") in bet_loop.sweep(x, NOW, lang="ko")


# ---------------------------------------------------------------- the state machine

def test_review_is_reached_only_from_a_live_bet_and_leaves_only_to_done_or_stopped():
    assert roadmap.BET_TRANSITIONS[roadmap.BetStatus.RUNNING] >= {roadmap.BetStatus.LISTENING, roadmap.BetStatus.REVIEW}
    assert roadmap.BET_TRANSITIONS[roadmap.BetStatus.LISTENING] == {roadmap.BetStatus.REVIEW}
    assert roadmap.BET_TRANSITIONS[roadmap.BetStatus.REVIEW] == {roadmap.BetStatus.RUNNING, roadmap.BetStatus.DONE,
                                                                  roadmap.BetStatus.STOPPED}
    reaching = {a for a, to in roadmap.BET_TRANSITIONS.items() if roadmap.BetStatus.REVIEW in to}
    assert reaching == {roadmap.BetStatus.RUNNING, roadmap.BetStatus.LISTENING}


def test_the_owner_ending_a_running_bet_goes_through_review_and_queues_the_retro(s, business):
    bet, run = running(s, business)
    run.status = "RUNNING"
    s.commit()

    with session_scope() as x:
        roadmap.move_bet(x, bet.id, "REVIEW", reason="이만하면 됐다")

    s.expire_all()
    got = s.get(Bet, bet.id)
    assert got.status == "REVIEW" and got.end_cause == "owner" and control.is_paused(s.get(Project, run.id))
    assert [j.status for j in jobs_of(s, "bet_retro", bet_id=bet.id)] == ["queued"]


def test_a_bet_in_review_holds_no_running_slot_and_shows_in_the_now_lane(s, business):
    first = make_bet(s, business)
    start(first.id)
    with session_scope() as x:
        roadmap.move_bet(x, first.id, "REVIEW")
    second = make_bet(s, business, title="둘째")

    start(second.id)

    s.expire_all()
    view = roadmap.roadmap(s, business.id, lang="ko")
    assert set(view["lanes"]["now"]) == {first.id, second.id}
    assert roadmap.running_count(s, business.id) == 1


# ---------------------------------------------------------------- the clock

def test_the_scheduler_keeps_a_sweep_and_gives_a_business_its_weekly_promotion_and_github_read(s):
    quiet = make_business(s, name="깃허브 없음")
    repo = make_business(s, name="깃허브 있음", repo="owner/repo")

    with session_scope() as x:
        scheduler.sync(x)

    s.expire_all()
    rows = s.scalars(select(Schedule).where(Schedule.kind.in_(["bet_sweep", "promotion_week", "github_collect"]))).all()
    by_kind = {(r.kind, r.payload.get("business_id")) for r in rows}
    assert ("bet_sweep", None) in by_kind
    assert ("promotion_week", quiet.id) in by_kind and ("promotion_week", repo.id) in by_kind
    assert ("github_collect", repo.id) in by_kind and ("github_collect", quiet.id) not in by_kind
    assert next(r for r in rows if r.kind == "github_collect" and r.payload["business_id"] == repo.id).interval_hours == 24
    assert next(r for r in rows if r.kind == "promotion_week").interval_hours == 168


def test_a_business_without_an_approved_brief_gets_no_promotion(s):
    b = roadmap.create_business(s, name="지시서 없는 사업", one_liner="x")
    s.commit()

    with session_scope() as x:
        scheduler.sync(x)

    s.expire_all()
    assert not s.scalars(select(Schedule).where(Schedule.kind == "promotion_week",
                                                Schedule.payload["business_id"].as_integer() == b.id)).all()
