"""What this PC needs and whether it has it (`services/system_health.py`), for Settings."""
from __future__ import annotations

from fastapi import APIRouter

from justshipit.services import app_settings, system_health

router = APIRouter()


@router.get("/system")
def system(fresh: bool = False) -> dict:
    """Every check with its state and, for a problem, the sentence and the fix. `fresh` runs them now instead of
    serving the last run (at most 30 seconds old)."""
    checks = system_health.report(app_settings.language(), results=system_health.snapshot(fresh=fresh))
    return {"ok": all(c["state"] != "problem" for c in checks), "checks": checks}
