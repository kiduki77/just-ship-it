"""What a recalled role answers from (design section 8): the records kept for one task or build, numbered, and nothing
else. Every record is a database row, so the bundle is the same on day 3 and day 300: the transcripts in `var/calls`
are never read, and `prune_calls` cannot change it.

A record's id is made from its row's own id, never from its place in the list, so a citation keeps naming the same
record when later work adds more. Recorded text was written by models and by people during the work, so a reader
puts the bundle between data fences; secrets are already removed here, whoever reads it."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.agents import roles
from justshipit.contracts import secrets_scan
from justshipit.contracts.handoff import DEV_JOB_SETTLED, FIX_QUESTION_KEY, DevJobStatus
from justshipit.contracts.work import TERMINAL_TASK_STATUSES, TaskStatus
from justshipit.db.models import AgentRequest, Crew, CrewTurn, DevJob, Evidence, LLMCallRecord, Spec, Task
from justshipit.i18n import msg
from justshipit.i18n.glossary import role as role_name
from justshipit.staffing.crew import SubjectKind

_ERROR_CHARS = 300

# What no record holds, said once in every bundle so the role can name it instead of guessing.
_NOT_KEPT = {
    SubjectKind.TASK: ["the prompts the role was given", "the model's raw output beyond what each turn keeps",
                       "the web pages and files the role read", "anything decided outside these records"],
    SubjectKind.BUILD: ["the prompts the role was given", "the model's raw output beyond what each turn keeps",
                        "the code and its diff (only the commits the steps name)",
                        "the spec's documents, its criteria among them (the acceptance turns say how each was judged)",
                        "anything decided outside these records"],
}


class RecordKind(StrEnum):
    ASSIGNMENT = "assignment"
    CREW = "crew"
    TURN = "turn"
    EVIDENCE = "evidence"
    REQUEST = "request"
    LEDGER = "ledger"
    NOT_KEPT = "not_kept"


@dataclass(frozen=True)
class Record:
    id: str
    kind: RecordKind
    body: dict[str, Any]  # JSON values only, secrets removed


@dataclass(frozen=True)
class Bundle:
    kind: SubjectKind
    subject_id: int
    in_progress: bool
    roles: tuple[str, ...]  # who has a turn on record, as current role ids
    records: tuple[Record, ...]

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(r.id for r in self.records)

    def text(self) -> str:
        return "\n\n".join(f"[{r.id}] {r.kind.value}\n{json.dumps(r.body, ensure_ascii=False, sort_keys=True)}"
                           for r in self.records)


def recall_bundle(s: Session, kind: SubjectKind, subject_id: int) -> Bundle | None:
    """The records of a task or build, or None when it has no crew: nothing did work on it, so nobody can be asked."""
    crew = s.scalar(select(Crew).where(Crew.subject_kind == kind.value, Crew.subject_id == subject_id))
    if crew is None:
        return None
    if kind is SubjectKind.TASK:
        t = s.get(Task, subject_id)
        head, in_progress = _task(t), TaskStatus(t.status) not in TERMINAL_TASK_STATUSES
        evidence = s.scalars(select(Evidence).where(Evidence.task_id == subject_id).order_by(Evidence.id))
        asked = select(AgentRequest).where(AgentRequest.task_id == subject_id)
        calls = select(LLMCallRecord).where(LLMCallRecord.task_id == subject_id)
    else:
        job = s.get(DevJob, subject_id)
        head, in_progress = _build(job, s.get(Spec, job.spec_id)), DevJobStatus(job.status) not in DEV_JOB_SETTLED
        evidence = ()
        params = AgentRequest.action_params
        asked = select(AgentRequest).where((params["dev_job_id"].as_integer() == subject_id)
                                           | (params[FIX_QUESTION_KEY].as_integer() == subject_id))
        calls = select(LLMCallRecord).where(LLMCallRecord.dev_job_id == subject_id)
    turns = list(s.scalars(select(CrewTurn).where(CrewTurn.crew_id == crew.id).order_by(CrewTurn.id)))
    records = [
        head,
        Record(f"crew-{crew.id}", RecordKind.CREW, _crew(crew)),
        *(Record(f"turn-{x.id}", RecordKind.TURN, _turn(x)) for x in turns),
        *(Record(f"E-{e.id}", RecordKind.EVIDENCE, _evidence(e)) for e in evidence),
        *(Record(f"REQ-{r.id}", RecordKind.REQUEST, _request(r)) for r in s.scalars(asked.order_by(AgentRequest.id))),
        *(Record(f"call-{c.id}", RecordKind.LEDGER, _call(c)) for c in s.scalars(calls.order_by(LLMCallRecord.id))),
        Record("not-kept", RecordKind.NOT_KEPT, {"no record holds": _NOT_KEPT[kind]}),
    ]
    who = tuple(dict.fromkeys(roles.current(x.role) for x in turns))
    return Bundle(kind, subject_id, in_progress, who,
                  tuple(Record(r.id, r.kind, _clean(r.body)) for r in records))


def _at(v: datetime | None) -> str | None:
    return v.isoformat() if v is not None else None


def _task(t: Task) -> Record:
    review = t.review or {}
    return Record(f"T-{t.id}", RecordKind.ASSIGNMENT, {
        "task": t.key, "title": t.title, "objective": t.objective, "acceptance criteria": t.acceptance_criteria,
        "status": t.status, "origin": t.origin, "started": _at(t.started_at), "finished": _at(t.finished_at),
        "verdict": review.get("verdict"), "outcome": review.get("outcome"), "error": t.error})


def _build(job: DevJob, spec: Spec) -> Record:
    """The build and its steps: what each step said, and the commit a review step read. The steps are the build's own
    record of each round, so they are read here, not copied into turns."""
    steps = [{"at": x.get("at"), "status": x.get("status"), "summary": x.get("summary"),
              **({"commit": c} if (c := (x.get("data") or {}).get("commit")) else {})} for x in job.steps or []]
    return Record(f"J-{job.id}", RecordKind.ASSIGNMENT, {
        "build": f"J-{job.id}", "spec": spec.spec_key, "title": spec.title, "status": job.status,
        "attempt": job.attempt, "branch": job.branch, "error": job.error, "steps": steps})


def _crew(crew: Crew) -> dict:
    """The placement as the Staffing Agency Manager recorded it, each reason with its sentence."""
    def said(r: dict) -> dict:
        words = {k: role_name(str(v), "en") if k in ("owner", "checker") else v for k, v in r["facts"].items()}
        return {"rule": r["rule"], "facts": r["facts"], "says": msg(f"staffing.why.{r['rule']}", "en", **words)}
    return {"family": crew.family, "slots": crew.slots, "why": [said(r) for r in crew.why],
            "amendments": [{"slot": a["slot"], "why": said(a["why"]), "at": a["at"]} for a in crew.amendments],
            "facts": crew.facts, "rules version": crew.rules_version, "assembled": _at(crew.assembled_at)}


def _turn(x: CrewTurn) -> dict:
    return {"role": roles.current(x.role), "duty": x.duty, "round": x.round, "iteration": x.iteration,
            "shadow check": x.shadow, "at": _at(x.at), "call": x.call_id, "record": x.record}


def _evidence(e: Evidence) -> dict:
    return {"by": roles.current(e.agent), "claim": e.claim, "kind": e.kind, "source": e.source_url,
            "source title": e.source_title, "quote": e.quote, "published": e.published_at,
            "confidence": e.confidence, "verification": e.verification_status, "verification note": e.verification_note,
            "superseded by": e.superseded_by}


def _request(r: AgentRequest) -> dict:
    return {"by": roles.current(r.from_agent), "type": r.type, "title": r.title, "summary": r.summary,
            "rationale": r.rationale, "status": r.status, "asked": _at(r.created_at), "resolved": _at(r.resolved_at)}


def _call(c: LLMCallRecord) -> dict:
    """A ledger row without its transcript path: the transcript is pruned, the row is not."""
    return {"role": roles.current(c.agent), "purpose": c.purpose, "attempt": c.attempt, "model": c.model,
            "status": c.status, "duration ms": c.duration_ms, "call": c.call_id, "at": _at(c.created_at),
            # scrubbed before it is clipped: a token cut short no longer matches the scrub
            "error": secrets_scan.scrub(c.error or "", _marker)[0][:_ERROR_CHARS] or None}


def _marker(kind: str) -> str:
    return msg("chat.scrub.marker", "en", what=msg(f"chat.secret.{kind}", "en"))


def _clean(v: Any) -> Any:
    """`v` with every secret in its text replaced by a marker, whichever record carried it."""
    if isinstance(v, str):
        return secrets_scan.scrub(v, _marker)[0]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_clean(x) for x in v]
    return v
