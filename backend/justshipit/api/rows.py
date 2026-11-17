"""A database row as the console reads it."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from justshipit.contracts import owner_text
from justshipit.db.models import ActivityEvent, AgentRequest, Artifact, Decision, OwnerTask, Project, Spec, Task
from justshipit.services import review_rounds

# Records whose title an agent wrote. The owner reads it through owner_text.title on every screen, stored rows from
# before the contracts scrubbed titles included.
_AGENT_TITLED = (AgentRequest, Artifact, Decision, OwnerTask, Spec, Task)


# A project's record of what the CEO judged after an owner directive, which the 결정 tab shows. Its reviews are
# `review_rounds` rows (`project_row`); the `ceo_reviews` older projects still carry in `context` are not read.
_JUDGED = ("realignments",)


def row(obj) -> dict[str, Any]:
    out = {c.name: getattr(obj, c.name) for c in obj.__table__.columns if c.name != "embedding"}
    if isinstance(obj, _AGENT_TITLED):
        out["title"] = owner_text.title(obj.title)
    if isinstance(obj, ActivityEvent) and isinstance((obj.data or {}).get("title"), str):
        out["data"] = {**obj.data, "title": owner_text.title(obj.data["title"])}
    if isinstance(obj, Project) and obj.context:
        out["context"] = {**{k: v for k, v in obj.context.items() if k != "ceo_reviews"},
                          **{k: [_judged(x) for x in obj.context[k]]
                             for k in _JUDGED if isinstance(obj.context.get(k), list)}}
    return out


def project_row(s: Session, p: Project) -> dict[str, Any]:
    """A project as the console's project screen reads it: its `context.ceo_reviews` are the review rounds, in order."""
    out = row(p)
    if reviews := review_rounds.reviews(s, p.id):
        out["context"] = {**out.get("context", {}), "ceo_reviews": [
            _judged({"round": r.plan_round, "assessment": r.assessment, "action": r.action, "unmet": r.unmet})
            for r in reviews]}
    return out


def _judged(entry):
    if not isinstance(entry, dict) or not isinstance(entry.get("assessment"), str):
        return entry
    return {**entry, "assessment": owner_text.detail(entry["assessment"])}
