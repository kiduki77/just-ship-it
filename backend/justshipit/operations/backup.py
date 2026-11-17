"""A copy of the app database in var/backups/, made daily by the scheduler and by `ship backup`, the newest
`operations.backups_kept` kept.

pg_dump runs inside the compose postgres container: its version always matches the server's, and a local connection
there needs no password, so none is passed or stored. The custom-format dump streams to a file on this PC. Restore
into a stopped company (worker and console off):

    docker compose exec -T postgres pg_restore -U ship -d ship --clean --if-exists < var/backups/<file>.dump
"""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from justshipit.i18n import msg
from justshipit.services import app_settings
from justshipit.settings import company_config, settings

log = logging.getLogger(__name__)

TIMEOUT_S = 1800


class BackupFailed(RuntimeError):
    pass


def kept() -> int:
    return int(company_config()["operations"].get("backups_kept", 7))


def run(keep: int | None = None) -> Path:
    url = make_url(settings().database_url)
    directory = settings().var_dir / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{url.database}-{datetime.now():%Y%m%d-%H%M%S}.dump"
    partial = target.with_name(target.name + ".partial")
    command = ["docker", "compose", "exec", "-T", "postgres",
               "pg_dump", "-U", url.username or "postgres", "-d", url.database, "--format=custom"]
    try:
        with partial.open("wb") as out:
            p = subprocess.run(command, cwd=settings().root, stdout=out, stderr=subprocess.PIPE, timeout=TIMEOUT_S,
                               check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        partial.unlink(missing_ok=True)
        raise BackupFailed(msg("operations.backup_failed", app_settings.language(), error=type(exc).__name__)) from None
    if p.returncode != 0:
        partial.unlink(missing_ok=True)
        why = p.stderr.decode("utf-8", "replace").strip()[-500:]
        raise BackupFailed(msg("operations.backup_exit", app_settings.language(), code=p.returncode, why=why))
    partial.replace(target)
    for old in sorted(directory.glob(f"{url.database}-*.dump"))[:-(keep or kept())]:
        old.unlink()
    log.info("database backed up to %s", target)
    return target
