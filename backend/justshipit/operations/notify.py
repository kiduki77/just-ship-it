"""Slack messages for the owner: a blocking decision, a failure, a new owner task, a usage wait, the daily briefing.

A notice is a `notify` job enqueued at its source in the same transaction as the change (`queue.notify`), so it
exists exactly when the change does and its key makes it one message across retries and reclaims. This module is the
sender. Slack `off` drops the notice, `outbox` writes `var/outbox/slack/<key>.json`, `live` posts it with the bot
token from `secrets`. The token goes only into the request's Authorization header; every error raised here is a
sentence in the owner's language built from the catalog and Slack's short error code, never from a response body or
an exception. The language is read once per job or test, where the settings are read.

The sender judges the notice again when it runs, not when it was queued: a notice whose request or task has been
answered since is dropped, quiet hours that began since hold it, and a Slack that refused the bot token holds every
notice (`notify_state`) until the owner fixes the token, so a wrong token costs one call, not five per notice.

The owner's test button is the one other call: in outbox mode it asks Slack's read-only `auth.test` whether the token
works, posting nothing, and only when the owner presses it. A pass is kept against that token (`notify_state`), and
Slack goes live only after one (services/sending.py).

Messages only link to the console. Approving from Slack (Socket Mode buttons) is not built.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from justshipit.contracts import notify as notices
from justshipit.contracts.notify import Notice
from justshipit.contracts.requests import RequestStatus
from justshipit.db.models import AgentRequest, DevJob, Job, OwnerTask, Project
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, msg
from justshipit.services import app_settings, notify_state, owner_tasks, queue, requests, secrets, usage
from justshipit.settings import settings

log = logging.getLogger("justshipit.notify")

POST_URL = "https://slack.com/api/chat.postMessage"
AUTH_TEST_URL = "https://slack.com/api/auth.test"
TIMEOUT_S = 10.0
TRIES = 3
MAX_INLINE_WAIT_S = 10
REFUSED_HOLD_S = 3600
UNCONFIRMED_TRIES = 2

_AUTH_CODES = {"invalid_auth", "not_authed", "token_revoked", "token_expired", "account_inactive"}
# Slack's error code -> the catalog message that says what to do about it.
_SLACK_ERRORS = {
    **dict.fromkeys(_AUTH_CODES, "notify.slack.auth"),
    "channel_not_found": "notify.slack.channel_not_found",
    "not_in_channel": "notify.slack.not_in_channel",
    "is_archived": "notify.slack.is_archived",
    "missing_scope": "notify.slack.missing_scope",
    "restricted_action": "notify.slack.no_permission",
    "no_permission": "notify.slack.no_permission",
}

# Lines other modules add to the briefing, each a function of the session and the owner's language returning a
# sentence or None. The idea box adds its count of ideas waiting for the owner to confirm a discard, so this file need
# not know that table.
BriefingLine = Callable[[Session, Lang], str | None]
BRIEFING_LINES: list[BriefingLine] = []


class NotifyError(RuntimeError):
    """A failure said in the owner's language. It never carries the token or a response body."""


class ConnectorOff(NotifyError):
    pass


class AuthError(NotifyError):
    """Slack refused the bot token, or the stored one cannot be sent. Retrying cannot help until the owner acts."""


class BadToken(AuthError):
    """The stored token is not made of what a Slack token is made of, so nothing was sent."""


class RateLimited(NotifyError):
    """Slack asked for a pause longer than the caller waits."""


class Unconfirmed(NotifyError):
    """Slack may have accepted the post and the answer was lost (a read timeout, a 5xx, a cut connection). Posting
    again could show the owner the same message twice."""


# Errors raised before a request left this machine, where a repeat cannot double a message.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.UnsupportedProtocol)


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(TIMEOUT_S), follow_redirects=False)


def _escape(text: str) -> str:
    """Slack reads &, < and > in a message as markup (links, @channel); an agent's title must not make any."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(n: Notice, console_url: str, lang: Lang) -> str:
    title, body = n.words(lang)
    lines = [f"*{msg(f'notify.label.{n.kind}', lang)}* · {_escape(n.ref)}", _escape(title)]
    if body:
        lines.append(_escape(body))
    lines.append(f"<{console_url}{n.link}|{msg('notify.view', lang)}>")
    return "\n".join(lines)


def _stem(key: str) -> str:
    return f"{re.sub(r'[^A-Za-z0-9_.-]', '_', key)[:100]}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"


def _write_outbox(stem: str, body: dict) -> Path:
    directory = settings().var_dir / "outbox" / "slack"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.json"
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _retry_after(resp: httpx.Response) -> int:
    try:
        return max(1, int(float(resp.headers.get("Retry-After", "1"))))
    except ValueError:
        return 1


def _check(resp: httpx.Response, lang: Lang) -> None:
    if resp.status_code in (401, 403):
        raise AuthError(msg("notify.slack.auth", lang))
    if resp.status_code >= 500:
        raise Unconfirmed(msg("notify.slack.server_error", lang, status=resp.status_code))
    try:
        body = resp.json()
    except ValueError:
        raise Unconfirmed(msg("notify.slack.unreadable", lang)) from None
    if isinstance(body, dict) and body.get("ok") is True:
        return
    code = re.sub(r"[^a-z_]", "", str(body.get("error", ""))[:40]) if isinstance(body, dict) else ""
    said = (msg(_SLACK_ERRORS[code], lang) if code in _SLACK_ERRORS
            else msg("notify.slack.refused", lang, code=code or msg("notify.slack.unknown_code", lang)))
    raise (AuthError if code in _AUTH_CODES else NotifyError)(said)


def _token(lang: Lang) -> str:
    """The stored bot token, or a NotifyError saying in `lang` why there is none to send."""
    try:
        token = secrets.get("slack_bot_token")
    except secrets.SecretError as exc:
        raise NotifyError(exc.say(lang)) from None
    if token is None:
        raise NotifyError(msg("notify.slack.no_token", lang))
    if secrets.format_problem("slack_bot_token", token):
        raise BadToken(msg("notify.slack.bad_token", lang))
    return token


def check_token(token: str, *, lang: Lang) -> None:
    """One `auth.test`: whether Slack takes the token, with nothing posted anywhere. Read-only, so a lost answer can be
    asked again, and a 429 is the owner's to retry, as on the test button."""
    try:
        with _client() as client:
            resp = client.post(AUTH_TEST_URL, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise NotifyError(msg("notify.slack.cant_connect", lang, error=type(exc).__name__)) from None
    if resp.status_code == 429:
        raise RateLimited(msg("notify.slack.busy_test", lang))
    try:
        _check(resp, lang)
    except Unconfirmed:
        raise NotifyError(msg("notify.slack.check_unanswered", lang)) from None


def post_message(channel: str, text: str, *, lang: Lang, patient: bool = True,
                 sleep: Callable[[float], None] = time.sleep) -> None:
    """One chat.postMessage. A 429 with a short Retry-After is waited out when `patient` (the worker) and the
    lease is confirmed after each sleep; any other 429, and every 429 when not patient (the test button, where the
    owner is waiting), is left to the caller to retry later. A failure is said in `lang`."""
    if not channel.strip():
        raise NotifyError(msg("notify.slack.no_channel", lang))
    token = _token(lang)
    payload = {"channel": channel.strip(), "text": text, "unfurl_links": False, "unfurl_media": False}
    with _client() as client:
        for attempt in range(1, TRIES + 1):
            try:
                resp = client.post(POST_URL, headers={"Authorization": f"Bearer {token}"}, json=payload)
            except _NOT_SENT as exc:
                raise NotifyError(msg("notify.slack.cant_connect", lang, error=type(exc).__name__)) from None
            except httpx.HTTPError as exc:
                raise Unconfirmed(msg("notify.slack.no_answer", lang, error=type(exc).__name__)) from None
            if resp.status_code != 429:
                _check(resp, lang)
                return
            wait = _retry_after(resp)
            if not patient:
                raise RateLimited(msg("notify.slack.busy_test", lang))
            if attempt == TRIES or wait > MAX_INLINE_WAIT_S:
                raise NotifyError(msg("notify.slack.busy", lang, s=wait))
            sleep(wait)
            queue.confirm_lease()


# The notices that say something about one row, and the state of that row in which the notice still stands.
_SUBJECT = {
    "decision_needed": (re.compile(r"REQ-(\d+)"), AgentRequest, RequestStatus.PENDING.value),
    "owner_task": (re.compile(r"OT-(\d+)"), OwnerTask, owner_tasks.OPEN),
}


def _still_stands(notice: Notice) -> bool:
    """False when the request was answered or the owner task closed since the notice was queued: the owner would be
    told about something already handled."""
    subject = _SUBJECT.get(notice.kind)
    if subject is None or not (found := subject[0].fullmatch(notice.ref)):
        return True
    with session_scope() as s:
        row = s.get(subject[1], int(found[1]))
        return row is None or row.status == subject[2]


def deliver(payload: dict, attempt: int = 1) -> str:
    """The `notify` job. Everything is judged again here, not when the notice was queued: the owner's settings may
    have changed, the request may have been answered, quiet hours may have begun, and Slack may have started to
    refuse the token. `attempt` is the job's: a post whose outcome is unknown is retried once."""
    notice = Notice.model_validate(payload)
    cfg = app_settings.effective()
    lang = cfg.general.language
    if not cfg.notifies(notice.kind):
        log.info("notice %s dropped: Slack is off or the owner did not choose this kind", notice.dedupe_key)
        return "dropped"
    if not _still_stands(notice):
        log.info("notice %s dropped: already handled", notice.dedupe_key)
        return "handled"
    if quiet := cfg.notifications.quiet_wait_s(cfg.usage.timezone):
        raise queue.Hold(quiet, msg("notify.quiet", lang))
    channel = cfg.notifications.slack_channel
    text = render(notice, cfg.notifications.console_url, lang)
    if cfg.connectors.slack.mode == "outbox":
        path = _write_outbox(_stem(notice.dedupe_key), {"key": notice.dedupe_key, "kind": notice.kind,
                                                        "ref": notice.ref, "channel": channel, "text": text})
        log.info("notice %s written to %s", notice.dedupe_key, path)
        return "outbox"
    if refused := notify_state.slack_refused():
        raise queue.Hold(REFUSED_HOLD_S, refused["why"])
    queue.confirm_lease()  # a holder whose job was reclaimed sends nothing; the new holder sends
    try:
        post_message(channel, text, lang=lang)
    except AuthError as exc:
        notify_state.mark_slack_refused(str(exc))
        raise queue.Hold(REFUSED_HOLD_S, str(exc)) from None
    except Unconfirmed as exc:
        if attempt >= UNCONFIRMED_TRIES:
            raise queue.GiveUp(msg("notify.slack.gave_up", lang, error=exc)) from None
        raise
    log.info("notice %s sent", notice.dedupe_key)
    return "sent"


WATCH_EVERY_S = 60


def announce_worker_down() -> str | None:
    """Run in the console's process, which keeps running when the worker stops: once per outage, tell the owner no
    worker has reported for a lease. Sent from here, not queued, since the queue's sender is the worker that stopped.
    Quiet hours, a refused token or a send that never left wait for the next check; one that may have gone out
    counts as told."""
    with session_scope(lease_checked=False) as s:
        seen, alive = queue.last_worker_seen(s)
    if alive or seen is None or notify_state.worker_down_announced() == seen:
        return None
    notice = notices.worker_down(seen, app_settings.effective().usage.timezone)
    try:
        sent = deliver(notice.model_dump(mode="json"), attempt=UNCONFIRMED_TRIES)
    except queue.Hold:
        return None
    except queue.GiveUp:
        sent = "unconfirmed"
    except NotifyError as exc:
        log.warning("worker-down notice not sent, trying again in %ss: %s", WATCH_EVERY_S, exc)
        return None
    notify_state.mark_worker_down_announced(seen)
    log.warning("no worker has reported since %s; the owner was told (%s)", seen, sent)
    return sent


def watch_worker() -> None:
    while True:
        time.sleep(WATCH_EVERY_S)
        try:
            announce_worker_down()
        except Exception as exc:  # noqa: BLE001 - a database outage must not end the watch
            log.error("checking for a stopped worker failed: %s", exc)


def reconnected() -> int:
    """The owner saved a new token or the test message went through: notices held for the refusal go out now."""
    if not notify_state.clear_slack_refused():
        return 0
    with session_scope() as s:
        return queue.release_notices(s)


def send_test() -> dict[str, str]:
    """The owner's connection test, for Settings. Live posts one test message to the channel. Outbox asks Slack's
    `auth.test` about the token, posts nothing, and keeps the test message in the outbox. Either way a pass is kept
    against the token and the channel. Raises ConnectorOff when Slack is off and NotifyError when the token is missing or Slack
    refuses; the answer never carries the token. It does not wait out a 429: the owner is waiting."""
    cfg = app_settings.effective()
    lang = cfg.general.language
    mode, channel = cfg.connectors.slack.mode, cfg.notifications.slack_channel
    if mode == "off":
        raise ConnectorOff(msg("notify.test.off", lang))
    text = (f"*{msg('notify.test.title', lang)}*\n{msg('notify.test.body', lang)}\n"
            f"<{cfg.notifications.console_url}/settings|{msg('notify.view', lang)}>")
    try:
        token = _token(lang)
        if mode == "outbox":
            check_token(token, lang=lang)
        else:
            post_message(channel, text, lang=lang, patient=False)
    except AuthError as exc:
        notify_state.mark_slack_refused(str(exc))
        raise
    notify_state.mark_slack_tested(token, channel)
    reconnected()
    if mode == "outbox":
        _write_outbox("test", {"key": "test", "kind": "test", "channel": channel, "text": text})
        return {"mode": mode, "message": msg("notify.test.outbox", lang)}
    return {"mode": mode, "message": msg("notify.test.sent", lang, channel=channel)}


# ------------------------------------------------------------------ the daily briefing

def add_briefing_line(fn: BriefingLine) -> None:
    if fn not in BRIEFING_LINES:
        BRIEFING_LINES.append(fn)


def _count(s: Session, query) -> int:
    return int(s.scalar(query) or 0)


def _news(query, column, window: notify_state.BriefingWindow):
    return query if window.everything else query.where(column > window.since)


def _failed_dev_jobs(s: Session, window: notify_state.BriefingWindow) -> int:
    newer = aliased(DevJob)
    superseded = select(newer.id).where(newer.spec_id == DevJob.spec_id, newer.attempt > DevJob.attempt).exists()
    found = select(func.count()).select_from(DevJob).where(DevJob.status == "FAILED", ~superseded)
    return _count(s, _news(found, DevJob.updated_at, window))


def _usage_line(s: Session, cfg: app_settings.AppSettings, now: datetime, lang: Lang) -> str:
    day, week = usage.spent(s, usage.windows(now, cfg.usage.timezone))

    def part(which: str, used: float, cap: float | None) -> str:
        if not cap:
            return msg(f"notify.briefing.usage_{which}_uncapped", lang, used=f"{used:.2f}")
        return msg(f"notify.briefing.usage_{which}", lang, used=f"{used:.2f}", cap=f"{cap:g}",
                   pct=f"{used / cap * 100:.0f}")
    return msg("notify.briefing.usage", lang, today=part("today", day, cfg.usage.day_usd_eq),
               week=part("week", week, cfg.usage.week_usd_eq))


def briefing_text(s: Session, now: datetime | None = None, window: notify_state.BriefingWindow | None = None, *,
                  lang: Lang) -> str | None:
    """What the owner should know today, or None when nothing needs them. Failures and requests and tasks count only
    when they are new or changed since the last briefing went out (`window`), so one old failure is not repeated
    every morning; once a week the window is everything, and what has stayed open is listed again. The usage numbers
    ride along with something to say and do not make a briefing on their own."""
    now = now or datetime.now(timezone.utc)
    window = window or notify_state.briefing_window(now)
    cfg = app_settings.effective()
    lines: list[str] = []
    waiting = [r for r in requests.pending(s) if window.fresh(r.created_at)]
    if waiting:
        stuck = [r for r in waiting if r.blocking]
        lines.append(msg("notify.briefing.waiting", lang, n=len(waiting))
                     + (msg("notify.briefing.stuck", lang, n=len(stuck)) if stuck else ""))
        lines += [f"· {requests.display_id(r.id)} {r.title}" for r in (stuck or waiting)[:3]]
    failed = select(func.count()).select_from(Project).where(Project.status == "FAILED")
    failed_projects = _count(s, _news(failed, Project.updated_at, window))
    failed_dev = _failed_dev_jobs(s, window)
    if failed_projects or failed_dev:
        lines.append(msg("notify.briefing.failed", lang, projects=failed_projects, builds=failed_dev))
    open_tasks = select(func.count()).select_from(OwnerTask).where(OwnerTask.status == "OPEN")
    if n := _count(s, _news(open_tasks, OwnerTask.created_at, window)):
        lines.append(msg("notify.briefing.todos", lang, n=n))
    if waits := _count(s, select(func.count()).select_from(Job).where(
            Job.status == "queued", Job.wait_reason.is_not(None), Job.run_after > func.now())):
        lines.append(msg("notify.briefing.usage_waits", lang, n=waits))
    lines += [line for fn in BRIEFING_LINES if (line := fn(s, lang))]
    if not lines:
        return None
    if window.everything and window.since:
        lines.insert(0, msg("notify.briefing.weekly", lang))
    return "\n".join([*lines, _usage_line(s, cfg, now, lang)])


def send_briefing(date: str, attempt: int = 1) -> str:
    now = datetime.now(timezone.utc)
    window = notify_state.briefing_window(now)
    with session_scope() as s:
        text = briefing_text(s, now, window, lang=app_settings.language())
    if text is None:
        log.info("briefing %s skipped: nothing to say", date)
        return "empty"
    sent = deliver(notices.daily_briefing(date, text).model_dump(mode="json"), attempt)
    if sent in ("sent", "outbox"):
        notify_state.briefing_sent(now, window)
    return sent


def schedule_briefing(s: Session, now: datetime | None = None) -> Job | None:
    """From the scheduler's tick: once the owner's briefing time has passed in their timezone, queue that day's
    briefing, one job per local date. A worker that was down at the time sends it when it is back. Held through
    quiet hours."""
    now = now or datetime.now(timezone.utc)
    cfg = app_settings.effective()
    if not cfg.notifies("daily_briefing"):
        return None
    local = now.astimezone(ZoneInfo(cfg.usage.timezone))
    if f"{local:%H:%M}" < cfg.notifications.briefing_time:
        return None
    date = local.date().isoformat()
    return queue.enqueue(s, "daily_briefing", {"date": date}, idempotency_key=f"notify:daily_briefing:{date}:run",
                         delay_s=cfg.notifications.quiet_wait_s(cfg.usage.timezone, now),
                         max_attempts=queue.NOTIFY_ATTEMPTS)
