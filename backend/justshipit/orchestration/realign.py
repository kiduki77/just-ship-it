"""Realign a project when the owner gives new direction in a note, an answer or a directive.

Owner guidance used to reach only agents whose call happened to start afterwards; work planned
earlier kept running in the old direction and kept raising requests the owner had already ruled out.
Here the CEO re-reads the plan with every owner answer and adjusts what has not started: cancel,
rewrite or add tasks, and withdraw pending requests that contradict the owner.

A directive also becomes rules (`services.plan_screen`): the work it rules out, as keywords. They are stored on the
directive and checked by the server from then on, so the directive keeps binding every later plan, request and
scheduled task of the business, and a task already running stops at its next step (`company_graph.work`). A project
that is not running still gets its rules; only its plan is left alone.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from justshipit.agents.executor import run_agent
from justshipit.contracts.planning import RealignOut, RequestWithdrawOut
from justshipit.contracts.requests import RequestStatus
from justshipit.contracts.work import TaskStatus
from justshipit.db.models import AgentRequest, OwnerTask, Project, Task
from justshipit.db.session import session_scope
from justshipit.engineering import specs
from justshipit.i18n import Lang, msg
from justshipit.orchestration import company_graph
from justshipit.orchestration import context as ctx
from justshipit.services import activity, app_settings, matters, owner_tasks, plan_screen, registry, requests, tasks
from justshipit.services.fence import fenced

log = logging.getLogger(__name__)
_OPEN = (TaskStatus.PROPOSED.value, TaskStatus.READY.value)
_ACTIVE = ("RUNNING", "WAITING_HUMAN", "PAUSED")


def _task_list(s, pid: int) -> str:
    rows = s.scalars(select(Task).where(Task.project_id == pid).order_by(Task.id))
    return "\n".join(f"- {tasks.display_id(t.id)} [{t.status}] ({t.owner}) {t.title}: {ctx.clip(t.objective, 300)}"
                     for t in rows)


def _check_trigger(t: OwnerTask, ref: str) -> str:
    """What the owner did with a check: found something, skipped it with a reason, or reopened it."""
    if t.status == owner_tasks.OPEN:
        return (f"## A check the owner reopened\n- {ref} {t.title}: the result the owner gave earlier no longer holds. "
                "Don't build on it; the owner will give a new one.")
    head = "What the owner found by hand" if t.status == owner_tasks.DONE else "A check the owner skipped, and why"
    return f"## {head}\n- {ref} {t.title}\n" + fenced(f"{ref} result", owner_tasks.owner_words(t.result))


def _retract_reopened(s, project_id: int, t: OwnerTask | None) -> None:
    """The engineers read clarifications.md beside the spec. When the CEO wrote one from a check's result and the
    owner then reopened the check, the file says that result no longer holds."""
    if t is None or t.status != owner_tasks.OPEN or (spec := matters.live_spec(s, project_id)) is None:
        return
    ref = owner_tasks.display_id(t.id)
    path = f"{spec.registry_path}/clarifications.md"
    text = registry.read(path) if (registry.root() / path).exists() else ""
    if text.rfind(f"after the owner's {ref}\n") > text.rfind(f"The owner reopened {ref}\n"):
        specs.add_clarification(spec, f"The owner reopened {ref}",
                                f"The result of {ref} written above no longer holds. Don't build on it until the "
                                "owner gives a new one.")


def _decided(r: AgentRequest) -> str:
    """The decision that prompted the re-plan, as the CEO reads it: the owner's answer, or what its rule did when nobody
    answered by the deadline, which is never the owner's answer."""
    resp = r.response or {}
    if requests.defaulted(r):
        return (f"## A decision nobody answered, which went on with its default\n- REQ-{r.id} {r.title}: nobody "
                f"answered by the deadline, so the team goes with the recommended option \"{resp.get('chosen_option')}\". "
                "It is the default, not the owner's answer.")
    if r.status == RequestStatus.EXPIRED.value:
        return (f"## A decision nobody answered\n- REQ-{r.id} {r.title}: it closed at its deadline without the owner's "
                "answer, and a task that could not start without it was cancelled. Plan without it: don't wait for it "
                "and don't assume an answer.")
    return (f"## The owner's new answer\n- REQ-{r.id} {r.title} → {r.status}: "
            f"{resp.get('answer') or ''} {resp.get('note') or ''}")


def realign(project_id: int, request_id: int | None = None, directive: int | None = None,
            owner_task: int | None = None, *, resume_after: bool = False) -> dict:
    """Re-plan unstarted work after an owner answer or a card's expiry (`request_id`), a standalone directive (its
    number) or what the owner found by hand (the to-do's id). `resume_after`: the answer or expiry also freed the
    waiting run, which goes on only once this is done (`requests.resume_replanned`), never on the plan before it."""
    lang = app_settings.language()
    with session_scope() as s:
        p = s.get(Project, project_id)
        if owner_task is not None:
            _retract_reopened(s, project_id, s.get(OwnerTask, owner_task))
        # A waiting or paused run has no work in flight, so its plan can be adjusted too. A directive on a project
        # that is not running still needs its rules, because the next run of the business must keep it.
        if p is None or p.status == "ARCHIVED" or (p.status not in _ACTIVE and directive is None):
            return {"skipped": f"project status {p.status if p else 'missing'}"}
        replan = p.status in _ACTIVE
        if replan:
            # The cards the round is holding were drafted before this answer: they go up now, so the CEO can
            # withdraw the ones it contradicts instead of their reaching the owner at the round's end.
            company_graph.release_held(s, p)
        if directive is not None:
            d = next((x for x in (p.context or {}).get("directives", []) if x["n"] == directive), None)
            if d is None:
                return {"skipped": f"directive #{directive} missing"}
            source = {"request": None, "directive": directive}
            trigger_text = f"## The owner's new instruction\n- Instruction #{directive}: {d['text']}"
        elif owner_task is not None:
            t = s.get(OwnerTask, owner_task)
            ref = owner_tasks.display_id(t.id)
            source = {"request": None, "owner_task": ref}
            trigger_text = _check_trigger(t, ref)
        else:
            trigger = s.get(AgentRequest, request_id)
            source = {"request": f"REQ-{trigger.id}"}
            trigger_text = _decided(trigger)
        prompt = "\n\n".join(filter(None, [
            ctx.project_brief(s, p),
            trigger_text,
            "## Current tasks\n" + _task_list(s, p.id),
            ctx.build_state(s, p),
            "Realign the plan with the owner's answers and instructions (the whole \"The owner's answers and "
            "instructions\" section above) as the constraint that comes first. Cancel the tasks that go the way the "
            "owner ruled out or rewrite their goal, and add only the tasks the new direction needs. Withdraw the waiting "
            "requests (\"Requests waiting for the owner\" above) that go against the owner. Finished tasks can't "
            "change. If the direction still holds, change nothing. When the new instruction says not to do some work, "
            "write one rule per piece of work in directive_rules; the server screens current and later tasks and "
            "requests with them. When the build has a spec and the owner's words change what it builds, say so in "
            "spec_clarification instead of a task."
            + ("" if replan else " This project isn't running now, so its plan stays as it is: write only "
                                 "directive_rules."),
        ]))
    out = run_agent("project_manager", "plan", prompt, RealignOut, project_id=project_id)
    with session_scope() as s:
        p = s.scalar(select(Project).where(Project.id == project_id).with_for_update())
        rules_added, rules_refused = _store_rules(s, p, directive, out, lang) if directive is not None else ([], [])
        if out.spec_clarification and (spec := matters.live_spec(s, project_id)) is not None:
            after = source["request"] or source.get("owner_task") or f"instruction #{directive}"
            specs.add_clarification(spec, f"Project Manager, after the owner's {after}", out.spec_clarification)
        active = plan_screen.rules(s, p)
        cancelled, revised, added, warnings = [], [], [], []
        if replan:
            for tid in out.cancel_task_ids:
                t = s.scalar(select(Task).where(Task.id == tid, Task.project_id == project_id).with_for_update())
                if t and t.status in _OPEN:
                    tasks.transition(t, TaskStatus.CANCELLED, error=msg("work.task.cancelled_by_ceo", lang))
                    cancelled.append(t.id)
            for rv in out.revise_tasks:
                t = s.scalar(select(Task).where(Task.id == rv.task_id, Task.project_id == project_id).with_for_update())
                if t and t.status in _OPEN and t.id not in cancelled:
                    t.title, t.objective, t.acceptance_criteria = rv.title[:300], rv.objective, rv.acceptance_criteria
                    revised.append(t.id)
            added, warnings = tasks.create_from_drafts(s, p, out.new_tasks, origin="realign", plan_round=p.plan_round,
                                                       lang=lang)
        # The rules apply whatever the CEO listed: open work that breaks one is cancelled now.
        for t in s.scalars(select(Task).where(Task.project_id == project_id, Task.status.in_(_OPEN)).with_for_update()):
            if t.id not in cancelled and (rule := plan_screen.broken(active, t.title)):
                tasks.transition(t, TaskStatus.CANCELLED,
                                 error=msg("work.task.cancelled_by_rule", lang, ref=rule.ref, label=rule.label))
                cancelled.append(t.id)
        withdraw = [*out.withdraw_requests] if replan else []
        # A card the server bound to its flow (a spec, a merge) is never withdrawn by a rule: consolidate spares it.
        withdraw += [RequestWithdrawOut(id=r.id, reason=msg("work.rule.withdrawn", lang, ref=rule.ref, label=rule.label))
                     for r in requests.pending(s, project_id) if not matters.server_bound(r)
                     and (rule := plan_screen.broken(active, r.title)) and r.id not in {w.id for w in withdraw}]
        withdrawn = requests.consolidate(s, project_id, [], withdraw, by="project_manager")["withdrawn"]
        if replan:
            plan = dict(p.plan or {})
            plan["goal_interpretation"] = out.goal_interpretation or plan.get("goal_interpretation", "")
            plan["out_of_scope"] = [*plan.get("out_of_scope", []), *out.out_of_scope_additions]
            p.plan = plan
        # What prompted it: a decision ("request": "REQ-5") or an instruction ("directive": 3). Records from before
        # instructions had their own key say "지시 #3" in "request".
        record = {"at": datetime.now(timezone.utc).isoformat(), **source,
                  "assessment": out.assessment, "cancelled": cancelled, "revised": revised,
                  "added": [t.id for t in added], "withdrawn": withdrawn, "warnings": warnings, "rules": rules_added,
                  "rules_refused": rules_refused}
        p.context = {**(p.context or {}), "realignments": [*(p.context or {}).get("realignments", []), record]}
        if resume_after:
            requests.resume_replanned(s, s.get(AgentRequest, request_id))
        return record


def _store_rules(s, p: Project, n: int, out: RealignOut, lang: Lang) -> tuple[list[dict], list[dict]]:
    """Put the CEO's rules on directive #n, and beside them the ones `plan_screen.screen_rules` refused, which the
    owner sees with the directive and the activity log names."""
    drafted = [{"label": r.label[:120], "keywords": [k.strip()[:40] for k in r.keywords if k.strip()][:3]}
               for r in out.directive_rules]
    rules, refused = plan_screen.screen_rules(s, p, drafted, lang=lang)
    for r in refused:
        log.warning("P-%s directive #%s: rule %r refused: %s", p.id, n, r["label"], r["why"])
        activity.record(s, verb="directive_rule_refused", entity="project", entity_id=p.id, project_id=p.id,
                        actor="system", summary=msg("work.rule.refused", lang, n=n, label=r["label"], why=r["why"])[:500],
                        data={"n": n, "rule": r})
    ctx_ = p.context or {}
    p.context = {**ctx_, "directives": [{**d, "rules": rules, "refused_rules": refused} if d["n"] == n else d
                                        for d in ctx_.get("directives", [])]}
    return rules, refused
