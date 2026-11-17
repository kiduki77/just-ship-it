"""Persona round 4 (Sam, a Shopify support digest): REQ-97 asked the owner to approve the merge of a build that never
ran. The setup failed on "python: not found", 35 of 35 criteria were unmet, and the card's front said "Decide whether to
approve" with Approve live; the failure sat under Details. The Engineering page said "Started building" for 44 minutes.
The after-merge decision recommended "Run it on my computer" while the drafted asks promised stores a private upload
link. The project read "Stage: Done" with the usable to-do open, and the second merge card's "Why" ran three unseen
lists together, one naming a state its own first thumbnail showed."""
from pathlib import Path

import pytest
from justshipit.contracts.handoff import (
    FIX_QUESTION_KEY,
    AcceptanceOut,
    CoreFlowOut,
    CriterionResultOut,
    UseOut,
)
from justshipit.contracts.requests import HumanDecision, HumanResponse
from justshipit.i18n import msg
from sqlalchemy import delete, select
from test_usable import USE, _delivered, _merged, _summary, _use_card
from ask_kit import by_project_manager

SETUP_FAILED = {"ran": True, "ok": False, "log": "[ci-setup failed]\nscripts/ci-setup.sh: 5: python: not found\n\n(exit 97)"}
FLOW_OK = {"ran": True, "ok": True, "log": "3 files: ok"}


@pytest.fixture(autouse=True)
def _leave_no_company_wide_to_do():
    """These cases build on a project that belongs to no business, so the to-dos they commit belong to none either, and
    a to-do that belongs to no business is on every business's Today list in the files that run after this one."""
    from justshipit.db.models import OwnerTask
    from justshipit.db.session import session_scope

    yield
    with session_scope() as s:
        s.execute(delete(OwnerTask).where(OwnerTask.business_id.is_(None)))


@pytest.fixture(autouse=True)
def _english_owner(monkeypatch):
    from justshipit.services import app_settings
    monkeypatch.setattr(app_settings, "language", lambda: "en")


@pytest.fixture
def check(monkeypatch):
    """The acceptance check with its ground stubbed: the registry's spec, and a core flow the case sets."""
    from justshipit.engineering import worker
    monkeypatch.setattr(worker.registry, "read_at", lambda *a: "- criteria")
    monkeypatch.setattr(worker, "_write_spec", lambda wt, h, given: None)

    def run(flow: dict, out: AcceptanceOut, seen: dict | None = None):
        monkeypatch.setattr(worker, "run_core_flow", lambda export: flow)

        def cpo(agent_id, purpose, prompt, model, **kw):
            if seen is not None:
                seen["prompt"] = prompt
            return out
        monkeypatch.setattr(worker, "run_agent", cpo)
    return run


def _never_ran(**kw) -> AcceptanceOut:
    """REQ-97's verdict: the code looked complete, nothing could be run, so no criterion could be met."""
    return AcceptanceOut(accepted=False, notes="I rejected this branch because the setup script failed.", use=USE,
                         core_flow=CoreFlowOut(realistic=True, quiet_day=True, evidence="fixtures/core-flow/ is there"),
                         criteria_results=[CriterionResultOut(criterion=f"Criterion {i}", met=False,
                                                              evidence="the run failed") for i in range(4)], **kw)


def _partly() -> AcceptanceOut:
    return AcceptanceOut(accepted=False, notes="One screen is missing its empty state.", use=USE,
                         core_flow=CoreFlowOut(realistic=True, quiet_day=True, evidence="fixtures/core-flow/ is there"),
                         criteria_results=[CriterionResultOut(criterion=f"Criterion {i}", met=i != 0, evidence="e")
                                           for i in range(4)])


def _pending(s, job_id: int, kind: str = "dev.merge"):
    from justshipit.db.models import AgentRequest
    return s.scalars(select(AgentRequest).where(AgentRequest.status == "PENDING", AgentRequest.action_kind == kind,
                                                AgentRequest.action_params["dev_job_id"].as_integer() == job_id)).all()


def _failed_card(s, job_id: int):
    from justshipit.db.models import AgentRequest
    return s.scalar(select(AgentRequest).where(AgentRequest.status == "PENDING",
                                               AgentRequest.action_params[FIX_QUESTION_KEY].as_integer() == job_id))


def _queued(s, kind: str, job_id: int):
    from justshipit.db.models import Job
    return s.scalars(select(Job).where(Job.kind == kind, Job.status == "queued",
                                       Job.payload["dev_job_id"].as_integer() == job_id)).all()


def _stage(s, project_id: int, job_id: int) -> dict:
    from justshipit.engineering import status
    return next(st for st in status.board(s, project_id=project_id, lang="en") if st["dev_job_id"] == job_id)


# ------------------------------------------------------------------ A. no merge card for a build that never ran

def test_a_build_whose_setup_failed_gets_no_merge_card_and_is_fixed_on_its_own(s, project, check):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    job, _, _ = _delivered(s, project, f"mg-auto-{project.id}")
    check(SETUP_FAILED, _never_ran())

    worker.acceptance(job.id)

    s.expire_all()
    assert _pending(s, job.id) == [], "nothing to approve: the build never ran"
    assert _failed_card(s, job.id) is None, "the company fixes it first, within the fix budget"
    assert len(_queued(s, "dev_fix", job.id)) == 1
    fix = s.get(DevJob, job.id).result["fixes"][-1]
    assert fix["auto"] is True and fix["request_id"] is None
    assert "python: not found" in fix["note"], "the failure is the round's instructions"
    assert _stage(s, project.id, job.id)["waits"] is None
    with pytest.raises(ValueError):
        worker.merge_again(job.id, "en")


def test_a_failed_build_the_budget_cannot_fix_says_so_first_and_offers_fix_or_drop_never_merge(s, project, check):
    from justshipit.db.models import Project
    from justshipit.engineering import worker
    from justshipit.services import requests

    job, _, _ = _delivered(s, project, f"mg-card-{project.id}")
    s.get(Project, project.id).spent_usd = 9.0  # 1.0 left of 10; a fix round needs 6
    s.commit()
    check(SETUP_FAILED, _never_ran())

    worker.acceptance(job.id)

    s.expire_all()
    assert _pending(s, job.id) == [] and _queued(s, "dev_fix", job.id) == []
    card = _failed_card(s, job.id)
    assert card.type == "QUESTION"
    choices = requests.choices(card, "en")
    assert [c["label"] for c in choices] == [msg("build.failed_card.fix", "en"), msg("build.failed_card.drop", "en")]
    assert [c["verdict"] for c in choices] == ["answer", "answer"]
    assert not any(c["label"] == msg("build.option.merge", "en") for c in choices)
    first = card.summary.split("\n")[0]
    assert first == msg("build.failed.setup", "en", line="scripts/ci-setup.sh: 5: python: not found")
    assert requests.why(card).startswith(first.split(":")[0])
    read = requests.read_model(s, card, "en", None)
    assert read["report"]["headline"] == msg("build.failed_card.headline", "en")
    stage = _stage(s, project.id, job.id)
    assert (stage["headline"], stage["waits"]) == (msg("build.stage.failed_check", "en"), "fix")
    with pytest.raises(ValueError):
        worker.merge_again(job.id, "en")


def test_fix_on_the_failed_card_starts_a_round_with_the_failure_and_drop_throws_the_build_away(s, project, check):
    from justshipit.db.models import DevJob, Project
    from justshipit.engineering import worker
    from justshipit.services import requests
    from test_engineering import _run_queued

    fixed, _, _ = _delivered(s, project, f"mg-fix-{project.id}")
    s.get(Project, project.id).spent_usd = 9.0
    s.commit()
    check(SETUP_FAILED, _never_ran())
    worker.acceptance(fixed.id)
    s.expire_all()
    s.get(Project, project.id).spent_usd = 0.0  # the owner raised the budget
    s.commit()

    requests.respond(s, _failed_card(s, fixed.id).id, HumanResponse(
        decision=HumanDecision.ANSWER, chosen_option=msg("build.failed_card.fix", "en"), responded_by="owner"))
    s.commit()
    [answered] = _queued(s, "dev_fix_answered", fixed.id)
    _run_queued(s, answered.id, answered.payload)

    s.expire_all()
    assert len(_queued(s, "dev_fix", fixed.id)) == 1
    assert "python: not found" in s.get(DevJob, fixed.id).result["fixes"][-1]["note"]

    dropped, spec2, _ = _delivered(s, project, f"mg-drop-{project.id}")
    s.get(Project, project.id).spent_usd = 9.0
    s.commit()
    worker.acceptance(dropped.id)
    s.expire_all()
    requests.respond(s, _failed_card(s, dropped.id).id, HumanResponse(
        decision=HumanDecision.ANSWER, chosen_option=msg("build.failed_card.drop", "en"), responded_by="owner"))
    s.commit()
    [drop] = _queued(s, "dev_fix_answered", dropped.id)
    _run_queued(s, drop.id, drop.payload)

    s.expire_all()
    assert s.get(DevJob, dropped.id).status == "DISCARDED"
    assert s.query(DevJob).filter_by(spec_id=spec2.id).count() == 1, "dropped, not rebuilt"


def test_after_its_automatic_round_a_build_that_fails_again_asks_the_owner(s, project, check):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    job, _, _ = _delivered(s, project, f"mg-twice-{project.id}")
    j = s.get(DevJob, job.id)
    j.result = {**j.result, "fixes": [{"n": 1, "note": "setup failed", "request_id": None, "from": "abc", "at": "x",
                                       "state": "delivered", "auto": True}]}
    s.commit()
    check({"ran": True, "ok": False, "log": "[scenario US1.1] fail: an error page\n(exit 1)"}, _never_ran())

    worker.acceptance(job.id)

    s.expire_all()
    assert _queued(s, "dev_fix", job.id) == []
    card = _failed_card(s, job.id)
    assert card is not None and card.summary.split("\n")[0] == msg("build.failed.run", "en")


def _answer_failed_card(s, project, check, name: str, decision: HumanDecision, option: str | None = None):
    """A build that failed its check with too little budget for a fix round, and the owner's answer to its card."""
    from justshipit.db.models import Project
    from justshipit.engineering import worker
    from justshipit.services import requests
    from test_engineering import _run_queued

    job, spec, _ = _delivered(s, project, name)
    s.get(Project, project.id).spent_usd = 9.0
    s.commit()
    check(SETUP_FAILED, _never_ran())
    worker.acceptance(job.id)
    s.expire_all()
    requests.respond(s, _failed_card(s, job.id).id, HumanResponse(decision=decision, chosen_option=option,
                                                                   responded_by="owner"))
    s.commit()
    for queued in _queued(s, "dev_fix_answered", job.id):
        _run_queued(s, queued.id, queued.payload)
    s.expire_all()
    return job, spec


def test_a_dropped_build_reads_dropped_everywhere_and_waits_on_no_new_attempt(s, project, check):
    from justshipit.api.app import _project_summaries
    from justshipit.db.models import DevJob, Project
    from justshipit.engineering import status

    job, spec = _answer_failed_card(s, project, check, f"mg-dropped-{project.id}", HumanDecision.ANSWER,
                                    msg("build.failed_card.drop", "en"))
    s.get(Project, project.id).status = "DONE"
    s.commit()

    assert s.get(DevJob, job.id).status == "DISCARDED"
    stage = _stage(s, project.id, job.id)
    assert stage["headline"] == msg("build.stage.dropped", "en") and stage["actions"] == []
    assert status.spec_state(s, spec) == "DROPPED"
    summary = next(p for p in _project_summaries(s) if p["id"] == project.id)
    assert summary["stage"] == "DEV_DROPPED", "the owner dropped its build: nothing waits, nothing was built"


def test_after_a_drop_the_next_development_ask_starts_a_new_plan(s, project, check):
    from justshipit.db.models import Job, Plan
    from justshipit.services import matters, requests
    from test_one_card import dev

    first = requests.create(s, dev("Quote bot"), from_agent="product", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    job, spec = _answer_failed_card(s, project, check, f"mg-drop-next-{project.id}", HumanDecision.ANSWER,
                                    msg("build.failed_card.drop", "en"))
    spec.request_id = first.id  # the spec that ask's writing left
    s.commit()

    again = requests.create(s, dev("Quote bot, built again"), from_agent="product", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    s.commit()

    assert isinstance(again, Plan), "a new development ask starts a product plan, not a note on the dropped build's card"
    assert s.scalar(select(Job).where(Job.idempotency_key == f"write_plan:{again.id}")) is not None
    assert matters.live_spec(s, project.id) is None
    assert _stage(s, project.id, job.id)["headline"] == msg("build.stage.dropped", "en")


def test_the_project_manager_reads_a_dropped_build_as_dropped_not_declined(s, project, check):
    from justshipit.db.models import Project
    from justshipit.orchestration import context as ctx

    _answer_failed_card(s, project, check, f"mg-drop-ceo-{project.id}", HumanDecision.ANSWER,
                        msg("build.failed_card.drop", "en"))
    p = s.get(Project, project.id)
    p.plan = {**(p.plan or {}), "build": {"title": "Weekly drafts"}}
    s.commit()

    state = ctx.build_state(s, p)
    assert "dropped" in state and "declined" not in state and "in development" not in state, state


def test_a_drop_whose_pr_will_not_close_still_drops_and_says_the_pr_is_open(s, project, check, monkeypatch):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    def silent(*a):
        raise ValueError("GitHub did not answer")
    monkeypatch.setattr(worker, "_close_prs", silent)

    job, _ = _answer_failed_card(s, project, check, f"mg-drop-pr-{project.id}", HumanDecision.ANSWER,
                                 msg("build.failed_card.drop", "en"))

    j = s.get(DevJob, job.id)
    assert j.status == "DISCARDED"
    assert j.steps[-1]["say"]["key"] == "build.step.drop_pr_open" and "GitHub did not answer" in j.error


def test_a_failed_card_put_off_leaves_the_build_reading_failed_with_a_way_on(s, project, check):
    job, _ = _answer_failed_card(s, project, check, f"mg-later-{project.id}", HumanDecision.DEFER)

    stage = _stage(s, project.id, job.id)
    assert stage["headline"] == msg("build.stage.failed_put_off", "en")
    assert "discard" in stage["actions"] and "merge_again" not in stage["actions"]


def test_a_build_that_partly_passed_still_gets_its_merge_card(s, project, check):
    from justshipit.engineering import worker

    job, _, _ = _delivered(s, project, f"mg-partly-{project.id}")
    check(FLOW_OK, _partly())

    worker.acceptance(job.id)

    s.expire_all()
    assert len(_pending(s, job.id)) == 1 and _failed_card(s, job.id) is None and _queued(s, "dev_fix", job.id) == []


def test_the_core_flow_and_screens_run_where_python_answers_as_in_the_test_image():
    """The tests ran in python:3.12-slim, which has `python`; the core flow and the screens ran the same ci-setup.sh in
    the engineer image, which had only python3."""
    root = Path(__file__).resolve().parents[1]
    assert "python-is-python3" in (root / "docker" / "engineer.Dockerfile").read_text(encoding="utf-8")
    from justshipit.settings import company_config
    assert company_config()["engineering"]["sandbox"]["image"] != "ship/engineer:3", "a new image, built anew"


# ------------------------------------------------------------------ B. the build says how far it has come

def _building(s, project, tmp_path):
    from justshipit.contracts.handoff import Handoff
    from justshipit.db.models import DevJob, Spec

    h = Handoff(spec_id="SPEC-P1", project="Test", title="Digest", source={"path": "specs/p", "commit_sha": "abc"},
                target={"repository": "digest"}, approval={"spec_approved": True, "implementation_authorized": True})
    spec = Spec(spec_key=f"SPEC-PG{project.id}", project_id=project.id, title="Digest", registry_path="specs/p",
                approved_commit_sha="abc", handoff=h.model_dump(mode="json"), status="IN_DEVELOPMENT")
    s.add(spec)
    s.flush()
    job = DevJob(spec_id=spec.id, status="IMPLEMENTING", branch="ai/x-a1", steps=[
        {"at": "2026-11-05T06:19:39+00:00", "status": "IMPLEMENTING", "summary": "implementing",
         "say": {"key": "build.step.build_start"}}])
    s.add(job)
    s.commit()
    wt = tmp_path / "wt"
    (wt / ".ship").mkdir(parents=True)
    (wt / ".git" / "logs").mkdir(parents=True)
    (wt / ".ship" / "plan.md").write_text("# Plan\n\n## Tasks\n1. Upload\n2. Redact\n3. Group\n4. Digest page\n")
    return job, wt


def _reflog(wt, *subjects: str) -> None:
    lines = ["0 1 AI Engineer <ai@ship.local> 1 +0000\tcheckout: moving from main to ai/x-a1"]
    lines += [f"1 2 AI Engineer <ai@ship.local> 2 +0000\tcommit: {x}" for x in subjects]
    (wt / ".git" / "logs" / "HEAD").write_text("\n".join(lines) + "\n")


def test_a_long_build_says_how_many_planned_tasks_are_done(s, project, tmp_path):
    from justshipit.engineering import worker

    job, wt = _building(s, project, tmp_path)
    _reflog(wt, "[task 1/4] upload form", "[task 2/4] redaction")

    worker.note_progress(job.id, wt)

    s.expire_all()
    stage = _stage(s, project.id, job.id)
    assert stage["headline"] == msg("build.stage.running", "en", doing=msg("build.progress.tasks", "en", done=2, total=4))
    steps_before = len(job.steps)
    worker.note_progress(job.id, wt)
    s.expire_all()
    assert len(s.get(type(job), job.id).steps) == steps_before, "progress is no step of its own: the log stays calm"


def test_without_task_marks_the_build_counts_its_saved_changes_and_never_follows_a_link(s, project, tmp_path):
    from justshipit.engineering import worker

    job, wt = _building(s, project, tmp_path)
    (wt / ".ship" / "plan.md").unlink()
    _reflog(wt, "feat: upload", "feat: redaction", "fix: tests")
    worker.note_progress(job.id, wt)
    s.expire_all()
    assert _stage(s, project.id, job.id)["headline"].endswith(msg("build.progress.commits", "en", n=3))

    (wt / ".git" / "logs" / "HEAD").unlink()
    secret = tmp_path / "secret"
    secret.write_text("commit: [task 9/9] host file\n")
    (wt / ".git" / "logs" / "HEAD").symlink_to(secret)
    worker.note_progress(job.id, wt)
    s.expire_all()
    assert "9" not in _stage(s, project.id, job.id)["headline"]


def _progress(s, job) -> dict:
    s.expire_all()
    return s.get(type(job), job.id).result["progress"]


def _items(p: dict) -> list[tuple[int, bool]]:
    return [(x["n"], x["done"]) for x in p["tasks"]]


def test_the_build_keeps_its_items_by_name_each_done_once_its_task_commit_is_made(s, project, tmp_path):
    from justshipit.engineering import worker

    job, wt = _building(s, project, tmp_path)
    _reflog(wt, "[task 1/4] upload form", "wip", "[task 3/4] grouping")

    worker.note_progress(job.id, wt)

    p = _progress(s, job)
    assert [x["title"] for x in p["tasks"]] == ["Upload", "Redact", "Group", "Digest page"]
    assert _items(p) == [(1, True), (2, False), (3, True), (4, False)]
    assert (p["done"], p["total"], p["current"]) == (2, 4, 2), "the lowest item not done is the one being built"
    assert _stage(s, project.id, job.id)["headline"].endswith(msg("build.progress.tasks", "en", done=2, total=4))


def test_the_items_show_from_the_first_minute_and_a_commit_without_a_mark_finishes_none(s, project, tmp_path):
    from justshipit.engineering import worker

    job, wt = _building(s, project, tmp_path)
    worker.note_progress(job.id, wt)
    p = _progress(s, job)
    assert (p["done"], p["total"], p["current"]) == (0, 4, 1)

    _reflog(wt, "chore: scaffold")
    worker.note_progress(job.id, wt)
    assert (_progress(s, job)["done"], _progress(s, job)["current"]) == (0, 1)


def test_an_unchanged_build_writes_nothing_and_a_new_mark_writes_once(s, project, tmp_path):
    from justshipit.engineering import worker

    job, wt = _building(s, project, tmp_path)
    _reflog(wt, "[task 1/4] upload form")
    worker.note_progress(job.id, wt)
    first = _progress(s, job)["at"]

    worker.note_progress(job.id, wt)
    assert _progress(s, job)["at"] == first, "the same state is not written again"

    _reflog(wt, "[task 1/4] upload form", "[task 2/4] redaction")
    worker.note_progress(job.id, wt)
    assert _progress(s, job)["current"] == 3


def test_each_item_keeps_what_works_once_done_and_a_new_wording_is_written_again(s, project, tmp_path):
    from justshipit.engineering import worker

    job, wt = _building(s, project, tmp_path)
    plan = wt / ".ship" / "plan.md"
    plan.write_text("## Tasks\n1. **Upload**: a user sends in a file · upload.ts, US1.1\n2. Redact: names.py\n",
                    encoding="utf-8")
    _reflog(wt, "[task 1/2] upload form")
    worker.note_progress(job.id, wt)
    p = _progress(s, job)
    assert p["tasks"] == [{"n": 1, "title": "Upload", "detail": "a user sends in a file", "done": True},
                          {"n": 2, "title": "Redact", "done": False}], "an old-shape item has no detail"

    worker.note_progress(job.id, wt)
    assert _progress(s, job)["at"] == p["at"], "the same items are not written again"

    plan.write_text("## Tasks\n1. **Upload**: a user drops in a file\n2. Redact: names.py\n", encoding="utf-8")
    worker.note_progress(job.id, wt)
    assert _progress(s, job)["tasks"][0]["detail"] == "a user drops in a file"


def test_a_finished_build_keeps_its_whole_list_after_its_worktree_is_gone(s, project, tmp_path):
    import shutil

    from justshipit.engineering import worker

    job, wt = _building(s, project, tmp_path)
    with worker.watching(job.id, wt):
        _reflog(wt, *(f"[task {k}/4] item {k}" for k in range(1, 5)))
    shutil.rmtree(wt)

    p = _progress(s, job)
    assert _items(p) == [(1, True), (2, True), (3, True), (4, True)] and p["current"] is None


def test_a_plan_past_its_size_cap_or_not_a_plain_file_gives_no_items(s, project, tmp_path):
    import os

    from justshipit.engineering import worker
    from justshipit.engineering.build_items import MAX_BYTES

    _, wt = _building(s, project, tmp_path)
    plan = wt / ".ship" / "plan.md"
    plan.write_text("## Tasks\n1. Upload\n" + "x" * MAX_BYTES)
    _reflog(wt, "[task 1/9] upload")
    assert worker.work_done(wt) == {"done": 1, "total": 9}, "the commits' own count, no list"

    plan.unlink()
    host = tmp_path / "host-notes.md"
    host.write_text("## Tasks\n1. Read a host file\n")
    try:
        plan.symlink_to(host)
    except OSError:
        pytest.skip("this host cannot make a symlink")
    assert worker.work_done(wt) == {"done": 1, "total": 9}
    if hasattr(os, "mkfifo"):
        plan.unlink()
        os.mkfifo(plan)
        assert worker.work_done(wt) == {"done": 1, "total": 9}, "a FIFO is refused, and reading it never hangs"


def test_a_progress_noted_before_build_items_still_reads(s, project, tmp_path):
    job, _ = _building(s, project, tmp_path)
    job.result = {"progress": {"done": 7, "total": 12, "status": "IMPLEMENTING", "step": 1, "at": "2026-11-05T06:30:00+00:00"}}
    s.commit()
    s.expire_all()
    assert _stage(s, project.id, job.id)["headline"].endswith(msg("build.progress.tasks", "en", done=7, total=12))


# ------------------------------------------------------------------ D. who must reach the product

def test_the_acceptance_check_reads_what_the_company_promised_people(s, project, check):
    from justshipit.db.models import OwnerTask
    from justshipit.engineering import worker

    job, _, _ = _delivered(s, project, f"mg-promise-{project.id}")
    s.add(OwnerTask(project_id=project.id, kind="other", title="Ask Tom for a week of support emails", card={
        "handout": {"to": "Tom", "kind": "message",
                    "body": "Once you've said yes, I'll send you a private upload link. Drag the files in."}}))
    s.commit()
    seen: dict = {}
    check(FLOW_OK, _partly(), seen)

    worker.acceptance(job.id)

    assert "private upload link" in seen["prompt"] and "Tom" in seen["prompt"]


def test_when_others_must_open_it_running_it_locally_is_not_recommended_and_picking_it_says_why(s, project):
    from justshipit.db.models import OwnerTask
    from justshipit.services import requests

    use = USE.model_copy(update={"recommend": "local", "reach": "the 8 stores, each through a private upload link"})
    _merged(s, project, f"mg-reach-{project.id}", AcceptanceOut(accepted=True, notes="ok", criteria_results=[], use=use,
                                                                 core_flow=CoreFlowOut(realistic=True, quiet_day=True,
                                                                                       evidence="e")))
    [card] = _use_card(s, project)
    choices = requests.choices(card, "en")
    assert [c["label"] for c in choices if c["recommended"]] == ["Host it on Render"]
    local = next(c for c in choices if c["label"] == msg("build.use.option.local", "en"))
    conflict = msg("build.use.local_conflict", "en", who="the 8 stores, each through a private upload link")
    assert conflict in local["summary"]

    requests.respond(s, card.id, HumanResponse(decision=HumanDecision.ANSWER, chosen_option=local["label"],
                                               responded_by="owner"))
    s.commit()
    task = s.query(OwnerTask).filter_by(request_id=card.id).one()
    assert task.card["what"].startswith(conflict), "picked anyway, the to-do says the conflict in one line first"


# ------------------------------------------------------------------ E and G. one project state everywhere

def test_a_merged_build_nobody_can_use_yet_is_not_done_anywhere(s, project):
    from justshipit.db.models import OwnerTask, Project
    from justshipit.services import owner_tasks, requests, roadmap
    from bet_loop_kit import make_bet
    from people_kit import make_business

    business = make_business(s)
    bet = make_bet(s, business)
    p = s.get(Project, project.id)
    p.status, p.business_id, p.bet_id = "DONE", business.id, bet.id
    s.commit()
    _merged(s, project, f"mg-stage-{project.id}")
    [card] = _use_card(s, project)

    def run_stage():
        view = roadmap.roadmap(s, business.id, lang="en")
        return next(b for b in view["bets"] if b["id"] == bet.id)["run"]["stage"]

    assert _summary(s, project)["stage"] == "MERGED" == run_stage()
    requests.respond(s, card.id, HumanResponse(decision=HumanDecision.ANSWER, chosen_option="Run it on my computer",
                                               responded_by="owner"))
    s.commit()
    assert _summary(s, project)["stage"] == "MERGED" == run_stage(), "the to-do with the steps is still open"

    owner_tasks.done(s, s.query(OwnerTask).filter_by(request_id=card.id).one().id)
    s.commit()
    assert _summary(s, project)["stage"] == "DONE" == run_stage()


def test_a_merged_build_with_nothing_to_open_ends_with_how_to_use_it(s, project):
    from justshipit.db.models import Project

    s.get(Project, project.id).status = "DONE"
    s.commit()
    _, spec = _merged(s, project, f"mg-tool-{project.id}", AcceptanceOut(
        accepted=True, notes="ok", criteria_results=[], use=UseOut(local_steps=[], local_address=None, hosting=[], needs=[], recommend="local",
                                                                   why="It is a library other code imports."),
        core_flow=CoreFlowOut(realistic=True, quiet_day=True, evidence="e")))

    assert _use_card(s, project) == []
    summary = _summary(s, project)
    assert summary["dev_use"]["state"] == "tool"
    assert summary["dev_use"]["readme"] == f"products/{_repo(spec)}/README.md"
    assert summary["stage"] == "DONE"


def test_a_build_with_screens_but_no_address_still_gets_the_decision_not_nothing_to_open(s, project):
    """The agent leaves the address empty when the files set no port (a framework's default); screens mean people open it."""
    from justshipit.contracts.handoff import Handoff
    from justshipit.engineering import usable, worker
    from test_usable import _accepted

    job, spec, _ = _delivered(s, project, f"mg-noaddr-{project.id}", _accepted(use=UseOut(
        local_steps=["npm install", "npm run dev"], local_address=None, hosting=[], needs=[], recommend="local",
        why="It runs on your computer.")))
    h = Handoff.model_validate(spec.handoff)
    h.workflow.screens = True
    spec.handoff = h.model_dump(mode="json")
    s.commit()
    worker.merge(job.id)
    usable.after_merge(job.id)
    s.expire_all()

    assert len(_use_card(s, project)) == 1
    assert _summary(s, project)["dev_use"]["state"] != "tool"


def _repo(spec) -> str:
    from justshipit.contracts.handoff import Handoff
    return Handoff.model_validate(spec.handoff).target.repository


# ------------------------------------------------------------------ F. the merge card's why and one preview address

def test_unseen_screens_read_as_a_list_and_a_capture_the_cpo_did_not_open_is_not_unseen(s, project, check):
    from justshipit.db.models import AgentRequest
    from justshipit.engineering import worker

    job, _, _ = _delivered(s, project, f"mg-unseen-{project.id}")
    check(FLOW_OK, AcceptanceOut(accepted=True, notes="ok", criteria_results=[], use=USE,
                                 unseen=["The loading screen, which I did not open", "The print view"],
                                 core_flow=CoreFlowOut(realistic=True, quiet_day=True, evidence="e")))

    worker.acceptance(job.id)

    s.expire_all()
    card = s.query(AgentRequest).filter_by(project_id=project.id, action_kind="dev.merge").one()
    assert card.summary.splitlines()[1] == msg("build.merge_card.unseen", "en",
                                               what="The loading screen, which I did not open; The print view")
    asked = worker._screens_ask({"files": ["digest-quiet-1440.png"], "log": "[shot] digest-quiet-1440 /"})
    assert "Open every capture" in asked and "no capture shows" in asked
    assert "no capture shows" in AcceptanceOut.model_fields["unseen"].description


def test_an_item_that_is_only_a_code_leaves_no_empty_line_on_the_merge_card(s, project, check):
    from justshipit.contracts.handoff import CriterionResultOut
    from justshipit.db.models import AgentRequest
    from justshipit.engineering import worker

    job, _, _ = _delivered(s, project, f"mg-codes-{project.id}")
    check(FLOW_OK, AcceptanceOut(accepted=True, notes="ok", use=USE, unseen=["Today", "T-105", "Settings"],
                                 criteria_results=[CriterionResultOut(criterion="T-104", met=True, evidence="e"),
                                                   CriterionResultOut(criterion="The digest lists today's drafts",
                                                                      met=True, evidence="e")],
                                 core_flow=CoreFlowOut(realistic=True, quiet_day=True, evidence="e")))

    worker.acceptance(job.id)

    s.expire_all()
    summary = s.query(AgentRequest).filter_by(project_id=project.id, action_kind="dev.merge").one().summary
    assert "- ✔ The digest lists today's drafts" in summary
    assert not any(line.strip() in ("- ✔", "- ✘") for line in summary.splitlines()), summary
    assert "Today; Settings" in summary and "; ;" not in summary


def test_one_preview_address_for_the_capture_the_card_and_the_run_steps():
    from justshipit.contracts.handoff import Handoff
    from justshipit.db.models import DevJob
    from justshipit.engineering import try_it, worker

    assert try_it.PREVIEW_ADDRESS in worker._capture("en")
    assert "127.0.0.1:4173" not in worker._capture("en").replace(try_it.PREVIEW_ADDRESS, "")
    job = DevJob(id=1, branch="ai/x", result={"try": {"preview": True, "ci_setup": True}})
    assert try_it.card(job, "x")["address"] == try_it.OWNER_PREVIEW_ADDRESS, "the same server, as the owner opens it"
    h = Handoff(spec_id="S", project="T", title="t", source={"path": "p", "commit_sha": "a"},
                target={"repository": "x"}, approval={}, workflow={"screens": True})
    assert try_it.PREVIEW_ADDRESS in worker._ui_build(h, "DESIGN.md"), "the engineer serves the product itself there too"
    assert "default port" in worker._USE_ASK, "the product agent never guesses a framework's default port"


# ------------------------------------------------------------------ L9a leftovers: a later answer, one card per run

@pytest.mark.parametrize("decision", [HumanDecision.DEFER, HumanDecision.REJECT])
def test_a_failed_card_put_off_or_turned_down_can_be_asked_again_and_answered(s, project, check, decision):
    """The owner put the fix-or-drop card off (or turned it down) and could then neither answer it nor have it fixed:
    the only way on was throwing it away and building from scratch."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import revise
    from justshipit.services import requests
    from test_engineering import _run_queued

    job, spec = _answer_failed_card(s, project, check, f"mg-again-{decision.value}-{project.id}", decision)
    stage = _stage(s, project.id, job.id)
    assert "fix_or_drop_again" in stage["actions"] and stage["detail"] == msg("build.stage.failed_put_off_next", "en")

    asked = revise.ask_again(job.id, "en")

    s.expire_all()
    card = _failed_card(s, job.id)
    assert card is not None and card.id == asked
    assert _stage(s, project.id, job.id)["waits"] == "fix" and "fix_or_drop_again" not in _stage(
        s, project.id, job.id)["actions"]
    with pytest.raises(ValueError):
        revise.ask_again(job.id, "en")
    requests.respond(s, card.id, HumanResponse(decision=HumanDecision.ANSWER, chosen_option=msg(
        "build.failed_card.drop", "en"), responded_by="owner"))
    s.commit()
    [drop] = _queued(s, "dev_fix_answered", job.id)
    _run_queued(s, drop.id, drop.payload)
    s.expire_all()
    assert s.get(DevJob, job.id).status == "DISCARDED"


def _acceptance_twice(s, project, check, name: str, flow: dict, out: AcceptanceOut, spent: float = 0.0):
    """The acceptance check as the queue runs it, then again: its worker died after the verdict was recorded and
    before the queue heard it finish, so the queue runs the same job once more."""
    from justshipit.db.models import Job, Project
    from justshipit.services import queue
    from test_engineering import _run_queued

    job, _, _ = _delivered(s, project, name)
    s.get(Project, project.id).spent_usd = spent
    run = queue.enqueue(s, "acceptance", {"dev_job_id": job.id}, idempotency_key=f"acceptance:{job.id}")
    s.commit()
    check(flow, out)
    _run_queued(s, run.id, {"dev_job_id": job.id})
    s.get(Job, run.id).status = "queued"
    s.commit()
    _run_queued(s, run.id, {"dev_job_id": job.id})
    s.expire_all()
    return job


def test_an_acceptance_the_queue_runs_again_opens_no_card_beside_the_fix_it_queued(s, project, check):
    job = _acceptance_twice(s, project, check, f"mg-rerun-fix-{project.id}", SETUP_FAILED, _never_ran())

    assert len(_queued(s, "dev_fix", job.id)) == 1 and _failed_card(s, job.id) is None


@pytest.mark.parametrize("flow, out, spent", [(FLOW_OK, "partly", 0.0), (SETUP_FAILED, "never_ran", 9.0)])
def test_an_acceptance_the_queue_runs_again_files_its_card_once(s, project, check, flow, out, spent):
    job = _acceptance_twice(s, project, check, f"mg-rerun-{out}-{project.id}", flow,
                            _partly() if out == "partly" else _never_ran(), spent)

    from justshipit.db.models import AgentRequest
    cards = s.scalars(select(AgentRequest).where(AgentRequest.project_id == project.id)).all()
    mine = [c for c in cards if (c.action_params or {}).get("dev_job_id") == job.id
            or (c.action_params or {}).get(FIX_QUESTION_KEY) == job.id]
    assert len(mine) == 1, [c.title for c in mine]


def test_the_merge_card_leads_with_the_verdict_then_what_nobody_saw_then_what_the_spec_did_not_ask(s, project, check):
    """Junho round 7: REQ-118 began with four 'Built beyond the spec' lines, and its verdict 'every acceptance criterion
    is met' sat under Details. Typing the card's number to confirm stays as it is."""
    from justshipit.db.models import AgentRequest
    from justshipit.engineering import worker
    from justshipit.services import requests

    job, _, _ = _delivered(s, project, f"mg-lead-{project.id}")
    check(FLOW_OK, AcceptanceOut(accepted=True, notes="It does what the spec asks.", use=USE,
                                 unseen=["The usage page"], sample_output="The captures use made-up rows.",
                                 beyond_spec=["Upload up to five files (src/ui/upload.ts)"],
                                 criteria_results=[CriterionResultOut(criterion="The report lists blanks", met=True,
                                                                      evidence="e")],
                                 core_flow=CoreFlowOut(realistic=True, quiet_day=True, evidence="e")))

    worker.acceptance(job.id)

    s.expire_all()
    card = s.query(AgentRequest).filter_by(project_id=project.id, action_kind="dev.merge").one()
    lines = card.summary.splitlines()
    assert lines[:4] == [msg("build.merge_card.accepted", "en", notes="It does what the spec asks."),
                         msg("build.merge_card.unseen", "en", what="The usage page"),
                         msg("build.merge_card.sample", "en", why="The captures use made-up rows."),
                         msg("build.gap.beyond", "en", detail="Upload up to five files (src/ui/upload.ts)")]
    assert lines[4] == "- ✔ The report lists blanks"
    assert requests.why(card) == "Product agent's verdict: every acceptance criterion is met."
    assert requests.report(s, card, "en")["typed_confirm"]


# ------------------------------------------------------------------ what the owner checks before approving

def test_the_merge_card_gives_the_steps_to_use_it_each_check_and_what_the_spec_did_not_ask(s, project, check):
    """REQ-4 (a CLI): the card said there was nothing to open, listed 24 green ticks and a raw list of 51 files, and
    the owner could not find what to fix before approving."""
    from justshipit.contracts import owner_text
    from justshipit.db.models import AgentRequest, DevJob
    from justshipit.engineering import try_it, worker
    from justshipit.services import requests

    job, _, repo = _delivered(s, project, f"mg-review-{project.id}")
    tasks = [{"n": 1, "title": "Read the input file", "detail": "Says which line is wrong.", "done": True},
             {"n": 2, "title": "Write the report", "detail": "", "done": True}]
    s.get(DevJob, job.id).result = {"progress": {"done": 2, "total": 2, "tasks": tasks, "current": None},
                                    "commits_ahead": 3}
    s.commit()
    check(FLOW_OK, AcceptanceOut(accepted=True, notes="ok", use=USE, beyond_spec=["A render command (src/render.ts)"],
                                 design_findings=["The empty list has no hint (T-104)"],
                                 criteria_results=[CriterionResultOut(criterion="The report lists blanks", met=False,
                                                                      evidence="Only the first blank (US1.2) shows.")],
                                 core_flow=CoreFlowOut(realistic=True, quiet_day=True, evidence="e")))

    worker.acceptance(job.id)

    s.expire_all()
    card = s.query(AgentRequest).filter_by(project_id=project.id, action_kind="dev.merge").one()
    review = requests.read_model(s, card, "en", None)["review"]
    assert review["use"]["steps"] == [*try_it.clone_steps(s.get(DevJob, job.id), repo.name), *USE.local_steps]
    assert review["use"]["folder"] == f"products/{repo.name}" and review["use"]["branch"] == job.branch
    assert [n["name"] for n in review["use"]["needs"]] == ["AI_API_KEY"] and review["use"]["needs"][0]["required"]
    assert review["criteria"][0] == {"criterion": "The report lists blanks", "met": False,
                                     "evidence": "Only the first blank shows.", "scenario": None}
    assert [(g["kind"], g["detail"]) for g in review["gaps"]] == [("beyond", "A render command (src/render.ts)")]
    assert review["findings"] == ["The empty list has no hint"]
    assert review["items"] == {"done": 2, "total": 2, "tasks": tasks} and review["commits_ahead"] == 3
    said = [c["criterion"] + " " + c["evidence"] for c in review["criteria"]] + review["findings"]
    assert [x for x in said if owner_text.leaks(x, line=True)] == []


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_p1_scenario_on_the_merge_card_reads_back_in_its_three_parts(lang):
    from justshipit.engineering import merge_review

    parts = {"given": "a feature file, with two screens", "when": "you run generate", "then": "three files appear"}

    assert merge_review.scenario(msg("build.scenario.criterion", lang, **parts)) == parts
    assert merge_review.scenario("The report lists blanks") is None


def test_a_step_too_long_to_show_whole_leaves_the_card_with_no_steps_rather_than_a_cut_one():
    from justshipit.db.models import DevJob
    from justshipit.engineering import merge_review

    use = {**USE.model_dump(mode="json"), "local_steps": ["npm ci", "x" * 400]}
    job = DevJob(id=1, branch="ai/x", result={"acceptance": {"use": use}})

    assert merge_review.card(job, "x")["use"] is None


def _forge(monkeypatch, remote: Path, branch: str) -> list[tuple[list[str], str]]:
    """A forge that merges any PR, recording each gh call and where the remote branch stood when it came."""
    import subprocess

    from justshipit.engineering import worker
    calls: list[tuple[list[str], str]] = []

    def gh(repo, *args):
        tip = subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/heads/{branch}"], cwd=remote,
                             capture_output=True, text=True).stdout.strip()
        calls.append((list(args), tip))
        return subprocess.CompletedProcess(["gh", *args], 0, "", "")
    monkeypatch.setattr(worker, "_gh", gh)
    return calls


@pytest.mark.parametrize("unpushed", [True, False])
def test_approving_a_fix_asked_in_team_chat_pushes_the_approved_commit_then_merges_at_it(s, project, tmp_path,
                                                                                         monkeypatch, unpushed):
    """A fix asked in team chat was delivered to this machine only. The merge the owner approved pushes exactly the
    approved commit first, and the PR merge checks the PR stands at it. A build already on the forge pushes nothing."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker
    from test_engineering import _merge_ready, _product_repo, _sh

    repo = _product_repo(f"mg-chat-push-{project.id}-{unpushed}")
    branch = f"ai/spec-mgpush{int(unpushed)}-a1"
    job, _, _ = _merge_ready(s, project, repo, branch, {"app.py": "v2\n"})
    remote = tmp_path / "origin.git"
    _sh(tmp_path, "git", "init", "-q", "--bare", str(remote))
    _sh(repo, "git", "remote", "add", "origin", str(remote))
    head = _sh(repo, "git", "rev-parse", branch)
    job = s.get(DevJob, job.id)
    job.pr_url = "https://github.com/o/uxscope/pull/4"
    job.result = {"head": head, **({"unpushed": head} if unpushed else {})}
    s.commit()
    calls = _forge(monkeypatch, remote, branch)

    worker.merge(job.id, head)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "MERGED" and "unpushed" not in done.result
    merged = [(args, tip) for args, tip in calls if args[:2] == ["pr", "merge"]]
    assert [args for args, _ in merged] == [["pr", "merge", job.pr_url, "--merge", "--match-head-commit", head]]
    assert merged[0][1] == (head if unpushed else ""), "pushed before the merge, and only when not on the forge yet"


def test_a_pr_merge_that_fails_after_the_push_never_says_the_fix_is_not_pushed(s, project, tmp_path, monkeypatch):
    """The approved commit is on the forge once the push lands, whatever the PR merge does next: the card asking for
    the merge again does not say it is on this computer only."""
    import subprocess
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker
    from justshipit.i18n import msg
    from test_engineering import _merge_ready, _pending_merge, _product_repo, _sh

    repo = _product_repo(f"mg-chat-push-fail-{project.id}")
    branch = "ai/spec-mgpushfail-a1"
    job, _, _ = _merge_ready(s, project, repo, branch, {"app.py": "v2\n"})
    remote = tmp_path / "origin.git"
    _sh(tmp_path, "git", "init", "-q", "--bare", str(remote))
    _sh(repo, "git", "remote", "add", "origin", str(remote))
    head = _sh(repo, "git", "rev-parse", branch)
    job = s.get(DevJob, job.id)
    job.pr_url = "https://github.com/o/uxscope/pull/4"
    job.result = {"head": head, "unpushed": head}
    s.commit()
    monkeypatch.setattr(worker, "_gh", lambda repo, *args: subprocess.CompletedProcess(
        ["gh", *args], 1, "", "GraphQL: Pull request is not mergeable"))

    worker.merge(job.id, head)

    s.expire_all()
    again = s.get(DevJob, job.id)
    assert again.status == "DELIVERED" and "unpushed" not in again.result
    assert _sh(remote, "git", "rev-parse", branch) == head
    card = _pending_merge(s, job.id)
    assert card is not None
    assert all(msg("build.merge_card.not_pushed", lang) not in card.summary for lang in ("en", "ko"))
