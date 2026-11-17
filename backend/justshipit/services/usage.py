"""Usage caps, and the one signal for "wait instead of fail".

The owner sets a day and a week cap in Settings, in list-price USD equivalents like every other cost here. A call
that would pass the cap, or one the subscription itself refuses, raises `MustWait` with the moment it may run
again. `MustWait` is a BaseException like `queue.LeaseLost`, so no `except Exception` between the call and the
worker records a task, a project or a dev job as failed: the job queue turns it into a deferral.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from justshipit.db.models import LLMCallRecord
from justshipit.i18n import DEFAULT, Lang, msg
from justshipit.settings import company_config, limits

if TYPE_CHECKING:
    from justshipit.services.app_settings import UsageSettings

Reason = Literal["usage_cap_day", "usage_cap_week", "subscription_limit", "github_rate_limit"]


def reason_label(reason: str, lang: Lang) -> str:
    """What the work waits on, as the owner reads it ("daily usage cap")."""
    return msg(f"usage.reason.{reason}", lang)



class MustWait(BaseException):
    """Not an error: the work is fine and resumes at `until`. `detail` is the sentence the owner reads. `provider`
    is set when one provider's subscription is what ran out."""

    def __init__(self, until: datetime, reason: Reason, detail: str, provider: str | None = None) -> None:
        super().__init__(detail)
        self.until, self.reason, self.detail, self.provider = until, reason, detail, provider


@dataclass(frozen=True)
class Purpose:
    name: str  # its catalog key: how the owner reads one call of this purpose is usage.purpose.<name>
    reserve_usd: float  # held against the caps and the budget until the call settles to its real cost
    owner_facing: bool  # may use the share of a cap that background work leaves free

    def label(self, lang: Lang) -> str:
        return msg(f"usage.purpose.{self.name}", lang)


# The only place that says who a call serves. An unlisted purpose is background.
# plan covers the kickoff plan, the CEO's review of results and a realignment after an owner directive.
# kickoff is a turn of the owner's conversation with the CEO, which the owner is waiting on.
# idea is the CEO reading an idea the owner just dropped in, which the owner expects an answer to.
# chat is an agent answering a line the owner wrote in team chat.
# idea_review is one lens of the panel that warns about a new idea while the owner is in the kickoff.
# customer_profile is the CEO writing the customer description from an answer the owner just gave or a plan the owner
# just approved.
# cycle is the CEO proposing, revising, summarizing or looking back on a cycle: each is a step the owner waits on.
# kit is the marketing agent writing the First 10 kit the owner just asked for and is waiting to copy from.
PURPOSES: dict[str, Purpose] = {p.name: p for p in (
    Purpose("plan", 1.0, True),
    Purpose("kickoff", 1.0, True),
    Purpose("idea", 1.0, True),
    Purpose("chat", 0.8, True),
    Purpose("idea_review", 1.2, True),
    Purpose("decision", 0.8, True),
    Purpose("meeting", 0.5, True),
    Purpose("report", 1.5, True),
    Purpose("retro", 1.5, True),
    Purpose("cycle", 1.5, True),
    Purpose("work", 1.5, False),
    Purpose("review", 0.8, False),
    Purpose("audit", 0.8, False),
    Purpose("product_plan", 1.0, False),
    Purpose("spec", 1.2, False),
    Purpose("dev", 4.0, False),
    Purpose("skill", 1.0, False),
    Purpose("acceptance", 0.6, False),
    Purpose("analysis", 0.8, False),
    Purpose("scheduled", 1.0, False),
    Purpose("people", 1.5, False),
    Purpose("kit", 1.0, True),
    Purpose("customer_profile", 0.6, True),
    Purpose("product_report", 3.0, True),
)}
_UNLISTED = Purpose("other", 1.0, False)


def purpose(name: str) -> Purpose:
    return PURPOSES.get(name, _UNLISTED)


@dataclass(frozen=True)
class Step:
    """A job step that makes several model calls and cannot be resumed halfway, so it waits for headroom for all of
    them before its first call. `fixed` calls happen once, `per_unit` calls repeat for each unit, both by purpose."""
    name: str  # its catalog key, usage.step.<name>
    purpose: str  # whose share of the caps the whole step uses
    fixed: Mapping[str, int]
    per_unit: Mapping[str, int]

    def label(self, lang: Lang) -> str:
        return msg(f"usage.step.{self.name}", lang)

    def usd(self, units: int) -> float:
        return sum(purpose(p).reserve_usd * (self.fixed.get(p, 0) + self.per_unit.get(p, 0) * units)
                   for p in {*self.fixed, *self.per_unit})


MEETING_MAX_PARTICIPANTS = 4

# Units: meeting = participants x (rebuttal rounds + 1), analysis = max_steps, skill_build = eval_cases.
STEPS: dict[str, Step] = {
    "meeting": Step("meeting", "meeting", {"decision": 1}, {"meeting": 1}),
    "analysis": Step("analysis", "analysis", {"work": 1}, {"analysis": 1}),
    "skill_build": Step("skill_build", "skill", {"skill": 2}, {"skill": 2}),
    # One research run of up to 50 turns with web search. A turn re-reads the growing context, so the run costs several
    # times one reserved call; four is an estimate to be replaced by what the ledger shows after real runs.
    "people_find": Step("people_find", "people", {"people": 4}, {}),
}


def worst_units() -> dict[str, int]:
    """The most units each step can have under the current configuration."""
    return {"meeting": MEETING_MAX_PARTICIPANTS * (int(limits()["meeting_rebuttal_rounds"]) + 1),
            "analysis": int(company_config().get("analysis", {}).get("max_steps", 6)),
            "skill_build": int(company_config().get("skills", {}).get("eval_cases", 3)), "people_find": 1}


@dataclass(frozen=True)
class Need:
    what: str
    usd: float
    pct: int  # the share of a cap this work may use


def once(p: Purpose, lang: Lang) -> str:
    """One call of a purpose, as the owner reads the work that waits ("one plan call")."""
    return msg("usage.once", lang, what=p.label(lang))


def class_needs(lang: Lang = DEFAULT) -> dict[bool, tuple[str, float]]:
    """For work the owner waits on (True) and for background work (False): the call or whole step that reserves the most."""
    units = worst_units()
    found: dict[bool, tuple[str, float]] = {}
    candidates = [(once(p, lang), p.reserve_usd, p.owner_facing) for p in PURPOSES.values()]
    candidates += [(s.label(lang), s.usd(units[name]), purpose(s.purpose).owner_facing) for name, s in STEPS.items()]
    for what, usd, owner_facing in candidates:
        if owner_facing not in found or usd > found[owner_facing][1]:
            found[owner_facing] = (what, usd)
    return found


def largest_need(reserve_pct: int, lang: Lang = DEFAULT) -> Need:
    """The work that needs the largest cap to be admitted at all: one call of any purpose, or one whole step."""
    needs = [Need(what, usd, 100 if owner_facing else 100 - reserve_pct)
             for owner_facing, (what, usd) in class_needs(lang).items()]
    return max(needs, key=lambda n: n.usd / n.pct)


def required_cap(need: Need) -> float:
    return math.ceil(need.usd * 100 / need.pct * 100 - 1e-9) / 100


def too_small(cfg: UsageSettings, *, lang: Lang) -> list[str]:
    """One sentence per cap that can never admit the largest step, naming the work and the minimum."""
    need = largest_need(cfg.owner_reserve_pct, lang)
    kept = msg("usage.too_small.kept", lang, pct=cfg.owner_reserve_pct) if need.pct < 100 else ""
    return [msg("usage.too_small", lang, what=need.what, usd=f"{need.usd:g}", kept=kept,
                cap=msg(f"usage.cap.{window}", lang), min=f"{required_cap(need):g}")
            for window, cap in (("day", cfg.day_usd_eq), ("week", cfg.week_usd_eq))
            if cap is not None and cap * need.pct / 100 < need.usd]


@dataclass(frozen=True)
class Windows:
    day_start: datetime
    next_day: datetime
    week_start: datetime
    next_week: datetime


def windows(now: datetime, tz: str) -> Windows:
    """The day starts at local midnight and the week on Monday 00:00, both in the owner's timezone."""
    zone = ZoneInfo(tz)
    today = now.astimezone(zone).date()
    monday = today - timedelta(days=today.weekday())

    def midnight(d):
        return datetime.combine(d, time.min, tzinfo=zone)
    return Windows(midnight(today), midnight(today + timedelta(days=1)), midnight(monday),
                   midnight(monday + timedelta(days=7)))


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def say_at(at: datetime, lang: Lang | None = None) -> str:
    """"Nov 4, 00:00 (KST)" or "11월 4일 00:00 (KST)" for a moment already in its zone: the date, since the owner
    may read it a day early, and the zone. Without `lang`, in the language the owner reads now."""
    if lang is None:
        from justshipit.services import app_settings  # app_settings imports this module
        lang = app_settings.language()
    return msg("usage.at", lang, month=_MONTHS[at.month - 1] if lang == "en" else at.month, day=at.day,
               time=f"{at:%H:%M}", zone=at.tzname())


def local_time(at: datetime, tz: str, lang: Lang | None = None) -> str:
    """A moment in the owner's zone `tz`, as `say_at` words it."""
    return say_at(at.astimezone(ZoneInfo(tz)), lang)


def spent(s: Session, w: Windows) -> tuple[float, float]:
    """Ledger cost since the day and the week began, in one scan of the week served by ix_llm_calls_created_at."""
    day = func.coalesce(func.sum(LLMCallRecord.cost_usd).filter(LLMCallRecord.created_at >= w.day_start), 0.0)
    week = func.coalesce(func.sum(LLMCallRecord.cost_usd), 0.0)
    d, wk = s.execute(select(day, week).where(LLMCallRecord.created_at >= w.week_start)).one()
    return float(d), float(wk)


def limit_pct(cfg: UsageSettings, name: str) -> int:
    return 100 if purpose(name).owner_facing else 100 - cfg.owner_reserve_pct


def check(s: Session, cfg: UsageSettings, name: str, live_usd: float, now: datetime | None = None, *,
          amount: float | None = None, step: str | None = None, lang: Lang = DEFAULT) -> None:
    """Raise MustWait when this purpose's reservation (`amount`, the whole `step`'s when given), on top of the
    ledger and the live reservations, would pass a cap. A cap that is None is unlimited. When both windows are over,
    wait for the later one. The owner's sentence is in `lang`."""
    if cfg.day_usd_eq is None and cfg.week_usd_eq is None:
        return
    p, pct = purpose(name), limit_pct(cfg, name)
    amount = p.reserve_usd if amount is None else amount
    what = STEPS[step].label(lang) if step else once(p, lang)
    w = windows(now or datetime.now(timezone.utc), cfg.timezone)
    day, week = spent(s, w)
    hits = [(reason, cap, used, until)
            for reason, cap, used, until in (("usage_cap_day", cfg.day_usd_eq, day, w.next_day),
                                             ("usage_cap_week", cfg.week_usd_eq, week, w.next_week))
            if cap is not None and used + live_usd + amount > cap * pct / 100]
    if not hits:
        return
    reason, cap, used, until = max(hits, key=lambda h: h[3])
    allowed = cap * pct / 100
    share = "" if p.owner_facing else msg("usage.wait.share", lang, reserve=cfg.owner_reserve_pct, pct=pct,
                                          allowed=f"{allowed:g}")
    at = local_time(until, cfg.timezone, lang)
    if amount > allowed:
        fix = msg("usage.wait.never", lang, min=f"{required_cap(Need(what, amount, pct)):g}")
        ends = msg("usage.wait.recheck", lang, at=at)
    else:
        fix, ends = "", msg("usage.wait.until", lang, at=at)
    raise MustWait(until, reason, msg("usage.wait", lang, reason=reason_label(reason, lang), cap=f"{cap:g}",
                                      used=f"{used + live_usd:.2f}", what=what, amount=f"{amount:g}",
                                      share=share, fix=fix, ends=ends))


def window_view(cap: float | None, used: float, live: float, start: datetime, end: datetime,
                reserve_pct: int) -> dict:
    return {"cap_usd_eq": cap, "used_usd_eq": round(used, 4), "reserved_usd_eq": round(live, 4),
            "used_pct": round((used + live) / cap * 100, 1) if cap else None,
            "background_limit_pct": 100 - reserve_pct, "starts_at": start.isoformat(), "resets_at": end.isoformat()}
