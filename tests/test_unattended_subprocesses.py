"""Nothing the worker waits on can hold it for good: git asks nobody and gives up with its helpers, a docker command
that gets no answer fails, and a database lock wait ends."""
import os
import stat
import time

import pytest
from justshipit.db.session import session_scope
from justshipit.sandbox import docker, hostgit
from sqlalchemy import text


@pytest.fixture
def fake_git(tmp_path, monkeypatch):
    """A `git` on PATH that writes what it was given, then hangs with a helper of its own, like a push whose
    credential helper waits for a password."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text('#!/bin/sh\n'
                   f'echo "prompt=$GIT_TERMINAL_PROMPT gcm=$GCM_INTERACTIVE" > {tmp_path}/env\n'
                   f'cat > {tmp_path}/stdin\n'
                   f'sleep 60 & echo $! > {tmp_path}/helper\n'
                   'wait\n')
    git.chmod(git.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return tmp_path


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/status") as f:
            return "State:\tZ" not in f.read()
    except FileNotFoundError:
        return False


@pytest.mark.skipif(os.name == "nt", reason="the fake git is a shell script")
def test_a_git_command_that_hangs_fails_with_its_helpers_stopped(fake_git):
    start = time.monotonic()
    out = hostgit.run(fake_git, "push", "-u", "origin", "ai/x", owner_config=True, timeout_s=1)
    assert time.monotonic() - start < 10
    assert out.returncode != 0 and "push" in out.stderr and "1s" in out.stderr
    assert (fake_git / "env").read_text().split() == ["prompt=0", "gcm=never"]
    assert (fake_git / "stdin").read_text() == ""
    helper = int((fake_git / "helper").read_text())
    for _ in range(50):
        if not _alive(helper):
            break
        time.sleep(0.05)
    assert not _alive(helper)


def test_docker_that_does_not_answer_is_unavailable_not_a_hang(docker_log, monkeypatch):
    fake = docker_log.parent / "bin" / "docker"
    fake.write_text(fake.read_text() + '[ "$1" = image ] && sleep 30\nexit 0\n')
    monkeypatch.setattr(docker, "CLI_TIMEOUT_S", 1)
    start = time.monotonic()
    with pytest.raises(docker.SandboxUnavailable):
        docker.image_present("ship/engineer:2")
    assert time.monotonic() - start < 10


def test_an_app_session_gives_up_a_lock_wait_instead_of_blocking_a_worker_slot_for_good():
    with session_scope() as s:
        assert s.scalar(text("SHOW lock_timeout")) == "2min"
