"""Console read models and owner actions beyond the Approval Center.

Read endpoints aggregate what the dashboard, task, agent, run and activity screens show. Write
endpoints call services/control.py inside `activity.as_actor("owner")`, so every console action is
in the audit trail as the owner's.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Date, case, cast, func, literal, or_, select, true
from sqlalchemy.orm import aliased

from justshipit import staffing
from justshipit.agents import roles
from justshipit.api.rows import row as _row
from justshipit.contracts import owner_text
from justshipit.contracts.requests import PolicyMode
from justshipit.db.models import (
    ActionExecution, ActivityEvent, AgentMessage, AgentRequest, BudgetMonth, Business, Decision, DevJob, Evidence, Job,
    KickoffSession, LLMCallRecord, Meeting, OwnerTask, Project, Skill, Spec, Task,
)
from justshipit.db.session import session_scope
from justshipit.i18n import DEFAULT, Lang, msg
from justshipit.operations import notify as slack
from justshipit.runtime import quota
from justshipit.services import (
    activity, app_settings, budget, control, matters, notify_state, project_state, queue, requests, roadmap, scope,
    secrets, sending, stops, strengths, tasks, usage, work_now,
)
from justshipit.services.work_now import job_label
from justshipit.services import policy as policy_service
from justshipit.services import system_health
from justshipit.settings import agents_config, company_config, limits, settings, tool_policy

router = APIRouter()

_OPEN = ("PROPOSED", "READY")



def _tz(name: str | None) -> str:
    """Day buckets follow the owner's browser timezone; anything unknown falls back to UTC."""
    try:
        ZoneInfo(name or "UTC")
        return name or "UTC"
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _test_ids(s) -> set[int]:
    return set(s.scalars(select(Project.id).where(Project.name.startswith("[TEST]"))))


def _archived_ids(s) -> set[int]:
    return set(s.scalars(select(Project.id).where(Project.status == "ARCHIVED")))


def _hidden(s, include_test: bool, business_id: int | None = None) -> set[int]:
    """Projects a console list leaves out, by the one rule in services/scope.py."""
    return scope.hidden_projects(s, business_id=business_id, include_test=include_test)


def _days(n: int, tz: str) -> list[str]:
    today = _now().astimezone(ZoneInfo(tz)).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def _day(col, tz: str):
    return cast(func.timezone(tz, col), Date)


# ============================================================== read models

def _calls_shown(hidden: set[int], business_id: int | None = None):
    """The model calls a view shows: those of its projects, and with a business, the project-less calls that name it
    (its kickoff, idea panel, people search) or no business (services/scope.py)."""
    return scope.rows(hidden, LLMCallRecord.project_id, LLMCallRecord.business_id, business_id)


def _agent_states(s, hidden: set[int], business_id: int | None = None) -> dict[str, dict]:
    """Live state per agent: what it is doing now, and how its recent calls went. Counts and costs cover the calls
    the view shows (`hidden`), the same set as the view's all-time figures. Without a business the state reads the
    latest call anywhere; with one, the latest call of that business, so another business's failure is not this one's."""
    cfg = agents_config()
    shown = _calls_shown(hidden, business_id)
    out = {k: {"running": [], "calls_24h": 0, "errors_24h": 0, "last_run_at": None, "last_status": None,
               "cost_month": 0.0} for k in cfg}
    lang = app_settings.language()
    for r in work_now.rows(s, hidden):
        if r.agent not in out:
            continue
        if r.kind == "dev":
            item = {"dev_job_id": r.dev_job_id, "title": r.title, "project_id": r.project_id, "status": r.status}
        elif r.kind == "job":
            item = {"job_id": r.job_id, "title": job_label(r.job_kind, lang), "project_id": r.project_id,
                    "project": r.project}
        else:
            item = {"task_id": r.task_id, "title": r.title, "project_id": r.project_id, "project": r.project}
        out[r.agent]["running"].append({**item, "role": {"task": "work"}.get(r.kind, r.kind), "stopping": r.stopping})
    since = _now() - timedelta(hours=24)
    for agent, n, err in s.execute(select(LLMCallRecord.agent, func.count(),
                                          func.count().filter(LLMCallRecord.status != "ok"))
                                   .where(LLMCallRecord.created_at >= since, shown).group_by(LLMCallRecord.agent)):
        if (agent := roles.current(agent)) in out:
            out[agent]["calls_24h"] += n
            out[agent]["errors_24h"] += err
    month_start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for agent, c in s.execute(select(LLMCallRecord.agent, func.sum(LLMCallRecord.cost_usd))
                              .where(LLMCallRecord.created_at >= month_start, shown).group_by(LLMCallRecord.agent)):
        if (agent := roles.current(agent)) in out:
            out[agent]["cost_month"] += float(c or 0)
    last = (select(LLMCallRecord.agent, LLMCallRecord.status, LLMCallRecord.created_at,
                   func.row_number().over(partition_by=LLMCallRecord.agent,
                                          order_by=LLMCallRecord.created_at.desc()).label("rn"))
            .where(shown if business_id is not None else true())).subquery()
    for agent, status, at, _ in s.execute(select(last).where(last.c.rn == 1)):
        if agent in out:
            out[agent]["last_run_at"], out[agent]["last_status"] = at, status
    held = stops.read(s).agents
    for k, v in out.items():
        # error: the latest call failed and nothing is running that could have recovered it; the owner's stop says more
        # than either, and the steps it still finishes stay listed under `running`
        v["stopped"] = stops.view(held.get(k))
        v["state"] = ("stopped" if k in held else "running" if v["running"]
                      else "error" if v["last_status"] not in (None, "ok") else "idle")
    return out


def _alerts(s, hidden: set[int], business_id: int | None = None, *, lang: Lang) -> list[dict]:
    """What needs the owner besides requests: stopped runs, failed work, budgets close to their cap. With
    `business_id`, an alert of no project shows when it names that business or none (services/scope.py). The
    server's sentences in it are in `lang`."""
    from justshipit.engineering import status as dev_status, worker
    out: list[dict] = []
    for p in s.scalars(select(Project).where(Project.status.in_(["FAILED", "PAUSED"]))):
        if p.id in hidden:
            continue
        # the same test the project page makes (web `budgetStop`): a failed run whose budget lacks the room one more call
        # holds is a budget stop whatever error it stored, so the alert leads to the budget and not to "Run again"
        refused = (p.error or "").startswith("BudgetExceeded") or budget.retry_blocked(s, p)
        out.append({"kind": "project_paused" if p.status == "PAUSED" else ("budget_stop" if refused else "project_failed"),
                    "severity": "warn" if p.status == "PAUSED" else "bad", "project_id": p.id, "project": p.name,
                    "title": p.name, "detail": (p.context or {}).get("paused", {}).get("reason") if p.status == "PAUSED"
                    else p.error, "reason": None if p.status == "PAUSED" else failure_reason(p.error, lang),
                    "at": p.updated_at})
    for p in s.scalars(select(Project).where(Project.status.notin_(["ARCHIVED", "DONE", "FAILED"]))):
        if p.id in hidden or not p.budget_usd:
            continue
        used = (p.spent_usd + p.reserved_usd) / p.budget_usd
        if used >= 0.8:
            out.append({"kind": "budget_warning", "severity": "warn", "project_id": p.id, "project": p.name,
                        "title": p.name, "detail": f"{used:.0%}", "used": used, "at": p.updated_at})
    month = s.get(BudgetMonth, _now().strftime("%Y-%m"))
    if month and month.limit_usd and (month.spent_usd + month.reserved_usd) / month.limit_usd >= 0.8:
        used = (month.spent_usd + month.reserved_usd) / month.limit_usd
        out.append({"kind": "month_budget_warning", "severity": "bad" if used >= 1 else "warn", "project_id": None,
                    "title": month.month, "detail": f"{used:.0%}", "used": used, "at": None})
    live_done = {p for p in s.scalars(select(Project.id).where(Project.status == "DONE"))}
    for t in s.scalars(select(Task).where(Task.status == "FAILED").order_by(Task.finished_at.desc().nullslast())):
        if t.project_id in hidden or t.project_id in live_done:
            continue
        stop = _budget_stop(s, t.error, t.project_id, lang)
        failed = task_failure(s, t, lang, stop=stop)
        closed = _closed_to_work(s.get(Project, t.project_id))
        out.append({"kind": "task_failed", "severity": "info" if failed["handled"] else "bad",
                    "handled": failed["handled"], "project_id": t.project_id, "task_id": t.id,
                    "title": owner_text.title(t.title), "detail": stop["says"] if stop else t.error,
                    "reason": failed["says"], **_stop_fields(stop),
                    "retryable": not closed and not (stop and stop["short"]),
                    "hint": msg("console.alert.task_closed", lang) if closed else None,
                    "owner": t.owner, "at": t.finished_at or t.updated_at})
    latest = (select(DevJob.id, DevJob.spec_id, DevJob.status, DevJob.error, DevJob.updated_at,
                     func.row_number().over(partition_by=DevJob.spec_id, order_by=DevJob.id.desc()).label("rn"))
              ).subquery()
    for jid, spec_id, status, _err, at, _ in s.execute(select(latest).where(latest.c.rn == 1, latest.c.status == "FAILED")):
        sp = s.get(Spec, spec_id)
        if sp.project_id in hidden:
            continue
        job = s.get(DevJob, jid)
        out.append({"kind": "dev_failed", "severity": "bad", "project_id": sp.project_id, "dev_job_id": jid,
                    "spec_key": sp.spec_key, "title": owner_text.title(sp.title),
                    "detail": dev_status.failure_line(s, job, lang), "retry_wait": dev_status.rebuild_wait(s, job, lang),
                    "at": at, **_dev_retry(sp, lang),
                    "actions": worker.owner_actions(s, job), "stop": dev_status.stop_kind(job)})
    # A delivered job keeps its branch, so nothing marks it FAILED: it waits on the owner when its merge did not land
    # (the re-approval request links it), or when nothing is queued or pending for it any more (an acceptance that
    # failed every attempt, a merge request the owner turned down), and the owner's actions move it on.
    lang = app_settings.language()
    for jid, spec_id, err, at in s.execute(select(latest.c.id, latest.c.spec_id, latest.c.error, latest.c.updated_at)
                                           .where(latest.c.rn == 1, latest.c.status == "DELIVERED")):
        sp = s.get(Spec, spec_id)
        if sp.project_id in hidden:
            continue
        job = s.get(DevJob, jid)
        waiting = worker.pending_for(s, jid)
        request = waiting.id if isinstance(waiting, AgentRequest) else None
        actions = worker.owner_actions(s, job)
        if not actions and not (request and err):
            continue
        out.append({"kind": "dev_delivered", "severity": "bad" if err and not request else "warn", "project_id": sp.project_id,
                    "dev_job_id": jid, "spec_key": sp.spec_key, "title": owner_text.title(sp.title),
                    "detail": err or (worker.step_line(job.steps[-1], lang) if job.steps else None), "at": at, "request_id": request, "actions": actions})
    queued = _queue_alerts(s, hidden, lang)
    # With no worker nothing else gets fixed, so the home screen's short list leads with it. A problem on this PC
    # comes next: it is usually why the work below failed.
    ordered = ([a for a in queued if a["kind"] == "worker_down"] + system_health.alerts(lang) + out
               + [a for a in queued if a["kind"] != "worker_down"])
    # An alert of neither a project nor a business is the company's (worker, monthly budget, Slack, usage): every
    # business shows it.
    out = []
    asks = {r.project_id: ask for r in requests.pending(s) if (ask := requests.budget_ask(r))}
    for a in ordered:
        bid = a.get("business_id")
        if a["project_id"] is not None and (p := s.get(Project, a["project_id"])) is not None:
            bid = p.business_id
        if business_id is None or bid in (None, business_id):
            out.append({**a, "business_id": bid, "company_wide": a["project_id"] is None and bid is None,
                        "budget_ask": asks.get(a["project_id"]) if a["kind"] == "budget_stop" or a.get("budget_short") else None})
    return out


def failed_by_business(s, lang: Lang) -> dict[int, int]:
    """Each business's stopped or failed work that waits on the owner: the alerts its own screens count (web
    `alertCounts().need`), without the ones the company is already redoing (`handled`) and without the company's own
    (the worker, the monthly budget), which every business shows already."""
    out: dict[int, int] = {}
    for a in _alerts(s, _hidden(s, False), None, lang=lang):
        if a["business_id"] is not None and not a.get("handled"):
            out[a["business_id"]] = out.get(a["business_id"], 0) + 1
    return out


def _budget_stop(s, error: str | None, project_id: int | None, lang: Lang = DEFAULT) -> dict | None:
    """What a budget refusal left of failed work: the cap, the project whose budget the owner raises, whether a retry
    would be refused again, and the owner's sentence. None when no budget stopped it."""
    cap = budget.refused_by(error)
    if cap is None:
        return None
    return {"cap": cap, "project_id": project_id if cap == "project" else None,
            "short": budget.still_short(s, cap, project_id), "says": budget.stop_line(s, cap, project_id, lang=lang)}


def _closed_to_work(p: Project) -> bool:
    """The project no longer takes tasks, so a failed one cannot be retried (control._accepts_work)."""
    return p.status in ("DONE", "ARCHIVED") or p.phase in control._CLOSED_PHASES


def _dev_retry(sp: Spec, lang: Lang) -> dict:
    """Whether a new attempt at the spec's build would start (engineering.specs.retry_dev refuses a spec with no
    approved commit), and when it would not, why."""
    ok = bool(sp.approved_commit_sha)
    return {"retryable": ok, "hint": None if ok else msg("console.alert.dev_no_spec", lang)}


def _stop_fields(stop: dict | None) -> dict:
    return {"stop": "budget" if stop else None, "budget_project_id": stop["project_id"] if stop else None,
            "budget_short": bool(stop and stop["short"])}


def _job_budget_project(s, payload: dict) -> int | None:
    """The project whose budget a queue job spends: the one its payload names, or a bet's run."""
    pid = _job_project(s, payload)
    if pid is None and payload.get("bet_id") is not None:
        pid = s.scalar(select(Project.id).where(Project.bet_id == payload["bet_id"]))
    return pid


_ERROR_CLASSES = re.compile(r"^(?:[A-Za-z_][\w./-]*:\s*)+")
_HANGUL = re.compile(r"[\uac00-\ud7a3]")


# A project in these states is still the company's to run: its CEO reviews the round's results, failed tasks too.
_RUNNING_PROJECT = ("RUNNING", "WAITING_HUMAN")


def task_failure(s, t: Task, lang: Lang, *, stop: dict | None = None) -> dict:
    """What a failed task means for the owner, in one line: a task that failed its review while its project runs and
    no budget stopped it is the company's to redo, and the owner has nothing to do (`handled`); otherwise why it
    failed. A task that failed for any other reason (the sandbox, the CLI's login) can need the owner. The reviewer's
    own words stay in the task's error, shown under details."""
    from justshipit.orchestration.company_graph import UNMET
    p = s.get(Project, t.project_id) if t.project_id else None
    if (stop is None and p is not None and p.status in _RUNNING_PROJECT
            and (t.review or {}).get("verdict") == UNMET):
        return {"handled": True, "says": msg("console.alert.task_handled", lang)}
    return {"handled": False, "says": stop["says"] if stop else failure_reason(t.error, lang)}


def failure_reason(error: str | None, lang: Lang) -> str:
    """Why work failed, in the owner's words: the stored error's first line past the exception and agent names in
    front of it, when it reads as a sentence; a plain sentence otherwise, and the raw text stays in detail. A Korean
    line is a sentence an owner was meant to read. On an English screen an English line of three words or more is
    too, since an English owner can read the cause itself; a bare code ("x") is not."""
    first = (error or "").strip().split("\n", 1)[0]
    if not first:
        return msg("console.reason.none", lang)
    if quota.is_quota_error(first):
        return msg("console.reason.quota", lang)
    said = _ERROR_CLASSES.sub("", first).strip()
    readable = _HANGUL.search(said) or (lang == "en" and len(said.split()) >= 3)
    return owner_text.one_line(said, 200) if readable else msg("console.reason.unexpected", lang)


def _why_not_retryable(s, job: Job, lang: Lang = DEFAULT) -> str | None:
    """None when a fresh copy of this failed job may run. Work a budget refused waits until the budget has room. An
    external action may run again on its approval only while nothing of it went out (its ledger row says not_sent),
    the owner has not asked again instead, and a grant is left for the retry to spend."""
    if (stop := _budget_stop(s, job.last_error, _job_budget_project(s, job.payload), lang)) and stop["short"]:
        return stop["says"]
    if job.kind == "redraft_spec":
        from justshipit.engineering import wireflow
        spec = s.get(Spec, job.payload["spec_id"])
        if spec is None or not wireflow.sent_back(spec):  # rewritten since, or the owner dropped the notes
            return msg("console.redraft_moved_on", lang)
    if job.kind == "execute_action":
        rid = job.payload["request_id"]
        unsent = s.scalar(select(ActionExecution.status).where(ActionExecution.request_id == rid)) == "not_sent"
        asked_again = s.scalar(select(AgentRequest.id).where(
            AgentRequest.action_params["re_request_of"].as_integer() == rid))
        if unsent and asked_again is None:
            req = s.get(AgentRequest, rid)
            amount = float((req.action_params.get("payload") or {}).get("amount_usd") or 0)
            if requests.grant_left(s, req.action_kind, req.project_id, amount, request_id=req.id):
                return None
            return msg("console.grant_lapsed", lang)
    return msg(f"console.not_retryable.{job.kind}", lang) if job.kind in queue.NOT_RETRYABLE else None


def _job_project(s, payload: dict) -> int | None:
    return scope.owner_of(s, payload)[0]


def _job_family(kind):
    # Any later run of a project's graph answers an earlier failed one, whichever of the three kinds it is.
    return case((kind.in_(queue.RUN_KINDS), literal("run_project")), else_=kind)


def _stalled(s) -> tuple[list[Project], list[DevJob]]:
    """Runs that look busy while nothing queued or holding a live lease will move them. While no worker runs none
    is stalled: every one waits for the worker, which the worker_down alert says once, and calling each a failure
    would contradict the pages that show it waiting."""
    if not queue.last_worker_seen(s)[1]:
        return [], []
    busy = queue.busy_lock_keys(s)
    runs = [p for p in s.scalars(select(Project).where(Project.status == "RUNNING")) if f"project:{p.id}" not in busy]
    devs = [d for d in s.scalars(select(DevJob).where(DevJob.status.in_(queue.DEV_ACTIVE)))
            if f"dev_job:{d.id}" not in busy]
    return runs, devs


def _waiting(s):
    """Reclaimed jobs whose old holder may still be mid-call: the subject waits, and the owner should see until when."""
    return s.scalars(select(Job).where(Job.held_until > func.now(), Job.lock_key.is_not(None)).order_by(Job.id))


def liveness(s) -> tuple[bool, frozenset[int], frozenset[int], frozenset[int], frozenset[int]]:
    """The alert state that changes with time instead of with a row: the live feed re-checks it."""
    runs, devs = _stalled(s)
    return (queue.last_worker_seen(s)[1], frozenset(p.id for p in runs), frozenset(d.id for d in devs),
            frozenset(j.id for j in _waiting(s)), frozenset(j.id for j in queue.usage_waiting(s)))


def _usage_wait_alert(s, hidden: set[int], lang: Lang) -> dict | None:
    """One alert for all the work that waits on usage: why, until when in the owner's zone, and what waits."""
    jobs = [(j, _job_project(s, j.payload)) for j in queue.usage_waiting(s)]
    jobs = [(j, pid) for j, pid in jobs if pid not in hidden]
    if not jobs:
        return None
    tz = app_settings.effective().usage.timezone
    latest = jobs[0][0]
    names = {pid: s.get(Project, pid).name for _, pid in jobs if pid is not None}
    until = usage.local_time(latest.run_after, tz, lang)
    waiting = ", ".join(names.values()) or ", ".join(job_label(j.kind, lang) for j, _ in jobs)
    reason = usage.reason_label(latest.wait_reason, lang)
    return {"kind": "usage_wait", "severity": "warn", "project_id": None, "title": msg("console.alert.usage_wait", lang),
            "reason": latest.wait_reason, "reason_label": reason,
            "until": latest.run_after, "until_local": until, "timezone": tz, "at": latest.run_after,
            "cause": latest.last_error, "waiting_work": waiting,
            "detail": msg("console.alert.usage_wait.detail", lang, reason=reason, until=until, waiting=waiting)
                      + (f" {latest.last_error}" if latest.last_error else ""),
            "projects": [{"project_id": pid, "project": name} for pid, name in names.items()],
            "jobs": [{"job_id": j.id, "job_kind": j.kind, "title": job_label(j.kind, lang), "project_id": pid,
                      "reason": j.wait_reason, "until": j.run_after} for j, pid in jobs]}


def _queue_alerts(s, hidden: set[int], lang: Lang) -> list[dict]:
    out: list[dict] = []
    seen, alive = queue.last_worker_seen(s)
    if not alive:
        waiting = s.scalar(select(func.count()).select_from(Job).where(Job.status == "queued"))
        out.append({"kind": "worker_down", "severity": "bad", "project_id": None, "title": msg("console.alert.worker", lang),
                    "detail": msg("console.alert.worker.detail", lang)
                              + (msg("console.alert.worker.waiting", lang, n=waiting) if waiting else ""),
                    "waiting": waiting, "at": seen})
    newer = aliased(Job)
    # A later copy is a retry, the owner's or the company's: the failure waits on nobody any more. While no worker
    # runs, the worker alert above says why the copy has not started.
    superseded = select(newer.id).where(newer.id > Job.id, _job_family(newer.kind) == _job_family(Job.kind),
                                        newer.payload == Job.payload).exists()
    for j in s.scalars(select(Job).where(Job.status == "failed", Job.alert_dismissed_at.is_(None), ~superseded)
                       .order_by(Job.finished_at.desc().nullslast())):
        if j.kind in queue.RUN_KINDS and (p := s.get(Project, j.payload.get("project_id"))) and p.status == "FAILED":
            continue  # shown as project_failed, with its retry control
        if j.kind in queue.DEV_KINDS and (d := s.get(DevJob, j.payload.get("dev_job_id"))) and d.status == "FAILED":
            continue  # shown as dev_failed
        pid, bid = scope.owner_of(s, j.payload)
        if pid in hidden:
            continue
        stop = _budget_stop(s, j.last_error, _job_budget_project(s, j.payload), lang)
        why = _why_not_retryable(s, j, lang)
        lost = msg("console.reason.worker_lost", lang) if queue.reclaimed_out(j) else None
        out.append({"kind": "job_failed", "severity": "bad", "project_id": pid, "business_id": bid,
                    "job_id": j.id, "job_kind": j.kind, "title": job_label(j.kind, lang),
                    "detail": stop["says"] if stop else lost or (j.last_error or "").split("\n", 1)[0][:600],
                    "reason": stop["says"] if stop else lost or failure_reason(j.last_error, lang), **_stop_fields(stop),
                    "retryable": why is None,
                    "hint": None if stop else why or (msg("console.unsent", lang) if j.kind == "execute_action" else None),
                    "at": j.finished_at})
    if refused := notify_state.slack_down():
        held = s.scalar(select(func.count()).select_from(Job).where(Job.status == "queued",
                                                                    Job.kind.in_(queue.NOTICE_KINDS)))
        out.append({"kind": "slack_disconnected", "severity": "bad", "project_id": None, "title": msg("console.alert.slack", lang),
                    "detail": msg("console.alert.slack.detail", lang, why=str(refused["why"]).rstrip("."), n=held),
                    "waiting": held, "at": refused["since"]})
    for j in _waiting(s):
        pid, bid = scope.owner_of(s, j.payload)
        if pid in hidden:
            continue
        p = s.get(Project, pid) if pid is not None else None
        out.append({"kind": "job_waiting", "severity": "warn", "project_id": pid, "business_id": bid,
                    "project": p.name if p else None,
                    "job_id": j.id, "job_kind": j.kind, "title": job_label(j.kind, lang),
                    "detail": None, "until": j.held_until,  # the title says why; the reclaim's own text is an engineer's
                    "resumes": j.status == "queued", "at": j.held_until})
    if waiting_on_usage := _usage_wait_alert(s, hidden, lang):
        out.append(waiting_on_usage)
    runs, devs = _stalled(s)
    for p in runs:
        if p.id not in hidden:
            out.append({"kind": "run_stalled", "severity": "bad", "project_id": p.id, "project": p.name,
                        "title": p.name, "detail": msg("console.alert.run_stalled", lang),
                        "at": p.updated_at})
    for d in devs:
        sp = s.get(Spec, d.spec_id)
        if sp.project_id not in hidden:
            out.append({"kind": "dev_stalled", "severity": "bad", "project_id": sp.project_id, "dev_job_id": d.id,
                        "spec_key": sp.spec_key, "title": owner_text.title(sp.title), **_dev_retry(sp, lang),
                        "detail": msg("console.alert.dev_stalled", lang, status=d.status), "at": d.updated_at})
    return out


@router.get("/overview")
def overview(include_test: bool = False, tz: str | None = None, business_id: int | None = None) -> dict:
    """Everything the home screen shows in one round trip, for one business (services/scope.py)."""
    from justshipit.api.app import _project_summaries
    tz = _tz(tz)
    with session_scope() as s:
        hidden = _hidden(s, include_test, business_id)
        agents = _agent_states(s, hidden, business_id)
        cfg = agents_config()
        visible = Task.project_id.notin_(hidden) if hidden else true()
        tasks_by = dict(s.execute(select(Task.status, func.count()).where(visible).group_by(Task.status)).all())
        pending = [r for r in s.scalars(select(AgentRequest).where(AgentRequest.status == "PENDING"))
                   if r.project_id not in hidden]
        month = s.get(BudgetMonth, _now().strftime("%Y-%m"))
        days = _days(14, tz)
        since = datetime.fromisoformat(days[0]).replace(tzinfo=ZoneInfo(tz))
        runs = {d: {"day": d, "ok": 0, "error": 0, "cost_usd": 0.0} for d in days}
        for d, st, n, c in s.execute(
                select(_day(LLMCallRecord.created_at, tz).label("d"), LLMCallRecord.status, func.count(),
                       func.sum(LLMCallRecord.cost_usd))
                .where(LLMCallRecord.created_at >= since, scope.rows(hidden, LLMCallRecord.project_id))
                .group_by("d", LLMCallRecord.status)):
            if (k := d.isoformat()) in runs:
                runs[k]["ok" if st == "ok" else "error"] += n
                runs[k]["cost_usd"] += float(c or 0)
        finished = {d: {"day": d, "DONE": 0, "FAILED": 0, "CANCELLED": 0} for d in days}
        for d, st, n in s.execute(
                select(_day(Task.finished_at, tz).label("d"), Task.status, func.count())
                .where(Task.finished_at >= since, visible).group_by("d", Task.status)):
            if (k := d.isoformat()) in finished and st in finished[k]:
                finished[k][st] += n
        # the dashboard shows the few an owner follows, so it reads further back than it shows
        events = s.execute(_activity_query(None, None, None, None, None, None, include_test, s, business_id)
                           .limit(40)).all()
        recent = s.execute(select(Task.id, Task.title, Task.status, Task.owner, Task.project_id, Task.updated_at)
                           .where(visible).order_by(Task.updated_at.desc()).limit(8)).all()
        projects = [p for p in _project_summaries(s, business_id=business_id)
                    if include_test or business_id is not None or not p["is_test"]]
        return {
            "company": company_config()["company"],
            "agents": [{"id": k, **{x: v.get(x) for x in ("title", "department", "provider", "model", "runtime")},
                        **agents[k]} for k, v in cfg.items()],
            "tasks_by_status": tasks_by,
            "pending": {"total": len(pending), "blocking": sum(1 for r in pending if r.blocking),
                        "strong": sum(1 for r in pending if requests.typed_confirm(r))},
            "budget": _row(month) if month else {"month": _now().strftime("%Y-%m"),
                                                  "limit_usd": float(limits()["monthly_budget_usd"]),
                                                  "spent_usd": 0.0, "reserved_usd": 0.0},
            "series": {"runs": list(runs.values()), "tasks": list(finished.values())},
            "alerts": _alerts(s, hidden, business_id, lang=app_settings.language()),
            "activity": _event_rows(s, events),
            "recent_tasks": [{**dict(zip(("id", "title", "status", "owner", "project_id", "updated_at"), r)),
                              "title": owner_text.title(r[1])} for r in recent],
            "projects": projects,
            "spend": _business_spend(s, business_id) if business_id is not None else None,
        }


def _business_spend(s, business_id: int) -> dict:
    """What the business has spent so far: every project's spend, archived ones too, which holds its tasks and builds,
    and the calls made for it outside any project (the kickoff, the idea panel, finding people). Such calls made
    before the ledger kept their business are in no business's total: `outside_known` is false when the ledger holds
    fewer of this business's answered kickoff calls than the kickoff posted turns, which a kickoff held before the
    deploy and one that ran across it both show, so the console can say the total misses them."""
    usd, n = s.execute(select(func.coalesce(func.sum(Project.spent_usd), 0.0), func.count())
                       .where(Project.business_id == business_id)).one()
    outside, kickoff_calls = s.execute(
        select(func.coalesce(func.sum(LLMCallRecord.cost_usd), 0.0),
               func.count().filter(LLMCallRecord.purpose == "kickoff", LLMCallRecord.status == "ok"))
        .where(LLMCallRecord.business_id == business_id, LLMCallRecord.project_id.is_(None))).one()
    turns = s.scalar(select(func.coalesce(func.sum(KickoffSession.turns_used), 0))
                     .where(KickoffSession.business_id == business_id))
    return {"projects_usd": round(float(usd), 2), "projects": n, "outside_usd": round(float(outside), 2),
            "total_usd": round(float(usd) + float(outside), 2), "outside_known": kickoff_calls >= turns}


@router.get("/alerts")
def alerts(include_test: bool = False, business_id: int | None = None) -> list[dict]:
    with session_scope() as s:
        return _alerts(s, _hidden(s, include_test, business_id), business_id, lang=app_settings.language())


def _activity_query(project_id, entity, actor, verb, before, since, include_test, s, business_id=None):
    """Events with the project each belongs to (activity.project_column), newest first."""
    pid = activity.project_column()
    q = select(ActivityEvent, pid.label("pid"))
    if project_id is not None:
        q = q.where(pid == project_id)
    else:
        hidden = _hidden(s, include_test, business_id)
        q = q.where(scope.rows(hidden, pid, ActivityEvent.business_id, business_id))
    if entity:
        q = q.where(ActivityEvent.entity == entity)
    if actor:
        q = q.where(ActivityEvent.actor.in_(roles.stored_ids(actor)))
    if verb:
        q = q.where(ActivityEvent.verb == verb)
    if before:
        q = q.where(ActivityEvent.id < before)
    if since:
        q = q.where(ActivityEvent.at >= since)
    return q.order_by(ActivityEvent.at.desc(), ActivityEvent.id.desc())


class ActivityVisit(BaseModel):
    business_id: int


@router.post("/activity/visit")
def activity_visit(body: ActivityVisit) -> dict:
    """The owner opens a business's activity: when they opened it before (on any of their computers), and now is
    kept as the next visit's starting point."""
    with session_scope() as s:
        return {"previous": app_settings.visit(s, f"activity:{body.business_id}")}


@router.get("/activity")
def activity_feed(project_id: int | None = None, entity: str | None = None, actor: str | None = None,
                  verb: str | None = None, entity_id: int | None = None, before: int | None = None,
                  since: datetime | None = None, include_test: bool = False, business_id: int | None = None,
                  limit: Annotated[int, Query(le=500)] = 100) -> dict:
    with session_scope() as s:
        q = _activity_query(project_id, entity, actor, verb, before, since, include_test, s, business_id)
        if entity_id is not None:
            q = q.where(ActivityEvent.entity_id == entity_id)
        rows = s.execute(q.limit(limit + 1)).all()
        actors = [a for (a,) in s.execute(select(ActivityEvent.actor).distinct().order_by(ActivityEvent.actor))]
        return {"events": _event_rows(s, rows[:limit]), "more": len(rows) > limit, "actors": actors}


def _event_row(e: ActivityEvent, pid: int | None, data: dict | None = None) -> dict:
    """The console words a known event from its data; the summary is the line it shows for the rest, so it names
    records by their titles ('REQ-104를 승인해서…: OT-48 …' carried the codes). The export keeps it as written.
    `data` is the event's data with the record's title filled in (activity.named)."""
    if data is not None and isinstance(data.get("title"), str):
        data = {**data, "title": owner_text.title(data["title"])}
    return {**_row(e), "project_id": pid, "summary": owner_text.title(e.summary, 400),
            **({} if data is None else {"data": data})}


def _event_rows(s, pairs) -> list[dict]:
    pairs = list(pairs)
    return [_event_row(e, pid, d) for (e, pid), d in zip(pairs, activity.named(s, [e for e, _ in pairs]))]


def _cell(v: str | None) -> str:
    """Agent-written text must not run as a spreadsheet formula when the owner opens the export."""
    v = v or ""
    return f"'{v}" if v[:1] in ("=", "+", "-", "@", chr(9), chr(13)) else v


@router.get("/activity.csv")
def activity_csv(project_id: int | None = None, entity: str | None = None, actor: str | None = None,
                 verb: str | None = None, since: datetime | None = None, include_test: bool = False,
                 business_id: int | None = None) -> StreamingResponse:
    """The audit trail as a spreadsheet (at most 10,000 rows)."""
    with session_scope() as s:
        rows = s.execute(_activity_query(project_id, entity, actor, verb, None, since, include_test, s, business_id)
                         .limit(10000)).all()
        buf = io.StringIO()
        buf.write("﻿")  # Excel opens UTF-8 Korean correctly only with a BOM
        w = csv.writer(buf)
        w.writerow(["id", "at", "actor", "verb", "entity", "entity_id", "project_id", "summary"])
        for e, pid in rows:
            w.writerow([e.id, e.at.isoformat(), _cell(e.actor), e.verb, e.entity, e.entity_id, pid,
                        _cell(e.summary)])
    name = f"activity-{_now():%Y%m%d-%H%M}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


_RUN_FIELDS = ("id", "call_id", "project_id", "business_id", "task_id", "dev_job_id", "agent", "purpose", "attempt",
               "provider", "model", "status", "input_tokens", "output_tokens", "cached_tokens", "cost_usd", "duration_ms",
               "created_at")


def _run_row(r: LLMCallRecord) -> dict:
    return {**{f: getattr(r, f) for f in _RUN_FIELDS}, "error": (r.error or "")[:400] or None}


@router.get("/runs")
def runs(agent: str | None = None, project_id: int | None = None, task_id: int | None = None,
         status: str | None = None, purpose: str | None = None, before: int | None = None,
         include_test: bool = False, business_id: int | None = None,
         limit: Annotated[int, Query(le=500)] = 100) -> dict:
    with session_scope() as s:
        q = select(LLMCallRecord)
        if agent:
            q = q.where(LLMCallRecord.agent.in_(roles.stored_ids(agent)))
        shown = _calls_shown(_hidden(s, include_test, business_id), business_id)
        if project_id is not None:
            q = q.where(LLMCallRecord.project_id == project_id)
        else:
            q = q.where(shown)
        if task_id is not None:
            q = q.where(LLMCallRecord.task_id == task_id)
        if status == "ok":
            q = q.where(LLMCallRecord.status == "ok")
        elif status == "error":
            q = q.where(LLMCallRecord.status != "ok")
        if purpose:
            q = q.where(LLMCallRecord.purpose == purpose)
        total = s.scalar(select(func.count()).select_from(q.subquery()))
        if before:
            q = q.where(LLMCallRecord.id < before)
        rows = s.scalars(q.order_by(LLMCallRecord.id.desc()).limit(limit + 1)).all()
        return {"runs": [_run_row(r) for r in rows[:limit]], "more": len(rows) > limit, "total": total,
                "purposes": [p for (p,) in s.execute(select(LLMCallRecord.purpose).distinct()
                                                     .where(shown if business_id is not None else true())
                                                     .order_by(LLMCallRecord.purpose))]}


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _clip(text: str | None, limit: int, lang: Lang) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + "\n\n" + msg("console.clipped", lang, n=f"{len(text) - limit:,}")


@router.get("/runs/{run_id}")
def run_detail(run_id: int) -> dict:
    """One model call with what was sent and what came back (the raw files the runtime kept)."""
    lang = app_settings.language()
    with session_scope() as s:
        r = s.get(LLMCallRecord, run_id)
        if r is None:
            raise HTTPException(404, msg("console.no_run", lang, id=run_id))
        out = {**_run_row(r), "error": r.error}
        task = s.get(Task, r.task_id) if r.task_id else None
        out["task"] = {"id": task.id, "title": owner_text.title(task.title), "status": task.status} if task else None
        files: dict[str, Any] = {}
        root = (settings().var_dir / "calls").resolve()
        if r.raw_path:
            d = Path(r.raw_path).resolve()
            # Only the runtime's own call folders are readable, whatever the row says.
            if d.is_relative_to(root) and d.is_dir():
                files["system_prompt"] = _clip(_read(d / "system_prompt.md"), 20000, lang)
                files["prompt"] = _clip(_read(d / "prompt.md"), 60000, lang)
                whole = _read(d / "output.json")
                raw = _clip(whole, 60000, lang)
                try:
                    files["output"] = json.loads(raw) if raw and raw == whole else raw
                except json.JSONDecodeError:
                    files["output"] = raw
        out["files"] = files
        return out


_TASK_LIST = ("id", "project_id", "key", "title", "owner", "status", "origin", "plan_round", "iteration",
              "max_iterations", "contested", "spent_usd", "budget_usd", "depends_on", "created_at", "updated_at",
              "started_at", "finished_at")


@router.get("/tasks")
def task_list(project_id: int | None = None, status: str | None = None, owner: str | None = None,
              q: str | None = None, include_test: bool = False, business_id: int | None = None,
              limit: Annotated[int, Query(le=2000)] = 1000) -> dict:
    with session_scope() as s:
        query = select(Task, Project.name).join(Project, Project.id == Task.project_id)
        if project_id is not None:
            query = query.where(Task.project_id == project_id)
        elif hidden := _hidden(s, include_test, business_id):
            query = query.where(Task.project_id.notin_(hidden))
        if status:
            query = query.where(Task.status.in_(status.split(",")))
        if owner:
            query = query.where(Task.owner == owner)
        if q:
            like = f"%{q.strip()}%"
            m = re.fullmatch(r"\s*T-?(\d+)\s*", q, flags=re.I)
            query = query.where(or_(Task.title.ilike(like), Task.objective.ilike(like),
                                    Task.id == int(m.group(1)) if m else False))
        rows = s.execute(query.order_by(Task.updated_at.desc()).limit(limit)).all()
        open_reqs: dict[int, int] = {}
        for tid, n in s.execute(select(AgentRequest.task_id, func.count()).where(
                AgentRequest.status == "PENDING", AgentRequest.task_id.is_not(None)).group_by(AgentRequest.task_id)):
            open_reqs[tid] = n
        out = []
        held = stops.read(s)
        for t, pname in rows:
            out.append({**{f: getattr(t, f) for f in _TASK_LIST}, "title": owner_text.title(t.title), "project": pname,
                        "verdict": (t.review or {}).get("verdict"), "outcome": (t.review or {}).get("outcome"),
                        "error": (t.error or "")[:300] or None,
                        "evidence": len((t.result or {}).get("evidence_ids", [])), "pending": open_reqs.get(t.id, 0),
                        "waiting_on": stops.waiting_on(t, held)})
        return {"tasks": out, "owners": sorted(tasks_owners())}


def tasks_owners() -> set[str]:
    from justshipit.services.tasks import _assignable_owners
    return _assignable_owners()


def _task_can(s, t: Task, p: Project) -> dict[str, bool]:
    closed = _closed_to_work(p)
    refused = (stop := _budget_stop(s, t.error, t.project_id)) is not None and stop["short"]
    return {"retry": t.status == "FAILED" and not closed and not refused, "cancel": t.status in _OPEN,
            "edit": t.status in _OPEN, "comment": p.status != "ARCHIVED", "add": not closed}


@router.get("/tasks/{task_id}")
def task_detail(task_id: int) -> dict:
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t is None:
            raise HTTPException(404, msg("console.no_task", app_settings.language(), id=task_id))
        p = s.get(Project, t.project_id)
        ev_ids = (t.result or {}).get("evidence_ids", [])
        deps = [{"id": d.id, "title": owner_text.title(d.title), "status": d.status} for d in
                s.scalars(select(Task).where(Task.id.in_(t.depends_on or [0])))]
        dependents = [{"id": d.id, "title": owner_text.title(d.title), "status": d.status} for d in
                      s.scalars(select(Task).where(Task.project_id == t.project_id,
                                                   Task.depends_on.contains([t.id])))]
        r = t.result or {}
        said = {k: tasks.document(r[k], ev_ids) for k in ("summary", "findings_markdown") if isinstance(r.get(k), str)}
        return {
            "task": {**_row(t), "result": {**r, **said} if t.result else t.result,
                     "objective": owner_text.body(t.objective),
                     "acceptance_criteria": [owner_text.body(c) for c in t.acceptance_criteria or []]},
            "project": {"id": p.id, "name": p.name, "status": p.status, "phase": p.phase, "business_id": p.business_id,
                        **project_state.of(s, p)},
            "evidence": [_row(e) for e in s.scalars(select(Evidence).where(Evidence.id.in_(ev_ids or [0]))
                                                       .order_by(Evidence.id))],
            "comments": [_row(m) for m in s.scalars(select(AgentMessage).where(
                AgentMessage.ref_type == "task", AgentMessage.ref_id == t.id, AgentMessage.kind == "comment")
                .order_by(AgentMessage.id))],
            "runs": [_run_row(r) for r in s.scalars(select(LLMCallRecord).where(LLMCallRecord.task_id == t.id)
                                                     .order_by(LLMCallRecord.id))],
            "requests": [{"id": r.id, "title": owner_text.title(r.title), "status": r.status, "type": r.type, "blocking": r.blocking}
                         for r in s.scalars(select(AgentRequest).where(AgentRequest.task_id == t.id)
                                            .order_by(AgentRequest.id))],
            "activity": [_event_row(e, t.project_id) for e in s.scalars(select(ActivityEvent).where(
                ActivityEvent.entity == "task", ActivityEvent.entity_id == t.id).order_by(ActivityEvent.at))],
            "depends_on": deps,
            "dependents": dependents,
            "reviewer": staffing.verifier_of(s, t),
            "fit": strengths.fit(strengths.card_areas(None, t.owner), app_settings.reader()[1]),
            "waiting_on": stops.waiting_on(t, stops.read(s)),
            "failure": (task_failure(s, t, lang := app_settings.language(),
                                     stop=_budget_stop(s, t.error, t.project_id, lang))
                        if t.status == "FAILED" else None),
            "can": _task_can(s, t, p),
        }


@router.get("/evidence")
def evidence_by_ids(ids: str) -> list[dict]:
    """Evidence rows for the ids a request, decision or report cites ("12,EV-13")."""
    wanted = sorted({int(x) for x in re.findall(r"\d+", ids)})[:300]
    with session_scope() as s:
        return [_row(e) for e in s.scalars(select(Evidence).where(Evidence.id.in_(wanted or [0])).order_by(Evidence.id))]


@router.get("/agents")
def agent_list(include_test: bool = False, business_id: int | None = None) -> list[dict]:
    with session_scope() as s:
        hidden = _hidden(s, include_test, business_id)
        states = _agent_states(s, hidden, business_id)
        total = {a: (n, float(c or 0)) for a, n, c in s.execute(
            select(LLMCallRecord.agent, func.count(), func.sum(LLMCallRecord.cost_usd))
            .where(_calls_shown(hidden, business_id)).group_by(LLMCallRecord.agent))}
        mine = Task.project_id.notin_(hidden) if business_id is not None and hidden else true()
        owned = {o: n for o, n in s.execute(select(Task.owner, func.count()).where(mine).group_by(Task.owner))}
        return [{"id": k, **{x: v.get(x) for x in ("title", "department", "provider", "model", "runtime")},
                 "tools": v.get("tools", []), "reviewer": bool(v.get("reviewer")), **states[k],
                 "calls_total": total.get(k, (0, 0.0))[0], "cost_total": total.get(k, (0, 0.0))[1],
                 "tasks_total": owned.get(k, 0)} for k, v in agents_config().items()]


@router.get("/agents/{agent_id}")
def agent_detail(agent_id: str, tz: str | None = None, include_test: bool = False,
                 business_id: int | None = None) -> dict:
    """One agent's work, calls and costs. With `business_id`, the selected business's only (services/scope.py)."""
    cfg = agents_config().get(agent_id)
    if cfg is None:
        raise HTTPException(404, msg("console.no_agent", app_settings.language(), id=agent_id))
    tz = _tz(tz)
    with session_scope() as s:
        hidden = _hidden(s, include_test, business_id) if business_id is not None else _archived_ids(s)
        calls = _calls_shown(hidden, business_id)
        state = _agent_states(s, hidden, business_id)[agent_id]
        days = _days(14, tz)
        since = datetime.fromisoformat(days[0]).replace(tzinfo=ZoneInfo(tz))
        series = {d: {"day": d, "ok": 0, "error": 0, "cost_usd": 0.0} for d in days}
        for d, ok, n, c in s.execute(
                select(_day(LLMCallRecord.created_at, tz).label("d"), LLMCallRecord.status, func.count(),
                       func.sum(LLMCallRecord.cost_usd))
                .where(LLMCallRecord.agent.in_(roles.stored_ids(agent_id)), LLMCallRecord.created_at >= since, calls)
                .group_by("d", LLMCallRecord.status)):
            if (k := d.isoformat()) in series:
                series[k]["ok" if ok == "ok" else "error"] += n
                series[k]["cost_usd"] += float(c or 0)
        by_purpose = [dict(zip(("purpose", "calls", "errors", "cost_usd", "duration_ms"), r)) for r in s.execute(
            select(LLMCallRecord.purpose, func.count(), func.count().filter(LLMCallRecord.status != "ok"),
                   func.sum(LLMCallRecord.cost_usd), func.avg(LLMCallRecord.duration_ms))
            .where(LLMCallRecord.agent.in_(roles.stored_ids(agent_id)), calls).group_by(LLMCallRecord.purpose))]
        recent_runs = [_run_row(r) for r in s.scalars(
            select(LLMCallRecord).where(LLMCallRecord.agent.in_(roles.stored_ids(agent_id)), calls)
            .order_by(LLMCallRecord.id.desc()).limit(30))]
        owned = s.execute(select(Task.id, Task.title, Task.status, Task.project_id, Project.name, Task.updated_at,
                                 Task.review).join(Project, Project.id == Task.project_id)
                          .where(Task.owner == agent_id, Task.project_id.notin_(hidden) if hidden else true())
                          .order_by(Task.updated_at.desc()).limit(40)).all()
        skills = [{"id": k.id, "name": k.name, "version": k.version, "kind": k.kind, "status": k.status}
                  for k in s.scalars(select(Skill).where(Skill.target_agents.contains([agent_id])).order_by(Skill.id))]
        prompt = None
        if cfg.get("prompt"):
            prompt = _clip(_read(settings().prompts_dir / "agents" / cfg["prompt"]), 30000, app_settings.language())
        return {
            "id": agent_id, **{x: cfg.get(x) for x in ("title", "department", "provider", "model", "runtime", "prompt")},
            "tools": cfg.get("tools", []), "reviewer": bool(cfg.get("reviewer")),
            **state, "series": list(series.values()), "by_purpose": by_purpose, "runs": recent_runs,
            "tasks": [{"id": i, "title": owner_text.title(ti), "status": st, "project_id": pid, "project": pn, "updated_at": u,
                       "verdict": (rv or {}).get("verdict"), "outcome": (rv or {}).get("outcome")}
                      for i, ti, st, pid, pn, u, rv in owned],
            "skills": skills, "prompt_text": prompt,
        }


_ID = re.compile(r"^\s*(P|T|REQ|EV|DEC|J|M)-?(\d+)\s*$", re.I)


@router.get("/search")
def search(q: str, include_test: bool = False) -> list[dict]:
    """Command palette: an id jumps straight to its record; words match titles and claims."""
    q = q.strip()
    if not q:
        return []
    out: list[dict] = []
    with session_scope() as s:
        if m := _ID.match(q):
            kind, n = m.group(1).upper(), int(m.group(2))
            model = {"P": Project, "T": Task, "REQ": AgentRequest, "EV": Evidence, "DEC": Decision, "J": DevJob,
                     "M": Meeting}[kind]
            o = s.get(model, n)
            if o is not None:
                title = getattr(o, "title", None) or getattr(o, "name", None) or getattr(o, "claim", None) \
                    or getattr(o, "agenda", None) or ""
                pid = o.id if kind == "P" else getattr(o, "project_id", None)
                if kind == "J":
                    sp = s.get(Spec, o.spec_id)
                    title, pid = sp.title, sp.project_id
                out.append({"kind": kind, "id": n, "title": owner_text.title(title) if kind in ("T", "REQ", "DEC", "J") else title[:200], "project_id": pid,
                            "status": getattr(o, "status", None) or getattr(o, "verification_status", None)})
            return out
        hidden = _hidden(s, include_test)
        like = f"%{q}%"
        for p in s.scalars(select(Project).where(Project.name.ilike(like) | Project.goal.ilike(like)).limit(5)):
            if p.id not in _test_ids(s) or include_test:
                out.append({"kind": "P", "id": p.id, "title": p.name, "project_id": p.id, "status": p.status})
        for t in s.scalars(select(Task).where(Task.title.ilike(like)).order_by(Task.id.desc()).limit(8)):
            if t.project_id not in hidden:
                out.append({"kind": "T", "id": t.id, "title": owner_text.title(t.title), "project_id": t.project_id, "status": t.status})
        for r in s.scalars(select(AgentRequest).where(AgentRequest.title.ilike(like)).order_by(AgentRequest.id.desc())
                           .limit(6)):
            if r.project_id not in hidden:
                out.append({"kind": "REQ", "id": r.id, "title": owner_text.title(r.title), "project_id": r.project_id, "status": r.status})
        for d in s.scalars(select(Decision).where(Decision.title.ilike(like)).limit(4)):
            if d.project_id not in hidden:
                out.append({"kind": "DEC", "id": d.id, "title": owner_text.title(d.title), "project_id": d.project_id, "status": d.status})
        for e in s.scalars(select(Evidence).where(Evidence.claim.ilike(like)).order_by(Evidence.id.desc()).limit(5)):
            if e.project_id not in hidden:
                out.append({"kind": "EV", "id": e.id, "title": e.claim[:200], "project_id": e.project_id,
                            "status": e.verification_status})
    for k, v in agents_config().items():
        if q.lower() in k.lower() or q.lower() in v.get("title", "").lower():
            out.append({"kind": "AGENT", "id": k, "title": v.get("title", k), "project_id": None, "status": None})
    return out


_REF = re.compile(r"(T|REQ|OT|EV|P|DEC|J)-(\d{1,9})")
# What each code names: its model and the column the owner reads. A build is named by its spec.
_REF_MODELS = {"T": (Task, Task.title), "REQ": (AgentRequest, AgentRequest.title), "OT": (OwnerTask, OwnerTask.title),
               "EV": (Evidence, Evidence.claim), "P": (Project, Project.name), "DEC": (Decision, Decision.title),
               "J": (DevJob, Spec.title)}


@router.get("/refs")
def refs(codes: str) -> dict[str, dict]:
    """What the codes in an agent's text point at, so the console links each by its title, or marks a piece of
    evidence it cites with its claim, and never shows the owner the code. A code with no record is left out. A piece
    of evidence also names its project, so a link to it works from a screen that does not know the project."""
    wanted: dict[str, set[int]] = {}
    for code in codes.split(",")[:200]:
        if m := _REF.fullmatch(code.strip()):
            wanted.setdefault(m.group(1), set()).add(int(m.group(2)))
    out: dict[str, dict] = {}
    with session_scope() as s:
        for kind, ids in wanted.items():
            model, col = _REF_MODELS[kind]
            if kind == "EV":
                for i, said, pid in s.execute(select(Evidence.id, Evidence.claim, Evidence.project_id).where(Evidence.id.in_(ids))):
                    out[f"EV-{i}"] = {"title": owner_text.one_line(said, 160), "project_id": pid}
                continue
            q = select(model.id, col).where(model.id.in_(ids))
            for i, said in s.execute(q.join(Spec, Spec.id == DevJob.spec_id) if kind == "J" else q):
                out[f"{kind}-{i}"] = {"title": owner_text.title(said, 40)}
    return out


@router.get("/costs")
def costs(tz: str | None = None, days: Annotated[int, Query(le=120)] = 30) -> dict:
    """Spend by day, project, agent, model and purpose (USD = list-price equivalents the CLIs report)."""
    tz = _tz(tz)
    with session_scope() as s:
        span = _days(days, tz)
        since = datetime.fromisoformat(span[0]).replace(tzinfo=ZoneInfo(tz))
        by_day = {d: {"day": d, "cost_usd": 0.0, "calls": 0, "errors": 0} for d in span}
        for d, c, n, e in s.execute(
                select(_day(LLMCallRecord.created_at, tz).label("d"), func.sum(LLMCallRecord.cost_usd), func.count(),
                       func.count().filter(LLMCallRecord.status != "ok"))
                .where(LLMCallRecord.created_at >= since).group_by("d")):
            if (k := d.isoformat()) in by_day:
                by_day[k].update(cost_usd=float(c or 0), calls=n, errors=e)

        def grouped(*cols):
            return s.execute(select(*cols, func.count(), func.count().filter(LLMCallRecord.status != "ok"),
                                    func.sum(LLMCallRecord.input_tokens), func.sum(LLMCallRecord.output_tokens),
                                    func.sum(LLMCallRecord.cost_usd)).group_by(*cols)).all()
        names = {p.id: p for p in s.scalars(select(Project))}
        businesses = dict(s.execute(select(Business.id, Business.name)).all())
        by_project = []
        for pid, n, e, i, o, c in grouped(LLMCallRecord.project_id):
            p = names.get(pid)
            bid = p.business_id if p else None
            by_project.append({"project_id": pid, "name": p.name if p else None, "status": p.status if p else None,
                               "business_id": bid, "business": businesses.get(bid),
                               "budget_usd": p.budget_usd if p else None, "spent_usd": p.spent_usd if p else None,
                               "budget_short": budget.retry_blocked(s, p), **(project_state.of(s, p) if p else {}),
                               "is_test": bool(p and p.name.startswith("[TEST]")), "calls": n, "errors": e,
                               "input_tokens": i or 0, "output_tokens": o or 0, "cost_usd": float(c or 0)})
        keys = ("calls", "errors", "input_tokens", "output_tokens", "cost_usd")
        # What this month's calls cost at list price, from the ledger every other spend figure adds up (Agents, the
        # stage card, by project): the owner's "what did this cost". The budget's own charge is the month row.
        month_start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        used, month_calls = s.execute(select(func.coalesce(func.sum(LLMCallRecord.cost_usd), 0.0), func.count())
                                      .where(LLMCallRecord.created_at >= month_start)).one()
        return {
            "month_used_usd": round(float(used), 4), "month_calls": month_calls,
            "months": [_row(m) for m in s.scalars(select(BudgetMonth).order_by(BudgetMonth.month.desc()))],
            "default_month_limit": float(limits()["monthly_budget_usd"]),
            "usage": budget.usage_view(),
            "by_day": list(by_day.values()),
            "by_project": sorted(by_project, key=lambda r: -r["cost_usd"]),
            "by_agent": [{"agent": a, "provider": pv, **dict(zip(keys, r))}
                         for a, pv, *r in grouped(LLMCallRecord.agent, LLMCallRecord.provider)],
            "by_model": [{"provider": pv, "model": m, **dict(zip(keys, r))}
                         for pv, m, *r in grouped(LLMCallRecord.provider, LLMCallRecord.model)],
            "by_purpose": [{"purpose": pu, **dict(zip(keys, r))} for pu, *r in grouped(LLMCallRecord.purpose)],
        }


@router.get("/settings")
def company_settings() -> dict:
    """The operating policy as the server enforces it, and the owner's overrides of it. `defaults` are the files'
    values, `overrides` what the owner saved, `effective` the two merged. Credentials are never returned, only
    whether each is set and its last four characters."""
    cc = company_config()
    with session_scope() as s:
        month = s.get(BudgetMonth, _now().strftime("%Y-%m"))
    rows = app_settings.stored()
    lang = app_settings.language()
    effective, problems = app_settings.load(lang)
    return {
        "company": cc["company"], "limits": cc["limits"], "providers": cc["providers"], "autonomy": cc["autonomy"],
        "engineering": {k: cc["engineering"].get(k) for k in ("isolation", "test_sandbox", "max_fix_iterations",
                                                              "sandbox", "disallowed_tools")},
        "operations": cc.get("operations", {}),
        "tool_policy": tool_policy(),
        "agents": [{"id": k, **{x: v.get(x) for x in ("title", "department", "provider", "model", "runtime")},
                    "tools": v.get("tools", [])} for k, v in agents_config().items()],
        "month": _row(month) if month else None,
        # the monthly budget in force: this month's own limit once the month has a row, the standing one before
        "month_limit_usd": float(month.limit_usd) if month else float(limits()["monthly_budget_usd"]),
        "config_dir": str(settings().config_dir),
        "defaults": app_settings.defaults().model_dump(mode="json"),
        "overrides": rows,
        "effective": effective.model_dump(mode="json"),
        "problems": problems,
        "supported_modes": app_settings.SUPPORTED_MODES,
        "policy_modes": {k: v.value for k, v in policy_service.modes().items()},
        "policy_floors": {k: app_settings.floor(PolicyMode(v)).value for k, v in tool_policy().items()},
        "usage_needs": {("owner" if owner_facing else "background"): {"what": what, "usd_eq": usd}
                        for owner_facing, (what, usd) in usage.class_needs(lang).items()},
        "secrets": secrets.status(lang),
        "sending": sending.view(effective, lang),
    }


@router.get("/settings/sending")
def settings_sending() -> dict:
    """What really leaves the company right now, for the top bar: each live-capable connector's mode, what it lacks,
    and whether it really sends. No value of any setting or credential is returned."""
    lang = app_settings.language()
    return sending.view(app_settings.effective(), lang)


def _secret_label(name: str, lang: Lang) -> str:
    if name not in secrets.REGISTRY:
        raise HTTPException(404, msg("settings.secret.unknown", lang))
    return secrets.label(name, lang)


@router.put("/settings/secrets/{name}")
def settings_secret_put(name: str, body: Annotated[Any, Body()]) -> dict:
    """Store a connector credential. The response, the activity event and every refusal carry no part of the
    value; the body is checked here, because a framework's default 422 would echo the input back."""
    lang = app_settings.language()
    label = _secret_label(name, lang)
    if not isinstance(body, dict) or not isinstance(body.get("value"), str):
        raise HTTPException(422, msg("settings.secret.body", lang))
    try:
        with activity.as_actor("owner"), session_scope() as s:
            secrets.put(name, body["value"])
            activity.record(s, verb="secret_set", entity="settings", entity_id=None, project_id=None,
                            summary=msg("console.event.secret_set", lang, label=label), data={"name": name})
    except secrets.SecretError as exc:
        raise HTTPException(422, exc.say(lang)) from exc
    if name == "slack_bot_token":
        slack.reconnected()
    return secrets.status(lang)[name]


@router.delete("/settings/secrets/{name}")
def settings_secret_delete(name: str) -> dict:
    lang = app_settings.language()
    label = _secret_label(name, lang)
    try:
        with activity.as_actor("owner"), session_scope() as s:
            if secrets.clear(name):
                activity.record(s, verb="secret_cleared", entity="settings", entity_id=None, project_id=None,
                                summary=msg("console.event.secret_cleared", lang, label=label), data={"name": name})
    except secrets.SecretError as exc:
        raise HTTPException(409, exc.say(lang)) from exc
    return secrets.status(lang)[name]


@router.post("/settings/test/slack")
def settings_test_slack() -> dict:
    """Send one test message the way the connector's mode sends every notice. The answer is a sentence in the
    owner's language and never carries the token. Nothing runs inside a database transaction: Slack may be slow."""
    try:
        result = slack.send_test()
    except slack.ConnectorOff as exc:
        raise HTTPException(409, str(exc)) from None
    except slack.BadToken as exc:
        raise HTTPException(422, str(exc)) from None
    except slack.RateLimited as exc:
        raise HTTPException(429, str(exc)) from None
    except slack.NotifyError as exc:
        raise HTTPException(502, str(exc)) from None
    with activity.as_actor("owner"), session_scope() as s:
        activity.record(s, verb="connection_tested", entity="settings", entity_id=None, project_id=None,
                        summary=msg("console.event.slack_test", app_settings.language(), mode=result["mode"]),
                        data={"connector": "slack", "mode": result["mode"]})
    return result


def _sends_unapproved(cfg: app_settings.AppSettings) -> set[str]:
    """The actions that would leave for real with no one approving them: their connector is live and the decision
    rules let the request approve itself (policy mode auto)."""
    modes, rules = cfg.connectors.modes(), policy_service.modes(cfg.policy.overrides)
    return {action for action, connector in app_settings.ACTION_CONNECTOR.items()
            if modes.get(connector) == "live" and rules.get(action) == PolicyMode.AUTO}


def _outward_warnings(now: app_settings.AppSettings, after: app_settings.AppSettings, lang: Lang) -> list[dict]:
    """What this save would newly send without the owner's approval. The save goes ahead (the owner decides); the
    console shows the warning and can ask first with `dry_run`."""
    return [{"kind": "sends_without_approval", "action": action,
             "connector": app_settings.ACTION_CONNECTOR[action],
             "text": msg("settings.warn.sends_without_approval", lang,
                         connector=msg(f"operations.connector.{app_settings.ACTION_CONNECTOR[action]}", lang))}
            for action in sorted(_sends_unapproved(after) - _sends_unapproved(now))]


@router.put("/settings/{section}")
def settings_put(section: str, body: Annotated[Any, Body()], dry_run: bool = False) -> dict:
    """Make `body` the owner's overrides of one section (usage, connectors, policy, notifications). Fields left
    out fall back to the files' defaults. A floor or an unsupported connector mode is refused. `warnings` names what
    the save would newly send for real without the owner's approval, and `cannot_send` each connector it turns live
    that could not send (sending.newly_blocked). A save that turns Slack live before its connection test passed is
    refused with what to do first (sending.refused_live). `dry_run` answers the same without saving, so the console
    can ask before it saves."""
    lang = app_settings.language()
    if not isinstance(body, dict):
        raise HTTPException(422, msg("settings.body", lang))
    try:
        now = app_settings.effective()
        after = now.model_copy(update={section: app_settings.merged(section, body, lang=lang)})
        if refusal := sending.refused_live(now, after, lang):
            raise HTTPException(409, refusal)
        warnings, blocked = _outward_warnings(now, after, lang), sending.newly_blocked(now, after, lang)
        with activity.as_actor("owner"), session_scope() as s:
            trial = s.begin_nested()
            changed = app_settings.save(s, section, body, actor="owner", lang=lang)
            if dry_run:
                trial.rollback()
            else:
                trial.commit()
                if changed and section == "usage":
                    queue.release_usage_waits(s)  # a raised cap lets the waiting work try again now
                if changed:
                    activity.record(s, verb="settings", entity="settings", entity_id=None, project_id=None,
                                    summary=msg("console.event.settings", lang, section=section,
                                                fields=", ".join(c["key"] for c in changed)),
                                    data={"section": section, "changes": changed})
    except app_settings.UnknownSection:
        raise HTTPException(404, msg("settings.unknown_section", lang)) from None
    except ValidationError as exc:
        raise HTTPException(422, app_settings.describe(exc, lang)) from None
    return {"section": section, "changed": changed, "overrides": app_settings.stored().get(section, {}),
            "effective": getattr(app_settings.effective(), section).model_dump(mode="json"), "warnings": warnings,
            "cannot_send": blocked, "dry_run": dry_run}


# ============================================================== roadmap

@router.get("/businesses")
def business_list(include_archived: bool = False, include_test: bool = False) -> list[dict]:
    """Every business with its bet counts, ladder position and the projects that ran for it."""
    with session_scope() as s:
        return roadmap.business_list(s, include_archived=include_archived, include_test=include_test)


@router.get("/businesses/{business_id}/roadmap")
def business_roadmap(business_id: int) -> dict:
    """The roadmap home in one round trip: ladder, tracks, models, bets with their runs, cycle, checkpoints."""
    with session_scope() as s:
        try:
            return roadmap.roadmap(s, business_id, lang=app_settings.language())
        except control.ControlError as exc:
            raise HTTPException(404, str(exc)) from exc


@router.get("/businesses/{business_id}/brief")
def business_brief(business_id: int) -> dict:
    """The approved brief in its sections with each line's source and the owner's own quote, for the business page."""
    with session_scope() as s:
        try:
            return roadmap.brief_view(s, business_id, lang=app_settings.language())
        except control.ControlError as exc:
            raise HTTPException(404, str(exc)) from exc


class ListsIn(BaseModel):
    rungs: list[dict] | None = None
    tracks: list[dict] | None = None
    models: list[dict] | None = None
    door_policy: list[dict] | None = None
    checkpoints: list[dict] | None = None


class BetNewIn(BaseModel):
    title: str
    pitch: str
    audience: str | None = None
    question: str | None = None
    appetite_days: int | None = Field(default=None, gt=0)
    budget_usd: float | None = Field(default=None, gt=0)
    exit_signal: str | None = None
    circuit_breaker: str | None = None
    track_id: str | None = None
    rung_id: str | None = None
    run_mode: str = "ai"


class BetEditIn(BetNewIn):
    title: str | None = None
    pitch: str | None = None
    run_mode: str | None = None
    hill: str | None = None
    health: str | None = None
    outcome: str | None = None


class BetMoveIn(BaseModel):
    to: str
    reason: str | None = None
    cycle_id: int | None = None


class BriefIn(BaseModel):
    sections: list[dict]
    assumptions: list[dict] = []
    approved: bool = False


@router.post("/businesses/{business_id}/lists")
def business_lists(business_id: int, body: ListsIn) -> dict:
    return _act(roadmap.update_lists, business_id, lang=app_settings.language(), **body.model_dump())


@router.post("/businesses/{business_id}/bets")
def business_new_bet(business_id: int, body: BetNewIn) -> dict:
    return _act(roadmap.create_bet, business_id, lang=app_settings.language(), **body.model_dump())


@router.post("/businesses/{business_id}/briefs")
def business_new_brief(business_id: int, body: BriefIn) -> dict:
    return _act(roadmap.new_brief_version, business_id, sections=body.sections, assumptions=body.assumptions,
                author="owner", approved=body.approved, lang=app_settings.language())


@router.post("/briefs/{brief_id}/approve")
def brief_approve(brief_id: int) -> dict:
    lang = app_settings.language()

    def approve(s, bid):
        brief = roadmap.approve_brief(s, bid, lang=lang)
        return {**_row(brief), "empty_defaults": roadmap.empty_defaults(brief.sections, lang=lang)}
    return _act(approve, brief_id)


@router.post("/bets/{bet_id}/move")
def bet_move(bet_id: int, body: BetMoveIn) -> dict:
    return _act(roadmap.move_bet, bet_id, body.to, reason=body.reason, cycle_id=body.cycle_id,
                lang=app_settings.language(), by_owner=True)


@router.post("/bets/{bet_id}/edit")
def bet_edit(bet_id: int, body: BetEditIn) -> dict:
    return _act(roadmap.update_bet, bet_id, lang=app_settings.language(), **body.model_dump())


# ============================================================== owner actions

def _act(fn, *args, **kwargs):
    """Run an owner action in its own transaction; refusals become 409 with the service's message."""
    with activity.as_actor("owner"):
        try:
            with session_scope() as s:
                result = fn(s, *args, **kwargs)
                s.flush()
                return _row(result) if hasattr(result, "__table__") else result
        except control.NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except control.ControlError as exc:
            raise HTTPException(409, str(exc)) from exc


class PauseIn(BaseModel):
    reason: str | None = None


class BudgetIn(BaseModel):
    budget_usd: float = Field(gt=0)
    note: str | None = None
    resume: bool = False


class TextIn(BaseModel):
    text: str


class RenameIn(BaseModel):
    name: str


class TaskEditIn(BaseModel):
    title: str | None = None
    objective: str | None = None
    acceptance_criteria: list[str] | None = None
    owner: str | None = None


class TaskNewIn(BaseModel):
    title: str
    objective: str
    owner: str
    acceptance_criteria: list[str] = []
    depends_on: list[int] = []


class CommentIn(BaseModel):
    body: str


class ReasonIn(BaseModel):
    reason: str | None = None


class MonthIn(BaseModel):
    limit_usd: float = Field(gt=0)


@router.get("/company")
def company() -> dict:
    """Whether the owner has stopped the whole company, since when and why."""
    with session_scope() as s:
        return stops.company_state(s)


@router.post("/company/stop")
def stop_company(body: ReasonIn | None = None) -> dict:
    """The owner's emergency stop: nothing new starts but housekeeping, and running work stops at its next check and
    goes back to the queue. Stopping again changes nothing."""
    return _act(stops.stop_company, body.reason if body else None)


@router.post("/company/resume")
def resume_company() -> dict:
    return _act(stops.resume_company)


def _agent_act(fn, key: str, *args) -> dict:
    try:
        return _act(fn, key, *args)
    except stops.UnknownAgent as exc:
        raise HTTPException(404, msg("console.no_agent", app_settings.language(), id=key)) from exc


@router.post("/agents/{agent_id}/stop")
def stop_agent(agent_id: str, body: ReasonIn | None = None) -> dict:
    """Stop one agent: what it is doing stops at its next step and waits, and it takes no new work until resumed."""
    return _agent_act(stops.stop_agent, agent_id, body.reason if body else None)


@router.post("/agents/{agent_id}/resume")
def resume_agent(agent_id: str) -> dict:
    return _agent_act(stops.resume_agent, agent_id)


@router.post("/projects/{project_id}/pause")
def pause_project(project_id: int, body: PauseIn) -> dict:
    return _act(control.pause, project_id, body.reason)


@router.post("/projects/{project_id}/resume")
def resume_project(project_id: int) -> dict:
    return _act(control.resume, project_id)


@router.post("/projects/{project_id}/retry")
def retry_project(project_id: int) -> dict:
    queue.settle_lapsed()
    return _act(control.retry_project, project_id)


@router.post("/projects/{project_id}/budget")
def project_budget(project_id: int, body: BudgetIn) -> dict:
    return _act(control.set_budget, project_id, body.budget_usd, body.note, resume_run=body.resume)


@router.post("/projects/{project_id}/directives")
def project_directive(project_id: int, body: TextIn) -> dict:
    return _act(control.add_directive, project_id, body.text)


class RuleRemoveIn(BaseModel):
    label: str


@router.post("/projects/{project_id}/directives/{n}/rules/{index}/remove")
def project_directive_rule_remove(project_id: int, n: int, index: int, body: RuleRemoveIn) -> dict:
    return _act(control.remove_directive_rule, project_id, n, index, body.label)


@router.post("/projects/{project_id}/rename")
def project_rename(project_id: int, body: RenameIn) -> dict:
    return _act(control.rename, project_id, body.name)


@router.post("/projects/{project_id}/unarchive")
def project_unarchive(project_id: int) -> dict:
    return _act(control.unarchive, project_id)


@router.post("/projects/{project_id}/tasks")
def project_new_task(project_id: int, body: TaskNewIn) -> dict:
    return _act(control.create_task, project_id, **body.model_dump())


@router.post("/projects/{project_id}/tasks/retry-failed")
def project_retry_failed(project_id: int) -> dict:
    def go(s, pid):
        control.accepts_work(s, pid)
        return {"retried": control.retry_failed_tasks(s, pid)}
    return _act(go, project_id)


class TaskRetryIn(BaseModel):
    note: str | None = None


@router.post("/tasks/{task_id}/retry")
def task_retry(task_id: int, body: TaskRetryIn | None = None) -> dict:
    """The task row, with what the retry changed: the budget it raised so a fresh attempt can run (`budget_was_usd`
    beside `budget_usd`), and whether a worker is there to run it now (`worker_running`). A `note` is the owner's
    comment for the new attempt, saved with the retry or not at all."""
    def go(s, tid):
        was = s.scalar(select(Task.budget_usd).where(Task.id == tid))
        t = control.retry_task(s, tid)
        if body and body.note and body.note.strip():
            control.comment_task(s, tid, body.note)
        return {**_row(t), "budget_was_usd": was, "worker_running": queue.last_worker_seen(s)[1]}
    return _act(go, task_id)


@router.post("/tasks/{task_id}/cancel")
def task_cancel(task_id: int, body: ReasonIn) -> dict:
    return _act(control.cancel_task, task_id, body.reason)


@router.post("/tasks/{task_id}/edit")
def task_edit(task_id: int, body: TaskEditIn) -> dict:
    return _act(control.update_task, task_id, **body.model_dump())


@router.post("/tasks/{task_id}/comments")
def task_comment(task_id: int, body: CommentIn) -> dict:
    return _act(control.comment_task, task_id, body.body)


@router.post("/dev-jobs/{job_id}/retry")
def dev_retry(job_id: int) -> dict:
    from justshipit.engineering.specs import retry_dev
    with session_scope() as s:
        job = s.get(DevJob, job_id)
        if job is None:
            raise HTTPException(404, msg("console.no_dev_job", app_settings.language(), id=job_id))
        key = s.get(Spec, job.spec_id).spec_key
    with activity.as_actor("owner"):
        try:
            return {"dev_job_id": retry_dev(key, app_settings.language())}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc


@router.post("/dev-jobs/{job_id}/deliver-again")
def dev_deliver_again(job_id: int) -> dict:
    """Deliver the reviewed branch of a job that stopped while shipping, from that step on."""
    from justshipit.engineering.worker import deliver_again
    with activity.as_actor("owner"):
        try:
            deliver_again(job_id, app_settings.language())
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    return {"dev_job_id": job_id}


@router.post("/dev-jobs/{job_id}/accept-again")
def dev_accept_again(job_id: int) -> dict:
    from justshipit.engineering.worker import accept_again
    with activity.as_actor("owner"):
        try:
            accept_again(job_id, app_settings.language())
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    return {"dev_job_id": job_id}


@router.post("/dev-jobs/{job_id}/merge-again")
def dev_merge_again(job_id: int) -> dict:
    from justshipit.engineering.worker import merge_again
    with activity.as_actor("owner"):
        try:
            return {"dev_job_id": job_id, "request_id": merge_again(job_id, app_settings.language())}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc


@router.get("/dev-jobs/{job_id}/screens/{name}")
def dev_screen(job_id: int, name: str) -> FileResponse:
    """A screen the acceptance check captured of the delivered branch. Served as an image only: the branch's own
    scripts wrote it, so nothing else may read it as a page."""
    from justshipit.engineering.try_it import screen_file
    with session_scope() as s:
        job = s.get(DevJob, job_id)
        path = screen_file(job, name) if job is not None else None
    if path is None:
        raise HTTPException(404, msg("console.no_screen", app_settings.language(), id=job_id))
    return FileResponse(path, media_type="image/png", headers={
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox", "Cache-Control": "private, max-age=300"})


@router.get("/dev-jobs/{job_id}/walk/{head}/{name}")
def dev_walk_shot(job_id: int, head: str, name: str) -> FileResponse:
    """A screen the walkthrough captured at an `open` step of the delivered commit `head`. Served as an image only,
    as the screens are: the branch's own code served what it shows."""
    from justshipit.engineering.try_it import walk_file
    with session_scope() as s:
        known = s.get(DevJob, job_id) is not None
    if (path := walk_file(job_id, head, name) if known else None) is None:
        raise HTTPException(404, msg("console.no_screen", app_settings.language(), id=job_id))
    return FileResponse(path, media_type="image/png", headers={
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox", "Cache-Control": "private, max-age=300"})


@router.post("/dev-jobs/{job_id}/fix-again")
def dev_fix_again(job_id: int) -> dict:
    """Ask for the latest fix round of a turned-down build again, with the owner's same words."""
    from justshipit.engineering.revise import again
    with activity.as_actor("owner"):
        try:
            again(job_id, app_settings.language())
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    return {"dev_job_id": job_id}


@router.post("/dev-jobs/{job_id}/fix-or-drop-again")
def dev_fix_or_drop_again(job_id: int) -> dict:
    """Ask again whether to fix or drop a build that failed its check, after the owner put the card off or turned it
    down. Returns the new request's id."""
    from justshipit.engineering.revise import ask_again
    with activity.as_actor("owner"):
        try:
            return {"dev_job_id": job_id, "request_id": ask_again(job_id, app_settings.language())}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc


@router.post("/dev-jobs/{job_id}/use-again")
def dev_use_again(job_id: int) -> dict:
    """Ask again how people will use a merged build, after the owner put it off. Returns the new request's id."""
    from justshipit.engineering.usable import ask_again
    with activity.as_actor("owner"):
        try:
            return {"dev_job_id": job_id, "request_id": ask_again(job_id, app_settings.language())}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc


@router.post("/dev-jobs/{job_id}/discard")
def dev_discard(job_id: int) -> dict:
    """Throw the delivered result away and start a new attempt. Returns the new attempt's job id."""
    from justshipit.engineering.worker import discard
    with activity.as_actor("owner"):
        try:
            return {"dev_job_id": discard(job_id, app_settings.language())}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc


@router.post("/budget/month")
def month_budget(body: MonthIn) -> dict:
    return _act(control.set_month_limit, body.limit_usd)


@router.post("/jobs/{job_id}/retry")
def job_retry(job_id: int) -> dict:
    """Queue a fresh copy of a failed worker job; the failed one stays in the record."""
    from justshipit.engineering import wireflow
    lang = app_settings.language()
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(404, msg("console.no_job", lang, id=job_id))
        if job.status != "failed" and not (job.kind == wireflow.REDRAFT and job.status in wireflow.GONE):
            raise HTTPException(409, msg("console.not_failed", lang, id=job_id))
        if job.kind == "execute_action":  # one at a time with a re-request of the same request
            s.get(AgentRequest, job.payload["request_id"], with_for_update=True)
        if job.kind == wireflow.REDRAFT:  # one at a time with the owner dropping the notes (specs.drop_notes)
            matters.lock(s, job.payload["project_id"])
        if why := _why_not_retryable(s, job, lang):
            raise HTTPException(409, why)
        key = f"job_retry:{job.id}"
        again = queue.enqueue(s, job.kind, dict(job.payload), idempotency_key=key, max_attempts=job.max_attempts,
                              by_owner=True)
        running = queue.last_worker_seen(s)[1]
        if again is None:  # a second click: the first one's copy answers it
            return {"job_id": s.scalar(select(Job.id).where(Job.idempotency_key == key)), "worker_running": running}
        pid, bid = scope.owner_of(s, job.payload)
        activity.record(s, verb="retried", entity="job", entity_id=job.id, project_id=pid, business_id=bid,
                        actor="owner", summary=msg("console.event.retried", lang, job=job_label(job.kind, lang), id=job.id),
                        data={"kind": job.kind, "job_id": job.id})
        return {"job_id": again.id, "worker_running": running}


@router.post("/jobs/{job_id}/re-request")
def job_re_request(job_id: int) -> dict:
    """For a failed external action: put a fresh request for the same action before the owner and close the alert."""
    lang = app_settings.language()
    with session_scope() as s:
        job = s.get(Job, job_id, with_for_update=True)
        if job is None:
            raise HTTPException(404, msg("console.no_job", lang, id=job_id))
        if job.kind != "execute_action" or job.status != "failed":
            raise HTTPException(409, msg("console.not_failed_action", lang, id=job_id))
        try:
            new = requests.re_request(s, job.payload["request_id"])
        except requests.ResponseRejected as exc:
            raise HTTPException(409, exc.words(app_settings.language())) from exc
        if job.alert_dismissed_at is None:
            job.alert_dismissed_at = _now()
            ref = requests.display_id(new.id)
            activity.record(s, verb="re_requested", entity="request", entity_id=new.id, project_id=new.project_id,
                            actor="owner", summary=msg("console.event.re_requested", lang, id=job.id, ref=ref),
                            data={"job_id": job.id, "ref": ref})
        return {"request_id": new.id, "status": new.status}


@router.post("/jobs/{job_id}/dismiss")
def job_dismiss(job_id: int) -> dict:
    """Close a failed job's alert. The job stays in the queue record; only the home screen stops showing it."""
    lang = app_settings.language()
    with session_scope() as s:
        job = s.get(Job, job_id, with_for_update=True)
        if job is None:
            raise HTTPException(404, msg("console.no_job", lang, id=job_id))
        if job.status != "failed":
            raise HTTPException(409, msg("console.not_failed", lang, id=job_id))
        if job.alert_dismissed_at is None:
            job.alert_dismissed_at = _now()
            pid, bid = scope.owner_of(s, job.payload)
            activity.record(s, verb="dismissed", entity="job", entity_id=job.id, project_id=pid, business_id=bid,
                            actor="owner",
                            summary=msg("console.event.dismissed", lang, job=job_label(job.kind, lang), id=job.id),
                            data={"kind": job.kind, "job_id": job.id})
        return {"job_id": job.id, "alert_dismissed_at": job.alert_dismissed_at}


@router.post("/jobs/{job_id}/cancel")
def job_cancel(job_id: int) -> dict:
    """The owner's cancel of work that has not started: queued, or parked until a stage starts (`gated`). The row
    stays, as `cancelled`, so the record keeps it; work already running is not stopped here. Its idempotency key is
    let go so the same work can be asked for again. A send or a merge the owner approved is not cancelled here
    (NOT_RETRYABLE): neither can be asked for again without a new decision. The row lock keeps a claim and a cancel
    from both winning: the claim skips a locked row, and a cancel that waited on a claim finds it running. What the job
    would have moved (a project run, a build, a kickoff turn) ends where a job the worker gave up leaves it, so the
    owner can start it again (queue.fail_subject)."""
    lang = app_settings.language()
    with session_scope() as s:
        job = s.get(Job, job_id, with_for_update=True)
        if job is None:
            raise HTTPException(404, msg("console.no_job", lang, id=job_id))
        if job.status not in queue.UNSTARTED:
            raise HTTPException(409, msg("console.not_cancellable", lang, id=job_id))
        if job.kind in queue.NOT_RETRYABLE:
            raise HTTPException(409, msg("console.not_cancellable_kind", lang, id=job_id))
        was = queue.cancel_unstarted(s, job, lang)
        return {"job_id": job.id, "status": job.status, "was": was}


@router.get("/jobs")
def jobs(status: str | None = None, limit: Annotated[int, Query(le=200)] = 50,
         business_id: int | None = None) -> list[dict]:
    """The worker queue, for the run log: what is waiting, running or gave up. With `business_id`, that business's
    jobs and the company's own (no business), by the stamp enqueue gave each job (services/scope.py). A job without a
    stamp whose payload still names a business's record (one enqueued before `owner_of` knew that record) reads as that
    business's, so it is listed under it alone."""
    with session_scope() as s:
        q = select(Job).order_by(Job.id.desc())
        if status:
            q = q.where(Job.status == status)
        if business_id is not None:
            q = q.where(or_(Job.business_id == business_id, Job.business_id.is_(None)))
        out = []
        for j in s.scalars(q.execution_options(yield_per=limit)):
            bid = j.business_id if j.business_id is not None else scope.subject_business(s, j.payload or {})
            if business_id is not None and bid not in (None, business_id):
                continue
            out.append({**_row(j), "business_id": bid, "last_error": (j.last_error or "")[:600] or None,
                        "cancellable": j.status in queue.UNSTARTED and j.kind not in queue.NOT_RETRYABLE})
            if len(out) == limit:
                break
        return out
