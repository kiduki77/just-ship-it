"""The cycle-stages migration on a database that holds work from before it: nothing in flight stops, each business
with an approved plan gets a draft cycle for the CEO to fill, and a schedule with nothing in flight waits for a stage."""
import importlib.util
import json

import pytest
from justshipit.services import queue
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_migrations import ROOT, alembic, scratch  # noqa: F401 - the fixture

PREVIOUS = "d4a8e2c6b917"
MIGRATION = ROOT / "backend" / "justshipit" / "db" / "migrations" / "versions" / "e6c3a9f1b204_cycle_stages.py"


def _business(conn, name: str, *, approved: bool = True, status: str = "ACTIVE") -> int:
    bid = conn.execute(text(
        "INSERT INTO businesses (slug, name, one_liner, status, cycle_days, rungs, tracks, models, door_policy, "
        "checkpoints) VALUES (:slug, :name, 'x', :status, 14, '[]', '[]', '[]', '[]', '[]') RETURNING id"),
        {"slug": name.lower().replace(" ", "-").replace("[", "").replace("]", ""), "name": name, "status": status}).scalar()
    conn.execute(text("INSERT INTO business_briefs (business_id, version, sections, assumptions, author, approved_at) "
                      "VALUES (:b, 1, '[]', '[]', 'owner', CASE WHEN :ok THEN now() END)"), {"b": bid, "ok": approved})
    return bid


def _project(conn, bid: int, status: str) -> int:
    return conn.execute(text(
        "INSERT INTO projects (slug, name, goal, status, budget_usd, spent_usd, reserved_usd, context, plan_round, "
        "meetings_held, business_id) VALUES (:slug, 'p', 'g', :status, 10, 0, 0, '{}', 0, 0, :b) RETURNING id"),
        {"slug": f"p-{bid}-{status.lower()}", "status": status, "b": bid}).scalar()


def _job(conn, kind: str, payload: dict, status: str = "queued") -> int:
    return conn.execute(text("INSERT INTO jobs (kind, payload, status, attempts, max_attempts) "
                             "VALUES (:k, CAST(:p AS jsonb), :st, 0, 1) RETURNING id"),
                        {"k": kind, "p": json.dumps(payload), "st": status}).scalar()


@pytest.fixture
def seeded(scratch):  # noqa: F811 - the fixture imported above
    alembic("upgrade", PREVIOUS)
    with scratch.begin() as conn:
        live = _business(conn, "Live business")
        conn.execute(text("INSERT INTO cycles (business_id, number, starts_on, ends_on) "
                          "VALUES (:b, 1, current_date, current_date + 14)"), {"b": live})
        run = _project(conn, live, "RUNNING")
        conn.execute(text("INSERT INTO bets (business_id, title, pitch, run_mode, origin, status) "
                          "VALUES (:b, 't', 'p', 'ai', 'owner', 'LISTENING')"), {"b": live})
        ids = {"live": live, "run": run,
               "run_job": _job(conn, "retry_project", {"project_id": run}),
               "finder": _job(conn, "find_people", {"business_id": live}),
               "notice": _job(conn, "notify", {"kind": "run_failed", "project_id": run}),
               "finished": _business(conn, "Finished business"),
               "fresh": _business(conn, "Fresh business"),
               "kickoff": _business(conn, "Kickoff business", approved=False),
               "test": _business(conn, "[TEST] fixture"),
               "archived": _business(conn, "Archived business", status="ARCHIVED")}
        _project(conn, ids["finished"], "DONE")
        conn.execute(text("INSERT INTO schedules (name, kind, payload, interval_hours, enabled, next_run_at) "
                          "VALUES ('B weekly promotion', 'promotion_week', CAST(:p AS jsonb), 168, true, now())"),
                     {"p": json.dumps({"business_id": ids["finished"]})})
    alembic("upgrade", "head")
    return ids


def _cycles(conn, bid: int) -> list[tuple]:
    return conn.execute(text("SELECT state, mode, closed_at IS NULL FROM cycles WHERE business_id = :b ORDER BY number"),
                        {"b": bid}).all()


def test_live_work_runs_on_in_an_adopted_stage_and_the_project_manager_is_asked_for_a_proposal(scratch, seeded):  # noqa: F811 - the fixture
    with scratch.connect() as conn:
        assert _cycles(conn, seeded["live"]) == [("closed", "new", False), ("draft", "adopt", True)]
        stage = conn.execute(text("SELECT id, status, adopted, allows_kinds, allows_owner_work FROM cycle_stages "
                                  "WHERE business_id = :b"), {"b": seeded["live"]}).one()
        assert stage[1:] == ("running", True, ["find_people"], [])
        assert conn.execute(text("SELECT version, status FROM cycle_proposals p JOIN cycles c ON c.id = p.cycle_id "
                                 "WHERE c.business_id = :b"), {"b": seeded["live"]}).all() == [(1, "drafting")]
        assert conn.execute(text("SELECT stage_id FROM projects WHERE id = :p"), {"p": seeded["run"]}).scalar() == stage[0]
        jobs = {r[0]: tuple(r[1:]) for r in conn.execute(text("SELECT id, gate, stage_id FROM jobs"))}
    assert jobs[seeded["run_job"]] == ("stage", stage[0])
    assert jobs[seeded["finder"]] == ("stage", stage[0])
    assert jobs[seeded["notice"]] == ("none", None)
    with Session(scratch) as s:
        taken = queue.claim(s, "migration-test", ["retry_project", "find_people", "notify"])
        assert taken is not None
        got = {taken.id}
        while (j := queue.claim(s, "migration-test", ["retry_project", "find_people", "notify"])) is not None:
            got.add(j.id)
        assert got == {seeded["run_job"], seeded["finder"], seeded["notice"]}
        s.rollback()


def test_a_business_with_nothing_in_flight_gets_a_draft_cycle_and_no_stage(scratch, seeded):  # noqa: F811 - the fixture
    with scratch.connect() as conn:
        assert _cycles(conn, seeded["finished"]) == [("draft", "adopt", True)]
        assert _cycles(conn, seeded["fresh"]) == [("draft", "new", True)]
        for bid in (seeded["finished"], seeded["fresh"]):
            assert conn.execute(text("SELECT count(*) FROM cycle_stages WHERE business_id = :b"), {"b": bid}).scalar() == 0
        for bid in (seeded["kickoff"], seeded["test"], seeded["archived"]):
            assert _cycles(conn, bid) == []


def test_the_backfill_is_safe_to_run_twice(scratch, seeded):  # noqa: F811 - the fixture
    spec = importlib.util.spec_from_file_location("cycle_stages_migration", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with scratch.begin() as conn:
        before = conn.execute(text("SELECT (SELECT count(*) FROM cycles), (SELECT count(*) FROM cycle_stages), "
                                   "(SELECT count(*) FROM cycle_proposals)")).one()
        mod.backfill(conn)
        after = conn.execute(text("SELECT (SELECT count(*) FROM cycles), (SELECT count(*) FROM cycle_stages), "
                                  "(SELECT count(*) FROM cycle_proposals)")).one()
    assert before == after


def test_a_job_row_made_by_hand_after_the_upgrade_names_no_gate_and_is_refused(scratch, seeded):  # noqa: F811 - the fixture
    with scratch.connect() as conn, pytest.raises(Exception, match="gate"):
        conn.execute(text("INSERT INTO jobs (kind, payload, status, attempts, max_attempts) "
                          "VALUES ('run_project', '{}', 'queued', 0, 1)"))
