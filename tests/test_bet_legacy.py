"""Bets that were running before the loop shipped: the sweep never retros them, they hold no slot, the migration closes the
ones whose project already ended, and the owner can see and end the rest."""
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from justshipit.db.models import AgentRequest, Bet, Project
from justshipit.db.session import engine, session_scope
from justshipit.services import bet_loop, bet_retro, control, projects, roadmap
from alembic.migration import MigrationContext
from alembic.operations import Operations
from bet_loop_kit import clear_jobs, jobs_of, make_bet, make_business, reset_world, start
from sqlalchemy import func, select

VERSIONS = Path(__file__).resolve().parents[1] / "backend/justshipit/db/migrations/versions"


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    clear_jobs("run_project", "bet_retro", "routine_run")
    yield
    reset_world()


@pytest.fixture
def business(s):
    return make_business(s)


def legacy(s, business, project_status, error=None) -> Bet:
    """A bet as the roadmap migration backfilled it: RUNNING, no appetite, a project linked by bet_id, and nothing that
    says the loop started it."""
    bet = make_bet(s, business, status="RUNNING", appetite_days=None, budget_usd=None)
    bet.started_at = datetime.now(UTC) - timedelta(days=200)
    s.add(Project(slug=f"legacy-{bet.id}", name="old", goal="g", status=project_status, budget_usd=10, context={},
                  business_id=business.id, bet_id=bet.id, error=error))
    s.commit()
    return bet


def test_the_first_sweep_over_backfilled_bets_calls_no_project_manager_and_makes_no_card_or_job(s, business, monkeypatch):
    bets = [legacy(s, business, st, err).id for st, err in
            (("DONE", None), ("FAILED", "RuntimeError: x"), ("DONE", None), ("PAUSED", None), ("WAITING_HUMAN", None),
             ("RUNNING", None))]
    calls = []
    monkeypatch.setattr(bet_retro, "run_agent", lambda *a, **k: calls.append(a))
    cards = s.scalar(select(func.count()).select_from(AgentRequest))

    with session_scope() as x:
        moved = bet_loop.sweep(x, lang="ko")
    for bet_id in bets:
        bet_retro.run(bet_id, lang="ko")

    s.expire_all()
    assert moved == [] and calls == []
    assert s.scalar(select(func.count()).select_from(AgentRequest)) == cards
    assert not [j for i in bets for j in jobs_of(s, "bet_retro", bet_id=i)]
    assert {s.get(Bet, i).status for i in bets} == {"RUNNING"}


def test_a_legacy_bet_with_a_live_project_holds_no_slot_so_the_loop_can_start_its_own(s, business):
    old = legacy(s, business, "PAUSED")
    new = make_bet(s, business, status="PICKED")

    start(new.id)

    s.expire_all()
    assert s.get(Bet, new.id).status == "RUNNING" and s.get(Bet, new.id).loop_started_at is not None
    assert roadmap.running_count(s, business.id) == 1 and s.get(Bet, old.id).status == "RUNNING"


def test_the_roadmap_lists_a_legacy_bet_for_the_owner_to_clean_up_and_not_a_loop_bet(s, business):
    old = legacy(s, business, "PAUSED")
    mine = make_bet(s, business)
    start(mine.id)

    rows = {b["id"]: b for b in roadmap.roadmap(s, business.id, lang="ko")["bets"]}

    assert rows[old.id]["legacy"] is True and rows[old.id]["attention"] == ["owner_cleanup"]
    assert rows[mine.id]["legacy"] is False and "owner_cleanup" not in rows[mine.id]["attention"]


def test_the_owner_ends_a_legacy_bet_and_its_live_run_is_paused(s, business):
    old = legacy(s, business, "RUNNING")

    with session_scope() as x:
        roadmap.move_bet(x, old.id, "DONE", reason="정리")

    s.expire_all()
    run = s.scalar(select(Project).where(Project.bet_id == old.id))
    assert s.get(Bet, old.id).status == "DONE" and control.is_paused(run)


def test_a_project_started_after_the_loop_shipped_is_a_loop_bet(s, business):
    p = projects.create(s, "새 일", name="새 일", budget_usd=5, start=True, business=business)
    s.commit()

    bet = s.get(Bet, p.bet_id)

    assert bet.status == "RUNNING" and bet.loop_started_at is not None and not roadmap.is_legacy_bet(bet)
    assert roadmap.running_count(s, business.id) == 1


def _migration():
    name = "d7a3f5c1e982_bet_loop"
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_migration_closes_legacy_bets_whose_project_ended_without_a_retro_or_a_job(s, business):
    done = legacy(s, business, "DONE")
    failed = legacy(s, business, "FAILED", "RuntimeError: x")
    archived = legacy(s, business, "ARCHIVED")
    paused = legacy(s, business, "PAUSED")
    for bet in (done, failed, archived, paused):
        s.get(Bet, bet.id).finished_at = None
    s.commit()

    with engine().begin() as conn, Operations.context(MigrationContext.configure(conn)):
        _migration().settle_legacy_bets()

    s.expire_all()
    got = {name: s.get(Bet, b.id) for name, b in (("done", done), ("failed", failed), ("archived", archived), ("paused", paused))}
    assert [got[k].status for k in ("done", "failed", "archived")] == ["DONE", "STOPPED", "STOPPED"]
    for k in ("done", "failed", "archived"):
        assert got[k].end_cause == "legacy" and got[k].retro == {"mode": "legacy"} and got[k].finished_at is not None
    assert got["paused"].status == "RUNNING" and got["paused"].retro is None and got["paused"].loop_started_at is None
    assert not [j for b in (done, failed, archived, paused) for j in jobs_of(s, "bet_retro", bet_id=b.id)]


def test_a_legacy_retro_is_not_promotion_material(s, business):
    from justshipit.services import promotion
    done = legacy(s, business, "DONE")
    done.status, done.end_cause, done.retro, done.finished_at = "DONE", "legacy", {"mode": "legacy"}, datetime.now(UTC)
    s.commit()

    material = promotion.gather_material(s, business, [], datetime.now(UTC))

    assert not [m for m in material if m.key == f"bet:{done.id}"]
