from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from justshipit.capability import factory
from justshipit.contracts.skills import SkillStatus
from justshipit.db.models import ActivityEvent, Job, Skill
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import activity, app_settings, queue, registry

router = APIRouter()


def _row(o) -> dict:
    return {c.name: getattr(o, c.name) for c in o.__table__.columns}


def _turned_off(s) -> dict[int, datetime]:
    """When the owner turned each skill off: the owner's own change of a skill to DEPRECATED, as the log keeps it. A
    skill a newer version replaced was retired by the factory, not by the owner."""
    q = (select(ActivityEvent.entity_id, ActivityEvent.at)
         .where(ActivityEvent.entity == "skill", ActivityEvent.verb == "status", ActivityEvent.actor == "owner",
                ActivityEvent.data["to"].astext == SkillStatus.DEPRECATED.value)
         .order_by(ActivityEvent.id))
    return dict(s.execute(q).all())


@router.get("/skills")
def list_skills() -> dict:
    with session_scope() as s:
        skills = []
        off = _turned_off(s)
        for k in s.scalars(select(Skill).order_by(Skill.id.desc())):
            d = _row(k)
            d["turned_off_at"] = off.get(k.id) if k.status == SkillStatus.DEPRECATED.value else None
            try:
                d["content"] = registry.read(f"{k.registry_path}/SKILL.md") if k.registry_path else None
            except OSError:
                d["content"] = None
            skills.append(d)
    return {"skills": skills, "gaps": factory.gaps()}


@router.post("/skills/run-factory")
def run_factory() -> dict:
    with session_scope() as s:
        pending = s.scalar(select(Job.id).where(Job.kind == "skill_factory", queue.in_flight()))
        if pending is not None:
            raise HTTPException(409, msg("factory.already_queued", app_settings.language()))
        job = queue.enqueue(s, "skill_factory", {}, max_attempts=1, by_owner=True)
        s.flush()
        return {"job_id": job.id}


@router.post("/skills/{skill_id}/turn-off")
def turn_off_skill(skill_id: int) -> dict:
    """The owner turns a skill that is on off: its agents stop reading it from their next step, the record keeps it as
    retired, and the factory does not propose the same skill again. Only a skill that is on can be turned off."""
    lang = app_settings.language()
    with activity.as_actor("owner"), session_scope() as s:
        sk = s.get(Skill, skill_id, with_for_update=True)
        if sk is None:
            raise HTTPException(404, msg("factory.no_skill", lang, id=skill_id))
        if sk.status != SkillStatus.ACTIVE.value:
            raise HTTPException(409, msg("factory.not_on", lang, name=sk.name, version=sk.version))
        sk.status = SkillStatus.DEPRECATED.value
        return {"id": sk.id, "status": sk.status}
