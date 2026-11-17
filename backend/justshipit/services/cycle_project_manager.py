"""The CEO's four cycle jobs: propose a cycle, revise it after the owner said what to change, summarize a stage at its
end, and look back once the owner finished the cycle. Each builds its input from the database, holds no transaction
during the model call, and hands the answer to services/cycle.py, which checks it and writes every number itself.
"""
from __future__ import annotations

import json
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.agents.executor import run_agent
from justshipit.contracts.cycle import CycleProposalOut, CycleRetroOut, CycleRevisionOut, StageSummaryOut
from justshipit.db.models import (
    AgentRequest,
    Bet,
    Business,
    Cycle,
    CycleProposal,
    CycleStage,
    Project,
    Schedule,
    Task,
)
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, msg
from justshipit.services import activity, app_settings, cycle, evidence, gate, roadmap
from justshipit.services.fence import fenced
from justshipit.settings import settings

LIVE_BET = ("RUNNING", "LISTENING")


def _system(name: str) -> str:
    return (settings().prompts_dir / f"{name}.md").read_text(encoding="utf-8")


def _plan(s: Session, business: Business) -> list[str]:
    brief = roadmap.approved_brief(s, business.id)
    out = [f"## Business\n{business.name}: {business.one_liner}", "## The plan the owner approved"]
    for sec in (brief.sections if brief else []):
        if sec.get("lines"):
            out += [f"### {sec['title']}", *roadmap.prompt_lines(sec["lines"])]
    return out


def _choices(lang: Lang) -> list[str]:
    """The closed lists a stage may name, written from gate.POLICY and the to-do kinds so the CEO reads the same sets
    the server checks against."""
    kinds = [f"- {k}: {gate.POLICY[k].why}" for k in gate.RECURRING_KINDS]
    todos = [f"- {k}: {msg(f'owner_task.kind.{k}', lang)}" for k in gate.OWNER_WORK_KINDS]
    return ["## Recurring jobs a stage may list in allows_kinds (none runs unless a started stage lists it)", *kinds,
            "## Kinds of to-do a stage may list in allows_owner_work (none is made unless the stage lists it)", *todos]


def _live(s: Session, business_id: int) -> dict[str, list[int]]:
    """The work running before cycles began, by kind and id: what an adopt proposal must lay out."""
    projects = list(s.scalars(select(Project.id).where(Project.business_id == business_id,
                                                       Project.status.in_(cycle.LIVE_PROJECT))))
    bets = list(s.scalars(select(Bet.id).where(Bet.business_id == business_id, Bet.status.in_(LIVE_BET))))
    schedules = [sc.id for sc in s.scalars(select(Schedule).where(Schedule.enabled.is_(True)))
                 if (sc.payload or {}).get("business_id") == business_id]
    return {"project": projects, "bet": bets, "schedule": schedules}


def _history(s: Session, business_id: int) -> tuple[list[str], bool]:
    """What earlier work took and cost, as the ground for the CEO's guesses; False when there is none to compare."""
    done = list(s.scalars(select(Project).where(Project.business_id == business_id, Project.status == "DONE")))
    stages = [st for st in s.scalars(select(CycleStage).where(CycleStage.business_id == business_id,
                                                              CycleStage.status == "done",
                                                              CycleStage.started_at.is_not(None)))
              if st.awaiting_at or st.finished_at]
    lines = []
    if done:
        lines.append(f"- finished projects: {len(done)}, median usage {median(p.spent_usd for p in done):.2f} USD")
    for st in stages:
        end = st.awaiting_at or st.finished_at
        cost = (st.summary or {}).get("cost_usd")
        lines.append(f"- earlier stage '{st.title}' ({st.key}): {int((end - st.started_at).total_seconds() // 60)} "
                     f"minutes, {cost if cost is not None else '?'} USD")
    return (["## What earlier work took (the ground for estimate_basis=history)", *lines] if lines else
            ["## What earlier work took", "- nothing to compare yet: every estimate is a guess"]), bool(lines)


def _past_cycles(s: Session, business_id: int) -> list[str]:
    out = []
    for c in s.scalars(select(Cycle).where(Cycle.business_id == business_id, Cycle.closed_at.is_not(None),
                                           Cycle.state.in_(("closed", "stopped"))).order_by(Cycle.number)):
        heads = [f"{st.title}: {(st.summary or {}).get('headline', '')}" for st in cycle.stages_of(s, c.id)
                 if st.status == "done"]
        note = (c.retro or {}).get("next_cycle_note")
        out += [f"### Cycle {c.number} ({c.state})", *[f"- {h}" for h in heads], *([f"- next: {note}"] if note else [])]
    ideas = []
    for bet in s.scalars(select(Bet).where(Bet.business_id == business_id, Bet.retro.is_not(None))):
        ideas += [f"- from BET-{bet.id}: {nb.get('title')}: {nb.get('pitch')}" for nb in (bet.retro or {}).get("next_bets", [])]
    return ((["## Earlier cycles", *out] if out else []) +
            (["## Next experiments earlier retros suggested (propose them as stages if they still fit)", *ideas]
             if ideas else []))


def _work_lines(s: Session, live: dict[str, list[int]]) -> list[str]:
    out = ["## Work already running (adopt mode: put every id below in exactly one stage's maps_existing)"]
    for p in s.scalars(select(Project).where(Project.id.in_(live["project"]))):
        tasks = list(s.scalars(select(Task.status).where(Task.project_id == p.id)))
        out.append(f"- project {p.id} '{p.name}': {p.status}, tasks {sum(t == 'DONE' for t in tasks)}/{len(tasks)}")
    for b in s.scalars(select(Bet).where(Bet.id.in_(live["bet"]))):
        out.append(f"- bet {b.id} '{b.title}': {b.status}")
    for sc in s.scalars(select(Schedule).where(Schedule.id.in_(live["schedule"]))):
        out.append(f"- schedule {sc.id} '{sc.name}': {sc.kind} every {sc.interval_hours:g} hours")
    return out


def _proposal_prompt(s: Session, c: Cycle, p: CycleProposal, lang: Lang) -> tuple[str, dict]:
    business = s.get(Business, c.business_id)
    live = _live(s, c.business_id) if c.mode == "adopt" else {"project": [], "bet": [], "schedule": []}
    history, has_history = _history(s, c.business_id)
    parts = [*_plan(s, business), f"## Mode\n{c.mode}", *_past_cycles(s, c.business_id), *history, *_choices(lang)]
    if c.mode == "adopt":
        parts += _work_lines(s, live)
    ready = cycle.proposal(s, c.id, status=("ready", "superseded"))
    if ready is not None:
        frozen = [st.key for st in cycle.stages_of(s, c.id) if st.status != "planned"]
        parts += ["## The version the owner read", json.dumps(ready.stages, ensure_ascii=False, indent=1),
                  "## Stages already started (keep them first and exactly as they are)", ", ".join(frozen) or "none",
                  "## What the owner asked to change", fenced("owner_note", p.owner_note or "")]
    return "\n\n".join(parts), {"live": live, "history": has_history}


def propose(cycle_id: int, version: int, *, attempt: int = 1, max_attempts: int = 1) -> None:
    """Handlers `cycle_propose` and `cycle_revise`: the CEO writes a version of the cycle's stages."""
    lang = app_settings.language()
    with session_scope() as s:
        p = s.scalar(select(CycleProposal).where(CycleProposal.cycle_id == cycle_id, CycleProposal.version == version))
        if p is None or p.status != "drafting":
            return
        c = s.get(Cycle, cycle_id)
        revising = cycle.proposal(s, cycle_id, status=("ready", "superseded")) is not None
        prompt, basis = _proposal_prompt(s, c, p, lang)
        business_id = c.business_id
    model = CycleRevisionOut if revising else CycleProposalOut
    out = run_agent("project_manager", "cycle", prompt, model, project_id=None,
                    business_id=business_id, attempt=attempt,
                    system_override=_system("cycle_revise" if revising else "cycle_propose"))
    with session_scope() as s, activity.as_actor("project_manager"):
        cycle.store_proposal(s, cycle_id, version, out, basis, lang=lang)


def _summary_prompt(s: Session, st: CycleStage, facts: dict) -> str:
    rest = [x for x in cycle.stages_of(s, st.cycle_id) if x.status == "planned"]
    tasks = list(s.scalars(select(Task).where(Task.project_id.in_(facts["project_ids"])).order_by(Task.id))) \
        if facts["project_ids"] else []
    pending = list(s.scalars(select(AgentRequest).where(AgentRequest.project_id.in_(facts["project_ids"]),
                                                        AgentRequest.status == "PENDING"))) if facts["project_ids"] else []
    parts = [f"## The stage\n{st.title}\nWhat the company did: {st.doing}\nWhat the owner did: {st.owner_does}",
             "## Tasks", *[f"- T-{t.id} [{t.status}] ({t.owner}) {t.title}: {((t.result or {}).get('summary') or '')[:300]}"
                          for t in tasks],
             "## Evidence (cite only these ids)",
             *[evidence.digest(s, pid, limit=60) for pid in facts["project_ids"]],
             "## Decisions still open", *([f"- REQ-{r.id} {r.title}" for r in pending] or ["- none"]),
             (f"## Numbers (the server writes them; do not repeat them as fact)\nusage {facts['cost_usd']} USD, "
              f"{facts['elapsed_s'] // 60} minutes, tasks {facts['tasks']['done']}/{facts['tasks']['total']} done"),
             "## Stages still to come (next_key is one of these keys, or null)",
             *([f"- {x.key}: {x.title}" for x in rest] or ["- none: this is the last stage"])]
    return "\n\n".join(parts)


def summarize(stage_id: int, round_: int, *, attempt: int = 1, max_attempts: int = 1) -> None:
    """Handler `cycle_summarize`: the CEO's summary of a stage that ended, for the gate the owner decides at."""
    lang = app_settings.language()
    with session_scope() as s:
        st = s.get(CycleStage, stage_id)
        if st is None or st.status != "closing" or st.round != round_:
            return
        prompt, business_id = _summary_prompt(s, st, cycle.stage_facts(s, st)), st.business_id
    out = run_agent("project_manager", "cycle", prompt, StageSummaryOut, project_id=None, business_id=business_id,
                    attempt=attempt, system_override=_system("cycle_summary"))
    with session_scope() as s, activity.as_actor("project_manager"):
        cycle.store_summary(s, stage_id, round_, out, lang=lang)


def retro(cycle_id: int, *, attempt: int = 1, max_attempts: int = 1) -> None:
    """Handler `cycle_retro`: the CEO looks back at a cycle the owner finished; the next cycle then opens."""
    lang = app_settings.language()
    with session_scope() as s:
        c = s.get(Cycle, cycle_id)
        if c is None or c.state != "retro":
            return
        stages = cycle.stages_of(s, cycle_id)
        lines = [f"- {st.title} ({st.status}): {json.dumps(st.summary or {}, ensure_ascii=False)[:1500]}"
                 for st in stages]
        bets = [f"- BET-{b.id} {b.title}: {b.verdict}, {(b.outcome or '')[:300]}"
                for b in s.scalars(select(Bet).where(Bet.id.in_([st.bet_id for st in stages if st.bet_id])))]
        prompt = "\n\n".join([*_plan(s, s.get(Business, c.business_id)), "## The stages and their summaries", *lines,
                              "## Experiments the stages ran", *(bets or ["- none"])])
        business_id = c.business_id
    out = run_agent("project_manager", "cycle", prompt, CycleRetroOut, project_id=None, business_id=business_id,
                    attempt=attempt, system_override=_system("cycle_retro"))
    with session_scope() as s, activity.as_actor("project_manager"):
        cycle.store_retro(s, cycle_id, out, lang=lang)

