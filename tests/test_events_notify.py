"""Committed row changes reach the console as Postgres notifications on this database; uncommitted ones never do."""
import json
import threading
import time
from contextlib import contextmanager

import httpx
import psycopg
import pytest
import uvicorn
from justshipit.api import app as app_module
from justshipit.api.app import app
from justshipit.db.models import ActivityEvent
from justshipit.db.session import _factory, session_scope
from justshipit.services import events
from justshipit.settings import settings


@pytest.fixture
def listener():
    conn = psycopg.connect(settings().checkpoint_dsn, autocommit=True)
    conn.execute(f"LISTEN {events.CHANNEL}")
    yield conn
    conn.close()


def heard(conn, until_ids: set[int], wait_s: float = 5) -> dict[int, dict]:
    """Notifications by event id, read until every id in `until_ids` arrived or `wait_s` passed. Other tests' events
    on this database may arrive too, so a test follows its own rows by id."""
    got: dict[int, dict] = {}
    deadline = time.monotonic() + wait_s
    while not until_ids <= got.keys() and time.monotonic() < deadline:
        for note in conn.notifies(timeout=0.5):
            if (event := json.loads(note.payload))["table"] == "activity_events":
                got[event["id"]] = event
    return got


def probe(**kw) -> ActivityEvent:
    return ActivityEvent(actor="system", verb="probe", entity="probe", summary=kw.pop("summary", "probe"), **kw)


def test_a_committed_change_is_heard_and_a_rolled_back_one_is_not(listener):
    s = _factory()()
    try:
        dropped = probe()
        s.add(dropped)
        s.flush()
        dropped_id = dropped.id
        s.rollback()
    finally:
        s.close()
    with session_scope() as s:  # notifications arrive in commit order, so a sent rollback would come first
        kept = probe()
        s.add(kept)
        s.flush()

    got = heard(listener, {kept.id}, wait_s=3)
    assert kept.id in got, "a committed change never reached the listener"
    assert dropped_id not in got, "a rolled-back change reached the listener"
    assert (got[kept.id]["kind"], got[kept.id]["verb"]) == ("change", "probe")


def test_a_released_savepoint_is_not_heard_before_its_transaction_commits(listener):
    s = _factory()()
    try:
        with s.begin_nested():
            inner = probe()
            s.add(inner)
        assert inner.id not in heard(listener, {inner.id}, wait_s=1), "heard before the transaction committed"
        s.commit()
    finally:
        s.close()
    assert inner.id in heard(listener, {inner.id}), "the committed transaction was never heard"


def test_an_event_too_long_for_a_notification_still_arrives_with_its_table_and_id(listener):
    with session_scope() as s:
        big = probe(summary="요약" * 3000, data={"blob": "x" * 9000})
        s.add(big)
        s.flush()
    got = heard(listener, {big.id})
    assert big.id in got, "an oversized event never arrived"
    assert (got[big.id]["verb"], "data" in got[big.id], "summary" in got[big.id]) == ("probe", False, False)


@contextmanager
def console():
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.02)
        yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(5)


def test_the_console_stream_relays_a_committed_change(monkeypatch, api_token):
    monkeypatch.setattr(app_module, "_SSE_WAKE_S", 0.05)
    with console() as base, httpx.stream("GET", f"{base}/api/events", headers={"Authorization": f"Bearer {api_token}"},
                                         timeout=10) as stream:
        lines = stream.iter_lines()
        assert next(lines) == "event: ready"
        with session_scope() as s:
            row = probe()
            s.add(row)
            s.flush()
        deadline = time.monotonic() + 5
        for line in lines:
            event = json.loads(line[6:]) if line.startswith("data: ") else {}
            if (event.get("table"), event.get("id")) == ("activity_events", row.id):
                break
            assert time.monotonic() < deadline, "the stream never relayed the committed change"


def test_a_stream_that_cannot_reach_the_database_ends_so_the_browser_reconnects(monkeypatch, api_token):
    monkeypatch.setattr(settings(), "database_url", "postgresql+psycopg://nobody:none@127.0.0.1:9/none")
    with console() as base:
        started = time.monotonic()
        with httpx.stream("GET", f"{base}/api/events", headers={"Authorization": f"Bearer {api_token}"},
                          timeout=10) as stream:
            assert stream.status_code == 200
            assert list(stream.iter_lines()) == []
        assert time.monotonic() - started < 8, "the stream hung instead of ending"


def listening_backends() -> int:
    with psycopg.connect(settings().checkpoint_dsn, autocommit=True) as c:
        return c.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                         "AND query = %s", (f"LISTEN {events.CHANNEL}",)).fetchone()[0]


def test_a_closed_stream_gives_its_listening_connection_back(api_token):
    with console() as base:
        before = listening_backends()
        with httpx.stream("GET", f"{base}/api/events", headers={"Authorization": f"Bearer {api_token}"},
                          timeout=10) as stream:
            lines = stream.iter_lines()  # a discarded iterator would close the response early
            assert next(lines) == "event: ready"
            assert listening_backends() == before + 1
        deadline = time.monotonic() + 5
        while listening_backends() > before:
            assert time.monotonic() < deadline, "the stream's LISTEN connection outlived the stream"
            time.sleep(0.1)


def test_an_event_that_cannot_be_sent_never_fails_the_committed_work():
    from justshipit.db.session import engine
    events.publish(engine(), "change", [{"table": "activity_events", "id": 1, "summary": "\ud800"}])
