"""Is this PC ready to do the company's work? One answer for the console, the terminal and `ship doctor`.

Each check asks one thing about the PC (Docker, the sandbox image, the egress proxy, the Claude CLI and its login,
a worker, the database's migrations) and answers ok, a `Problem`, or skipped when what it needs failed first. A
Problem is a catalog code: `system.<code>.problem` is the owner's sentence and `system.<code>.fix` the way out, both
in the owner's language. Results hold codes, not text, so one run serves either language.

The checks run side by side under one deadline, so a hung Docker costs the caller DEADLINE_S, not a minute. The
console polls `snapshot()`, which serves the last run for CACHE_S and refreshes behind the caller after that.
Nothing here reads, returns or logs a token: the Claude login is only asked whether its file is there.
"""
from __future__ import annotations

import logging
from datetime import datetime
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from justshipit.i18n import DEFAULT, Lang, msg
from justshipit.settings import company_config, settings

log = logging.getLogger(__name__)

DEADLINE_S = 8
CACHE_S = 30


@dataclass(frozen=True)
class Problem:
    code: str
    params: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Result:
    id: str
    state: Literal["ok", "problem", "skipped"]
    problem: Problem | None = None


@dataclass(frozen=True)
class Check:
    id: str
    run: Callable[[], Problem | None]
    needs: str | None = None  # a check that must be ok first; this one is skipped otherwise


def _docker() -> Problem | None:
    from justshipit.sandbox import docker
    return None if docker.docker_ok() else Problem("docker.down")


def _image() -> Problem | None:
    from justshipit.sandbox import docker
    image = company_config()["engineering"]["sandbox"]["image"]
    try:
        return None if docker.image_present(image) else Problem("image.missing", {"image": image})
    except docker.SandboxUnavailable:
        return Problem("slow", {"s": DEADLINE_S})


def _egress() -> Problem | None:
    from justshipit.sandbox import docker
    return None if docker.egress_running() else Problem("egress.down", {"proxy": docker.EGRESS_PROXY})


def _claude() -> Problem | None:
    from justshipit.runtime import host_auth
    from justshipit.runtime.base import LLMError, resolve_bin
    try:
        resolve_bin("claude", settings().claude_bin)
    except LLMError:
        return Problem("claude.missing")
    # The container reads the host's login file; a host-isolated engineer uses the CLI's own login.
    if company_config()["engineering"].get("isolation", "container") == "container" and not host_auth.login_present():
        return Problem("claude.login", {"path": host_auth.credentials_path()})
    return None


def _database() -> Problem | None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text

    from justshipit.db.session import session_scope
    try:
        with session_scope(lease_checked=False) as s:
            current = s.scalar(text("select version_num from alembic_version"))
    except Exception:  # noqa: BLE001 - any failure to ask is the answer
        return Problem("database.down")
    cfg = Config()
    cfg.set_main_option("script_location", str(settings().root / "backend" / "justshipit" / "db" / "migrations"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    if current in heads:
        return None
    return Problem("database.behind", {"db": current or "-", "code": ", ".join(heads)})


def _worker() -> Problem | None:
    from justshipit.db.session import session_scope
    from justshipit.services import queue
    with session_scope(lease_checked=False) as s:
        alive = queue.last_worker_seen(s)[1]
    return None if alive else Problem("worker.down")


CHECKS: tuple[Check, ...] = (
    Check("docker", _docker),
    Check("image", _image, needs="docker"),
    Check("egress", _egress, needs="docker"),
    Check("claude", _claude),
    Check("database", _database),
    Check("worker", _worker, needs="database"),
)
_BY_ID = {c.id: c for c in CHECKS}
# Checks about the PC itself. A worker is started after the console, so a report at startup leaves it out.
STARTUP = tuple(c.id for c in CHECKS if c.id != "worker")


def run_all(deadline_s: float = DEADLINE_S) -> list[Result]:
    done: dict[str, Result] = {}

    def work(check: Check) -> None:
        try:
            problem = check.run()
            done[check.id] = Result(check.id, "problem" if problem else "ok", problem)
        except Exception as exc:  # a check that breaks is a problem to show, not a crash
            log.exception("system check %s failed", check.id)
            done[check.id] = Result(check.id, "problem", Problem("error", {"error": type(exc).__name__}))

    threads = [threading.Thread(target=work, args=(c,), daemon=True, name=f"health-{c.id}") for c in CHECKS]
    for t in threads:
        t.start()
    end = time.monotonic() + deadline_s
    for t in threads:
        t.join(max(0.0, end - time.monotonic()))
    out: dict[str, Result] = {}
    for c in CHECKS:
        r = done.get(c.id) or Result(c.id, "problem", Problem("slow", {"s": int(deadline_s)}))
        if c.needs and out[c.needs].state != "ok":
            r = Result(c.id, "skipped")
        out[c.id] = r
    return list(out.values())


_lock = threading.Lock()
_run_lock = threading.Lock()
_cache: tuple[float, list[Result]] | None = None
_refreshing = False


def _refresh(*, fresh: bool, arrived: float | None = None) -> list[Result]:
    """Run the checks, one run at a time. A caller that waited for the lock takes the run that finished meanwhile: for
    `fresh` one that began after the caller arrived would be needed, but a run that finished after it did is as new as
    the caller could have made it, so a burst of fresh calls costs one run and not one each."""
    global _cache, _refreshing
    arrived = time.monotonic() if arrived is None else arrived
    with _run_lock:
        if _cache and (_cache[0] >= arrived if fresh else time.monotonic() - _cache[0] < CACHE_S):
            return _cache[1]  # another caller just ran them
        try:
            results = run_all()
            _cache = (time.monotonic(), results)
        finally:
            _refreshing = False
        return results


def snapshot(*, fresh: bool = False) -> list[Result]:
    """The checks' last results: at most CACHE_S old, or run now with `fresh`. Past CACHE_S the old results are
    served while a thread refreshes them, so a poll never waits on Docker."""
    global _refreshing
    arrived = time.monotonic()
    with _lock:
        cached = _cache
        if cached and not fresh:
            if time.monotonic() - cached[0] < CACHE_S:
                return cached[1]
            if not _refreshing:
                _refreshing = True
                threading.Thread(target=_refresh, kwargs={"fresh": True}, daemon=True, name="health-refresh").start()
            return cached[1]
    return _refresh(fresh=fresh, arrived=arrived)


def forget() -> None:
    """Drop the cached results; the next snapshot runs the checks."""
    global _cache
    with _lock:
        _cache = None


def owner_language() -> Lang:
    """The owner's language, or the default when the database cannot say (it may be the thing that is down)."""
    try:
        from justshipit.services import app_settings
        return app_settings.language()
    except Exception:  # noqa: BLE001
        return DEFAULT


def describe(r: Result, lang: Lang) -> dict:
    """One check for the screen: its name, state and, for a problem, the sentence and the fix."""
    title = msg(f"system.{r.id}.title", lang)
    out: dict = {"id": r.id, "title": title, "state": r.state, "needs": _BY_ID[r.id].needs, "problem": None, "fix": None,
                 "note": worker_note(lang) if r.id == "worker" and r.state == "ok" else None}
    if r.problem:
        values = {**r.problem.params, "what": title}
        out["problem"] = msg(f"system.{r.problem.code}.problem", lang, **values)
        out["fix"] = msg(f"system.{r.problem.code}.fix", lang, **values)
    return out


def _minutes(since: datetime, now: datetime) -> int:
    return max(0, int((now - since).total_seconds() // 60))


def worker_note(lang: Lang) -> str | None:
    """What is known of the work beside "the worker reports in", which shows only that its process runs: how many jobs
    wait and for how long, and when a job last finished. A worker that reports in while the oldest job waits for many
    minutes is not working, and the owner is told the two facts instead of "Ready"."""
    from sqlalchemy import func, select

    from justshipit.db.models import Job
    from justshipit.db.session import session_scope
    try:
        with session_scope(lease_checked=False) as s:
            now = s.scalar(select(func.now()))
            waiting, oldest = s.execute(select(func.count(), func.min(Job.created_at)).where(
                Job.status == "queued", Job.run_after <= func.now())).one()
            last = s.scalar(select(func.max(Job.finished_at)).where(Job.status.in_(("done", "failed"))))
    except Exception:  # noqa: BLE001 - the database may be the thing that is down
        return None
    lines = [msg("system.worker.note.waiting", lang, n=waiting, minutes=_minutes(oldest, now)) if waiting
             else msg("system.worker.note.idle", lang)]
    lines.append(msg("system.worker.note.last", lang, minutes=_minutes(last, now)) if last
                 else msg("system.worker.note.never", lang))
    return " ".join(lines)


def report(lang: Lang, *, results: list[Result] | None = None) -> list[dict]:
    return [describe(r, lang) for r in (snapshot() if results is None else results)]


def alerts(lang: Lang) -> list[dict]:
    """One bad alert per problem, for the home screen's list. A stopped worker already has its own alert."""
    return [{"kind": "system_problem", "severity": "bad", "project_id": None, "check": d["id"], "title": d["problem"],
             "detail": d["fix"], "at": None}
            for d in report(lang) if d["state"] == "problem" and d["id"] != "worker"]


def startup_problems(lang: Lang) -> list[dict]:
    """Run the PC checks now and log one warning for each that fails; the caller shows the same lines in a terminal."""
    found = [d for d in report(lang, results=snapshot(fresh=True)) if d["state"] == "problem" and d["id"] in STARTUP]
    for d in found:
        log.warning("%s Fix: %s", d["problem"], d["fix"])
    return found
