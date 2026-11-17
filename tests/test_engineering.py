import os
import re
import shutil

import pytest
from justshipit.agents.independence import Served
from justshipit.contracts.handoff import (
    AcceptanceOut, CodeReviewOut, CoreFlowOut, DevJobStatus, DevStepOut, Handoff, IntakeOut,
)


@pytest.fixture(autouse=True)
def _korean_owner(monkeypatch):
    """These cases read the words a Korean owner sees; tests/test_build_i18n.py covers English."""
    from justshipit.services import app_settings
    monkeypatch.setattr(app_settings, "language", lambda: "ko")


def _fake_output(model):
    if model is IntakeOut:
        return IntakeOut(clear_enough=True, blocking_questions=[], assumptions_safe_to_make=[],
                         implementation_outline="outline")
    if model is CodeReviewOut:
        return CodeReviewOut(verdict="pass", summary="review ok", spec_coverage=[], findings=[])
    if model is AcceptanceOut:
        return AcceptanceOut(accepted=True, criteria_results=[], notes="ok", core_flow=CoreFlowOut(
            realistic=True, quiet_day=True, evidence="fixtures/core-flow/ has the owner's columns"))
    return DevStepOut(status="done", summary="done", files_changed=[], tests_run=[], decisions=[], blockers=[])


def _queued_job(s, project):
    from justshipit.db.models import DevJob, Spec

    h = Handoff(spec_id="SPEC-T1", project="Test", title="t",
                source={"path": "specs/t", "commit_sha": "abc123"}, target={"repository": "demo"},
                approval={"spec_approved": True, "implementation_authorized": True})
    spec = Spec(spec_key=f"SPEC-ENG-{project.id}", project_id=project.id, title="t", registry_path="specs/t",
                approved_commit_sha="abc123", handoff=h.model_dump(mode="json"))
    s.add(spec)
    s.flush()
    job = DevJob(spec_id=spec.id)
    s.add(job)
    s.commit()
    return job


def test_plan_and_review_steps_use_pstack_plugin_skills(s, project, tmp_path, monkeypatch):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker
    from justshipit.sandbox import docker

    job = _queued_job(s, project)
    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    monkeypatch.setattr(docker, "image_present", lambda image: True)
    monkeypatch.setattr(worker, "host_git_refusal", lambda lang: None)

    calls: list[tuple[str, str]] = []

    def fake_run_agent(agent_id, purpose, prompt, model, **kw):
        calls.append((agent_id, prompt))
        return _fake_output(model)

    monkeypatch.setattr(worker, "run_agent", fake_run_agent)
    monkeypatch.setattr(worker, "last_served",
                        lambda: Served("claude", "sonnet" if calls[-1][0] == "eng_reviewer" else "opus"))
    monkeypatch.setattr(worker, "prepare_worktree", lambda spec, h, attempt: (tmp_path, tmp_path, "ai/t", None))
    monkeypatch.setattr(worker, "_write_spec", lambda wt, h, given: None)
    monkeypatch.setattr(worker, "run_tests", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(worker, "_git", lambda *a, **kw: "")
    monkeypatch.setattr(worker, "_ship", lambda *a, **kw: None)

    worker.run_dev_job(job.id)

    engineer_prompts = [p for agent, p in calls if agent == "engineer"]
    assert any(p.startswith("Use the pstack:architect skill") for p in engineer_prompts)
    assert any(p.startswith("Use the pstack:thermo-nuclear-review skill") for p in engineer_prompts)
    assert not any("pstack-" in p for p in engineer_prompts)
    assert any(agent == "eng_reviewer" for agent, _ in calls)

    s.expire_all()
    summaries = [step["summary"] for step in s.get(DevJob, job.id).steps]
    assert "pstack:architect" in summaries
    assert "pstack:thermo-nuclear-review + independent review" in summaries
    assert "review by eng_reviewer (claude/sonnet) of engineer (claude/opus)" in summaries


def test_missing_engineer_image_fails_the_job_at_intake(s, project, monkeypatch):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker
    from justshipit.sandbox import docker
    from justshipit.settings import company_config

    job = _queued_job(s, project)
    image = company_config()["engineering"]["sandbox"]["image"]
    asked: list[str] = []
    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    monkeypatch.setattr(docker, "image_present", lambda name: asked.append(name) or False)
    monkeypatch.setattr(worker, "prepare_worktree", lambda *a, **kw: pytest.fail("worktree prepared"))
    monkeypatch.setattr(worker, "run_agent", lambda *a, **kw: pytest.fail("agent called"))

    worker.run_dev_job(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert asked == [image]
    assert done.status == "FAILED"
    assert image in done.error and "uv run ship sandbox build engineer" in done.error
    assert [step["status"] for step in done.steps] == ["FAILED"], "no step ran"


def test_failed_intake_leaves_the_spec_retryable(s, project, monkeypatch):
    from justshipit.contracts.handoff import SpecStatus
    from justshipit.db.models import DevJob, Spec
    from justshipit.engineering import specs, worker
    from justshipit.sandbox import docker

    job = _queued_job(s, project)
    spec_id = job.spec_id
    s.get(Spec, spec_id).status = SpecStatus.READY_FOR_DEV.value
    s.commit()
    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    monkeypatch.setattr(docker, "image_present", lambda name: False)

    worker.run_dev_job(job.id)

    s.expire_all()
    spec = s.get(Spec, spec_id)
    assert s.get(DevJob, job.id).status == "FAILED"
    assert spec.status == SpecStatus.READY_FOR_DEV.value
    retry_id = specs.retry_dev(spec.spec_key, "ko")
    s.expire_all()
    assert s.get(DevJob, retry_id).status == "QUEUED"


def test_docker_down_fails_the_job_without_telling_the_owner_to_build(s, project, monkeypatch):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker
    from justshipit.sandbox import docker

    job = _queued_job(s, project)
    monkeypatch.setattr(docker, "docker_ok", lambda: False)
    monkeypatch.setattr(docker, "image_present", lambda name: pytest.fail("image checked"))
    monkeypatch.setattr(worker, "run_agent", lambda *a, **kw: pytest.fail("agent called"))

    worker.run_dev_job(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "FAILED"
    assert "Docker" in done.error and "sandbox build" not in done.error


def test_missing_docker_binary_fails_the_job_instead_of_leaving_it_queued(s, project, monkeypatch):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker
    from justshipit.sandbox import docker

    def no_docker(*a, **kw):
        raise FileNotFoundError("docker")

    job = _queued_job(s, project)
    monkeypatch.setattr(docker.subprocess, "run", no_docker)
    monkeypatch.setattr(worker, "run_agent", lambda *a, **kw: pytest.fail("agent called"))

    worker.run_dev_job(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "FAILED"
    assert "Docker" in done.error and "sandbox build" not in done.error


def test_missing_image_is_ignored_when_engineering_runs_on_the_host(s, project, monkeypatch):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker
    from justshipit.sandbox import docker

    job = _queued_job(s, project)
    monkeypatch.setattr(worker, "_containerized", lambda: False)
    monkeypatch.setattr(docker, "image_present", lambda name: pytest.fail("image checked"))
    def stop_at_worktree(*a, **kw):
        raise RuntimeError("reached")

    monkeypatch.setattr(worker, "prepare_worktree", stop_at_worktree)

    worker.run_dev_job(job.id)

    s.expire_all()
    assert "이 PC에서 하는 작업이 실패했어요: reached" in s.get(DevJob, job.id).error


def _sh(cwd, *args):
    import subprocess
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _plant(clone, marker_dir):
    """Every way an engineer could make host git run their code: hooks, fsmonitor, a clean filter."""
    import os
    for name in ("pre-commit", "commit-msg", "post-commit", "fsmonitor", "filter"):
        script = clone / ".git" / f"evil-{name}"
        body = f"touch '{marker_dir / name}'\n" + ("cat\n" if name == "filter" else "")
        script.write_text("#!/bin/sh\n" + body)
        os.chmod(script, 0o755)
        if name in ("pre-commit", "commit-msg", "post-commit"):
            (clone / ".git" / "hooks" / name).write_text(script.read_text())
            os.chmod(clone / ".git" / "hooks" / name, 0o755)
    _sh(clone, "git", "config", "core.fsmonitor", str(clone / ".git" / "evil-fsmonitor"))
    _sh(clone, "git", "config", "filter.evil.clean", str(clone / ".git" / "evil-filter"))
    (clone / ".gitattributes").write_text("* filter=evil\n")


def _product_repo(name):
    from justshipit.settings import settings

    repo = settings().products_dir / name
    repo.mkdir(parents=True)
    _sh(repo, "git", "init", "-q", "-b", "main")
    (repo / "README.md").write_text("demo\n")
    _sh(repo, "git", "add", "-A")
    _sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qm", "init")
    return repo


def _approved_job(s, project, repo_name, status="QUEUED"):
    from justshipit.db.models import AgentRequest, DevJob, Spec

    req = AgentRequest(project_id=project.id, from_agent="product", type="APPROVAL", title="spec", summary="s",
                       rationale="r", status="APPROVED", policy_mode="approve")
    s.add(req)
    s.flush()
    sha = _spec_in_registry({"prd.md": "# PRD\n"})  # every export the reviewers and the CPO read carries the spec
    h = Handoff(spec_id="SPEC-C1", project="Test", title="t", source={"path": "specs/c1", "commit_sha": sha},
                target={"repository": repo_name}, approval={"spec_approved": True, "implementation_authorized": True})
    spec = Spec(spec_key=f"SPEC-{repo_name.upper()}", project_id=project.id, title="t", registry_path="specs/c1",
                approved_commit_sha=sha, handoff=h.model_dump(mode="json"), approval_request_id=req.id,
                status="IN_DEVELOPMENT")
    s.add(spec)
    s.flush()
    job = DevJob(spec_id=spec.id, status=status)
    s.add(job)
    s.commit()
    return job, spec, h


def _review(job, repo, wt, branch) -> str:
    """What run_dev_job does before the review: the branch the reviewer reads, recorded with the review's verdict."""
    from justshipit.engineering import worker

    reviewed = worker._branch_to_review(repo, wt, branch)
    worker._step(job.id, DevJobStatus.REVIEWING, "리뷰 통과: 요구사항을 모두 구현",
                 {"verdict": "pass", "blocking": 0, "coverage": ["REQ-1: 구현"], "commit": reviewed}, say="리뷰를 통과했어요.")
    return reviewed


def _review_and_ship(job, spec, repo, wt, branch, h, test_log="ok"):
    from justshipit.engineering import worker

    worker._ship(job.id, spec.id, repo, branch, _review(job, repo, wt, branch), h, test_log, None, "ko")


def test_shipping_a_clone_never_runs_code_the_engineer_planted_in_it(s, project, tmp_path, monkeypatch):
    import shutil

    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    repo = _product_repo(f"c1-{project.id}")
    branch = "ai/spec-c1-a1"
    wt = tmp_path / "clone"
    _sh(tmp_path, "git", "clone", "-q", "--no-hardlinks", str(repo), str(wt))
    _sh(wt, "git", "checkout", "-qB", branch)
    _sh(wt, "git", "-c", "user.name=e", "-c", "user.email=e@x", "commit", "-q", "--allow-empty", "-m", "feat")
    markers = tmp_path / "markers"
    markers.mkdir()
    _plant(wt, markers)
    (wt / "leftover.txt").write_text("uncommitted work\n")

    # Control: the planted code is live, so plain git in the clone runs it.
    control = tmp_path / "control"
    shutil.copytree(wt, control)
    _sh(control, "git", "add", "-A")
    _sh(control, "git", "-c", "user.name=e", "-c", "user.email=e@x", "commit", "-qm", "probe")
    assert sorted(p.name for p in markers.iterdir()) == ["commit-msg", "filter", "fsmonitor", "post-commit",
                                                        "pre-commit"]
    shutil.rmtree(control)
    for p in markers.iterdir():
        p.unlink()

    job, spec, h = _approved_job(s, project, repo.name, "REVIEWING")
    monkeypatch.setattr(worker, "_containerized", lambda: True)

    _review_and_ship(job, spec, repo, wt, branch, h)

    assert sorted(p.name for p in markers.iterdir()) == []
    s.expire_all()
    assert s.get(DevJob, job.id).status == "DELIVERED"
    assert _sh(repo, "git", "show", f"{branch}:leftover.txt") == "uncommitted work"
    assert _sh(repo, "git", "rev-list", "--count", f"main..{branch}") == "2"


def test_shipping_commits_leftovers_in_a_repo_that_ignores_ship(s, project, tmp_path, monkeypatch):
    """J-1 passed review and then failed to ship: `git add -A -- . :(exclude).ship` exits 1 with "The following
    paths are ignored" when the product repo's .gitignore names .ship, which every repo ensure_product_repo makes
    does."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import specs, worker
    from justshipit.settings import settings

    name = f"c1-ignored-{project.id}"
    specs.ensure_product_repo(name, "main", "t")
    repo = settings().products_dir / name
    assert ".ship/" in (repo / ".gitignore").read_text()
    branch, wt = "ai/spec-c1i-a1", tmp_path / "clone"
    _sh(tmp_path, "git", "clone", "-q", "--no-hardlinks", str(repo), str(wt))
    _sh(wt, "git", "checkout", "-qB", branch)
    (wt / "app.py").write_text("print('built')\n")
    _sh(wt, "git", "add", "app.py")
    _sh(wt, "git", "-c", "user.name=e", "-c", "user.email=e@x", "commit", "-qm", "feat")
    (wt / ".ship" / "spec").mkdir(parents=True)
    (wt / ".ship" / "spec" / "prd.md").write_text("# PRD\n")
    (wt / "leftover.txt").write_text("uncommitted work\n")
    job, spec, h = _approved_job(s, project, name, "REVIEWING")
    monkeypatch.setattr(worker, "_containerized", lambda: True)

    _review_and_ship(job, spec, repo, wt, branch, h)

    s.expire_all()
    assert s.get(DevJob, job.id).status == "DELIVERED", s.get(DevJob, job.id).error
    assert _sh(repo, "git", "show", f"{branch}:leftover.txt") == "uncommitted work"
    assert ".ship" not in _sh(repo, "git", "ls-tree", "-r", "--name-only", branch)


def _failed_while_shipping(s, project, tmp_path, monkeypatch, name):
    """A reviewed branch whose delivery step failed, the way J-1's did."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import specs, worker
    from justshipit.settings import settings

    specs.ensure_product_repo(name, "main", "t")
    repo = settings().products_dir / name
    branch, wt = "ai/spec-c1r-a1", tmp_path / "clone"
    _sh(tmp_path, "git", "clone", "-q", "--no-hardlinks", str(repo), str(wt))
    _sh(wt, "git", "checkout", "-qB", branch)
    (wt / "app.py").write_text("print('built')\n")
    _sh(wt, "git", "add", "app.py")
    _sh(wt, "git", "-c", "user.name=e", "-c", "user.email=e@x", "commit", "-qm", "feat")
    job, spec, h = _approved_job(s, project, name, "REVIEWING")
    job.branch, job.worktree_path, job.attempt = branch, str(wt), 1
    job.steps = [{"at": "t", "status": "QA", "summary": "tests: True", "data": {"log": "12 passed"}}]
    s.commit()
    monkeypatch.setattr(worker, "_containerized", lambda: True)
    reviewed = _review(job, repo, wt, branch)
    real = worker._refuse_attributes

    def git_step_fails(*a):
        raise RuntimeError("git: could not read the attributes")

    monkeypatch.setattr(worker, "_refuse_attributes", git_step_fails)
    with pytest.raises(RuntimeError) as failed:
        worker._ship(job.id, spec.id, repo, branch, reviewed, h, "12 passed", None, "ko")
    worker._record_failure(job.id, worker._cause(failed.value, "ko"))  # what run_dev_job does with it
    monkeypatch.setattr(worker, "_refuse_attributes", real)
    s.expire_all()
    assert s.get(DevJob, job.id).status == "FAILED"
    return job, spec, repo, branch


def test_a_file_the_clone_gets_after_the_review_is_never_delivered(s, project, tmp_path, monkeypatch):
    """L6: the ship committed whatever the work tree held when it ran, after the review had read the branch."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import specs, worker
    from justshipit.settings import settings

    name = f"l6-late-{project.id}"
    specs.ensure_product_repo(name, "main", "t")
    repo = settings().products_dir / name
    branch, wt = "ai/spec-l6-a1", tmp_path / "clone"
    _sh(tmp_path, "git", "clone", "-q", "--no-hardlinks", str(repo), str(wt))
    _sh(wt, "git", "checkout", "-qB", branch)
    (wt / "app.py").write_text("print('built')\n")
    _sh(wt, "git", "add", "app.py")
    _sh(wt, "git", "-c", "user.name=e", "-c", "user.email=e@x", "commit", "-qm", "feat")
    (wt / "leftover.txt").write_text("left before the review\n")
    job, spec, h = _approved_job(s, project, name, "REVIEWING")
    monkeypatch.setattr(worker, "_containerized", lambda: True)
    reviewed = _review(job, repo, wt, branch)

    (wt / "late.txt").write_text("written after the review\n")
    worker._ship(job.id, spec.id, repo, branch, reviewed, h, "ok", None, "ko")

    s.expire_all()
    assert s.get(DevJob, job.id).status == "DELIVERED", s.get(DevJob, job.id).error
    assert _sh(repo, "git", "rev-parse", branch) == reviewed
    assert _sh(repo, "git", "show", f"{branch}:leftover.txt") == "left before the review"
    assert "late.txt" not in _sh(repo, "git", "ls-tree", "-r", "--name-only", branch)


def test_delivering_again_ships_only_the_reviewed_commit_whatever_the_clone_holds_now(s, project, tmp_path,
                                                                                     monkeypatch):
    """L6: a redelivery fetched the surviving clone and committed its leftovers, so a file the review never read
    reached the PR."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    job, _, repo, branch = _failed_while_shipping(s, project, tmp_path, monkeypatch, f"l6-redeliver-{project.id}")
    reviewed = _sh(repo, "git", "rev-parse", branch)
    wt = tmp_path / "clone"
    (wt / "late.txt").write_text("unreviewed\n")
    _sh(wt, "git", "add", "late.txt")
    _sh(wt, "git", "-c", "user.name=e", "-c", "user.email=e@x", "commit", "-qm", "late")
    (wt / "loose.txt").write_text("unreviewed, uncommitted\n")

    worker.deliver_again(job.id, "ko")
    worker.run_delivery(job.id)

    s.expire_all()
    assert s.get(DevJob, job.id).status == "DELIVERED", s.get(DevJob, job.id).error
    assert _sh(repo, "git", "rev-parse", branch) == reviewed
    files = _sh(repo, "git", "ls-tree", "-r", "--name-only", branch).split()
    assert "late.txt" not in files and "loose.txt" not in files


def test_a_branch_that_moved_after_the_review_is_refused_at_delivery(s, project, tmp_path, monkeypatch):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    job, _, repo, branch = _failed_while_shipping(s, project, tmp_path, monkeypatch, f"l6-moved-{project.id}")
    reviewed = _sh(repo, "git", "rev-parse", branch)
    _sh(repo, "git", "branch", "-f", branch, "main")

    assert worker.owner_actions(s, s.get(DevJob, job.id)) == []
    with pytest.raises(ValueError, match="리뷰한 커밋"):
        worker.deliver_again(job.id, "ko")

    _sh(repo, "git", "branch", "-f", branch, reviewed)
    worker.deliver_again(job.id, "ko")
    _sh(repo, "git", "branch", "-f", branch, "main")  # moved again while the delivery waited in the queue
    worker.run_delivery(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "FAILED" and "reviewed commit" in done.steps[-1]["summary"], done.steps[-1]
    assert done.steps[-1]["say"] == {"key": "build.step.deliver_again_refused"}
    assert _sh(repo, "git", "rev-parse", branch) != reviewed


def test_a_run_from_before_reviews_recorded_their_commit_is_delivered_again_only_without_a_late_commit(
        s, project, tmp_path, monkeypatch):
    """Such a run committed leftovers only after its review, so its branch is the reviewed one until a leftovers
    commit sits on it (J-1 on the live console stopped before committing any)."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    job, _, repo, branch = _failed_while_shipping(s, project, tmp_path, monkeypatch, f"l6-old-{project.id}")
    old = s.get(DevJob, job.id)
    old.steps = [{**x, "data": {k: v for k, v in (x.get("data") or {}).items() if k != "commit"}} for x in old.steps]
    s.commit()
    assert worker.owner_actions(s, s.get(DevJob, job.id)) == ["deliver_again"]

    (tmp_path / "clone" / "late.txt").write_text("committed after the review\n")
    worker._commit_leftovers(repo, tmp_path / "clone", branch)

    assert worker.owner_actions(s, s.get(DevJob, job.id)) == []
    with pytest.raises(ValueError, match="리뷰한 커밋"):
        worker.deliver_again(job.id, "ko")


def test_a_job_that_failed_while_shipping_is_delivered_again_without_rebuilding(s, project, tmp_path, monkeypatch):
    from sqlalchemy import select

    from justshipit.db.models import DevJob, Job
    from justshipit.engineering import worker
    from justshipit.services import registry

    job, spec, repo, branch = _failed_while_shipping(s, project, tmp_path, monkeypatch, f"c1-redeliver-{project.id}")
    assert worker.owner_actions(s, s.get(DevJob, job.id)) == ["deliver_again"]
    monkeypatch.setattr(worker, "run_agent", lambda *a, **kw: pytest.fail("a delivery runs no model call"))

    worker.deliver_again(job.id, "ko")
    s.expire_all()
    queued = s.scalar(select(Job).where(Job.kind == "dev_deliver", Job.payload["dev_job_id"].as_integer() == job.id))
    assert queued.payload == {"dev_job_id": job.id} and queued.lock_key == f"dev_job:{job.id}"
    assert worker.owner_actions(s, s.get(DevJob, job.id)) == [], "a queued delivery is not offered twice"
    worker.run_delivery(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED", done.error
    assert done.error is None and done.attempt == 1
    assert _sh(repo, "git", "show", f"{branch}:app.py") == "print('built')"
    doc = registry.read(done.result["delivery_doc"])
    assert "리뷰 통과: 요구사항을 모두 구현" in doc and "REQ-1: 구현" in doc and "12 passed" in doc


def test_only_the_latest_attempt_that_stopped_while_shipping_is_delivered_again(s, project, tmp_path, monkeypatch):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    job, spec, _, _ = _failed_while_shipping(s, project, tmp_path, monkeypatch, f"c1-newer-{project.id}")
    s.add(DevJob(spec_id=spec.id, status="QUEUED", attempt=2))
    s.commit()
    assert worker.owner_actions(s, s.get(DevJob, job.id)) == []
    with pytest.raises(ValueError, match="새로 만든 작업"):
        worker.deliver_again(job.id, "ko")


def test_a_job_that_failed_before_shipping_is_not_offered_delivery(s, project):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    job, _, _ = _approved_job(s, project, f"c1-early-{project.id}", "FAILED")
    job.branch = "ai/x-a1"
    job.steps = [{"at": "t", "status": "IMPLEMENTING", "summary": "implementing"},
                 {"at": "t", "status": "FAILED", "summary": "engineer step failed"}]
    s.commit()
    assert "deliver_again" not in worker.owner_actions(s, s.get(DevJob, job.id))


def test_shipping_a_host_worktree_commits_leftovers_and_leaves_it_clean(s, project, tmp_path, monkeypatch):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    repo = _product_repo(f"c1-host-{project.id}")
    branch, wt = "ai/spec-c1h-a1", tmp_path / "wt"
    _sh(repo, "git", "worktree", "add", "-q", "-B", branch, str(wt), "main")
    (wt / "leftover.txt").write_text("uncommitted work\n")
    (wt / "README.md").write_text("changed\n")
    job, spec, h = _approved_job(s, project, repo.name, "REVIEWING")
    monkeypatch.setattr(worker, "_containerized", lambda: False)

    _review_and_ship(job, spec, repo, wt, branch, h)

    s.expire_all()
    assert s.get(DevJob, job.id).status == "DELIVERED"
    assert _sh(repo, "git", "show", f"{branch}:leftover.txt") == "uncommitted work"
    assert _sh(repo, "git", "show", f"{branch}:README.md") == "changed"
    assert _sh(wt, "git", "status", "--porcelain") == ""


def test_tests_never_fall_back_to_the_host_shell(tmp_path, monkeypatch):
    from justshipit.engineering import worker
    from justshipit.sandbox import docker

    monkeypatch.setattr(docker, "docker_ok", lambda: False)
    with pytest.raises(docker.SandboxUnavailable):
        worker.run_tests(tmp_path, "touch ran-on-host", image="python:3.13-slim")
    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    with pytest.raises(docker.SandboxUnavailable):
        worker.run_tests(tmp_path, "touch ran-on-host", image=None)
    assert not (tmp_path / "ran-on-host").exists()


def test_test_container_is_locked_down(tmp_path, monkeypatch):
    import os
    import subprocess

    from justshipit.engineering import worker
    from justshipit.sandbox import docker

    runs: list[list[str]] = []

    def fake_run(args, **kw):
        if args[:2] == ["docker", "run"]:
            runs.append(args)
        if args[:2] == ["docker", "inspect"]:  # the egress proxy is up
            return subprocess.CompletedProcess(args, 0, "true\n", "")
        return subprocess.CompletedProcess(args, 0, b"passed", b"")

    monkeypatch.setattr(docker.subprocess, "run", fake_run)
    ok, log = worker.run_tests(tmp_path, "pytest -q", image="python:3.13-slim")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "ci-setup.sh").write_text("pip install -r requirements.txt\n")
    _, online_log = worker.run_tests(tmp_path, "pytest -q", image="python:3.13-slim")

    assert ok is True and "passed" in log
    offline, online = (" ".join(a) for a in runs)
    for args in (offline, online):
        assert "--cap-drop ALL" in args and "--security-opt no-new-privileges" in args and "--pids-limit" in args
        assert f"-v {tmp_path.resolve()}:/work " in args
        # Not root: a root container leaves root-owned files in the worktree and gives a test full image rights.
        assert f"--user {os.getuid()}:{os.getgid()}" in args
        assert "-e HOME " in args and "-e HOME=" not in args, "values never go on the command line"
        assert 'export PATH="$HOME/.local/bin:$PATH"' in args, "user-level installs from ci-setup stay runnable"
    assert "--network none" in offline and "network=none" in log
    assert f"--network {docker.SANDBOX_NETWORK}" in online and "network=sandbox" in online_log, \
        "a setup script is the one way to a network, and that network leads only to the egress proxy"
    assert "-e HTTPS_PROXY " in online and "-e HTTPS_PROXY " not in offline


def test_test_container_runs_unprivileged_on_windows(tmp_path, monkeypatch):
    import os
    import subprocess

    from justshipit.engineering import worker
    from justshipit.sandbox import docker

    for name in ("getuid", "getgid"):
        monkeypatch.delattr(os, name)
    runs: list[str] = []

    def fake_run(args, **kw):
        if args[:2] == ["docker", "run"]:
            runs.append(" ".join(args))
        return subprocess.CompletedProcess(args, 0, b"passed", b"")

    monkeypatch.setattr(docker.subprocess, "run", fake_run)
    ok, _ = worker.run_tests(tmp_path, "pytest -q", image="python:3.13-slim")
    assert ok is True
    assert "--user 1000:1000" in runs[0] and "--cap-drop ALL" in runs[0], "never root, even without a host uid"


def _spec_in_registry(files: dict[str, str]) -> str:
    from justshipit.services import registry
    return registry.write_files({f"specs/c1/{name}": body for name, body in files.items()}, "spec")


def test_refreshing_a_clone_never_writes_through_links_the_engineer_planted(s, project, tmp_path, monkeypatch):
    from justshipit.engineering import worker
    from justshipit.settings import settings

    repo = _product_repo(f"m1-{project.id}")
    sha = _spec_in_registry({"prd.md": "# PRD v1\n", "clarifications.md": "# Q\n"})
    job, spec, h = _approved_job(s, project, repo.name)
    h.source.commit_sha = spec.approved_commit_sha = sha
    spec.handoff = h.model_dump(mode="json")
    s.commit()
    monkeypatch.setattr(worker, "_containerized", lambda: True)
    monkeypatch.setattr(worker, "company_config", lambda: {"engineering": {"isolation": "container",
                                                                           "worktrees_dir": "wt"}})
    monkeypatch.setattr(worker, "settings", lambda: settings().model_copy(update={"root": tmp_path}))
    _, wt, _, _ = worker.prepare_worktree(spec, h, 1)
    assert (wt / ".ship" / "spec" / "prd.md").read_text().strip() == "# PRD v1"

    # The engineer ran: it replaced the host-written paths with links to the owner's files.
    outside = {name: tmp_path / name for name in ("bashrc", "exclude")}
    for p in outside.values():
        p.write_text("owner's\n")
    (wt / ".ship" / "spec" / "prd.md").unlink()
    (wt / ".ship" / "spec" / "prd.md").symlink_to(outside["bashrc"])
    (wt / ".git" / "info" / "exclude").unlink()
    (wt / ".git" / "info" / "exclude").symlink_to(outside["exclude"])

    refused = None
    try:
        worker.prepare_worktree(spec, h, 1)
    except RuntimeError as exc:
        refused = worker.words(exc, "ko")
    assert {name: p.read_text() for name, p in outside.items()} == {name: "owner's\n" for name in outside}
    assert refused and re.search("[가-힣]", refused), refused

    (wt / ".git" / "info" / "exclude").unlink()
    (wt / ".ship" / "spec").rename(wt / ".ship" / "real")
    (wt / ".ship" / "spec").symlink_to(tmp_path)
    with pytest.raises(RuntimeError, match="plain file"):
        worker.prepare_worktree(spec, h, 1)
    assert not (tmp_path / "prd.md").exists() and not (tmp_path / "clarifications.md").exists()


def test_independent_review_runs_in_a_host_export_without_the_engineers_git(s, project, tmp_path, monkeypatch):
    import subprocess

    from justshipit.engineering import worker
    from justshipit.sandbox import docker
    from justshipit.settings import settings

    repo = _product_repo(f"m2-{project.id}")
    sha = _spec_in_registry({"prd.md": "# PRD\n"})
    job, spec, h = _approved_job(s, project, repo.name)
    h.source.commit_sha = spec.approved_commit_sha = sha
    spec.handoff = h.model_dump(mode="json")
    s.commit()
    monkeypatch.setattr(worker, "_containerized", lambda: True)
    monkeypatch.setattr(worker, "company_config", lambda: {"engineering": {
        "isolation": "container", "worktrees_dir": "wt", "max_fix_iterations": 0, "sandbox": {"image": "img"}}})
    monkeypatch.setattr(worker, "settings", lambda: settings().model_copy(update={"root": tmp_path}))
    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    monkeypatch.setattr(docker, "image_present", lambda image: True)
    monkeypatch.setattr(worker, "host_git_refusal", lambda lang: None)
    monkeypatch.setattr(worker, "run_tests", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(worker, "last_served", lambda: Served("claude", "sonnet"))
    monkeypatch.setattr(worker, "_ship", lambda *a, **kw: None)
    markers = tmp_path / "markers"
    markers.mkdir()
    seen: dict = {}

    def fake_run_agent(agent_id, purpose, prompt, model, **kw):
        wt = kw["cwd"]
        if agent_id == "engineer" and purpose == "dev" and not (wt / "feature.py").exists():
            (wt / "feature.py").write_text("print('hi')\n")
            _sh(wt, "git", "add", "feature.py")
            _sh(wt, "git", "-c", "user.name=e", "-c", "user.email=e@x", "commit", "-qm", "feat")
            _plant(wt, markers)
        if agent_id == "eng_reviewer":
            seen["cwd"] = wt
            seen["files"] = sorted(p.name for p in wt.iterdir())
            seen["spec"] = (wt / ".ship" / "spec" / "prd.md").read_text().strip()
            probe = subprocess.run(["git", "status", "--porcelain"], cwd=wt, capture_output=True, text=True)
            seen["git_status"] = probe.returncode, probe.stdout
        return _fake_output(model)

    monkeypatch.setattr(worker, "run_agent", fake_run_agent)

    worker.run_dev_job(job.id)

    clone = tmp_path / "wt" / repo.name / f"{spec.spec_key}-a1"
    assert seen["cwd"] != clone and not seen["cwd"].is_relative_to(clone)
    # The engineer's uncommitted .gitattributes is committed before the review, so the reviewer reads what ships.
    assert seen["files"] == [".gitattributes", ".ship", "README.md", "feature.py"]
    assert seen["spec"] == "# PRD"
    assert seen["git_status"][0] != 0 or "fatal" in seen["git_status"][1], "no repository for planted config to act in"
    assert sorted(p.name for p in markers.iterdir()) == []


def test_the_reviewer_gets_the_whole_diff_and_the_specs_sources_and_a_cut_says_where_the_rest_is(
        s, project, tmp_path, monkeypatch):
    """The reviewer wrote "전달된 diff는 … 잘렸습니다": the prompt carried the first 60,000 characters with no word that
    more existed. The spec's sources (the task results the CPO wrote it from) reached nobody."""
    from justshipit.engineering import worker
    from justshipit.sandbox import docker
    from justshipit.settings import settings

    repo = _product_repo(f"a6-{project.id}")
    table = "| 원두 | 재고 |\n" + "".join(f"| bean-{i} | {i} |\n" for i in range(40))
    sha = _spec_in_registry({"prd.md": "# PRD\n", "sources/T-30.md": table})
    job, spec, h = _approved_job(s, project, repo.name)
    h.source.commit_sha = spec.approved_commit_sha = sha
    spec.handoff = h.model_dump(mode="json")
    s.commit()
    monkeypatch.setattr(worker, "_containerized", lambda: True)
    monkeypatch.setattr(worker, "company_config", lambda: {"engineering": {
        "isolation": "container", "worktrees_dir": "wt", "max_fix_iterations": 0, "sandbox": {"image": "img"}}})
    monkeypatch.setattr(worker, "settings", lambda: settings().model_copy(update={"root": tmp_path}))
    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    monkeypatch.setattr(docker, "image_present", lambda image: True)
    monkeypatch.setattr(worker, "host_git_refusal", lambda lang: None)
    monkeypatch.setattr(worker, "_REVIEW_DIFF_CHARS", 2000)
    monkeypatch.setattr(worker, "run_tests", lambda *a, **kw: (True, "x" * 9000 + "\n12 passed"))
    monkeypatch.setattr(worker, "last_served", lambda: Served("claude", "sonnet"))
    monkeypatch.setattr(worker, "_ship", lambda *a, **kw: None)
    seen: dict = {}

    def fake_run_agent(agent_id, purpose, prompt, model, **kw):
        wt = kw["cwd"]
        if agent_id == "engineer" and purpose == "dev" and not (wt / "big.py").exists():
            (wt / "big.py").write_text("".join(f"line_{i} = {i}\n" for i in range(2000)))
            _sh(wt, "git", "add", "big.py")
            _sh(wt, "git", "-c", "user.name=e", "-c", "user.email=e@x", "commit", "-qm", "feat")
        if agent_id == "eng_reviewer":
            seen["prompt"] = prompt
            seen["diff"] = (wt / ".ship" / "review" / "diff.patch").read_text()
            seen["log"] = (wt / ".ship" / "review" / "tests.log").read_text()
            seen["source"] = (wt / ".ship" / "spec" / "sources" / "T-30.md").read_text()
        return _fake_output(model)

    monkeypatch.setattr(worker, "run_agent", fake_run_agent)

    worker.run_dev_job(job.id)

    assert "line_1999 = 1999" in seen["diff"] and "line_1999" not in seen["prompt"]
    assert re.search(r"…\(showing the first 2,000 of [\d,]+ characters; the last [\d,]+ are left out\. The whole text is in \.ship/review/diff\.patch\.\)",
                     seen["prompt"])
    assert "big.py" in seen["prompt"].split("## 변경분")[0], "the --stat names every changed file"
    assert seen["log"].startswith("x" * 9000) and "The whole text is in .ship/review/tests.log." in seen["prompt"]
    assert seen["source"] == table.strip(), "the registry keeps no trailing newline"


def _job_run_by(s, project, tmp_path, monkeypatch, repo, engineer):
    """A dev job on `repo` whose engineer step is `engineer(clone)`; every agent is stubbed, host git is real.
    Returns the job, its spec and what the independent reviewer saw in its export."""
    from justshipit.engineering import worker
    from justshipit.sandbox import docker
    from justshipit.settings import settings

    sha = _spec_in_registry({"prd.md": "# PRD\n"})
    job, spec, h = _approved_job(s, project, repo.name)
    h.source.commit_sha = spec.approved_commit_sha = sha
    spec.handoff = h.model_dump(mode="json")
    s.commit()
    monkeypatch.setattr(worker, "_containerized", lambda: True)
    monkeypatch.setattr(worker, "company_config", lambda: {"engineering": {
        "isolation": "container", "worktrees_dir": "wt", "max_fix_iterations": 0, "sandbox": {"image": "img"}}})
    monkeypatch.setattr(worker, "settings", lambda: settings().model_copy(update={"root": tmp_path}))
    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    monkeypatch.setattr(docker, "image_present", lambda image: True)
    monkeypatch.setattr(worker, "host_git_refusal", lambda lang: None)
    monkeypatch.setattr(worker, "run_tests", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(worker, "last_served", lambda: Served("claude", "sonnet"))
    seen: dict = {}

    def fake_run_agent(agent_id, purpose, prompt, model, **kw):
        if agent_id == "engineer" and purpose == "dev" and "engineered" not in seen:
            seen["engineered"] = True
            engineer(kw["cwd"])
        if agent_id == "eng_reviewer":
            seen["review"] = {p.name: (p.read_text(errors="replace") if p.is_file() else None)
                              for p in kw["cwd"].iterdir()}
        return _fake_output(model)

    monkeypatch.setattr(worker, "run_agent", fake_run_agent)
    return job, spec, seen


def _engineer_commit(clone, message="feat"):
    _sh(clone, "git", "add", "-A", "-f", ".")
    _sh(clone, "git", "-c", "user.name=e", "-c", "user.email=e@x", "commit", "-qm", message)


def test_branch_attributes_never_select_a_host_filter(s, project, tmp_path, monkeypatch):
    """The product repo's own config names filters (git lfs install writes exactly this); the branch's
    .gitattributes must not make the host run them on the engineer's files, at review or at ship."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    repo = _product_repo(f"s5-attr-{project.id}")
    markers = tmp_path / "markers"
    markers.mkdir()
    _sh(repo, "git", "config", "filter.owner.smudge", f"sh -c 'touch {markers}/smudge; cat'")
    _sh(repo, "git", "config", "filter.owner.clean", f"sh -c 'touch {markers}/clean; cat'")

    def engineer(clone):
        (clone / ".gitattributes").write_text("* filter=owner\n")
        (clone / "feature.py").write_text("print('hi')\n")
        _engineer_commit(clone)
        (clone / "leftover.txt").write_text("late\n")

    job, _, seen = _job_run_by(s, project, tmp_path, monkeypatch, repo, engineer)

    worker.run_dev_job(job.id)

    assert sorted(p.name for p in markers.iterdir()) == []
    assert seen["review"]["feature.py"] == "print('hi')\n"
    s.expire_all()
    assert s.get(DevJob, job.id).status == "DELIVERED"
    assert _sh(repo, "git", "show", f"{job.branch}:leftover.txt") == "late"


def _lfs_listener(hits: list):
    """A git-lfs server on a free loopback port that records every request and serves one 5-byte object."""
    import http.server
    import json
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(f"{self.command} {self.path}")
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            objects = [{"oid": o["oid"], "size": o["size"], "actions": {"download": {
                "href": f"http://127.0.0.1:{self.server.server_address[1]}/object"}}} for o in body["objects"]]
            self._reply(json.dumps({"transfer": "basic", "objects": objects}).encode(), "application/vnd.git-lfs+json")

        def do_GET(self):
            hits.append(f"{self.command} {self.path}")
            self._reply(b"aaaa\n", "application/octet-stream")

        def _reply(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.mark.skipif(shutil.which("git-lfs") is None, reason="git-lfs is not installed on this host")
def test_a_branch_lfsconfig_never_makes_the_host_fetch_from_the_engineers_url(s, project, tmp_path, monkeypatch):
    import hashlib
    import os
    import subprocess

    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    hits: list[str] = []
    srv = _lfs_listener(hits)
    port = srv.server_address[1]
    repo = _product_repo(f"s5-lfs-{project.id}")
    for key, value in (("clean", "git-lfs clean -- %f"), ("smudge", "git-lfs smudge -- %f"),
                       ("process", "git-lfs filter-process"), ("required", "true")):
        _sh(repo, "git", "config", f"filter.lfs.{key}", value)
    pointer = f"version https://git-lfs.github.com/spec/v1\noid sha256:{hashlib.sha256(b'aaaa\n').hexdigest()}\nsize 5\n"

    def engineer(clone):
        (clone / ".gitattributes").write_text("*.bin filter=lfs -text\n")
        (clone / ".lfsconfig").write_text(f"[lfs]\n\turl = http://127.0.0.1:{port}/engineer\n")
        (clone / "data.bin").write_text(pointer)
        blob = _sh(clone, "git", "hash-object", "-w", "--no-filters", "data.bin")
        _sh(clone, "git", "update-index", "--add", "--cacheinfo", f"100644,{blob},data.bin")
        _engineer_commit(clone)

    job, _, seen = _job_run_by(s, project, tmp_path, monkeypatch, repo, engineer)

    worker.run_dev_job(job.id)

    assert hits == []
    assert seen["review"]["data.bin"] == pointer, "the reviewer sees the pointer, not an object fetched for it"
    s.expire_all()
    assert s.get(DevJob, job.id).status == "DELIVERED"

    # Control: plain git with the repo's lfs config reads the branch's .lfsconfig and fetches from it.
    export = tmp_path / "control"
    export.mkdir()
    env = {**os.environ, "GIT_INDEX_FILE": str(tmp_path / "control-index")}
    subprocess.run(["git", "read-tree", job.branch], cwd=repo, env=env, check=True)
    subprocess.run(["git", f"--work-tree={export}", "checkout-index", "-a"], cwd=repo, env=env, check=True,
                   timeout=60, capture_output=True)
    srv.shutdown()
    assert hits == ["POST /engineer/objects/batch", "GET /object"]
    assert (export / "data.bin").read_text() == "aaaa\n"


def test_host_git_never_lazy_fetches_from_the_clone(tmp_path, monkeypatch):
    import subprocess

    from justshipit.engineering import worker

    envs: list[dict] = []

    class FakeGit:
        returncode = 0

        def __init__(self, args, **kw):
            envs.append(kw.get("env") or {})

        def communicate(self, timeout=None):
            return "", ""

    monkeypatch.setattr(subprocess, "Popen", FakeGit)
    monkeypatch.setattr(worker, "_containerized", lambda: True)
    worker._fetch_branch(tmp_path / "repo", tmp_path / "clone", "ai/x")
    worker._git(tmp_path / "repo", "rev-parse", "HEAD", env={"GIT_INDEX_FILE": "idx"})
    assert [e.get("GIT_NO_LAZY_FETCH") for e in envs] == ["1", "1"]
    assert envs[1]["GIT_INDEX_FILE"] == "idx"


def _host_git(bin_dir, body: str, version: str = "2.43.0") -> None:
    """A git on PATH that reports `version` and otherwise does `body` (sh, "$@" is git's argv)."""
    bin_dir.mkdir(exist_ok=True)
    real = shutil.which("git", path=os.defpath)
    script = bin_dir / "git"
    script.write_text(f'#!/bin/sh\nREAL={real}\nif [ "$1" = --version ]; then echo "git version {version}"; exit 0; fi\n'
                      + body + "\n")
    script.chmod(0o755)


@pytest.fixture
def git_on_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "gitbin"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


def test_this_hosts_git_passes_the_lazy_fetch_probe():
    from justshipit.engineering import worker

    # This box's git 2.43.0 is Ubuntu's, with the CVE-2024-32465 fix backported: a version check would refuse it.
    assert worker.host_git_refusal("ko") is None


def test_a_host_git_that_runs_a_planted_lazy_fetch_stops_container_jobs_at_intake(s, project, git_on_path,
                                                                                  monkeypatch):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker
    from justshipit.sandbox import docker

    # A git without the fix ignores GIT_NO_LAZY_FETCH; on this git, setting it to 0 brings that behaviour back.
    _host_git(git_on_path, 'GIT_NO_LAZY_FETCH=0 exec "$REAL" "$@"')
    refusal = worker.host_git_refusal("ko")
    assert refusal and "git을 2.45.1 이상으로" in refusal and "CVE-2024-32465" in refusal, refusal

    job = _queued_job(s, project)
    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    monkeypatch.setattr(docker, "image_present", lambda name: True)
    monkeypatch.setattr(worker, "prepare_worktree", lambda *a, **kw: pytest.fail("made a worktree"))
    worker.run_dev_job(job.id)
    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "FAILED" and done.error == refusal


def test_a_host_git_too_old_for_attr_source_is_refused_by_name(git_on_path):
    from justshipit.engineering import worker

    _host_git(git_on_path, 'case "$1" in --attr-source=*) echo "unknown option: $1" >&2; exit 129;; esac\n'
                           'exec "$REAL" "$@"', version="2.39.2")
    refusal = worker.host_git_refusal("ko")
    assert refusal and "--attr-source" in refusal and "2.39.2" in refusal and "git을 2.45.1 이상으로" in refusal, refusal


def test_a_host_git_probe_that_errors_is_a_refusal_that_says_why(git_on_path, monkeypatch):
    from justshipit.engineering import worker

    _host_git(git_on_path, 'echo "fatal: disk on fire" >&2; exit 128')
    refusal = worker.host_git_refusal("ko")
    assert refusal and "disk on fire" in refusal and "git을 2.45.1 이상으로" in refusal, refusal
    monkeypatch.setenv("PATH", str(git_on_path / "nowhere"))
    assert "찾지 못했어요" in worker.host_git_refusal("ko")


def test_the_lazy_fetch_probe_runs_once_per_git_binary_and_version(git_on_path):
    from justshipit.engineering import worker

    calls = git_on_path / "calls"
    _host_git(git_on_path, f'echo "$1" >> {calls}\nexec "$REAL" "$@"', version="2.43.0.probe-a")
    assert worker.host_git_refusal("ko") is None
    probed = calls.read_text()
    assert worker.host_git_refusal("ko") is None and calls.read_text() == probed, "cached"
    _host_git(git_on_path, f'echo "$1" >> {calls}\nexec "$REAL" "$@"', version="2.43.0.probe-b")
    worker.host_git_refusal("ko")
    assert len(calls.read_text()) > len(probed), "a new git version is probed again"


@pytest.mark.parametrize("command", ["init", "fetch"])
def test_a_probe_that_could_not_run_is_a_refusal_that_is_tried_again(git_on_path, command):
    """A one-off error (Defender holding a temp file, a full disk) must not refuse every container job until the
    worker restarts. A fetch that failed for any reason but disabled lazy fetch proves nothing, so it refuses."""
    from justshipit.engineering import worker

    flag = git_on_path / "fail-once"
    _host_git(git_on_path, f'case " $* " in *" {command} "*) if [ -f {flag} ]; then rm -f {flag}; '
                           'echo "fatal: transient" >&2; exit 128; fi;; esac\nexec "$REAL" "$@"',
              version=f"2.43.0.s8-{command}")
    flag.write_text("x")

    first = worker.host_git_refusal("ko")
    assert first and "transient" in first and "git을 2.45.1 이상으로" in first, first
    assert not flag.exists()
    assert worker.host_git_refusal("ko") is None, "probed again once the error cleared"


def _owner_hook(repo, name: str, marker) -> None:
    import os
    hook = repo / ".git" / "hooks" / name
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    os.chmod(hook, 0o755)


def test_merging_an_approved_branch_runs_nothing_from_the_branch(s, project, tmp_path):
    """husky and .githooks keep hooks in the tree (core.hooksPath inside the work tree), and git fills the
    work tree with the branch before pre-merge-commit and post-merge run. The merge runs no hook and no
    filter at all; the owner's own hooks run when the owner merges by hand."""
    from datetime import datetime, timedelta, timezone

    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    repo = _product_repo(f"l5-merge-{project.id}")
    markers = tmp_path / "markers"
    markers.mkdir()
    _sh(repo, "git", "config", "core.hooksPath", ".githooks")
    _sh(repo, "git", "config", "filter.owner.smudge", f"sh -c 'touch {markers}/smudge; cat'")
    _owner_hook(repo, "post-merge", markers / "owner-post-merge")
    branch = "ai/spec-l5-a1"
    _sh(repo, "git", "checkout", "-qb", branch)
    (repo / ".githooks").mkdir()
    for name in ("pre-merge-commit", "post-merge"):
        (repo / ".githooks" / name).write_text(f"#!/bin/sh\ntouch '{markers / name}'\n")
        (repo / ".githooks" / name).chmod(0o755)
    (repo / ".gitattributes").write_text("* filter=owner\n")
    (repo / "feature.py").write_text("print('hi')\n")
    _engineer_commit(repo)
    _sh(repo, "git", "checkout", "-q", "main")
    assert sorted(p.name for p in markers.iterdir()) == []
    job, spec, _ = _approved_job(s, project, repo.name, "DELIVERED")
    job.branch = branch
    s.add(Grant(request_id=spec.approval_request_id, project_id=project.id, action_kind="dev.merge",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    s.commit()

    worker.merge(job.id)

    s.expire_all()
    assert s.get(DevJob, job.id).status == "MERGED"
    assert _sh(repo, "git", "show", "main:feature.py") == "print('hi')"
    assert sorted(p.name for p in markers.iterdir()) == []


def _merge_asked_again(s, job) -> list[str]:
    """The failure each pending merge re-approval for the job carries: the owner's way on after a merge that
    did not land, since a delivered job keeps its status and gets no new attempt. It leads the summary; where the
    branch is follows it."""
    from justshipit.db.models import AgentRequest

    return [r.summary.rsplit("\n\n", 1)[0] for r in s.query(AgentRequest).filter_by(status="PENDING", action_kind="dev.merge")
            if r.action_params.get("dev_job_id") == job.id and r.title.startswith("머지 다시 승인")]


def test_a_merge_that_conflicts_is_recorded_undone_and_spends_no_grant(s, project):
    from datetime import datetime, timedelta, timezone

    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    repo = _product_repo(f"r6-conflict-{project.id}")
    branch = "ai/spec-r6-a1"
    _sh(repo, "git", "checkout", "-qb", branch)
    (repo / "README.md").write_text("engineer\n")
    (repo / ".env").write_text("ENGINEER=1\n")
    _engineer_commit(repo)
    _sh(repo, "git", "checkout", "-q", "main")
    (repo / "README.md").write_text("owner\n")
    (repo / ".gitignore").write_text(".env\n")
    _sh(repo, "git", "add", ".gitignore")
    _sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qam", "owner edit")
    (repo / ".env").write_text("OWNER_SECRET=1\n")
    job, spec, _ = _approved_job(s, project, repo.name)
    job.branch, job.status = branch, "DELIVERED"
    grant = Grant(request_id=spec.approval_request_id, project_id=project.id, action_kind="dev.merge",
                  expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    s.add(grant)
    s.commit()

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED" and "README.md" in done.error and re.search("[가-힣]", done.error), done.error
    assert _merge_asked_again(s, job) == [done.error]
    assert s.get(Grant, grant.id).uses == 0, "a merge that did not happen spends no grant"
    assert _sh(repo, "git", "status", "--porcelain") == "", "the owner's checkout is not left mid-merge"
    assert _sh(repo, "git", "show", "main:README.md") == "owner"
    assert (repo / ".env").read_text() == "OWNER_SECRET=1\n", "the owner's ignored file survives the conflict"


@pytest.mark.parametrize("raised", ["quota", "unexpected"])
def test_a_failed_acceptance_is_recorded_on_the_dev_job(s, project, monkeypatch, raised):
    from justshipit.agents.executor import AgentFailed
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    repo = _product_repo(f"r6-accept-{raised}-{project.id}")
    job, _, _ = _approved_job(s, project, repo.name)
    job.branch, job.status = "main", "DELIVERED"
    s.commit()
    monkeypatch.setattr(worker.registry, "read_at", lambda *a: "- 기준")
    reason = {"quota": AgentFailed("all providers exhausted"), "unexpected": KeyError("criteria_results")}[raised]

    def fail(*a, **kw):
        raise reason

    monkeypatch.setattr(worker, "run_agent", fail)

    worker.acceptance(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED" and "완료 기준 확인이 실패했어요" in done.error and str(reason) in done.error, done.error


def test_shipping_pushes_the_branch_without_running_hooks(s, project, tmp_path, monkeypatch):
    import os

    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    repo = _product_repo(f"l5-push-{project.id}")
    origin = tmp_path / "origin.git"
    _sh(tmp_path, "git", "init", "-q", "--bare", str(origin))
    _sh(repo, "git", "remote", "add", "origin", str(origin))
    _sh(repo, "git", "push", "-q", "origin", "main")
    branch, wt = "ai/spec-l5p-a1", tmp_path / "wt"
    _sh(repo, "git", "worktree", "add", "-q", "-B", branch, str(wt), "main")
    (wt / "feature.py").write_text("print('hi')\n")
    _owner_hook(repo, "pre-push", tmp_path / "owner-pre-push")
    gh = tmp_path / "bin"
    gh.mkdir()
    (gh / "gh").write_text("#!/bin/sh\necho https://github.com/o/r/pull/7\n")
    os.chmod(gh / "gh", 0o755)
    monkeypatch.setenv("PATH", f"{gh}{os.pathsep}{os.environ['PATH']}")
    job, spec, h = _approved_job(s, project, repo.name, "REVIEWING")
    monkeypatch.setattr(worker, "_containerized", lambda: False)

    _review_and_ship(job, spec, repo, wt, branch, h)

    s.expire_all()
    assert s.get(DevJob, job.id).pr_url == "https://github.com/o/r/pull/7"
    assert _sh(origin, "git", "show", f"{branch}:feature.py") == "print('hi')"
    assert not (tmp_path / "owner-pre-push").exists(), "an unattended push runs nothing on unreviewed content"


def test_a_host_refusal_during_review_fails_the_job_instead_of_leaving_it_in_review(s, project, tmp_path, monkeypatch):
    """A planted link in the branch is refused when the host writes the spec into the review export. That
    refusal is the job's final result: recorded on it in Korean, the spec back to READY_FOR_DEV, the queue job
    done. Before, it escaped run_dev_job and the DevJob stayed in REVIEWING with no error."""
    import shutil as sh

    from justshipit.contracts.handoff import SpecStatus
    from justshipit.db.models import DevJob, Job, Spec
    from justshipit.engineering import specs
    from justshipit.services import queue

    repo = _product_repo(f"s5-link-{project.id}")
    outside = tmp_path / "outside"
    outside.mkdir()

    def engineer(clone):
        (clone / "feature.py").write_text("print('hi')\n")
        sh.rmtree(clone / ".ship" / "spec")
        (clone / ".ship" / "spec").symlink_to(outside)
        _engineer_commit(clone)

    job, spec, _ = _job_run_by(s, project, tmp_path, monkeypatch, repo, engineer)
    qjob = queue.enqueue(s, "dev_job", {"dev_job_id": job.id}, idempotency_key=f"dev_job:{job.id}", max_attempts=1)
    s.commit()

    _execute_claimed(s, qjob.id, {"dev_job_id": job.id})

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "FAILED"
    assert re.search("[가-힣]", done.error or ""), done.error
    assert s.get(Spec, spec.id).status == SpecStatus.READY_FOR_DEV.value
    assert s.get(Job, qjob.id).status == "done"
    assert sorted(p.name for p in outside.iterdir()) == []
    assert s.get(DevJob, specs.retry_dev(spec.spec_key, "ko")).status == "QUEUED"


def test_a_dev_job_the_project_budget_stops_tells_the_owner_what_it_lacks(s, project, tmp_path, monkeypatch):
    """J-2 failed three minutes after approval with `engineer step failed: project budget 40.0 USD-eq would be
    exceeded`: research had spent 37.97. The owner reads what was spent on what, how much more development needs,
    and the two things to do."""
    from justshipit.db.models import DevJob, LLMCallRecord, Project
    from justshipit.engineering import worker
    from justshipit.sandbox import docker
    from justshipit.services import budget

    job = _queued_job(s, project)
    p = s.get(Project, project.id)
    p.budget_usd, p.spent_usd = 40.0, 37.97
    s.add(LLMCallRecord(call_id="research-1", project_id=project.id, agent="research", purpose="work", attempt=1,
                        provider="claude", status="ok", cost_usd=37.97))
    s.commit()
    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    monkeypatch.setattr(docker, "image_present", lambda image: True)
    monkeypatch.setattr(worker, "host_git_refusal", lambda lang: None)
    monkeypatch.setattr(worker, "prepare_worktree", lambda spec, h, attempt: (tmp_path, tmp_path, "ai/t", None))
    monkeypatch.setattr(worker, "run_agent", lambda *a, **kw: budget.reserve(project.id, None, "dev", dev=True))

    worker.run_dev_job(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "FAILED"
    need = budget.dev_need_usd() - 2.03
    assert done.error == (f"예산이 모자라 개발을 멈췄어요. 예산 $40 중 $37.97 사용(조사·분석 $37.97, 개발 $0.00). "
                          f"개발을 끝내려면 약 ${-int(-need // 1)} 더 필요해요. 프로젝트 설정에서 예산을 올린 뒤 "
                          "'처음부터 다시 만들기'를 눌러 주세요.")


@pytest.mark.parametrize("raised, label", [
    ("agent", "에이전트가 실패했어요"),
    ("budget", "예산을 넘었어요"),
])
def test_an_agent_or_budget_failure_is_recorded_under_its_own_name(s, project, tmp_path, monkeypatch, raised, label):
    """The CPO answering the engineer's questions runs outside the engineer step's own catch, so a quota or
    budget stop there reaches run_dev_job. The owner reads what stopped the job, not a host failure."""
    from justshipit.agents.executor import AgentFailed
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker
    from justshipit.sandbox import docker
    from justshipit.services.budget import BudgetExceeded

    job = _queued_job(s, project)
    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    monkeypatch.setattr(docker, "image_present", lambda image: True)
    monkeypatch.setattr(worker, "host_git_refusal", lambda lang: None)
    monkeypatch.setattr(worker, "prepare_worktree", lambda spec, h, attempt: (tmp_path, tmp_path, "ai/t", None))
    monkeypatch.setattr(worker.registry, "read_at", lambda *a: "# PRD")
    reason = {"agent": AgentFailed("product/spec: claude is out of quota until 11-03 09:00"),
              "budget": BudgetExceeded("project budget 5.0 USD-eq would be exceeded")}[raised]

    def fake_run_agent(agent_id, purpose, prompt, model, **kw):
        if agent_id == "product":
            raise reason
        return IntakeOut(clear_enough=False, blocking_questions=["누가 결제하나요?"], assumptions_safe_to_make=[],
                         implementation_outline="")

    monkeypatch.setattr(worker, "run_agent", fake_run_agent)

    worker.run_dev_job(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "FAILED"
    assert done.error == f"{label}: {reason}"


def test_a_cp949_file_in_the_branch_is_reviewed_delivered_and_accepted(s, project, tmp_path, monkeypatch):
    """A Korean product can hold a CP949 file. Host git output was decoded strictly as UTF-8, so the review
    diff raised out of run_dev_job, and the DevJob sat in REVIEWING with no error and no retry."""
    from justshipit.db.models import AgentRequest, DevJob, Job
    from justshipit.engineering import worker
    from justshipit.services import queue
    from sqlalchemy import select

    repo = _product_repo(f"s7-cp949-{project.id}")

    def engineer(clone):
        (clone / "legacy.txt").write_bytes("안녕하세요\n".encode("cp949"))
        _engineer_commit(clone)

    job, _, seen = _job_run_by(s, project, tmp_path, monkeypatch, repo, engineer)
    qjob = queue.enqueue(s, "dev_job", {"dev_job_id": job.id}, idempotency_key=f"dev_job:{job.id}", max_attempts=1)
    s.commit()

    _execute_claimed(s, qjob.id, {"dev_job_id": job.id})

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert (done.status, done.error, s.get(Job, qjob.id).last_error) == ("DELIVERED", None, None)
    assert "legacy.txt" in seen["review"]
    monkeypatch.setattr(worker.registry, "read_at", lambda *a: "- 기준")
    monkeypatch.setattr(worker, "run_core_flow", lambda export: {"ran": True, "ok": True, "log": "ok"})

    worker.acceptance(job.id)

    s.expire_all()
    assert s.get(DevJob, job.id).error is None
    merge_requests = s.scalars(select(AgentRequest).where(AgentRequest.project_id == project.id,
                                                          AgentRequest.action_kind == "dev.merge")).all()
    assert [r.action_params["dev_job_id"] for r in merge_requests] == [job.id]


def _merge_ready(s, project, repo, branch, files: dict[str, str]):
    from datetime import datetime, timedelta, timezone

    from justshipit.db.models import Grant

    _sh(repo, "git", "checkout", "-qb", branch)
    for name, body in files.items():
        (repo / name).write_text(body)
    _engineer_commit(repo)
    _sh(repo, "git", "checkout", "-q", "main")
    job, spec, _ = _approved_job(s, project, repo.name)
    job.branch, job.status, spec.status = branch, "DELIVERED", "ACCEPTED"
    grant = Grant(request_id=spec.approval_request_id, project_id=project.id, action_kind="dev.merge",
                  expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    s.add(grant)
    s.commit()
    return job, spec, grant


def test_a_file_name_that_is_not_utf8_is_accepted_and_merged_with_quotepath_off(s, project, tmp_path, monkeypatch):
    """With the owner's core.quotePath=false, git prints file names as raw bytes. A Latin-1 name broke the
    strict UTF-8 decode of the acceptance diff, and of the merge's own output after the merge commit landed."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    owner_config = tmp_path / "gitconfig"
    owner_config.write_text("[core]\n\tquotePath = false\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(owner_config))
    name = os.fsdecode(b"caf\xe9.txt")
    repo = _product_repo(f"s7-latin1-{project.id}")
    job, _, _ = _merge_ready(s, project, repo, "ai/spec-s7l-a1", {name: "menu\n"})
    monkeypatch.setattr(worker.registry, "read_at", lambda *a: "- 기준")
    monkeypatch.setattr(worker, "run_agent", lambda agent_id, purpose, prompt, model, **kw: _fake_output(model))

    worker.acceptance(job.id)

    s.expire_all()
    assert s.get(DevJob, job.id).error is None

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert (done.status, done.error) == ("MERGED", None)
    assert (repo / name).read_text() == "menu\n"


def _merge_failing(s, project, monkeypatch, fault: str):
    """A merge whose host git raises an unexpected error just before or just after the merge commit."""
    from justshipit.engineering import worker

    repo = _product_repo(f"s7-{fault}-{project.id}")
    job, spec, grant = _merge_ready(s, project, repo, f"ai/spec-s7{fault[0]}-a1", {"feature.py": "print('hi')\n"})
    real_git = worker._git

    def git(cwd, *args, **kw):
        if fault == "before" and args[:1] == ("rev-parse",):
            raise KeyError("HEAD")
        out = real_git(cwd, *args, **kw)
        if fault == "after" and "--ff-only" in args:
            raise KeyError("merged")
        return out

    monkeypatch.setattr(worker, "_git", git)
    worker.merge(job.id)
    s.expire_all()
    return repo, job, spec, grant


def test_a_merge_that_landed_is_recorded_merged_whatever_failed_after_it(s, project, monkeypatch):
    """Read from the product repo, not the rolled-back session: the grant is spent once, and a merged spec
    cannot be retried into a second branch."""
    from justshipit.db.models import DevJob, Grant, Spec
    from justshipit.engineering import specs

    repo, job, spec, grant = _merge_failing(s, project, monkeypatch, "after")

    assert s.get(DevJob, job.id).status == "MERGED", s.get(DevJob, job.id).error
    assert s.get(Spec, spec.id).status == "MERGED"
    assert s.get(Grant, grant.id).uses == 1
    assert (repo / "feature.py").read_text() == "print('hi')\n"
    with pytest.raises(ValueError, match="MERGED|머지"):
        specs.retry_dev(spec.spec_key, "ko")


def test_an_unexpected_error_before_the_merge_is_noted_and_the_merge_asked_again(s, project, monkeypatch):
    """The branch is delivered, so the job stays DELIVERED: a new attempt would be a second branch."""
    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import specs

    repo, job, spec, grant = _merge_failing(s, project, monkeypatch, "before")

    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED" and "KeyError" in done.error and re.search("[가-힣]", done.error), done.error
    assert _merge_asked_again(s, job) == [done.error]
    assert s.get(Grant, grant.id).uses == 0
    assert not (repo / "feature.py").exists()
    with pytest.raises(ValueError, match="DELIVERED"):
        specs.retry_dev(spec.spec_key, "ko")


def test_an_unexpected_error_in_a_dev_job_is_recorded_and_retryable(s, project, tmp_path, monkeypatch):
    """A missing config key (company.yaml without engineering.worktrees_dir) is a KeyError, not a RuntimeError."""
    from justshipit.contracts.handoff import SpecStatus
    from justshipit.db.models import DevJob, Spec
    from justshipit.engineering import specs, worker

    job = _queued_job(s, project)
    monkeypatch.setattr(worker, "_containerized", lambda: False)

    def no_worktrees_dir(*a):
        raise KeyError("worktrees_dir")

    monkeypatch.setattr(worker, "prepare_worktree", no_worktrees_dir)

    worker.run_dev_job(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "FAILED" and "KeyError" in done.error and re.search("[가-힣]", done.error), done.error
    assert s.get(Spec, job.spec_id).status == SpecStatus.READY_FOR_DEV.value
    assert s.get(DevJob, specs.retry_dev(s.get(Spec, job.spec_id).spec_key, "ko")).status == "QUEUED"


def test_a_branch_the_owner_already_merged_is_recorded_merged(s, project):
    """No grant is left to spend (the owner merged by hand, or an earlier run spent it), and the repo still
    decides: the branch is in main, so the DevJob is MERGED, not a failed merge with a retry offered."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    repo = _product_repo(f"s7-by-hand-{project.id}")
    job, _, grant = _merge_ready(s, project, repo, "ai/spec-s7h-a1", {"feature.py": "print('hi')\n"})
    _sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "merge", "-q", "--no-ff", job.branch, "-m", "by hand")
    grant.revoked = True
    s.commit()

    worker.merge(job.id)

    s.expire_all()
    assert s.get(DevJob, job.id).status == "MERGED", s.get(DevJob, job.id).error


def test_the_owners_diff_settings_never_change_or_run_on_what_the_reviewers_read(s, project, tmp_path, monkeypatch):
    """diff.external would run the owner's tool on engineer content and hand its output to the reviewer in
    place of the diff; color.ui=always would fill the diff with escape codes. Neither applies to the worker."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    marker = tmp_path / "external-diff-ran"
    tool = tmp_path / "owner-diff"
    tool.write_text(f"#!/bin/sh\ntouch '{marker}'\necho OWNER-TOOL\n")
    os.chmod(tool, 0o755)
    owner_config = tmp_path / "gitconfig"
    owner_config.write_text(f"[diff]\n\texternal = {tool}\n[color]\n\tui = always\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(owner_config))
    repo = _product_repo(f"s7-extdiff-{project.id}")

    def engineer(clone):
        (clone / "feature.py").write_text("print('hi')\n")
        _engineer_commit(clone)

    job, _, _ = _job_run_by(s, project, tmp_path, monkeypatch, repo, engineer)
    stubbed, prompts = worker.run_agent, {}

    def run_agent(agent_id, purpose, prompt, model, **kw):
        prompts[purpose] = prompt
        return stubbed(agent_id, purpose, prompt, model, **kw)

    monkeypatch.setattr(worker, "run_agent", run_agent)

    worker.run_dev_job(job.id)
    monkeypatch.setattr(worker.registry, "read_at", lambda *a: "- 기준")
    worker.acceptance(job.id)

    s.expire_all()
    assert s.get(DevJob, job.id).error is None
    for purpose in ("review", "acceptance"):
        assert "+print('hi')" in prompts[purpose] and "OWNER-TOOL" not in prompts[purpose], purpose
        assert "\x1b[" not in prompts[purpose], purpose
    assert not marker.exists()


def _owner_diverged(repo):
    """An owner branch and a main commit that both change README.md, so the owner's own operations conflict."""
    _sh(repo, "git", "checkout", "-qb", "owner-feature")
    (repo / "README.md").write_text("feature\n")
    _sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qam", "feature")
    _sh(repo, "git", "checkout", "-q", "main")
    (repo / "README.md").write_text("main\n")
    _sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qam", "main edit")


def _owner_snapshot(repo):
    return (_sh(repo, "git", "rev-parse", "HEAD"), _sh(repo, "git", "status", "--porcelain"),
            (repo / "README.md").read_text())


_OWNER_OPERATIONS = {
    "merge": (["merge", "owner-feature"], "MERGE_HEAD", "git merge --abort"),
    "rebase": (["rebase", "main", "owner-feature"], "rebase-merge", "git rebase --abort"),
    "cherry-pick": (["cherry-pick", "owner-feature"], "CHERRY_PICK_HEAD", "git cherry-pick --abort"),
    "revert": (["revert", "--no-commit", "HEAD"], "REVERT_HEAD", "git revert --abort"),
    "bisect": (["bisect", "start"], "BISECT_LOG", "git bisect reset"),
}


@pytest.mark.parametrize("operation", list(_OWNER_OPERATIONS))
def test_a_merge_never_touches_an_operation_the_owner_has_in_progress(s, project, operation):
    """git refuses to merge over the owner's unfinished merge, and the abort that followed the refusal threw away
    the owner's staged resolution. Every operation the owner has in progress is now left as it is."""
    import subprocess

    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    args, state, finish = _OWNER_OPERATIONS[operation]
    repo = _product_repo(f"s8-busy-{operation}-{project.id}")
    job, _, grant = _merge_ready(s, project, repo, f"ai/spec-s8{operation[:3]}-a1", {"ai.txt": "ai\n"})
    _owner_diverged(repo)
    subprocess.run(["git", "-c", "user.name=o", "-c", "user.email=o@x", *args], cwd=repo, capture_output=True,
                   check=False)
    if operation == "merge":
        (repo / "README.md").write_text("owner's careful resolution\n")
        _sh(repo, "git", "add", "README.md")
    assert (repo / ".git" / state).exists()
    before = _owner_snapshot(repo)

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED" and finish in done.error and re.search("[가-힣]", done.error), done.error
    assert s.get(Grant, grant.id).uses == 0
    assert (repo / ".git" / state).exists(), "the owner's operation is still in progress"
    assert _owner_snapshot(repo) == before
    assert not (repo / "ai.txt").exists()


@pytest.mark.parametrize("change", ["unstaged", "staged"])
def test_a_merge_lands_beside_the_owners_uncommitted_changes_and_keeps_them(s, project, change):
    """The merge is computed outside the work tree and reaches the checkout only as a fast-forward, which git
    refuses when it would touch a changed file. A draft the merge does not touch stays as it is."""
    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    repo = _product_repo(f"s8-dirty-{change}-{project.id}")
    job, _, grant = _merge_ready(s, project, repo, f"ai/spec-s8{change[0]}-a1", {"ai.txt": "ai\n"})
    (repo / "README.md").write_text("owner's draft\n")
    if change == "staged":
        _sh(repo, "git", "add", "README.md")
    before = _sh(repo, "git", "status", "--porcelain")

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert (done.status, done.error) == ("MERGED", None)
    assert s.get(Grant, grant.id).uses == 1
    assert (repo / "README.md").read_text() == "owner's draft\n"
    assert _sh(repo, "git", "status", "--porcelain") == before
    assert (repo / "ai.txt").read_text() == "ai\n"


def test_a_merge_never_overwrites_a_tracked_file_the_owner_changed(s, project):
    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    repo = _product_repo(f"s10-touched-{project.id}")
    job, _, grant = _merge_ready(s, project, repo, "ai/spec-s10t-a1", {"README.md": "engineer\n"})
    (repo / "README.md").write_text("owner's draft\n")
    before = _owner_snapshot(repo)

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED" and "README.md" in done.error and re.search("[가-힣]", done.error), done.error
    assert _merge_asked_again(s, job) == [done.error]
    assert s.get(Grant, grant.id).uses == 0
    assert _owner_snapshot(repo) == before


def _owner_ignores(repo, *patterns: str) -> None:
    (repo / ".gitignore").write_text("".join(f"{p}\n" for p in patterns))
    _sh(repo, "git", "add", ".gitignore")
    _sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qm", "ignore")


@pytest.mark.parametrize("owner_has", ["file", "directory"])
def test_a_merge_never_replaces_the_owners_ignored_files(s, project, owner_has):
    """git treats an ignored file as expendable: a merge in the checkout replaced the owner's .env with the
    engineer's, and turned the owner's build/ directory into the engineer's build file. The reviewer's proof
    (review-s8/ignored.sh) is the red test here."""
    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    repo = _product_repo(f"s10-ignored-{owner_has}-{project.id}")
    job, _, grant = _merge_ready(s, project, repo, f"ai/spec-s10i{owner_has[0]}-a1",
                                 {".env": "ENGINEER=1\n", "build": "engineer\n"})
    _owner_ignores(repo, ".env", "build/")
    (repo / ".env").write_text("OWNER_SECRET=1\n")
    if owner_has == "directory":
        (repo / "build").mkdir()
        (repo / "build" / "keep.bin").write_bytes(b"owner artifact")
    base = _sh(repo, "git", "rev-parse", "main")

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED" and ".env" in done.error and re.search("[가-힣]", done.error), done.error
    assert s.get(Grant, grant.id).uses == 0
    assert (repo / ".env").read_text() == "OWNER_SECRET=1\n"
    if owner_has == "directory":
        assert (repo / "build" / "keep.bin").read_bytes() == b"owner artifact" and "build" in done.error
    assert _sh(repo, "git", "rev-parse", "main") == base and _sh(repo, "git", "status", "--porcelain") == ""


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_a_merge_never_overwrites_a_local_edit_git_was_told_to_ignore(s, project, flag):
    """`git status` shows nothing for such a file, so a pre-check passed and the merge in the checkout replaced
    the owner's local config. The fast-forward compares the file itself, flag or not."""
    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    repo = _product_repo(f"s10-{flag[2:6]}-{project.id}")
    (repo / "config.ini").write_text("db=prod\n")
    _sh(repo, "git", "add", "config.ini")
    _sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qm", "config")
    job, _, grant = _merge_ready(s, project, repo, f"ai/spec-s10{flag[2]}-a1", {"config.ini": "db=prod\nnew=1\n"})
    _sh(repo, "git", "update-index", flag, "config.ini")
    (repo / "config.ini").write_text("db=OWNER_LOCAL\n")
    assert _sh(repo, "git", "status", "--porcelain") == ""

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED" and "config.ini" in done.error and re.search("[가-힣]", done.error), done.error
    assert s.get(Grant, grant.id).uses == 0
    assert (repo / "config.ini").read_text() == "db=OWNER_LOCAL\n"


def test_a_merge_the_owner_starts_in_the_gap_is_left_as_it_is(s, project, git_on_path):
    """Between the worker's checks and git's own, the owner starts a conflicting merge. git refuses to
    fast-forward over MERGE_HEAD, and nothing the worker does afterwards touches that merge."""
    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    repo = _product_repo(f"s10-gap-merge-{project.id}")
    job, _, grant = _merge_ready(s, project, repo, "ai/spec-s10g-a1", {"ai.txt": "ai\n"})
    _owner_diverged(repo)
    _host_git(git_on_path, 'case " $* " in *" merge "*) "$REAL" -c user.name=o -c user.email=o@x merge owner-feature '
                           '>/dev/null 2>&1; echo "owner resolution" > README.md; "$REAL" add README.md;; esac\n'
                           'exec "$REAL" "$@"')
    base = _sh(repo, "git", "rev-parse", "main")

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED" and re.search("[가-힣]", done.error), done.error
    assert s.get(Grant, grant.id).uses == 0
    assert (repo / ".git" / "MERGE_HEAD").exists(), "the owner's merge is still in progress"
    assert (repo / "README.md").read_text() == "owner resolution\n"
    assert _sh(repo, "git", "rev-parse", "main") == base and not (repo / "ai.txt").exists()


def test_a_rebase_the_owner_starts_in_the_gap_never_records_a_merge_that_did_not_land(s, project, git_on_path):
    """A rebase detaches HEAD, so a merge run in the checkout then lands on the detached HEAD and not on the
    base branch. MERGED is recorded only once the base branch itself is at the merge commit."""
    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    repo = _product_repo(f"s10-gap-rebase-{project.id}")
    job, _, grant = _merge_ready(s, project, repo, "ai/spec-s10r-a1", {"ai.txt": "ai\n"})
    _host_git(git_on_path, 'case " $* " in *" merge "*) "$REAL" checkout -q --detach;; esac\nexec "$REAL" "$@"')
    base = _sh(repo, "git", "rev-parse", "main")

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED" and re.search("[가-힣]", done.error), done.error
    assert _merge_asked_again(s, job) == [done.error]
    assert s.get(Grant, grant.id).uses == 0
    assert _sh(repo, "git", "rev-parse", "main") == base


def test_a_merge_lands_without_touching_a_checkout_that_is_on_another_branch(s, project):
    """The owner is working on their own branch: only the base ref moves (compare-and-swap), and the owner's
    checkout, index and uncommitted work stay as they are."""
    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    repo = _product_repo(f"s10-other-branch-{project.id}")
    job, _, grant = _merge_ready(s, project, repo, "ai/spec-s10o-a1", {"ai.txt": "ai\n"})
    _sh(repo, "git", "checkout", "-qb", "owner-work")
    (repo / "README.md").write_text("owner's draft\n")
    before = _owner_snapshot(repo)

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert (done.status, done.error) == ("MERGED", None)
    assert s.get(Grant, grant.id).uses == 1
    assert _sh(repo, "git", "symbolic-ref", "HEAD") == "refs/heads/owner-work"
    assert _owner_snapshot(repo) == before and not (repo / "ai.txt").exists()
    assert _sh(repo, "git", "show", "main:ai.txt") == "ai"
    assert _sh(repo, "git", "merge-base", "--is-ancestor", job.branch, "main") == ""


def test_a_merge_lands_in_the_linked_worktree_that_has_the_base_branch(s, project, tmp_path):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    repo = _product_repo(f"s10-linked-{project.id}")
    job, _, _ = _merge_ready(s, project, repo, "ai/spec-s10l-a1", {"ai.txt": "ai\n"})
    _sh(repo, "git", "checkout", "-qb", "owner-work")
    linked = tmp_path / "owner-main"
    _sh(repo, "git", "worktree", "add", "-q", str(linked), "main")
    (linked / ".env").write_text("OWNER_SECRET=1\n")

    worker.merge(job.id)

    s.expire_all()
    assert s.get(DevJob, job.id).status == "MERGED", s.get(DevJob, job.id).error
    assert (linked / "ai.txt").read_text() == "ai\n" and (linked / ".env").read_text() == "OWNER_SECRET=1\n"
    assert not (repo / "ai.txt").exists() and _sh(repo, "git", "symbolic-ref", "HEAD") == "refs/heads/owner-work"


def test_a_branch_the_owner_merged_is_recorded_merged_while_the_owner_is_busy(s, project):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    repo = _product_repo(f"s8-merged-busy-{project.id}")
    job, _, _ = _merge_ready(s, project, repo, "ai/spec-s8m-a1", {"feature.py": "print('hi')\n"})
    _sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "merge", "-q", "--no-ff", job.branch, "-m", "by hand")
    _sh(repo, "git", "bisect", "start")

    worker.merge(job.id)

    s.expire_all()
    assert s.get(DevJob, job.id).status == "MERGED", s.get(DevJob, job.id).error
    assert (repo / ".git" / "BISECT_LOG").exists()


def _acceptance_queued(s, project, monkeypatch, outcomes: list):
    """A delivered dev job, its acceptance on the queue (max_attempts=3, as _ship enqueues it), and a CPO whose
    answer on each attempt is the next outcome: an exception to raise or None to accept."""
    from justshipit.engineering import worker
    from justshipit.services import queue

    repo = _product_repo(f"s8-accept-{len(outcomes)}-{project.id}")
    job, _, _ = _approved_job(s, project, repo.name)
    job.branch, job.status = "main", "DELIVERED"
    qjob = queue.enqueue(s, "acceptance", {"dev_job_id": job.id}, idempotency_key=f"acceptance:{job.id}")
    s.commit()
    monkeypatch.setattr(worker.registry, "read_at", lambda *a: "- 기준")
    monkeypatch.setattr(worker, "run_core_flow", lambda export: {"ran": True, "ok": True, "log": "2 files: ok"})

    def cpo(agent_id, purpose, prompt, model, **kw):
        if outcome := outcomes.pop(0):
            raise outcome
        return _fake_output(model)

    monkeypatch.setattr(worker, "run_agent", cpo)
    return job, qjob


def _execute_claimed(s, qjob_id: int, payload: dict) -> None:
    """One attempt the way the worker runs it: claimed (running, attempts counted, a lease token this process
    holds), then executed and finished."""
    import uuid

    from justshipit.db.models import Job
    from justshipit.services import queue
    from justshipit.workers import runner

    qjob = s.get(Job, qjob_id)
    kind, token = qjob.kind, uuid.uuid4().hex
    qjob.status, qjob.attempts, qjob.lease_token, qjob.locked_by = "running", qjob.attempts + 1, token, "worker-test"
    s.commit()
    runner._execute(qjob_id, kind, payload, runner._handlers(), runner.Runs(), queue.Lease(job_id=qjob_id, token=token))
    s.expire_all()


def _run_queued(s, qjob_id: int, payload: dict) -> None:
    _execute_claimed(s, qjob_id, payload)


def test_a_provider_error_in_acceptance_is_retried_by_the_queue(s, project, monkeypatch):
    from justshipit.agents.executor import AgentFailed
    from justshipit.db.models import AgentRequest, DevJob, Job

    job, qjob = _acceptance_queued(s, project, monkeypatch, [AgentFailed("all providers exhausted"), None])

    _run_queued(s, qjob.id, {"dev_job_id": job.id})

    assert s.get(Job, qjob.id).status == "queued" and "all providers exhausted" in s.get(Job, qjob.id).last_error
    assert (s.get(DevJob, job.id).status, s.get(DevJob, job.id).error) == ("DELIVERED", None)

    _run_queued(s, qjob.id, {"dev_job_id": job.id})

    done = s.get(DevJob, job.id)
    assert s.get(Job, qjob.id).status == "done"
    assert (done.status, done.error, done.result["acceptance"]["accepted"]) == ("DELIVERED", None, True)
    assert [r.action_kind for r in s.query(AgentRequest).filter_by(project_id=project.id, type="APPROVAL")
            if r.action_params.get("dev_job_id") == job.id] == ["dev.merge"]


def test_an_acceptance_that_fails_every_attempt_is_recorded_on_the_last(s, project, monkeypatch):
    from justshipit.agents.executor import AgentFailed
    from justshipit.db.models import DevJob, Job

    job, qjob = _acceptance_queued(s, project, monkeypatch, [AgentFailed(f"quota {n}") for n in range(3)])

    for _ in range(2):
        _run_queued(s, qjob.id, {"dev_job_id": job.id})
        assert (s.get(DevJob, job.id).status, s.get(Job, qjob.id).status) == ("DELIVERED", "queued")
    _run_queued(s, qjob.id, {"dev_job_id": job.id})

    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED" and "완료 기준 확인이 실패했어요" in done.error and "quota 2" in done.error, done.error
    assert s.get(Job, qjob.id).status == "done", "recorded on the DevJob, not retried again"


def _owner_attributes(tmp_path, monkeypatch, repo, where: str):
    """The owner's own git setup names a merge driver and a filter, selected for every file from outside any
    branch: their global core.attributesFile, or the product repo's .git/info/attributes. --attr-source
    replaces only the tree's .gitattributes, so both are read unless the worker turns them off."""
    markers = tmp_path / "markers"
    markers.mkdir()
    attributes = "* merge=owner filter=owner\n"
    tool = tmp_path / "owner-tool"
    tool.write_text(f"#!/bin/sh\ntouch '{markers}'/\"$1@${{PWD##*/}}\"\n[ \"$1\" = merge-driver ] && exit 1\nexec cat\n")
    os.chmod(tool, 0o755)
    owner_config = tmp_path / "gitconfig"
    owner_config.write_text(f"[merge \"owner\"]\n\tdriver = {tool} merge-driver\n"
                            f"[filter \"owner\"]\n\tsmudge = {tool} smudge\n\tclean = {tool} clean\n")
    if where == "global":
        (tmp_path / "attributes").write_text(attributes)
        owner_config.write_text(owner_config.read_text() + f"[core]\n\tattributesFile = {tmp_path / 'attributes'}\n")
    else:
        (repo / ".git" / "info").mkdir(exist_ok=True)
        (repo / ".git" / "info" / "attributes").write_text(attributes)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(owner_config))
    return markers


def _ran_on_engineer_content(markers) -> list[str]:
    """Where the owner's tool ran: the product repo, an export, or the registry that holds the delivery doc."""
    return sorted(p.name for p in markers.iterdir())


@pytest.mark.parametrize("where", ["global", "info"])
def test_the_owners_own_attributes_never_run_a_driver_on_the_approved_merge(s, project, tmp_path, monkeypatch, where):
    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    repo = _product_repo(f"s8-attr-merge-{where}-{project.id}")
    (repo / "shared.txt").write_text("a\nb\nc\n")
    _sh(repo, "git", "add", "shared.txt")
    _sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qm", "shared")
    job, _, grant = _merge_ready(s, project, repo, f"ai/spec-s8a{where[0]}-a1", {"shared.txt": "A\nb\nc\n"})
    (repo / "shared.txt").write_text("a\nb\nC\n")
    _sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qam", "owner edit")
    markers = _owner_attributes(tmp_path, monkeypatch, repo, where)

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert _ran_on_engineer_content(markers) == []
    if where == "global":
        assert (done.status, done.error) == ("MERGED", None)
        assert (repo / "shared.txt").read_text() == "A\nb\nC\n"
    else:
        assert done.status == "DELIVERED" and "info/attributes" in done.error, done.error
        assert re.search("[가-힣]", done.error) and s.get(Grant, grant.id).uses == 0
        assert (repo / "shared.txt").read_text() == "a\nb\nC\n"


@pytest.mark.parametrize("where", ["global", "info"])
def test_the_owners_own_attributes_never_run_a_filter_on_the_engineers_files(s, project, tmp_path, monkeypatch,
                                                                              where):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    repo = _product_repo(f"s8-attr-dev-{where}-{project.id}")

    def engineer(clone):
        """In its container, where the owner's git config does not exist."""
        (clone / "feature.py").write_text("print('hi')\n")
        for args in (["add", "-A"], ["-c", "user.name=e", "-c", "user.email=e@x", "commit", "-qm", "feat"]):
            _sh(clone, "env", "GIT_CONFIG_GLOBAL=/dev/null", "git", *args)
        (clone / "leftover.txt").write_text("late\n")

    job, _, seen = _job_run_by(s, project, tmp_path, monkeypatch, repo, engineer)
    markers = _owner_attributes(tmp_path, monkeypatch, repo, where)

    worker.run_dev_job(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert _ran_on_engineer_content(markers) == []
    if where == "global":
        assert (done.status, done.error) == ("DELIVERED", None)
        assert seen["review"]["feature.py"] == "print('hi')\n"
    else:
        assert done.status == "FAILED" and "info/attributes" in done.error and re.search("[가-힣]", done.error)
        assert "engineered" not in seen, "refused before the engineer started"


def test_a_failure_after_delivery_is_a_note_and_never_a_second_branch(s, project, tmp_path, monkeypatch):
    """A DB error after the ship committed DELIVERED reached run_dev_job's catch, which recorded FAILED, and
    retry_dev then queued a second attempt: two branches for one spec. A delivered job only moves on to MERGED."""
    import sqlalchemy.exc
    from justshipit.db.models import DevJob, Job, Spec
    from justshipit.engineering import specs, worker
    from sqlalchemy import select

    repo = _product_repo(f"s9-after-delivered-{project.id}")

    def engineer(clone):
        (clone / "f.py").write_text("x = 1\n")
        _engineer_commit(clone)

    job, spec, _ = _job_run_by(s, project, tmp_path, monkeypatch, repo, engineer)
    real_ship = worker._ship

    def ship(*a, **kw):
        real_ship(*a, **kw)
        raise sqlalchemy.exc.OperationalError("UPDATE dev_jobs", {}, Exception("server closed the connection"))

    monkeypatch.setattr(worker, "_ship", ship)

    worker.run_dev_job(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED", done.error
    assert "server closed the connection" in done.error
    assert "server closed the connection" in done.steps[-1]["summary"], "the failure is a note in the job's steps"
    assert s.get(Spec, spec.id).status == "DELIVERED"
    assert len(s.scalars(select(Job).where(Job.idempotency_key == f"acceptance:{job.id}")).all()) == 1
    with pytest.raises(ValueError, match="DELIVERED"):
        specs.retry_dev(spec.spec_key, "ko")


def test_a_spec_with_a_delivered_attempt_gets_no_new_attempt(s, project):
    """The old rule recorded FAILED after delivery, so a DB can hold a delivered attempt with a FAILED one after it.
    Its spec already has a branch."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import specs

    job, spec, _ = _approved_job(s, project, f"s9-two-{project.id}")
    job.status = "DELIVERED"
    s.add(DevJob(spec_id=spec.id, status="FAILED", attempt=2, error="old rule"))
    s.commit()

    with pytest.raises(ValueError, match=f"J-{job.id}"):
        specs.retry_dev(spec.spec_key, "ko")


@pytest.mark.parametrize("left, recorded", [("DELIVERED", "DELIVERED")])
def test_a_job_run_again_never_goes_back_to_intake(s, project, tmp_path, monkeypatch, left, recorded):
    """The queue runs a dev job again when its worker died after the run ended. A delivered job stays delivered, its
    error untouched; one left mid-run carries on from its step (test_build_survive.py)."""
    from justshipit.db.models import DevJob, Spec
    from justshipit.engineering import worker

    repo = _product_repo(f"s9-rerun-{left.lower()}-{project.id}")
    job, spec, _ = _job_run_by(s, project, tmp_path, monkeypatch, repo, lambda clone: None)
    job.status = left
    spec.status = "DELIVERED" if left == "DELIVERED" else "IN_DEVELOPMENT"
    s.commit()
    calls = []
    monkeypatch.setattr(worker, "run_agent", lambda agent_id, *a, **kw: calls.append(agent_id))

    worker.run_dev_job(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert (done.status, calls, done.error) == (recorded, [], None)
    assert done.steps[-1]["summary"] == f"not run again: the job is {left}"
    assert s.get(Spec, spec.id).status == ("DELIVERED" if left == "DELIVERED" else "READY_FOR_DEV")


def test_a_merge_approved_again_after_a_failed_one_records_merged_without_the_old_error(s, project):
    from datetime import datetime, timedelta, timezone

    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    repo = _product_repo(f"s9-again-{project.id}")
    job, spec, _ = _merge_ready(s, project, repo, "ai/spec-s9g-a1", {"README.md": "engineer\n"})
    (repo / "README.md").write_text("owner's draft\n")
    worker.merge(job.id)
    s.expire_all()
    assert (s.get(DevJob, job.id).status, len(_merge_asked_again(s, job))) == ("DELIVERED", 1)
    _sh(repo, "git", "checkout", "README.md")
    s.add(Grant(request_id=spec.approval_request_id, project_id=project.id, action_kind="dev.merge",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    s.commit()

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert (done.status, done.error) == ("MERGED", None)
    assert [step["status"] for step in done.steps] == ["DELIVERED", "MERGED"], "the failed merge stays in the steps"


@pytest.mark.parametrize("forge, recorded, uses", [("MERGED", "MERGED", 1), ("OPEN", "DELIVERED", 0),
                                                    (None, "DELIVERED", 0)])
def test_a_pr_merge_that_landed_on_the_forge_is_recorded_merged(s, project, monkeypatch, forge, recorded, uses):
    """`gh pr merge` can merge on the forge and still fail here (a timeout, a dropped connection, a DB error
    after it). The forge decides: `gh pr view` says MERGED, so the job is MERGED and the grant spent once.
    A PR the forge has not merged, or a forge that cannot answer, leaves the job DELIVERED with the error."""
    import json
    import subprocess

    from justshipit.db.models import DevJob, Grant, Spec
    from justshipit.engineering import worker

    repo = _product_repo(f"s9-pr-{forge}-{project.id}")
    job, spec, grant = _merge_ready(s, project, repo, f"ai/spec-s9p{str(forge)[0]}-a1", {"f.py": "x\n"})
    job.pr_url = "https://github.com/o/r/pull/1"
    s.commit()

    def gh(argv, **kw):
        if argv[:3] == ["gh", "pr", "merge"]:
            raise subprocess.TimeoutExpired(argv, 60)
        if argv[:3] == ["gh", "pr", "view"] and forge:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"state": forge, "mergeCommit": None}), "")
        return subprocess.CompletedProcess(argv, 1, "", "HTTP 502")

    monkeypatch.setattr(worker.subprocess, "run", gh)

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert (done.status, s.get(Grant, grant.id).uses) == (recorded, uses), done.error
    assert s.get(Spec, spec.id).status == ("MERGED" if recorded == "MERGED" else "ACCEPTED")
    assert (done.error is None) == (recorded == "MERGED"), done.error


@pytest.mark.parametrize("grant_state", ["expired", "missing", "os-denied"])
def test_a_merge_without_a_valid_grant_tells_the_owner_to_approve_it_again(s, project, monkeypatch, grant_state):
    """The grant check raises PermissionError, an OSError, so a missing or expired merge approval read as a host
    failure. The owner reads that the approval is gone and approves the merge again; a host file the OS refused
    is still a host failure."""
    from datetime import datetime, timedelta, timezone

    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    repo = _product_repo(f"s9-grant-{grant_state}-{project.id}")
    job, _, grant = _merge_ready(s, project, repo, f"ai/spec-s9{grant_state[0]}-a1", {"f.py": "x\n"})
    if grant_state == "expired":
        grant.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    elif grant_state == "missing":
        s.delete(grant)
    else:
        def denied(*a, **kw):
            raise PermissionError(13, "Permission denied", str(repo / ".git" / "index"))

        monkeypatch.setattr(worker, "_git", denied)
    s.commit()

    worker.merge(job.id)

    s.expire_all()
    error = s.get(DevJob, job.id).error
    if grant_state == "os-denied":
        assert "이 PC에서 하는 작업이 실패했어요" in error and "Permission denied" in error, error
    else:
        assert "이 PC에서 하는 작업이 실패했어요" not in error and "머지를 다시 승인" in error, error
    assert len(_merge_asked_again(s, job)) == 1


def test_an_acceptance_waiting_in_backoff_is_a_visible_note_on_the_delivered_job(s, project, monkeypatch):
    """Between attempts the job read DELIVERED with nothing to say that its acceptance had failed and would run
    again. Each retry leaves a note; the status never moves, and the last failure is the job's error."""
    from justshipit.agents.executor import AgentFailed
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    job, qjob = _acceptance_queued(s, project, monkeypatch, [AgentFailed(f"quota {n}") for n in range(3)])

    for _ in range(3):
        _run_queued(s, qjob.id, {"dev_job_id": job.id})

    done = s.get(DevJob, job.id)
    notes = [step["summary"] for step in done.steps]
    assert [worker.step_line(step, "ko") for step in done.steps[:2]] == [
        "완료 기준 확인에 실패해서 다시 해요(3번 중 1번째).", "완료 기준 확인에 실패해서 다시 해요(3번 중 2번째)."]
    assert "quota 0" in notes[0] and "quota 1" in notes[1]
    assert {step["status"] for step in done.steps} == {"DELIVERED"}
    assert done.status == "DELIVERED" and "quota 2" in done.error and "quota 2" in notes[2]


def test_the_registry_runs_none_of_the_owners_git_filters_or_hooks(tmp_path, monkeypatch):
    """The delivery doc carries the engineer's test log, and the registry committed it with plain git, so a
    filter the owner selected in their global attributes, the git-lfs filter, or a global hook ran on it."""
    from justshipit.services import registry

    markers = tmp_path / "markers"
    markers.mkdir()
    tool = tmp_path / "owner-tool"
    tool.write_text(f"#!/bin/sh\ntouch '{markers}'/\"$1\"\nexec cat\n")
    os.chmod(tool, 0o755)
    hooks = tmp_path / "owner-hooks"
    hooks.mkdir()
    for name in ("pre-commit", "commit-msg", "post-commit"):
        (hooks / name).write_text(f"#!/bin/sh\ntouch '{markers}/{name}'\n")
        os.chmod(hooks / name, 0o755)
    (tmp_path / "attributes").write_text("*.txt filter=owner\n*.md filter=lfs\n")
    owner_config = tmp_path / "gitconfig"
    owner_config.write_text(f"[filter \"owner\"]\n\tclean = {tool} clean\n\tsmudge = {tool} smudge\n"
                            f"[filter \"lfs\"]\n\tclean = {tool} lfs-clean\n\tsmudge = {tool} lfs-smudge\n"
                            f"[core]\n\tattributesFile = {tmp_path / 'attributes'}\n\thooksPath = {hooks}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(owner_config))
    log = "$ pytest\n" + "\n".join(f"engineer log line {n}" for n in range(3)) + "\n"

    sha = registry.write_files({"projects/s9/delivery-s9.md": f"## Tests\n{log}", "projects/s9/log.txt": log},
                               "[S9] Delivery")

    assert " ".join(sorted(p.name for p in markers.iterdir())) == ""
    assert registry.read_at("projects/s9/log.txt", sha) == log.strip()


def test_attributes_the_owner_adds_during_a_dev_job_never_run_a_filter_at_ship(s, project, tmp_path, monkeypatch):
    """info/attributes was checked at intake only. The owner adds one while the engineer works, and the ship's
    `add -A` of leftovers through the product repo's git dir would run the filter it names on engineer files."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    repo = _product_repo(f"s10-attr-ship-{project.id}")
    markers: list = []

    def engineer(clone):
        (clone / "feature.py").write_text("print('hi')\n")
        for args in (["add", "-A"], ["-c", "user.name=e", "-c", "user.email=e@x", "commit", "-qm", "feat"]):
            _sh(clone, "env", "GIT_CONFIG_GLOBAL=/dev/null", "git", *args)
        (clone / "leftover.txt").write_text("late\n")
        markers.append(_owner_attributes(tmp_path, monkeypatch, repo, "info"))

    job, _, seen = _job_run_by(s, project, tmp_path, monkeypatch, repo, engineer)

    worker.run_dev_job(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert _ran_on_engineer_content(markers[0]) == []
    assert done.status == "FAILED" and "info/attributes" in done.error and re.search("[가-힣]", done.error), done.error
    assert "engineered" in seen and "review" not in seen, "refused before the review export, after the engineer worked"


def test_a_new_product_repo_is_made_without_the_owners_hooks_or_filters(tmp_path, monkeypatch):
    """ensure_product_repo committed its README and .gitignore with plain git, so the owner's global hooks and
    the filter their global attributes select ran on files the AI wrote."""
    from justshipit.engineering import specs
    from justshipit.settings import settings

    markers = tmp_path / "markers"
    markers.mkdir()
    tool = tmp_path / "owner-tool"
    tool.write_text(f"#!/bin/sh\ntouch '{markers}'/\"$1\"\nexec cat\n")
    os.chmod(tool, 0o755)
    hooks = tmp_path / "owner-hooks"
    hooks.mkdir()
    for name in ("pre-commit", "post-commit"):
        (hooks / name).write_text(f"#!/bin/sh\ntouch '{markers}/{name}'\n")
        os.chmod(hooks / name, 0o755)
    (tmp_path / "attributes").write_text("* filter=owner\n")
    (tmp_path / "gitconfig").write_text(f"[filter \"owner\"]\n\tclean = {tool} clean\n\tsmudge = {tool} smudge\n"
                                        f"[core]\n\tattributesFile = {tmp_path / 'attributes'}\n\thooksPath = {hooks}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))

    specs.ensure_product_repo("s10-fresh", "main", "a product")

    repo = settings().products_dir / "s10-fresh"
    assert sorted(p.name for p in markers.iterdir()) == []
    assert _sh(repo, "git", "log", "--format=%s", "main") == "Initialize repository"
    assert _sh(repo, "git", "show", "main:.gitignore") == ".ship/"


def _alert_for(job_id: int) -> dict | None:
    from justshipit.api import console
    return next((a for a in console.alerts(include_test=True) if a.get("dev_job_id") == job_id), None)


def test_the_owner_can_ask_for_the_acceptance_again_after_its_last_attempt_failed(s, project, monkeypatch):
    """An acceptance that failed on the queue's last attempt left the job DELIVERED with an error and nothing
    the owner could do about it. The dashboard names it, and the owner's action re-queues the acceptance of the
    same branch, never a new attempt."""
    from sqlalchemy import select

    from justshipit.agents.executor import AgentFailed
    from justshipit.db.models import AgentRequest, DevJob, Job, Spec
    from justshipit.engineering import worker

    job, qjob = _acceptance_queued(s, project, monkeypatch, [AgentFailed(f"quota {n}") for n in range(3)] + [None])
    for _ in range(3):
        _run_queued(s, qjob.id, {"dev_job_id": job.id})
    assert "quota 2" in s.get(DevJob, job.id).error and s.get(Job, qjob.id).status == "done"
    alert = _alert_for(job.id)
    assert (alert["kind"], alert["severity"], alert["request_id"]) == ("dev_delivered", "bad", None), alert
    assert alert["actions"] == ["accept_again", "discard"]
    assert "quota 2" in alert["detail"] and alert["spec_key"] == s.get(Spec, job.spec_id).spec_key

    worker.accept_again(job.id, "ko")

    s.expire_all()
    again = s.scalar(select(Job).where(Job.kind == "acceptance", Job.status == "queued",
                                       Job.payload["dev_job_id"].as_integer() == job.id))
    assert again is not None and again.id != qjob.id
    assert s.get(DevJob, job.id).attempt == 1 and s.query(DevJob).filter_by(spec_id=job.spec_id).count() == 1
    with pytest.raises(ValueError, match="대기"):
        worker.accept_again(job.id, "ko")

    _run_queued(s, again.id, {"dev_job_id": job.id})

    done = s.get(DevJob, job.id)
    assert (done.status, done.error, done.result["acceptance"]["accepted"]) == ("DELIVERED", None, True)
    assert [r.action_kind for r in s.query(AgentRequest).filter_by(project_id=project.id, type="APPROVAL")
            if r.action_params.get("dev_job_id") == job.id] == ["dev.merge"]
    assert _alert_for(job.id) is None


def test_a_merge_that_needs_approval_again_is_an_alert_that_links_the_request(s, project):
    from sqlalchemy import select

    from justshipit.db.models import AgentRequest
    from justshipit.engineering import worker

    repo = _product_repo(f"s10-alert-merge-{project.id}")
    job, _, _ = _merge_ready(s, project, repo, "ai/spec-s10am-a1", {"README.md": "engineer\n"})
    (repo / "README.md").write_text("owner's draft\n")

    worker.merge(job.id)

    s.expire_all()
    req = s.scalar(select(AgentRequest).where(AgentRequest.status == "PENDING", AgentRequest.action_kind == "dev.merge",
                                              AgentRequest.action_params["dev_job_id"].as_integer() == job.id))
    alert = _alert_for(job.id)
    assert (alert["kind"], alert["severity"], alert["request_id"], alert["actions"]) == (
        "dev_delivered", "warn", req.id, []), alert
    assert "README.md" in alert["detail"]
    with pytest.raises(ValueError, match="머지 결정"):
        worker.accept_again(job.id, "ko")
    failed, _, _ = _approved_job(s, project, f"{repo.name}-failed", "FAILED")
    with pytest.raises(ValueError, match="FAILED"):
        worker.accept_again(failed.id, "ko")


def test_a_gh_that_hangs_is_a_merge_that_did_not_land(s, project, git_on_path, monkeypatch):
    """`gh` ran with no timeout, so a forge that stopped answering held the merge job, and its worker thread,
    for good. A gh call that runs out of time is a merge that did not land: the grant stays unspent and the
    owner is asked to approve the merge again."""
    import time

    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    git_on_path.mkdir()
    gh = git_on_path / "gh"
    gh.write_text('#!/bin/sh\ncase "$2" in view) echo \'{"state": "OPEN"}\'; exit 0;; esac\nexec sleep 30\n')
    gh.chmod(0o755)
    monkeypatch.setattr(worker, "_GH_TIMEOUT_S", 1, raising=False)
    repo = _product_repo(f"s11-gh-hangs-{project.id}")
    job, _, grant = _merge_ready(s, project, repo, "ai/spec-s11gh-a1", {"f.py": "x\n"})
    job.pr_url = "https://github.com/o/r/pull/1"
    s.commit()

    started = time.monotonic()
    worker.merge(job.id)

    assert time.monotonic() - started < 10, "gh was given up on"
    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED" and "1s" in done.error, done.error
    assert s.get(Grant, grant.id).uses == 0
    assert len(_merge_asked_again(s, job)) == 1


def _pending_merge(s, job_id: int):
    from sqlalchemy import select

    from justshipit.db.models import AgentRequest
    return s.scalar(select(AgentRequest).where(AgentRequest.status == "PENDING", AgentRequest.action_kind == "dev.merge",
                                               AgentRequest.action_params["dev_job_id"].as_integer() == job_id))


def _decide(s, request_id: int, decision: str) -> None:
    """The owner's decision in the Approval Center, then whatever it queued for the dev job, run by the worker."""
    from sqlalchemy import select

    from justshipit.contracts.requests import HumanDecision, HumanResponse
    from justshipit.db.models import Job
    from justshipit.services import requests

    req = requests.respond(s, request_id, HumanResponse(decision=HumanDecision(decision)))
    s.commit()
    for qjob in s.scalars(select(Job).where(Job.status == "queued",
                                            Job.payload["dev_job_id"].as_integer() == req.action_params["dev_job_id"])):
        _run_queued(s, qjob.id, qjob.payload)


def test_a_merge_the_owner_puts_on_hold_leaves_two_ways_on(s, project, monkeypatch):
    """보류 on a merge request left the delivered job with no next action: no alert, retry_dev refusing a second
    branch, and no way to ask for the merge again. The job stays DELIVERED with a note, and the owner either asks for
    the merge again or throws the result away, which starts a new attempt. (반려 starts a fix: tests/test_dev_fix.py.)"""
    decision, word = "defer", "보류"
    from fastapi import HTTPException
    from sqlalchemy import select

    from justshipit.api import console
    from justshipit.db.models import DevJob, Job, Spec
    from justshipit.engineering import worker
    from justshipit.settings import settings

    job, qjob = _acceptance_queued(s, project, monkeypatch, [None])
    repo = settings().products_dir / s.get(Spec, job.spec_id).handoff["target"]["repository"]
    _sh(repo, "git", "checkout", "-qb", "ai/spec-s11-a1")
    (repo / "f.py").write_text("x\n")
    _engineer_commit(repo)
    _sh(repo, "git", "checkout", "-q", "main")
    job.branch = "ai/spec-s11-a1"
    s.commit()
    _run_queued(s, qjob.id, {"dev_job_id": job.id})
    _decide(s, _pending_merge(s, job.id).id, decision)

    turned_down = s.get(DevJob, job.id)
    assert (turned_down.status, turned_down.error) == ("DELIVERED", None)
    assert word in worker.step_line(turned_down.steps[-1], "ko"), turned_down.steps[-1]
    alert = _alert_for(job.id)
    assert (alert["kind"], alert["actions"], alert["request_id"]) == ("dev_delivered", ["merge_again", "discard"], None)
    assert word in alert["detail"], alert

    asked = console.dev_merge_again(job.id)["request_id"]

    s.expire_all()
    assert _pending_merge(s, job.id).id == asked and _alert_for(job.id) is None
    with pytest.raises(HTTPException, match="기다리고 있어요"):
        console.dev_discard(job.id)
    _decide(s, asked, "defer")

    new_id = console.dev_discard(job.id)["dev_job_id"]

    s.expire_all()
    new = s.get(DevJob, new_id)
    assert (s.get(DevJob, job.id).status, new.status, new.attempt) == ("DISCARDED", "QUEUED", 2)
    assert s.scalar(select(Job).where(Job.kind == "dev_job", Job.payload["dev_job_id"].as_integer() == new_id))
    assert s.get(Spec, job.spec_id).status == "READY_FOR_DEV"
    with pytest.raises(HTTPException, match="DISCARDED"):
        console.dev_merge_again(job.id)


@pytest.mark.parametrize("closes", [True, False])
def test_a_result_is_thrown_away_only_once_its_pr_is_closed(s, project, git_on_path, closes):
    """The new attempt opens a PR of its own, so the old one is closed first: a PR left open could still be merged
    on the forge beside the new one. A PR the forge will not close keeps the result."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    calls = git_on_path / "gh-calls"
    git_on_path.mkdir()
    (git_on_path / "gh").write_text(f'#!/bin/sh\necho "$@" >> {calls}\ncase "$2" in\n'
                                    'view) echo \'{"state": "OPEN"}\';;\n'
                                    f'close) {"exit 0" if closes else "echo HTTP 502 >&2; exit 1"};;\nesac\n')
    (git_on_path / "gh").chmod(0o755)
    job, spec, _ = _approved_job(s, project, _product_repo(f"s11-pr-{closes}-{project.id}").name, "DELIVERED")
    job.branch, job.pr_url = "ai/spec-s11pr-a1", "https://github.com/o/r/pull/7"
    job.result = {"acceptance": {"accepted": True, "criteria_results": [], "notes": ""}}
    s.commit()

    if closes:
        new_id = worker.discard(job.id, "ko")
    else:
        with pytest.raises(ValueError, match="HTTP 502"):
            worker.discard(job.id, "ko")

    s.expire_all()
    assert "pr close https://github.com/o/r/pull/7" in calls.read_text()
    assert s.get(DevJob, job.id).status == ("DISCARDED" if closes else "DELIVERED")
    assert s.query(DevJob).filter_by(spec_id=spec.id).count() == (2 if closes else 1)
    if closes:
        assert s.get(DevJob, new_id).attempt == 2


def test_a_result_the_owner_already_merged_is_recorded_merged_instead_of_thrown_away(s, project):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    repo = _product_repo(f"s11-discard-merged-{project.id}")
    job, spec, _ = _merge_ready(s, project, repo, "ai/spec-s11dm-a1", {"f.py": "x\n"})
    _sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "merge", "-q", "--no-ff", "-m", "by hand", job.branch)

    with pytest.raises(ValueError, match="MERGED|머지"):
        worker.discard(job.id, "ko")

    s.expire_all()
    assert s.get(DevJob, job.id).status == "MERGED"
    assert s.query(DevJob).filter_by(spec_id=spec.id).count() == 1


def test_a_spec_whose_failed_attempt_opened_a_pr_gets_no_second_pr(s, project, git_on_path):
    """The queue reclaimed a live dev job after two hours and recorded it FAILED, and the first run went on to push
    and open its PR (the ship writes pr_url whatever the move said). retry_dev then opened a second PR for the spec.
    It names the open PR instead, and the owner's way on is to throw that result away, which closes the PR."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import specs, worker

    url = "https://github.com/o/r/pull/9"
    git_on_path.mkdir()
    (git_on_path / "gh").write_text('#!/bin/sh\ncase "$2" in view) echo \'{"state": "CLOSED"}\';; '
                                    f'list) echo \'[{{"url": "{url}"}}]\';; esac\nexit 0\n')
    (git_on_path / "gh").chmod(0o755)
    job, spec, _ = _approved_job(s, project, _product_repo(f"s11-second-pr-{project.id}").name, "FAILED")
    job.branch, job.pr_url, job.error = "ai/spec-s11sp-a1", url, "개발 작업: 상태 REVIEWING에서는 처음부터 다시 실행하지 않습니다."
    job.result = {"branch": job.branch, "pr_url": url}
    s.commit()

    with pytest.raises(ValueError, match=url):
        specs.retry_dev(spec.spec_key, "ko")
    assert s.query(DevJob).filter_by(spec_id=spec.id).count() == 1
    assert _alert_for(job.id)["actions"] == ["discard"]

    new_id = worker.discard(job.id, "ko")

    s.expire_all()
    assert (s.get(DevJob, job.id).status, s.get(DevJob, new_id).attempt) == ("DISCARDED", 2)


@pytest.mark.parametrize("rel", [
    ".gitattributes", "skills/candidates/x/v1/.gitattributes", "a/.GitModules", "a/.lfsconfig.", ".git/config",
    "skills/x/.git/hooks/post-commit",
    # Names a file system opens as one of those: NTFS streams, an 8.3 short name, case folding on APFS or NTFS
    # (dotless i, long s), a code point HFS+ ignores, and a trailing space Windows drops.
    ".gitattributes::$DATA", ".git::$INDEX_ALLOCATION/config", "GIT~1/config", ".g\u0131tattributes",
    ".gitmodule\u017f", ".git\u200cattributes", ".gitattributes\u3000",
])
def test_the_registry_never_writes_a_file_git_reads_as_its_own_settings(rel):
    """Only --attr-source kept a .gitattributes in the registry tree from selecting a filter, and git 2.39 has no
    such option. A path git reads as attributes, submodules, LFS settings or the repository itself is refused, so
    the registry can drop the option on an old git. The skill builder, an agent, names its package's files, so a
    name is ASCII with no stream or short-name syntax."""
    from justshipit.services import registry

    with pytest.raises(ValueError, match="registry path"):
        registry.write_files({"projects/ok.md": "ok\n", rel: "* filter=owner\n"}, "planted")
    planted = registry.root() / rel
    assert not planted.exists() or "filter=owner" not in planted.read_text(), "the registry's own git file is kept"
    assert not (registry.root() / "projects/ok.md").exists(), "nothing of the refused write lands"


@pytest.mark.parametrize("version, knows", [("2.39.2", False), ("2.45.1", True)])
def test_the_registry_keeps_working_on_a_git_without_attr_source(git_on_path, caplog, version, knows):
    """Apple Git 2.39 refused --attr-source, so every artifact, spec draft and skill write failed and the company
    stopped. The registry drops only that option on a git older than 2.41, keeps every other protection, and says
    so once. Engineering still refuses that git."""
    import logging

    from justshipit.engineering import worker
    from justshipit.services import registry

    calls = git_on_path / "calls"
    _host_git(git_on_path, f'echo "$GIT_ATTR_NOSYSTEM $*" >> {calls}\n'
                           + ('' if knows else 'case "$1" in --attr-source=*) echo "unknown option: $1" >&2; exit 129;; '
                                               'esac\n')
                           + 'exec "$REAL" "$@"', version=f"{version}.s11")

    with caplog.at_level(logging.WARNING):
        sha = registry.write_files({f"projects/s11/{version}.md": "one\n"}, "one")
        registry.write_files({f"projects/s11/{version}.md": "two\n"}, "two")

    assert registry.read_at(f"projects/s11/{version}.md", sha) == "one"
    commits = [line for line in calls.read_text().splitlines() if " commit " in f" {line} "]
    assert commits and all(line.startswith("1 ") and "core.hooksPath=/dev/null" in line for line in commits), commits
    assert all(("--attr-source=" in line) == knows for line in commits), commits
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "attr-source" in r.getMessage()]
    assert len(warnings) == (0 if knows else 1) and all("2.45.1" in w for w in warnings), warnings
    if not knows:
        assert "--attr-source" in worker.host_git_refusal("ko")


@pytest.mark.parametrize("via", ["local", "pr"])
@pytest.mark.parametrize("status", ["FAILED", "MERGED", "DISCARDED"])
def test_a_job_that_is_not_delivered_never_runs_a_merge(s, project, git_on_path, status, via):
    """A merge approval can outlive the job it was for: the job failed, was merged by other means, or the owner
    threw its result away. Its merge moves no branch, runs no `gh pr merge`, and spends no grant."""
    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    calls = git_on_path / "gh-calls"
    git_on_path.mkdir()
    (git_on_path / "gh").write_text(f'#!/bin/sh\necho "$@" >> {calls}\nexit 0\n')
    (git_on_path / "gh").chmod(0o755)
    repo = _product_repo(f"s11-guard-{status.lower()}-{via}-{project.id}")
    job, _, grant = _merge_ready(s, project, repo, "ai/spec-s11g-a1", {"f.py": "x\n"})
    job.status, job.pr_url = status, "https://github.com/o/r/pull/3" if via == "pr" else None
    s.commit()
    base = _sh(repo, "git", "rev-parse", "main")

    worker.merge(job.id)

    s.expire_all()
    assert _sh(repo, "git", "rev-parse", "main") == base
    assert "pr merge" not in (calls.read_text() if calls.exists() else "")
    assert (s.get(DevJob, job.id).status, s.get(Grant, grant.id).uses) == (status, 0)


def test_a_merge_never_lands_under_a_rebase_of_base_in_another_worktree(s, project, tmp_path):
    """A rebase runs on a detached HEAD, so the worktree rebasing main does not hold main, no checkout does, and the
    merge moved main by ref alone under the rebase, which then wrote its own result over the merge. Every worktree
    of the product repo is checked for an operation in progress."""
    import subprocess

    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    repo = _product_repo(f"s11-third-{project.id}")
    job, _, grant = _merge_ready(s, project, repo, "ai/spec-s11t-a1", {"ai.txt": "ai\n"})
    _sh(repo, "git", "checkout", "-qb", "owner-work")
    (repo / "README.md").write_text("owner work\n")
    _sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qam", "owner work")
    third = tmp_path / "owner-rebasing"
    _sh(repo, "git", "worktree", "add", "-q", str(third), "main")
    (third / "README.md").write_text("main edit\n")
    _sh(third, "git", "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qam", "main edit")
    subprocess.run(["git", "-c", "user.name=o", "-c", "user.email=o@x", "rebase", "owner-work"], cwd=third,
                   capture_output=True, check=False)
    rebasing = third / _sh(third, "git", "rev-parse", "--git-path", "rebase-merge")
    assert rebasing.exists()
    main = _sh(repo, "git", "rev-parse", "main")

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED" and str(third) in done.error and "git rebase --abort" in done.error, done.error
    assert _sh(repo, "git", "rev-parse", "main") == main and rebasing.exists()
    assert s.get(Grant, grant.id).uses == 0


@pytest.mark.parametrize("where", ["global", "repo"])
def test_the_owners_autostash_never_stashes_their_edit_under_the_merge(s, project, tmp_path, monkeypatch, where):
    """merge.autoStash=true made the fast-forward stash the owner's uncommitted edit, land over it, and write the
    stash back as a conflict: the file ended up full of conflict markers, with the stash left behind. Host git
    reads none of the owner's global or system config, and the product repo's own autoStash is off."""
    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    repo = _product_repo(f"s12-autostash-{where}-{project.id}")
    job, _, grant = _merge_ready(s, project, repo, f"ai/spec-s12a{where[0]}-a1", {"README.md": "engineer\n"})
    if where == "global":
        owner_config = tmp_path / "gitconfig"
        owner_config.write_text("[merge]\n\tautoStash = true\n")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(owner_config))
    else:
        _sh(repo, "git", "config", "merge.autoStash", "true")
    (repo / "README.md").write_text("owner's draft\n")
    before = _owner_snapshot(repo)

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED" and re.search("[가-힣]", done.error), done.error
    assert _owner_snapshot(repo) == before
    assert _sh(repo, "git", "stash", "list") == ""
    assert s.get(Grant, grant.id).uses == 0


@pytest.mark.parametrize("where", ["global", "repo"])
def test_the_owners_default_merge_driver_never_runs_on_the_approved_merge(s, project, tmp_path, monkeypatch, where):
    """merge.default names the driver for every file no attribute names, and attributes are off, so the owner's
    driver ran on engineer content and decided the merge."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    repo = _product_repo(f"s12-driver-{where}-{project.id}")
    (repo / "shared.txt").write_text("a\nb\nc\n")
    _sh(repo, "git", "add", "shared.txt")
    _sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qm", "shared")
    job, _, _ = _merge_ready(s, project, repo, f"ai/spec-s12d{where[0]}-a1", {"shared.txt": "A\nb\nc\n"})
    (repo / "shared.txt").write_text("a\nb\nC\n")
    _sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qam", "owner edit")
    marker = tmp_path / "driver-ran"
    driver = f"touch '{marker}'; cp %B %A; exit 0"
    if where == "global":
        owner_config = tmp_path / "gitconfig"
        owner_config.write_text(f"[merge]\n\tdefault = owner\n[merge \"owner\"]\n\tdriver = {driver}\n")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(owner_config))
    else:
        _sh(repo, "git", "config", "merge.default", "owner")
        _sh(repo, "git", "config", "merge.owner.driver", driver)

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert not marker.exists(), "the owner's driver ran on engineer content"
    assert (done.status, done.error) == ("MERGED", None)
    assert (repo / "shared.txt").read_text() == "A\nb\nC\n"


def test_a_push_still_asks_the_owners_credential_helper(s, project, tmp_path, monkeypatch, git_on_path):
    """The push is the one host git command that keeps the owner's global config: their credential helper lives
    there, and a push moves refs only, never checking content out."""
    import http.server
    import threading

    from justshipit.engineering import worker

    class Forge(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="forge"')
            self.end_headers()

        do_POST = do_GET

        def log_message(self, *args):
            pass

    forge = http.server.HTTPServer(("127.0.0.1", 0), Forge)
    threading.Thread(target=forge.serve_forever, daemon=True).start()
    asked = tmp_path / "helper-asked"
    helper = tmp_path / "owner-helper"
    helper.write_text(f"#!/bin/sh\necho \"$1\" >> '{asked}'\n[ \"$1\" = get ] && printf 'username=o\\npassword=p\\n'\nexit 0\n")
    os.chmod(helper, 0o755)
    owner_config = tmp_path / "gitconfig"
    owner_config.write_text(f"[credential]\n\thelper = {helper}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(owner_config))
    for name in ("http_proxy", "HTTP_PROXY"):
        monkeypatch.delenv(name, raising=False)
    git_on_path.mkdir()
    (git_on_path / "gh").write_text("#!/bin/sh\nexit 1\n")
    (git_on_path / "gh").chmod(0o755)
    repo = _product_repo(f"s12-push-{project.id}")
    _sh(repo, "git", "remote", "add", "origin", f"http://127.0.0.1:{forge.server_port}/product.git")
    branch = "ai/spec-s12p-a1"
    _sh(repo, "git", "checkout", "-qb", branch)
    (repo / "ai.txt").write_text("ai\n")
    _engineer_commit(repo)
    _sh(repo, "git", "checkout", "-q", "main")
    job, spec, h = _approved_job(s, project, repo.name, "REVIEWING")
    job.branch = branch
    s.commit()

    try:
        with pytest.raises(worker.DevJobStopped, match="브랜치를 올리지 못했어요"):
            worker._ship(job.id, spec.id, repo, branch, _sh(repo, "git", "rev-parse", branch), h, "ok", None, "ko")
    finally:
        forge.shutdown()

    assert asked.exists() and "get" in asked.read_text().split(), "the owner's credential helper was never asked"


@pytest.mark.parametrize("when", ["before", "during"])
def test_an_acceptance_that_ends_after_the_merge_changes_nothing(s, project, monkeypatch, when):
    """Two acceptances of one job can run (a queue job reclaimed beside the live one). The one that finished after
    the owner's merge landed set the spec back from MERGED and filed a second merge request. A verdict is recorded
    only on a job the transition table still lets stay DELIVERED."""
    from justshipit.db.models import AgentRequest, DevJob, Spec
    from justshipit.engineering import worker

    job, qjob = _acceptance_queued(s, project, monkeypatch, [None])
    cpo, ran = worker.run_agent, []

    def merged(agent_id, purpose, prompt, model, **kw):
        ran.append(purpose)
        with worker.session_scope() as inner:
            inner.get(DevJob, job.id).status = "MERGED"
            inner.get(Spec, job.spec_id).status = "MERGED"
        return cpo(agent_id, purpose, prompt, model, **kw)

    if when == "before":
        job.status, s.get(Spec, job.spec_id).status = "MERGED", "MERGED"
        s.commit()
    else:
        monkeypatch.setattr(worker, "run_agent", merged)

    _run_queued(s, qjob.id, {"dev_job_id": job.id})

    done = s.get(DevJob, job.id)
    assert (done.status, done.error, s.get(Spec, job.spec_id).status) == ("MERGED", None, "MERGED")
    assert ran == ([] if when == "before" else ["acceptance"])
    assert [r for r in s.query(AgentRequest).filter_by(project_id=project.id, action_kind="dev.merge")
            if r.action_params.get("dev_job_id") == job.id] == []


def test_the_owner_cannot_ask_for_the_acceptance_again_while_a_merge_is_queued(s, project, monkeypatch):
    from justshipit.db.models import AgentRequest
    from justshipit.engineering import worker
    from justshipit.services import queue

    job, qjob = _acceptance_queued(s, project, monkeypatch, [None])
    _run_queued(s, qjob.id, {"dev_job_id": job.id})
    asked = next(r for r in s.query(AgentRequest).filter_by(action_kind="dev.merge")
                 if r.action_params.get("dev_job_id") == job.id)
    asked.status = "APPROVED"
    queue.enqueue(s, "dev_merge", {"dev_job_id": job.id}, idempotency_key=f"dev_merge:{asked.id}")
    s.commit()

    with pytest.raises(ValueError, match="진행 중"):
        worker.accept_again(job.id, "ko")


def test_a_merge_is_refused_while_a_worktree_of_the_product_repo_is_gone(s, project, tmp_path):
    """A linked worktree whose directory the owner deleted stays in `worktree list` as prunable. The filter never
    matched (the line is `prunable <reason>`), so the merge ran git in the missing directory and the owner got a
    host error instead of a refusal that names the way on."""
    from justshipit.db.models import DevJob, Grant
    from justshipit.engineering import worker

    repo = _product_repo(f"s12-prunable-{project.id}")
    job, _, grant = _merge_ready(s, project, repo, "ai/spec-s12w-a1", {"ai.txt": "ai\n"})
    _sh(repo, "git", "checkout", "-qb", "owner-work")
    gone = tmp_path / "gone"
    _sh(repo, "git", "worktree", "add", "-q", str(gone), "main")
    shutil.rmtree(gone)
    main = _sh(repo, "git", "rev-parse", "main")

    worker.merge(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert done.status == "DELIVERED" and "git worktree prune" in done.error and re.search("[가-힣]", done.error), done.error
    assert str(gone) in done.error
    assert _sh(repo, "git", "rev-parse", "main") == main and s.get(Grant, grant.id).uses == 0


@pytest.mark.parametrize("known", ["pr_url", "pushed"])
@pytest.mark.parametrize("action", ["discard", "retry"])
def test_a_failed_job_whose_pr_the_owner_merged_on_the_forge_is_recorded_merged(s, project, git_on_path, action,
                                                                                 known):
    """retry_dev's refusal tells the owner to review and merge the PR a FAILED job opened. After that merge the job
    could never move: FAILED had no way to MERGED, discard answered that the PR was merged, and retry that it
    existed. The owner's next action asks the forge and records MERGED, and spends no merge grant the project
    holds for another job. A job whose gh pr create timed out knows only that it pushed; the forge names the PR."""
    from datetime import datetime, timedelta, timezone

    from justshipit.db.models import DevJob, Grant, Spec
    from justshipit.engineering import specs, worker

    url = "https://github.com/o/r/pull/11"
    git_on_path.mkdir()
    (git_on_path / "gh").write_text('#!/bin/sh\ncase "$2" in view) echo \'{"state": "MERGED"}\'; exit 0;; '
                                    f'list) [ "$6" = merged ] && echo \'[{{"url": "{url}"}}]\' || echo "[]"; exit 0;; '
                                    'close) echo "already merged" >&2; exit 1;; esac\nexit 1\n')
    (git_on_path / "gh").chmod(0o755)
    job, spec, _ = _approved_job(s, project, _product_repo(f"s13-landed-{action}-{known}-{project.id}").name, "FAILED")
    job.branch = "ai/spec-s13m-a1"
    job.pr_url = url if known == "pr_url" else None
    job.result = {"branch": job.branch, "pr_url": job.pr_url} if known == "pr_url" else {"branch": job.branch,
                                                                                         "pushed": True}
    grant = Grant(request_id=spec.approval_request_id, project_id=project.id, action_kind="dev.merge",
                  expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    s.add(grant)
    s.commit()

    with pytest.raises(ValueError, match="MERGED|머지"):
        worker.discard(job.id, "ko") if action == "discard" else specs.retry_dev(spec.spec_key, "ko")

    s.expire_all()
    merged = s.get(DevJob, job.id)
    assert (merged.status, merged.pr_url, s.get(Spec, spec.id).status) == ("MERGED", url, "MERGED")
    assert s.query(DevJob).filter_by(spec_id=spec.id).count() == 1
    assert _alert_for(job.id) is None
    assert s.get(Grant, grant.id).uses == 0


def _shipping_to_a_forge(s, project, git_on_path, tmp_path, monkeypatch, name: str, gh_body: str):
    from justshipit.engineering import worker

    git_on_path.mkdir()
    (git_on_path / "gh").write_text(f'#!/bin/sh\necho "$@" >> {git_on_path / "calls"}\n{gh_body}')
    (git_on_path / "gh").chmod(0o755)
    repo = _product_repo(f"{name}-{project.id}")
    bare = tmp_path / "origin.git"
    _sh(tmp_path, "git", "init", "-q", "--bare", str(bare))
    _sh(repo, "git", "remote", "add", "origin", str(bare))
    branch, wt = f"ai/spec-{name}-a1", tmp_path / "wt"
    _sh(repo, "git", "worktree", "add", "-q", "-B", branch, str(wt), "main")
    (wt / "f.py").write_text("x\n")
    job, spec, h = _approved_job(s, project, repo.name, "REVIEWING")
    job.attempt, job.branch = 1, branch
    s.commit()
    monkeypatch.setattr(worker, "_containerized", lambda: False)
    return job, spec, h, repo, wt, branch


def test_a_pr_the_forge_made_while_gh_pr_create_timed_out_stops_a_second_attempt(s, project, git_on_path, tmp_path,
                                                                                   monkeypatch):
    """gh pr create reached the forge but did not answer in time, so the job was FAILED with no record that it had
    pushed, and retry_dev queued a second attempt, a second PR for the spec. The push is recorded the moment it
    lands, and a new attempt asks the forge about every attempt that pushed."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import specs, worker

    url = "https://github.com/o/r/pull/12"
    job, spec, h, repo, wt, branch = _shipping_to_a_forge(
        s, project, git_on_path, tmp_path, monkeypatch, "s13-timeout",
        f'case "$2" in create) sleep 5;; list) [ "$6" = open ] && echo \'[{{"url": "{url}"}}]\' || echo "[]";; '
        'close) exit 0;; esac\n')
    monkeypatch.setattr(worker, "_GH_TIMEOUT_S", 1)

    with pytest.raises(worker.DevJobStopped):
        _review_and_ship(job, spec, repo, wt, branch, h)
    s.expire_all()
    failed = s.get(DevJob, job.id)
    assert (failed.status, failed.pr_url, (failed.result or {}).get("pushed")) == ("FAILED", None, True)

    with pytest.raises(ValueError, match=url):
        specs.retry_dev(spec.spec_key, "ko")
    assert f"pr list --head {branch} --state open" in (git_on_path / "calls").read_text()
    assert s.query(DevJob).filter_by(spec_id=spec.id).count() == 1
    assert _alert_for(job.id)["actions"] == ["deliver_again", "discard"]

    new_id = worker.discard(job.id, "ko")

    assert f"pr close {url}" in (git_on_path / "calls").read_text()
    s.expire_all()
    assert (s.get(DevJob, job.id).status, s.get(DevJob, new_id).attempt) == ("DISCARDED", 2)


def test_delivering_again_after_gh_pr_create_timed_out_records_the_pr_the_forge_made(s, project, git_on_path,
                                                                                      tmp_path, monkeypatch):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    url = "https://github.com/o/r/pull/14"
    job, spec, h, repo, wt, branch = _shipping_to_a_forge(
        s, project, git_on_path, tmp_path, monkeypatch, "s13-again",
        f'case "$2" in create) sleep 5;; list) [ "$6" = open ] && echo \'[{{"url": "{url}"}}]\' || echo "[]";; esac\n')
    monkeypatch.setattr(worker, "_GH_TIMEOUT_S", 1)
    with pytest.raises(worker.DevJobStopped):
        _review_and_ship(job, spec, repo, wt, branch, h)

    worker.deliver_again(job.id, "ko")
    worker.run_delivery(job.id)

    s.expire_all()
    done = s.get(DevJob, job.id)
    assert (done.status, done.pr_url) == ("DELIVERED", url)
    assert (git_on_path / "calls").read_text().count("pr create") == 1, "no second PR for the branch"


def test_a_failed_job_that_pushed_but_opened_no_pr_is_thrown_away_without_closing_one(s, project, git_on_path):
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker

    git_on_path.mkdir()
    (git_on_path / "gh").write_text(f'#!/bin/sh\necho "$@" >> {git_on_path / "calls"}\n'
                                    'case "$2" in list) echo "[]";; *) echo "no pull requests found" >&2; exit 1;; esac\n')
    (git_on_path / "gh").chmod(0o755)
    job, _, _ = _approved_job(s, project, _product_repo(f"s13-no-pr-{project.id}").name, "FAILED")
    job.branch = "ai/spec-s13n-a1"
    job.result = {"branch": job.branch, "pushed": True}
    s.commit()
    assert _alert_for(job.id)["actions"] == ["discard"]

    new_id = worker.discard(job.id, "ko")

    assert "pr close" not in (git_on_path / "calls").read_text()
    s.expire_all()
    assert (s.get(DevJob, job.id).status, s.get(DevJob, new_id).attempt) == ("DISCARDED", 2)


def test_a_pr_a_reclaimed_run_opens_stops_a_retry_before_its_delivery_is_written(s, project, git_on_path, tmp_path,
                                                                                 monkeypatch):
    """The queue recorded the job FAILED while its first run went on to push and open the PR, and the run wrote
    pr_url only after the delivery document: a retry in between queued a second PR. The PR is recorded the moment
    the forge has it, whatever the job's status."""
    from justshipit.db.models import DevJob
    from justshipit.engineering import specs, worker

    url = "https://github.com/o/r/pull/13"
    job, spec, h, repo, wt, branch = _shipping_to_a_forge(
        s, project, git_on_path, tmp_path, monkeypatch, "s13-reclaimed",
        f'case "$2" in create) echo "{url}";; list) echo \'[{{"url": "{url}"}}]\';; esac\n')
    retried = {}
    write_files = worker.registry.write_files

    def reclaimed_then_retried(files, message):
        worker._record_failure(job.id, "개발 작업 J: 큐가 다시 가져갔습니다")
        try:
            retried["new attempt"] = specs.retry_dev(spec.spec_key, "ko")
        except ValueError as exc:
            retried["refusal"] = str(exc)
        return write_files(files, message)

    monkeypatch.setattr(worker.registry, "write_files", reclaimed_then_retried)

    _review_and_ship(job, spec, repo, wt, branch, h)

    assert url in retried.get("refusal", ""), retried
    s.expire_all()
    assert (s.get(DevJob, job.id).status, s.get(DevJob, job.id).pr_url) == ("FAILED", url)
    assert s.query(DevJob).filter_by(spec_id=spec.id).count() == 1


@pytest.mark.parametrize("forge", ["hangs", "errors", "says nothing"])
def test_no_new_attempt_starts_while_the_forge_cannot_say_whether_a_pushed_attempt_has_an_open_pr(
        s, project, git_on_path, monkeypatch, forge):
    from justshipit.db.models import DevJob
    from justshipit.engineering import specs, worker

    git_on_path.mkdir()
    (git_on_path / "gh").write_text("#!/bin/sh\n" + {"hangs": "exec sleep 30\n", "errors": "echo 'HTTP 502' >&2; exit 1\n",
                                                      "says nothing": "exit 0\n"}[forge])
    (git_on_path / "gh").chmod(0o755)
    monkeypatch.setattr(worker, "_GH_TIMEOUT_S", 1)
    job, spec, _ = _approved_job(s, project, _product_repo(f"s13-forge-{forge.replace(' ', '-')}-{project.id}").name,
                                 "FAILED")
    job.branch = "ai/spec-s13f-a1"
    job.result = {"branch": job.branch, "pushed": True}
    s.commit()

    with pytest.raises(ValueError, match="GitHub에서 답이 없어서"):
        specs.retry_dev(spec.spec_key, "ko")
    assert s.query(DevJob).filter_by(spec_id=spec.id).count() == 1


@pytest.mark.parametrize("owner_config", [False, True])
def test_config_the_workers_environment_carries_never_reaches_host_git(tmp_path, monkeypatch, owner_config):
    """GIT_CONFIG_PARAMETERS (what `git -c` hands its children) and GIT_CONFIG_COUNT with its KEY_n and VALUE_n
    pairs set config from the environment, so a worker started from the owner's shell or a git alias passed it
    to every host git around the isolation. Only the push keeps it, with the rest of the owner's config."""
    from justshipit.sandbox import hostgit

    repo = _product_repo(f"s13-env-config-{owner_config}")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.pager'='owner-pager'")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "diff.external")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "owner-diff")

    seen = [hostgit.run(repo, "config", "--get", key, owner_config=owner_config).stdout.strip()
            for key in ("core.pager", "diff.external")]

    assert seen == (["owner-pager", "owner-diff"] if owner_config else ["", ""])


def test_a_stalled_dev_job_can_be_retried_and_is_closed_as_failed(s, project):
    from justshipit.db.models import DevJob, Spec
    from justshipit.engineering import specs
    from justshipit.services import queue

    job = _queued_job(s, project)
    spec_key = s.get(Spec, job.spec_id).spec_key
    job.status = "IMPLEMENTING"
    s.commit()
    retry_id = specs.retry_dev(spec_key, "ko")
    s.expire_all()
    assert s.get(DevJob, job.id).status == "FAILED"
    assert "맡은 백그라운드 프로세스가 없어서" in s.get(DevJob, job.id).error
    assert s.get(DevJob, retry_id).status == "QUEUED"
    queue.enqueue(s, "dev_job", {"dev_job_id": retry_id}, max_attempts=1)  # a live job: the retry is in flight
    s.commit()
    with pytest.raises(ValueError):
        specs.retry_dev(spec_key, "ko")


def test_a_dev_job_step_does_not_write_once_the_lease_is_lost(s, project):
    from justshipit.contracts.handoff import DevJobStatus
    from justshipit.db.models import DevJob
    from justshipit.engineering import worker
    from justshipit.services import queue

    job = _queued_job(s, project)
    lease = queue.Lease(job_id=0, token="t")
    lease.lost.set()
    token = queue.current_lease.set(lease)
    try:
        with pytest.raises(queue.LeaseLost):
            worker._step(job.id, DevJobStatus.INTAKE, "reading approved spec", say="t")
        with pytest.raises(queue.LeaseLost):
            worker._fail(job.id, "engineer step failed")
    finally:
        queue.current_lease.reset(token)
    s.expire_all()
    assert (s.get(DevJob, job.id).status, s.get(DevJob, job.id).error) == ("QUEUED", None)


def test_a_failed_merge_job_marks_its_dev_job_failed_so_the_owner_has_a_path(s, project):
    from justshipit.db.models import DevJob, Job
    from justshipit.db.session import session_scope
    from justshipit.services import queue
    from sqlalchemy import delete

    job = _queued_job(s, project)
    job.status = "DELIVERED"
    s.execute(delete(Job).where(Job.kind == "dev_merge", Job.status == "queued"))  # queue.claim takes the oldest
    s.commit()
    with session_scope() as s2:
        jid = queue.enqueue(s2, "dev_merge", {"dev_job_id": job.id}, max_attempts=1).id
        claimed = queue.claim(s2, "worker-a", ["dev_merge"])
        assert claimed.id == jid
        queue.finish(s2, jid, claimed.lease_token, error="RuntimeError: gh pr merge failed: CONFLICT")
    s.expire_all()
    failed = s.get(DevJob, job.id)
    assert failed.status == "FAILED"
    assert "gh pr merge failed" in failed.error


def test_retry_dev_reclaims_a_dead_workers_job_first_and_refuses_in_korean_until_its_hold_ends(korean_owner, 
        s, project, dead_worker):
    from datetime import UTC, datetime, timedelta

    from justshipit.db.models import DevJob, Spec
    from justshipit.db.session import session_scope
    from justshipit.engineering import specs
    from justshipit.services import queue
    from sqlalchemy import text

    job = _queued_job(s, project)
    spec_key = s.get(Spec, job.spec_id).spec_key
    job.status = "IMPLEMENTING"
    s.commit()
    with session_scope() as s2:
        jid = queue.enqueue(s2, "dev_job", {"dev_job_id": job.id}, max_attempts=1).id
        s2.execute(text("UPDATE jobs SET status = 'running', locked_by = :w, lease_token = 't', locked_at = now(), "
                        "attempts = :n, max_attempts = :n WHERE id = :id"),  # the build's last try
                   {"id": jid, "w": dead_worker, "n": queue.BUILD_ATTEMPTS_AFTER_RECLAIM})
    with pytest.raises(ValueError, match="IMPLEMENTING") as refused:
        specs.retry_dev(spec_key, "ko")
    assert "실행 중이에요" in str(refused.value)
    s.expire_all()
    assert s.get(DevJob, job.id).status == "IMPLEMENTING"

    with session_scope() as s2:  # the worker died: its lease went stale
        s2.execute(text("UPDATE jobs SET locked_at = :t WHERE id = :id"),
                   {"id": jid, "t": datetime.now(UTC) - timedelta(hours=1)})
    with pytest.raises(ValueError, match="까지 기다려요"):  # its test container or CLI child may still run
        specs.retry_dev(spec_key, "ko")
    with session_scope() as s2:  # the worker's tick
        queue.reclaim(s2, {})
    s.expire_all()
    assert s.get(DevJob, job.id).status == "FAILED"

    with session_scope() as s2:
        s2.execute(text("UPDATE jobs SET held_until = now() WHERE id = :id"), {"id": jid})
    retry_id = specs.retry_dev(spec_key, "ko")
    s.expire_all()
    assert s.get(DevJob, job.id).status == "FAILED"
    assert s.get(DevJob, retry_id).status == "QUEUED"
