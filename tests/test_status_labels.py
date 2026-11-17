"""Every status the console can show has an owner label. 활동 showed 'BET-1 … 진행 중 → LISTENING': a status added
to the backend reached the screen raw because the console had no word for it."""
from justshipit.contracts.handoff import DevJobStatus, SpecStatus
from justshipit.contracts.requests import RequestStatus
from justshipit.contracts.skills import SkillStatus
from justshipit.contracts.work import TaskStatus
from justshipit.db import models
from justshipit.services import ideas, kickoff, roadmap
from i18n_kit import web_catalog

# Statuses stored as plain strings, by the column that holds them.
PROJECT = ("CREATED", "RUNNING", "WAITING_HUMAN", "PAUSED", "DONE", "FAILED", "ARCHIVED")
PHASES = ("planning", "executing", "ceo_review", "meeting", "deciding", "reporting", "done", "archived")
DECISION = ("EFFECTIVE", "PENDING_APPROVAL", "REJECTED", "SUPERSEDED")
ACTION = ("started", "done", "failed", "not_sent")
BUSINESS = ("ACTIVE", "ARCHIVED")


def _status_labels() -> set[str]:
    """The codes the `label` catalog has a word for in every language, the ones `label()` reads."""
    return {key.removeprefix("label.status.") for key, entry in web_catalog()["label"].items()
            if key.startswith("label.status.") and entry.get("en") and entry.get("ko")}


def test_every_status_the_console_shows_has_an_owner_label():
    shown = {*map(str, (*TaskStatus, *RequestStatus, *DevJobStatus, *SpecStatus, *SkillStatus, *roadmap.BetStatus,
                        *ideas.Status, *kickoff.Status)),
             *models.BET_STATUSES, *models.BET_HEALTH, *models.HILL_POSITIONS, *models.KICKOFF_STATUSES,
             *models.OWNER_TASK_STATUSES, *models.IDEA_STATUSES, *PROJECT, *PHASES, *DECISION, *ACTION, *BUSINESS}

    assert sorted(shown - _status_labels()) == []
