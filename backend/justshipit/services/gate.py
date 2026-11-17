"""Which job may run, and on whose approval.

The owner approves, and what the owner approved runs by itself. Nothing that spends model usage or puts work in the
owner's to-do list runs without that approval. `POLICY` is the one list of job kinds: each row says why the kind may
run. A kind with no row is denied: `queue.enqueue` raises, the worker refuses to start, and the claim matches nothing.

Approval comes three ways. The owner pressed Start on a stage whose card listed the work (`STAGE_WORK`, and the
recurring kinds the stage names in `allows_kinds`). The owner clicked for this one thing (`ASKED`, and any kind enqueued
`by_owner`). Or the job is how a cycle gets to the owner's next decision (`CYCLE`). `FREE` kinds spend nothing and
make no to-dos, and run_agent fails a FREE job that tries.

The build of a spec the owner approved, its acceptance check and the usable check after a merge follow that approval
(`approved`): with no stage running they run by themselves, and while one runs they belong to it.

The guarantee is the claim (`queue.claim`): a job stamped `stage` starts only while its stage is running. Enqueue stamps
every job through `admit`; `queue.check_lease` stops a running job whose stage stopped; `executor.run_agent` refuses a
FREE job and a model call made outside any job. A leaf: it reads models and `scope`, and imports no service.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from justshipit.db.models import OWNER_TASK_KINDS, Business, BusinessBrief, Cycle, CycleStage, Project
from justshipit.services import scope


class Gate(StrEnum):
    STAGE_WORK = "stage_work"
    STAGE_RECURRING = "stage_recurring"
    ASKED = "asked"
    CYCLE = "cycle"
    FREE = "free"


class UndeclaredJobKind(RuntimeError):
    """A job kind with no row in POLICY. It is denied until someone writes its row and says why it may run."""


class GateViolation(RuntimeError):
    """A model call with no approval behind it: made outside any job, and not marked as the owner's own (`asked`)."""


@dataclass(frozen=True)
class Rule:
    gate: Gate
    why: str
    agent: str | None = None   # the agent a job's action card names; None when tasks and builds carry the cards
    scope: Literal["business", "company"] = "business"


_RUN = "the project's graph run: the Project Manager's plan, the agents' tasks and reviews, and the to-dos the plan writes"
_BUILD = "a build of a spec the owner approved: the engineer's and reviewers' model calls"

POLICY: dict[str, Rule] = {
    # What a stage the owner started does. With no running stage it is parked and joins the next stage that starts.
    "run_project": Rule(Gate.STAGE_WORK, _RUN),
    "resume_project": Rule(Gate.STAGE_WORK, _RUN),
    "retry_project": Rule(Gate.STAGE_WORK, _RUN),
    "reassess_project": Rule(Gate.STAGE_WORK, "starts a child project for an approved reassessment; it runs in the stage"),
    "write_plan": Rule(Gate.STAGE_WORK, "the product agent writes the product plan of a build the run asks for; the "
                                        "owner approves it before a spec is written", "product"),
    "redraft_plan": Rule(Gate.STAGE_WORK, "the product agent writes a product plan again from the owner's note",
                         "product"),
    "write_spec": Rule(Gate.STAGE_WORK, "the product agent drafts the spec of an approved development request", "product"),
    "redraft_spec": Rule(Gate.STAGE_WORK, "the product agent writes a spec again from the owner's notes on its "
                                          "screens", "product"),
    "spec_approved": Rule(Gate.STAGE_WORK, "starts the build of a spec the owner approved"),
    "spec_questions": Rule(Gate.STAGE_WORK, "the build goes on once the spec's questions are answered"),
    "dev_job": Rule(Gate.STAGE_WORK, _BUILD),
    "dev_resume": Rule(Gate.STAGE_WORK, _BUILD),
    "dev_deliver": Rule(Gate.STAGE_WORK, _BUILD),
    "dev_fix": Rule(Gate.STAGE_WORK, _BUILD),
    "dev_fix_answered": Rule(Gate.STAGE_WORK, _BUILD),
    "acceptance": Rule(Gate.STAGE_WORK, "the product agent checks a delivered build against its spec", "product"),
    "dev_merged": Rule(Gate.STAGE_WORK, "after a merge: asks whether the merged change is usable"),
    "audit_task": Rule(Gate.STAGE_WORK, "a shadow check of a task that ran alone: one review call that gates nothing, "
                                        "paid from the project's audit budget"),
    "bet_retro": Rule(Gate.STAGE_WORK, "the Project Manager's retro of an experiment; in a cycle its next-step ideas go into the "
                                       "retro for the next proposal, not into new experiments or cards", "project_manager"),
    # Background work that runs only while the running stage lists it, so the owner read it on the card before Start.
    "find_people": Rule(Gate.STAGE_RECURRING, "searches for people and may make contact to-dos", "research"),
    "find_threads": Rule(Gate.STAGE_RECURRING, "searches public threads and makes reply to-dos", "research"),
    "promotion_week": Rule(Gate.STAGE_RECURRING, "writes the week's posts and makes post to-dos", "marketing"),
    "github_collect": Rule(Gate.STAGE_RECURRING, "reads the repo's GitHub numbers with the owner's token on a schedule"),
    "kpi_review": Rule(Gate.STAGE_RECURRING, "the Project Manager reviews a project's KPI trend and may file cards", "project_manager"),
    "evidence_watch": Rule(Gate.STAGE_RECURRING, "re-checks a finished project's evidence and may file cards", "research"),
    "routine_run": Rule(Gate.STAGE_RECURRING, "one round of a routine: a new project that runs in the stage"),
    "portfolio_review": Rule(Gate.STAGE_RECURRING, "the Project Manager's review of every business", "project_manager", scope="company"),
    "skill_factory": Rule(Gate.STAGE_RECURRING, "drafts and tests skills from repeated work", "skill_builder",
                          scope="company"),
    # One bounded call the owner asked for with a click or a message.
    "kickoff_turn": Rule(Gate.ASKED, "the Project Manager's next turn of the kickoff chat the owner is having", "project_manager"),
    "product_report": Rule(Gate.ASKED, "reads the product the owner brought, or asked to read again", "product"),
    "triage_idea": Rule(Gate.ASKED, "the Project Manager reads an idea the owner just dropped in", "project_manager"),
    "chat_reply": Rule(Gate.ASKED, "an agent answers a message the owner wrote in team chat"),
    "idea_review": Rule(Gate.ASKED, "one lens of the idea panel the owner asked for", "critic"),
    "realign_project": Rule(Gate.ASKED, "the Project Manager replans after the owner answered, wrote a directive or did a to-do",
                            "project_manager"),
    "dev_merge": Rule(Gate.ASKED, "merges a build the owner approved on its merge card"),
    "dev_merge_declined": Rule(Gate.ASKED, "settles a build whose merge the owner declined"),
    "skill_activate": Rule(Gate.ASKED, "activates a skill the owner approved on its card"),
    "raise_budget": Rule(Gate.ASKED, "adds the amount of a budget card the owner approved to the project's budget; "
                                     "no model call"),
    "derive_door_rules": Rule(Gate.ASKED, "one call caused by the owner's plan approval; writes door rules, no to-dos",
                              "project_manager"),
    "profile_infer": Rule(Gate.ASKED, "one call caused by the owner's answer about customers, a plan approval, or "
                                      "`ship profile backfill`; writes the customer description, no to-dos", "project_manager"),
    "kit_prepare": Rule(Gate.ASKED, "one call caused by the owner's click on the First 10 kit: writes texts for the owner "
                                    "to copy; no to-dos, and nothing is sent", "marketing"),
    # How a cycle reaches the owner's next decision; gating them on a stage would keep the gate shut for good.
    "cycle_propose": Rule(Gate.CYCLE, "the Project Manager drafts the cycle the owner's plan approval asked for", "project_manager"),
    "cycle_revise": Rule(Gate.CYCLE, "the Project Manager redraws the cycle after the owner said what to change", "project_manager"),
    "cycle_summarize": Rule(Gate.CYCLE, "the Project Manager's summary that opens the gate at a stage's end", "project_manager"),
    "cycle_retro": Rule(Gate.CYCLE, "the Project Manager's look back once the owner finished the cycle", "project_manager"),
    # Spends nothing and makes no to-do; run_agent fails one of these that tries.
    "notify": Rule(Gate.FREE, "delivers a message to the owner; no model call"),
    "daily_briefing": Rule(Gate.FREE, "sends the day's summary of what already happened; no model call"),
    "backup": Rule(Gate.FREE, "dumps the database"),
    "retention": Rule(Gate.FREE, "deletes what passed its keep-by date"),
    "bet_sweep": Rule(Gate.FREE, "moves experiments whose window or run ended; its retros are stage work"),
    "execute_action": Rule(Gate.FREE, "sends what the owner already approved on a card; no model call"),
    "post_reactions": Rule(Gate.FREE, "reads reactions to posts the owner made by hand; no model call"),
}

RECURRING_KINDS: tuple[str, ...] = tuple(k for k, r in POLICY.items() if r.gate is Gate.STAGE_RECURRING)
OWNER_WORK_KINDS: tuple[str, ...] = OWNER_TASK_KINDS


def rule(kind: str) -> Rule:
    try:
        return POLICY[kind]
    except KeyError:
        raise UndeclaredJobKind(f"job kind {kind!r} is not declared in gate.POLICY") from None


def check_handlers(kinds: Mapping[str, Any]) -> None:
    """The worker refuses to start with a handler that has no policy row: that kind would run undeclared."""
    if missing := sorted(set(kinds) - set(POLICY)):
        raise RuntimeError(f"job kinds with a handler and no gate.POLICY row: {', '.join(missing)}")


# ------------------------------------------------------------------ the one query for "which stage is running"

def running_stage(s: Session, business_id: int | None) -> CycleStage | None:
    """The stage that admits new work. Only `running` does: a `closing` stage lets what already runs finish."""
    if business_id is None:
        return None
    return s.scalar(select(CycleStage).where(CycleStage.business_id == business_id, CycleStage.status == "running"))


def stage_is_open(s: Session, stage_id: int | None) -> bool:
    """Whether work already running under this stage may go on: the stage is running or closing."""
    if stage_id is None:
        return False
    return s.scalar(select(CycleStage.status).where(CycleStage.id == stage_id)) in ("running", "closing")


def managed(s: Session, business_id: int | None) -> bool:
    """Whether the business runs in cycles: an approved plan or any cycle. The others (a CLI project's business, a
    [TEST] business, one still in its kickoff) have no stage to approve with, so their stage work runs as asked."""
    if business_id is None:
        return False
    b = s.get(Business, business_id)
    if b is None or b.name.startswith("[TEST]"):
        return False
    approved = exists().where(BusinessBrief.business_id == business_id, BusinessBrief.approved_at.is_not(None))
    cycled = exists().where(Cycle.business_id == business_id)
    return bool(s.scalar(select(or_(approved, cycled))))


def owner_work_allowed(s: Session, business_id: int | None, kind: str, *, by_owner: bool) -> bool:
    """Whether a to-do of this kind may go in the owner's list. The owner's own click always may. One the company makes
    on its own (the plan, a finder, an automatic approval) needs the running stage to list its kind."""
    if by_owner or not managed(s, business_id):
        return True
    st = running_stage(s, business_id)
    return st is not None and kind in (st.allows_owner_work or [])


# ------------------------------------------------------------------ admission

Verdict = Literal["run", "park", "skip"]


@dataclass(frozen=True)
class Admission:
    verdict: Verdict
    gate: Literal["none", "direct", "stage"]
    stage_id: int | None
    business_id: int | None
    project_id: int | None = None


def _lists(s: Session, kind: str, r: Rule, business_id: int | None) -> CycleStage | None:
    """The running stage that names this recurring kind. A company-wide kind runs when any business's running stage
    names it, and its cost goes to the first such stage."""
    if r.scope == "company":
        for st in s.scalars(select(CycleStage).where(CycleStage.status == "running").order_by(CycleStage.id)):
            if kind in (st.allows_kinds or []):
                return st
        return None
    st = running_stage(s, business_id)
    return st if st is not None and kind in (st.allows_kinds or []) else None


def _cycle_stage(s: Session, payload: Mapping[str, Any]) -> int | None:
    if (sid := payload.get("stage_id")) is not None:
        return sid
    if (cid := payload.get("cycle_id")) is not None:
        return s.scalar(select(CycleStage.id).where(CycleStage.cycle_id == cid, CycleStage.status.in_(("running", "closing"))))
    return None


def admit(s: Session, kind: str, payload: Mapping[str, Any], *, by_owner: bool = False,
          approved: bool = False) -> Admission:
    """Where a new job stands: `run` now (stamped with who approved it), `park` until the owner starts a stage, or
    `skip` (a recurring kind no running stage lists: no catch-up later). Reads only.

    `by_owner` is the owner's own click (an approval, an answer, Retry, "find now"): it runs, stamped with the stage it
    belongs to for its cost. Only work the company starts on its own waits for a stage.

    `approved`: the work follows something the owner approved (the build of an approved spec, its acceptance check,
    the usable check after a merge the owner made). With no stage running the owner's approval is the approval, so it
    runs on its own. While a stage runs it belongs to that stage like any stage work, so stopping the stage stops it."""
    r = rule(kind)
    pid, bid = scope.owner_of(s, dict(payload))
    if r.gate is Gate.FREE:
        return Admission("run", "none", None, bid, pid)
    if r.gate is Gate.CYCLE:
        return Admission("run", "direct", _cycle_stage(s, payload), bid, pid)
    project = s.get(Project, pid) if pid is not None else None
    running = running_stage(s, bid)
    if r.gate is Gate.ASKED or by_owner:
        own = project.stage_id if project is not None and project.stage_id is not None else None
        return Admission("run", "direct", own or (running.id if running else None), bid, pid)
    if r.gate is Gate.STAGE_RECURRING:
        st = _lists(s, kind, r, bid)
        return Admission("run", "stage", st.id, bid, pid) if st else Admission("skip", "none", None, bid, pid)
    if not managed(s, bid):
        return Admission("run", "direct", None, bid, pid)
    if project is not None and project.stage_id is not None:
        own = s.get(CycleStage, project.stage_id)
        if own is not None and own.status == "stopped":
            return Admission("park", "stage", own.id, bid, pid)   # the owner's resume of that stage releases it
    if running is not None:
        return Admission("run", "stage", running.id, bid, pid)
    if approved:
        return Admission("run", "direct", project.stage_id if project is not None else None, bid, pid)
    return Admission("park", "stage", None, bid, pid)


# ------------------------------------------------------------------ model calls outside a job

_asked: ContextVar[str | None] = ContextVar("gate_asked", default=None)


@contextmanager
def asked(why: str) -> Iterator[None]:
    """Mark a model call made outside any worker job as one the owner asked for in person: a CLI command run by hand
    (`cli:<name>`), or a test (`test:<name>`). Greppable on purpose; run_agent refuses any other call outside a job."""
    token = _asked.set(why)
    try:
        yield
    finally:
        _asked.reset(token)


def asked_why() -> str | None:
    return _asked.get()
