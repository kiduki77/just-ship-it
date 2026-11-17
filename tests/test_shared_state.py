"""Worker processes share provider slots (Postgres advisory locks) and quota marks (files under var/). When the
sharing fails, each process falls back to state of its own, keeps working, and says so once per transition."""
import subprocess
import sys
import threading
import time
from pathlib import Path

import psycopg
import pytest
from justshipit.db.session import engine
from justshipit.runtime import quota, shared_state, slots
from justshipit.settings import settings

ROOT = Path(__file__).resolve().parents[1]
_sleep = time.sleep  # slot waits tick faster here; time.sleep itself is what gets patched


@pytest.fixture(autouse=True)
def sharing_works():
    """Each test starts from shared state that works, so its own fallback is the first transition it logs."""
    shared_state._down.clear()


@pytest.fixture
def one_slot(monkeypatch):
    monkeypatch.setattr(slots, "_limit", lambda provider: 1)


@pytest.fixture
def database_unreachable(monkeypatch):
    engine()  # the app's own engine is already made; only new connections are refused
    monkeypatch.setattr(settings(), "database_url", "postgresql+psycopg://nobody:none@127.0.0.1:9/none")


@pytest.fixture
def var_unwritable(monkeypatch, tmp_path):
    (tmp_path / "a-file").write_text("")
    monkeypatch.setattr(settings(), "var_dir", tmp_path / "a-file" / "var")


def enter_in_thread(provider: str) -> threading.Event:
    entered = threading.Event()

    def run():
        with slots.provider_slot(provider):
            entered.set()
    threading.Thread(target=run, daemon=True).start()
    return entered


def peak_holders(provider: str, threads: int = 5) -> int:
    active, peak, lock = 0, 0, threading.Lock()

    def hold():
        nonlocal active, peak
        with slots.provider_slot(provider):
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.5)
            with lock:
                active -= 1
    started = [threading.Thread(target=hold) for _ in range(threads)]
    [t.start() for t in started]
    [t.join(30) for t in started]
    return peak


def warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING" and "Cannot share" in r.getMessage()]


def test_slots_cap_concurrent_holders_at_max_concurrency(monkeypatch, caplog):
    monkeypatch.setattr(slots, "_limit", lambda provider: 2)
    assert peak_holders("test-cap") == 2
    assert warnings(caplog) == [], "the cap came from the in-process fallback, not the shared slots"


HOLD = """
import time
from justshipit.runtime import slots
slots._limit = lambda provider: 1
with slots.provider_slot("test-crash"):
    print("held", flush=True)
    time.sleep(60)
"""


def test_a_slot_held_by_another_process_waits_until_that_process_dies(one_slot, monkeypatch):
    monkeypatch.setattr(slots.time, "sleep", lambda _s: _sleep(0.2))
    holder = subprocess.Popen([sys.executable, "-c", HOLD], cwd=ROOT, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        entered = enter_in_thread("test-crash")
        assert not entered.wait(1.5), "two processes held a provider's only slot at once"
        holder.kill()
        holder.wait(10)
        assert entered.wait(10), "a dead holder's slot was never freed"
    finally:
        holder.kill()
        holder.stdout.close()


def test_a_slot_wait_falls_back_when_the_database_drops(one_slot, monkeypatch, caplog):
    monkeypatch.setattr(slots.time, "sleep", lambda _s: _sleep(0.2))
    release, held = threading.Event(), threading.Event()

    def hold():
        with slots.provider_slot("test-drop"):
            held.set()
            release.wait(30)
    threading.Thread(target=hold, daemon=True).start()
    try:
        assert held.wait(10)
        entered = enter_in_thread("test-drop")
        with psycopg.connect(settings().checkpoint_dsn, autocommit=True) as admin:
            deadline = time.monotonic() + 10
            while not admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = current_database()"
                    " AND query LIKE 'SELECT pg_try_advisory_lock%' AND pid <> pg_backend_pid()"
                    " AND pid NOT IN (SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted)").fetchall():
                assert time.monotonic() < deadline, "the second call never waited for the slot"
                time.sleep(0.1)
        assert entered.wait(10), "a call whose slot wait lost the database never ran"
        assert any("provider slots" in m and "in-process" in m for m in warnings(caplog))
    finally:
        release.set()


def test_slots_fall_back_to_an_in_process_cap_when_the_database_is_unreachable(one_slot, database_unreachable,
                                                                              caplog):
    assert peak_holders("test-unreachable", threads=3) == 1
    assert len([m for m in warnings(caplog) if "provider slots" in m]) == 1, "logged once, not per call"


MARK = """
from justshipit.runtime import quota
quota.mark_down("claude", {until!r})
"""


def test_a_quota_mark_made_by_another_process_is_seen_here():
    until = time.time() + 3600
    subprocess.run([sys.executable, "-c", MARK.format(until=until)], cwd=ROOT, check=True)
    assert quota.down_until("claude") == until


def test_a_quota_mark_expires_at_its_reset_time():
    quota.mark_down("claude", time.time() + 0.5)
    quota._local_down.clear()  # what a process that did not make the mark knows
    assert quota.down_until("claude") is not None
    time.sleep(0.6)
    assert quota.down_until("claude") is None


def test_a_quota_mark_that_cannot_be_written_still_holds_in_this_process(var_unwritable, caplog):
    until = time.time() + 600
    quota.mark_down("claude", until)
    assert quota.down_until("claude") == until
    assert any("quota marks" in m and "this process" in m for m in warnings(caplog))


def test_falling_back_and_recovering_is_logged_once_per_transition(monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")

    def round_trips():
        for _ in range(3):
            with slots.provider_slot("test-logs"):
                pass
            quota.mark_down("claude", time.time() + 60)
            quota.down_until("claude")

    round_trips()
    caplog.clear()
    with monkeypatch.context() as broken:
        engine()
        broken.setattr(settings(), "database_url", "postgresql+psycopg://nobody:none@127.0.0.1:9/none")
        (tmp_path / "a-file").write_text("")
        broken.setattr(settings(), "var_dir", tmp_path / "a-file" / "var")
        round_trips()
    round_trips()
    round_trips()

    logs = [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "justshipit.runtime.shared_state"]
    assert [level for level, _ in logs] == ["WARNING", "WARNING", "INFO", "INFO"]
    down, up = " ".join(m for _, m in logs[:2]), " ".join(m for _, m in logs[2:])
    assert "provider slots" in down and "in-process" in down and "quota marks" in down and "this process" in down
    assert "provider slots" in up and "quota marks" in up


def test_a_quota_mark_is_shared_even_while_a_reader_briefly_holds_the_old_one(monkeypatch):
    """On Windows os.replace fails while another worker has the old mark open; the write retries instead of keeping
    the mark to this process only."""
    real, failures = quota.os.replace, iter([PermissionError("in use")])

    def replace_after_a_reader_lets_go(src, dst):
        if (exc := next(failures, None)) is not None:
            raise exc
        real(src, dst)

    monkeypatch.setattr(quota.os, "replace", replace_after_a_reader_lets_go)
    until = time.time() + 3600
    quota.mark_down("claude", until)
    quota._local_down.clear()  # what another worker process knows
    assert quota.down_until("claude") == until
