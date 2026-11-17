"""Who built, reviewed and checked a build, from the model calls the ledger recorded for it (`llm_calls.agent` and the
model that actually answered). Every build has these calls, past ones included, so the merge card and the Engineering
screen can name the team without any new record."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.db.models import LLMCallRecord

# Which part each role's calls on a build are. The product agent's other calls on a build (a spec question) are not
# a check.
_PART = {("engineer", None): "built", ("eng_reviewer", None): "reviewed", ("product", "acceptance"): "checked"}


def _part(agent: str, purpose: str) -> str | None:
    return _PART.get((agent, None)) or _PART.get((agent, purpose))


def of_builds(s: Session, job_ids: list[int]) -> dict[int, dict]:
    """{job id: {"built", "reviewed", "checked"}}, each a list of {"agent", "model"} in the order they first worked."""
    out: dict[int, dict] = {j: {"built": [], "reviewed": [], "checked": []} for j in job_ids}
    if not job_ids:
        return out
    rows = s.execute(select(LLMCallRecord.dev_job_id, LLMCallRecord.agent, LLMCallRecord.purpose, LLMCallRecord.model)
                     .where(LLMCallRecord.dev_job_id.in_(job_ids), LLMCallRecord.status == "ok")
                     .order_by(LLMCallRecord.id))
    for job_id, agent, purpose, model in rows:
        if (part := _part(agent, purpose)) is None:
            continue
        seen = out[job_id][part]
        if {"agent": agent, "model": model} not in seen:
            seen.append({"agent": agent, "model": model})
    return out


def of_build(s: Session, job_id: int) -> dict:
    return of_builds(s, [job_id])[job_id]
