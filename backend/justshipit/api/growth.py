"""Routes of the bet loop: a business's numbers and GitHub repo, the launch gate, the weekly promotion, a bet's retro,
and the program's done sentence for the home screen. Each route reads the owner's language once, for its labels and
its refusals."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from justshipit.db.models import Bet, Business, ChannelDrop
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, msg
from justshipit.i18n.said import say
from justshipit.services import (
    activity,
    app_settings,
    audience,
    bet_loop,
    channels,
    completion,
    launch_gate,
    metrics,
    profile,
    promotion,
    queue,
    roadmap,
)
from justshipit.services.control import ControlError

router = APIRouter()


class RepoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str = Field(max_length=200)


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=1000)


def _act(fn, *args, **kwargs):
    """Run an owner action in its own transaction; a refusal becomes 409 with its message in the owner's language."""
    with activity.as_actor("owner"):
        try:
            with session_scope() as s:
                result = fn(s, *args, **kwargs)
                s.flush()
                return result
        except (ControlError, metrics.CollectError) as exc:
            raise HTTPException(409, str(exc)) from exc


def _business(s, business_id: int, lang: Lang) -> Business:
    b = s.get(Business, business_id)
    if b is None:
        raise ControlError(msg("promotion.not_found", lang, id=business_id))
    return b


@router.get("/businesses/{business_id}/completion")
def business_completion(business_id: int) -> dict[str, Any]:
    return _act(completion.check, business_id, lang=app_settings.language())


@router.get("/businesses/{business_id}/metrics")
def business_metrics(business_id: int, days: int = 30) -> dict[str, Any]:
    days = max(1, min(days, 365))
    lang = app_settings.language()

    def read(s):
        _business(s, business_id, lang)
        now = datetime.now(UTC)
        return metrics.observed(s, business_id, now - timedelta(days=days), now, lang=lang).model_dump(mode="json")
    return _act(read)


@router.get("/businesses/{business_id}/numbers")
def business_numbers(business_id: int, days: int = 30) -> dict[str, Any]:
    """The three numbers of the business page (interest, first value, repeat use) with a daily trend, and the done
    sentence checked against them."""
    days = max(2, min(days, 90))
    lang = app_settings.language()

    def read(s):
        _business(s, business_id, lang)
        return {"stages": metrics.stage_trends(s, business_id, lang=lang, days=days),
                "completion": completion.check(s, business_id, lang=lang)}
    return _act(read)


@router.post("/businesses/{business_id}/github")
def set_github_repo(business_id: int, body: RepoIn) -> dict[str, Any]:
    lang = app_settings.language()

    def write(s):
        b = _business(s, business_id, lang)
        b.github_repo = metrics.check_repo(body.repo, lang=lang)
        return {"business_id": b.id, "github_repo": b.github_repo}
    return _act(write)


@router.post("/businesses/{business_id}/github/collect")
def collect_github_now(business_id: int) -> dict[str, Any]:
    lang = app_settings.language()

    def enqueue(s):
        if not _business(s, business_id, lang).github_repo:
            raise ControlError(msg("metrics.error.repo_first", lang))
        job = queue.enqueue(s, "github_collect", {"business_id": business_id}, max_attempts=2, by_owner=True)
        return {"queued": job is not None}
    return _act(enqueue)


@router.get("/businesses/{business_id}/launch-gate")
def show_launch_gate(business_id: int) -> dict[str, Any]:
    lang = app_settings.language()

    def read(s):
        _business(s, business_id, lang)
        items = list(launch_gate.OWNER_GATE_ITEMS) + sorted({i for c in channels.registry().values() for i in c.requires
                                                               if i not in launch_gate.OWNER_GATE_ITEMS})
        state = launch_gate.gate_state(s, business_id, items)
        return {"items": [{"item": i, "label": launch_gate.gate_label(i, lang=lang), "met": state[i],
                           "by": "owner" if i in launch_gate.OWNER_GATE_ITEMS else "people_list"} for i in items],
                "channels": {c.key: {"met": all(state[i] for i in c.requires), "requires": list(c.requires)}
                             for c in channels.registry().values() if c.requires}}
    return _act(read)


@router.post("/businesses/{business_id}/launch-gate/{item}")
def confirm_launch_item(business_id: int, item: str, body: NoteIn | None = None) -> dict[str, Any]:
    lang = app_settings.language()

    def write(s):
        fact = promotion.confirm_gate_item(s, business_id, item, body.note if body else None, lang=lang)
        return {"business_id": business_id, "item": fact.item, "confirmed_at": fact.confirmed_at}
    return _act(write)


@router.delete("/businesses/{business_id}/launch-gate/{item}")
def withdraw_launch_item(business_id: int, item: str) -> dict[str, Any]:
    lang = app_settings.language()

    def write(s):
        promotion.withdraw_gate_item(s, _business(s, business_id, lang).id, item)
        return {"item": item}
    return _act(write)


@router.post("/businesses/{business_id}/promotion/run")
def run_promotion_now(business_id: int) -> dict[str, Any]:
    lang = app_settings.language()

    def enqueue(s):
        _business(s, business_id, lang)
        week = promotion.week_of(datetime.now(UTC))
        job = queue.enqueue(s, "promotion_week", {"business_id": business_id},
                            idempotency_key=f"promotion_week:{business_id}:{week}:manual", max_attempts=2,
                            by_owner=True)
        return {"queued": job is not None, "week": week}
    return _act(enqueue)


@router.get("/businesses/{business_id}/channels")
def business_channels(business_id: int) -> dict[str, Any]:
    """The places for the business's market with whether each one's rules were read, checked ones first. `does` is what
    the company writes there: points (an outline place), a draft, or nothing (rules unchecked)."""
    lang = app_settings.language()

    def read(s):
        _business(s, business_id, lang)
        prof, gone = profile.of(s, business_id), promotion.dropped(s, business_id)
        places = sorted(channels.for_market(audience.market_of_business(s, business_id)),
                        key=lambda c: not channels.writable(c))
        why = {c.key: channels.unchecked_said(c) for c in places}
        rows = [{"key": c.key, "name": c.names[lang], "region": c.region, "generic": c.generic,
                 "state": channels.state(c), "checked_on": c.checked_on, "source_url": c.source_url,
                 "background_url": c.background_url, "rules": c.rules[lang], "unchecked_why": c.unchecked_why,
                 "unchecked_text": say(why[c.key], lang) if why[c.key] else None,
                 "does": {"outline": "points", "draft": "draft"}[c.mode] if channels.writable(c) else "nothing",
                 "fits": isinstance(profile.fit(c.fits, prof), profile.Fit), "repost_days": c.repost.min_interval_days,
                 "requires_unmet": [{"item": i, "label": launch_gate.gate_label(i, lang=lang)}
                                    for i in launch_gate.unmet(s, business_id, c)],
                 "dropped": c.key in gone} for c in places]
        listed = [r for r in rows if not r["generic"]]
        checked = sum(1 for r in listed if r["state"] == "checked")
        return {"places": rows, "counts": {"checked": checked, "unchecked": len(listed) - checked}}
    return _act(read)


@router.get("/businesses/{business_id}/dropped-channels")
def dropped_channels(business_id: int) -> list[dict[str, Any]]:
    lang = app_settings.language()

    def read(s):
        return [{"channel": d.channel, "reason": d.reason, "bet_id": d.bet_id, "dropped_at": d.dropped_at}
                for d in s.scalars(select(ChannelDrop).where(ChannelDrop.business_id == _business(s, business_id, lang).id))]
    return _act(read)


@router.get("/bets/{bet_id}/retro")
def bet_retro(bet_id: int) -> dict[str, Any]:
    lang = app_settings.language()

    def read(s):
        bet = s.get(Bet, bet_id)
        if bet is None:
            raise ControlError(msg("promotion.bet_not_found", lang, id=bet_id))
        return {"bet_id": bet.id, "status": bet.status, "end_cause": bet.end_cause, "verdict": bet.verdict,
                "retro": bet.retro, "waiting": bet.status == roadmap.BetStatus.REVIEW.value and bet.retro is None,
                "stuck": roadmap.retro_stuck(s, bet), "routine": bet_loop.routine_state(s, bet)}
    return _act(read)


@router.post("/bets/{bet_id}/retro/redo")
def redo_retro(bet_id: int) -> dict[str, Any]:
    def enqueue(s):
        return {"queued": True, "job_id": bet_loop.redo_retro(s, bet_id, lang=app_settings.language()).id}
    return _act(enqueue)


@router.get("/businesses/{business_id}/routines")
def routines(business_id: int) -> list[dict[str, Any]]:
    lang = app_settings.language()

    def read(s):
        out = []
        waiting = {(r.action_params or {}).get("bet_id"): r.id for r in bet_loop.pending_routine_cards(s, business_id)}
        for bet in s.scalars(select(Bet).where(Bet.business_id == _business(s, business_id, lang).id, Bet.verdict == "routine")
                             .order_by(Bet.id)):
            sc = bet_loop.routine_schedule(s, bet.id)
            out.append({"bet_id": bet.id, "title": bet.title, "state": bet_loop.routine_state(s, bet),
                        "request_id": waiting.get(bet.id),
                        "every_days": sc.interval_hours / 24 if sc else (bet.retro or {}).get("routine", {}).get("every_days"),
                        "per_run_budget_usd": bet.budget_usd, "next_run_at": sc.next_run_at if sc and sc.enabled else None})
        return out
    return _act(read)


@router.post("/bets/{bet_id}/routine/stop")
def stop_routine(bet_id: int) -> dict[str, Any]:
    def write(s):
        bet_loop.stop_routine(s, bet_id, lang=app_settings.language())
        return {"bet_id": bet_id, "state": "stopped"}
    return _act(write)
