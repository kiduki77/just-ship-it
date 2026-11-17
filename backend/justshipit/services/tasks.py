"""Task Registry: creation from agent drafts, DAG validation, state transitions, scheduling."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.contracts import owner_text
from justshipit.contracts.work import TASK_TRANSITIONS, TERMINAL_TASK_STATUSES, TaskDraft, TaskStatus
from justshipit.contracts.work_path import HoldPhase
from justshipit.db.models import Project, Task
from justshipit.i18n import Lang, msg
from justshipit.services import activity, plan_screen, rounds
from justshipit.settings import agents_config, limits
from justshipit.staffing import checked_family


class InvalidTransition(RuntimeError):
    pass


def display_id(task_id: int) -> str:
    return f"T-{task_id}"


_CITED = re.compile(r"[ \t]*((?:\[E\d+(?:\s*[,·]\s*E?\d+)*\])+)")
_DOC_HEAD = re.compile(r"\A# T-\d+ ")


def document(text: str, evidence_ids: list[int]) -> str:
    """A task's result document as the owner reads it. The agent cites evidence by its place in the result ([E1]),
    which the console cannot point at, so each becomes the ledger's EV-id, the code the console marks and links; a
    number with no evidence behind it goes. Brief line ids go too. Documents written before keep '# T-12 ' on their
    heading, which the console would show as the title twice."""
    def cite(m: re.Match) -> str:
        ns = [int(n) for n in re.findall(r"\d+", m.group(1))]
        ids = list(dict.fromkeys(evidence_ids[n - 1] for n in ns if 0 < n <= len(evidence_ids)))
        return (" " if m.group(0)[:1] in " \t" else "") + "".join(f"[EV-{i}]" for i in ids) if ids else ""
    return owner_text.body(_CITED.sub(cite, _DOC_HEAD.sub("# ", text or "")))


def transition(task: Task, new: TaskStatus, *, error: str | None = None) -> None:
    cur = TaskStatus(task.status)
    if new not in TASK_TRANSITIONS[cur]:
        raise InvalidTransition(f"{display_id(task.id)}: {cur} -> {new} not allowed")
    task.status = new.value
    now = datetime.now(timezone.utc)
    if new == TaskStatus.RUNNING and task.started_at is None:
        task.started_at = now
    if new in TERMINAL_TASK_STATUSES:
        task.finished_at = now
    if error:
        task.error = error


def budget_usd(owner: str) -> float:
    """A task's budget: the company's, or more when agents.yaml gives its owner more."""
    return max(float(limits()["task_budget_usd"]), float(agents_config().get(owner, {}).get("task_budget_usd") or 0))


def _assignable_owners() -> set[str]:
    return {k for k, v in agents_config().items() if k != "project_manager" and v.get("department") in ("business", "audit")}


# The origins whose drafts come from the Project Manager's own output, which is asked to name each task's family.
_FAMILY_ASKED = (*rounds.PLANNED, "realign")


def slug(key: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", key.lower()).strip("_")[:60] or "task"


def create_from_drafts(s: Session, project: Project, drafts: list[TaskDraft], *, origin: str,
                       plan_round: int, lang: Lang | None = None) -> tuple[list[Task], list[str]]:
    """Create tasks, resolving `depends_on` against this batch's keys and existing tasks.

    Returns (tasks, warnings). Invalid owners fall back to research; cyclic edges are dropped. A draft an owner
    directive rules out, or one that repeats finished work without new evidence, is not created and the activity log
    says why (`plan_screen`), in `lang` (the owner's setting when a caller outside a run gives none). A task the owner
    adds is the owner's call and is never screened. Each task keeps the family it is placed under (`Task.family`; a
    missing or unknown one becomes `other`, with a warning in the log) for the crew that staffs it later, and the
    created draft's `family` says the same.
    """
    if lang is None:
        from justshipit.services import app_settings
        lang = app_settings.language()
    warnings: list[str] = []
    if origin != "owner":
        drafts = _screen(s, project, drafts, origin, warnings, lang)
    owners = _assignable_owners()
    # A round holds at most max_tasks_per_round planned tasks, the plan's and its follow-ups' together; a realign's
    # new tasks answer the owner and are held to the round's cost envelope instead (`rounds.admit`).
    cap = rounds.max_tasks() - (rounds.planned_in(s, project, plan_round) if origin in rounds.PLANNED else 0)
    if origin != "owner" and len(drafts) > max(0, cap):
        warnings.append(f"plan had {len(drafts)} tasks; round {plan_round} takes {max(0, cap)} more")
        for d in drafts[max(0, cap):]:
            activity.record(s, verb="task_not_planned", entity="project", entity_id=project.id, project_id=project.id,
                            actor="system", summary=msg("work.task.not_planned", lang, title=d.title[:80],
                                                        why=msg("work.task.round_cap", lang, n=rounds.max_tasks())),
                            data={"title": d.title[:300], "origin": origin, "why": "round_cap"})
        drafts = drafts[:max(0, cap)]

    existing = {t.key: t for t in s.scalars(select(Task).where(Task.project_id == project.id))}
    key_map: dict[str, str] = {}
    for d in drafts:
        base = slug(d.key)
        key = base
        n = 2
        while key in existing or key in key_map.values():
            key = f"{base}_{n}"
            n += 1
        key_map[d.key] = key

    created: dict[str, Task] = {}
    for d in drafts:
        owner = d.owner if d.owner in owners else "research"
        if owner != d.owner:
            warnings.append(f"task {d.key}: unknown owner '{d.owner}' -> research")
        d.family = checked_family(d.family, owner, asked=origin in _FAMILY_ASKED)
        t = Task(project_id=project.id, key=key_map[d.key], title=d.title[:300], owner=owner,
                 objective=d.objective, acceptance_criteria=d.acceptance_criteria, depends_on=[],
                 status=TaskStatus.PROPOSED.value, origin=origin, family=d.family, plan_round=plan_round,
                 max_iterations=int(limits()["max_rework_iterations"]),
                 budget_usd=budget_usd(owner))
        s.add(t)
        created[d.key] = t
    s.flush()

    for d in drafts:
        deps = []
        for ref in d.depends_on:
            ref = ref.strip()
            target = created[ref] if ref in created else _task_ref(s, project, existing, ref)
            if target is None:
                warnings.append(f"task {d.key}: unknown dependency '{ref}' ignored")
            elif target.id != created[d.key].id:
                deps.append(target.id)
        created[d.key].depends_on = sorted(set(deps))

    _break_cycles(list(created.values()), warnings)
    for d in drafts:
        if d.redoes and (old := _task_ref(s, project, existing, d.redoes)) is not None:
            s.refresh(old, with_for_update=True)  # an owner's retry (FAILED -> READY) may have landed meanwhile
            if old.status == TaskStatus.FAILED.value:
                from justshipit.services import requests  # it imports this module through request_contract
                failed_at = old.finished_at
                transition(old, TaskStatus.REPLACED)
                old.finished_at = failed_at
                _take_build_input(s, project, old.id, created[d.key].id)
                requests.carry_holds(s, old.id, created[d.key].id)
    return list(created.values()), warnings


def _take_build_input(s: Session, project: Project, old: int, new: int) -> None:
    """A task redoing a failed input of the build's spec becomes the input the build waits for."""
    if old not in (((project.plan or {}).get("build") or {}).get("task_ids") or []):
        return
    # Under the row's lock, as a realign and the build ask write the plan; pending changes are flushed first.
    p = s.scalar(select(Project).where(Project.id == project.id).with_for_update()
                 .execution_options(populate_existing=True))
    build = p.plan["build"]
    p.plan = {**p.plan, "build": {**build, "task_ids": [new if i == old else i for i in build["task_ids"]]}}


def _task_ref(s: Session, project: Project, existing: dict[str, Task], ref: str) -> Task | None:
    """A task of this project named by key or id ("T-43", "T43", "43")."""
    ref = ref.strip()
    if slug(ref) in existing:
        return existing[slug(ref)]
    if m := re.fullmatch(r"(?:T-?)?(\d+)", ref, flags=re.I):
        t = s.get(Task, int(m.group(1)))
        return t if t is not None and t.project_id == project.id else None
    return None


def _screen(s: Session, project: Project, drafts: list[TaskDraft], origin: str, warnings: list[str],
            lang: Lang) -> list[TaskDraft]:
    active = plan_screen.rules(s, project)
    kept = []
    for d in drafts:
        if why := plan_screen.task_problem(s, project, d, active, lang=lang):
            warnings.append(f"task {d.key}: not created: {why}")
            activity.record(s, verb="task_not_planned", entity="project", entity_id=project.id, project_id=project.id,
                            actor="system", summary=msg("work.task.not_planned", lang, title=d.title[:80], why=why),
                            data={"title": d.title[:300], "origin": origin, "why": why})
        else:
            kept.append(d)
    return kept


def _break_cycles(tasks: list[Task], warnings: list[str]) -> None:
    by_id = {t.id: t for t in tasks}
    state: dict[int, int] = {}

    def visit(t: Task) -> None:
        state[t.id] = 1
        for dep in list(t.depends_on):
            if dep not in by_id:
                continue
            if state.get(dep) == 1:
                t.depends_on = [x for x in t.depends_on if x != dep]
                warnings.append(f"cycle {display_id(t.id)} -> {display_id(dep)} removed")
            elif state.get(dep) is None:
                visit(by_id[dep])
        state[t.id] = 2

    for t in tasks:
        if t.id not in state:
            visit(t)


def promote_ready(s: Session, project_id: int) -> list[Task]:
    """PROPOSED -> READY when all dependencies are terminal and no open card holds the task's start, which waits like
    a dependency. Returns the READY tasks no card holds."""
    from justshipit.services import holds  # it imports this module through request_contract
    held = holds.held(s, project_id, HoldPhase.START)
    tasks = list(s.scalars(select(Task).where(Task.project_id == project_id)))
    by_id = {t.id: t for t in tasks}
    terminal = {st.value for st in TERMINAL_TASK_STATUSES}
    for t in tasks:
        if t.status == TaskStatus.PROPOSED.value and t.id not in held and all(
                by_id[d].status in terminal for d in t.depends_on if d in by_id):
            transition(t, TaskStatus.READY)
    return [t for t in tasks if t.status == TaskStatus.READY.value and t.id not in held]


def unsettled(s: Session, project_id: int) -> list[Task]:
    terminal = {st.value for st in TERMINAL_TASK_STATUSES}
    return [t for t in s.scalars(select(Task).where(Task.project_id == project_id)) if t.status not in terminal]
