"""Ctrl+C stops the worker and the console cleanly, the way `uv run ship worker` / `uv run ship api` are run."""
import signal
import threading
import time

import httpx
import pytest
from justshipit.api import app as app_module
from justshipit.runtime import children
from justshipit.workers import runner, supervisor


@pytest.fixture
def worker_signals(monkeypatch):
    exits: list[int] = []
    clock = [1000.0]
    monkeypatch.setattr(runner.os, "_exit", exits.append)
    monkeypatch.setattr(children.time, "monotonic", lambda: clock[0])
    stop = threading.Event()
    restore = runner._stop_on_signals(stop)
    try:
        yield stop, exits, clock, signal.getsignal(signal.SIGINT)
    finally:
        restore()


def test_one_ctrl_c_that_arrives_twice_hands_the_jobs_back(worker_signals):
    """Under `uv run` on Windows one Ctrl+C reached the worker twice, 56 ms apart, and the second was taken for "force
    quit": the worker exited without handing its running jobs back."""
    stop, exits, clock, handler = worker_signals
    handler(signal.SIGINT, None)
    clock[0] += 0.056
    handler(signal.SIGINT, None)
    assert stop.is_set(), "the first signal stops claiming"
    assert exits == [], "the same press delivered again must not force the exit"


def test_a_second_ctrl_c_later_still_forces_the_exit(worker_signals):
    _, exits, clock, handler = worker_signals
    handler(signal.SIGINT, None)
    clock[0] += 5
    handler(signal.SIGINT, None)
    assert exits == [128 + signal.SIGINT], "pressing Ctrl+C again exits at once"


def test_ctrl_c_ends_the_open_event_streams_so_the_console_stops_at_once(monkeypatch, api_token):
    """An open browser tab held /api/events, and the console waited 21 s for it before it stopped."""
    server = app_module.ConsoleServer(app_module.server_config(host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.02)
        base = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
        with httpx.stream("GET", f"{base}/api/events", headers={"Authorization": f"Bearer {api_token}"},
                          timeout=10) as stream:
            lines = stream.iter_lines()
            assert next(lines) == "event: ready"
            pressed = time.monotonic()
            server.handle_exit(signal.SIGINT, None)
            for _ in lines:
                pass
            assert time.monotonic() - pressed < 3, "the stream outlived Ctrl+C"
        thread.join(5)
        assert not thread.is_alive(), "the console did not stop"
    finally:
        server.should_exit = True
        thread.join(5)


def test_supervise_waits_for_both_when_one_ctrl_c_arrives_twice(monkeypatch):
    """`ship supervise` runs the worker and the console in one terminal. A doubled Ctrl+C made it exit at once, so
    the prompt came back while both were still stopping."""
    exits: list[int] = []
    monkeypatch.setattr(supervisor.os, "_exit", exits.append)
    clock = [1000.0]
    monkeypatch.setattr(children.time, "monotonic", lambda: clock[0])
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    stop = threading.Event()
    try:
        supervisor.stop_on_signals(stop)
        handler = signal.getsignal(signal.SIGINT)
        handler(signal.SIGINT, None)
        clock[0] += 0.056
        handler(signal.SIGINT, None)
        assert stop.is_set() and exits == []
        clock[0] += 5
        handler(signal.SIGINT, None)
        assert exits == [128 + signal.SIGINT]
    finally:
        for sig, h in previous.items():
            signal.signal(sig, h)
