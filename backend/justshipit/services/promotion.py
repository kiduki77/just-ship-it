"""The weekly promotion: once a week per business, post cards for the places that may be posted to again.

Promotion is a routine, not a bet. A one-off post is buried in hours, and a repeat of the same post is buried as a
duplicate (Hacker News) or refused (GeekNews). So each week the server looks at every channel and keeps the ones where
all of these hold:

- the owner or a retro has not dropped it,
- its launch gate (`requires`) is met,
- its `repost` rule allows another post: the floor between posts has passed, and there is material no earlier post
  there used (`needs_new_material`),
- no card for it waits for the owner.

A channel whose rules were not read (`channels.writable`) is never picked, whatever else is true of it: the company
writes nothing for it, and the week's record names the places left out for that reason.

Only channels that fit the business's customer description are looked at (`profile.fit`), so a local shop gets 당근 and
네이버 카페 and a CLI gets Hacker News, never the other way round. A channel that declares a line the description does
not know yet waits, and the report says which line it needs. A guess counts, and the card it makes says so; a one-time
launch (`requires`) waits until who buys is said or confirmed. A generic entry (any subreddit, anything else) and a
place that is not for promotion (`weekly: false`, a job board) are never picked: the routine posts to the specific
units. Global channels come before Korean ones, and the weekly cap (`bets.weekly_post_cap`) counts cards already made this
week. Material is what has happened since: a release, a milestone of people or stars, a finished bet. With none, the
routine makes nothing and says so in the activity log for the briefing. One marketing call writes the cards for the
picked channels; the server builds each one through `owner_tasks.build_post_card`, so a card passes the same checks as
one an agent proposes. A channel that bans generated text (`self_written`) gets facts and questions, never a sentence.
Cards go on different days when a channel is `same_day_exclusive`.

What the owner reads (the to-do's title, why a channel was left out, the activity line) is in the owner's language,
`lang`, which the job handler reads once. The places themselves follow the business's market
(`audience.market_of_business`), so a Korean shop read in English still gets 당근, named in English.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.agents.executor import run_agent
from justshipit.contracts.post_card import PostCardDraft
from justshipit.contracts.promotion import PromotionOut
from justshipit.db.models import Bet, Business, ChannelDrop, LaunchFact, MetricPoint, OwnerTask
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, msg
from justshipit.services import (
    activity,
    app_settings,
    audience,
    channels,
    launch_gate,
    metrics,
    owner_tasks,
    people_link,
    profile,
    roadmap,
    secrets,
)
from justshipit.services.control import ControlError
from justshipit.services.fence import fenced
from justshipit.settings import settings

log = logging.getLogger(__name__)

FIRST_VALUE_STEPS = (5, 10, 20, 50, 100)
STAR_STEPS = (100, 500, 1000, 5000, 10000)
RELEASE_DAYS = 45
BET_DAYS = 60
MAX_DETAIL = 300

def confirm_gate_item(s: Session, business_id: int, item: str, note: str | None = None, *, lang: Lang) -> LaunchFact:
    """The owner vouches for a gate item. Computed items are refused: they follow the people list."""
    if item not in launch_gate.OWNER_GATE_ITEMS:
        items = ", ".join(launch_gate.gate_label(i, lang=lang) for i in launch_gate.OWNER_GATE_ITEMS)
        raise ControlError(msg("promotion.gate.not_owner", lang, items=items))
    if s.get(Business, business_id) is None:
        raise ControlError(msg("promotion.not_found", lang, id=business_id))
    fact = s.get(LaunchFact, (business_id, item))
    if fact is None:
        fact = LaunchFact(business_id=business_id, item=item)
        s.add(fact)
    fact.note = (note or "").strip()[:1000] or None
    fact.confirmed_at = datetime.now(UTC)
    activity.record(s, verb="launch_fact_confirmed", entity="business", entity_id=business_id, project_id=None,
                    business_id=business_id, actor="owner",
                    summary=msg("promotion.activity.gate_confirmed", lang, id=business_id,
                                item=launch_gate.gate_label(item, lang=lang)),
                    data={"item": item})
    return fact


def withdraw_gate_item(s: Session, business_id: int, item: str) -> None:
    fact = s.get(LaunchFact, (business_id, item))
    if fact is not None:
        s.delete(fact)


# ------------------------------------------------------------------ what was posted

@dataclass(frozen=True)
class PostRecord:
    channel: str
    status: str
    at: datetime
    due: datetime | None
    week: str | None
    material_keys: tuple[str, ...]


def posts(s: Session, business_id: int) -> list[PostRecord]:
    """The business's post tasks that count: waiting for the owner, or done. A skipped one never went out."""
    out = []
    for t in s.scalars(select(OwnerTask).where(OwnerTask.business_id == business_id, OwnerTask.kind == "post",
                                               OwnerTask.status.in_(("OPEN", "DONE"))).order_by(OwnerTask.id)):
        card = t.card or {}
        out.append(PostRecord(card.get("channel") or "other", t.status, t.done_at or t.created_at, t.due_at,
                              card.get("week"), tuple(card.get("material_keys") or ())))
    return out


def droppable(s: Session, business_id: int, key: str, now: datetime | None = None, *, lang: Lang,
              ) -> tuple[bool, str]:
    """Whether a retro may stop using a channel: only after it posted three times with new material or four weeks have
    passed since the first post, and only on first-value numbers. A channel is never dropped on one post's luck, nor on
    stars. The reason is in `lang`."""
    now = now or datetime.now(UTC)
    done = [p for p in posts(s, business_id) if p.channel == key and p.status == "DONE"]
    if not done:
        return False, msg("promotion.drop.no_posts", lang)
    fresh = sum(1 for p in done if p.material_keys)
    weeks = (now - min(p.at for p in done)).days // 7
    if fresh < 3 and weeks < 4:
        return False, msg("promotion.drop.too_soon", lang, fresh=fresh, weeks=weeks)
    seen = s.scalar(select(MetricPoint.id).where(MetricPoint.business_id == business_id,
                                                MetricPoint.metric == "people_first_value",
                                                MetricPoint.value.is_not(None)).limit(1))
    if seen is None:
        return False, msg("promotion.drop.no_value_numbers", lang)
    return True, msg("promotion.drop.ok", lang, fresh=fresh, weeks=weeks)


def drop_channel(s: Session, business_id: int, key: str, bet_id: int | None, reason: str) -> None:
    if s.get(ChannelDrop, (business_id, key)) is None:
        s.add(ChannelDrop(business_id=business_id, channel=key, bet_id=bet_id, reason=reason[:1000]))


def dropped(s: Session, business_id: int) -> set[str]:
    return set(s.scalars(select(ChannelDrop.channel).where(ChannelDrop.business_id == business_id)))


# ------------------------------------------------------------------ material

@dataclass(frozen=True)
class Material:
    key: str
    kind: str
    title: str
    detail: str
    url: str | None
    at: datetime


def _clip(text: str, n: int = MAX_DETAIL) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def fetch_releases(business: Business, fetch: metrics.Fetch | None, *, lang: Lang) -> list[dict]:
    """The repo's latest releases, or none when the repo is unset or GitHub will not say. A rate limit raises MustWait."""
    if not business.github_repo:
        return []
    with metrics.using(fetch) as http:
        reply = metrics.github_get(http, f"/repos/{business.github_repo}/releases", secrets.get("github_token"),
                                   params={"per_page": 10})
    metrics.rate_limit_wait(reply, "releases", lang=lang)
    return reply.body if reply.status == 200 and isinstance(reply.body, list) else []


def _bet_summary(bet: Bet) -> str:
    retro = bet.retro or {}
    if retro.get("mode") == "people_quotes":
        said = " / ".join(q["quote"] for q in retro.get("users_said", [])[:2])
        return _clip(f"{retro.get('did', '')} What users said: {said}" if said else retro.get("did", ""))
    return _clip(f"{retro.get('hypothesis', bet.question or '')} Verdict: {retro.get('verdict_reason', '')}")


def gather_material(s: Session, business: Business, releases: list[dict], now: datetime) -> list[Material]:
    """What happened lately, as data for the marketing agent's prompt. The agent writes the owner's words from it, so
    the titles here are plain English facts, never owner text."""
    out: list[Material] = []
    for r in releases:
        at = datetime.fromisoformat(r["published_at"]) if r.get("published_at") else None
        if r.get("draft") or at is None or now - at > timedelta(days=RELEASE_DAYS) or not r.get("tag_name"):
            continue
        out.append(Material(f"release:{r['tag_name']}", "release", r.get("name") or r["tag_name"], _clip(r.get("body") or ""),
                            r.get("html_url"), at))
    people = people_link.provider()
    if people.available:
        first_value = people.north_star(s, business.id)["first_value"]
        if reached := [n for n in FIRST_VALUE_STEPS if first_value >= n]:
            out.append(Material(f"milestone:first_value:{reached[-1]}", "milestone",
                                f"{reached[-1]} people have now got value", f"{first_value} on the People list now", None, now))
    stars = s.scalar(select(MetricPoint.value).where(MetricPoint.business_id == business.id,
                                                    MetricPoint.metric == "github_stars_total",
                                                    MetricPoint.value.is_not(None))
                     .order_by(MetricPoint.observed_at.desc()).limit(1))
    if stars is not None and (reached := [n for n in STAR_STEPS if stars >= n]):
        out.append(Material(f"milestone:stars:{reached[-1]}", "milestone", f"Passed {reached[-1]} GitHub stars",
                            f"{stars:g} stars now", None, now))
    for bet in s.scalars(select(Bet).where(Bet.business_id == business.id, Bet.status == "DONE", Bet.retro.is_not(None),
                                           Bet.end_cause.is_distinct_from("legacy"),
                                           Bet.finished_at >= now - timedelta(days=BET_DAYS)).order_by(Bet.id)):
        out.append(Material(f"bet:{bet.id}", "bet", f"Experiment result: {bet.title}", _bet_summary(bet), None, bet.finished_at))
    return out


# ------------------------------------------------------------------ choosing the week's places

@dataclass(frozen=True)
class Candidate:
    channel: channels.Channel
    fresh: list[Material]
    fit: profile.Fit


@dataclass
class Report:
    week: str
    tasks: list[int] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    note: str = ""


def unchecked_places(skipped: dict[str, str]) -> list[channels.Channel]:
    """The places left out of the week because their rules were not read."""
    return [c for k in skipped if (c := channels.get(k)) is not None and not channels.writable(c)]


def _no_one_writable(picked: list[Candidate], skipped: dict[str, str]) -> bool:
    """Whether the rules check is the only reason the week has no place: nothing was picked, and every place that
    was looked at was left out for unchecked rules."""
    return not picked and bool(unchecked_places(skipped)) and len(unchecked_places(skipped)) == len(skipped)


def week_of(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def pick(s: Session, business_id: int, material: list[Material], cap_left: int, now: datetime, *, lang: Lang,
         ) -> tuple[list[Candidate], dict[str, str]]:
    """The channels to post to this week, global first, at most `cap_left`, and why each other one is out, in `lang`.
    Only the channels that fit the customer description (`profile.fit`) and the market (`region`) are looked at. A
    channel whose rules were not read (`channels.writable`) is out before any other check, whatever its fit. A
    channel that declares a line nobody knows yet is not picked, and `skipped` holds one reason per such line, keyed
    `profile:<line>`, not one per channel."""
    launch_gate.checked_registry()
    history, gone = posts(s, business_id), dropped(s, business_id)
    prof = profile.of(s, business_id)
    registry = channels.for_market(audience.market_of_business(s, business_id))
    chosen: list[Candidate] = []
    skipped: dict[str, str] = {}
    for ch in registry:
        if ch.generic or not ch.weekly:
            continue
        fit = profile.fit(ch.fits, prof)
        if isinstance(fit, profile.Miss):
            continue
        if not channels.writable(ch):
            skipped[ch.key] = msg("promotion.skip.rules_unchecked", lang)
            continue
        if isinstance(fit, profile.Unsure):
            for line in fit.lines:
                skipped.setdefault(f"profile:{line}", msg("promotion.skip.profile_unknown", lang,
                                                          lines=msg(f"profile.line.{line}", lang).lower()))
            continue
        mine = [p for p in history if p.channel == ch.key]
        used = {k for p in mine for k in p.material_keys}
        waiting = any(p.status == "OPEN" for p in mine)
        last_done = max((p.at for p in mine if p.status == "DONE"), default=None)
        fresh = [m for m in material if m.key not in used] if ch.repost.needs_new_material else list(material)
        if ch.key in gone:
            why = msg("promotion.skip.dropped", lang)
        elif lacking := launch_gate.unmet(s, business_id, ch):
            why = msg("promotion.skip.gate", lang, items=", ".join(launch_gate.gate_label(i, lang=lang) for i in lacking))
        elif ch.requires and not profile.launch_ready(ch.fits, prof):
            why = msg("promotion.skip.launch_guess", lang)
        elif waiting:
            why = msg("promotion.skip.waiting", lang)
        elif last_done and now - last_done < timedelta(days=ch.repost.min_interval_days):
            why = msg("promotion.skip.interval", lang, n=ch.repost.min_interval_days)
        elif not fresh:
            why = msg("promotion.skip.no_material", lang)
        elif len(chosen) >= cap_left:
            why = msg("promotion.skip.cap", lang)
        else:
            chosen.append(Candidate(ch, fresh, fit))
            continue
        skipped[ch.key] = why
    return chosen, skipped


def assign_days(order: list[channels.Channel], taken: dict[date, set[bool]], start: date) -> list[date]:
    """A day for each channel. A same-day-exclusive channel needs a day nothing else is planned on, and no channel
    shares a day with an exclusive one. `taken` holds the days already planned, with whether each plan was exclusive."""
    days = []
    for ch in order:
        d = start
        while (flags := taken.get(d, set())) and (ch.same_day_exclusive or True in flags):
            d += timedelta(days=1)
        taken.setdefault(d, set()).add(ch.same_day_exclusive)
        days.append(d)
    return days


# ------------------------------------------------------------------ the weekly job

def _system() -> str:
    return (settings().prompts_dir / "promotion.md").read_text(encoding="utf-8")


def _material_block(m: Material) -> str:
    """One piece of material as data. A release name and body, a tag and a bet's words come from outside or from the
    owner, so none of it can close the fence or pass for the prompt's instructions."""
    return fenced(f"material {m.key}", f"[{m.key}] {m.title}: {m.detail}" + (f" ({m.url})" if m.url else ""))


def _prompt(business: Business, brief_text: str, who: str, picked: list[Candidate], lang: Lang) -> str:
    """The week's input for the marketing agent. Each channel's name and rules are in the owner's language, the one
    the cards are written in."""
    blocks = []
    for c in picked:
        mats = "\n".join(_material_block(m) for m in c.fresh)
        blocks.append(f"### {c.channel.key} ({c.channel.names[lang]}, {c.channel.mode})\nRules: {c.channel.rules[lang]}\n"
                      f"New material you may use:\n{mats}")
    return "\n\n".join(["## The business\n" + fenced("business", f"B-{business.id} {business.name}: {business.one_liner}"),
                        "## From the plan\n" + fenced("plan", brief_text),
                        "## Who the customers are\n" + who,
                        "## Channels to make a post card for this week\n" + "\n\n".join(blocks),
                        "Make one post card for each channel above. Use only facts from its new material."])


def _brief_text(s: Session, business_id: int) -> str:
    brief = roadmap.approved_brief(s, business_id)
    if brief is None:
        return ""
    keep = ("one_liner", "customer_problem", "scope")
    return "\n".join(f"- {sec['title']}: {line['text']}" for sec in brief.sections if sec["key"] in keep
                     for line in sec["lines"] if line["provenance"] in ("said", "confirmed"))


def fit_record(fit: profile.Fit) -> dict:
    """What the pick rested on, written by the server onto the card: the lines whose matching values were only guesses,
    and the codes that matched per declared line. The console compares these with the description as it is now."""
    return {"guessed": list(fit.guessed), "codes": {line: list(codes) for line, codes in fit.basis.items()}}


def _headline(card: dict) -> str:
    what = card["what"]
    return _clip((what.get("title") or (what.get("facts") or [""])[0]), 80)


# A refusal no rewrite can lift: the card was fine as writing, the channel was not open to it.
_NOT_THE_WRITING = frozenset({"launch_gate", "unknown_channel", "rules_unchecked"})


def _drafts(business_id: int, prompt: str, wanted: dict[str, Candidate]) -> dict[str, PostCardDraft]:
    out = run_agent("marketing", "scheduled", prompt, PromotionOut, project_id=None, business_id=business_id,
                    system_override=_system())
    return {d.channel: d for d in out.cards if d.channel in wanted}


def _build(s: Session, business_id: int, wanted: dict[str, Candidate], drafts: dict[str, PostCardDraft], week: str,
           lang: Lang) -> tuple[list[tuple[Candidate, dict]], dict[str, owner_tasks.CardRefused | None]]:
    """The cards that pass the checks, and each channel that has none: the refusal, or None when no draft came."""
    built: list[tuple[Candidate, dict]] = []
    failed: dict[str, owner_tasks.CardRefused | None] = {}
    for key, cand in wanted.items():
        if key not in drafts:
            failed[key] = None
            continue
        try:
            card, _ = owner_tasks.build_post_card(drafts[key], launch_gate.gate_for(s, business_id), lang=lang)
        except owner_tasks.CardRefused as exc:
            failed[key] = exc
            continue
        built.append((cand, {**card, "week": week, "material_keys": [m.key for m in cand.fresh],
                             "fit": fit_record(cand.fit)}))
    return built, failed


def _again_prompt(prompt: str, failed: dict[str, owner_tasks.CardRefused | None]) -> str:
    """The week's input once more, for the channels whose first card the server would not keep, each with why. A
    refusal quotes the card's own words, which came from the material, so it is fenced as data."""
    lines = [f"### {key}\n" + ("You wrote no card for this channel." if why is None
                               else fenced(f"refused {key}", why.words("en"))) for key, why in failed.items()]
    return "\n\n".join([prompt, "## Write these again\nThe server did not keep your first card for each channel below, "
                        "for the reason given. Write one card for each of them again so that the reason no longer "
                        "holds, and no card for any other channel. Rewrite the whole card, not only the item named: "
                        "every item must pass the same rule.\n\n" + "\n\n".join(lines)])


def _then(summary: str, more: str) -> str:
    """One sentence after another: the notes end without a full stop, so one is put between them."""
    return f"{summary.rstrip()}{'' if summary.rstrip().endswith(('.', '!', '?')) else '.'} {more}"


def run_week(business_id: int, *, lang: Lang, fetch: metrics.Fetch | None = None, now: datetime | None = None) -> Report:
    """One weekly round, with what the owner reads in `lang`. The model call holds no transaction, and a `MustWait` out
    of it (or out of the release read) leaves nothing written, so the job simply runs again later."""
    now = now or datetime.now(UTC)
    week = week_of(now)
    report = Report(week)
    with session_scope() as s:
        business = s.get(Business, business_id)
        if business is None or business.status == "ARCHIVED" or roadmap.approved_brief(s, business_id) is None:
            report.note = msg("promotion.note.no_brief", lang)
            return report
        cap = app_settings.effective().bets.weekly_post_cap
        made = sum(1 for p in posts(s, business_id) if p.week == week)
        cap_left = max(0, cap - made)
        participate = people_link.provider().request_participation(s, business_id)
        if cap_left == 0:
            report.note = msg("promotion.note.capped", lang, n=made, cap=cap)
            return report
    releases = fetch_releases(business, fetch, lang=lang)
    with session_scope() as s:
        material = gather_material(s, s.get(Business, business_id), releases, now)
        picked, report.skipped = pick(s, business_id, material, cap_left, now, lang=lang)
        brief_text = _brief_text(s, business_id)
        who = profile.prompt_block(profile.of(s, business_id), lang)
        if not picked:
            report.note = msg("promotion.note.no_channel" if material else "promotion.note.no_material", lang)
            if _no_one_writable(picked, report.skipped):
                report.note = msg("promotion.note.no_writable", lang)
            left_out = unchecked_places(report.skipped)
            summary = msg("promotion.activity.week", lang, id=business_id, week=week, note=report.note)
            if left_out:
                summary = _then(summary, msg("promotion.activity.unchecked", lang,
                                             places=", ".join(c.names[lang] for c in left_out)))
            activity.record(s, verb="promotion_week", entity="business", entity_id=business_id, project_id=None,
                            business_id=business_id, actor="system", summary=summary,
                            data={"week": week, "skipped": report.skipped, "material": len(material),
                                  "rules_unchecked": [c.key for c in left_out],
                                  "participate_requested": participate})
            return report
        prompt = _prompt(s.get(Business, business_id), brief_text, who, picked, lang)
    by_key = {c.channel.key: c for c in picked}
    drafts = _drafts(business_id, prompt, by_key)
    with session_scope() as s:
        built, failed = _build(s, business_id, by_key, drafts, week, lang)
    again = {k: why for k, why in failed.items() if why is None or why.rule not in _NOT_THE_WRITING}
    if again:
        drafts = _drafts(business_id, _again_prompt(prompt, again), {k: by_key[k] for k in again})
        with session_scope() as s:
            rebuilt, refailed = _build(s, business_id, {k: by_key[k] for k in again}, drafts, week, lang)
        built = sorted(built + rebuilt, key=lambda b: list(by_key).index(b[0].channel.key))
        failed = {k: v for k, v in failed.items() if k not in again} | refailed
    for key, why in failed.items():
        report.skipped[key] = (msg("promotion.skip.no_draft", lang) if why is None
                               else msg("promotion.skip.refused", lang, rule=why.rule))
    with session_scope() as s, activity.as_actor("system"):
        taken: dict[date, set[bool]] = {}
        for t in s.scalars(select(OwnerTask).where(OwnerTask.business_id == business_id, OwnerTask.kind == "post",
                                                   OwnerTask.status == "OPEN", OwnerTask.due_at.is_not(None))):
            ch = channels.get((t.card or {}).get("channel") or "")
            taken.setdefault(t.due_at.date(), set()).add(bool(ch and ch.same_day_exclusive))
        days = assign_days([c.channel for c, _ in built], taken, now.date() + timedelta(days=1))
        for (cand, card), day in zip(built, days, strict=True):
            title = msg("promotion.task.title", lang, channel=cand.channel.names[lang], headline=_headline(card))
            task = owner_tasks.create_post_task(s, business_id, card, title, due_at=datetime.combine(day, time(15, 0), UTC))
            if task is not None:
                report.tasks.append(task.id)
        if not report.tasks:
            # A card that passed and still made no to-do was held by the stage gate, which logs each one itself.
            report.note = (msg("promotion.note.held", lang, n=len(built)) if built
                           else msg("promotion.note.none_passed", lang))
        summary = (msg("promotion.activity.made", lang, id=business_id, week=week, n=len(report.tasks)) if report.tasks
                   else msg("promotion.activity.week", lang, id=business_id, week=week, note=report.note))
        if left_out := unchecked_places(report.skipped):
            summary = _then(summary, msg("promotion.activity.unchecked", lang,
                                         places=", ".join(c.names[lang] for c in left_out)))
        activity.record(s, verb="promotion_week", entity="business", entity_id=business_id, project_id=None,
                        business_id=business_id, actor="system", summary=summary,
                        data={"week": week, "tasks": report.tasks, "skipped": report.skipped,
                              "rules_unchecked": [c.key for c in left_out], "participate_requested": participate})
    return report
