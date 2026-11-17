"""LangGraph company orchestrator.

    plan ─► plan_gate ─► schedule ─┬─► work (fan-out, one per ready task) ─► schedule
                                   ├─► human_gate (a result to settle, or the run waits for the owner) ─► schedule
                                   └─► ceo_review ─┬─► meeting ─► schedule
                                                   ├─► schedule (follow-up tasks)
                                                   ├─► human_gate (its card holds the next plan step)
                                                   └─► decide ─► final_gate ─► report ─► END

Graph state is deliberately tiny; all business data is read from and written to PostgreSQL, so the
checkpoint only records where the run is. Every loop is bounded by company limits.

What agents read is in English, the working language of their prompts. What the owner reads (the documents a run
writes, the cards it raises, why a task stopped) is in the owner's language, which each node reads once when it starts.
"""
from __future__ import annotations

import functools
import logging
import operator
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict
from zoneinfo import ZoneInfo

from langchain_core.runnables.config import ContextThreadPoolExecutor
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt
from sqlalchemy import or_, select
from sqlalchemy.orm import object_session

from justshipit import staffing
from justshipit.agents.executor import AgentFailed, last_call_id, last_served, run_agent
from justshipit.agents.independence import Served, assess
from justshipit.analysis import runner as analysis
from justshipit.contracts.planning import (
    MAX_BUILD_INPUTS, REPORT_BODY_CHARS, ProjectManagerPlanOut, ProjectManagerReviewAction, ProjectManagerReviewOut, DecisionOut, FinalReportOut, InboxCleanupOut, PositionOut,
)
from justshipit.contracts.requests import (
    HANDOUT_CHARS, AgentRequestDraft, RequestStatus, RequestType, RiskLevel,
)
from justshipit.contracts.work import (
    TERMINAL_TASK_STATUSES, AgentResultOut, EvidenceKind, ReviewOut, ReviewVerdict, TaskStatus,
)
from justshipit.contracts.origins import DecisionOrigin, OriginKind, RoundOrigin, ServerOrigin, TaskOrigin
from justshipit.contracts.work_path import HoldPhase, ReviewKind
from justshipit.db.models import AgentRequest, Artifact, Decision, Evidence, Kpi, Meeting, Plan, Project, Task
from justshipit.contracts.owner_text import clip as owner_clip
from justshipit.db.session import session_scope
from justshipit.engineering import owner_trace, usable
from justshipit.i18n import Lang, catalog, msg
from justshipit.i18n.said import say
from justshipit.i18n.glossary import role
from justshipit.orchestration import context as ctx
from justshipit.services import (
    app_settings, budget, evidence, existing_product, finance, holds, memory, owner_tasks, people, people_tasks,
    plan_screen, queue, registry, request_contract, requests, review_rounds, rounds, stops, tasks, usage,
)
from justshipit.services.budget import BudgetExceeded
from justshipit.services.fence import fenced
from justshipit.services.usage import MEETING_MAX_PARTICIPANTS, MustWait
from justshipit.settings import agents_config, limits

log = logging.getLogger(__name__)


class CompanyState(TypedDict, total=False):
    project_id: int
    task_id: int                      # only in per-task Send payloads
    batch: list[int]
    done: Annotated[list[int], operator.add]
    next: str


# ----------------------------------------------------------------------------- helpers

def _project(s, state) -> Project:
    return s.get(Project, state["project_id"])


def _set_phase(project_id: int, phase: str, status: str | None = None) -> None:
    with session_scope() as s:
        p = s.get(Project, project_id)
        p.phase = phase
        if status:
            p.status = status


def _write_artifact(s, p: Project, kind: str, title: str, rel: str, content: str) -> Artifact:
    sha = registry.write_files({rel: content}, f"[P-{p.id}] {kind}: {title}")
    art = Artifact(project_id=p.id, kind=kind, title=title, path=rel, commit_sha=sha)
    s.add(art)
    s.flush()
    return art


_ANALYST_REVIEW_NOTE = (
    "The author is the data analyst: the server fetched the source files, recorded their sha256, and ran the "
    "author's script in an isolated container. A value whose server check is reproduced came out the same when that "
    "script ran again in a fresh container. What to review is the method: whether it used the right sheet, columns and "
    "filters, whether its definitions (unit, reference date, what is included) match the conclusion, and whether the "
    "source data answers the question. Judge from the script and results.json at the end of the text. A transcribed "
    "value came from inputs the author typed into the script, not from a file the server fetched: don't mark it "
    "supported until you have checked those inputs against their source yourself.")


def _option_labels(lang: Lang, *keys: str) -> list[str]:
    return [msg(f"work.option.{k}", lang) for k in keys]


# ----------------------------------------------------------------------------- nodes

def plan(state: CompanyState) -> dict:
    pid = state["project_id"]
    lang = app_settings.language()
    with session_scope() as s:
        p = s.get(Project, pid)
        if p.plan is not None:
            # Restarted run (e.g. checkpoint lost): rebuild from the business DB. In-flight tasks
            # restart; completed tasks, evidence and decisions are kept.
            for t in s.scalars(select(Task).where(Task.project_id == pid, Task.status.in_(
                    [TaskStatus.RUNNING.value, TaskStatus.REVIEW.value, TaskStatus.REWORK.value]))):
                t.status = TaskStatus.READY.value
            p.status = "RUNNING"
            return {}
        p.status, p.phase = "RUNNING", "planning"
        now = datetime.now(timezone.utc)
        prompt = "\n\n".join([
            ctx.project_brief(s, p),
            memory.recall_block(s, p.goal, exclude_project=p.id, k=10),
            "## The team (agents you can give tasks to)\n" + ctx.roster(),
            _plan_limits(p, existing=existing_product.of_project(s, p.id) is not None),
            _plan_dates(now),
            "Make the work plan for the goal above.",
        ])
    out = run_agent("project_manager", "plan", prompt, ProjectManagerPlanOut, project_id=pid)
    call_id = last_call_id()
    with session_scope() as s:
        p = s.get(Project, pid)
        p.plan = out.model_dump(mode="json", exclude={"tasks", "requests", "build"})
        p.plan_round = 1
        step = review_rounds.add(s, pid, ReviewKind.PLAN, plan_round=1, call_id=call_id)
        created, warnings = tasks.create_from_drafts(s, p, out.tasks, origin="ceo_plan", plan_round=1, lang=lang)
        if out.build is not None:
            needs = {tasks.slug(k) for k in out.build.needs_task_keys[:MAX_BUILD_INPUTS]}
            inputs = [t.id for t in created if t.key in needs]
            if needs and not inputs:
                # The keys named no task of the plan (a title, a dropped task): the product tasks are what a spec needs.
                inputs = [t.id for t in created if t.owner == "product"][:MAX_BUILD_INPUTS]
                warnings.append(f"build inputs {sorted(needs)} matched no task; using the product tasks")
            p.plan = {**p.plan, "build": {**out.build.model_dump(mode="json"), "request_id": None,
                                          "task_ids": inputs}}
        if warnings:
            p.context = {**(p.context or {}), "plan_warnings": warnings}
        requests.from_agent_drafts(s, out.requests, agent="project_manager", project_id=pid, origin=RoundOrigin(step.id))
        talks = (people_tasks.first_talks(s, p.business_id, out.stop_rule, lang=lang, asks=out.talk_asks, now=now)
                 if p.business_id else [])
        def head(key: str) -> str:
            return f"## {msg(f'work.doc.plan.{key}', lang)}\n"
        rule = next((t.card["stop_rule"] for t in talks if t.card.get("stop_rule")), None)
        first = [head("first_talks") + "\n".join(f"- {t.title}" for t in talks)
                 + (f"\n\n{msg('work.doc.plan.stop_rule', lang, rule=rule)}" if rule else "")] if talks else []
        md = [f"# {msg('work.doc.plan.heading', lang, name=p.name)}", head("goal") + out.goal_interpretation, *first,
              head("deliverables") + "\n".join(f"- {d}" for d in out.deliverables),
              head("success") + "\n".join(f"- {d}" for d in out.success_criteria),
              head("unknowns") + "\n".join(f"- {d}" for d in out.key_uncertainties),
              head("tasks") + "\n".join(
                  f"- {tasks.display_id(t.id)} `{t.key}` ({t.owner}) {t.title}"
                  + (f" ← {', '.join(tasks.display_id(d) for d in t.depends_on)}" if t.depends_on else "")
                  for t in created)]
        _write_artifact(s, p, "plan", msg("work.doc.plan.title", lang), f"projects/{p.slug}/plan.md", "\n\n".join(md))
    return {}


def _day(at: datetime) -> str:
    return f"{at:%A} {at.day} {at:%B %Y}"


def _plan_dates(now: datetime) -> str:
    """The days the plan counts from, in the owner's time zone: today, and the day the first talks are due
    (`people_tasks.first_talks_due`), which the stop rule is judged against."""
    zone = ZoneInfo(app_settings.effective().usage.timezone)
    due = people_tasks.first_talks_due(now).astimezone(zone)
    return (f"## Dates (the owner's time zone)\n- Today is {_day(now.astimezone(zone))}\n"
            f"- The talks with the people the plan names are due by {_day(due)}, the end of the owner's week. Judge "
            "`stop_rule` on that day, or count it from a step the plan names (\"within 7 days of their first "
            "digest\"); never on a calendar date you work out yourself")


def _plan_limits(p: Project, *, existing: bool = False) -> str:
    """The plan's limits as the CEO reads them: the server enforces each (`rounds`, `tasks.create_from_drafts`)."""
    share, dev = float(limits()["round_budget_share"]), budget.dev_need_usd()
    per_task = float(limits()["task_budget_usd"]) / 2
    return (f"## Limits\n- A round holds at most {rounds.max_tasks()} tasks, {limits()['max_concurrent_tasks']} "
            "running at once\n"
            f"- A round's analysis budget is about ${share * p.budget_usd:.0f} (about ${per_task:g} per task). A project "
            f"that builds software (build) sets the build's share (${dev:g}) aside from the start, so its analysis "
            f"budget drops to about ${share * max(0.0, p.budget_usd - dev):.0f}. A task over budget does not start\n"
            f"- At most {limits()['max_rework_iterations']} reworks per task and {limits()['max_plan_rounds']} plan "
            "rounds\n"
            + (_EXISTING_BUILD if existing else _FIRST_BUILD))


_FIRST_BUILD = ("- When the owner's first deliverable is working software, fill in build, and plan round 1 starting with "
                "the tasks its spec needs. Leave out of this round any research that would not change what gets built "
                "(market size, more competitor comparison and the like)")
_EXISTING_BUILD = ("- The product already exists ('The product as it is today'). Do not plan building it from scratch. "
                   "Fill in build only for changes the goal needs to its current code (what the report says is "
                   "unfinished, risky or missing for this goal), and plan round 1 starting with the tasks that "
                   "change's spec needs. Launching and marketing what already works needs no build")


def human_gate(state: CompanyState) -> dict:
    """Hold the run while `holds.park_reason` says it waits for the owner, once the cards past their deadline have had
    their rule applied (`requests.expire_due`): a card past due holds until then, so its default is recorded before the
    work it frees goes on. Once it goes on, each result that waited on the owner's cards and whose cards are all decided
    counts (DONE) or is rejected by those cards (`holds.result_counts`), and a rejected one stays REJECTED: terminal, so
    the tasks after it still start, but never read as done by them or by the CEO's review. A result a card still holds
    waits while the tasks beside it run."""
    pid = state["project_id"]
    with session_scope() as s:
        requests.expire_due(s, pid, datetime.now(timezone.utc))
        if reason := holds.park_reason(s, pid):
            pending = [requests.display_id(r.id) for r in holds.parking(s, pid)]
            s.get(Project, pid).status = "PAUSED" if reason == holds.ParkReason.PAUSED else "WAITING_HUMAN"
    if reason:
        interrupt({"project_id": pid, "waiting_for": pending, "paused": reason == holds.ParkReason.PAUSED})
    with session_scope() as s:
        p = s.get(Project, pid)
        p.status = "RUNNING"
        kept = holds.held(s, pid, HoldPhase.FINISH)
        for t in s.scalars(select(Task).where(Task.project_id == pid,
                                              Task.status == TaskStatus.APPROVAL_REQUIRED.value)):
            if t.id in kept:
                continue
            if not holds.result_counts(s, t.id):
                tasks.transition(t, TaskStatus.REJECTED)
                continue
            tasks.transition(t, TaskStatus.APPROVED)
            s.flush()  # DONE would overwrite the verdict before the activity log saw it
            tasks.transition(t, TaskStatus.DONE)
    return {}


def schedule(state: CompanyState) -> dict:
    """Start the tasks that can run now. A task an open card holds is not started, nor a result it holds settled, and
    the rest of the round goes on beside it. A task whose author the owner stopped is not started, and a result whose
    reviewer the owner stopped is not reviewed; the rest of the round goes on. When nothing else is left, the run waits
    on that agent (queue.AgentStopped) instead of closing a round that is not over, and on the owner while a card holds
    a task or the CEO's next plan step (`holds.park_reason`): the CEO never reviews a round a held task is part of."""
    pid = state["project_id"]
    lang = app_settings.language()
    waits_on = None
    with session_scope() as s:
        p = s.get(Project, pid)
        # An owner pause or a card that parks the run takes effect here: running tasks finish, no new ones start.
        if holds.park_reason(s, pid):
            release_held(s, p)   # the owner is asked now, so what the round raised goes up with it
            return {"batch": [], "next": "human_gate"}
        # The project row before its tasks, as a re-plan beside the run takes them (`realign`), so the two never move
        # the same unstarted task at once: a READY written over a re-plan's cancel ran the cancelled task.
        p = s.scalar(select(Project).where(Project.id == pid).with_for_update(key_share=True)
                     .execution_options(populate_existing=True))
        held = stops.read(s)
        active = plan_screen.rules(s, p)
        ready = [t for t in tasks.promote_ready(s, pid) if not _cancel_if_ruled_out(t, active, lang)]
        reviews = [t for t in s.scalars(select(Task).where(Task.project_id == pid,
                                                           Task.status == TaskStatus.REVIEW.value).order_by(Task.id))
                   if _awaiting_review(t) is not None]
        waiting = [t for t in ready + reviews if stops.waiting_on(t, held)]
        start, left = rounds.admit(s, p, [t for t in ready if t not in waiting])
        if left and not start:
            rounds.defer(s, p, left, lang=lang)
        batch = [t.id for t in [*(t for t in reviews if t not in waiting), *start]][: int(limits()["max_concurrent_tasks"])]
        kept = holds.held(s, pid, HoldPhase.FINISH)
        settles = any(t.status == TaskStatus.APPROVAL_REQUIRED.value and t.id not in kept
                      for t in tasks.unsettled(s, pid))
        if batch or not waiting or settles:
            # A task a card still holds is part of this round: it runs once the owner answers.
            _ask_for_build(s, p, round_over=not batch and not holds.live_holds(s, pid), lang=lang)
            if not batch:
                release_held(s, p)
        else:
            waits_on = stops.waiting_on(waiting[0], held)
        p.phase = "executing" if batch else p.phase
        # The tasks this step cancelled (a directive's rule, a spent envelope) may leave only held work behind.
        parks = not (batch or settles or waits_on) and holds.park_reason(s, pid) is not None
    if batch:
        return {"batch": batch, "next": "work"}
    if settles or parks:
        return {"batch": [], "next": "human_gate"}
    if waits_on is not None:
        raise queue.AgentStopped(waits_on, stops.waiting_text(waits_on, lang))
    return {"batch": [], "next": "ceo_review"}


def _ask_for_build(s, p: Project, *, round_over: bool, lang: Lang, final: bool = False) -> None:
    """Ask for the development of the software the owner's first deliverable is, once the tasks its spec needs are
    done, or when the round ends whatever they are. 원두재고 and 견적봇 first asked 144 and 115 minutes in, after $27 and
    $29 of their $40 (scripts/analysis_replay.py). A failed input holds the ask while a follow-up round can still redo
    it, and a task redoing it takes its place (`tasks.create_from_drafts`): 반품 예측's spec was written without its
    failed rule spec (T-87), and three tasks then tried to fill the spec in. The run's last review (`final`) asks
    whatever they are. A new build starts a product plan the owner approves before its spec (`plans.planned`); the
    build records the plan, or the card its ask joined, so it is asked once."""
    if not ((p.plan or {}).get("build") or {}).get("title") or _asked(p.plan["build"]):
        return
    # A realign rewrites the plan under this row's lock; this write must not undo it. The same lock `schedule` took:
    # an upgrade to FOR UPDATE would wait on an approval's Grant insert, whose key check holds this row.
    p = s.scalar(select(Project).where(Project.id == p.id).with_for_update(key_share=True)
                 .execution_options(populate_existing=True))
    build = p.plan["build"]
    if _asked(build):
        return
    terminal = {st.value for st in TERMINAL_TASK_STATUSES}
    inputs = [t for t in (s.get(Task, i) for i in build.get("task_ids") or []) if t is not None]
    if (not final and any(t.status == TaskStatus.FAILED.value for t in inputs)
            and p.plan_round < int(limits()["max_plan_rounds"])):
        return
    if not round_over and any(t.status not in terminal for t in inputs):
        return
    done = [t.id for t in inputs if t.status == TaskStatus.DONE.value]
    refs = ", ".join(tasks.display_id(i) for i in done)
    needs = [{"title": build["title"], "problem": build["problem"], "why_software": build["why_software"],
              "expected_value": "", "priority": "P0"}]
    draft = AgentRequestDraft(
        type=RequestType.DEVELOPMENT, title=msg("work.ask.build.title", lang, title=build["title"]),
        summary=msg("work.ask.build.summary", lang, problem=build["problem"]),
        rationale=build["why_software"] + (f"\n{msg('work.ask.build.inputs', lang, refs=refs)}" if refs else ""),
        # No cost: one would make the owner approve the spec writing too, and the owner approves the build once, on the
        # spec card. Its share is already kept (`budget.dev_hold`).
        options=_option_labels(lang, "write_spec", "hold", "decline"), recommended_option=None,
        action_kind=requests.DEV_ASK, estimated_cost_usd=None, risk_level=RiskLevel.MEDIUM)
    asked = requests.create(s, draft, from_agent="product", project_id=p.id,
                            origin=ServerOrigin(OriginKind.BUILD_ASK, review_rounds.plan_step(s, p.id)),
                            action_params={"need": {
        "title": build["title"], "problem": build["problem"], "why_software": build["why_software"],
        "task_ids": done, "needs": needs}})
    p.plan = {**p.plan, "build": {**build, **({"plan_id": asked.id} if isinstance(asked, Plan)
                                              else {"request_id": asked.id})}}


def _asked(build: dict) -> bool:
    """Whether the run already asked for its build: a plan started for it, or a card its ask joined."""
    return bool(build.get("request_id") or build.get("plan_id"))


HELD = "held_requests"   # a task result whose cards wait for the round to settle


def _raise_requests(s, p: Project, t: Task) -> None:
    """Put the task's drafted requests to the owner, in place of the cards its earlier passes raised."""
    drafts, cards = requests.stored_drafts((t.result or {}).get("requests") or [])
    req_ids = requests.from_agent_drafts(s, drafts, agent=t.owner, project_id=p.id, origin=TaskOrigin(t.id),
                                         cards=cards)
    requests.supersede_iteration(s, t.id, t.result.get("earlier_request_ids", []), req_ids)
    t.result = {**{k: v for k, v in t.result.items() if k != HELD}, "request_ids": req_ids}


def release_held(s, p: Project) -> None:
    """The round has settled, or the owner just answered (`realign`): the cards the round's tasks drafted go to the
    owner together, as one batch. 동네산책's tasks raised 18 cards over two hours in nine drops, each the moment a task
    finished. The rows are locked, so a run and a realign releasing at once raise each card once."""
    for t in s.scalars(select(Task).where(Task.project_id == p.id, Task.result.has_key(HELD)).order_by(Task.id)
                       .with_for_update(skip_locked=True).execution_options(populate_existing=True)):
        _raise_requests(s, p, t)


def _cancel_if_ruled_out(t: Task, active: list[plan_screen.Rule], lang: Lang) -> bool:
    """Cancel a task an owner directive rules out (T-43 re-verified a table after '같은 표를 다시 검증하는 업무는 더
    만들지 마세요'). True when it was cancelled."""
    if (rule := plan_screen.broken(active, t.title)) is None:
        return False
    tasks.transition(t, TaskStatus.CANCELLED,
                     error=msg("work.task.cancelled_by_rule", lang, ref=rule.ref, label=rule.label))
    return True


def _ruled_out(pid: int, tid: int, lang: Lang) -> bool:
    """Whether a directive given while this task ran rules it out; the task is then cancelled at this step, before
    another authoring pass or a review is paid for."""
    with session_scope() as s:
        t = s.scalar(select(Task).where(Task.id == tid).with_for_update())
        if t.status not in (TaskStatus.READY.value, TaskStatus.REVIEW.value, TaskStatus.REWORK.value):
            return t.status == TaskStatus.CANCELLED.value
        return _cancel_if_ruled_out(t, plan_screen.rules(s, s.get(Project, pid)), lang)


def route_schedule(state: CompanyState):
    if state.get("next") == "work":
        return [Send("work", {"project_id": state["project_id"], "task_id": tid, "batch": state["batch"]})
                for tid in state["batch"]]
    return state.get("next", "ceo_review")


@dataclass(frozen=True)
class _Authored:
    """What an authoring pass committed, read back from the task, so a review resumes without authoring again."""
    owner: str
    iteration: int
    ev_ids: list[int]
    served: Served | None


def _awaiting_review(t: Task) -> _Authored | None:
    r = t.result or {}
    if t.status != TaskStatus.REVIEW.value or "request_ids" not in r:
        return None
    served = Served(*r["author_served"]) if r.get("author_served") else None
    return _Authored(t.owner, t.iteration, r["evidence_ids"], served)


def _author(pid: int, tid: int, feedback: str | None, lang: Lang) -> bool:
    """One authoring pass. It commits the result and its evidence together with the move to REVIEW, so a task found
    in REVIEW has both. The requests it drafted stay in the result until the review settles. False when the pass
    failed the task, or a re-plan beside the run cancelled it before it started: it starts under its row lock, the
    one the re-plan cancels it under."""
    with session_scope() as s:
        t = s.scalar(select(Task).where(Task.id == tid).with_for_update(key_share=True)
                     .execution_options(populate_existing=True))
        if t.status == TaskStatus.CANCELLED.value:
            return False
        p = s.get(Project, pid)
        tasks.transition(t, TaskStatus.RUNNING)
        t.iteration += 1
        iteration, owner, slug = t.iteration, t.owner, p.slug
        crew = staffing.task_crew(s, tid)
        model = staffing.author(crew).model if crew is not None else None   # None runs the configured model
        parts = [ctx.project_brief(s, p, reader=owner), ctx.task_header(t), ctx.owner_comments(s, t), ctx.fed_answers(s, t),
                 ctx.asked_before(s, t), ctx.dependency_outputs(s, t),
                 memory.recall_block(s, f"{t.title}\n{t.objective}", exclude_project=pid, k=6),
                 "## Evidence list so far (use it instead of researching again; to cite an item, record it again as "
                 "a new evidence item)\n" + evidence.digest(s, pid, limit=60)]
        if feedback:
            parts.append(f"## What to fix in your last result (make every one)\n{feedback}\n\n"
                         f"### Summary of your last result\n{(t.result or {}).get('summary', '')}")
        parts.append("Write a result that meets every acceptance criterion.")
        prompt = "\n\n".join(x for x in parts if x)
    analysis_rec = None
    try:
        if agents_config()[owner].get("runtime") == "analysis":
            out, analysis_rec = analysis.run_task(owner, prompt, project_id=pid, task_id=tid,
                                                  project_slug=slug, attempt=iteration, lang=lang)
        else:
            out = run_agent(owner, "work", prompt, AgentResultOut, project_id=pid, task_id=tid,
                            attempt=iteration, **({"model_name": model} if model else {}))
        author_served, call_id = last_served(), last_call_id()
    except (AgentFailed, BudgetExceeded) as exc:
        with session_scope() as s:
            tasks.transition(s.get(Task, tid), TaskStatus.FAILED, error=str(exc)[:2000])
        return False
    except (MustWait, queue.AgentStopped):
        # Nothing was produced. The pass starts over when the run resumes, or the owner resumes the author, and does
        # not count as an iteration.
        with session_scope() as s:
            t = s.get(Task, tid)
            t.status, t.iteration = TaskStatus.READY.value, t.iteration - 1
        raise

    with session_scope() as s:
        t = s.get(Task, tid)
        ev_ids = evidence.record(s, pid, tid, owner, out.evidence)
        result = out.model_dump(mode="json")
        result["evidence_ids"] = ev_ids
        if analysis_rec is not None:
            p = s.get(Project, pid)
            base_rel = f"projects/{p.slug}/analysis/T-{tid}-i{iteration}"
            for eid, declared in zip(ev_ids, out.evidence, strict=True):
                e = s.get(Evidence, eid)
                if status := analysis.classify(analysis_rec, e.source_url, e.quote, lang=lang):
                    e.verification_status, e.verification_note = status
                    # A value the server computed from an owner-provided file is sourced, though not by URL.
                    if status[0] in ("reproduced", "computed") and declared.kind == EvidenceKind.SOURCED_CLAIM:
                        e.kind = EvidenceKind.SOURCED_CLAIM.value
            sha = registry.write_files(analysis.artifact_files(analysis_rec, base_rel),
                                       f"[analysis] P-{pid} T-{tid} attempt {iteration}")
            result["findings_markdown"] += analysis.appendix(analysis_rec, base_rel, lang=lang)
            result["analysis"] = {"path": base_rel, "commit": sha, "reproduced": analysis_rec.reproduced,
                                  "steps": analysis_rec.steps, "inputs": analysis_rec.manifest}
        if out.financial_model and out.financial_model.scenarios:
            _, table, claims = finance.run_model(out.financial_model, lang=lang)
            calc_ids = evidence.record_calculation(s, pid, tid, claims)
            result["finance_table"] = table
            result["findings_markdown"] += (
                f"\n\n## {msg('work.doc.finance', lang)}\n{table}\n\n"
                + msg("work.doc.finance_basis", lang, refs=", ".join(evidence.ev_id(i) for i in calc_ids)))
        result |= {k: t.result[k] for k in (LAST_UNMET, SENT_BACK) if t.result and k in t.result}
        # The output's requests wait for the review: a card raised from work the reviewer then finds unmet would reach
        # the owner anyway (T-26, T-39). They are raised when the task finishes (`_finish`), where they replace the
        # cards an earlier pass of this task raised.
        prev = t.result or {}
        result["earlier_request_ids"] = prev.get("request_ids") or prev.get("earlier_request_ids") or []
        result["request_ids"] = []
        result["author_served"] = [author_served.provider, author_served.model] if author_served else None
        t.result = result
        tasks.transition(t, TaskStatus.REVIEW)
        if call_id is not None:
            staffing.record_turn(s, staffing.task_crew(s, tid), staffing.AuthorTurn(
                role=owner, call_id=call_id, iteration=iteration, fixing=feedback or "", summary=result["summary"],
                findings_markdown=result["findings_markdown"], assumptions=result["assumptions"],
                risks=result["risks"], open_questions=result["open_questions"],
                unmet_criteria=result["unmet_criteria"], evidence_ids=ev_ids))
    return True


def _send_back(t: Task) -> str | None:
    """What the server found in the requests the author drafted that the author must fix before any reaches the owner
    (`owner_tasks.author_problems`), as the next pass's feedback. The task goes back to REWORK without a review, which
    could not pass it anyway. It does so only after the first pass, since each pass spends one of the reviewer's
    reworks: a later pass answers a review's rework and goes to that review, whose unmet list it was written from. A
    pass past the rework limit, or one that would end past the task's hard cap, goes on to the review as it is."""
    if ((t.result or {}).get(SENT_BACK) or t.iteration > 1 or t.iteration > t.max_iterations
            or not _attempt_fits(t)):
        return None
    drafts, _ = requests.stored_drafts((t.result or {}).get("requests") or [])
    s = object_session(t)
    business_id = s.scalar(select(Project.business_id).where(Project.id == t.project_id))
    named = (lambda text: [p.display_name for p in people.named(s, business_id, text)]) if business_id else None
    problems = owner_tasks.author_problems(drafts, named=named) + [
        f"Request '{d.title}' is not filed: {' '.join(say(r.said(), 'en') for r in refused)} "
        f"({', '.join(r.key.value for r in refused)})" for d, refused in zip(drafts, _refusals(s, t, drafts))
        if refused]
    if not problems:
        return None
    feedback = "The server sends these back before review:\n" + "\n".join(f"- {x}" for x in problems)
    tasks.transition(t, TaskStatus.REWORK)
    t.result = {**t.result, "rework_feedback": feedback, SENT_BACK: True}
    return feedback


def _refusals(s, t: Task, drafts: list[AgentRequestDraft]) -> list[list[request_contract.Refused]]:
    """Why the request contract refuses each of the task's drafts, by position: nothing for one it files."""
    origin = TaskOrigin(t.id)
    return [requests.check(s, d, origin, t.project_id) if d.owner_action else [] for d in drafts]


def _drop_refused(s, t: Task) -> None:
    """A draft the contract still refuses once the task's review settles is filed as nothing: the task result and the
    activity log say why (`request_not_filed`), and the CEO reads it with the project."""
    raw = (t.result or {}).get("requests") or []
    drafts, _ = requests.stored_drafts(raw)
    refusals = _refusals(s, t, drafts)
    if not any(refusals):
        return
    origin = TaskOrigin(t.id)
    dropped = [requests.not_filed(s, d, refused, agent=t.owner, project_id=t.project_id, origin=origin)
               for d, refused in zip(drafts, refusals) if refused]
    t.result = {**t.result, "requests": [x for x, refused in zip(raw, refusals) if not refused],
                "request_not_filed": [*t.result.get("request_not_filed", []), *dropped]}


def work(state: CompanyState) -> dict:
    """Execute one task with bounded review/rework iterations. A wait for usage (MustWait) ends the node with
    the task where it stood: a task in REVIEW is reviewed again on re-entry, never authored again, and a pass
    that produced nothing is repeated. So does a stop of its author or its reviewer (queue.AgentStopped), without
    ending the run: the task waits for the agent at that step and `schedule` starts it again once the owner resumes it."""
    pid, tid = state["project_id"], state["task_id"]
    lang = app_settings.language()
    with session_scope() as s:
        t = s.get(Task, tid)
        if t.status == TaskStatus.CANCELLED.value:   # realigned away between scheduling and start
            return {"done": [tid]}
        # No row lock is held here: the crew's unique constraint keeps a replay or a second caller to one crew.
        staffing.ensure(s, tid, staffing.task_facts(s, t, alongside=state.get("batch") or ()), staffing.load_limits())
        authored = _awaiting_review(t)
        if authored is None and t.status in (TaskStatus.RUNNING.value, TaskStatus.REVIEW.value):
            t.status = TaskStatus.READY.value   # recovery after a crash mid-task (checkpoint replay)
        feedback = None if authored else (t.result or {}).get("rework_feedback")
    while True:
        if _ruled_out(pid, tid, lang):
            return {"done": [tid]}
        if authored is None:
            try:
                if not _author(pid, tid, feedback, lang) or _ruled_out(pid, tid, lang):
                    return {"done": [tid]}
            except queue.AgentStopped:
                return {}
            with session_scope() as s:
                authored = _awaiting_review(s.get(Task, tid))
                if authored is not None and (fixes := _send_back(s.get(Task, tid))):
                    feedback, authored = fixes, None
                    continue
        a, authored = authored, None
        owner, iteration, ev_ids = a.owner, a.iteration, a.ev_ids
        queue.check_lease()   # the source check fetches pages; a stop ends the job here, the result waits in REVIEW
        evidence.verify_sources(ev_ids)

        with session_scope() as s:
            t = s.get(Task, tid)
            crew = staffing.settle_solo(s, staffing.task_crew(s, tid), t)
            if (slot := staffing.verifier(crew)) is None:
                staffing.enqueue_audit(s, crew, t)
                _finish_unchecked(s, s.get(Project, pid), t, lang)
                return {"done": [tid]}
            reviewer, prompt = slot.role, review_prompt(s, t, slot.role)
        try:
            review = run_agent(reviewer, "review", prompt, ReviewOut, project_id=pid, task_id=tid,
                               attempt=iteration)
        except queue.AgentStopped:
            return {}   # the result waits in REVIEW for its reviewer
        except (AgentFailed, BudgetExceeded) as exc:
            # Never let a missing review look like a passed one: CEO and report see "unreviewed".
            with session_scope() as s:
                t = s.get(Task, tid)
                t.error = f"review failed: {exc}"[:2000]
                t.review = {"verdict": UNREVIEWED, "reviewer": reviewer, "iteration": iteration,
                            "criteria_met": [], "criteria_unmet": [msg("work.task.not_reviewed", lang)],
                            "claim_checks": [], "counter_arguments": [], "error": str(exc)[:500]}
                _finish(s, s.get(Project, pid), t, UNREVIEWED, lang)
            return {"done": [tid]}

        reviewer_served, call_id = last_served(), last_call_id()
        independence = assess(owner, a.served, reviewer, reviewer_served)
        with session_scope() as s:
            t = s.get(Task, tid)
            p = s.get(Project, pid)
            if call_id is not None:
                staffing.record_turn(s, staffing.task_crew(s, tid), staffing.VerifierTurn(
                    role=reviewer, call_id=call_id, iteration=iteration, review=review,
                    independence=independence.label))
            # A review by the author's own role must not promote evidence to verified_fact.
            evidence.apply_review(s, ev_ids, [c for c in review.claim_checks if independence.independent or
                                              c.status != "supported"], reviewer)
            t.review = {**review.model_dump(mode="json"), "reviewer": reviewer, "iteration": iteration,
                        "served_by": str(reviewer_served), "independence": independence.label}
            t.contested = t.contested or review.contested
            last_unmet = t.result.get(LAST_UNMET) or []
            t.result = {**t.result, LAST_UNMET: review.criteria_unmet}
            if (review.verdict == ReviewVerdict.REWORK and review.criteria_unmet and iteration <= t.max_iterations
                    and _fixed_any(review, last_unmet) and _attempt_fits(t)):
                tasks.transition(t, TaskStatus.REWORK)
                feedback = "\n".join(filter(None, [
                    review.rework_instructions,
                    "Unmet criteria: " + "; ".join(review.criteria_unmet) if review.criteria_unmet else "",
                    "Weak or contradicted evidence: " + "; ".join(
                        f"[E{c.evidence_index + 1}] {c.status}: {c.note}" for c in review.claim_checks
                        if c.status in ("weak", "contradicted")),
                ]))
                t.result = {**t.result, "rework_feedback": feedback}
                continue
            outcome = review_outcome(review, t.result.get("unmet_criteria") or [])
            t.review = {**t.review, "reviewer_verdict": review.verdict.value, "verdict": outcome}
            if review.verdict == ReviewVerdict.ESCALATE:
                t.contested = True
            _finish(s, p, t, outcome, lang)
        return {"done": [tid]}


PASSED, PARTIAL, UNMET, ESCALATED, UNREVIEWED = "pass", "partial", "unmet", "escalate", "unreviewed"
UNCHECKED = staffing.UNCHECKED
LAST_UNMET = "_last_unmet"   # the blocking items of the task's latest review, which the next review judges first
SENT_BACK = "_sent_back"     # the server sent this task's requests back to their author once (`_send_back`)


def review_prompt(s, t: Task, reviewer: str) -> str:
    r = t.result
    rows = [s.get(Evidence, i) for i in r["evidence_ids"]]
    ev_list = "\n".join(
        f"[E{n + 1}] (evidence_index {n}; {e.kind}, server source check: {e.verification_status}"
        f"{' - ' + e.verification_note if e.verification_note else ''}) {e.claim}"
        f"{' <' + e.source_url + '>' if e.source_url else ''}"
        f"{chr(10) + '    quote: ' + ctx.clip(e.quote, 300) if e.quote else ''}"
        for n, e in enumerate(rows))
    return "\n\n".join(filter(None, [
        ctx.project_brief(s, s.get(Project, t.project_id), reader=reviewer), ctx.task_header(t),
        f"## The result to review (by {t.owner}, attempt {t.iteration})\n**Summary**: {r.get('summary', '')}\n\n"
        + ctx.clip(r["findings_markdown"], 14000)
        + ("\n\n**Criteria the author says are unmet**: " + "; ".join(r["unmet_criteria"])
           if r.get("unmet_criteria") else ""),
        f"## Evidence, numbered as the result cites it\n{ev_list or '(none)'}",
        _previous_review(r.get(LAST_UNMET) or []),
        "As the independent reviewer, check strictly whether the acceptance criteria are met and judge each "
        "evidence item. An item whose server source check is url_unreachable may cite a source that does not "
        "exist. criteria_unmet holds only what keeps the result from the task's goal and a rework can fix; "
        "what else is unmet goes in notes. The owner reads criteria_unmet, notes and counter_arguments as "
        "written: say what the work is missing in plain words, cite evidence as [E3] the way the result "
        "does, and leave out how the evidence list is numbered or formatted, which the server handles. "
        + (_ANALYST_REVIEW_NOTE if "analysis" in r else
           "The author's only tools are web search and opening pages (no file downloads, aggregation or "
           "fieldwork). A criterion unmet for that reason goes in notes, marked '(tool limit)', and when "
           "source data needs aggregating, say an analyst follow-up task is needed."),
    ]))


def _previous_review(last_unmet: list[str]) -> str:
    """The reviewer's own blocking list from the attempt before, to judge first. Without it the attempt-2 reviewer
    reviewed from scratch, found new points, and 12 of 14 failed tasks stopped there (persona round 3)."""
    if not last_unmet:
        return ""
    return ("## Your previous review of this task\nWhat it found blocking, which the author was asked to fix:\n"
            + "\n".join(f"[U{n}] {x}" for n, x in enumerate(last_unmet, 1))
            + "\n\nJudge each of these first, in previous_unmet. criteria_unmet then lists the ones still not fixed, "
            "and a new item only when it keeps the result from the task's goal; any other new point goes in notes.")


def _fixed_any(review: ReviewOut, last_unmet: list[str]) -> bool:
    """Whether a rework fixed an item the previous review asked for, by the reviewer's judgment of each item. A rework
    that fixed none stops; one that fixed some goes on, whatever new points the review found."""
    return not last_unmet or any(c.fixed and 1 <= c.number <= len(last_unmet) for c in review.previous_unmet)


def _attempt_fits(t: Task) -> bool:
    """Whether another attempt, costing what this task's attempts cost on average, ends inside the task's hard cap
    with room left to hold its review's call. T-94's third attempt started at $3.08 of its $4.50 and was cut off
    without a result."""
    next_attempt = t.spent_usd / max(t.iteration, 1)
    return t.spent_usd + next_attempt + usage.purpose("review").reserve_usd <= budget.task_cap(t)


def review_outcome(review: ReviewOut, author_unmet: list[str]) -> str:
    """What a settled review means for the task. A rework that stopped (attempts, budget, or nothing fixed) keeps
    its result as partial when it meets more criteria than it misses, and is unmet otherwise. Anything still listed
    as unmet, blocking or not, the reviewer's or the author's ("T-12는 미완료입니다"), makes a pass partial, so it
    never reads as 검토 통과. A rework that lists neither an unmet criterion nor a note names nothing to weigh, so it
    is unmet: the reviewer said the work is not done."""
    if review.verdict == ReviewVerdict.ESCALATE:
        return ESCALATED
    if review.verdict == ReviewVerdict.REWORK and review.criteria_unmet:
        return PARTIAL if len(review.criteria_met) > len(review.criteria_unmet) else UNMET
    if review.verdict == ReviewVerdict.REWORK and not review.notes:
        return UNMET
    return PARTIAL if review.criteria_unmet or review.notes or author_unmet else PASSED


def _finish(s, p: Project, t: Task, outcome: str, lang: Lang) -> None:
    """End a reviewed task. Unmet work fails with what is missing and raises none of its requests, so no card reaches
    the owner from output the reviewer rejected; work kept as partial after a stopped rework raises none either. A
    task that asks something that holds work raises its requests now, since that work waits until the owner answers; a
    card among them that holds this task's finish keeps it in APPROVAL_REQUIRED, where its result waits for that card.
    Any other task's requests wait for the round to settle (`release_held`)."""
    if outcome == UNMET:
        unmet = "; ".join((t.review or {}).get("criteria_unmet") or []) or msg("work.task.review_again", lang)
        said = "work.task.alone_unmet" if (t.review or {}).get("verdict") == UNCHECKED else "work.task.review_failed"
        tasks.transition(t, TaskStatus.FAILED, error=msg(said, lang, unmet=unmet)[:2000])
        _write_task_artifact(s, p, t, lang)
        return
    if (t.review or {}).get("reviewer_verdict") == ReviewVerdict.REWORK.value and t.review.get("criteria_unmet"):
        tasks.transition(t, TaskStatus.DONE)
        _write_task_artifact(s, p, t, lang)
        return
    _drop_refused(s, t)
    drafts, _ = requests.stored_drafts((t.result or {}).get("requests") or [])
    if drafts and not any(d.holds_work and d.owner_action for d in drafts):
        t.result = {**t.result, HELD: True}
        tasks.transition(t, TaskStatus.DONE)
        _write_task_artifact(s, p, t, lang)
        return
    _raise_requests(s, p, t)
    waits = t.id in holds.held(s, p.id, HoldPhase.FINISH)
    tasks.transition(t, TaskStatus.APPROVAL_REQUIRED if waits else TaskStatus.DONE)
    _write_task_artifact(s, p, t, lang)


def _finish_unchecked(s, p: Project, t: Task, lang: Lang) -> None:
    """End a task that ran alone. No review was due, so the author's own unmet list says whether it met its criteria,
    and its evidence is never promoted (no `evidence.apply_review`). One that met none fails, as a checked one does
    (`review_outcome`); one that met some is partial. It drafted no request, or it would have a checker
    (`staffing.settle_solo`)."""
    unmet = (t.result or {}).get("unmet_criteria") or []
    outcome = PASSED if not unmet else UNMET if len(unmet) >= len(t.acceptance_criteria or []) else PARTIAL
    t.review = {"verdict": UNCHECKED, "reviewer": None, "outcome": outcome, "criteria_unmet": unmet}
    _finish(s, p, t, outcome, lang)


def _said_or(key: str, lang: Lang) -> str:
    """A status or verdict word from the catalog; one it does not name reads as nothing rather than as its code."""
    return msg(key, lang) if key in catalog() else ""


def _write_task_artifact(s, p: Project, t: Task, lang: Lang) -> None:
    r, rev = t.result or {}, t.review or {}
    ev_rows = [s.get(Evidence, i) for i in r.get("evidence_ids", [])]

    def head(key: str) -> str:
        return f"\n## {msg(f'work.doc.task.{key}', lang)}"
    lines = [f"# {t.title}",
             "- " + msg("work.doc.task.meta", lang, owner=role(t.owner, lang),
                        status=_said_or(f"work.doc.task.status.{t.status}", lang))]
    if t.iteration > 1:
        lines.append("- " + msg("work.doc.task.reworked", lang, n=t.iteration - 1))
    if rev.get("verdict") == UNCHECKED:
        lines.append("- " + msg("work.doc.task.verdict.unchecked", lang))
    elif rev:
        lines.append("- " + msg("work.doc.task.review", lang, reviewer=role(rev.get("reviewer"), lang),
                                verdict=_said_or(f"work.doc.task.verdict.{rev.get('verdict')}", lang)))
    if t.contested:
        lines.append("- " + msg("work.doc.task.contested", lang))
    lines += [f"{head('summary')}\n{r.get('summary', '')}", f"\n{r.get('findings_markdown', '')}", head("evidence")] + [
        f"- [E{n + 1}] ({evidence.grade_label(evidence.grade(e), lang)}) {e.claim}"
        + (f" — <{e.source_url}>" if e.source_url else "") for n, e in enumerate(ev_rows)]
    for key in ("assumptions", "risks", "open_questions"):
        if r.get(key):
            lines.append(f"{head(key)}\n" + "\n".join(f"- {x}" for x in r[key]))
    if rev.get("verdict") == UNCHECKED:
        if rev.get("criteria_unmet"):
            lines.append(f"{head('author_unmet')}\n" + "\n".join(
                f"- {msg('work.doc.task.unmet', lang, x=x)}" for x in rev["criteria_unmet"]))
    elif rev:
        lines.append(f"{head('independent_review')}\n" + "\n".join(
            [f"- {msg('work.doc.task.unmet', lang, x=x)}" for x in rev.get("criteria_unmet", [])] +
            [f"- {msg('work.doc.task.note', lang, x=x)}" for x in rev.get("notes", [])] +
            [f"- {msg('work.doc.task.counter', lang, x=x)}" for x in rev.get("counter_arguments", [])]))
    art = _write_artifact(s, p, "task_output", t.title,
                          f"projects/{p.slug}/work/{tasks.display_id(t.id)}-{t.key}.md",
                          tasks.document("\n".join(lines), r.get("evidence_ids", [])))
    t.artifact_id = art.id


def ceo_review(state: CompanyState) -> dict:
    pid = state["project_id"]
    lang = app_settings.language()
    with session_scope() as s:
        p = s.get(Project, pid)
        if _standing_decision(s, p):
            return {"next": "decide"}
        p.phase = "ceo_review"
        contested_tasks = list(s.scalars(select(Task).where(Task.project_id == pid, Task.contested.is_(True))))
        contested = [tasks.display_id(t.id) for t in contested_tasks]
        can_follow_up = p.plan_round < int(limits()["max_plan_rounds"])
        can_meet = p.meetings_held < int(limits()["max_meetings_per_run"])
        env = rounds.envelope(s, p)
        # Disagreement left unresolved must be argued out before closing (P-1 closed with 4 contested
        # tasks and no meeting); the CEO may still choose follow-up work first.
        auto_meeting = (_contested_meeting(contested_tasks, lang) if contested_tasks and p.meetings_held == 0
                        else None)
        prompt = "\n\n".join(filter(None, [
            ctx.project_brief(s, p), "## Task results\n" + ctx.results_overview(s, p), ctx.evidence_ledger(s, p, 100),
            ctx.build_state(s, p), "## The team\n" + ctx.roster(),
            f"## Where things stand\n- Contested tasks: {', '.join(contested) or 'none'}\n"
            f"- A meeting can be called: {can_meet} ({p.meetings_held} held)\n"
            f"- A follow-up round can be planned: {can_follow_up} (current round {p.plan_round})\n"
            f"- The next round holds at most {rounds.max_tasks()} tasks, with an analysis budget of about "
            f"${env.limit_usd:g} (this round spent ${env.spent_usd:.2f})\n"
            f"- How well the evidence holds: {evidence.mix(s, pid).line("en")}\n"
            "- Don't choose an action that isn't possible.",
            "Assess the results against the success criteria and decide what to do next.",
        ]))
    out = run_agent("project_manager", "plan", prompt, ProjectManagerReviewOut, project_id=pid)
    call_id = last_call_id()
    action = out.action
    if action == ProjectManagerReviewAction.MEETING and not can_meet:
        action = ProjectManagerReviewAction.COMPLETE
    if action == ProjectManagerReviewAction.FOLLOW_UP and (not can_follow_up or not out.follow_up_tasks):
        action = ProjectManagerReviewAction.COMPLETE
    if action == ProjectManagerReviewAction.COMPLETE and auto_meeting and can_meet:
        action = ProjectManagerReviewAction.MEETING
        out.meeting_agenda, out.meeting_participants = auto_meeting
    with session_scope() as s:
        p = s.get(Project, pid)
        step = review_rounds.add(s, pid, ReviewKind.REVIEW, plan_round=p.plan_round, assessment=out.assessment,
                                 action=action.value, unmet=out.unmet_success_criteria, call_id=call_id)
        # The follow-up tasks come first, so the review's cards can hold the tasks it just planned.
        if action == ProjectManagerReviewAction.FOLLOW_UP:
            p.plan_round += 1
            tasks.create_from_drafts(s, p, out.follow_up_tasks, origin="follow_up", plan_round=p.plan_round,
                                     lang=lang)
        requests.from_agent_drafts(s, out.requests, agent="project_manager", project_id=pid, origin=RoundOrigin(step.id))
        if action == ProjectManagerReviewAction.FOLLOW_UP:
            return {"next": "schedule"}
        if tasks.unsettled(s, pid):
            # The owner added or retried work while the CEO was reviewing: run it before closing.
            return {"next": "schedule"}
        if holds.park_reason(s, pid):
            # A card this review raised holds the next plan step or the run: the CEO reviews again once it is answered.
            return {"next": "human_gate"}
        if action == ProjectManagerReviewAction.MEETING:
            participants = [a for a in out.meeting_participants
                            if a in agents_config() and a != "project_manager"][:MEETING_MAX_PARTICIPANTS]
            p.context = {**p.context, "pending_meeting": {
                "agenda": out.meeting_agenda or out.assessment, "participants": participants or ["strategy", "risk"]}}
            return {"next": "meeting"}
        _ask_for_build(s, p, round_over=True, lang=lang, final=True)
    return {"next": "decide"}


def _contested_meeting(contested: list[Task], lang: Lang) -> tuple[str, list[str]]:
    """The agenda the owner reads on the meeting, and its participants."""
    lines = [msg("work.meeting.contested", lang)]
    for t in contested:
        rev = t.review or {}
        points = (rev.get("counter_arguments") or []) + (rev.get("criteria_unmet") or [])
        lines.append("- " + msg("work.meeting.contested_task", lang, ref=tasks.display_id(t.id), title=t.title,
                                owner=t.owner)
                     + "".join(f"\n  - {msg('work.meeting.counter', lang, x=owner_clip(x, 300))}" for x in points[:3]))
    owners = [t.owner for t in contested if t.owner in agents_config() and t.owner != "project_manager"]
    participants = list(dict.fromkeys(owners + ["risk"]))[:MEETING_MAX_PARTICIPANTS]
    return "\n".join(lines), participants


def route_ceo_review(state: CompanyState) -> str:
    return state["next"]


_E_REF = re.compile(r"\[E(\d+)\]")


def _resolve_citations(pos: PositionOut) -> PositionOut:
    """The company preamble cites evidence as [E1] (order of the evidence array); in a meeting that array is
    evidence_ids, so rewrite [En] to the EV-id. Otherwise other participants and the minutes can't tell
    which ledger entry was meant."""
    def sub(m: re.Match) -> str:
        n = int(m.group(1))
        return f"[{pos.evidence_ids[n - 1]}]" if 0 < n <= len(pos.evidence_ids) else m.group(0)
    return pos.model_copy(update={"statement": _E_REF.sub(sub, pos.statement),
                                  "concerns": [_E_REF.sub(sub, c) for c in pos.concerns]})


def meeting(state: CompanyState) -> dict:
    """Issue-centred meeting: positions in parallel, bounded rebuttals, CEO decision record."""
    pid = state["project_id"]
    lang = app_settings.language()
    with session_scope() as s:
        p = s.get(Project, pid)
        p.phase = "meeting"
        spec = (p.context or {}).get("pending_meeting") or {}
        agenda, participants = spec.get("agenda", ""), spec.get("participants", ["strategy", "risk"])
        rounds = int(limits()["meeting_rebuttal_rounds"])
        work_so_far = "\n\n".join(["## Task results\n" + ctx.results_overview(s, p, chars_per_task=2000),
                                   ctx.evidence_ledger(s, p, 150)])
        # Each reads the brief its own tools allow (ctx.reads_the_web).
        base = {a: f"{ctx.project_brief(s, p, reader=a)}\n\n{work_so_far}" for a in (*participants, "project_manager")}

    budget.gate_step("meeting", len(participants) * (rounds + 1))  # a meeting cut off by a wait restarts at round 0
    queue.check_agent(*participants, "project_manager")   # one stopped participant would make the others' calls go to waste

    def position(agent: str, transcript: str) -> PositionOut:
        prompt = (f"{base[agent]}\n\n## Agenda\n{agenda}\n\n" + (f"## What has been said\n{transcript}\n\n" if transcript else "")
                  + "State your position and back it with EV-ids from the evidence list. Cite in the text by EV-id "
                    "too, as [EV-12], not [E1]. Where another participant's evidence is weak, say whose claim and "
                    "which one.")
        return _resolve_citations(run_agent(agent, "meeting", prompt, PositionOut, project_id=pid))

    def safe_position(agent: str, transcript: str):
        try:
            return agent, position(agent, transcript)
        except (AgentFailed, BudgetExceeded) as exc:   # one absent participant must not abort the meeting
            return agent, PositionOut(stance="undecided", statement=msg("work.meeting.absent", lang, why=str(exc)[:200]),
                                      evidence_ids=[], concerns=[])

    transcript: list[dict[str, Any]] = []
    with ContextThreadPoolExecutor(max_workers=len(participants)) as pool:
        firsts = list(pool.map(lambda a: safe_position(a, ""), participants))
    transcript += [{"round": 0, "agent": a, **o.model_dump(mode="json")} for a, o in firsts]
    for rnd in range(1, rounds + 1):
        text = "\n".join(f"- {x['agent']} ({x['stance']}): {x['statement']} evidence {x['evidence_ids']}"
                         for x in transcript)
        with ContextThreadPoolExecutor(max_workers=len(participants)) as pool:
            rebut = list(pool.map(lambda a: safe_position(a, text), participants))
        transcript += [{"round": rnd, "agent": a, **o.model_dump(mode="json")} for a, o in rebut]

    text = "\n".join(f"- (r{x['round']}) {x['agent']} [{x['stance']}]: {x['statement']} evidence {x['evidence_ids']}"
                     f" concerns {x['concerns']}" for x in transcript)
    decision = run_agent("project_manager", "decision",
                         f"{base['project_manager']}\n\n## Agenda\n{agenda}\n\n## Meeting transcript\n{text}\n\n"
                         "Compare the claims by the quality of their evidence and record the decision. Make only the "
                         "follow-up tasks that are needed, as action_tasks.",
                         DecisionOut, project_id=pid)
    with session_scope() as s:
        p = s.get(Project, pid)
        dec = _record_decision(s, p, decision, lang)
        m = Meeting(project_id=pid, agenda=agenda, participants=participants, transcript=transcript, decision_id=dec.id)
        s.add(m)
        s.flush()
        p.meetings_held += 1
        p.context = {k: v for k, v in (p.context or {}).items() if k != "pending_meeting"}
        minutes = [f"# {msg('work.doc.minutes.heading', lang, id=m.id, agenda=agenda)}",
                   msg("work.doc.minutes.attendees", lang, names=", ".join(participants)), ""] + [
            f"- **{x['agent']}** (r{x['round']}, {x['stance']}): {x['statement']}  \n  "
            + msg("work.doc.minutes.evidence", lang, refs=", ".join(x["evidence_ids"]))
            for x in transcript] + ["", _decision_md(dec, lang)]
        _write_artifact(s, p, "minutes", msg("work.doc.minutes.title", lang, agenda=agenda[:60]),
                        f"projects/{p.slug}/meetings/M-{m.id}.md", "\n".join(minutes))
    return {}


def _record_decision(s, p: Project, out: DecisionOut, lang: Lang) -> Decision:
    sup = [x for x in out.supporting_evidence_ids if evidence.parse_ev(x)]
    dis = [x for x in out.dissenting_evidence_ids if evidence.parse_ev(x)]
    dec = Decision(project_id=p.id, title=out.title, decision=out.decision.value, rationale=out.rationale,
                   supporting_evidence_ids=sup, dissenting_evidence_ids=dis, dissent_summary=out.dissent_summary,
                   open_questions=out.open_questions, approval_required=out.approval_required, status="EFFECTIVE")
    s.add(dec)
    s.flush()
    if out.action_tasks and p.plan_round < int(limits()["max_plan_rounds"]):
        p.plan_round += 1
        created, _ = tasks.create_from_drafts(s, p, out.action_tasks, origin="decision", plan_round=p.plan_round,
                                              lang=lang)
        dec.action_task_ids = [t.id for t in created]
    if out.approval_required:
        req = requests.create(s, AgentRequestDraft(
            # The card's kind already says it is an approval, so the title is the decision's own.
            type=RequestType.APPROVAL, title=out.title,
            summary=f"{msg(f'work.decision.kind.{out.decision.value}', lang)}: {out.rationale}",
            rationale=out.approval_reason or out.rationale,
            options=_option_labels(lang, "approve", "decline", "hold"), recommended_option=msg("work.option.approve", lang),
            action_kind="project.direction_change",
            estimated_cost_usd=None, risk_level=RiskLevel.MEDIUM,
        ), from_agent="project_manager", project_id=p.id, origin=DecisionOrigin(dec.id), action_params={"decision_id": dec.id})
        dec.approval_request_id = req.id
        dec.status = "PENDING_APPROVAL"
        requests.follow_decision(s, req)
        owner_trace.restate(s, p.id, lang)
    return dec


def _decision_md(d: Decision, lang: Lang) -> str:
    def line(key: str, **vars) -> str:
        return msg(f"work.doc.decision.{key}", lang, **vars)
    return "\n".join([
        f"## {line('heading', id=d.id, title=d.title)}",
        f"- {line('verdict', decision=_said_or(f'work.decision.kind.{d.decision}', lang) or d.decision)}",
        f"- {line('rationale', x=d.rationale)}",
        f"- {line('for', refs=', '.join(d.supporting_evidence_ids) or '-')}",
        f"- {line('against', refs=', '.join(d.dissenting_evidence_ids) or '-', dissent=d.dissent_summary or '')}",
        f"- {line('open', x='; '.join(d.open_questions) or '-')}",
        f"- {line('follow_up', refs=', '.join(tasks.display_id(i) for i in d.action_task_ids) or '-')}",
        f"- {line('needs_you', yes=line('yes' if d.approval_required else 'no'))}"])


def _standing_decision(s, p: Project) -> Decision | None:
    """The final decision this project already recorded, while nothing its brief draws on changed after it: no
    task added or finished, no owner directive, no owner response to another request, no realigned plan. A rerun
    (lost checkpoint, retry after a later step failed) reuses it instead of recording the same decision and its
    approval request again (P-5's DEC-5 and DEC-6)."""
    context = p.context or {}
    dec = s.get(Decision, context.get("final_decision_id") or 0)
    if dec is None or dec.project_id != p.id:
        return None
    since = s.scalar(select(Task.id).where(Task.project_id == p.id, or_(
        Task.created_at > dec.created_at, Task.finished_at > dec.created_at)).limit(1))
    # The owner approving this decision's own request settles it; any other owner response may change it.
    answered = s.scalar(select(AgentRequest.id).where(
        AgentRequest.project_id == p.id, AgentRequest.resolved_at > dec.created_at,
        AgentRequest.response.has_key("responded_by"),
        ~((AgentRequest.id == dec.approval_request_id) & (AgentRequest.status == RequestStatus.APPROVED.value)),
    ).limit(1))
    owner_later = any(datetime.fromisoformat(x["at"]) > dec.created_at
                      for x in [*context.get("directives", []), *context.get("realignments", [])])
    return None if since or answered or owner_later else dec


def decide(state: CompanyState) -> dict:
    pid = state["project_id"]
    lang = app_settings.language()
    with session_scope() as s:
        p = s.get(Project, pid)
        p.phase = "deciding"
        standing = _standing_decision(s, p)
        dec_id = standing.id if standing else None
        prompt = None if standing else "\n\n".join([
            ctx.project_brief(s, p), "## Task results\n" + ctx.results_overview(s, p, chars_per_task=2500),
            ctx.evidence_ledger(s, p, 150),
            "Record the final decision on the owner's goal. Leave what web research can't verify in open_questions, "
            "and make no more action_tasks in this run (checks in the field go to the report's validation plan).",
        ])
    if dec_id is None:
        out = run_agent("project_manager", "decision", prompt, DecisionOut, project_id=pid)
        out.action_tasks = []
        with session_scope() as s:
            p = s.get(Project, pid)
            dec_id = _record_decision(s, p, out, lang).id
            p.context = {**(p.context or {}), "final_decision_id": dec_id}
    with session_scope() as s:
        p, dec = s.get(Project, pid), s.get(Decision, dec_id)
        if (p.context or {}).get("inbox_cleanup", {}).get("decision_id") == dec_id:
            return {}
        inbox = requests.pending(s, pid)
        # The decision's own card is listed so a card asking the same can merge into it; consolidate never closes it.
        listing = "\n".join(f"- {r.id}: [{r.type}/{r.action_kind or '-'}{'/' + r.action_params['proposed_action_kind'] if r.action_params.get('proposed_action_kind') else ''}] "
                            f"{r.title} — {ctx.clip(r.summary, 200)} (from {r.from_agent})"
                            + (" (this decision's own card: keep it)" if r.id == dec.approval_request_id else "")
                            for r in inbox)
        decision_text = _decision_md(dec, lang)
    if len(inbox) >= 2:
        # The organization cleans its own inbox before the owner sees it.
        cleanup = run_agent("project_manager", "decision",
                            f"{decision_text}\n\n## Requests about to go to the owner\n{listing}\n\n"
                            "So the owner never makes the same decision twice, merge the requests that ask the same "
                            "thing in effect (merges), and withdraw the ones the final decision above made unneeded "
                            "(withdrawals). Leave the requests that need different decisions as they are.",
                            InboxCleanupOut, project_id=pid)
        with session_scope() as s:
            done = requests.consolidate(s, pid, cleanup.merges, cleanup.withdrawals)
            p = s.get(Project, pid)
            p.context = {**(p.context or {}), "inbox_cleanup": {**done, "decision_id": dec_id}}
    return {}


def report(state: CompanyState) -> dict:
    pid = state["project_id"]
    lang = app_settings.language()
    with session_scope() as s:
        p = s.get(Project, pid)
        p.phase, p.status = "reporting", "REPORTING"
        dec = s.get(Decision, (p.context or {}).get("final_decision_id")) if (p.context or {}).get(
            "final_decision_id") else None
        prompt = "\n\n".join([
            ctx.project_brief(s, p), "## Task results\n" + ctx.results_overview(s, p, chars_per_task=5000),
            ctx.evidence_ledger(s, p, 200), _decision_md(dec, lang) if dec else "",
            owner_trace.report_section(s, pid),
            _messages_section(s, pid),
            f"## How well the evidence holds\n{evidence.mix(s, pid).line("en")}",
            "Write the final report for the owner. As in a report to a superior, open executive_summary with the "
            "conclusion, what the owner should do, and why, in that order and in five lines at most; put the rest in "
            "sections of report_markdown. Put the key conclusions in conclusions with their evidence ids. Never write "
            "a conclusion that only assumptions or inference back as if it were a checked fact. Keep "
            "verified_fact/sourced_claim apart from hypothesis by the evidence list's verification status, and cite "
            "only the finance engine's results for money. Don't call willingness to pay or the workload in the field "
            "verified when web research couldn't confirm it; move it to validation_plan. For what the owner does "
            "themselves (posting, publishing, paying), say done only for what the section on the real status of the "
            "owner's own to-dos marks done, and say the rest wasn't done or isn't known. Where software needs "
            "building, list it in development_needs (an empty array when nothing does). For software that is "
            "approved but not merged yet, say only what was asked and that it isn't built yet: never say what the "
            "build does, follows or contains, nor where it can be opened. What the merged code does is added to this "
            "report from the merged branch. This report ends the project, and nothing it says is queued: never say "
            "the company will revise, raise again or send anything later, and never tell the owner to hold an "
            "approved message for a fixed version. Before you say an approved message lacks something, read it as it "
            "stands on the owner's to-do (the messages section above); say it lacks something only when it does, and "
            "then say what, in one line.",
        ])
    out = run_agent("project_manager", "report", prompt, FinalReportOut, project_id=pid, max_turns=20)
    call_id = last_call_id()
    if len(out.report_markdown) > REPORT_BODY_CHARS:
        log.warning("P-%s report body %d chars, over the %d the contract asks for", pid, len(out.report_markdown),
                    REPORT_BODY_CHARS)
    with session_scope() as s:
        p = s.get(Project, pid)
        mix = evidence.mix(s, pid)
        ledger = _write_artifact(s, p, "evidence", msg("work.doc.evidence.title", lang),
                                 f"projects/{p.slug}/reports/evidence.md", _ledger_md(s, p, mix, lang))
        graded = [(c.statement, c.evidence_ids, evidence.support(s, pid, c.evidence_ids)) for c in out.conclusions]
        body = "\n\n".join(x for x in [
            f"# {out.title}", f"> **{msg('work.doc.report.recommendation', lang)}**: {out.recommendation}",
            f"## {msg('work.doc.report.summary', lang)}\n{out.executive_summary}\n\n{_grounding(mix, graded, lang)}",
            _conclusions_md(graded, lang), out.report_markdown,
            f"## {msg('work.doc.report.validation', lang)}\n" + "\n".join(f"- {x}" for x in out.validation_plan),
            owner_tasks.status_section(s, pid, fence=False),
            f"## {msg('work.doc.report.evidence', lang)}\n"
            + msg("work.doc.report.evidence_where", lang, n=mix.total, title=ledger.title),
        ] if x) + "\n"
        try:  # what a build merged before this report did; update_report keeps it current after later merges
            body = usable.with_section(body, usable.report_section(s, pid, lang))
        except Exception as exc:  # noqa: BLE001 - the build's section must not fail the report
            log.warning("P-%s report: the merged build's section was left out: %s", pid, exc)
        art = _write_artifact(s, p, "report", out.title, f"projects/{p.slug}/reports/final-report.md", body)
        if needs := sorted(out.development_needs, key=lambda n: n.priority):
            # One product, one development ask: the report's needs are the scope of one plan and spec, not a card each.
            first = needs[0]
            listed = [n.model_dump(mode="json") for n in needs]
            requests.create(s, AgentRequestDraft(
                type=RequestType.DEVELOPMENT,
                title=(msg("work.ask.proposal.title_more", lang, title=first.title, n=len(needs) - 1) if len(needs) > 1
                       else msg("work.ask.proposal.title", lang, title=first.title)),
                summary="\n".join(f"- [{n.priority}] {n.title}: {n.problem}" for n in needs),
                rationale=msg("work.ask.proposal.why", lang, path=art.path, n=len(needs)),
                options=_option_labels(lang, "write_spec", "hold", "decline"), recommended_option=None,
                action_kind="dev.implementation_start", estimated_cost_usd=None, risk_level=RiskLevel.MEDIUM,
            ), from_agent="product", project_id=pid,
                origin=ServerOrigin(OriginKind.DEV_PROPOSAL, request_contract.call_row(s, call_id)),
                action_params={"need": {
                "title": first.title, "problem": first.problem, "why_software": first.why_software, "needs": listed}})
        for k in out.kpis[:5]:
            s.add(Kpi(project_id=pid, name=k.name[:200], unit=k.unit[:40], target=k.target,
                      direction="down" if k.direction == "down" else "up", rationale=k.rationale, source="report"))
        try:
            memory.index_project(s, pid)
        except Exception as exc:  # noqa: BLE001 - memory indexing must not fail the report
            p.context = {**(p.context or {}), "memory_index_error": str(exc)[:500]}
        p.status, p.phase = "DONE", "done"
        p.context = {**(p.context or {}), "report_artifact_id": art.id,
                     "report_areas": {a.heading.strip().lstrip("#").strip().rstrip(".:"): a.area
                                      for a in out.section_areas if a.area != "none"},
                     "finished_at": datetime.now(timezone.utc).isoformat()}
    return {}


REPORT_MESSAGES = 20


def _messages_section(s, pid: int) -> str:
    """The messages approved for the owner to send, as they stand on the to-dos: what the report may say about them.
    A task's result can describe an earlier draft (P-10's report held back a message that already said what it asked
    for)."""
    said = usable.promised(s, pid, most=REPORT_MESSAGES, chars=HANDOUT_CHARS)
    return (f"## Messages on the owner's to-dos, as approved\n{fenced('Approved messages', said)}" if said
            else "")


_RESTS_ON = {evidence.Grade.FIRM: "work.doc.report.firm", evidence.Grade.SOURCED: "work.doc.report.sourced",
             evidence.Grade.ASSUMED: "work.doc.report.assumed_tag"}


def _grounding(mix: evidence.Mix, graded: list[tuple[str, list[str], evidence.Grade]], lang: Lang) -> str:
    """The line under the summary that says how much of the report was checked, so a conclusion an assumption carries
    never reads as a finding."""
    assumed = sum(1 for *_, g in graded if g == evidence.Grade.ASSUMED)
    tail = msg("work.doc.report.assumed", lang, n=len(graded), assumed=assumed) if graded else ""
    return msg("work.doc.report.grounding", lang, mix=mix.line(lang)) + tail


def _conclusions_md(graded: list[tuple[str, list[str], evidence.Grade]], lang: Lang) -> str:
    if not graded:
        return ""
    return f"## {msg('work.doc.report.conclusions', lang)}\n" + "\n".join(
        f"- [{msg(_RESTS_ON[g], lang)}] {statement}" + (f" ({', '.join(refs)})" if refs else "")
        for statement, refs, g in graded)


def _ledger_md(s, p: Project, mix: evidence.Mix, lang: Lang) -> str:
    rows = [msg("work.doc.evidence.columns", lang), "|---|---|---|---|---|---|"]
    for e in s.scalars(select(Evidence).where(Evidence.project_id == p.id).order_by(Evidence.id)):
        claim = e.claim.replace("|", "\\|").replace("\n", " ")
        rows.append(f"| {evidence.ev_id(e.id)} | {e.kind} | {e.verification_status} | {e.agent} | {claim} | "
                    f"{e.source_url or ''} |")
    return f"# {msg('work.doc.evidence.heading', lang, name=p.name)}\n\n{mix.line(lang)}\n\n" + "\n".join(rows) + "\n"


# ----------------------------------------------------------------------------- graph

def _lease_checked(node):
    """A holder whose lease is lost stops before the next node's side effects; the checkpoint already holds
    the finished ones for the new holder."""
    @functools.wraps(node)
    def run(state: CompanyState) -> dict:
        queue.check_lease()
        return node(state)
    return run


def build_graph():
    g = StateGraph(CompanyState)
    for name, node in [("plan", plan), ("plan_gate", human_gate), ("schedule", schedule), ("work", work),
                       ("human_gate", human_gate), ("ceo_review", ceo_review), ("meeting", meeting),
                       ("decide", decide), ("final_gate", human_gate), ("report", report)]:
        g.add_node(name, _lease_checked(node))

    g.add_edge(START, "plan")
    g.add_edge("plan", "plan_gate")
    g.add_edge("plan_gate", "schedule")
    g.add_conditional_edges("schedule", route_schedule, ["work", "human_gate", "ceo_review"])
    g.add_edge("work", "schedule")
    g.add_edge("human_gate", "schedule")
    g.add_conditional_edges("ceo_review", route_ceo_review, ["schedule", "meeting", "decide", "human_gate"])
    g.add_edge("meeting", "schedule")
    g.add_edge("decide", "final_gate")
    g.add_edge("final_gate", "report")
    g.add_edge("report", END)
    return g
