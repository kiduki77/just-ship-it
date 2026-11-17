"""A notice for the owner: one event, said once. Each builder fixes the event's key, so the same event enqueued
twice (a retry, a reclaim, a second code path) is one message.

A notice holds the facts, not the sentence: its fixed words are a catalog message (`Said`) put into words when the
notice is sent, in the owner's language then. Text an agent or the owner wrote (a request's title, a failure's
reason) is a plain string and stays as written. A notice queued before this was a plain string too, and still reads."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from justshipit.i18n import Lang
from justshipit.i18n.said import Said, Text, say

NotifyKind = Literal["decision_needed", "failed", "usage_wait", "owner_task", "daily_briefing"]

TITLE_MAX = 120
BODY_MAX = 1500
_PATH = re.compile(r"^/[^\s<>|]*$")


def clip(text: str, n: int, *, lines: bool = False) -> str:
    """The text with its whitespace squeezed, cut to `n` characters. `lines` keeps the line breaks."""
    if lines:
        text = "\n".join(" ".join(line.split()) for line in text.strip().splitlines())
    else:
        text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _said(key: str, **values: Text) -> Said:
    return Said(key=key, vars={k: clip(v, BODY_MAX, lines=True) if isinstance(v, str) else v
                               for k, v in values.items()})


class Notice(BaseModel):
    """`dedupe_key` is `notify:<kind>:<ref>:<state>`, the queue's idempotency key. `link` is a console path; the
    console's address is added when the message is sent, so a changed address applies to a notice still waiting."""
    model_config = ConfigDict(frozen=True)

    kind: NotifyKind
    ref: str
    title: Text
    body: Text = ""
    link: str = "/"
    dedupe_key: str
    # The record it is about, in the subject ids a job payload names (scope.owner_of), so its queue job belongs to that
    # record's business. Not part of the message, so the payload leaves it out.
    about: dict[str, int] = Field(default_factory=dict, exclude=True)

    @field_validator("link")
    @classmethod
    def _console_path(cls, v: str) -> str:
        if not _PATH.match(v):
            raise ValueError("link must be a path on the console")
        return v

    def words(self, lang: Lang) -> tuple[str, str]:
        """The title and the body in `lang`, cut to fit a Slack message."""
        return clip(say(self.title, lang), TITLE_MAX), clip(say(self.body, lang), BODY_MAX, lines=True)


def notice(kind: NotifyKind, ref: str, state: str, title: Text, body: Text = "", link: str = "/", *,
           about: dict[str, int] | None = None) -> Notice:
    if isinstance(title, str):
        title = clip(title, TITLE_MAX)
    if isinstance(body, str):
        body = clip(body, BODY_MAX, lines=True)
    return Notice(kind=kind, ref=ref, title=title, body=body, link=link, dedupe_key=f"notify:{kind}:{ref}:{state}",
                  about=about or {})


def decision_needed(request_id: int, title: str, summary: str) -> Notice:
    return notice("decision_needed", f"REQ-{request_id}", "pending", title, summary, f"/inbox?req={request_id}",
                  about={"request_id": request_id})


def decision_expiring(request_id: int, title: str, at: datetime, tz: str, default: str | None) -> Notice:
    """A card that expires within a day: what its rule does at the deadline if nobody answers, said once. `default` is
    the option a question goes on with, None for a card that closes."""
    body = (_said("notify.expiring.default", when=when(at, tz), option=default) if default is not None
            else _said("notify.expiring.close", when=when(at, tz)))
    return notice("decision_needed", f"REQ-{request_id}", "expiring", _said("notify.expiring.title", title=title), body,
                  f"/inbox?req={request_id}", about={"request_id": request_id})


def run_failed(project_id: int, name: str, why: str, job_id: int) -> Notice:
    return notice("failed", f"P-{project_id}", f"job{job_id}", _said("notify.run_failed", name=name), why,
                  f"/projects/{project_id}", about={"project_id": project_id})


def dev_job_failed(dev_job_id: int, spec_key: str, why: str, *, merge: bool = False) -> Notice:
    """A merge that failed is its own event: its key differs from the development failure that came before it."""
    key, state = ("notify.merge_failed", "merge") if merge else ("notify.dev_failed", "failed")
    return notice("failed", f"J-{dev_job_id}", state, _said(key, spec=spec_key), why, f"/engineering?job={dev_job_id}",
                  about={"dev_job_id": dev_job_id})


def when(at: datetime, tz: str) -> Said:
    """A moment as the owner reads it in a notice, in the owner's zone: the date too, since it may be read a day late."""
    at = at.astimezone(ZoneInfo(tz))
    return Said(key="notify.when", vars={"month": Said(key=f"notify.month.{at.month}"), "day": str(at.day),
                                         "time": f"{at:%H:%M}", "zone": at.tzname() or tz})


def usage_wait(reason: str, provider: str | None, until: datetime, tz: str, detail: str) -> Notice:
    """One wait is one reason, one provider and one hour of resuming, in UTC so that two zones never split it.
    `reason` is a `usage.reason.*` code; `tz` is the zone the owner reads the time in."""
    window = until.astimezone(timezone.utc).strftime("%Y%m%dT%H")
    state = "-".join(part for part in (reason, provider, window) if part)
    title = _said("notify.usage_wait.title", reason=Said(key=f"usage.reason.{reason}"))
    return notice("usage_wait", "usage", state, title, _said("notify.usage_wait.body", until=when(until, tz), detail=detail),
                  "/audit/costs")


def worker_down(seen_at: datetime, tz: str) -> Notice:
    """One outage is one message: it is named by the worker's last report."""
    return notice("failed", "worker", f"down-{seen_at.astimezone(timezone.utc):%Y%m%dT%H%M%S}",
                  _said("notify.worker_down.title"), _said("notify.worker_down.body", seen=when(seen_at, tz)), "/")


def owner_task(task_id: int, title: str, kind: str) -> Notice:
    """`kind` is the to-do's kind code (`owner_task.kind.*`)."""
    return notice("owner_task", f"OT-{task_id}", "open", title,
                  _said("notify.owner_task.body", kind=Said(key=f"owner_task.kind.{kind}")), "/",
                  about={"owner_task_id": task_id})


def stage_gate(stage_id: int, round_: int, title: str) -> Notice:
    """A stage ended and waits at its gate: a decision, so it goes out as one."""
    return notice("decision_needed", f"STAGE-{stage_id}", f"gate{round_}", _said("notify.stage_gate", title=title),
                  _said("notify.stage_gate.body"), "/", about={"stage_id": stage_id})


def cycle_proposal(cycle_id: int, version: int) -> Notice:
    return notice("decision_needed", f"CYCLE-{cycle_id}", f"v{version}", _said("notify.cycle_proposal"),
                  _said("notify.cycle_proposal.body"), "/", about={"cycle_id": cycle_id})


def daily_briefing(date: str, text: str) -> Notice:
    """`text` is already in the owner's language: the briefing is written by the job that sends it."""
    return notice("daily_briefing", date, "sent", _said("notify.briefing.title"), text, "/")
