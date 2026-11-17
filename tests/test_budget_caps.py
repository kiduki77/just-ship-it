"""The money caps a call is held against: the month, the project, and a task at one and a half times its budget. A
refusal is final and holds nothing, and callers racing at a cap boundary never hold more than the cap allows."""
import threading
import time

import pytest
from justshipit.db.models import AppSetting, BudgetMonth, LLMCallRecord, Project, Task
from justshipit.db.session import engine, session_scope
from justshipit.services import budget
from justshipit.services.budget import BudgetExceeded
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError

# usage.PURPOSES: decision holds 0.8, plan 1.0, work 1.5, dev 4.0 USD-eq.


@pytest.fixture(autouse=True)
def only_the_budget_caps():
    """No usage window cap, so a refusal can only come from the budget. The month row is put back as found."""
    with session_scope(lease_checked=False) as s:
        s.execute(delete(AppSetting).where(AppSetting.section == "usage"))
        before = s.get(BudgetMonth, budget._month())
        kept = None if before is None else (before.limit_usd, before.spent_usd, before.reserved_usd)
    yield
    with session_scope(lease_checked=False) as s:
        row = s.get(BudgetMonth, budget._month())
        if kept is None and row is not None:
            s.delete(row)
        elif row is not None:
            row.limit_usd, row.spent_usd, row.reserved_usd = kept


@pytest.fixture
def project(s, project):
    """The conftest project, committed: reserve and settle write it from sessions of their own."""
    s.commit()
    return project


def set_month(limit: float, spent: float = 0.0, reserved: float = 0.0) -> None:
    with session_scope(lease_checked=False) as s:
        row = s.get(BudgetMonth, budget._month())
        if row is None:
            row = BudgetMonth(month=budget._month())
            s.add(row)
        row.limit_usd, row.spent_usd, row.reserved_usd = limit, spent, reserved


def month_held() -> float:
    with session_scope(lease_checked=False) as s:
        return s.get(BudgetMonth, budget._month()).reserved_usd


def project_held(project_id: int) -> float:
    with session_scope(lease_checked=False) as s:
        return s.get(Project, project_id).reserved_usd


def make_task(project_id: int, budget_usd: float, spent_usd: float = 0.0) -> int:
    with session_scope() as s:
        t = Task(project_id=project_id, key=f"cap-{budget_usd}-{spent_usd}", title="t", owner="research", objective="o",
                 acceptance_criteria=["c"], status="READY", max_iterations=1, budget_usd=budget_usd,
                 spent_usd=spent_usd)
        s.add(t)
        s.flush()
        return t.id


# ------------------------------------------------------------------ the monthly cap

@pytest.mark.parametrize("limit, spent, reserved, purpose, allowed", [
    (3.0, 0.0, 0.0, "work", True),
    (3.0, 1.5, 0.0, "work", True),     # spent + amount is exactly the limit
    (3.0, 1.5, 0.0, "dev", False),
    (3.0, 0.0, 1.5, "work", True),     # a reservation counts like money spent
    (3.0, 0.0, 1.6, "work", False),
    (3.0, 2.9, 0.0, "decision", False),
    (3.0, 1.5, 1.5, "decision", False),
])
def test_a_call_is_held_only_while_spent_plus_reserved_plus_its_own_fits_the_month(
        limit, spent, reserved, purpose, allowed):
    set_month(limit, spent, reserved)

    if allowed:
        held = budget.reserve(None, None, purpose)
        assert month_held() == reserved + held.amount
    else:
        with pytest.raises(BudgetExceeded, match="monthly budget"):
            budget.reserve(None, None, purpose)
        assert month_held() == reserved, "a refusal holds nothing"


def test_a_monthly_refusal_leaves_the_project_and_its_task_unheld(project):
    set_month(1.0)
    task_id = make_task(project.id, budget_usd=10)

    held = budget.reserve(project.id, task_id, "decision")
    with pytest.raises(BudgetExceeded, match="monthly budget"):
        budget.reserve(project.id, task_id, "decision")

    assert project_held(project.id) == held.amount and month_held() == held.amount


def test_settling_frees_the_hold_and_the_spent_money_still_counts_against_the_month():
    set_month(2.0)
    held = budget.reserve(None, None, "work")
    with pytest.raises(BudgetExceeded):
        budget.reserve(None, None, "decision")

    budget.settle(None, None, held, 1.0)

    assert month_held() == 0.0
    budget.reserve(None, None, "decision")
    with pytest.raises(BudgetExceeded):
        budget.reserve(None, None, "work")


# ------------------------------------------------------------------ the project cap

def test_a_project_cannot_hold_more_than_its_budget(project):
    set_month(300.0)
    with session_scope() as s:
        s.get(Project, project.id).budget_usd = 2.0

    first = budget.reserve(project.id, None, "work")
    with pytest.raises(BudgetExceeded, match="project budget"):
        budget.reserve(project.id, None, "work")

    assert project_held(project.id) == first.amount and month_held() == first.amount


# ------------------------------------------------------------------ development's share of the project

@pytest.fixture(autouse=True)
def _no_open_development_left():
    """A pending development request outlives the test in the shared database and would reach other tests' inboxes."""
    yield
    from justshipit.db.models import AgentRequest, Job
    with session_scope(lease_checked=False) as s:
        ids = select(AgentRequest.id).where(AgentRequest.title == _DEV_TITLE)
        s.execute(delete(Job).where(Job.kind == "write_spec", Job.payload["request_id"].as_integer().in_(ids)))
        s.execute(delete(AgentRequest).where(AgentRequest.title == _DEV_TITLE))


_DEV_TITLE = "개발 제안: 예산 몫 시험"


def _dev_request(project_id: int, status: str = "PENDING", writing: bool = True) -> int:
    """A development request; an approved one has its spec being written, as approving it queues, until `writing`
    says the spec was written."""
    from justshipit.db.models import AgentRequest
    from justshipit.services import queue
    with session_scope() as s:
        r = AgentRequest(project_id=project_id, from_agent="product", type="DEVELOPMENT", title=_DEV_TITLE,
                         summary="s", rationale="r", status=status, policy_mode="approve")
        s.add(r)
        s.flush()
        if status == "APPROVED" and writing:
            queue.enqueue(s, "write_spec", {"project_id": project_id, "request_id": r.id},
                          idempotency_key=f"write_spec:{r.id}")
        return r.id


def test_research_cannot_spend_the_share_an_open_development_needs(project):
    """견적봇 spent 37.97 of its 40 on research after the owner approved development at 8.77, and the dev job failed
    on its first call. While a development request is open, every other call leaves dev_need_usd free."""
    set_month(300.0)
    need = budget.dev_need_usd()
    with session_scope() as s:
        p = s.get(Project, project.id)
        p.budget_usd, p.spent_usd = need + 2.0, 0.0

    budget.reserve(project.id, None, "work")  # 1.5 of the 2.0 nothing else needs
    _dev_request(project.id, "APPROVED")
    with pytest.raises(BudgetExceeded, match="kept for development") as refused:
        budget.reserve(project.id, None, "decision")
    assert refused.value.scope == "project"
    held = budget.reserve(project.id, None, "dev", dev=True)
    assert held.amount == 4.0, "development itself spends the share"


@pytest.mark.parametrize("status, delivered, kept", [
    ("PENDING", False, True), ("APPROVED", False, True), ("REJECTED", False, False), ("DEFERRED", False, False),
    ("APPROVED", True, False),
])
def test_the_share_is_kept_while_development_is_wanted_and_nothing_is_delivered(project, status, delivered, kept):
    from justshipit.db.models import DevJob, Spec
    _dev_request(project.id, status, writing=not delivered)
    with session_scope() as s:
        if delivered:
            spec = Spec(spec_key=f"HOLD-{project.id}", project_id=project.id, title="t", registry_path="specs/h",
                        handoff={})
            s.add(spec)
            s.flush()
            s.add(DevJob(spec_id=spec.id, status="DELIVERED"))
        s.flush()
        assert budget.dev_hold(s, project.id) == (budget.dev_need_usd() if kept else 0.0)


def test_a_project_with_no_development_request_keeps_nothing_back(project):
    with session_scope() as s:
        assert budget.dev_hold(s, project.id) == 0.0


def test_the_shortfall_names_what_development_lacks(project):
    need = budget.dev_need_usd()
    with session_scope() as s:
        p = s.get(Project, project.id)
        p.budget_usd, p.spent_usd, p.reserved_usd = 40.0, 37.97, 0.0
        s.flush()
        assert budget.dev_shortfall(s, project.id) == {"need_usd": need, "left_usd": 2.03,
                                                      "short_usd": round(need - 2.03, 2), "budget_usd": 40.0,
                                                      "spent_usd": 37.97}
        p.budget_usd = 100.0
        s.flush()
        assert budget.dev_shortfall(s, project.id) is None


def test_a_development_card_warns_before_approval_when_the_budget_cannot_cover_it(project):
    from justshipit.db.models import AgentRequest
    from justshipit.services import requests
    rid = _dev_request(project.id)
    with session_scope() as s:
        p = s.get(Project, project.id)
        p.budget_usd, p.spent_usd = 40.0, 37.97
        s.flush()
        assert requests.read_model(s, s.get(AgentRequest, rid), "en", None)["dev_budget"]["left_usd"] == 2.03
        p.budget_usd = 100.0
        s.flush()
        assert requests.read_model(s, s.get(AgentRequest, rid), "en", None)["dev_budget"] is None


# ------------------------------------------------------------------ the task cap

@pytest.mark.parametrize("task_budget, task_spent, purpose, allowed", [
    (1.0, 0.0, "work", True),          # 1.5 is exactly one and a half times 1.0
    (1.0, 0.5, "plan", True),          # 0.5 + 1.0 reaches the cap without passing it
    (1.0, 0.5, "work", False),
    (1.0, 1.5, "decision", False),     # a task already at its cap holds nothing more
    (2.0, 1.0, "work", True),
    (0.5, 0.0, "plan", False),
])
def test_a_task_may_spend_up_to_one_and_a_half_times_its_budget(project, task_budget, task_spent, purpose, allowed):
    set_month(300.0)
    task_id = make_task(project.id, task_budget, task_spent)

    if allowed:
        held = budget.reserve(project.id, task_id, purpose)
        assert month_held() == held.amount and project_held(project.id) == held.amount
    else:
        with pytest.raises(BudgetExceeded, match="task budget"):
            budget.reserve(project.id, task_id, purpose)
        assert month_held() == 0.0 and project_held(project.id) == 0.0, "a refusal holds nothing"


def test_a_tasks_settled_spend_closes_its_cap(project):
    set_month(300.0)
    task_id = make_task(project.id, 1.0)
    held = budget.reserve(project.id, task_id, "work")

    budget.settle(project.id, task_id, held, 1.5)

    with pytest.raises(BudgetExceeded, match="task budget"):
        budget.reserve(project.id, task_id, "decision")
    with session_scope() as s:
        assert s.scalar(select(Task.spent_usd).where(Task.id == task_id)) == 1.5


# ------------------------------------------------------------------ racing at the boundary

def race(callers: int, project_id: int | None = None, purpose: str = "work") -> tuple[int, int]:
    """`callers` threads reserve at once. Returns how many were held and how many were refused."""
    gate = threading.Barrier(callers)
    results: list[str] = []

    def call():
        gate.wait()
        try:
            budget.reserve(project_id, None, purpose)
            results.append("held")
        except BudgetExceeded:
            results.append("refused")

    threads = [threading.Thread(target=call) for _ in range(callers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(results) == callers, "a caller never came back"
    return results.count("held"), results.count("refused")


def test_two_callers_at_the_boundary_get_exactly_one_hold(project):
    set_month(1.5)

    assert race(2, project.id) == (1, 1)

    assert month_held() == 1.5 and project_held(project.id) == 1.5


@pytest.mark.parametrize("callers, limit, winners", [(8, 1.5, 1), (5, 3.0, 2), (6, 9.0, 6)])
def test_callers_racing_for_a_cap_hold_what_fits_and_no_more(project, callers, limit, winners):
    set_month(limit)

    assert race(callers, project.id) == (winners, callers - winners)

    assert month_held() == winners * 1.5 == project_held(project.id)


def test_a_race_after_some_spending_counts_the_spent_money(project):
    set_month(4.5, spent=1.5)

    assert race(4, project.id) == (2, 2)

    assert month_held() == 3.0


# ------------------------------------------------------------------ lock order

def test_settling_a_call_while_another_call_is_held_does_not_deadlock(project):
    """reserve locks the month row and then the project row. settle must take them in the same order: a run's four
    workers deadlocked when settle's ledger row (which references the project) went in before the month lock."""
    set_month(10.0)
    task_id = make_task(project.id, budget_usd=10)
    held = budget.reserve(project.id, task_id, "work")
    record = LLMCallRecord(call_id="lock-order", project_id=project.id, task_id=task_id, agent="research",
                           purpose="work", provider="claude", status="ok")
    errors: list[BaseException] = []

    def settle():
        try:
            budget.settle(project.id, task_id, held, 1.0, record)
        except BaseException as exc:  # noqa: BLE001 (the assertion below names it)
            errors.append(exc)

    with engine().connect() as other:  # a reserve that already holds the month row
        other.execute(text("SET LOCAL lock_timeout = '5s'"))
        other.execute(text("SELECT 1 FROM budget_months WHERE month = :m FOR UPDATE"), {"m": budget._month()})
        waiter = threading.Thread(target=settle)
        waiter.start()
        for _ in range(100):
            other.execute(text("SELECT pg_stat_clear_snapshot()"))  # the view is read once per transaction otherwise
            if other.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
                                 "AND datname = current_database() AND query LIKE '%budget_months%'")):
                break
            time.sleep(0.05)
        else:
            pytest.fail("settle never waited for the month row")
        try:
            other.execute(text("SELECT 1 FROM projects WHERE id = :p FOR UPDATE"), {"p": project.id})
            other.execute(text("SELECT 1 FROM tasks WHERE id = :t FOR UPDATE"), {"t": task_id})
        except DBAPIError as exc:
            pytest.fail(f"the reserve side could not lock the project or task after the month: {exc.orig}")
        finally:
            other.rollback()
        waiter.join(30)

    assert not waiter.is_alive(), "settle never finished"
    assert not errors, f"settle failed: {errors[0]!r}" if errors else ""
    with session_scope(lease_checked=False) as s:
        assert s.get(Project, project.id).spent_usd == 1.0
        assert s.scalar(select(LLMCallRecord.status).where(LLMCallRecord.call_id == "lock-order")) == "ok"
