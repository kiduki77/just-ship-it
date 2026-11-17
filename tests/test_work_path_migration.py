"""The work path migration on a database that holds what the old schema recorded: a pending blocking card holds the
run, an approval-held task gets its finish hold, `ceo_reviews` become review rounds in order, a card nothing
explains stays unknown, and the downgrade gives back a stored `blocking` with the same values. Runs on a database
of its own."""
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from conftest import _ADMIN, TEST_DB
from justshipit.db.models import Business, Project
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
ADMIN = _ADMIN.checkpoint_dsn
SCRATCH = f"{TEST_DB}_work_path"
BEFORE = "7c1e5a9d3f20"
WORK_PATH = "a9d4f7b2c613"  # the revision under test: the staffing revision after it refuses a database that holds tasks
NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)

_CARD = text("""
    INSERT INTO agent_requests (project_id, task_id, from_agent, type, title, summary, rationale, options, action_params,
                                risk_level, blocking, policy_mode, status, expires_at, resolved_at)
    VALUES (:project, :task, :agent, :type, :title, 's', 'r', '[]'::jsonb, CAST(:params AS jsonb), 'low', :blocking,
            'approve', :status, :expires, :resolved)
    RETURNING id""")


def alembic(*args: str) -> None:
    done = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, capture_output=True, text=True,
                          env={**os.environ, "SHIP_DATABASE_URL": _ADMIN.database_url.rsplit("/", 1)[0] + f"/{SCRATCH}"})
    assert done.returncode == 0, f"alembic {' '.join(args)} failed:\n{done.stderr[-3000:]}"


@pytest.fixture
def scratch():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{SCRATCH}"')
    engine = create_engine(_ADMIN.database_url.rsplit("/", 1)[0] + f"/{SCRATCH}")
    yield engine
    engine.dispose()
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')


@pytest.fixture
def old(scratch) -> dict:
    """The old schema with what REQ-6 and its neighbours look like: ids of the rows, and each card by name."""
    alembic("upgrade", BEFORE)
    with Session(scratch) as s:
        b = Business(slug="wp", name="WP", one_liner="x")
        s.add(b)
        s.flush()
        planned = Project(slug="wp-planned", name="Planned", goal="g", budget_usd=1, business_id=b.id,
                          plan={"goal_interpretation": "g"}, context={"ceo_reviews": [
                              {"round": 1, "assessment": "first look", "action": "follow_up", "unmet": ["a"]},
                              {"round": 2, "assessment": "second look", "action": "complete", "unmet": []}],
                              "realignments": [{"assessment": "kept"}]})
        unplanned = Project(slug="wp-unplanned", name="Unplanned", goal="g", budget_usd=1, business_id=b.id,
                            context={"ceo_reviews": [{"round": 4, "assessment": "only look", "action": "complete"}]})
        s.add_all([planned, unplanned])
        s.flush()
        # Raw rows: the ORM Task has columns later revisions add, and this database is at the old schema.
        held, other = (s.execute(text(
            "INSERT INTO tasks (project_id, key, title, owner, objective, acceptance_criteria, depends_on, status, origin, "
            "plan_round, iteration, max_iterations, budget_usd, spent_usd, contested) VALUES "
            "(:p, :k, :t, 'research', 'o', '[]'::jsonb, '[]'::jsonb, :s, 'ceo_plan', 0, 0, 2, 3.0, 0.0, false) RETURNING id"),
            {"p": planned.id, "k": k, "t": title, "s": status}).scalar_one()
            for k, title, status in (("held", "Held", "APPROVAL_REQUIRED"), ("other", "Other", "DONE")))
        ids = {"business": b.id, "planned": planned.id, "unplanned": unplanned.id, "held": held, "other": other}
        s.commit()
    with scratch.begin() as conn:
        def card(name, **kw):
            ids[name] = conn.execute(_CARD, {"project": ids["planned"], "task": None, "agent": "ceo", "type": "QUESTION",
                                             "title": name, "params": "{}", "blocking": False, "status": "PENDING",
                                             "expires": NOW + timedelta(days=2), "resolved": None, **kw}).scalar_one()
        card("req6", blocking=True)
        card("approval", agent="marketing", type="APPROVAL", task=ids["held"], blocking=True, params='{"revision": 3}')
        card("resolved", type="APPROVAL", task=ids["other"], blocking=True, status="APPROVED", resolved=NOW)
        card("origins", agent="product", params=f'{{"decision_id": 7, "spec_id": 5, "bet_id": 9, "re_request_of": 2, '
                                                f'"dev_job_id": 4, "use_of_job": 6, "need": {{"task_ids": '
                                                f'[{ids["held"]}, {ids["other"]}, "x"]}}, "revision": "bad"}}',
             expires=None)
        conn.execute(text("INSERT INTO agent_messages (project_id, from_agent, to_agent, kind, body, data) VALUES "
                          "(:p, 'product', 'owner', 'decision', 'b', CAST(:d AS jsonb))"),
                     {"p": ids["planned"], "d": f'{{"request_id": {ids["origins"]}}}'})
        ids["message"] = conn.execute(text("SELECT max(id) FROM agent_messages")).scalar_one()
        conn.execute(text("CREATE TABLE checkpoints (thread_id text, body text)"))
        conn.execute(text("INSERT INTO checkpoints VALUES ('t', 'kept')"))
    return ids


def rows(engine, sql: str, **params) -> list:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).all()


def links(engine, request_id: int) -> set[tuple]:
    return {tuple(r) for r in rows(engine, "SELECT rel, target_kind, target_id, phase, revision, source, known, "
                                           "ended_at IS NOT NULL FROM request_links WHERE request_id = :r", r=request_id)}


def test_a_pending_blocking_card_holds_the_run_and_still_reads_as_blocking(scratch, old):
    alembic("upgrade", WORK_PATH)

    assert rows(scratch, "SELECT id, hold_scope, blocking, context_version FROM agent_requests ORDER BY id") == [
        (old["req6"], "run", True, 0), (old["approval"], "run", True, 0), (old["resolved"], "run", True, 0),
        (old["origins"], "none", False, 0)]


def test_an_approval_held_task_gets_its_finish_hold(scratch, old):
    alembic("upgrade", WORK_PATH)

    assert (("holds", "task", old["held"], "finish", 3, "backfill", "backfilled", False) in links(scratch, old["approval"]))
    assert not [x for x in links(scratch, old["resolved"]) if x[0] == "holds"], "an answered card holds nothing"


def test_the_ceo_reviews_become_review_rounds_after_the_plan_in_order(scratch, old):
    alembic("upgrade", WORK_PATH)

    got = rows(scratch, "SELECT project_id, seq, kind, plan_round, assessment, action, unmet, call_id, created_at "
                        "FROM review_rounds ORDER BY project_id, seq")
    assert got == [
        (old["planned"], 1, "plan", 1, "", None, [], None, None),
        (old["planned"], 2, "review", 1, "first look", "follow_up", ["a"], None, None),
        (old["planned"], 3, "review", 2, "second look", "complete", [], None, None),
        (old["unplanned"], 1, "review", 4, "only look", "complete", [], None, None)]
    assert rows(scratch, "SELECT context->'ceo_reviews'->0->>'assessment' FROM projects WHERE id = :p",
                p=old["planned"]) == [("first look",)], "the stored reviews are left where they were"


def test_a_card_nothing_explains_stays_unknown(scratch, old):
    alembic("upgrade", WORK_PATH)

    assert links(scratch, old["req6"]) == set()
    assert rows(scratch, "SELECT ask_reason, expiry_policy, reason_note, default_option, reason_refs "
                         "FROM agent_requests WHERE id = :r", r=old["req6"]) == [(None, None, None, None, [])]


def test_what_a_row_records_of_its_origin_is_linked_as_backfilled(scratch, old):
    alembic("upgrade", WORK_PATH)

    assert {x[:3] for x in links(scratch, old["origins"])} == {
        ("raised_by", "decision", 7), ("raised_by", "spec", 5), ("raised_by", "bet", 9), ("raised_by", "request", 2),
        ("raised_by", "dev_job", 4), ("raised_by", "dev_job", 6), ("raised_by", "task", old["held"]),
        ("raised_by", "task", old["other"]), ("raised_by", "chat_message", old["message"])}
    assert {x[4:7] for x in links(scratch, old["origins"])} == {(1, "backfill", "backfilled")}
    assert rows(scratch, "SELECT expiry_policy FROM agent_requests WHERE id = :r", r=old["origins"]) == [("keep_waiting",)]


def test_an_answered_card_keeps_what_raised_it(scratch, old):
    """Answering ends what a card holds, not where it came from: a decided card still names its asker."""
    alembic("upgrade", WORK_PATH)

    assert links(scratch, old["resolved"]) == {("raised_by", "task", old["other"], None, 1, "backfill", "backfilled", False)}
    assert all(not x[-1] for x in links(scratch, old["approval"]))


def test_the_migration_leaves_the_checkpoint_tables_alone(scratch, old):
    alembic("upgrade", WORK_PATH)

    assert rows(scratch, "SELECT thread_id, body FROM checkpoints") == [("t", "kept")]


def test_blocking_cannot_be_written_once_it_is_generated(scratch, old):
    alembic("upgrade", WORK_PATH)

    with pytest.raises(Exception, match="generated"), scratch.begin() as conn:
        conn.execute(text("UPDATE agent_requests SET blocking = false"))


def test_the_downgrade_gives_back_a_stored_blocking_with_the_same_values(scratch, old):
    alembic("upgrade", WORK_PATH)
    with scratch.begin() as conn:
        conn.execute(text("UPDATE agent_requests SET hold_scope = 'planning' WHERE id = :r"), {"r": old["origins"]})
    alembic("downgrade", BEFORE)

    assert rows(scratch, "SELECT is_generated, is_nullable, data_type FROM information_schema.columns "
                         "WHERE table_name = 'agent_requests' AND column_name = 'blocking'") == [("NEVER", "NO", "boolean")]
    assert rows(scratch, "SELECT id, blocking FROM agent_requests ORDER BY id") == [
        (old["req6"], True), (old["approval"], True), (old["resolved"], True), (old["origins"], True)]
    assert rows(scratch, "SELECT count(*) FROM information_schema.columns WHERE table_name = 'agent_requests' "
                         "AND column_name IN ('hold_scope', 'context_version', 'ask_reason')") == [(0,)]
    assert rows(scratch, "SELECT to_regclass('request_links'), to_regclass('review_rounds')") == [(None, None)]
    assert rows(scratch, "SELECT thread_id, body FROM checkpoints") == [("t", "kept")]
