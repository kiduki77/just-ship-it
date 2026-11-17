"""The shadow check of a task that ran alone (docs/design/staffing-structure.md, section 7): the checker the rule would
have placed reviews the finished result, and the review is kept as a shadow turn of the task's crew. It gates nothing.
It never calls `evidence.apply_review`, never changes `tasks.review`, and the owner never sees it as a decision. It is a
job of its own (`audit_task`), so the run's next scheduling step never waits for it, and its call (`budget.AUDIT`) is
paid from the project's audit budget, not the task's cap or the round's envelope."""
from __future__ import annotations

import logging

from justshipit import staffing
from justshipit.agents.executor import AgentFailed, last_call_id, last_served, run_agent
from justshipit.agents.independence import Served, assess
from justshipit.contracts.work import ReviewOut
from justshipit.db.models import Task
from justshipit.db.session import session_scope
from justshipit.orchestration import company_graph
from justshipit.services.budget import BudgetExceeded

log = logging.getLogger(__name__)


def run(task_id: int) -> None:
    with session_scope() as s:
        t = s.get(Task, task_id)
        crew = staffing.task_crew(s, task_id) if t is not None else None
        if (crew is None or (t.review or {}).get("verdict") != company_graph.UNCHECKED or staffing.audited(s, crew)
                or staffing.verifier(crew) is not None):
            return
        checker, owner, iteration = staffing.verifier_role(t.owner), t.owner, t.iteration
        author = Served(*t.result["author_served"]) if (t.result or {}).get("author_served") else None
        prompt = company_graph.review_prompt(s, t, checker)
        pid = t.project_id
    try:
        review = run_agent(checker, staffing.AUDIT, prompt, ReviewOut, project_id=pid, task_id=task_id, attempt=iteration)
    except (AgentFailed, BudgetExceeded) as exc:
        log.info("audit of T-%s not taken: %s", task_id, exc)   # a missing sample, never a failed task
        return
    call_id, label = last_call_id(), assess(owner, author, checker, last_served()).label
    if call_id is None:
        return
    with session_scope() as s:
        staffing.record_turn(s, staffing.task_crew(s, task_id), staffing.VerifierTurn(
            role=checker, call_id=call_id, iteration=iteration, review=review, independence=label), shadow=True)
