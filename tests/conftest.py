import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import psycopg
import pytest

from justshipit.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
# Parallel worktrees each set their own name so concurrent runs never drop each other's database.
TEST_DB = os.environ.get("SHIP_TEST_DB", "ship_test")
_ADMIN = Settings(database_url="")  # the compose Postgres, with the password .env or the environment gives
os.environ["SHIP_DATABASE_URL"] = _ADMIN.database_url.rsplit("/", 1)[0] + f"/{TEST_DB}"
_tmp = Path(tempfile.mkdtemp(prefix="ship-test-"))
os.environ["SHIP_REGISTRY_DIR"] = str(_tmp / "registry")
os.environ["SHIP_PRODUCTS_DIR"] = str(_tmp / "products")
os.environ["SHIP_VAR_DIR"] = str(_tmp / "var")


@pytest.fixture(scope="session", autouse=True)
def database():
    with psycopg.connect(_ADMIN.checkpoint_dsn, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {TEST_DB}")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True,
                   env={**os.environ})
    yield


@pytest.fixture(autouse=True)
def _shared_services_keep_their_passwords(monkeypatch):
    """The test containers reach the owner's live compose Postgres, so `ship init` under test would set a test-made
    password on it and lock the running worker and console out."""
    from justshipit.services import credentials

    def refuse(_env_path):
        raise AssertionError("a test called credentials.ensure: stub it, the compose services are the owner's")
    monkeypatch.setattr(credentials, "ensure", refuse)


@pytest.fixture(autouse=True)
def _no_quota_marks():
    """Quota marks live in var/, which every test of a run shares, so one test's used-up quota would make the next
    test's agent calls wait."""
    from justshipit.runtime import quota
    from justshipit.settings import settings

    def clear():
        quota._local_down.clear()
        for mark in (settings().var_dir / "state").glob("provider_down.*"):
            mark.unlink(missing_ok=True)
    clear()
    yield
    clear()


@pytest.fixture(autouse=True)
def _pc_is_ready(monkeypatch):
    """The test containers have no Docker, proxy or Claude login, and the home screen's alerts would list all three.
    Every check answers ok here; tests/test_system_health.py runs the real ones."""
    from justshipit.services import system_health
    system_health.forget()
    monkeypatch.setattr(system_health, "run_all",
                        lambda *a, **k: [system_health.Result(c.id, "ok") for c in system_health.CHECKS])
    yield
    system_health.forget()


@pytest.fixture
def s():
    from justshipit.db.session import session_scope
    with session_scope() as session:
        yield session


# Job kinds tests make up for their own handlers.
MADE_UP_KINDS = ("lost_run", "lease_run", "lease_dev", "recovery_noop", "recovery_run", "recovery_first",
                 "recovery_block", "recovery_busy", "defer_probe", "wait_probe", "noop", "reservation_probe",
                 "lost_suspend", "stale_run", "somebody_elses_job")


@pytest.fixture
def made_up_kinds(monkeypatch):
    """Declare the kinds tests make up, the way a new kind must be declared (gate.POLICY), so enqueue takes them and
    the worker starts with their handlers."""
    from justshipit.services import gate
    for kind in MADE_UP_KINDS:
        monkeypatch.setitem(gate.POLICY, kind, gate.Rule(gate.Gate.ASKED, "a kind this test made up for its handler"))


@pytest.fixture
def outside_cycles(monkeypatch):
    """Every business runs outside cycles, as one made from the CLI does: its stage work runs as asked, and its
    experiments' retros make their own next experiments and cards. For the tests of that path."""
    from justshipit.services import gate
    monkeypatch.setattr(gate, "managed", lambda s, business_id: False)


@pytest.fixture
def strengths_answered():
    """An owner who already said what they are good at (Settings > About you), so a new business's kickoff opens on
    the CEO's first question rather than the server's strengths question (kickoff.STRENGTHS_TURN). Never asked again
    after the test."""
    from justshipit.db.models import AppSetting
    from justshipit.db.session import session_scope
    from justshipit.services import app_settings
    from sqlalchemy import delete

    def wipe():
        with session_scope(lease_checked=False) as s:
            s.execute(delete(AppSetting).where(AppSetting.section == "owner"))
    wipe()
    with session_scope() as s:
        app_settings.save(s, "owner", {"strengths": ["software"]}, actor="owner")
    yield
    wipe()


@pytest.fixture
def own_jobs_settled():
    """Settle every job the test made, whatever state it was left in, so a later file's worker never claims it. The
    plan-approval derivations it made are removed, as the kits remove theirs: test_door_policy counts every one."""
    from sqlalchemy import func, select, text
    from justshipit.db.models import Job
    from justshipit.db.session import session_scope
    with session_scope() as s:
        mark = s.scalar(select(func.coalesce(func.max(Job.id), 0)))
    yield
    with session_scope(lease_checked=False) as s:
        s.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL, lease_token = NULL, held_until = NULL "
                       "WHERE id > :mark AND status IN ('queued', 'gated', 'running')"), {"mark": mark})
        s.execute(text("DELETE FROM jobs WHERE id > :mark AND kind = 'derive_door_rules'"), {"mark": mark})


@pytest.fixture
def owner_asked():
    """Model calls made in the test's own thread, outside any worker job, count as the owner's own (gate.asked):
    run_agent refuses any other call outside a job."""
    from justshipit.services import gate
    with gate.asked("test"):
        yield


@pytest.fixture
def project(s):
    from justshipit.services import projects
    return projects.create(s, "테스트 목표", name="Test", budget_usd=10, start=False)


def store_usage(**body) -> None:
    """Save the usage settings as a row, without the write-side checks: what is stored after a code change made a
    cap too small. Tests delete AppSetting rows to clean up."""
    from justshipit.db.models import AppSetting
    from justshipit.db.session import session_scope
    with session_scope(lease_checked=False) as s:
        s.merge(AppSetting(section="usage", value=body, updated_by="test"))


@pytest.fixture
def api_token():
    """A fresh console API token, the one a logged-in console or a script presents."""
    from justshipit.api import access
    from justshipit.settings import settings
    return access.rotate_token(settings().var_dir)


@pytest.fixture
def email_live():
    """The owner has turned the email connector live in Settings. Request this before the approval: a grant
    remembers the connector's mode at approval, and a connector opened later needs a new approval."""
    from sqlalchemy import delete
    from justshipit.db.models import AppSetting
    from justshipit.db.session import session_scope
    from justshipit.services import app_settings
    with session_scope() as s:
        app_settings.save(s, "connectors", {"email": {"mode": "live"}}, actor="test")
    yield
    with session_scope() as s:
        s.execute(delete(AppSetting).where(AppSetting.section == "connectors"))


@pytest.fixture
def dead_worker() -> str:
    """The id of a worker that ran on this host and whose process is gone. Its job still waits out the rerun
    delay: the CLI calls and containers it started outlive it."""
    p = subprocess.Popen(["true"])
    p.wait()
    with pytest.raises(ProcessLookupError):
        os.kill(p.pid, 0)
    from justshipit.settings import HOST
    return f"{HOST}:{p.pid}:dead00"


FAKE_DOCKER = """#!/bin/sh
echo "$@" >> "$FAKE_DOCKER_LOG"
if [ "$1" = ps ]; then
  printf 'ship-eng-dead\\tworker-dead\\nship-eng-live\\tworker-live\\nship-eng-old\\t\\nship-analysis-x\\tworker-dead\\n'
  printf 'ship-test-dead\\tworker-dead\\nship-test-live\\tworker-live\\n'
fi
"""


@pytest.fixture
def docker_log(tmp_path, monkeypatch):
    """A `docker` on PATH that records its arguments and answers `ps` with fixed containers, so a test that
    runs the worker never reaches the real daemon."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(FAKE_DOCKER)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "docker.log"
    log.write_text("")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    return log


@pytest.fixture
def korean_owner(monkeypatch):
    """An owner who reads Korean: the settings say so wherever they are read, with no settings row written, so a
    test that compares the stored settings still sees none."""
    from justshipit.services import app_settings

    real = app_settings.load

    def load(lang=app_settings.DEFAULT_LANG):
        eff, problems = real(lang)
        return eff.model_copy(update={"general": eff.general.model_copy(update={"language": "ko"})}), problems

    monkeypatch.setattr(app_settings, "load", load)
