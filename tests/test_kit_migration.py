"""The kit revision creates `kit_texts` and nothing else (no LangGraph checkpoint table, no other table's column), writes
no row, and its table refuses what the design forbids: a message with no person, a person on another part, a text with
both a body and a refusal or neither, and two live texts in one slot. Runs on the migration tests' own scratch database."""
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from test_migrations import alembic, drift, old_business, scratch  # noqa: F401  (scratch is a fixture)

KIT = "d5f1a8c3e726"
BEFORE_KIT = "c4a8e1d7b295"


def shape(engine) -> dict[str, set[str]]:
    with engine.connect() as conn:
        insp = inspect(conn)
        return {t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names()}


def test_the_revision_adds_one_table_and_changes_no_other(scratch):  # noqa: F811
    alembic("upgrade", BEFORE_KIT)
    before = shape(scratch)

    alembic("upgrade", KIT)

    after = shape(scratch)
    assert set(after) - set(before) == {"kit_texts"} and set(before) - set(after) == set()
    assert {t: cols for t, cols in after.items() if t != "kit_texts"} == before
    with scratch.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM kit_texts")).scalar() == 0


def test_the_downgrade_drops_the_table_and_the_chain_comes_back_to_the_models(scratch):  # noqa: F811
    alembic("upgrade", "head")
    assert drift(scratch) == []

    alembic("downgrade", BEFORE_KIT)

    assert "kit_texts" not in shape(scratch)
    alembic("upgrade", "head")
    assert drift(scratch) == []


@pytest.fixture
def ids(scratch):  # noqa: F811
    alembic("upgrade", KIT)
    with scratch.begin() as conn:
        business = old_business(conn, "kit-constraints").id
        person = conn.execute(text(
            "INSERT INTO people (business_id, handle, display_name, platform, where_found_url, source) "
            "VALUES (:b, 'alice', 'Alice', 'direct', 'owner:alice', 'warm') RETURNING id"), {"b": business}).scalar()
    return business, person


def put(scratch, business, *, part="profile", slot="line", person=None, body="a text", refused=None,  # noqa: F811
        replaced=False):
    with scratch.begin() as conn:
        conn.execute(text(
            "INSERT INTO kit_texts (business_id, part, slot, person_id, lang, body, refused, basis, replaced_at) "
            "VALUES (:b, :part, :slot, :p, 'en', :body, :refused, '{}'::jsonb, "
            + ("now()" if replaced else "NULL") + ")"),
            {"b": business, "part": part, "slot": slot, "p": person, "body": body, "refused": refused})


@pytest.mark.parametrize("fields", [
    {"part": "message", "slot": "p", "person": None},
    {"part": "profile", "slot": "line", "person": "someone"},
    {"part": "invite", "slot": "known", "person": "someone"},
    {"part": "flyer", "slot": "line", "person": None},
    {"body": "a text", "refused": "contact"},
    {"body": None, "refused": None},
], ids=["message-without-person", "profile-with-person", "invite-with-person", "unknown-part", "body-and-refusal",
        "neither"])
def test_the_table_refuses_what_the_design_forbids(scratch, ids, fields):  # noqa: F811
    business, person = ids
    fields = {**fields, **({"person": person} if fields.get("person") == "someone" else {})}
    with pytest.raises(IntegrityError):
        put(scratch, business, **fields)


def test_a_slot_holds_one_live_text_and_any_number_of_replaced_ones(scratch, ids):  # noqa: F811
    business, person = ids
    put(scratch, business, part="profile", slot="line")
    put(scratch, business, part="profile", slot="line", replaced=True)
    put(scratch, business, part="profile", slot="line", replaced=True)
    put(scratch, business, part="profile", slot="short")
    put(scratch, business, part="message", slot="p", person=person)
    with pytest.raises(IntegrityError):
        put(scratch, business, part="profile", slot="line")
    with pytest.raises(IntegrityError):
        put(scratch, business, part="message", slot="p", person=person, refused="contact", body=None)
    with scratch.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM kit_texts WHERE replaced_at IS NULL")).scalar() == 3
