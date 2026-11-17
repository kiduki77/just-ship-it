"""`rerun_delay_s` is the longest stretch a handler spends between two lease checks, so every stretch must be
one of the bounded ones: a model call, a test run, one analysis container run, one download chunk, one slot
wait tick. Writes after a lost lease never land, except the budget settlement and the call record."""
import threading

import pytest
from justshipit.analysis import runner as analysis
from justshipit.contracts.analysis import AnalysisAction, AnalysisStepOut
from justshipit.db.models import Worker
from justshipit.db.session import session_scope
from justshipit.runtime import slots
from justshipit.sandbox import docker
from justshipit.services import budget, queue, registry
from justshipit.settings import company_config


@pytest.fixture
def lost_lease():
    lease = queue.Lease(990001, "t")
    token = queue.current_lease.set(lease)
    try:
        yield lease
    finally:
        queue.current_lease.reset(token)


def test_the_slot_wait_stops_once_the_lease_is_lost(lost_lease, monkeypatch):
    monkeypatch.setattr(slots, "_limit", lambda provider: 1)
    monkeypatch.setattr(slots.time, "sleep", lambda _s: None)
    taken, release = threading.Event(), threading.Event()

    def hold():
        with slots.provider_slot("lease-bounds-wait"):
            taken.set()
            release.wait(30)
    threading.Thread(target=hold, daemon=True).start()
    try:
        assert taken.wait(10)
        lost_lease.lost.set()
        with pytest.raises(queue.LeaseLost), slots.provider_slot("lease-bounds-wait"):
            pass
    finally:
        release.set()


def test_a_slot_taken_after_the_lease_was_lost_is_given_back_at_once(lost_lease, monkeypatch):
    monkeypatch.setattr(slots, "_limit", lambda provider: 1)
    lost_lease.lost.set()
    with pytest.raises(queue.LeaseLost), slots.provider_slot("lease-bounds-back"):
        pass
    lost_lease.lost.clear()
    entered = threading.Event()

    def take():
        with slots.provider_slot("lease-bounds-back"):
            entered.set()
    threading.Thread(target=take, daemon=True).start()
    assert entered.wait(5), "the slot taken under the lost lease was kept"


def test_analysis_stops_between_a_model_call_and_a_container_run_once_the_lease_is_lost(lost_lease, monkeypatch):
    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    monkeypatch.setattr(docker, "image_present", lambda _image: True)

    def run_agent(owner, purpose, prompt, model, **kw):  # the heartbeat flags the loss during the call
        lost_lease.lost.set()
        return AnalysisStepOut(reasoning="r", action=AnalysisAction.RUN, inputs=[], script="print(1)")

    executed = []
    monkeypatch.setattr(analysis, "run_agent", run_agent)
    monkeypatch.setattr(analysis, "execute", lambda *a: executed.append(a) or (0, False, "", "", "{}", []))
    with pytest.raises(queue.LeaseLost):
        analysis.run_task("analyst", "p", project_id=990001, task_id=990001, project_slug="lease-bounds", attempt=1, lang="en")
    assert executed == []


def test_the_rerun_wait_covers_one_analysis_container_run(monkeypatch):
    monkeypatch.setitem(company_config(), "analysis", {**company_config().get("analysis", {}), "run_timeout_s": 100_000})
    assert queue.rerun_delay_s() >= 100_000


def test_a_session_opened_under_a_lost_lease_does_not_commit(lost_lease):
    lost_lease.lost.set()
    with pytest.raises(queue.LeaseLost), session_scope() as s:
        s.add(Worker(id="lease-bounds-lost"))
    lost_lease.lost.clear()
    with session_scope() as s:
        assert s.get(Worker, "lease-bounds-lost") is None


def test_the_registry_does_not_commit_under_a_lost_lease(lost_lease):
    lost_lease.lost.set()
    with pytest.raises(queue.LeaseLost):
        registry.write_files({"lease-bounds/lost.md": "x"}, "lost lease")


def test_the_budget_settles_even_after_the_lease_is_lost(lost_lease):
    reserved = budget.reserve(None, None, "dev")
    lost_lease.lost.set()
    budget.settle(None, None, reserved, 0.0)
    lost_lease.lost.clear()
    with session_scope() as s:
        from justshipit.db.models import BudgetMonth
        assert s.get(BudgetMonth, budget._month()).reserved_usd == 0.0


def test_the_call_record_lands_even_after_the_lease_is_lost(lost_lease, monkeypatch):
    from justshipit.agents import executor
    from justshipit.db.models import LLMCallRecord
    from justshipit.runtime import LLMResult
    from pydantic import BaseModel
    from sqlalchemy import select

    class Out(BaseModel):
        ok: bool

    seen: list[str] = []

    def run_with_retry(call):
        lost_lease.lost.set()
        seen.append(call.call_id)
        return LLMResult(call_id=call.call_id, provider=call.provider, model="m", output={"ok": True}, input_tokens=1,
                         output_tokens=1, cached_tokens=0, cost_usd=0.0, duration_ms=1, session_id=None)

    monkeypatch.setattr(executor, "run_with_retry", run_with_retry)
    assert executor.run_agent("project_manager", "work", "p", Out, project_id=None).ok
    lost_lease.lost.clear()
    with session_scope() as s:
        assert s.scalar(select(LLMCallRecord.status).where(LLMCallRecord.call_id == seen[0])) == "ok"
