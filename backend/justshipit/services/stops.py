"""The owner's stops: the whole company at once, or one agent. The owner approves and what was approved runs by itself,
so the owner can also stop it at any moment and take nothing back by doing so.

A stop is a row in `stops` while it is in force: `company`, or `agent:<role key>` for one agent. Nothing is cancelled or
deleted. While the company is stopped the claim admits only `HOUSEKEEPING` jobs, a running job stops at its next lease
check and goes back to the queue with its attempt returned, and the scheduler skips its ticks. While an agent is stopped
its own kinds of job (`kind_agents`) are not claimed and stop at their next lease check; a run skips its tasks and
reviews and goes on with the rest, and work that reaches the agent anywhere else waits on it (`Job.waits_on`). Resuming
deletes the row; queued work then runs again. Both are idempotent, and only a change is written to the activity log.

A leaf like services/gate.py: the queue, the scheduler, the run and the console read these; it imports no service that
imports the queue.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, object_session

from justshipit import staffing
from justshipit.agents import roles
from justshipit.db.models import Job, Stop, Task
from justshipit.i18n import Lang, msg
from justshipit.i18n.glossary import role
from justshipit.services import activity, gate
from justshipit.settings import agents_config

COMPANY = "company"
_AGENT = "agent:"

# FREE kinds that still run while the company is stopped: they keep the books and reach the owner. Two FREE kinds reach
# the outside world, so an emergency stop holds them: sending what the owner approved on a card (`execute_action`) and
# reading the reactions to the owner's posts, which fetches pages from Hacker News and Reddit (`post_reactions`).
HOUSEKEEPING: frozenset[str] = (frozenset(k for k, r in gate.POLICY.items() if r.gate is gate.Gate.FREE)
                                - {"execute_action", "post_reactions"})

# A build is the engineer's work: its card names no agent (gate.POLICY), but stopping the engineer stops it.
BUILD_KINDS = ("dev_job", "dev_resume", "dev_deliver", "dev_fix", "dev_fix_answered")

# Task states in which the next step is the author's, and the one in which it is the reviewer's.
_AUTHOR_NEXT = ("PROPOSED", "READY", "REWORK")
_REVIEWER_NEXT = ("REVIEW",)


class UnknownAgent(LookupError):
    """The key names no agent in config/agents.yaml."""


@dataclass(frozen=True)
class Stops:
    """Every stop in force, read at once."""
    company: Stop | None
    agents: dict[str, Stop]

    def holds(self, agent: str | None) -> bool:
        return agent is not None and agent in self.agents

    def holds_kind(self, kind: str) -> bool:
        """Whether a job of this kind may not start now (the claim says the same in SQL, queue._not_stopped)."""
        return (self.company is not None and kind not in HOUSEKEEPING) or any(map(self.holds, kind_agents(kind)))


def read(s: Session) -> Stops:
    rows = list(s.scalars(select(Stop)))
    return Stops(next((r for r in rows if r.target == COMPANY), None),
                 {roles.current(r.target[len(_AGENT):]): r for r in rows if r.target.startswith(_AGENT)})


def view(stop: Stop | None) -> dict | None:
    """A stop as the console reads it."""
    return None if stop is None else {"at": stop.stopped_at, "by": stop.stopped_by, "reason": stop.reason}


def _engineers() -> tuple[str, ...]:
    return tuple(k for k, v in agents_config().items() if v.get("runtime") == "engineering")


def kind_agents(kind: str) -> tuple[str, ...]:
    """The agents whose stop holds a job of this kind before it starts and stops it at its next lease check: the agent
    its gate.POLICY row names, or the engineers for a build. A run names none: it skips the stopped agent's tasks."""
    if kind in BUILD_KINDS:
        return _engineers()
    r = gate.POLICY.get(kind)
    return (r.agent,) if r is not None and r.agent else ()


def held_kinds(agents: set[str] | dict) -> list[str]:
    """The job kinds a claim leaves alone while these agents are stopped."""
    return [k for k in gate.POLICY if any(a in agents for a in kind_agents(k))]


def job_held(stops: Stops, kind: str, gate_value: str) -> str | None:
    """Why a job claimed earlier must stop now: `company` (unless it is FREE, which spends nothing), the stopped agent
    the kind belongs to, or None when it may go on."""
    if stops.company is not None and gate_value != "none":
        return COMPANY
    return next((a for a in kind_agents(kind) if stops.holds(a)), None)


def waiting_on(t: Task, stops: Stops) -> str | None:
    """The stopped agent a task waits for: its author while the next step is the author's, its crew's checker while
    the result waits for review. None while it runs, once it is settled, and for a result no checker waits on."""
    if t.status in _AUTHOR_NEXT and stops.holds(t.owner):
        return t.owner
    if t.status in _REVIEWER_NEXT and stops.holds(reviewer := staffing.verifier_of(object_session(t), t)):
        return reviewer
    return None


def waiting_text(agent: str, lang: Lang) -> str:
    return msg("stop.waiting", lang, agent=role(agent, lang))


# ------------------------------------------------------------------ the owner's actions

def _owner_lang(lang: Lang | None) -> Lang:
    from justshipit.services import app_settings
    return lang or app_settings.language()


def _put(s: Session, target: str, reason: str | None, by: str) -> bool:
    """Put the stop in force; False when it already was (its time and reason stay those of the first stop)."""
    reason = (reason or "").strip()[:500] or None
    return s.scalar(insert(Stop).values(target=target, stopped_by=by, reason=reason)
                    .on_conflict_do_nothing(index_elements=[Stop.target]).returning(Stop.target)) is not None


def _lift(s: Session, *targets: str) -> bool:
    return s.scalar(delete(Stop).where(Stop.target.in_(targets)).returning(Stop.target)) is not None


def stop_company(s: Session, reason: str | None = None, *, by: str = "owner", lang: Lang | None = None) -> dict:
    lang = _owner_lang(lang)
    if _put(s, COMPANY, reason, by):
        activity.record(s, verb="stopped", entity="company", entity_id=None, project_id=None, actor=by,
                        summary=msg("stop.activity.company", lang) + (f": {reason.strip()}" if reason and reason.strip()
                                                                       else ""),
                        data={"reason": reason})
    return company_state(s)


def resume_company(s: Session, *, by: str = "owner", lang: Lang | None = None) -> dict:
    lang = _owner_lang(lang)
    if _lift(s, COMPANY):
        activity.record(s, verb="resumed", entity="company", entity_id=None, project_id=None, actor=by,
                        summary=msg("stop.activity.company_resumed", lang))
    return company_state(s)


def company_state(s: Session) -> dict:
    return {"stopped": view(read(s).company)}


def _agent(key: str) -> str:
    if key not in agents_config():
        raise UnknownAgent(key)
    return key


def stop_agent(s: Session, key: str, reason: str | None = None, *, by: str = "owner", lang: Lang | None = None) -> dict:
    lang = _owner_lang(lang)
    if _put(s, _AGENT + _agent(key), reason, by):
        activity.record(s, verb="stopped", entity="agent", entity_id=None, project_id=None, actor=by,
                        summary=msg("stop.activity.agent", lang, agent=role(key, lang))
                        + (f": {reason.strip()}" if reason and reason.strip() else ""),
                        data={"agent": key, "reason": reason})
    return agent_state(s, key)


def resume_agent(s: Session, key: str, *, by: str = "owner", lang: Lang | None = None) -> dict:
    """Lift the agent's stop. Every job that waits on a stopped agent looks again, since the work this resume frees may
    be any of theirs (a run waits on one agent while it has tasks of several)."""
    lang = _owner_lang(lang)
    if _lift(s, *(_AGENT + k for k in roles.stored_ids(_agent(key)))):
        s.execute(update(Job).where(Job.waits_on.is_not(None)).values(waits_on=None)
                  .execution_options(synchronize_session=False))
        activity.record(s, verb="resumed", entity="agent", entity_id=None, project_id=None, actor=by,
                        summary=msg("stop.activity.agent_resumed", lang, agent=role(key, lang)), data={"agent": key})
    return agent_state(s, key)


def agent_state(s: Session, key: str) -> dict:
    return {"agent": key, "stopped": view(read(s).agents.get(key))}
