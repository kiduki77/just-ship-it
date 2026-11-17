"""What only the owner can do today: one list for the home screen, built from the services that already know.

Every source that waits on the owner (a blocking request, a decision card, an open owner task, a kickoff conversation, a
brief to approve, a cycle proposal to read or a stage that waits at its gate, an idea the CEO proposed to discard, a
legacy bet, a broken Slack connection) becomes an `Item`. The
list is sorted by `Tier` first (what stops the company, then what is overdue, then what is due today, then the rest),
then by the shorter estimated time. The home shows the top three and the total; the to-do page asks for all.

The yesterday lines and the number are counts over rows the server already has. No model is called. Every line the
server writes is in the owner's language (`lang`); titles stay as the company stored them.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import IntEnum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from justshipit.contracts import owner_text
from justshipit.contracts.requests import RequestStatus
from justshipit.db.models import (
    ActivityEvent,
    AgentRequest,
    Bet,
    Business,
    KickoffSession,
    MetricPoint,
    OwnerIdea,
    OwnerTask,
    Project,
)
from justshipit.i18n import Lang, msg
from justshipit.services import (
    app_settings,
    completion,
    cycle,
    kit,
    notify_state,
    owner_tasks,
    owner_time,
    people_link,
    queue,
    requests,
    roadmap,
    stops,
    todo_steps,
    usage,
    your_turn,
)
from justshipit.services.control import NotFound
from justshipit.services.profile import store as profile_store

HOME_LIMIT = 3


class Tier(IntEnum):
    BLOCKING = 0
    OVERDUE = 1
    TODAY = 2
    OTHER = 3


SOURCES = ("decision", "kickoff", "brief", "cycle", "idea", "bet", "alert", "profile", "kit")


def label(kind: str, lang: Lang) -> str:
    """What the owner sees on the item's badge: the source's name, or an owner task's kind."""
    return msg(f"today.label.{kind}" if kind in SOURCES else f"owner_task.kind.{kind}", lang)


# A guess at how long the work takes, in minutes. The guess is a constant per kind: nothing records how long the owner
# really took. An owner task with steps (todo_steps) takes the minutes of the steps not ticked yet.
ESTIMATE_MIN: dict[str, int] = {"decision": 3, "kickoff": 10, "brief": 5, "cycle": 5, "idea": 1, "bet": 5, "alert": 3, "post": 10,
                                "publish": 10, "spend": 5, "other": 10, "account": 10, "contact": 10, "onboard": 30,
                                "follow_up": 5, "referral": 5, "participate": 10, "check": 15, "profile": 2, "kit": 5}

@dataclass(frozen=True)
class Item:
    kind: str
    ref: str
    title: str
    reason: str
    tier: Tier
    action: str
    link: str
    due_at: datetime | None = None
    shown: str | None = None   # the kind an owner task is labelled as (`owner_tasks.shown_kind`), when not its own
    steps: tuple[int, int, int] | None = None   # an owner task's steps: ticked, all, and the minutes of those left
    turn: your_turn.TurnCard | None = None      # a blocking decision: what answering it frees, which sets its place

    @property
    def estimate_min(self) -> int:
        return self.steps[2] if self.steps else ESTIMATE_MIN[self.kind]

    def sort_key(self) -> tuple[int, int, tuple]:
        """Inside the blocking tier a decision goes by what it frees (`your_turn.sort_key`, the order the Decisions page
        shares) and before any other blocking turn; everything else keeps the shorter estimate first."""
        if self.turn is not None and self.tier == Tier.BLOCKING:
            return self.tier, 0, your_turn.sort_key(self.turn)
        return self.tier, 1, (self.estimate_min, self.due_at or datetime.max.replace(tzinfo=UTC))

    def view(self, lang: Lang, day: int = 0) -> dict[str, Any]:
        meta = {"estimate_min": self.estimate_min, "due_at": self.due_at, "reason": self.reason,
                **({"hold": {"scope": self.turn.effective, "frees": self.turn.frees, "behind": self.turn.behind,
                             "nothing_live": self.turn.nothing_live}} if self.turn else {}),
                **({"steps": {"done": self.steps[0], "total": self.steps[1]}} if self.steps else {})}
        return {"kind": self.kind, "label": label(self.shown or self.kind, lang), "ref": self.ref, "title": self.title,
                "meta": meta,
                "tier": self.tier.name.lower(), "action": self.action, "link": self.link, "day": day}


def _tier_by_date(due: datetime | None, day: date, zone: ZoneInfo) -> Tier:
    if due is None:
        return Tier.OTHER
    local = due.astimezone(zone).date()
    return Tier.OVERDUE if local < day else Tier.TODAY if local == day else Tier.OTHER


# ------------------------------------------------------------------ the sources

def _kickoff(s: Session, bid: int, lang: Lang) -> list[Item]:
    sess = s.scalar(select(KickoffSession).where(KickoffSession.business_id == bid,
                                                 KickoffSession.status.in_(("WAITING_OWNER", "PAUSED"))))
    if sess is None:
        return []
    reason = msg("today.kickoff.waiting" if sess.status == "WAITING_OWNER" else "today.kickoff.paused", lang)
    return [Item("kickoff", f"KICKOFF-{sess.id}", msg("today.kickoff.title", lang), reason, Tier.BLOCKING, "open",
                 f"/kickoff/{sess.id}")]


def _brief(s: Session, bid: int, lang: Lang) -> list[Item]:
    live = s.scalar(select(KickoffSession.id).where(KickoffSession.business_id == bid,
                                                    KickoffSession.status.in_(("OPEN", "WAITING_OWNER", "PAUSED"))))
    last = s.scalar(select(KickoffSession).where(KickoffSession.business_id == bid)
                    .order_by(KickoffSession.id.desc()).limit(1))
    draft = roadmap.draft_brief(s, bid)
    if live or draft is None or (last is not None and last.status == "ABANDONED"):
        return []
    which = "first" if roadmap.approved_brief(s, bid) is None else "changed"
    return [Item("brief", f"BRIEF-{draft.id}", msg(f"today.brief.{which}.title", lang),
                 msg(f"today.brief.{which}.reason", lang), Tier.BLOCKING, "open",
                 f"/kickoff/{last.id}" if last else f"/business?b={bid}")]


def _cycle(s: Session, bid: int, lang: Lang) -> list[Item]:
    """The company waits for the owner when the CEO's proposal is ready to read and start, or a stage finished and sits at
    its gate. A stage the owner stopped, and one still running, wait for no one."""
    c = cycle.open_cycle_of(s, bid)
    if c is None:
        return []
    link = f"/?b={bid}"   # the dashboard: the board with the gate card or the proposal lives there
    gate = next((st for st in cycle.stages_of(s, c.id) if st.status == "awaiting_owner"), None)
    if gate is not None:
        return [Item("cycle", f"STAGE-{gate.id}", msg("today.cycle.gate.title", lang, stage=owner_text.title(gate.title, 60)),
                     msg("today.cycle.gate.reason", lang), Tier.BLOCKING, "open", link)]
    ready, latest = cycle.proposal(s, c.id, status=("ready",)), cycle.proposal(s, c.id)
    if c.state == "draft" and ready is not None and latest.status != "drafting":
        return [Item("cycle", f"CYCLE-{c.id}", msg("today.cycle.proposal.title", lang),
                     msg("today.cycle.proposal.reason", lang), Tier.BLOCKING, "open", link)]
    return []


def _requests(s: Session, bid: int, day: date, zone: ZoneInfo, lang: Lang) -> list[Item]:
    out = []
    turns = your_turn.order_of(s, bid)
    for r in requests.pending(s, business_id=bid):
        tier = Tier.BLOCKING if r.blocking else _tier_by_date(r.expires_at, day, zone)
        turn = turns.get(r.id)   # None for a card filed since the order was read: it takes its place on the next read
        why = ("moot" if turn and turn.nothing_live else "blocking") if r.blocking else "typed" if requests.typed_confirm(r) else "default"
        out.append(Item("decision", requests.display_id(r.id), owner_text.title(r.title, 120), msg(f"today.decision.{why}", lang), tier,
                        "decide", f"/inbox?req={r.id}", r.expires_at, turn=turn))
    return out


def _owner_tasks(s: Session, bid: int, day: date, zone: ZoneInfo, lang: Lang) -> list[Item]:
    out = []
    for t in s.scalars(select(OwnerTask).where(OwnerTask.status == owner_tasks.OPEN,
                                               or_(OwnerTask.business_id == bid, OwnerTask.business_id.is_(None)))
                       .order_by(OwnerTask.id)):
        tier, shown = _tier_by_date(t.due_at, day, zone), owner_tasks.shown_kind(t)
        steps = todo_steps.of(t.card, t.kind, ESTIMATE_MIN[t.kind], lang)
        progress = (len(todo_steps.done_keys(t.card) & {x.key for x in steps}), len(steps),
                    todo_steps.next_min(steps, t.card)) if steps else None
        out.append(Item(t.kind, owner_tasks.display_id(t.id), owner_text.title(owner_tasks.readable_title(t.title, lang), 120),
                        msg(f"today.task.{shown}", lang), tier, "open",
                        f"/my-tasks?ot={t.id}", t.due_at, shown, progress))
    return out


def _ideas(s: Session, bid: int, lang: Lang) -> list[Item]:
    return [Item("idea", f"IDEA-{i.id}", owner_text.title(i.text.strip().splitlines()[0], 60),
                 msg("today.idea.discard_because", lang, reason=owner_text.title(i.reason, 60)) if i.reason
                 else msg("today.idea.discard", lang), Tier.OTHER, "open", f"/business?b={bid}")
            for i in s.scalars(select(OwnerIdea).where(OwnerIdea.business_id == bid, OwnerIdea.status == "TRIAGED",
                                                       OwnerIdea.disposition == "discard_proposed")
                               .order_by(OwnerIdea.id))]


def _bets(s: Session, bid: int, lang: Lang) -> list[Item]:
    return [Item("bet", f"BET-{b.id}", owner_text.title(b.title, 80), msg("today.bet.legacy", lang),
                 Tier.OTHER, "open", f"/business?b={bid}")
            for b in s.scalars(select(Bet).where(Bet.business_id == bid).order_by(Bet.id)) if roadmap.is_legacy_bet(b)]


def _profile(s: Session, bid: int, lang: Lang) -> list[Item]:
    """The company wrote a customer description and the owner has not looked at it: they act on it once, by saying a
    line is right, not right or changed, and it goes away. It stops no work, so it is never blocking."""
    if not profile_store.live_rows(s, bid) or profile_store.owner_acted(s, bid):
        return []
    return [Item("profile", f"PROFILE-{bid}", msg("today.profile.title", lang), msg("today.profile.reason", lang),
                 Tier.OTHER, "open", f"/business?b={bid}#customers")]


def _kit(s: Session, bid: int, now: datetime, lang: Lang) -> list[Item]:
    """People to reach today and no to-do about any of them: one line that opens the First 10 kit, where the owner sees who
    and has words to copy. Never one item per person; it is gone as soon as nobody is due or a person has a to-do."""
    business = s.get(Business, bid)
    if business is None or business.status != "ACTIVE" or not kit.reach_today(s, bid, lang=lang, now=now):
        return []
    return [Item("kit", f"KIT-{bid}", msg("today.kit.title", lang), msg("today.kit.reason", lang), Tier.TODAY, "open",
                 f"/business?b={bid}&tab=people#first-ten-kit")]


def _slack(lang: Lang) -> list[Item]:
    if notify_state.slack_down() is None:
        return []
    return [Item("alert", "SLACK", msg("today.slack.title", lang), msg("today.slack.reason", lang), Tier.TODAY, "open",
                 "/settings")]


def _turns(s: Session, bid: int, lang: Lang) -> list[Item]:
    """The owner's turns that are neither a decision nor a to-do: a kickoff conversation, a brief to approve, a cycle
    proposal to start or a stage at its gate."""
    return [*_kickoff(s, bid, lang), *_brief(s, bid, lang), *_cycle(s, bid, lang)]


def collect(s: Session, bid: int, now: datetime, lang: Lang) -> list[Item]:
    zone = ZoneInfo(app_settings.effective().usage.timezone)
    day = now.astimezone(zone).date()
    found = [*_turns(s, bid, lang), *_requests(s, bid, day, zone, lang),
             *_owner_tasks(s, bid, day, zone, lang), *_ideas(s, bid, lang), *_bets(s, bid, lang),
             *_profile(s, bid, lang), *_kit(s, bid, now, lang), *_slack(lang)]
    return sorted(found, key=Item.sort_key)


# ------------------------------------------------------------------ yesterday, the number, the banner

YESTERDAY: tuple[tuple[str, Callable[[ActivityEvent], bool]], ...] = (
    ("today.yesterday.people_found", lambda e: e.verb == "people_found"),
    ("today.yesterday.bet_retro", lambda e: e.verb == "bet_retro"),
    ("today.yesterday.post_cards",
     lambda e: e.verb == "owner_task_created" and e.actor != "owner" and e.data.get("kind") in ("post", "publish")),
    ("today.yesterday.ideas", lambda e: e.verb == "idea_triaged"),
    ("today.yesterday.tasks", lambda e: e.entity == "task" and e.verb == "status" and e.data.get("to") == "DONE"),
)


def yesterday(s: Session, bid: int, now: datetime, lang: Lang) -> list[str]:
    """Up to three lines of what the company did in the last 24 hours, counted from the activity log, in the order of
    `YESTERDAY`."""
    events = list(s.scalars(select(ActivityEvent).where(
        ActivityEvent.business_id == bid, ActivityEvent.at >= now - timedelta(hours=24),
        ActivityEvent.verb.in_(("people_found", "bet_retro", "owner_task_created", "idea_triaged", "status")))))
    counted = [(key, sum(1 for e in events if match(e))) for key, match in YESTERDAY]
    return [msg(key, lang, n=n) for key, n in counted if n][:3]


def number(s: Session, bid: int, lang: Lang) -> dict[str, Any]:
    """The completion sentence's own number: people who used it twice or more, and the brief's headline goal in its
    own words and unit (`goal`), counted when the people list can count it. First value and stars are smaller signals
    beside it."""
    goals, source = completion.goals(s, bid, lang=lang)
    goal = completion.headline(goals)
    provider = people_link.provider()
    counted = completion.counts(s, bid)
    stars = s.scalar(select(MetricPoint.value).where(MetricPoint.business_id == bid, MetricPoint.metric == "github_stars_total",
                                                     MetricPoint.value.is_not(None))
                     .order_by(MetricPoint.observed_at.desc(), MetricPoint.id.desc()).limit(1))
    return {"repeat": provider.used_twice(s, bid).users if provider.available else None,
            "first_value": provider.north_star(s, bid)["first_value"] if provider.available else None,
            "goal": {"text": goal.text, "number": goal.number, "unit": goal.unit, "measure": goal.measure,
                     "current": counted.get(goal.measure) if goal.measure else None},
            "goal_from_brief": source == "brief", "stars": None if stars is None else int(stars),
            "link": f"/business?b={bid}"}


def elsewhere(s: Session, business_id: int, lang: Lang, failed: Mapping[int, int] | None = None) -> list[dict[str, Any]]:
    """The other businesses with something waiting on the owner, so choosing one business hides no one's decision: each
    one's pending decisions (`blocking` of them stop its work), open to-dos, the other turns its own home lists
    (`turns`: a kickoff conversation, a brief, a cycle proposal or a stage gate), and the work that stopped or failed
    there and waits on the owner (`failed`, counted by the caller from the alert list, keyed by business). Company-wide
    requests, to-dos and alerts belong to every business and are on this business's own screens already, so they count
    nowhere here. Archived and test businesses are left out, as the business list leaves them out."""
    failed = failed or {}
    asks = {bid: (n, blocking) for bid, n, blocking in s.execute(
        select(Project.business_id, func.count(), func.count().filter(AgentRequest.blocking.is_(True)))
        .join(Project, Project.id == AgentRequest.project_id)
        .where(AgentRequest.status == RequestStatus.PENDING.value, Project.business_id != business_id)
        .group_by(Project.business_id))}
    todos = dict(s.execute(select(OwnerTask.business_id, func.count())
                           .where(OwnerTask.status == owner_tasks.OPEN, OwnerTask.business_id != business_id)
                           .group_by(OwnerTask.business_id)).all())
    out = []
    for b in s.scalars(select(Business).where(Business.id != business_id).order_by(Business.id)):
        if roadmap.hidden_business(b, include_archived=False, include_test=False):
            continue
        decisions, blocking = asks.get(b.id, (0, 0))
        row = {"business_id": b.id, "name": b.name, "decisions": decisions, "todos": todos.get(b.id, 0),
               "blocking": blocking, "turns": len(_turns(s, b.id, lang)), "failed": failed.get(b.id, 0)}
        if row["decisions"] or row["todos"] or row["turns"] or row["failed"]:
            out.append(row)
    return out


def banner(s: Session, lang: Lang) -> dict[str, str] | None:
    """The one line that explains a quiet company: all its work waits on a usage cap."""
    jobs = queue.usage_waiting(s)
    if not jobs:
        return None
    latest = jobs[0]
    until = usage.local_time(latest.run_after, app_settings.effective().usage.timezone, lang=lang)
    return {"kind": "usage_wait", "link": "/settings",
            "text": msg("today.banner.usage_wait", lang, reason=msg(f"usage.reason.{latest.wait_reason}", lang),
                        until=until)}


def build(s: Session, business_id: int, *, lang: Lang, everything: bool = False,
          now: datetime | None = None, failed: Mapping[int, int] | None = None) -> dict[str, Any]:
    """The home's read model in the owner's language. `everything` returns every item, for the page that lists them
    all. Each item names the day it falls on (0 is today), so no day asks for more than the minutes a day the owner
    gave at kickoff (`daily_min`, None when the plan names none); `today_min` and `today_count` are today's share.
    `failed` counts each business's stopped or failed work for `elsewhere`."""
    if s.get(Business, business_id) is None:
        raise NotFound(msg("today.not_found", lang, id=business_id))
    now = now or datetime.now(UTC)
    items = collect(s, business_id, now, lang)
    daily = owner_time.of_business(s, business_id)
    days = owner_time.plan_days([i.estimate_min for i in items], [i.tier <= Tier.TODAY for i in items], daily)
    shown = items if everything else items[:HOME_LIMIT]
    return {"business_id": business_id, "items": [i.view(lang, d) for i, d in zip(shown, days)], "total": len(items),
            "total_min": sum(i.estimate_min for i in items), "daily_min": daily,
            "today_min": sum(i.estimate_min for i, d in zip(items, days) if d == 0),
            "today_count": days.count(0), "yesterday": yesterday(s, business_id, now, lang),
            "number": number(s, business_id, lang), "banner": banner(s, lang),
            "company_stop": stops.view(stops.read(s).company), "elsewhere": elsewhere(s, business_id, lang, failed)}
