"""Owner controls: what the console can do to a run besides answering requests.

Each action is a small state change plus, where the graph has to move, a queued job. Nothing here
calls a model; the worker does that when it picks the job up. Errors are `ControlError` with a
message the console shows as is, in the owner's language.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from justshipit import staffing
from justshipit.contracts.work import TaskDraft, TaskStatus
from justshipit.contracts.requests import RequestStatus
from justshipit.db.models import AgentMessage, AgentRequest, BudgetMonth, Job, Project, Task
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, msg
from justshipit.services import activity, budget, holds, queue, requests, tasks
from justshipit.settings import limits


class ControlError(ValueError):
    pass


class NotFound(ControlError):
    """The id names nothing. The console answers 404 where other refusals are 409."""


def owner_lang(lang: Lang | None) -> Lang:
    """The language an owner action writes in. The console's routes, and the bet loop and the roadmap pausing a run,
    call these actions without one, so it is the owner's setting, read once per action; a test passes its own."""
    from justshipit.services import app_settings
    return lang or app_settings.language()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_paused(p: Project | None) -> bool:
    return bool(p is not None and (p.context or {}).get("paused"))


def _project(s: Session, pid: int, lang: Lang, *, lock: bool = True) -> Project:
    q = select(Project).where(Project.id == pid)
    p = s.scalar(q.with_for_update() if lock else q)
    if p is None:
        raise ControlError(msg("work.refuse.no_project", lang, ref=f"P-{pid}"))
    return p


# ------------------------------------------------------------------ project run

def pause(s: Session, pid: int, reason: str | None = None, *, lang: Lang | None = None) -> Project:
    lang = owner_lang(lang)
    p = _project(s, pid, lang)
    if p.status not in ("CREATED", "RUNNING", "WAITING_HUMAN"):
        raise ControlError(msg("work.refuse.cannot_pause", lang, ref=f"P-{pid}", status=p.status))
    if is_paused(p):
        return p
    p.context = {**(p.context or {}), "paused": {"at": _now(), "by": "owner", "reason": reason}}
    if p.status == "WAITING_HUMAN":
        # The run is already held at a gate; it stays there until the owner resumes it.
        p.status = "PAUSED"
    activity.record(s, verb="paused", entity="project", entity_id=p.id, project_id=p.id,
                    summary=msg("work.activity.paused", lang, ref=f"P-{p.id}") + (f": {reason}" if reason else ""),
                    data={"reason": reason})
    return p


def resume(s: Session, pid: int, *, lang: Lang | None = None) -> Project:
    lang = owner_lang(lang)
    p = _project(s, pid, lang)
    if not is_paused(p) and p.status != "PAUSED":
        raise ControlError(msg("work.refuse.not_paused", lang, ref=f"P-{pid}"))
    ctx = dict(p.context or {})
    ctx.pop("paused", None)
    p.context = ctx
    if p.status == "PAUSED":
        if holds.park_reason(s, pid) or holds.replanning(s, pid):
            # Still held by the owner's open decisions, or by a re-plan an answer started, which resumes the run itself
            p.status = "WAITING_HUMAN"
        else:
            queue.enqueue(s, "resume_project", {"project_id": pid}, idempotency_key=f"resume:{pid}:owner:{_now()}",
                          by_owner=True)
    activity.record(s, verb="resumed", entity="project", entity_id=p.id, project_id=p.id,
                    summary=msg("work.activity.resumed", lang, ref=f"P-{p.id}"))
    return p


def retry_project(s: Session, pid: int, *, lang: Lang | None = None) -> Project:
    """Decides under the claim lock, settling lapsed leases first, so a run whose worker stopped answering is
    refused with the time its hold ends, not only with the worker's name; a run a worker still holds is refused
    too. The console settles them in a transaction of its own before this (queue.settle_lapsed), since a refusal
    rolls this one back and the next click would name a later end."""
    lang = owner_lang(lang)
    queue.reclaim(s, {})
    p = _project(s, pid, lang)
    if p.status not in ("FAILED", "RUNNING"):
        raise ControlError(msg("work.refuse.cannot_retry_project", lang, ref=f"P-{pid}", status=p.status))
    if why := queue.why_busy(s, f"project:{pid}", queue.RUN_KINDS, lang=lang):
        raise ControlError(msg("work.refuse.run_busy", lang, ref=f"P-{pid}", why=why))
    queue.enqueue(s, "retry_project", {"project_id": pid}, idempotency_key=f"retry:{pid}:{_now()}", max_attempts=1,
                  by_owner=True)
    activity.record(s, verb="retried", entity="project", entity_id=p.id, project_id=p.id,
                    summary=msg("work.activity.retried", lang, ref=f"P-{p.id}"), data={"error": (p.error or "")[:300]})
    return p


def set_budget(s: Session, pid: int, budget_usd: float, note: str | None = None, *, resume_run: bool = False,
               request_id: int | None = None, lang: Lang | None = None) -> Project:
    lang = owner_lang(lang)
    if resume_run:
        queue.reclaim(s, {})  # claim lock before the project row, the order a reclaim that fails this project uses
    p = _project(s, pid, lang)
    if budget_usd <= p.spent_usd + p.reserved_usd:
        raise ControlError(msg("work.refuse.budget_too_low", lang, spent=f"{p.spent_usd + p.reserved_usd:.2f}"))
    old = p.budget_usd
    p.budget_usd = float(budget_usd)
    change = {"at": _now(), "by": "owner", "from": old, "to": p.budget_usd, "note": note,
              **({"request_id": request_id} if request_id else {})}
    if resume_run and p.status == "FAILED" and (p.error or "").startswith("BudgetExceeded"):
        change["resume"] = _restart_after_raise(s, p, lang)
    ctx = p.context or {}
    p.context = {**ctx, "budget_changes": [*ctx.get("budget_changes", []), change]}
    activity.record(s, verb="budget", entity="project", entity_id=p.id, project_id=p.id,
                    summary=msg("work.activity.budget", lang, ref=f"P-{p.id}", old=f"{old:g}", new=f"{p.budget_usd:g}")
                    + (f": {note}" if note else "")
                    + (f" · {change['resume']}" if "resume" in change else ""),
                    data={"from": old, "to": p.budget_usd, "note": note, "resume": change.get("resume")})
    return p


def raise_for_request(s: Session, req: AgentRequest, *, lang: Lang | None = None) -> Project | None:
    """What the owner's yes to a budget card does: add the card's amount to the project's budget and start again a run
    the budget stopped. It stands once: the card's id is kept on the change it made, so a repeated job adds nothing."""
    lang = owner_lang(lang)
    if req.status != RequestStatus.APPROVED.value:
        raise PermissionError("budget raise not approved")
    amount = float(req.estimated_cost_usd or 0)
    p = _project(s, req.project_id, lang)
    if amount <= 0 or any(c.get("request_id") == req.id for c in (p.context or {}).get("budget_changes", [])):
        return None
    return set_budget(s, p.id, p.budget_usd + amount, f"REQ-{req.id}", resume_run=True, request_id=req.id, lang=lang)


def raise_for_request_id(request_id: int) -> None:
    with session_scope() as s:
        raise_for_request(s, s.get(AgentRequest, request_id))


_RESTARTS_FAILED = ("run_project", "retry_project")


def _restart_after_raise(s: Session, p: Project, lang: Lang) -> str:
    """Restart a run the budget stopped, and say when. The raise stands whatever the run's job is doing: a job a
    worker still runs keeps the run, a queued run or retry carries on with the new budget, and a reclaimed one's
    hold only delays the restart, since a claim skips the key until the hold ends. A queued resume skips a FAILED
    project, so it carries nothing on: the retry queued here runs after it."""
    key = f"project:{p.id}"
    jobs = s.scalars(select(Job).where(Job.lock_key == key, Job.kind.in_(queue.RUN_KINDS), queue.in_flight())).all()
    if any(j.status == "running" for j in jobs):
        return msg("work.restart.not_scheduled", lang, why=queue.why_busy(s, key, queue.RUN_KINDS, lang=lang).rstrip("."))
    retry_failed_tasks(s, p.id, only_budget=True)
    if any(j.status == "queued" and j.kind in _RESTARTS_FAILED for j in jobs):
        return msg("work.restart.carries_on", lang, why=queue.why_busy(s, key, _RESTARTS_FAILED, lang=lang).rstrip("."))
    job = queue.enqueue(s, "retry_project", {"project_id": p.id}, idempotency_key=f"retry:{p.id}:{_now()}",
                        max_attempts=1, by_owner=True)
    now = s.scalar(select(func.now()))
    if held := max((j.held_until for j in jobs if j.held_until and j.held_until > now), default=None):
        job.run_after = held
        return msg("work.restart.after_hold", lang, time=queue.local_time(held, lang=lang))
    return msg("work.restart.scheduled", lang)


def add_directive(s: Session, pid: int, text: str, *, lang: Lang | None = None) -> dict:
    """A standalone owner instruction: every later agent call sees it, and the CEO re-plans unstarted work."""
    lang = owner_lang(lang)
    text = text.strip()
    if not text:
        raise ControlError(msg("work.refuse.empty_directive", lang))
    p = _project(s, pid, lang)
    if p.status in ("ARCHIVED",):
        raise ControlError(msg("work.refuse.archived_project", lang, ref=f"P-{pid}"))
    ctx = p.context or {}
    existing = ctx.get("directives", [])
    d = {"n": len(existing) + 1, "at": _now(), "text": text[:4000]}
    p.context = {**ctx, "directives": [*existing, d]}
    realigning = p.status in ("RUNNING", "WAITING_HUMAN", "PAUSED")
    # Every directive is read for its rules, which bind the business's later runs too; only a live plan is re-planned.
    queue.enqueue(s, "realign_project", {"project_id": pid, "directive": d["n"]},
                  idempotency_key=f"realign:{pid}:directive:{d['n']}", max_attempts=2, by_owner=True)
    activity.record(s, verb="directive", entity="project", entity_id=p.id, project_id=p.id,
                    summary=msg("work.activity.directive", lang, ref=f"P-{p.id}", n=d["n"], text=text[:200]),
                    data={"n": d["n"], "text": text[:4000]})
    from justshipit.engineering import owner_trace
    owner_trace.restate(s, pid, lang)
    return {**d, "realigning": realigning}


def remove_directive_rule(s: Session, pid: int, n: int, index: int, label: str, *,
                          lang: Lang | None = None) -> dict:
    """The owner takes a rule off a directive: the server stops refusing work by it. The rule is kept beside the
    directive as removed, so the record says what it was and who removed it. `label` must name the rule at `index`, so
    a page showing an older list cannot remove another rule."""
    lang = owner_lang(lang)
    p = _project(s, pid, lang)
    ctx = p.context or {}
    d = next((x for x in ctx.get("directives", []) if x["n"] == n), None)
    if d is None:
        raise NotFound(msg("work.refuse.no_directive", lang, ref=f"P-{pid}", n=n))
    kept = list(d.get("rules", []))
    if not 0 <= index < len(kept) or kept[index]["label"] != label:
        raise ControlError(msg("work.refuse.rule_changed", lang, n=n))
    rule = kept.pop(index)
    removed = [*d.get("removed_rules", []), {**rule, "removed_at": _now(), "by": "owner"}]
    p.context = {**ctx, "directives": [{**x, "rules": kept, "removed_rules": removed} if x["n"] == n else x
                                       for x in ctx["directives"]]}
    activity.record(s, verb="directive_rule_removed", entity="project", entity_id=p.id, project_id=p.id,
                    summary=msg("work.activity.rule_removed", lang, ref=f"P-{p.id}", n=n, label=rule["label"]),
                    data={"n": n, "rule": rule})
    return {"n": n, "rules": kept, "removed": rule}


def rename(s: Session, pid: int, name: str, *, lang: Lang | None = None) -> Project:
    lang = owner_lang(lang)
    name = name.strip()
    if not name:
        raise ControlError(msg("work.refuse.empty_name", lang))
    p = _project(s, pid, lang)
    old, p.name = p.name, name[:200]
    activity.record(s, verb="renamed", entity="project", entity_id=p.id, project_id=p.id,
                    summary=msg("work.activity.renamed", lang, ref=f"P-{p.id}", old=old, new=p.name),
                    data={"from": old, "to": p.name})
    return p


def unarchive(s: Session, pid: int, *, lang: Lang | None = None) -> Project:
    """Bring an archived project back onto the books with the status it had. Its cancelled requests stay cancelled."""
    lang = owner_lang(lang)
    p = _project(s, pid, lang)
    if p.status != "ARCHIVED":
        raise ControlError(msg("work.refuse.not_archived_project", lang, ref=f"P-{pid}"))
    prev = (p.context or {}).get("archived", {})
    status = prev.get("previous_status") or "DONE"
    # A run that was held at a gate cannot be resumed (its requests were cancelled); it comes back as stopped.
    p.status = status if status in ("DONE", "FAILED") else "FAILED"
    p.phase = prev.get("previous_phase")
    if p.status == "FAILED" and not p.error:
        p.error = msg("work.task.unarchived", lang)
    return p


# ------------------------------------------------------------------ tasks

# Work added after the CEO has moved on to the final decision would never be scheduled.
_CLOSED_PHASES = ("deciding", "reporting", "done", "archived")


def accepts_work(s: Session, pid: int, *, lang: Lang | None = None) -> Project:
    """The project, when it still takes tasks: refused once the CEO has moved on to the final decision."""
    lang = owner_lang(lang)
    p = _project(s, pid, lang)
    _accepts_work(p, lang)
    return p


def _accepts_work(p: Project, lang: Lang) -> None:
    if p.status in ("DONE", "ARCHIVED") or p.phase in _CLOSED_PHASES:
        raise ControlError(msg("work.refuse.closed_to_work", lang, ref=f"P-{p.id}"))


def _task(s: Session, tid: int, lang: Lang) -> Task:
    t = s.scalar(select(Task).where(Task.id == tid).with_for_update())
    if t is None:
        raise ControlError(msg("work.refuse.no_task", lang, ref=f"T-{tid}"))
    return t


def retry_task(s: Session, tid: int, *, lang: Lang | None = None) -> Task:
    lang = owner_lang(lang)
    t = _task(s, tid, lang)
    if t.status != TaskStatus.FAILED.value:
        raise ControlError(msg("work.refuse.retry_only_failed", lang, ref=f"T-{tid}", status=t.status))
    _accepts_work(_project(s, t.project_id, lang), lang)
    _reset_failed(s, t)
    return t


def _reset_failed(s: Session, t: Task) -> None:
    tasks.transition(t, TaskStatus.READY)
    staffing.settle_retry(s, t)
    t.error = None
    t.finished_at = None
    # A fresh attempt gets the normal rework allowance and enough task budget to run again.
    t.max_iterations = t.iteration + int(limits()["max_rework_iterations"])
    t.budget_usd = max(t.budget_usd, t.spent_usd / budget.TASK_OVERRUN + tasks.budget_usd(t.owner))


def retry_failed_tasks(s: Session, pid: int, *, only_budget: bool = False) -> list[int]:
    out = []
    for t in s.scalars(select(Task).where(Task.project_id == pid, Task.status == TaskStatus.FAILED.value)
                       .with_for_update()):
        if only_budget and "budget" not in (t.error or "").lower():
            continue
        _reset_failed(s, t)
        out.append(t.id)
    return out


def cancel_task(s: Session, tid: int, reason: str | None = None, *, lang: Lang | None = None) -> Task:
    lang = owner_lang(lang)
    t = _task(s, tid, lang)
    if t.status not in (TaskStatus.PROPOSED.value, TaskStatus.READY.value):
        raise ControlError(msg("work.refuse.cancel_only_unstarted", lang, ref=f"T-{tid}", status=t.status))
    tasks.transition(t, TaskStatus.CANCELLED, error=msg("work.task.cancelled_by_owner_why", lang, reason=reason)
                     if reason else msg("work.task.cancelled_by_owner", lang))
    return t


def update_task(s: Session, tid: int, *, title: str | None = None, objective: str | None = None,
                acceptance_criteria: list[str] | None = None, owner: str | None = None,
                lang: Lang | None = None) -> Task:
    lang = owner_lang(lang)
    t = _task(s, tid, lang)
    if t.status not in (TaskStatus.PROPOSED.value, TaskStatus.READY.value):
        raise ControlError(msg("work.refuse.edit_only_unstarted", lang, ref=f"T-{tid}"))
    changed: dict[str, Any] = {}
    title_was = t.title
    if owner is not None and owner != t.owner:
        if owner not in tasks._assignable_owners():
            raise ControlError(msg("work.refuse.bad_owner", lang, owner=owner))
        changed["owner"] = [t.owner, owner]
        t.owner = owner
    for field, value in (("title", title), ("objective", objective)):
        if value is not None and value.strip() and value != getattr(t, field):
            changed[field] = True
            setattr(t, field, value.strip()[:300] if field == "title" else value.strip())
    if acceptance_criteria is not None:
        crit = [c.strip() for c in acceptance_criteria if c.strip()]
        if crit != t.acceptance_criteria:
            changed["acceptance_criteria"] = True
            t.acceptance_criteria = crit
    if changed:
        # `title` is the task's title, as in every event's data, so the activity names the task; `fields` lists what
        # changed and `title_was` keeps the title an edit replaced.
        data = {**changed, "fields": list(changed), "title": t.title,
                **({"title_was": title_was} if "title" in changed else {})}
        activity.record(s, verb="edited", entity="task", entity_id=t.id, project_id=t.project_id,
                        summary=msg("work.activity.task_edited", lang, ref=f"T-{t.id}", title=t.title[:120],
                                    fields=", ".join(changed)), data=data)
    return t


def comment_task(s: Session, tid: int, body: str, *, lang: Lang | None = None) -> AgentMessage:
    lang = owner_lang(lang)
    body = body.strip()
    if not body:
        raise ControlError(msg("work.refuse.empty_comment", lang))
    t = s.get(Task, tid)
    if t is None:
        raise ControlError(msg("work.refuse.no_task", lang, ref=f"T-{tid}"))
    from justshipit.services import chat, chat_tags  # chat imports the queue, which imports this module's neighbours
    thread = chat.thread_for(s, chat_tags.resolve(s, "task", str(t.id), None))
    text = chat.scrub(body, lang)[0][:4000]  # the activity feed is broadcast too, so it gets the scrubbed words
    m = chat.append(s, thread, from_agent=chat.OWNER, to_agent=t.owner, kind="comment", body=text,
                    project_id=t.project_id)
    activity.record(s, verb="comment", entity="task", entity_id=t.id, project_id=t.project_id,
                    summary=msg("work.activity.task_comment", lang, ref=f"T-{t.id}", title=t.title[:120]),
                    data={"body": text[:500]})
    return m


def create_task(s: Session, pid: int, *, title: str, objective: str, owner: str,
                acceptance_criteria: list[str], depends_on: list[int], lang: Lang | None = None) -> Task:
    lang = owner_lang(lang)
    p = _project(s, pid, lang)
    _accepts_work(p, lang)
    if owner not in tasks._assignable_owners():
        raise ControlError(msg("work.refuse.bad_owner", lang, owner=owner))
    if not title.strip() or not objective.strip():
        raise ControlError(msg("work.refuse.need_title_goal", lang))
    crit = [c.strip() for c in acceptance_criteria if c.strip()] or [msg("work.task.default_criterion", lang)]
    draft = TaskDraft(key=f"owner_{title}", title=title.strip(), owner=owner, objective=objective.strip(),
                      acceptance_criteria=crit, depends_on=[str(d) for d in depends_on])
    # A running project schedules it at its next step; a paused or failed one when the owner resumes or retries.
    created, _ = tasks.create_from_drafts(s, p, [draft], origin="owner", plan_round=p.plan_round, lang=lang)
    return created[0]


# ------------------------------------------------------------------ company

def set_month_limit(s: Session, limit_usd: float, *, lang: Lang | None = None) -> BudgetMonth:
    lang = owner_lang(lang)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    s.execute(insert(BudgetMonth).values(month=month, limit_usd=float(limits()["monthly_budget_usd"]),
                                         spent_usd=0.0, reserved_usd=0.0).on_conflict_do_nothing())
    m = s.get(BudgetMonth, month, with_for_update=True)
    if limit_usd <= m.spent_usd + m.reserved_usd:
        raise ControlError(msg("work.refuse.month_limit_too_low", lang,
                               spent=f"{m.spent_usd + m.reserved_usd:.2f}"))
    old, m.limit_usd = m.limit_usd, float(limit_usd)
    activity.record(s, verb="budget", entity="company", entity_id=None, project_id=None,
                    summary=msg("work.activity.month_limit", lang, month=month, old=f"{old:g}", new=f"{m.limit_usd:g}"),
                    data={"from": old, "to": m.limit_usd})
    return m
