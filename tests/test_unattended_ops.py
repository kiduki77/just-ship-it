"""What keeps the company running and visible on a PC no one watches: a supervisor that starts the worker and the
console again, a daily database backup, a health answer, and one Slack notice when the worker stops."""
import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from justshipit.db.models import Schedule, Worker
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.operations import backup, notify, scheduler
from justshipit.settings import settings
from justshipit.workers import supervisor
from fastapi.testclient import TestClient
from notify_kit import choose, outbox_files, wipe
from sqlalchemy import delete, select


@pytest.fixture
def fake_pg_dump(docker_log):
    """`docker compose exec ... pg_dump` writes a dump to stdout; FAKE_DUMP_FAIL makes it fail like a stopped
    container."""
    fake = docker_log.parent / "bin" / "docker"
    fake.write_text(fake.read_text() + '[ -n "$FAKE_DUMP_FAIL" ] && { echo "service postgres is not running" >&2; '
                                       'exit 1; }\n[ "$1" = compose ] && printf PGDMP-dump\nexit 0\n')
    return docker_log


def test_a_backup_is_a_dump_of_the_app_database_and_the_oldest_go(fake_pg_dump, monkeypatch):
    directory = settings().var_dir / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    database = settings().database_url.rsplit("/", 1)[1]
    for old in ("20260101-030000", "20260102-030000"):
        (directory / f"{database}-{old}.dump").write_bytes(b"old")

    made = backup.run(keep=2)

    assert made.read_bytes() == b"PGDMP-dump"
    assert sorted(p.name for p in directory.glob("*.dump")) == [f"{database}-20260102-030000.dump", made.name]
    call = fake_pg_dump.read_text().splitlines()[-1].split()
    assert call[:5] == ["compose", "exec", "-T", "postgres", "pg_dump"] and database in call
    assert not any(directory.glob("*.partial"))

    monkeypatch.setenv("FAKE_DUMP_FAIL", "1")
    with pytest.raises(backup.BackupFailed, match="not running"):
        backup.run(keep=2)
    assert not any(directory.glob("*.partial")) and made.exists()


def test_the_scheduler_backs_the_database_up_every_day():
    with session_scope() as s:
        scheduler.sync(s)
        sc = s.scalar(select(Schedule).where(Schedule.kind == "backup"))
        assert (sc.enabled, sc.interval_hours) == (True, 24)


def test_health_says_what_is_up(api_token):
    from justshipit.api.app import app
    from justshipit.services import queue
    with session_scope() as s:
        s.execute(delete(Worker))
    console = TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": f"Bearer {api_token}"})
    body = console.get("/api/health").json()
    assert body == {"ok": False, "api": True, "database": True, "worker": {"alive": False, "last_seen": None}}
    with session_scope() as s:
        queue.worker_seen(s, "health-probe")
    body = console.get("/api/health").json()
    assert (body["ok"], body["worker"]["alive"]) == (True, True), "ok follows the database and a live worker alone"
    with session_scope() as s:
        s.execute(delete(Worker))


@pytest.fixture
def stopped_worker():
    wipe()
    choose(mode="outbox", kinds=["failed"])
    seen = datetime.now(timezone.utc) - timedelta(hours=1)
    with session_scope() as s:
        s.execute(delete(Worker))
        s.add(Worker(id="worker-gone", seen_at=seen))
    yield seen
    wipe()


def test_a_stopped_worker_is_announced_once_per_outage(stopped_worker):
    assert notify.announce_worker_down() == "outbox"
    assert notify.announce_worker_down() is None
    (sent,) = outbox_files()
    assert msg("notify.worker_down.title", "en") in json.loads(sent.read_text(encoding="utf-8"))["text"]

    with session_scope() as s:  # it came back, then stopped again: a new outage
        s.get(Worker, "worker-gone").seen_at = stopped_worker + timedelta(minutes=30)
    assert notify.announce_worker_down() == "outbox"
    assert len(outbox_files()) == 2


def test_a_live_worker_is_not_announced(stopped_worker):
    with session_scope() as s:
        s.get(Worker, "worker-gone").seen_at = datetime.now(timezone.utc)
    assert notify.announce_worker_down() is None and outbox_files() == []


def _supervise(kids):
    stop = threading.Event()
    t = threading.Thread(target=supervisor.run, args=(kids, stop), kwargs={"poll_s": 0.05})
    t.start()
    return t, stop


def test_the_supervisor_starts_a_child_again_when_it_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "BACKOFF_S", (0,))
    starts = tmp_path / "starts"
    kid = supervisor.Child("worker", [sys.executable, "-c", f"open({str(starts)!r}, 'a').write('x')"])
    t, stop = _supervise([kid])
    deadline = time.monotonic() + 10
    while (not starts.exists() or len(starts.read_text()) < 3) and time.monotonic() < deadline:
        time.sleep(0.05)
    stop.set()
    t.join(10)
    assert not t.is_alive() and len(starts.read_text()) >= 3


@pytest.mark.skipif(sys.platform == "win32", reason="the stop is passed on as SIGTERM off Windows")
def test_stopping_the_supervisor_stops_its_children_and_starts_nothing(tmp_path):
    kid = supervisor.Child("api", [sys.executable, "-c", "import time; time.sleep(60)"])
    t, stop = _supervise([kid])
    deadline = time.monotonic() + 10
    while kid.proc is None and time.monotonic() < deadline:
        time.sleep(0.05)
    stop.set()
    t.join(10)
    assert not t.is_alive() and kid.proc.poll() is not None


def test_the_logon_task_runs_the_supervisor_without_a_time_limit():
    script = supervisor.task_script(4, 8000)
    assert "-m justshipit.cli supervise --concurrency 4 --port 8000" in script
    assert "-AtLogOn" in script and "-ExecutionTimeLimit ([TimeSpan]::Zero)" in script
    assert f"-WorkingDirectory '{settings().root}'" in script


def test_the_logon_task_under_the_old_app_name_is_replaced_and_removed():
    """The app was renamed: an install must not leave the old task running a second supervisor."""
    install = supervisor.task_script(4, 8000)
    assert "Get-ScheduledTask -TaskName 'AI Company OS' -ErrorAction SilentlyContinue | Unregister-ScheduledTask" in install
    assert install.index("'AI Company OS'") < install.index("Register-ScheduledTask -TaskName 'Just Ship It'")
    remove = supervisor.task_script(4, 8000, remove=True)
    assert "Get-ScheduledTask -TaskName 'Just Ship It', 'AI Company OS'" in remove
    assert "Unregister-ScheduledTask -Confirm:$false" in remove
