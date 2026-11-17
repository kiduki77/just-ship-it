"""The migrations and the models describe one schema: `upgrade head` leaves nothing for autogenerate to add, and the
whole chain can go down to nothing and up again. Runs on a database of its own, so no other test sees it change."""
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from justshipit.db.models import Base
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from conftest import _ADMIN, TEST_DB
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
ADMIN = _ADMIN.checkpoint_dsn  # the compose Postgres with the password .env or the environment gives
SCRATCH = f"{TEST_DB}_migrations"


STAFFING_RECORDS = "b8e2c6a4d1f7"
BEFORE_STAFFING = "a9d4f7b2c613"  # the revision under STAFFING_RECORDS: a database with rows in it still upgrades to here


def run_alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, capture_output=True, text=True,
                          env={**os.environ, "SHIP_DATABASE_URL": _ADMIN.database_url.rsplit("/", 1)[0] + f"/{SCRATCH}"})


def alembic(*args: str) -> None:
    done = run_alembic(*args)
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


def drift(engine) -> list:
    """What autogenerate would still write into a new migration. Tables the models do not declare are LangGraph's
    checkpoint tables (the env.py rule), and are left out."""
    def only_ours(obj, name, type_, reflected, compare_to) -> bool:
        return not (type_ == "table" and reflected and compare_to is None)

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True, "include_object": only_ours})
        return compare_metadata(ctx, Base.metadata)


def leftovers(engine) -> dict[str, list[str]]:
    with engine.connect() as conn:
        return {
            "tables": sorted(set(inspect(conn).get_table_names()) - {"alembic_version"}),
            "types": sorted(r[0] for r in conn.execute(text(
                "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
                "WHERE n.nspname = 'public' AND t.typtype = 'e'"))),
        }


def task_columns(engine) -> set[str]:
    with engine.connect() as conn:
        return {c["name"] for c in inspect(conn).get_columns("tasks")}


def test_upgrade_head_leaves_the_database_matching_the_models(scratch):
    alembic("upgrade", "head")

    assert drift(scratch) == []


def test_the_chain_goes_down_to_nothing_and_back_up_to_the_same_schema(scratch):
    alembic("upgrade", "head")
    alembic("downgrade", "base")

    assert leftovers(scratch) == {"tables": [], "types": []}, "a downgrade left part of the schema behind"

    alembic("upgrade", "head")

    assert drift(scratch) == []


def test_the_drift_check_sees_a_stray_column_and_ignores_a_checkpoint_table(scratch):
    alembic("upgrade", "head")
    with scratch.begin() as conn:
        conn.execute(text("CREATE TABLE checkpoints_foreign (id int)"))
    assert drift(scratch) == []

    with scratch.begin() as conn:
        conn.execute(text("ALTER TABLE projects ADD COLUMN stray int"))

    assert [d[0] for d in drift(scratch)] == ["remove_column"]


def old_task(s, project_id: int) -> int:
    """A task row as a revision before head holds it: only the columns named here, so a column the model gained later
    is not written into a table that does not have it yet."""
    from justshipit.db.models import Task
    from sqlalchemy import insert
    return s.scalar(insert(Task).values(project_id=project_id, key="k", title="T", owner="research", objective="o")
                    .returning(Task.id))


def old_business(s, slug: str):
    """A business row as a revision before the tracking one holds it, named by its id: the model's newer columns
    (`doors`) are not written into a table that does not have them yet."""
    from types import SimpleNamespace
    return SimpleNamespace(id=s.execute(text(
        "INSERT INTO businesses (slug, name, one_liner, status, cycle_days, rungs, tracks, models, door_policy, "
        "checkpoints) VALUES (:slug, :name, 'x', 'ACTIVE', 14, '[]', '[]', '[]', '[]', '[]') RETURNING id"),
        {"slug": slug, "name": slug.title()}).scalar())


def test_team_chat_gives_each_commented_task_its_thread_with_the_comments_in_order(scratch):
    from justshipit.db.models import Project
    from sqlalchemy.orm import Session
    alembic("upgrade", "b4e9c2a7d815")
    with Session(scratch) as s:
        b = old_business(s, "chat-backfill")
        p =Project(slug="chat-backfill-p", name="P", goal="g", budget_usd=1, business_id=b.id)
        s.add(p)
        s.flush()
        ids = (b.id, p.id, old_task(s, p.id))
        s.commit()
    with scratch.begin() as conn:
        for body in ("first", "second"):
            conn.execute(text("INSERT INTO agent_messages (project_id, from_agent, to_agent, kind, ref_type, ref_id, body) "
                              "VALUES (:p, 'owner', 'research', 'comment', 'task', :t, :body)"),
                         {"p": ids[1], "t": ids[2], "body": body})

    alembic("upgrade", BEFORE_STAFFING)

    with scratch.connect() as conn:
        thread = conn.execute(text("SELECT id, business_id, default_agent, last_seq, owner_read_seq FROM chat_threads "
                                   "WHERE tag_kind = 'task' AND tag_ref = :t"), {"t": str(ids[2])}).one()
        lines = conn.execute(text("SELECT body, seq, business_id FROM agent_messages WHERE thread_id = :id ORDER BY seq"),
                             {"id": thread.id}).all()
    assert (thread.business_id, thread.default_agent, thread.last_seq, thread.owner_read_seq) == (ids[0], "research", 2, 2)
    assert [(x.body, x.seq, x.business_id) for x in lines] == [("first", 1, ids[0]), ("second", 2, ids[0])]


def _insert_a_task(engine) -> None:
    from justshipit.db.models import Project
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        b = old_business(s, "staffing-guard")
        p =Project(slug="staffing-guard-p", name="P", goal="g", budget_usd=1, business_id=b.id)
        s.add(p)
        s.flush()
        old_task(s, p.id)
        s.commit()


def _insert_a_build(engine) -> None:
    from justshipit.db.models import DevJob, Project, Spec
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        b = old_business(s, "staffing-guard-b")
        p =Project(slug="staffing-guard-bp", name="P", goal="g", budget_usd=1, business_id=b.id)
        s.add(p)
        s.flush()
        spec = Spec(spec_key="GUARD-0001", project_id=p.id, title="S", registry_path="specs/GUARD-0001.md")
        s.add(spec)
        s.flush()
        s.add(DevJob(spec_id=spec.id))
        s.commit()


@pytest.mark.parametrize(("insert", "table"), [(_insert_a_task, "tasks"), (_insert_a_build, "dev_jobs")])
def test_the_staffing_records_refuse_a_database_that_already_has_work_and_change_nothing(scratch, insert, table):
    alembic("upgrade", BEFORE_STAFFING)
    insert(scratch)

    done = run_alembic("upgrade", "head")

    assert done.returncode != 0
    assert f"{table} already hold rows" in done.stderr and "reset" in done.stderr
    assert {"crews", "crew_turns"}.isdisjoint(leftovers(scratch)["tables"])
    with scratch.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == BEFORE_STAFFING


def test_the_staffing_records_are_added_to_an_empty_database_and_removed_again(scratch):
    alembic("upgrade", BEFORE_STAFFING)

    alembic("upgrade", STAFFING_RECORDS)

    assert {"crews", "crew_turns"} <= set(leftovers(scratch)["tables"])
    with scratch.connect() as conn:
        uniques = {r[0] for r in conn.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid IN ('crews'::regclass, 'crew_turns'::regclass) "
            "AND contype = 'u'"))}
    assert uniques == {"uq_crews_subject", "uq_crew_turns_call"}
    assert "family" in task_columns(scratch)

    alembic("downgrade", BEFORE_STAFFING)

    assert {"crews", "crew_turns"}.isdisjoint(leftovers(scratch)["tables"])
    assert "family" not in task_columns(scratch)
    alembic("upgrade", "head")
    assert drift(scratch) == []
