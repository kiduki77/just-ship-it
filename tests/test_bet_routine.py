"""A routine spends money on a schedule, so only the owner starts one: a retro's routine verdict files a decision card, and
nothing runs until it is approved. The server allows the verdict only with evidence and under a cap, and the owner can
stop a routine at any time."""
from datetime import UTC, datetime

import pytest
from justshipit.api.app import app
from justshipit.contracts.requests import HumanDecision, RequestStatus
from justshipit.contracts.retro import RoutineSpec
from justshipit.db.models import Bet, MetricPoint, Project, Schedule
from justshipit.db.session import session_scope
from justshipit.operations import scheduler
from justshipit.services import bet_loop, control, metrics, policy, roadmap
from justshipit.settings import tool_policy
from bet_loop_kit import (
    answer,
    card_of,
    clear_jobs,
    install_people,
    jobs_of,
    make_bet,
    make_business,
    reset_world,
    set_bets,
)
from fastapi.testclient import TestClient
from notify_kit import choose, notice_jobs, wipe
from sqlalchemy import select
from test_bet_retro import ScriptedProjectManager, bets_from, cards_for, draft, ended_bet, next_bet, people_draft, settle


pytestmark = pytest.mark.usefixtures("outside_cycles")


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    set_bets(failed_grace_minutes=0)
    clear_jobs("run_project", "bet_retro", "routine_run")
    yield
    reset_world()


@pytest.fixture
def business(s):
    return make_business(s)


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000", headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def propose(s, business, monkeypatch, *, first_value=40, every=7, **kw) -> Bet:
    install_people(first_value=first_value)
    bet = ended_bet(s, business, **kw)
    make = draft if first_value >= 30 else people_draft
    settle(bet.id, ScriptedProjectManager(make(verdict="routine", routine=RoutineSpec(every_days=every, what="이슈 정리"))), monkeypatch)
    s.expire_all()
    return s.get(Bet, bet.id)


def schedule_of(s, bet_id: int):
    s.expire_all()
    return bet_loop.routine_schedule(s, bet_id)


def test_a_routine_verdict_files_a_card_for_the_owner_and_starts_nothing(s, business, monkeypatch):
    bet = propose(s, business, monkeypatch, every=7)

    card = card_of(s, bet.id)

    assert bet.status == "DONE" and bet.verdict == "routine"
    assert card.status == RequestStatus.PENDING.value and card.policy_mode != "auto"
    assert schedule_of(s, bet.id) is None
    assert bet_loop.run_routine(bet.id) is None, "the company cannot run a routine the owner has not approved"
    assert not jobs_of(s, "routine_run", bet_id=bet.id)
    assert bet_loop.routine_state(s, bet) == "proposed"


def test_the_routine_card_shows_the_evidence_the_schedule_and_the_per_run_budget(s, business, monkeypatch):
    bet = propose(s, business, monkeypatch, every=7)

    stored = card_of(s, bet.id).action_params["card"]

    assert stored["headline"] == "이 실험을 정기 업무로 돌릴까요?" and "도움 됨 40명" in stored["why"]
    metrics_ = {m["label"]: m["value"] for m in stored["metrics"]}
    assert "도움 됨 40명" in metrics_["근거"]
    assert metrics_["주기"] == "7일마다" and metrics_["한 번 돌 때 예산"] == "$6 환산"
    assert metrics_["한 달 최대"] == "$30 환산"
    assert [o["label"] for o in stored["options"]] == ["정기 업무로 돌리기", "하지 않기"]


def test_approving_the_card_makes_the_schedule_and_runs_the_first_round(s, business, monkeypatch):
    bet = propose(s, business, monkeypatch, every=3)

    answer(card_of(s, bet.id).id)

    sched = schedule_of(s, bet.id)
    assert sched.enabled and sched.interval_hours == 72 and bet_loop.routine_state(s, bet) == "active"
    [first] = jobs_of(s, "routine_run", bet_id=bet.id)
    assert first.status == "queued"
    assert bet_loop.run_routine(bet.id, "이슈 정리") is not None


def test_rejecting_the_card_leaves_no_routine(s, business, monkeypatch):
    bet = propose(s, business, monkeypatch)

    answer(card_of(s, bet.id).id, HumanDecision.REJECT)

    assert schedule_of(s, bet.id) is None and bet_loop.routine_state(s, s.get(Bet, bet.id)) == "declined"
    assert bet_loop.run_routine(bet.id) is None


def test_the_project_manager_cannot_decide_the_routine_card_by_a_door_rule():
    assert tool_policy()[bet_loop.ROUTINE_CARD] == "approve"
    assert bet_loop.ROUTINE_CARD not in policy.PROJECT_MANAGER_DECIDABLE


def test_a_routine_with_nobody_at_first_value_becomes_continue_with_a_reason(s, business, monkeypatch):
    install_people(first_value=0)
    bet = ended_bet(s, business)

    got = settle(bet.id, ScriptedProjectManager(people_draft(verdict="routine")), monkeypatch)

    s.expire_all()
    assert got.verdict == "continue" and got.status == "DONE"
    assert any("정기 업무로 돌릴 근거가 없어요" in n for n in got.retro["notes"])
    assert card_of(s, bet.id) is None and schedule_of(s, bet.id) is None
    assert bet_loop.run_routine(bet.id) is None


def test_a_metric_point_at_first_value_is_evidence_too(s, business, monkeypatch):
    with session_scope() as x:
        metrics.record(x, business.id, "people_first_value", 2.0, datetime.now(UTC), source="people")
    bet = propose(s, business, monkeypatch, first_value=0)

    assert bet.verdict == "routine" and card_of(s, bet.id) is not None
    assert s.scalar(select(MetricPoint.id).where(MetricPoint.business_id == business.id)) is not None


def test_a_business_keeps_at_most_the_set_number_of_routines_counting_cards_waiting(s, business, monkeypatch):
    set_bets(max_routines=1)
    first = propose(s, business, monkeypatch)
    install_people(first_value=40)
    second = ended_bet(s, business, title="둘째 도전")

    got = settle(second.id, ScriptedProjectManager(draft(verdict="routine", routine=RoutineSpec(every_days=7, what="정리"))), monkeypatch)

    s.expire_all()
    assert got.verdict == "continue" and any("정기 업무가 이미 1개" in n for n in got.retro["notes"])
    assert card_of(s, second.id) is None and card_of(s, first.id) is not None


def test_the_default_cap_is_two_and_a_lowered_cap_stops_an_approval(s, business, monkeypatch):
    first = propose(s, business, monkeypatch)
    answer(card_of(s, first.id).id)
    set_bets(max_routines=1)
    second_bet = make_bet(s, business, title="다른 도전")
    other = Bet(business_id=business.id, title="x", pitch="p", status="DONE", verdict="routine")
    s.add(other)
    s.commit()

    with session_scope() as x:
        started = bet_loop.start_routine(x, x.get(Bet, other.id), every_days=7, what="정리", lang="ko")

    assert started is False and schedule_of(s, other.id) is None and second_bet.id
    assert bet_loop.routine_schedule(s, first.id).enabled


def test_stopping_a_routine_disables_the_schedule_and_pauses_the_running_round(s, business, monkeypatch, api):
    bet = propose(s, business, monkeypatch, every=2)
    answer(card_of(s, bet.id).id)
    clear_jobs("routine_run")
    round_id = bet_loop.run_routine(bet.id, "이슈 정리")

    r = api.post(f"/api/bets/{bet.id}/routine/stop")

    assert r.status_code == 200, r.text
    s.expire_all()
    assert not schedule_of(s, bet.id).enabled and bet_loop.routine_state(s, s.get(Bet, bet.id)) == "stopped"
    assert control.is_paused(s.get(Project, round_id))
    assert bet_loop.run_routine(bet.id) is None
    with session_scope() as x:
        sched = x.scalar(select(Schedule).where(Schedule.name == bet_loop.routine_name(bet.id)))
        sched.next_run_at = datetime.now(UTC)
    with session_scope() as x:
        fired = scheduler.tick(x)
    assert schedule_of(s, bet.id).id not in fired


def test_stopping_a_bet_that_is_no_routine_is_refused(s, business, api):
    bet = make_bet(s, business)

    assert api.post(f"/api/bets/{bet.id}/routine/stop").status_code == 409


def test_the_business_lists_its_routines_with_their_state(s, business, monkeypatch, api):
    bet = propose(s, business, monkeypatch, every=7)

    [row] = api.get(f"/api/businesses/{business.id}/routines").json()

    assert row["bet_id"] == bet.id and row["state"] == "proposed" and row["per_run_budget_usd"] == 6.0
    answer(card_of(s, bet.id).id)
    [row] = api.get(f"/api/businesses/{business.id}/routines").json()
    assert row["state"] == "active" and row["every_days"] == 7
    assert roadmap.BetStatus.DONE.value == s.get(Bet, bet.id).status


@pytest.fixture
def slack():
    wipe()
    choose(kinds=["decision_needed"])
    yield
    wipe()


def test_a_routine_card_and_an_out_of_scope_next_bet_card_each_tell_the_owner_once(s, business, slack, monkeypatch):
    bet = propose(s, business, monkeypatch, every=7)
    routine = card_of(s, bet.id)
    [stray] = ended_and_settled_with_stray_bet(s, business, monkeypatch)
    s.commit()

    keys = sorted(j.idempotency_key for j in notice_jobs())

    assert keys == sorted([f"notify:decision_needed:REQ-{routine.id}:pending",
                           f"notify:decision_needed:REQ-{stray.id}:pending"])
    assert routine.blocking is False and stray.blocking is False, "the notice comes from the card kind, not from blocking work"


def ended_and_settled_with_stray_bet(s, business, monkeypatch):
    bet = ended_bet(s, business)
    out = settle(bet.id, ScriptedProjectManager(draft(bets=[next_bet("범위 밖 도전", scope=False)])), monkeypatch)
    return cards_for(s, [b.id for b in bets_from(s, out.id)])
