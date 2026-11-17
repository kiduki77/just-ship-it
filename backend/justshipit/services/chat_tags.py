"""What a team chat conversation is about: the subject on the owner's screen, as a table of rules by kind.

A subject is `(kind, ref)`, as a screen names it (`request:31`, `page:engineering`). Each kind's rule says what owns the
work behind it (`canonical`: a merge card is about its build, so every merge card of one build is one conversation), who
answers for it (`owner_agent`), what the dock shows about it (`facts`, data the console words), where its cost goes
(`scope`), what work it takes from chat (`work`), and what records a reply reads about it (`records`). A new kind is
one row here and one entry in the console's route table (web/src/chat/routes.ts). Nothing else branches on the kind.

`TASK_STATE` reads a chat task's state from the record of the work itself (a fix round on the build), so the task card
and the Engineering screen can never disagree. `allowed` is the one rule for what an agent's reply may cause.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from justshipit.i18n import Lang

from justshipit.contracts.handoff import FIX_QUESTION_KEY, DevJobStatus
from justshipit.db.models import CHAT_TAG_KINDS, AgentMessage, AgentRequest, Business, DevJob, OwnerTask, Plan, Project, Spec, Task
from justshipit.services import work_path

TagKind = Literal["general", "business", "project", "task", "request", "owner_task", "plan", "spec", "dev_job", "page"]
Intent = Literal["answer", "work", "escalate", "handoff", "decision"]

# The pages a conversation can be about, by the console's route table. Each is answered by the CEO but Engineering.
PAGES = ("home", "my_tasks", "business", "new_business", "kickoff", "inbox", "tasks", "projects", "agents", "audit",
         "crews", "engineering", "skills", "routines", "settings")
_PAGE_AGENT = {"engineering": "engineer"}


class UnknownSubject(LookupError):
    """A subject that names no record: its kind is not in the table, or the record it names is gone."""


@dataclass(frozen=True)
class Scope:
    project_id: int | None
    business_id: int | None


def _int(ref: str) -> int:
    try:
        return int(ref)
    except ValueError:
        raise UnknownSubject(ref) from None


def _get(s: Session, model, ref: str):
    row = s.get(model, _int(ref))
    if row is None:
        raise UnknownSubject(f"{model.__tablename__}:{ref}")
    return row


def _project_scope(s: Session, project_id: int | None, business_id: int | None = None) -> Scope:
    p = s.get(Project, project_id) if project_id is not None else None
    return Scope(project_id, p.business_id if p is not None else business_id)


def _job(s: Session, ref: str) -> tuple[DevJob, Spec]:
    job = _get(s, DevJob, ref)
    return job, s.get(Spec, job.spec_id)


def _merge_job(req: AgentRequest) -> int | None:
    """The build a merge card, or a card asking what to fix in a build, is about."""
    params = req.action_params or {}
    job = params.get("dev_job_id") if req.action_kind == "dev.merge" else params.get(FIX_QUESTION_KEY)
    return job if isinstance(job, int) else None


def _repository(spec: Spec) -> str | None:
    return ((spec.handoff or {}).get("target") or {}).get("repository") or spec.target_repository


@dataclass(frozen=True)
class TagRule:
    canonical: Callable[[Session, str], tuple[TagKind, str]]
    owner_agent: Callable[[Session, str], str]
    facts: Callable[[Session, str], list[dict]]
    scope: Callable[[Session, str, int | None], Scope]
    work: Callable[[Session, str], tuple[str, ...]] = lambda s, ref: ()
    # The subject's records for a reply's prompt, each already between fences, given the owner's lines it answers.
    records: Callable[[Session, str, Lang, list[AgentMessage]], list[str]] = lambda s, ref, lang, asked: []


def _same(kind: TagKind) -> Callable[[Session, str], tuple[TagKind, str]]:
    return lambda s, ref: (kind, ref)


def _project_manager(s: Session, ref: str) -> str:
    return "project_manager"


def _request_canonical(s: Session, ref: str) -> tuple[TagKind, str]:
    req = _get(s, AgentRequest, ref)
    return ("dev_job", str(job)) if (job := _merge_job(req)) is not None else ("request", ref)


def _job_facts(s: Session, ref: str) -> list[dict]:
    from justshipit.engineering import revise  # revise imports the worker, which imports most services
    job, spec = _job(s, ref)
    waiting = s.query(AgentRequest.id).filter(
        AgentRequest.status == "PENDING", AgentRequest.action_kind == "dev.merge",
        AgentRequest.action_params["dev_job_id"].as_integer() == job.id).order_by(AgentRequest.id.desc()).first()
    return [*([{"k": "merge", "ref": f"REQ-{waiting[0]}"}] if waiting else []),
            {"k": "build", "ref": f"J-{job.id}"},
            *([{"k": "product", "v": repo}] if (repo := _repository(spec)) else []),
            *([{"k": "branch", "v": job.branch}] if job.branch else []),
            *([{"k": "fix", "n": fix["n"]}] if (fix := revise.latest(job)) and fix.get("asked_in") else [])]


def _job_work(s: Session, ref: str) -> tuple[str, ...]:
    """A fix round, on a delivered build with nothing else on its way (revise.busy)."""
    from justshipit.engineering import revise  # revise imports the worker, which imports most services
    job, _ = _job(s, ref)
    return ("dev_fix",) if job.status == DevJobStatus.DELIVERED.value and not revise.busy(s, job) else ()


def _job_records(s: Session, ref: str, lang: Lang, asked: list[AgentMessage]) -> list[str]:
    from justshipit.services import chat_reply  # chat_reply reads this table
    job = _get(s, DevJob, ref)
    return [chat_reply.build_records(s, job, lang), *chat_reply.step_records(job, asked)]


def _request_records(s: Session, ref: str, lang: Lang, asked: list[AgentMessage]) -> list[str]:
    from justshipit.services import chat_reply  # chat_reply reads this table
    return [chat_reply.card_records(s, _get(s, AgentRequest, ref))]


def _job_scope(s: Session, ref: str, business_id: int | None) -> Scope:
    _, spec = _job(s, ref)
    return _project_scope(s, spec.project_id)


def _project_facts(s: Session, ref: str) -> list[dict]:
    p = _get(s, Project, ref)
    return [{"k": "project", "ref": f"P-{p.id}", "v": p.name}]


def _task_facts(s: Session, ref: str) -> list[dict]:
    t = _get(s, Task, ref)
    return [{"k": "task", "ref": f"T-{t.id}", "v": t.title}]


def _request_facts(s: Session, ref: str) -> list[dict]:
    """The card, the project it belongs to, and the step that filed it. A step the records do not hold is `unknown`,
    which the dock words as not recorded."""
    req = _get(s, AgentRequest, ref)
    out = [{"k": "request", "ref": f"REQ-{req.id}", "v": req.title}]
    if (ctx := work_path.card_or_none(s, req)) is None:
        return out
    if project := next((x for x in ctx.chain if x.kind == "project"), None):
        out.append({"k": "project", "ref": project.ref, "v": project.vars["name"]})
    filed = ctx.chain[-2]  # the step before "this": the one that filed the card, or the unknown origin
    return [*out, {"k": "filed", "known": "unknown"} if filed.known == "unknown"
            else {"k": "filed", "key": filed.label_key, "vars": filed.vars, "known": filed.known}]


def _page(ref: str) -> str:
    if ref not in PAGES:
        raise UnknownSubject(f"page:{ref}")
    return ref


TAGS: dict[TagKind, TagRule] = {
    "general": TagRule(_same("general"), _project_manager, lambda s, ref: [{"k": "general"}], lambda s, ref, b: Scope(None, None)),
    "business": TagRule(_same("business"), _project_manager,
                        lambda s, ref: [{"k": "business", "ref": f"B-{ref}", "v": _get(s, Business, ref).name}],
                        lambda s, ref, b: Scope(None, _get(s, Business, ref).id)),
    "project": TagRule(_same("project"), _project_manager, _project_facts,
                       lambda s, ref, b: _project_scope(s, _get(s, Project, ref).id)),
    "task": TagRule(_same("task"), lambda s, ref: _get(s, Task, ref).owner, _task_facts,
                    lambda s, ref, b: _project_scope(s, _get(s, Task, ref).project_id)),
    "request": TagRule(_request_canonical, lambda s, ref: _get(s, AgentRequest, ref).from_agent, _request_facts,
                       lambda s, ref, b: _project_scope(s, _get(s, AgentRequest, ref).project_id),
                       records=_request_records),
    "owner_task": TagRule(_same("owner_task"), _project_manager,
                          lambda s, ref: [{"k": "owner_task", "ref": f"OT-{ref}", "v": _get(s, OwnerTask, ref).title}],
                          lambda s, ref, b: (lambda ot: _project_scope(s, ot.project_id, ot.business_id))(
                              _get(s, OwnerTask, ref))),
    "plan": TagRule(_same("plan"), lambda s, ref: "product",
                    lambda s, ref: [{"k": "plan", "v": _get(s, Plan, ref).title}],
                    lambda s, ref, b: _project_scope(s, _get(s, Plan, ref).project_id)),
    "spec": TagRule(_same("spec"), lambda s, ref: "product",
                    lambda s, ref: [{"k": "spec", "ref": _get(s, Spec, ref).spec_key, "v": _get(s, Spec, ref).title}],
                    lambda s, ref, b: _project_scope(s, _get(s, Spec, ref).project_id)),
    "dev_job": TagRule(_same("dev_job"), lambda s, ref: (_job(s, ref), "engineer")[1], _job_facts, _job_scope, _job_work,
                       _job_records),
    "page": TagRule(lambda s, ref: ("page", _page(ref)), lambda s, ref: _PAGE_AGENT.get(_page(ref), "project_manager"),
                    lambda s, ref: [{"k": "page", "v": _page(ref)}], lambda s, ref, b: Scope(None, b)),
}
assert set(TAGS) == set(CHAT_TAG_KINDS), "every subject kind the table allows has its rule"


@dataclass(frozen=True)
class Subject:
    """A canonical subject and where it belongs: the thread key `(business_id, kind, ref)`."""
    kind: TagKind
    ref: str
    scope: Scope

    @property
    def business_id(self) -> int | None:
        return self.scope.business_id


def resolve(s: Session, kind: str, ref: str | None, business_id: int | None) -> Subject:
    """The subject a screen names, as the work behind it: a merge card becomes its build. `business_id` is the
    business the screen shows, which places a page or a general conversation. General is the company's own."""
    if kind not in TAGS:
        raise UnknownSubject(kind)
    if business_id is not None and s.get(Business, business_id) is None:
        raise UnknownSubject(f"business:{business_id}")
    if kind == "general":
        return Subject("general", "", Scope(None, None))
    kind, ref = TAGS[kind].canonical(s, ref or "")
    return Subject(kind, ref, TAGS[kind].scope(s, ref, business_id))


def owner_agent(s: Session, subject: Subject) -> str:
    return TAGS[subject.kind].owner_agent(s, subject.ref)


def facts(s: Session, kind: str, ref: str) -> list[dict]:
    try:
        return TAGS[kind].facts(s, ref)
    except UnknownSubject:
        return []


def work(s: Session, subject: Subject) -> tuple[str, ...]:
    return TAGS[subject.kind].work(s, subject.ref)


# ------------------------------------------------------------------ what a reply may cause

def allowed(agent: str, intent: str, kind: str, *, hop: int, max_hops: int, recall: bool = False) -> bool:
    """Whether a reply's intent may happen, whatever the model asked for. Answering is always allowed. A recalled
    role (`recall`) only answers: it explains past work and never acts, in a build's conversation too. Otherwise work
    is a fix round on a delivered build, by its engineer. Any agent may escalate to the Project Manager, and only the
    Project Manager hands off, each while the message has replies left (`max_hops`). Any agent may file a decision card
    the owner still decides."""
    if recall:
        return intent == "answer"
    if intent in ("answer", "decision"):
        return True
    if intent == "work":
        return agent == "engineer" and kind == "dev_job"
    if hop >= max_hops:
        return False
    if intent == "escalate":
        return agent != "project_manager"
    if intent == "handoff":
        return agent == "project_manager"
    return False


# ------------------------------------------------------------------ a chat task's state, read from its work

TaskState = Literal["received", "budget", "running", "checking", "done", "needs_fixing", "stopped", "cancelled"]


def _dev_fix_state(s: Session, job: DevJob, n: int) -> dict:
    """{"state", "who": [role keys], "progress"?: {"done", "total"}, "request_id"?} of fix round `n` of the build."""
    from justshipit.engineering import revise  # revise imports the worker, which imports most services
    fix = next((f for f in revise.rounds(job) if f["n"] == n), None)
    if fix is None or fix["state"] == revise.State.QUEUED:
        return {"state": "received", "who": ["engineer"]}
    if fix["state"] == revise.State.WAITING_BUDGET:
        return {"state": "budget", "who": ["engineer"]}
    if fix["state"] == revise.State.STOPPED:
        return {"state": "cancelled" if fix.get("cancelled") else "stopped", "who": ["engineer"]}
    if fix["state"] == revise.State.RUNNING:
        done = (job.result or {}).get("progress") or {}
        progress = ({"done": done["done"], "total": done["total"]}
                    if isinstance(done.get("done"), int) and isinstance(done.get("total"), int)
                    and str(done.get("at") or "") >= str(fix.get("at") or "") else None)
        who = ["eng_reviewer"] if job.status == DevJobStatus.REVIEWING.value else ["engineer"]
        return {"state": "running", "who": who, **({"progress": progress} if progress else {})}
    later = revise.rounds(job)[-1]["n"] != n  # a later round moved the build on: this one's check is behind it
    if (job.result or {}).get("failed") and not later:
        return {"state": "needs_fixing", "who": ["product"]}
    if "acceptance" not in (job.result or {}) and not later and job.status == DevJobStatus.DELIVERED.value:
        return {"state": "checking", "who": ["product"]}
    merge = s.query(AgentRequest.id).filter(
        AgentRequest.action_kind == "dev.merge", AgentRequest.action_params["dev_job_id"].as_integer() == job.id,
        AgentRequest.created_at >= datetime.fromisoformat(fix["at"])).order_by(AgentRequest.id.desc()).first()
    return {"state": "done", "who": ["engineer", "eng_reviewer", "product"],
            **({"request_id": merge[0]} if merge else {})}


TASK_STATE: dict[str, Callable[[Session, DevJob, int], dict]] = {"dev_fix": _dev_fix_state}


def task_state(s: Session, work: dict) -> dict | None:
    """The state of the work a task message names (`{"kind", "dev_job_id", "n"}`), or None before it has any."""
    if work.get("kind") not in TASK_STATE or not isinstance(work.get("n"), int):
        return None
    job = s.get(DevJob, work.get("dev_job_id"))
    return TASK_STATE[work["kind"]](s, job, work["n"]) if job is not None else None
