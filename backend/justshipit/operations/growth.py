"""Jobs of the bet loop: end running bets, settle ended ones with a retro, run routines, read GitHub and post reactions,
and make the week's promotion. The logic is in the services; these only open the transactions and read the owner's
language once for the text the services write."""
from __future__ import annotations

import logging

from justshipit.db.models import Business
from justshipit.db.session import session_scope
from justshipit.services import activity, app_settings, bet_loop, bet_retro, kit, metrics, promotion

log = logging.getLogger(__name__)


def github_collect(business_id: int) -> None:
    """Today's numbers from GitHub, then the people list's counts, each in its own transaction so a people error never
    discards the GitHub points. A rate limit raises MustWait out of here and the job comes back after the limit resets;
    nothing is stored until all of GitHub has been read."""
    with session_scope() as s:
        business = s.get(Business, business_id)
        if business is None or business.status == "ARCHIVED" or not business.github_repo:
            return
    with session_scope() as s, activity.as_actor("system"):
        metrics.collect_github(s, business_id, lang=app_settings.language())
    with session_scope() as s:
        metrics.snapshot_people(s, business_id)


def post_reactions() -> None:
    with session_scope() as s:
        metrics.poll_reactions(s, lang=app_settings.language())


HANDLERS = {
    "bet_sweep": lambda p: bet_loop.run_sweep(),
    "bet_retro": lambda p: bet_retro.run(p["bet_id"], lang=app_settings.language()),
    "routine_run": lambda p: bet_loop.run_routine(p["bet_id"], p.get("what", "")),
    "github_collect": lambda p: github_collect(p["business_id"]),
    "post_reactions": lambda p: post_reactions(),
    "promotion_week": lambda p: promotion.run_week(p["business_id"], lang=app_settings.language()),
    "kit_prepare": lambda p: kit.run(p["business_id"], p.get("parts") or list(kit.PARTS), p.get("person_ids") or [],
                                     lang=app_settings.language()),
}
