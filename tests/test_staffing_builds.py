"""A build is a crew too (docs/design/staffing-structure.md, step 0.2c unit 6). It is staffed by the fixed chain when it
starts working, and each call that says something DevJob.steps does not keep is a turn of that crew, keyed by its ledger
call: the engineer's passes, the code reviewer's whole findings list, and the product role's acceptance after every
delivery. Agents are stubbed; each stub call gets a ledger call id of its own, as a real one does."""
from __future__ import annotations

import itertools
import uuid

import pytest
from justshipit.agents.independence import Served
from justshipit.contracts.handoff import (
    AcceptanceOut, CodeFindingOut, CodeReviewOut, CoreFlowOut, DevStepOut, IntakeOut,
)
from justshipit.db.models import Crew, CrewTurn, DevJob
from sqlalchemy import select
from test_engineering import _approved_job, _product_repo


@pytest.fixture(autouse=True)
def _korean_owner(monkeypatch):
    from justshipit.services import app_settings
    monkeypatch.setattr(app_settings, "language", lambda: "ko")


def _finding(n: int, severity: str) -> CodeFindingOut:
    return CodeFindingOut(severity=severity, file=f"f{n}.py", location=f"l{n}", issue=f"issue {n}", suggestion=f"fix {n}")


class Ledger:
    """What stands in for `run_agent` and the ledger behind it: it answers each role in turn and gives each call an id.
    `reviews` are the code reviewer's answers in order (a pass once they run out)."""

    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:6]
        self.count = itertools.count(1)
        self.last: str | None = None
        self.calls: list[tuple[str, str]] = []
        self.reviews: list[CodeReviewOut] = []
        self.ids = True

    def run(self, agent_id, purpose, prompt, model, **kw):
        n = next(self.count)
        self.last = f"{self.tag}-{n}" if self.ids else None
        self.calls.append((agent_id, self.last))
        if model is IntakeOut:
            return IntakeOut(clear_enough=True, blocking_questions=[], assumptions_safe_to_make=[],
                             implementation_outline="outline")
        if model is CodeReviewOut:
            return self.reviews.pop(0) if self.reviews else CodeReviewOut(
                verdict="pass", summary=f"review {n}", spec_coverage=["REQ-1: built"], findings=[])
        if model is AcceptanceOut:
            return AcceptanceOut(accepted=True, criteria_results=[], notes=f"accepted {n}", core_flow=CoreFlowOut(
                realistic=True, quiet_day=True, evidence="fixtures/core-flow/ has the owner's columns"))
        return DevStepOut(status="done", summary=f"{agent_id}/{purpose} {n}", files_changed=[], tests_run=[],
                          decisions=[f"decision {n}"], blockers=[])


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    """A build's run with its ground stubbed (no container, no git) and nothing delivered."""
    from justshipit.engineering import worker
    from justshipit.sandbox import docker
    from justshipit.settings import company_config

    ledger = Ledger()
    cfg = company_config()
    monkeypatch.setattr(worker, "company_config", lambda: {**cfg, "engineering": {**cfg["engineering"],
                                                                                   "max_fix_iterations": 1}})
    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    monkeypatch.setattr(docker, "image_present", lambda image: True)
    monkeypatch.setattr(worker, "host_git_refusal", lambda lang: None)
    monkeypatch.setattr(worker, "run_agent", ledger.run)
    monkeypatch.setattr(worker, "last_call_id", lambda: ledger.last)
    monkeypatch.setattr(worker, "last_served", lambda: Served("claude", "opus"))
    monkeypatch.setattr(worker, "prepare_worktree", lambda spec, h, attempt, fix=None: (tmp_path, tmp_path, "ai/t", None))
    monkeypatch.setattr(worker, "_write_spec", lambda wt, h, given: None)
    monkeypatch.setattr(worker, "run_tests", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(worker, "_git", lambda *a, **kw: "")
    monkeypatch.setattr(worker, "_deliver", lambda *a, **kw: None)
    return ledger


def _queued(s, project) -> DevJob:
    """A build waiting to start, on a spec with a real registry commit (the walkthrough check reads it)."""
    return _approved_job(s, project, f"sb-{project.id}")[0]


def _crews(s, job_id: int) -> list[Crew]:
    s.expire_all()
    return list(s.scalars(select(Crew).where(Crew.subject_kind == "dev_job", Crew.subject_id == job_id)))


def _turns(s, job_id: int) -> list[CrewTurn]:
    s.expire_all()
    (crew,) = _crews(s, job_id)
    return list(s.scalars(select(CrewTurn).where(CrewTurn.crew_id == crew.id).order_by(CrewTurn.id)))


def _shape(turn: CrewTurn) -> tuple:
    """(role, which pass, round, iteration): the pass is the engineer's stage, or what the other two roles produced."""
    rec = turn.record
    return (turn.role, rec.get("stage") or ("acceptance" if "acceptance" in rec else "review"), turn.round,
            turn.iteration)


def test_a_build_run_leaves_one_crew_with_the_fixed_chain_and_a_turn_for_each_call(s, project, ledger):
    from justshipit.engineering import worker

    job = _queued(s, project)

    worker.run_dev_job(job.id)

    (crew,) = _crews(s, job.id)
    assert (crew.family, crew.facts["owner"], crew.facts["origin"]) == ("build", "engineer", "build")
    assert [(x["duty"], x["role"]) for x in crew.slots] == [
        ("author", "engineer"), ("verifier", "eng_reviewer"), ("verifier", "product")]
    assert crew.why == [{"rule": "fixed.build", "facts": {}}]
    turns = _turns(s, job.id)
    assert [_shape(t) for t in turns] == [
        ("engineer", "plan", 0, 0), ("engineer", "build", 0, 0), ("engineer", "review", 0, 0),
        ("eng_reviewer", "review", 0, 0)]
    made = [call for _, call in ledger.calls]
    assert [t.call_id for t in turns] == made[1:], "every call but the intake, which the steps keep whole"
    assert turns[0].record["decisions"] and turns[0].record["summary"].startswith("engineer/dev ")


def test_both_engineer_calls_of_an_iteration_and_the_whole_findings_list_are_kept(s, project, ledger):
    from justshipit.engineering import worker

    found = [_finding(1, "blocking"), _finding(2, "minor")]
    ledger.reviews = [CodeReviewOut(verdict="fix_required", summary="not yet", spec_coverage=["REQ-1: partly"],
                                    findings=found)]
    job = _queued(s, project)

    worker.run_dev_job(job.id)

    turns = _turns(s, job.id)
    assert [_shape(t) for t in turns] == [
        ("engineer", "plan", 0, 0), ("engineer", "build", 0, 0),
        ("engineer", "review", 0, 0), ("eng_reviewer", "review", 0, 0), ("engineer", "fix", 0, 0),
        ("engineer", "review", 0, 1), ("eng_reviewer", "review", 0, 1)]
    assert len({t.call_id for t in turns}) == len(turns), "one turn per call, though iterations repeat their numbers"
    blocked = turns[3].record["review"]
    assert [f["issue"] for f in blocked["findings"]] == ["issue 1", "issue 2"], "the whole list, not a count"
    step = next(x for x in s.get(DevJob, job.id).steps if (x.get("data") or {}).get("verdict") == "fix_required")
    assert step["data"]["blocking"] == 1 and "findings" not in step["data"], "the steps still keep only the count"


def test_a_step_recorded_twice_with_the_same_call_leaves_one_turn(s, project, ledger):
    from justshipit.contracts.handoff import DevJobStatus
    from justshipit.engineering import worker
    from justshipit.staffing import BuildTurn

    job = _queued(s, project)
    turn = BuildTurn(role="engineer", call_id="same-call", stage="fix", summary="s", decisions=[])

    for _ in range(2):
        worker._step(job.id, DevJobStatus.INTAKE, "again", say=worker._say("build.step.intake_start"), turns=(turn,))

    assert [t.call_id for t in _turns(s, job.id)] == ["same-call"]
    assert len(_crews(s, job.id)) == 1


def test_a_run_the_queue_takes_back_adds_its_turns_and_drops_none(s, project, ledger):
    """The loop starts from iteration 0 again on a resume, so the numbers repeat; the call ids keep the turns apart."""
    from justshipit.engineering import worker

    job = _queued(s, project)
    worker.run_dev_job(job.id)
    first = [(t.id, t.call_id, t.record) for t in _turns(s, job.id)]
    s.get(DevJob, job.id).status = "REVIEWING"
    s.commit()

    worker.run_dev_job(job.id)

    after = _turns(s, job.id)
    assert [(t.id, t.call_id, t.record) for t in after[:len(first)]] == first, "nothing earlier moved or changed"
    assert [_shape(t) for t in after[len(first):]] == [("engineer", "review", 0, 0), ("eng_reviewer", "review", 0, 0)]
    assert len({t.call_id for t in after}) == len(after)
    assert len(_crews(s, job.id)) == 1


def test_a_call_with_no_ledger_row_has_no_key_so_it_leaves_no_turn_and_the_build_runs_on(s, project, ledger):
    from justshipit.engineering import worker

    ledger.ids = False
    job = _queued(s, project)

    worker.run_dev_job(job.id)

    s.expire_all()
    assert s.get(DevJob, job.id).status != "FAILED"
    assert len(_crews(s, job.id)) == 1 and _turns(s, job.id) == []


def test_a_build_refused_before_it_works_has_no_crew(s, project, ledger):
    from justshipit.db.models import Spec
    from justshipit.engineering import worker

    job = _queued(s, project)
    spec = s.get(Spec, job.spec_id)
    spec.handoff = {**spec.handoff, "approval": {**spec.handoff["approval"], "spec_approved": False}}
    s.commit()

    worker.run_dev_job(job.id)

    s.expire_all()
    assert s.get(DevJob, job.id).status == "FAILED" and _crews(s, job.id) == []


def test_a_fix_round_adds_its_turns_under_its_round_and_changes_none_before_it(s, project, ledger):
    from justshipit.engineering import revise, worker

    job = _queued(s, project)
    worker.run_dev_job(job.id)
    first = [(t.id, t.call_id, t.record) for t in _turns(s, job.id)]
    done = s.get(DevJob, job.id)
    done.status = "DELIVERED"
    done.result = {"head": "abc", "fixes": [{"n": 1, "state": revise.State.QUEUED.value, "note": "change it",
                                             "request_id": None, "from": "abc", "at": "t"}]}
    s.commit()

    revise.run(job.id, 1)

    after = _turns(s, job.id)
    assert [(t.id, t.call_id, t.record) for t in after[:len(first)]] == first
    assert [_shape(t) for t in after[len(first):]] == [
        ("engineer", "build", 1, 0), ("engineer", "review", 1, 0), ("eng_reviewer", "review", 1, 0)]
    assert len(_crews(s, job.id)) == 1 and len({t.call_id for t in after}) == len(after)


# ---------------------------------------------------------------- acceptance, and fix rounds

def _delivered(s, project, name: str, monkeypatch, ledger) -> DevJob:
    """A delivered build whose acceptance check is on the queue (what `_deliver` leaves), with real git underneath."""
    from justshipit.engineering import worker

    repo = _product_repo(f"{name}-{project.id}")
    job, _, _ = _approved_job(s, project, repo.name)
    job.branch, job.status = "main", "DELIVERED"
    s.commit()
    monkeypatch.setattr(worker.registry, "read_at", lambda *a: "- criterion")
    monkeypatch.setattr(worker, "run_core_flow", lambda export: {"ran": True, "ok": True, "log": "2 files: ok"})
    monkeypatch.setattr(worker, "run_agent", ledger.run)
    monkeypatch.setattr(worker, "last_call_id", lambda: ledger.last)
    return job


def test_the_acceptance_after_every_delivery_is_a_turn_of_its_own_round(s, project, ledger, monkeypatch):
    """A fix round's delivery is judged again, and `result["acceptance"]` is replaced by the new verdict, so the first
    verdict survives only as a turn."""
    from justshipit.engineering import worker

    job = _delivered(s, project, "acc-turns", monkeypatch, ledger)
    worker.acceptance(job.id)
    s.expire_all()
    assert s.get(DevJob, job.id).result["acceptance"]["notes"] == "accepted 1"
    j = s.get(DevJob, job.id)
    j.result = {**j.result, "fixes": [{"n": 1, "state": "delivered", "note": "n", "request_id": None, "from": "x",
                                       "at": "t"}]}
    j.status = "DELIVERED"
    s.commit()

    worker.acceptance(job.id)

    s.expire_all()
    assert s.get(DevJob, job.id).result["acceptance"]["notes"] == "accepted 2", "the result keeps only the latest"
    turns = _turns(s, job.id)
    assert [(_shape(t), t.record["acceptance"]["notes"]) for t in turns] == [
        (("product", "acceptance", 0, 0), "accepted 1"), (("product", "acceptance", 1, 0), "accepted 2")]
    assert turns[0].duty == "verifier"
