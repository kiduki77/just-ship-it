"""A bet in review: a retro that never arrived is queued again and then flagged, a failed run gets a grace period before
its bet goes to review, a retried run takes the bet back, and a decision card is bound to the bet it concerns."""
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from justshipit.api.app import app
from justshipit.contracts.requests import HumanDecision
from justshipit.db.models import Bet, Project
from justshipit.db.session import session_scope
from justshipit.services import bet_loop, control, queue, roadmap
from justshipit.services.control import ControlError
from bet_loop_kit import (
    answer,
    card_of,
    clear_jobs,
    end_run,
    install_people,
    jobs_of,
    make_bet,
    make_business,
    reset_world,
    run_of,
    set_bets,
    start,
)
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from test_bet_retro import ScriptedProjectManager, ended_bet, next_bet, people_draft, settle


pytestmark = pytest.mark.usefixtures("outside_cycles")


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    set_bets(failed_grace_minutes=0)
    clear_jobs("run_project", "bet_retro", "routine_run", "retry_project")
    yield
    reset_world()


@pytest.fixture
def business(s):
    return make_business(s)


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000", headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def keys_of(s, bet_id: int) -> list[str]:
    s.expire_all()
    return sorted(j.idempotency_key for j in jobs_of(s, "bet_retro", bet_id=bet_id))


def fail_jobs(bet_id: int) -> None:
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'failed' WHERE kind = 'bet_retro' AND (payload->>'bet_id')::int = :b"),
                  {"b": bet_id})


def sweep() -> None:
    with session_scope() as x:
        bet_loop.sweep(x, lang="ko")


# ---------------------------------------------------------------- a retro that never arrives

def test_a_review_bet_with_no_retro_and_no_live_job_gets_a_new_round_under_a_new_key(s, business):
    bet = ended_bet(s, business)
    assert keys_of(s, bet.id) == [f"bet_retro:{bet.id}:1"]
    sweep()
    assert keys_of(s, bet.id) == [f"bet_retro:{bet.id}:1"], "a live job is left alone"

    fail_jobs(bet.id)
    sweep()

    assert keys_of(s, bet.id) == [f"bet_retro:{bet.id}:1", f"bet_retro:{bet.id}:2"]


def test_after_three_rounds_the_bet_is_flagged_for_the_owner_and_no_fourth_is_queued(s, business, api):
    bet = ended_bet(s, business)
    for _ in range(4):
        fail_jobs(bet.id)
        sweep()

    got = s.get(Bet, bet.id)
    assert len(keys_of(s, bet.id)) == 3
    assert "회고 다시" in got.status_reason
    view = {b["id"]: b for b in roadmap.roadmap(s, business.id, lang="ko")["bets"]}[bet.id]
    assert "retro_stuck" in view["attention"]
    body = api.get(f"/api/bets/{bet.id}/retro").json()
    assert body["stuck"] is True and body["waiting"] is True


def test_the_owner_asks_for_another_round_and_only_one_at_a_time(s, business, api):
    bet = ended_bet(s, business)
    for _ in range(3):
        fail_jobs(bet.id)
        sweep()
    fail_jobs(bet.id)

    first = api.post(f"/api/bets/{bet.id}/retro/redo")
    second = api.post(f"/api/bets/{bet.id}/retro/redo")

    assert first.status_code == 200, first.text
    assert second.status_code == 409 and "already being written" in second.json()["detail"]
    assert keys_of(s, bet.id)[-1] == f"bet_retro:{bet.id}:4"
    sweep()
    assert len(keys_of(s, bet.id)) == 4


def test_a_bet_with_a_retro_or_not_in_review_cannot_be_redone(s, business, api, monkeypatch):
    install_people(first_value=1)
    settled = ended_bet(s, business)
    settle(settled.id, ScriptedProjectManager(people_draft()), monkeypatch)
    running = make_bet(s, business, title="진행 중")

    assert api.post(f"/api/bets/{settled.id}/retro/redo").status_code == 409
    assert api.post(f"/api/bets/{running.id}/retro/redo").status_code == 409


REFUSED = "BudgetExceeded: project budget 40.0 USD-eq would be exceeded"


def refuse_jobs(bet_id: int) -> None:
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'failed', last_error = :e "
                       "WHERE kind = 'bet_retro' AND (payload->>'bet_id')::int = :b"), {"b": bet_id, "e": REFUSED})


def test_a_retro_the_budget_refused_asks_for_budget_and_spends_no_round_on_a_refusal_to_come(s, business, api):
    """동네산책 offered '회고 다시 맡기기' while every round was refused by the project budget, so another failed too."""
    bet = ended_bet(s, business)
    run = run_of(s, bet.id)
    run.spent_usd = run.budget_usd
    s.commit()
    refuse_jobs(bet.id)
    sweep()

    s.expire_all()
    view = {b["id"]: b for b in roadmap.roadmap(s, business.id, lang="ko")["bets"]}[bet.id]
    assert keys_of(s, bet.id) == [f"bet_retro:{bet.id}:1"], "a round the budget would refuse is not queued"
    assert "retro_budget" in view["attention"] and "retro_stuck" not in view["attention"]
    assert view["budget_stop"]["project_id"] == run.id and "예산" in view["budget_stop"]["says"]
    assert s.get(Bet, bet.id).status_reason.startswith("예산이 모자라 회고를 쓰지 못했어요.")
    assert api.post(f"/api/bets/{bet.id}/retro/redo").status_code == 409

    run.budget_usd += 20
    s.commit()
    sweep()

    assert keys_of(s, bet.id)[-1] == f"bet_retro:{bet.id}:2", "raising the budget lets the next round go"


# ---------------------------------------------------------------- a failed run

def failed_bet(s, business, minutes_ago: int | None = None):
    bet = make_bet(s, business)
    start(bet.id)
    s.expire_all()
    run = run_of(s, bet.id)
    end_run(s, run, "FAILED", "RuntimeError: x")
    if minutes_ago is not None:
        s.execute(text("UPDATE projects SET updated_at = :t WHERE id = :i"),
                  {"t": datetime.now(UTC) - timedelta(minutes=minutes_ago), "i": run.id})
        s.commit()
    return bet, run


def test_a_failed_run_waits_out_the_grace_period_before_its_bet_goes_to_review(s, business):
    set_bets(failed_grace_minutes=30)
    bet, _ = failed_bet(s, business, minutes_ago=5)
    sweep()
    s.expire_all()
    assert s.get(Bet, bet.id).status == "RUNNING"

    s.execute(text("UPDATE projects SET updated_at = :t WHERE bet_id = :b"),
              {"t": datetime.now(UTC) - timedelta(minutes=31), "b": bet.id})
    s.commit()
    sweep()

    s.expire_all()
    assert s.get(Bet, bet.id).status == "REVIEW" and s.get(Bet, bet.id).end_cause == "run_failed"


def test_the_default_grace_is_thirty_minutes():
    from justshipit.services import app_settings
    reset_world()
    assert app_settings.effective().bets.failed_grace_minutes == 30


@pytest.mark.parametrize("kind", ["retry_project", "resume_project"])
def test_a_failed_run_with_a_retry_or_resume_alive_stays_running_however_late(s, business, kind):
    bet, run = failed_bet(s, business, minutes_ago=600)
    with session_scope() as x:
        queue.enqueue(x, kind, {"project_id": run.id}, idempotency_key=f"{kind}:{run.id}:t")

    sweep()

    s.expire_all()
    assert s.get(Bet, bet.id).status == "RUNNING" and jobs_of(s, kind, project_id=run.id)


def test_a_reviewed_bet_with_no_retro_goes_back_to_running_when_its_failed_run_is_retried(s, business):
    bet = ended_bet(s, business, cause="run_failed")
    run = run_of(s, bet.id)
    s.expire_all()
    assert s.get(Bet, bet.id).status == "REVIEW"
    with session_scope() as x:
        x.get(Project, run.id).status = "RUNNING"
        c = dict(x.get(Project, run.id).context or {})
        c.pop("paused", None)
        x.get(Project, run.id).context = c

    sweep()

    s.expire_all()
    got = s.get(Bet, bet.id)
    assert got.status == "RUNNING" and got.end_cause is None and got.finished_at is None
    assert got.started_at is not None and roadmap.running_count(s, business.id) == 1


def test_a_retro_that_is_stored_ends_the_way_back_to_running(s, business, monkeypatch):
    install_people(first_value=1)
    bet = ended_bet(s, business, cause="run_failed")
    settle(bet.id, ScriptedProjectManager(people_draft()), monkeypatch)
    with session_scope() as x:
        x.get(Bet, bet.id).status = "REVIEW"

    with pytest.raises(ControlError, match="이미 회고가 있어"), session_scope() as x:
        roadmap.move_bet(x, bet.id, "RUNNING")


def test_going_back_to_running_needs_a_free_slot(s, business):
    bet = ended_bet(s, business)
    other = make_bet(s, business, title="다른 도전")
    start(other.id)

    with pytest.raises(ControlError, match="상한 1개"), session_scope() as x:
        roadmap.move_bet(x, bet.id, "RUNNING")


def test_the_owner_ending_a_running_bet_as_done_pauses_its_run(s, business):
    bet = make_bet(s, business)
    start(bet.id)
    s.expire_all()
    run = run_of(s, bet.id)
    with session_scope() as x:
        x.get(Project, run.id).status = "RUNNING"

    with session_scope() as x:
        roadmap.move_bet(x, bet.id, "DONE", reason="충분하다")

    s.expire_all()
    assert s.get(Bet, bet.id).status == "DONE" and control.is_paused(s.get(Project, run.id))


# ---------------------------------------------------------------- a card bound to its bet

def out_of_scope(s, business, monkeypatch) -> tuple[Bet, Bet]:
    install_people()
    bet = ended_bet(s, business)
    settle(bet.id, ScriptedProjectManager(people_draft(bets=[next_bet("범위 밖", scope=False)])), monkeypatch)
    s.expire_all()
    nb = s.scalar(select(Bet).where(Bet.source_bet_id == bet.id))
    return bet, nb


def test_rejecting_an_out_of_scope_card_drops_its_bet(s, business, monkeypatch):
    _, nb = out_of_scope(s, business, monkeypatch)
    assert nb.status == "PROPOSED"

    answer(card_of(s, nb.id, "project.direction_change").id, HumanDecision.REJECT)

    s.expire_all()
    assert s.get(Bet, nb.id).status == "DROPPED"


def test_approving_an_out_of_scope_card_picks_its_bet_and_starts_nothing(s, business, monkeypatch):
    _, nb = out_of_scope(s, business, monkeypatch)

    answer(card_of(s, nb.id, "project.direction_change").id)

    s.expire_all()
    got = s.get(Bet, nb.id)
    assert got.status == "PICKED" and run_of(s, nb.id) is None and got.loop_started_at is None
    assert "REQ-" not in got.status_reason and "You approved" in got.status_reason, "the reason names no record code"


def test_deferring_an_out_of_scope_card_changes_nothing(s, business, monkeypatch):
    _, nb = out_of_scope(s, business, monkeypatch)

    answer(card_of(s, nb.id, "project.direction_change").id, HumanDecision.DEFER)

    s.expire_all()
    assert s.get(Bet, nb.id).status == "PROPOSED"


# ---------------------------------------------------------------- the slot count

def test_counting_the_running_bets_to_start_one_holds_the_business_row(s, business):
    from justshipit.db.session import engine
    url = engine().url.render_as_string(hide_password=False).replace("+psycopg", "")
    roadmap.running_count(s, business.id, lock=True)
    with psycopg.connect(url) as other, pytest.raises(psycopg.errors.LockNotAvailable):
        other.execute("SELECT id FROM businesses WHERE id = %s FOR UPDATE NOWAIT", (business.id,))
    s.rollback()


def test_next_bets_are_picked_against_the_slots_free_when_the_retro_settles(s, business, monkeypatch):
    from justshipit.services import bet_retro
    set_bets(max_running=3)
    install_people()
    bet = ended_bet(s, business)
    seen = {}
    real = bet_retro.build_packet

    def build_then_fill(session, b, now, *, lang):
        packet = real(session, b, now, lang=lang)
        seen["free_when_built"] = packet.free_slots
        return packet

    monkeypatch.setattr(bet_retro, "build_packet", build_then_fill)
    project_manager = ScriptedProjectManager(lambda prompt: (set_bets(max_running=1), people_draft(bets=[next_bet("하나"), next_bet("둘")]))[1])

    settle(bet.id, project_manager, monkeypatch)

    s.expire_all()
    mine = list(s.scalars(select(Bet).where(Bet.source_bet_id == bet.id).order_by(Bet.id)))
    assert seen["free_when_built"] == 3
    assert [b.status for b in mine] == ["PICKED", "PROPOSED"], "one slot was free by the time the retro settled"
