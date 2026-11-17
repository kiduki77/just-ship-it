"""Retention: how long a person stays on the list when the owner does nothing, and how long raw model transcripts stay.

The list holds public handles and quotes, which are personal data. A person found and never contacted, or dropped,
is archived after a set time, and archiving erases what identifies them. Transcripts of model calls (`var/calls`)
hold the prompts and answers of every run, including the names the people finder was shown. They are in dated
folders, one per call, and are not indexed by person, so they are pruned by age and only by age.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from justshipit.db.models import Person
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import activity, app_settings, people
from justshipit.settings import company_config, settings

DEFAULTS = {"call_transcript_days": 30, "people_found_days": 90, "people_dropped_days": 90}


def config() -> dict[str, int]:
    return {**DEFAULTS, **company_config().get("retention", {})}


def prune_calls(*, today: datetime | None = None) -> int:
    """Delete the day folders of `var/calls` older than the retention window. Returns how many were removed."""
    root = settings().var_dir / "calls"
    if not root.is_dir():
        return 0
    cutoff = (today or datetime.now()).date() - timedelta(days=config()["call_transcript_days"])
    removed = 0
    for day_dir in root.iterdir():
        try:
            day = datetime.strptime(day_dir.name, "%Y%m%d").date()
        except ValueError:
            continue
        if day < cutoff and day_dir.is_dir():
            shutil.rmtree(day_dir, ignore_errors=True)
            removed += 1
    return removed


def archive_stale_people(s: Session, *, now: datetime | None = None) -> dict[int, int]:
    """Archive people found and never contacted, and people dropped, once their last change is older than the window.
    Returns how many were archived per business."""
    now = now or datetime.now(timezone.utc)
    cfg = config()
    stale = list(s.scalars(select(Person).where(
        Person.archived_at.is_(None),
        or_((Person.stage == "found") & (Person.updated_at < now - timedelta(days=cfg["people_found_days"])),
            (Person.stage == people.DROPPED) & (Person.updated_at < now - timedelta(days=cfg["people_dropped_days"]))))
        .order_by(Person.id)))
    per_business: dict[int, int] = {}
    lang = app_settings.language()
    for person in stale:
        people.archive(s, person.id, by="system", lang=lang)
        per_business[person.business_id] = per_business.get(person.business_id, 0) + 1
    lang = app_settings.language() if per_business else None
    for business_id, n in per_business.items():
        activity.record(s, verb="people_retention_ran", entity="business", entity_id=business_id, project_id=None,
                        business_id=business_id, actor="system",
                        summary=msg("operations.retention", lang, id=business_id, n=n), data={"archived": n})
    return per_business


def run() -> dict[str, int]:
    """The daily job."""
    removed = prune_calls()
    with session_scope() as s:
        archived = sum(archive_stale_people(s).values())
    return {"call_folders": removed, "people": archived}
