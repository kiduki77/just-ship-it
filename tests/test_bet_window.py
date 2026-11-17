"""A bet's window: a finished run leaves the bet LISTENING for market response, it holds its slot until its days run
out, the retro then reads the whole window, the owner can end the wait early, and a crash or a retry around the move
to REVIEW still gives one retro. Times are relative to the real clock, so the tests hold on any day."""
from datetime import UTC, datetime, timedelta

import pytest
from justshipit.api.app import app
from justshipit.db.models import ActivityEvent, Bet, MetricPoint, OwnerTask, Project
from justshipit.db.session import session_scope
from justshipit.services import bet_loop, bet_retro, metrics, roadmap
from justshipit.services.control import ControlError
from bet_loop_kit import clear_jobs, end_run, install_people, jobs_of, make_bet, make_business, reset_world, run_of, set_bets, start
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_bet_retro import ScriptedProjectManager, draft


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    clear_jobs("run_project", "bet_retro")
    yield
    reset_world()


@pytest.fixture
def business(s):
    return make_business(s)


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000", headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def sweep(now: datetime | None = None) -> list[tuple[int, str]]:
    with session_scope() as x:
        return bet_loop.sweep(x, now, lang="ko")


def finished_run(s, business, started_days_ago: float = 0, **kw) -> Bet:
    """A bet the owner started `started_days_ago` days ago whose run has just finished its work."""
    bet = make_bet(s, business, **kw)
    start(bet.id)
    s.expire_all()
    bet = s.get(Bet, bet.id)
    bet.started_at = datetime.now(UTC) - timedelta(days=started_days_ago)
    end_run(s, run_of(s, bet.id), "DONE")
    return bet


def status_of(s, bet_id: int) -> Bet:
    s.expire_all()
    return s.get(Bet, bet_id)


def test_a_finished_run_leaves_the_bet_listening_with_its_slot_and_no_retro(s, business):
    bet = finished_run(s, business, appetite_days=14)

    assert sweep() == []
    assert sweep() == []

    got = status_of(s, bet.id)
    assert got.status == "LISTENING" and got.end_cause is None and got.finished_at is None
    assert not jobs_of(s, "bet_retro", bet_id=bet.id), "no market response exists yet, so nothing is judged"
    assert roadmap.running_count(s, business.id) == 1, "the bet keeps its slot for its whole window"
    with pytest.raises(ControlError, match="상한 1개"):
        start(make_bet(s, business, title="둘째").id)

    row = next(b for b in roadmap.roadmap(s, business.id, lang="ko")["bets"] if b["id"] == bet.id)
    assert row["window"]["days_left"] == 14 and bet.id in roadmap.roadmap(s, business.id, lang="ko")["lanes"]["now"]


def test_a_bet_an_earlier_test_left_live_is_not_swept_into_the_next_test(s, business):
    """The sweep reads every business's live bets against the real clock, so a leftover whose window has closed would
    be moved, and returned, by whichever test sweeps next."""
    left = finished_run(s, business, started_days_ago=30, appetite_days=3)

    reset_world()

    assert sweep() == []
    assert status_of(s, left.id).status == "STOPPED"


def test_a_bet_promised_no_days_listens_for_the_default_window(s, business):
    """A bet promised no days runs to its brief's deadline (test_brief_targets); with none stated, the default."""
    brief = roadmap.approved_brief(s, business.id)
    roadmap.new_brief_version(s, business.id, approved=True, assumptions=[], author="owner", sections=[
        {**x, "lines": [{**ln, "text": "첫 가치에 닿은 사람 30명"} for ln in x["lines"]]} if x["key"] == "goal_deadline" else x
        for x in brief.sections])
    s.commit()
    set_bets(window_days=2)
    bet = finished_run(s, business, started_days_ago=1, appetite_days=None)

    sweep()
    assert status_of(s, bet.id).status == "LISTENING"

    with session_scope() as x:
        x.get(Bet, bet.id).started_at = datetime.now(UTC) - timedelta(days=3)
    assert (bet.id, "time_limit") in sweep()


def test_the_window_closing_runs_the_retro_over_the_whole_window(s, business, monkeypatch):
    install_people(first_value=40)
    bet = finished_run(s, business, started_days_ago=4, appetite_days=3)
    started = bet.started_at

    sweep(started + timedelta(hours=2))
    assert status_of(s, bet.id).status == "LISTENING"
    assert not jobs_of(s, "bet_retro", bet_id=bet.id)

    link = "https://news.ycombinator.com/item?id=1"
    with session_scope() as x:
        metrics.record(x, business.id, "hn_score", 42.0, started + timedelta(days=2), source="hn", ref=link)
        x.add(OwnerTask(business_id=business.id, kind="post", title="Show HN 글", status="DONE",
                        done_at=started + timedelta(days=2), result={"link": link}))
    late_id = metrics.eid(s.scalar(select(MetricPoint.id).where(MetricPoint.business_id == business.id,
                                                                MetricPoint.metric == "hn_score")))

    assert (bet.id, "time_limit") in sweep()
    ended = status_of(s, bet.id)
    assert ended.status == "REVIEW" and ended.finished_at >= started + timedelta(days=3)
    assert len(jobs_of(s, "bet_retro", bet_id=bet.id)) == 1

    project_manager = ScriptedProjectManager(draft())
    monkeypatch.setattr(bet_retro, "run_agent", project_manager)
    bet_retro.run(bet.id, lang="ko")

    settled = status_of(s, bet.id)
    assert settled.status == "DONE" and project_manager.calls == 1
    window = settled.retro["observed"]["window"]
    assert datetime.fromisoformat(window["start"]) == started
    assert datetime.fromisoformat(window["end"]) == ended.finished_at
    shown = [o["evidence_id"] for rows in settled.retro["observed"]["by_stage"].values() for o in rows]
    assert late_id in shown, "a reaction that came in after the run finished is in the retro"
    assert "Show HN 글" in project_manager.prompts[0], "a post made after the run finished is in the retro"


def test_the_owner_ends_the_wait_early_and_the_retro_is_queued_once(s, business, api):
    bet = finished_run(s, business, appetite_days=14)
    sweep()
    assert status_of(s, bet.id).status == "LISTENING"

    r = api.post(f"/api/bets/{bet.id}/move", json={"to": "REVIEW", "reason": "오너가 기간 전에 회고를 맡겼습니다"})
    assert r.status_code == 200, r.text
    again = api.post(f"/api/bets/{bet.id}/move", json={"to": "REVIEW"})
    assert again.status_code == 200, "a second click changes nothing"
    sweep()

    got = status_of(s, bet.id)
    assert got.status == "REVIEW" and got.end_cause == "owner" and got.finished_at is not None
    assert len(jobs_of(s, "bet_retro", bet_id=bet.id)) == 1
    assert roadmap.running_count(s, business.id) == 0, "a bet in review frees its slot"
    row = next(b for b in api.get(f"/api/businesses/{business.id}/roadmap").json()["bets"] if b["id"] == bet.id)
    assert row["window"] is None


def test_a_listening_bet_cannot_go_back_to_running_or_skip_the_retro(s, business):
    bet = finished_run(s, business)
    sweep()

    for to in ("RUNNING", "DONE", "STOPPED"):
        with pytest.raises(ControlError), session_scope() as x:
            roadmap.move_bet(x, bet.id, to)
    assert status_of(s, bet.id).status == "LISTENING"


def test_a_crash_while_the_window_closes_leaves_the_bet_listening_and_the_next_sweep_queues_one_retro(s, business, monkeypatch):
    bet = finished_run(s, business, started_days_ago=4, appetite_days=3)
    sweep(bet.started_at + timedelta(hours=1))

    def crash(*a, **kw):
        raise RuntimeError("worker died")

    with monkeypatch.context() as m:
        m.setattr(roadmap, "queue_retro", crash)
        with pytest.raises(RuntimeError):
            sweep()
    assert status_of(s, bet.id).status == "LISTENING" and not jobs_of(s, "bet_retro", bet_id=bet.id)

    sweep()
    sweep()
    with session_scope() as x:
        roadmap.move_bet(x, bet.id, "REVIEW")

    got = status_of(s, bet.id)
    assert got.status == "REVIEW" and got.end_cause == "time_limit"
    assert len(jobs_of(s, "bet_retro", bet_id=bet.id)) == 1


def test_a_retried_retro_job_stores_one_retro(s, business, monkeypatch):
    install_people(first_value=40)
    bet = finished_run(s, business, started_days_ago=4, appetite_days=3)
    sweep()

    monkeypatch.setattr(bet_retro, "run_agent", ScriptedProjectManager(RuntimeError("claude exited")))
    with pytest.raises(RuntimeError):
        bet_retro.run(bet.id, lang="ko")
    assert status_of(s, bet.id).status == "REVIEW" and status_of(s, bet.id).retro is None

    project_manager = ScriptedProjectManager(draft())
    monkeypatch.setattr(bet_retro, "run_agent", project_manager)
    bet_retro.run(bet.id, lang="ko")
    bet_retro.run(bet.id, lang="ko")

    assert status_of(s, bet.id).status == "DONE" and project_manager.calls == 1
    retros = s.scalars(select(ActivityEvent).where(ActivityEvent.verb == "bet_retro", ActivityEvent.entity_id == bet.id)).all()
    assert len(retros) == 1
    assert s.get(Project, run_of(s, bet.id).id).status == "DONE"
