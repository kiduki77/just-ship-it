"""Keeps the worker and the console running on a PC no one watches: `ship supervise` starts both and starts each
again when it exits, waiting longer while it keeps failing fast (a database that is still coming up after a reboot).
`ship supervise --install` adds a Windows logon task that runs it, so a reboot brings the company back.

Ctrl+C in its console reaches the children too: the worker hands its jobs back (runner.serve), the console closes,
and the supervisor waits for both and starts nothing again."""
from __future__ import annotations

import base64
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

from justshipit.runtime import children
from justshipit.settings import settings

log = logging.getLogger("justshipit.supervisor")

TASK_NAME = "Just Ship It"
# Names the logon task had before the app was renamed. Installing replaces them and removing removes them too, so a PC
# that installed under an old name never runs two supervisors.
OLD_TASK_NAMES = ("AI Company OS",)
BACKOFF_S = (5, 15, 60, 300)
HEALTHY_AFTER_S = 300  # a child that ran this long was fine: a restart after it starts the backoff over
STOP_WAIT_S = 120


@dataclass
class Child:
    name: str
    args: list[str]
    proc: subprocess.Popen | None = None
    started: float = 0.0
    quick_exits: int = 0
    start_at: float = 0.0


def managed(concurrency: int, port: int) -> list[Child]:
    cli = [sys.executable, "-m", "justshipit.cli"]
    return [Child("worker", [*cli, "worker", "--concurrency", str(concurrency)]),
            Child("api", [*cli, "api", "--port", str(port)])]


def run(kids: list[Child], stop: threading.Event, poll_s: float = 1.0) -> None:
    while not stop.is_set():
        now = time.monotonic()
        for c in kids:
            if c.proc is not None and c.proc.poll() is not None:
                ran = now - c.started
                c.quick_exits = 0 if ran >= HEALTHY_AFTER_S else c.quick_exits + 1
                wait = BACKOFF_S[min(max(c.quick_exits - 1, 0), len(BACKOFF_S) - 1)]
                log.warning("%s exited with code %s after %ds; starting it again in %ds", c.name, c.proc.returncode,
                            ran, wait)
                c.proc, c.start_at = None, now + wait
            if c.proc is None and now >= c.start_at:
                c.proc, c.started = subprocess.Popen(c.args, cwd=settings().root), now
                log.info("%s started (pid %s)", c.name, c.proc.pid)
        stop.wait(poll_s)
    _stop(kids)


def _stop(kids: list[Child]) -> None:
    """The children got the console's Ctrl+C themselves; a stop that came another way (SIGTERM) is passed on. One
    that is still running after STOP_WAIT_S is ended."""
    running = [c for c in kids if c.proc is not None and c.proc.poll() is None]
    if os.name != "nt":
        for c in running:
            c.proc.send_signal(signal.SIGTERM)
    deadline = time.monotonic() + STOP_WAIT_S
    for c in running:
        try:
            c.proc.wait(max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            log.error("%s did not stop in %ss; ending it", c.name, STOP_WAIT_S)
            c.proc.kill()
        log.info("%s stopped", c.name)


def stop_on_signals(stop: threading.Event) -> None:
    on_signal = children.stop_handler(stop, lambda signum: log.info("signal %s: stopping the worker and the console",
                                                                    signum), lambda signum: os._exit(128 + signum))

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, on_signal)


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def task_script(concurrency: int, port: int, *, remove: bool = False) -> str:
    """PowerShell for the logon task. Not schtasks: a task it makes is stopped after 72 hours and on battery."""
    names = ", ".join(_quoted(n) for n in (TASK_NAME, *OLD_TASK_NAMES))
    if remove:
        return "\n".join([
            f"$tasks = Get-ScheduledTask -TaskName {names} -ErrorAction SilentlyContinue",
            f"if (-not $tasks) {{ throw 'No logon task named {TASK_NAME}' }}",
            "$tasks | Unregister-ScheduledTask -Confirm:$false",
        ])
    olds = ", ".join(_quoted(n) for n in OLD_TASK_NAMES)
    arguments = f"-m justshipit.cli supervise --concurrency {concurrency} --port {port}"
    return "\n".join([
        f"$action = New-ScheduledTaskAction -Execute {_quoted(sys.executable)} -Argument {_quoted(arguments)} "
        f"-WorkingDirectory {_quoted(str(settings().root))}",
        "$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME",
        "$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) "
        "-MultipleInstances IgnoreNew",
        f"Get-ScheduledTask -TaskName {olds} -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false",
        f"Register-ScheduledTask -TaskName {_quoted(TASK_NAME)} -Action $action -Trigger $trigger "
        "-Settings $settings -Force | Out-Null",
    ])


def install(concurrency: int, port: int, *, remove: bool = False) -> subprocess.CompletedProcess:
    encoded = base64.b64encode(task_script(concurrency, port, remove=remove).encode("utf-16-le")).decode()
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                          capture_output=True, text=True, timeout=120, check=False)
