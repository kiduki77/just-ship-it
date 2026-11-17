"""The crews read, `GET /crews` (docs/design/staffing-structure.md section 9, step 0.2c unit 7): who worked each task and
build of a project and why, from the crew, its turns, the ledger and the subject's own row. Calls and cost are the
ledger's sums, shadow checks on a line of their own, and a job with no crew says so instead of showing a team."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from justshipit import staffing
from justshipit.api.app import app
from justshipit.contracts.handoff import CodeReviewOut
from justshipit.contracts.work import ReviewOut, ReviewVerdict
from justshipit.db.models import DevJob, LLMCallRecord, Spec, Task
from justshipit.engineering import build_crew
from justshipit.i18n import msg
from justshipit.i18n.glossary import role
from justshipit.services import projects

ALONE = staffing.Limits(always_check=False, solo_max_criteria=1, audit_rate=0.2, audit_budget_usd=2.0, min_samples=20,
                        solo_max_unmet_rate=0.1, model_tolerance=0.05, explore_rate=0.1)


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


@pytest.fixture
def lang(monkeypatch):
    from justshipit.services import app_settings
    chosen = {"lang": "en"}
    monkeypatch.setattr(app_settings, "language", lambda: chosen["lang"])
    return chosen


@pytest.fixture
def project(s):
    return projects.create(s, "crews goal", name="Crews", budget_usd=10, start=False)


def crews(api, **params) -> list[dict]:
    res = api.get("/api/crews", params=params)
    assert res.status_code == 200, res.text
    return res.json()["crews"]


def one(api, kind: str, subject_id: int, **params) -> dict:
    (found,) = [c for c in crews(api, **params) if c["subject"]["kind"] == kind and c["subject"]["id"] == subject_id]
    return found


def task(s, project, *, owner="research", status="READY", criteria=("c",)) -> Task:
    t = Task(project_id=project.id, key=f"t-{uuid.uuid4().hex[:6]}", title="Competitor price list", owner=owner,
             objective="o", acceptance_criteria=list(criteria), status=status)
    s.add(t)
    s.flush()
    return t


def call(s, project, *, agent, purpose="work", task_id=None, dev_job_id=None, usd=0.4, ms=1000, status="ok",
         model="sonnet") -> str:
    call_id = uuid.uuid4().hex[:20]
    s.add(LLMCallRecord(call_id=call_id, project_id=project.id, task_id=task_id, dev_job_id=dev_job_id, agent=agent,
                        purpose=purpose, provider="claude", model=model, status=status, cost_usd=usd, duration_ms=ms))
    s.flush()
    return call_id


def authored(s, crew, call_id, *, who="research", iteration=1):
    staffing.record_turn(s, crew, staffing.AuthorTurn(
        role=who, call_id=call_id, iteration=iteration, summary="s", findings_markdown="f", assumptions=[], risks=[],
        open_questions=[], unmet_criteria=[], evidence_ids=[]))


def reviewed(s, crew, call_id, *, who="strategy", iteration=1, shadow=False):
    review = ReviewOut(verdict=ReviewVerdict.PASS, criteria_met=["c"], criteria_unmet=[], claim_checks=[],
                       counter_arguments=[], rework_instructions=None, contested=False)
    staffing.record_turn(s, crew, staffing.VerifierTurn(role=who, call_id=call_id, iteration=iteration, review=review,
                                                        independence="independent"), shadow=shadow)


def test_an_alone_task_shows_its_one_call_and_its_shadow_check_on_a_line_of_its_own(s, project, api, lang):
    t = task(s, project)
    crew = staffing.ensure(s, t.id, staffing.task_facts(s, t), ALONE)
    authored(s, crew, call(s, project, agent="research", task_id=t.id, usd=0.41, ms=130_000))
    reviewed(s, crew, call(s, project, agent="risk", purpose=staffing.AUDIT, task_id=t.id, usd=0.25), who="risk",
             shadow=True)
    t.status, t.review = "DONE", {"verdict": staffing.UNCHECKED, "reviewer": None, "outcome": "pass",
                                  "criteria_unmet": []}
    s.commit()

    row = one(api, "task", t.id, project_id=project.id)

    assert row["standing"] == "dissolved" and row["note"] is None
    assert (row["outcome"]["calls"], row["outcome"]["cost_usd"]) == (1, pytest.approx(0.41))
    assert row["outcome"]["audit"] == {"calls": 1, "cost_usd": pytest.approx(0.25)}
    assert row["outcome"]["busy_ms"] == 130_000
    assert [(m["role"], m["calls"]) for m in row["members"]] == [("research", 1)], "the audit is not a member's call"
    assert row["outcome"]["verdict"] == "unchecked"
    assert row["outcome"]["fixed_path_calls"] == 2, "the path before staffing would have added a review"
    assert row["team"]["shape"] == "solo" and row["team"]["idle"] == []
    assert [w["rule"] for w in row["team"]["why"]] == ["solo.small_leaf"]
    assert row["team"]["why"][0]["text"] == msg("staffing.why.solo.small_leaf", "en", criteria=1)


def test_a_checked_task_names_its_members_its_rework_and_its_reasons_in_the_owners_language(s, project, api, lang):
    lang["lang"] = "ko"
    t = task(s, project, owner="risk")
    crew = staffing.ensure(s, t.id, staffing.task_facts(s, t), ALONE)
    call(s, project, agent="risk", task_id=t.id, status="error", usd=0.0, ms=500)
    authored(s, crew, call(s, project, agent="risk", task_id=t.id, usd=0.5, ms=1000), who="risk", iteration=1)
    reviewed(s, crew, call(s, project, agent="strategy", task_id=t.id, usd=0.3, ms=2000, model="opus"), iteration=1)
    authored(s, crew, call(s, project, agent="risk", task_id=t.id, usd=0.5, ms=1000), who="risk", iteration=2)
    reviewed(s, crew, call(s, project, agent="strategy", task_id=t.id, usd=0.3, ms=2000, model="opus"), iteration=2)
    start = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
    t.status, t.started_at, t.finished_at = "DONE", start, start + timedelta(minutes=9)
    t.review = {"verdict": "pass", "reviewer": "strategy"}
    s.commit()

    row = one(api, "task", t.id, project_id=project.id)
    out = row["outcome"]

    assert (out["calls"], out["failed_calls"], out["cost_usd"]) == (5, 1, pytest.approx(1.6))
    assert (out["busy_ms"], out["wall_ms"]) == (6500, 9 * 60 * 1000)
    assert (out["verdict"], out["reworks"], out["fixed_path_calls"]) == ("pass", 1, 4)
    assert out["audit"] == {"calls": 0, "cost_usd": 0.0}
    assert {(m["role"], m["model"], m["calls"], m["failed_calls"]) for m in row["members"]} == {
        ("risk", "sonnet", 3, 1), ("strategy", "opus", 2, 0)}
    assert row["team"]["shape"] == "checked"
    assert [(x["duty"], x["role"]) for x in row["team"]["slots"]] == [("author", "risk"), ("verifier", "strategy")]
    (said,) = [w for w in row["team"]["why"] if w["rule"] == "check.reviewer_role"]
    assert said["facts"] == {"owner": "risk", "checker": "strategy"}
    assert role("risk", "ko") in said["text"] and role("strategy", "ko") in said["text"], said["text"]


def test_a_checker_added_after_the_placement_shows_with_its_reason_and_as_idle_until_it_works(s, project, api, lang):
    t = task(s, project)
    crew = staffing.ensure(s, t.id, staffing.task_facts(s, t), ALONE)
    authored(s, crew, call(s, project, agent="research", task_id=t.id))
    staffing.amend(s, crew, staffing.Slot(staffing.Duty.VERIFIER, "risk", None),
                   staffing.Reason("check.owner_card", {"requests": 1}))
    t.status = "REVIEW"
    s.commit()

    row = one(api, "task", t.id, project_id=project.id)

    assert row["standing"] == "working"
    assert row["team"]["shape"] == "checked"
    assert [w["rule"] for w in row["team"]["why"]] == ["solo.small_leaf", "check.owner_card"]
    assert row["team"]["why"][0]["at"] is None and row["team"]["why"][1]["at"]
    assert [x["role"] for x in row["team"]["idle"]] == ["risk"], "the checker has made no call yet"


def test_a_build_reads_the_way_a_task_does(s, project, api, lang):
    spec = Spec(spec_key=f"CREW-{uuid.uuid4().hex[:6]}", project_id=project.id, title="Price page",
                registry_path="registry/specs/x.md")
    s.add(spec)
    s.flush()
    job = DevJob(spec_id=spec.id, status="DELIVERED", result={"acceptance": {"accepted": True}})
    s.add(job)
    s.flush()
    crew = build_crew.crew(s, job.id)
    assembled = crew.assembled_at
    job.steps = [{"at": (assembled + timedelta(minutes=41)).isoformat(), "status": "DELIVERED"}]
    call(s, project, agent="engineer", dev_job_id=job.id, usd=2.0)
    call(s, project, agent="engineer", dev_job_id=job.id, usd=1.0)
    first = call(s, project, agent="eng_reviewer", dev_job_id=job.id, usd=0.8)
    again = call(s, project, agent="eng_reviewer", dev_job_id=job.id, usd=0.8)
    call(s, project, agent="product", purpose="acceptance", dev_job_id=job.id, usd=1.2)
    for n, call_id in enumerate((first, again)):
        build_crew.keep(s, job.id, staffing.BuildReviewTurn(
            role="eng_reviewer", call_id=call_id, round=n,
            review=CodeReviewOut(verdict="pass", summary="ok", spec_coverage=[], findings=[])))
    s.commit()

    row = one(api, "dev_job", job.id, project_id=project.id)
    out = row["outcome"]

    assert row["subject"]["key"] == spec.spec_key and row["standing"] == "dissolved"
    assert (out["calls"], out["cost_usd"]) == (5, pytest.approx(5.8))
    assert (out["verdict"], out["reworks"], out["wall_ms"]) == ("accepted", 1, 41 * 60 * 1000)
    assert out["fixed_path_calls"] is None
    assert [m["role"] for m in row["members"]] == ["engineer", "eng_reviewer", "product"]
    assert row["team"]["shape"] == "chain" and row["team"]["idle"] == []
    assert [w["rule"] for w in row["team"]["why"]] == ["fixed.build"]


def test_jobs_with_no_crew_say_so_and_leave_the_rest_of_the_list_standing(s, project, api, lang):
    staffed = task(s, project)
    staffing.ensure(s, staffed.id, staffing.task_facts(s, staffed), ALONE)
    cancelled = task(s, project, status="CANCELLED")
    waiting = task(s, project)
    spec = Spec(spec_key=f"CREW-{uuid.uuid4().hex[:6]}", project_id=project.id, title="Refused build",
                registry_path="registry/specs/y.md")
    s.add(spec)
    s.flush()
    refused = DevJob(spec_id=spec.id, status="FAILED")
    s.add(refused)
    s.commit()

    rows = {(c["subject"]["kind"], c["subject"]["id"]): c for c in crews(api, project_id=project.id)}

    assert rows[("task", staffed.id)]["team"] is not None
    for key, standing, note in ((("task", cancelled.id), "cancelled_before_start", "staffing.crew.cancelled_before_start"),
                                (("task", waiting.id), "waiting", "staffing.crew.waiting"),
                                (("dev_job", refused.id), "no_record", "staffing.crew.no_record")):
        row = rows[key]
        assert (row["standing"], row["team"], row["note"]) == (standing, None, msg(note, "en")), key
        assert row["members"] == [] and row["outcome"]["calls"] == 0 and row["outcome"]["fixed_path_calls"] is None
    assert list(rows) == [("task", staffed.id), ("task", cancelled.id), ("task", waiting.id),
                          ("dev_job", refused.id)], "a project's tasks come before its builds"


def test_the_list_keeps_to_one_project_and_to_the_state_asked_for(s, project, api, lang):
    other = projects.create(s, "other goal", name="Other", budget_usd=10, start=False)
    mine, theirs = task(s, project), task(s, other)
    ended = task(s, project, status="CANCELLED")
    s.commit()

    assert {c["subject"]["id"] for c in crews(api, project_id=project.id)} == {mine.id, ended.id}
    assert [c["subject"]["id"] for c in crews(api, project_id=project.id, state="waiting")] == [mine.id]
    assert theirs.id in {c["subject"]["id"] for c in crews(api, project_id=other.id)}
    assert api.get("/api/crews", params={"state": "busy"}).status_code == 422
