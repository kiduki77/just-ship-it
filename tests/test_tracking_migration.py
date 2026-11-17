"""The tracking revision adds `businesses.doors`, `people.heard_via` and `people.heard_words`, writes no row, and its
downgrade drops them. Runs on the migration tests' own scratch database."""
from sqlalchemy import inspect, text
from test_migrations import alembic, drift, old_business, scratch  # noqa: F401  (scratch is a fixture)

TRACKING = "c4a8e1d7b295"
BEFORE_TRACKING = "b8e2c6a4d1f7"


def columns(engine, table: str) -> dict[str, dict]:
    with engine.connect() as conn:
        return {c["name"]: c for c in inspect(conn).get_columns(table)}


def counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in ("businesses", "people")}


def test_the_columns_are_added_to_a_database_that_has_a_business_and_a_person_and_no_row_changes(scratch):  # noqa: F811
    alembic("upgrade", BEFORE_TRACKING)
    assert {"doors"}.isdisjoint(columns(scratch, "businesses"))
    assert {"heard_via", "heard_words"}.isdisjoint(columns(scratch, "people"))
    with scratch.begin() as conn:
        business = old_business(conn, "trk")
        conn.execute(text("INSERT INTO people (business_id, handle, display_name, platform, where_found_url, source) "
                          "VALUES (:b, 'alice', 'Alice', 'direct', 'owner:alice', 'warm')"), {"b": business.id})
    before = counts(scratch)

    alembic("upgrade", TRACKING)

    assert counts(scratch) == before == {"businesses": 1, "people": 1}
    doors = columns(scratch, "businesses")["doors"]
    assert doors["nullable"] is False
    assert "'[]'" in str(doors["default"])
    people = columns(scratch, "people")
    assert people["heard_via"]["nullable"] and people["heard_words"]["nullable"]
    assert (people["heard_via"]["type"].length, people["heard_words"]["type"].length) == (40, 120)
    with scratch.connect() as conn:
        assert conn.execute(text("SELECT doors FROM businesses")).scalar() == []
        assert conn.execute(text("SELECT heard_via, heard_words FROM people")).one() == (None, None)


def test_the_downgrade_drops_the_three_columns_and_the_chain_comes_back_to_the_models(scratch):  # noqa: F811
    alembic("upgrade", TRACKING)
    assert [d for d in drift(scratch) if "kit_texts" not in repr(d)] == [], "later revisions add only the kit's table"

    alembic("downgrade", BEFORE_TRACKING)

    assert "doors" not in columns(scratch, "businesses")
    assert {"heard_via", "heard_words"}.isdisjoint(columns(scratch, "people"))
    alembic("upgrade", "head")
    assert drift(scratch) == []
