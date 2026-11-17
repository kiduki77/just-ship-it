"""One status per object and one definition of spent per scope, whichever screen asks. The evaluation round found a
project reading Waiting for you, Not started, Created and Running on four screens, a build Failed in Alerts while
Engineering said it waited for the worker, and four different "spent" figures for one business."""
import uuid
from datetime import UTC, datetime

import cycle_kit
import pytest
from justshipit.api import console
from justshipit.api.app import _project_summaries
from justshipit.db.models import AgentRequest, BudgetMonth, DevJob, LLMCallRecord, Spec, Task, Worker
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import cycle, cycle_board, project_state, projects, queue
from justshipit.settings import limits
from sqlalchemy import delete, func, update

pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def _worker(s, alive: bool) -> None:
    s.execute(update(Worker).values(seen_at=func.now() - func.make_interval(0, 0, 0, 0, 1)))
    if alive:
        queue.worker_seen(s, "worker-one-truth")
    s.flush()


def _ask(s, p) -> AgentRequest:
    r = AgentRequest(project_id=p.id, from_agent="project_manager", type="QUESTION", title="Which audience first?", summary="s",
                     rationale="r", policy_mode="approve", hold_scope="run")
    s.add(r)
    s.flush()
    return r


def _state(s, p) -> tuple[str, bool]:
    [row] = [r for r in _project_summaries(s, include_archived=True, business_id=p.business_id) if r["id"] == p.id]
    one = project_state.of(s, p)
    assert (one["owner_state"], one["needs_worker"]) == (row["owner_state"], row["needs_worker"])
    return row["owner_state"], row["needs_worker"]


def test_a_project_has_one_state_on_the_list_and_in_the_task_panel(s):
    b = cycle_kit.business(s, "One state")
    p = projects.create(s, "Find out who pays", name="Define", start=False, business=b)
    s.flush()
    assert _state(s, p) == ("not_started", True)
    _ask(s, p)
    assert _state(s, p) == ("waiting_you", True)   # the worker still has to move it once answered
    p.status = "FAILED"
    assert _state(s, p) == ("stopped", False)       # a stop outranks the owner's turn
    p.status = "RUNNING"
    p.context = {**(p.context or {}), "paused": {"reason": "later"}}
    assert _state(s, p) == ("pausing", False)
    p.status = "PAUSED"
    assert _state(s, p) == ("paused", False)
    p.status = "ARCHIVED"
    assert _state(s, p) == ("archived", False)
    s.rollback()


def test_the_board_says_the_company_waits_for_the_worker_while_none_runs(s):
    b = cycle_kit.business(s, "Worker off")
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    [p] = cycle.stage_projects(s, st.id)
    p.status = "RUNNING"
    s.add(Task(project_id=p.id, key="a", title="Interview", owner="research", objective="o", acceptance_criteria=["c"],
               status="RUNNING"))
    _worker(s, alive=False)
    off = cycle_board.board(s, b.id, lang="en").sentence
    assert off == f"Cycle 1 · {st.title} · {msg('cycle.held', 'en')}"
    assert "working" not in off
    _worker(s, alive=True)
    assert cycle_board.board(s, b.id, lang="en").sentence == f"Cycle 1 · {st.title} · 1 agent working"
    s.rollback()


def test_no_run_or_build_is_called_stalled_while_no_worker_runs(s, project):
    project.status = "RUNNING"
    spec = Spec(spec_key=f"OT-{project.id}", project_id=project.id, title="CLI", registry_path="specs/ot",
                status="IN_DEVELOPMENT", handoff={})
    s.add(spec)
    s.flush()
    s.add(DevJob(spec_id=spec.id, status="QA", attempt=1))
    s.flush()
    _worker(s, alive=False)
    assert console._stalled(s) == ([], [])
    _worker(s, alive=True)
    runs, devs = console._stalled(s)
    assert project.id in {r.id for r in runs} and spec.id in {d.spec_id for d in devs}
    s.rollback()


def _call(s, cost: float, **kw) -> LLMCallRecord:
    r = LLMCallRecord(call_id=uuid.uuid4().hex[:32], agent="research", purpose="task", provider="claude",
                      status="ok", cost_usd=cost, **kw)
    s.add(r)
    s.flush()
    return r


def test_the_stage_card_and_the_agent_cards_add_up_the_same_ledger(s):
    b = cycle_kit.business(s, "One ledger")
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    [p] = cycle.stage_projects(s, st.id)
    p.status = "RUNNING"
    # the budget's own charge on the task says 2.40; the calls made for it cost 0.75 at list price
    t = Task(project_id=p.id, key="a", title="Interview", owner="research", objective="o", acceptance_criteria=["c"],
             status="RUNNING", spent_usd=2.4)
    s.add(t)
    s.flush()
    _call(s, 0.5, project_id=p.id, task_id=t.id)          # a call of the stage's project, not stamped with the stage
    _call(s, 0.25, project_id=p.id, task_id=t.id, stage_id=st.id)
    board = cycle_board.board(s, b.id, lang="en")
    [card] = board.working
    assert board.current.cost_usd == pytest.approx(0.75)
    assert card.items[0].spent_usd == pytest.approx(0.75)
    s.rollback()


def test_costs_leads_with_this_months_calls_and_settings_reads_the_budget_in_force():
    with session_scope() as s:
        before = console.costs()["month_used_usd"]
        r = _call(s, 1.25)
        rid = r.id
    try:
        assert console.costs()["month_used_usd"] == pytest.approx(before + 1.25)
    finally:
        with session_scope() as s:
            s.execute(delete(LLMCallRecord).where(LLMCallRecord.id == rid))

    month = datetime.now(UTC).strftime("%Y-%m")
    with session_scope() as s:
        row = s.get(BudgetMonth, month)
        kept = None if row is None else (row.limit_usd, row.spent_usd, row.reserved_usd)
        if row is not None:
            s.delete(row)
    try:
        assert console.company_settings()["month_limit_usd"] == float(limits()["monthly_budget_usd"])
        with session_scope() as s:
            s.add(BudgetMonth(month=month, limit_usd=123.0, spent_usd=0.0, reserved_usd=0.0))
        assert console.company_settings()["month_limit_usd"] == 123.0
    finally:
        with session_scope() as s:
            s.execute(delete(BudgetMonth).where(BudgetMonth.month == month))
            if kept is not None:
                s.add(BudgetMonth(month=month, limit_usd=kept[0], spent_usd=kept[1], reserved_usd=kept[2]))


def test_a_reaction_reading_says_only_the_readings_that_failed(s):
    from datetime import timedelta

    from justshipit.services import metrics
    b = cycle_kit.business(s, "Reactions")
    now = datetime.now(UTC)
    seen = metrics.observed(s, b.id, now - timedelta(days=14), now, lang="en")
    # the stages with no reading yet are the Numbers cards' to say, from the People list
    assert seen.gaps == []
    assert any("Got value" in m or "first" in m.lower() for m in seen.missing)
    s.rollback()
