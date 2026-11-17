"""Shared setup for the Slack notice tests."""
import shutil
from collections.abc import Callable
from pathlib import Path

import httpx
from justshipit.db.models import AppSetting, Job
from justshipit.db.session import session_scope
from justshipit.operations import jobs as ops
from justshipit.operations import notify
from justshipit.services import app_settings, queue, secrets
from justshipit.settings import settings
from justshipit.workers import runner
from sqlalchemy import delete, select, text

TOKEN = "xoxb-1234567890-ABCDEFGHIJKLMNOP-wxyz"
NOTICE_KINDS = ("notify", "daily_briefing")


def choose(*, mode: str = "outbox", kinds: list[str] | None = None, channel: str = "#company", **notifications) -> None:
    """The owner's Slack settings: the connector's mode, the kinds they chose and the channel."""
    body = {"slack_channel": channel, **notifications, **({} if kinds is None else {"kinds": kinds})}
    with session_scope() as s:
        app_settings.save(s, "connectors", {"slack": {"mode": mode}}, actor="test")
        app_settings.save(s, "notifications", body, actor="test")


def wipe() -> None:
    with session_scope(lease_checked=False) as s:
        s.execute(delete(AppSetting))
        s.execute(text("DELETE FROM jobs WHERE kind = ANY(:kinds)"), {"kinds": list(NOTICE_KINDS)})
    for name in secrets.REGISTRY:
        secrets.clear(name)
    shutil.rmtree(settings().var_dir / "outbox" / "slack", ignore_errors=True)
    shutil.rmtree(settings().var_dir / "state", ignore_errors=True)


def ok() -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(200, json={"ok": True})


def refused(code: str) -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(200, json={"ok": False, "error": code})


def too_many(retry_after: str = "2") -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(429, headers={"Retry-After": retry_after})


def slack_answers(monkeypatch, *answers: Callable[[], httpx.Response] | Exception) -> list[httpx.Request]:
    """Slack answers each post with the next answer, the last one for every post after it. Returns the requests it got."""
    seen: list[httpx.Request] = []
    left = list(answers)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = left.pop(0) if len(left) > 1 else left[0]
        if isinstance(answer, Exception):
            raise answer
        return answer()

    monkeypatch.setattr(notify, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return seen


def outbox_files() -> list[Path]:
    directory = settings().var_dir / "outbox" / "slack"
    return sorted(directory.glob("*.json")) if directory.exists() else []


def notice_jobs(kind: str = "notify") -> list[Job]:
    with session_scope() as s:
        found = list(s.scalars(select(Job).where(Job.kind == kind).order_by(Job.id)))
        s.expunge_all()
        return found


def run_job(kind: str = "notify") -> Job:
    """One attempt the way the worker runs it: claimed, executed, finished."""
    handlers = {k: ops.HANDLERS[k] for k in queue.NOTICE_KINDS}
    with session_scope() as s:
        job = queue.claim(s, "worker-notify-test", [kind])
        assert job is not None
        jid, payload, token = job.id, dict(job.payload), job.lease_token
    runner._execute(jid, kind, payload, handlers, runner.Runs(), queue.Lease(job_id=jid, token=token))
    with session_scope() as s:
        done = s.get(Job, jid)
        s.expunge(done)
        return done


def retry_now(job_id: int) -> None:
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET run_after = now() WHERE id = :id"), {"id": job_id})
