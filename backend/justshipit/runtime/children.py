"""Processes and containers a job has running outside the worker. A worker asked to stop ends them, so the job unwinds
now instead of after its calls, and can go straight back to the queue: nothing it started is left working."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress

# Its own process group: the owner's Ctrl+C in the worker's console asks the worker to stop, and the worker decides
# what happens to the calls; the console must not kill them under it.
GROUP: dict = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
               else {"start_new_session": True})


def stop_tree(p: subprocess.Popen) -> None:
    """End a process started with GROUP and everything it started (a git credential helper, a CLI's tools)."""
    if os.name == "nt":
        with suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, timeout=30, check=False)
        with suppress(OSError):
            p.kill()
    else:
        with suppress(ProcessLookupError, PermissionError):  # the group may have exited already
            os.killpg(p.pid, signal.SIGKILL)


def alive(pid: int) -> bool:
    """Whether a process with this pid runs on this host. A pid that runs may be a reuse, so only False proves its old
    process gone. Never os.kill(pid, 0) on Windows: signal 0 is CTRL_C_EVENT there, sent to the pid's console group."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: no process has this pid
        try:
            code = wintypes.DWORD()
            return not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


class Children:
    """One job's running children. Handler threads register and unregister them; the worker's main thread stops
    them. `stopped` stays set, so a child registered after the stop is ended as it starts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen] = set()
        self._containers: set[str] = set()
        self.stopped = False

    @contextmanager
    def process(self, p: subprocess.Popen) -> Iterator[None]:
        with self._lock:
            self._processes.add(p)
            stopped = self.stopped
        if stopped:
            stop_tree(p)
        try:
            yield
        finally:
            with self._lock:
                self._processes.discard(p)

    @contextmanager
    def container(self, name: str) -> Iterator[None]:
        with self._lock:
            self._containers.add(name)
        try:
            yield
        finally:
            with self._lock:
                self._containers.discard(name)

    def stop(self, remove_containers: Callable[[list[str]], None]) -> None:
        with self._lock:
            self.stopped = True
            processes, containers = list(self._processes), sorted(self._containers)
        for p in processes:
            stop_tree(p)
        if containers:
            remove_containers(containers)


SAME_PRESS_S = 1.0


def stop_handler(stop: threading.Event, first: Callable[[int], None], force: Callable[[int], None]):
    """A SIGINT/SIGTERM handler: the first signal calls `first` and sets `stop`; a later one calls `force`. A repeat
    within SAME_PRESS_S is the same Ctrl+C: under `uv run` on Windows one press arrived twice, 56 ms apart, and was
    taken for "force quit"."""
    pressed: list[float] = []

    def handle(signum, _frame) -> None:
        if stop.is_set():
            if pressed and time.monotonic() - pressed[0] < SAME_PRESS_S:
                return
            force(signum)
            return
        pressed.append(time.monotonic())
        first(signum)
        stop.set()
    return handle
