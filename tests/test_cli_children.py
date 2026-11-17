"""A CLI call (`claude -p`) runs in its own process group: the owner's Ctrl+C in the worker's console
asks the worker to finish its calls instead of killing them, and a call that times out stops the processes the CLI
started (tools, MCP servers) along with it."""
import os
import sys
import time
from pathlib import Path

import pytest
from justshipit.runtime.base import LLMError, run_process

pytestmark = pytest.mark.skipif(os.name == "nt" or not Path("/proc/self").exists(), reason="POSIX process groups")


def _running(pid: int) -> bool:
    try:
        return "\nState:\tZ" not in Path(f"/proc/{pid}/status").read_text()
    except FileNotFoundError:
        return False


def test_a_cli_call_is_outside_the_workers_process_group():
    out = run_process([sys.executable, "-c", "import os; print(os.getpgrp())"], "", None, 30)
    assert int(out.stdout) not in (os.getpgrp(), 0)


def test_a_cli_call_that_times_out_stops_the_processes_it_started(tmp_path):
    pidfile = tmp_path / "child.pid"
    with pytest.raises(LLMError, match="timed out"):
        run_process(["sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"], "", None, 1)
    child = int(pidfile.read_text())
    for _ in range(50):
        if not _running(child):
            break
        time.sleep(0.05)
    assert not _running(child)
