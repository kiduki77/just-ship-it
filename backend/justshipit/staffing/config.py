from __future__ import annotations

import logging

from justshipit.settings import staffing_config
from justshipit.staffing.crew import Limits

log = logging.getLogger(__name__)

OTHER = "other"


def load_limits() -> Limits:
    return Limits.from_config(staffing_config()["staffing"])


def task_families() -> tuple[str, ...]:
    return tuple(staffing_config()["families"])


def default_family(owner: str) -> str:
    return staffing_config()["owner_default_family"].get(owner, OTHER)


def checked_family(value: str | None, owner: str, *, asked: bool) -> str:
    """The family a task is kept under. A listed family is kept. `asked` is true when the Project Manager was asked to
    name one, so a missing family there is a gap worth a warning and becomes `other`; a task from any other path
    (the owner's, an idea's) has none to give and gets its role's default. A name off the list is `other` either way,
    the way an unknown owner falls back to research."""
    given = (value or "").strip()
    if given in task_families():
        return given
    if not given and not asked:
        return default_family(owner)
    log.warning("task family %r of a task for %s is %s; using %s", given or None, owner,
                "not on the list" if given else "missing", OTHER)
    return OTHER
