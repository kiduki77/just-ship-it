"""The placement statistics and version 2 in the running system (docs/design/staffing-structure.md section 7, unit
13): the statistics are counted from seeded crews, turns and ledger rows and match numbers worked out by hand, a stored
crew is never placed again, a new crew records the rules version and the model history chose, the author runs on that
model, and `GET /crews` still reads a crew version 1 placed. Each test seeds its own family, since the test database
keeps every test's rows."""
from __future__ import annotations

import uuid
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from justshipit import staffing
from justshipit.agents import executor
from justshipit.api.app import app
from justshipit.contracts.work import ReviewOut, ReviewVerdict
from justshipit.db.models import Crew, Job, LLMCallRecord, Task
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.orchestration import company_graph
from justshipit.runtime.base import LLMResult
from justshipit.services import projects
from justshipit.settings import staffing_config
from sqlalchemy import select

LIMITS = staffing.Limits(always_check=False, solo_max_criteria=1, audit_rate=0.2, audit_budget_usd=2.0, min_samples=20,
                         solo_max_unmet_rate=0.1, model_tolerance=0.05, explore_rate=0.1,
                         model_ladder=("haiku", "sonnet", "opus"))
ONE = replace(LIMITS, min_samples=1)   # one judged task is enough, so a test seeds one


@pytest.fixture
def family() -> str:
    return f"f-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def project(s):
    return projects.create(s, "stats goal", name="Stats", budget_usd=10, start=False)


def task(s, project, family, *, owner="research", criteria=("c",)) -> Task:
    t = Task(project_id=project.id, key=f"t-{uuid.uuid4().hex[:6]}", title="t", owner=owner, objective="o",
             acceptance_criteria=list(criteria), status="READY", family=family)
    s.add(t)
    s.flush()
    return t


def call(s, project, t, *, agent, model="claude-sonnet-4-5-20250929", purpose="work", status="ok") -> str:
    call_id = uuid.uuid4().hex[:20]
    s.add(LLMCallRecord(call_id=call_id, project_id=project.id, task_id=t.id, agent=agent, purpose=purpose,
                        provider="claude", model=model, status=status, cost_usd=0.4, duration_ms=1))
    s.flush()
    return call_id


def authored(s, crew, call_id, *, who="research", iteration=1):
    staffing.record_turn(s, crew, staffing.AuthorTurn(
        role=who, call_id=call_id, iteration=iteration, summary="s", findings_markdown="f", assumptions=[], risks=[],
        open_questions=[], unmet_criteria=[], evidence_ids=[]))


def checked(s, crew, call_id, *, unmet=(), who="risk", iteration=1, shadow=False):
    review = ReviewOut(verdict=ReviewVerdict.REWORK if unmet else ReviewVerdict.PASS, criteria_met=[],
                       criteria_unmet=list(unmet), claim_checks=[], counter_arguments=[], rework_instructions=None,
                       contested=False)
    staffing.record_turn(s, crew, staffing.VerifierTurn(role=who, call_id=call_id, iteration=iteration, review=review,
                                                        independence="independent"), shadow=shadow)


def judged(s, project, family, *, owner="research", model="claude-sonnet-4-5-20250929", shadow=False, unmet=()):
    """One finished task of `family` whose first pass ran on `model` and whose first check found `unmet`."""
    t = task(s, project, family, owner=owner)
    crew = staffing.ensure(s, t.id, staffing.task_facts(s, t), LIMITS)
    authored(s, crew, call(s, project, t, agent=owner, model=model), who=owner)
    purpose = staffing.AUDIT if shadow else "review"
    checked(s, crew, call(s, project, t, agent="risk", purpose=purpose), unmet=unmet, shadow=shadow)
    return t


# ------------------------------------------------------------------ the statistics query

def test_the_statistics_match_the_records_counted_by_hand(s, project, family):
    # Alone, a shadow check found an unmet criterion. One own call; the audit's call is not the task's.
    judged(s, project, family, shadow=True, unmet=["no prices"])
    # Alone, the shadow check found nothing.
    judged(s, project, family, shadow=True)
    # Checked: the first review sent it back, the second passed. A failed call is still a call.
    hard = task(s, project, family, criteria=("a", "b", "c"))
    crew = staffing.ensure(s, hard.id, staffing.task_facts(s, hard), LIMITS)
    call(s, project, hard, agent="research", status="error")
    authored(s, crew, call(s, project, hard, agent="research"), iteration=1)
    checked(s, crew, call(s, project, hard, agent="risk", purpose="review"), unmet=["b"], iteration=1)
    authored(s, crew, call(s, project, hard, agent="research"), iteration=2)
    checked(s, crew, call(s, project, hard, agent="risk", purpose="review"), iteration=2)
    # Checked on haiku, passed first time.
    judged(s, project, family, model="claude-haiku-4-5-20251001")
    # Another role on opus.
    judged(s, project, family, owner="strategy", model="claude-opus-4-1")
    # Never judged: authored and alone, no shadow check drawn. It counts nowhere.
    waiting = task(s, project, family)
    authored(s, staffing.ensure(s, waiting.id, staffing.task_facts(s, waiting), LIMITS),
             call(s, project, waiting, agent="research"))
    # Another family's task is not this family's.
    judged(s, project, f"{family}-x", shadow=True, unmet=["x"])
    s.flush()

    stats = staffing.placement_stats(s, LIMITS.model_ladder, family)

    T = staffing.Tally
    assert stats == (
        staffing.Stat(family, "research", "haiku", alone=T(0, 0), checked=T(1, 0), calls=2, reworks=0),
        staffing.Stat(family, "research", "sonnet", alone=T(2, 1), checked=T(1, 1), calls=1 + 1 + 5, reworks=1),
        staffing.Stat(family, "strategy", "opus", alone=T(0, 0), checked=T(1, 0), calls=2, reworks=0),
    )
    assert {x.family for x in staffing.placement_stats(s, LIMITS.model_ladder)} >= {family, f"{family}-x"}


def test_a_model_off_the_ladder_keeps_its_own_name(s, project, family):
    judged(s, project, family, model="m")

    (only,) = staffing.placement_stats(s, LIMITS.model_ladder, family)

    assert only.model == "m"


def test_the_history_carries_the_rosters_models_and_the_draw(s, project, family):
    judged(s, project, family)

    hist = staffing.recorded_history(s, LIMITS, family, 0.42)

    assert [(x.role, x.model) for x in hist.stats] == [("research", "sonnet")]
    assert hist.configured["research"] == "sonnet" and hist.configured["risk"] == "opus"
    assert hist.draw == 0.42


# ------------------------------------------------------------------ ensure and version 2

def test_an_empty_family_is_placed_as_version_one_and_records_version_two(s, project, family):
    t = task(s, project, family)

    crew = staffing.ensure(s, t.id, staffing.task_facts(s, t), LIMITS, draw=lambda: 0.0)

    assert crew.rules_version == staffing.RULES_VERSION == "2"
    assert [r["rule"] for r in crew.why] == ["solo.small_leaf"]
    assert crew.slots == [{"duty": "author", "role": "research", "model": None}]


def test_a_stored_crew_is_never_placed_again_whatever_the_statistics_say_now(s, project, family):
    first = task(s, project, family)
    crew = staffing.ensure(s, first.id, staffing.task_facts(s, first), ONE, draw=lambda: 0.0)
    judged(s, project, family, shadow=True, unmet=["missed"])   # the family's shadow checks now fail

    again = staffing.ensure(s, first.id, staffing.task_facts(s, first), ONE, draw=lambda: 0.0)
    later = task(s, project, family)
    new = staffing.ensure(s, later.id, staffing.task_facts(s, later), ONE, draw=lambda: 1.0)

    assert again.id == crew.id
    assert [r["rule"] for r in again.why] == ["solo.small_leaf"] and len(again.slots) == 1, "the decision made stays"
    assert [r["rule"] for r in new.why] == ["history.checked"], "only a new crew reads the statistics"
    assert [x["role"] for x in new.slots] == ["research", "risk"]
    assert new.why[0]["facts"]["samples"] == 1 and new.why[0]["facts"]["unmet"] == 1


def test_a_new_crew_drawn_to_explore_records_the_cheaper_model_on_its_author(s, project, family):
    judged(s, project, family)   # sonnet, the roster's model for research, has its one sample
    t = task(s, project, family)
    draws = iter([0.05])

    crew = staffing.ensure(s, t.id, staffing.task_facts(s, t), ONE, draw=lambda: next(draws))

    assert crew.slots == [{"duty": "author", "role": "research", "model": "haiku"}]
    assert crew.why[-1]["rule"] == "history.explore" and crew.why[-1]["facts"]["draw"] == 0.05


# ------------------------------------------------------------------ the author runs on the chosen model

@pytest.fixture
def served(monkeypatch):
    """Every model call answers at once; the models the calls asked for are kept."""
    asked: list[tuple[str, str | None]] = []
    evidence = {"claim": "c", "kind": "sourced_claim", "source_url": "https://example.org/a", "source_title": "t",
                "quote": "q", "published_at": "2025", "confidence": 0.9}
    body = {"summary": "s", "findings_markdown": "f [E1]", "evidence": [evidence], "assumptions": [], "risks": [],
            "open_questions": [], "unmet_criteria": [], "proposed_follow_up_tasks": [], "requests": [],
            "capability_gaps": [], "financial_model": None}

    def run(c):
        asked.append((c.agent, c.model))
        return LLMResult(call_id=c.call_id, provider=c.provider, model=c.model, output=body, input_tokens=1,
                         output_tokens=1, cached_tokens=0, cost_usd=0.1, duration_ms=1, session_id=None)
    monkeypatch.setattr(executor, "run_with_retry", run)
    monkeypatch.setattr(company_graph.evidence, "verify_sources", lambda ids: None)
    return asked


@pytest.mark.usefixtures("owner_asked", "own_jobs_settled")
def test_the_author_runs_on_the_model_its_crew_was_placed_with_and_an_explored_task_is_audited(
        s, project, family, served, monkeypatch):
    monkeypatch.setitem(staffing_config()["staffing"], "audit_rate", 1e-9)   # the sample would audit neither
    judged(s, project, family)
    explored, plain = task(s, project, family), task(s, project, family)
    staffing.ensure(s, explored.id, staffing.task_facts(s, explored), ONE, draw=lambda: 0.0)
    staffing.ensure(s, plain.id, staffing.task_facts(s, plain), ONE, draw=lambda: 1.0)
    ids = explored.id, plain.id
    s.commit()

    for tid in ids:
        company_graph.work({"project_id": project.id, "task_id": tid, "batch": [tid]})

    assert served == [("research", "haiku"), ("research", "sonnet")], "the plain one runs on the roster's model"
    with session_scope() as s2:
        queued = {j.payload["task_id"] for j in s2.scalars(select(Job).where(Job.kind == "audit_task"))}
        assert queued & set(ids) == {explored.id}, "a sample on the explored model counts only once a check judges it"
        assert all(s2.get(Task, tid).status == "DONE" for tid in ids)


# ------------------------------------------------------------------ the crews read

@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


@pytest.fixture
def english(monkeypatch):
    from justshipit.services import app_settings
    monkeypatch.setattr(app_settings, "language", lambda: "en")


def test_the_crews_read_shows_a_version_one_crew_as_it_was_and_a_version_two_one_with_its_statistics(
        s, project, family, api, english):
    old = task(s, project, family)
    s.add(Crew(subject_kind="task", subject_id=old.id, family=family,
               slots=[{"duty": "author", "role": "research", "model": None}],
               facts={**staffing.task_facts(s, old).__dict__, "kind": "task"},
               why=[{"rule": "solo.small_leaf", "facts": {"criteria": 1}}], amendments=[], rules_version="1"))
    for _ in range(2):
        judged(s, project, family, model="claude-haiku-4-5")
        judged(s, project, family)
    new = task(s, project, family)
    staffing.ensure(s, new.id, staffing.task_facts(s, new), replace(LIMITS, min_samples=2), draw=lambda: 1.0)
    s.commit()

    rows = {c["subject"]["id"]: c for c in api.get("/api/crews", params={"project_id": project.id}).json()["crews"]}

    was = rows[old.id]["team"]
    assert was["rules_version"] == "1"
    assert was["slots"] == [{"duty": "author", "role": "research", "model": None}]
    assert [(w["rule"], w["text"]) for w in was["why"]] == [
        ("solo.small_leaf", msg("staffing.why.solo.small_leaf", "en", criteria=1))]
    now = rows[new.id]["team"]
    assert now["rules_version"] == "2" and now["slots"][0]["model"] == "haiku"
    (said,) = [w for w in now["why"] if w["rule"] == "history.model"]
    assert said["facts"]["samples"] == 2 and said["facts"]["configured"] == "sonnet"
    assert said["text"] == msg("staffing.why.history.model", "en", **said["facts"])
