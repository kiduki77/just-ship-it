"""The owner cancels work that has not started: the row stays as cancelled, nothing claims it, and what it blocked
(archiving its project, asking for the same work again) opens. What the job would have moved ends where a job the
worker gave up leaves it, so the owner can start it again."""
import pytest
from fastapi.testclient import TestClient
from justshipit.api.app import app
from justshipit.contracts.handoff import Handoff
from justshipit.db.models import ActivityEvent, DevJob, Job, Project, Spec
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import projects, queue
from sqlalchemy import select, update


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


@pytest.fixture(autouse=True)
def _settled():
    yield
    with session_scope() as s:
        s.execute(update(Job).where(Job.payload.has_key("cancel_test")).values(status="done"))


def job(s, project, status="queued", kind="run_project", key=None) -> int:
    j = Job(gate="direct", kind=kind, payload={"project_id": project.id, "cancel_test": True}, status=status,
            idempotency_key=key, lock_key=f"project:{project.id}")
    s.add(j)
    s.commit()
    return j.id


@pytest.mark.parametrize("status", ["queued", "gated"])
def test_a_job_that_has_not_started_is_cancelled_and_kept(s, project, api, status):
    jid = job(s, project, status)

    r = api.post(f"/api/jobs/{jid}/cancel")
    assert r.status_code == 200 and r.json() == {"job_id": jid, "status": "cancelled", "was": status}
    with session_scope() as s2:
        row = s2.get(Job, jid)
        assert row.status == "cancelled" and row.finished_at is not None
        assert s2.scalar(select(ActivityEvent.verb).where(ActivityEvent.entity == "job", ActivityEvent.entity_id == jid,
                                                          ActivityEvent.verb == "cancelled")) == "cancelled"


def test_once_its_job_is_cancelled_the_project_can_be_archived(s, project, api):
    jid = job(s, project)
    with session_scope() as s2, pytest.raises(ValueError):
        projects.archive(s2, project.id)

    api.post(f"/api/jobs/{jid}/cancel")
    with session_scope() as s2:
        projects.archive(s2, project.id)
        assert s2.get(type(project), project.id).status == "ARCHIVED"


def test_the_same_work_can_be_asked_for_again_after_a_cancel(s, project, api):
    jid = job(s, project, key=f"run:{project.id}:cancel-test")
    api.post(f"/api/jobs/{jid}/cancel")
    with session_scope() as s2:
        again = queue.enqueue(s2, "run_project", {"project_id": project.id, "cancel_test": True},
                              idempotency_key=f"run:{project.id}:cancel-test", by_owner=True)
        assert again is not None and again.id != jid


@pytest.mark.parametrize("status", ["running", "done", "failed", "cancelled"])
def test_work_that_started_or_ended_is_not_cancelled(s, project, api, status):
    jid = job(s, project, status)
    r = api.post(f"/api/jobs/{jid}/cancel")
    assert r.status_code == 409
    with session_scope() as s2:
        assert s2.get(Job, jid).status == status


def test_an_approved_send_is_not_cancelled_here(s, project, api):
    jid = job(s, project, kind="execute_action")
    assert api.post(f"/api/jobs/{jid}/cancel").status_code == 409
    assert api.post("/api/jobs/99999999/cancel").status_code == 404


def test_a_cancelled_run_leaves_its_project_failed_so_it_can_be_started_again(s, project, api):
    jid = job(s, project)

    api.post(f"/api/jobs/{jid}/cancel")
    with session_scope() as s2:
        p = s2.get(Project, project.id)
        assert (p.status, p.error) == ("FAILED", msg("queue.cancelled.run", "en", id=jid))
        assert s2.scalar(select(Job.id).where(Job.kind == "notify", Job.idempotency_key.like(f"notify:failed:P-{project.id}:%")))             is None  # the owner did it; nothing pages them about it
    r = api.post(f"/api/projects/{project.id}/retry")
    assert r.status_code == 200, r.text
    with session_scope() as s2:
        assert s2.scalar(select(Job.id).where(Job.kind == "retry_project", Job.status == "queued",
                                              Job.payload["project_id"].as_integer() == project.id)) is not None
        s2.execute(update(Job).where(Job.kind == "retry_project", Job.payload["project_id"].as_integer() == project.id)
                   .values(status="done"))


def test_a_cancelled_build_leaves_its_dev_job_failed_and_its_spec_ready_again(s, project, api):
    handoff = Handoff(spec_id="SPEC-CANCEL", project="Test", title="취소 명세", source={"path": "specs/c", "commit_sha": "abc"},
                      target={"repository": "demo"}, approval={"spec_approved": True, "implementation_authorized": True})
    spec = Spec(spec_key=f"SPEC-CANCEL-{project.id}", project_id=project.id, title="취소 명세", registry_path="specs/c",
                status="IN_DEVELOPMENT", approved_commit_sha="abc", handoff=handoff.model_dump(mode="json"))
    s.add(spec)
    s.flush()
    d = DevJob(spec_id=spec.id, status="QUEUED")
    s.add(d)
    s.flush()
    j = Job(gate="direct", kind="dev_job", payload={"dev_job_id": d.id, "cancel_test": True}, status="queued",
            lock_key=f"dev_job:{d.id}")
    s.add(j)
    s.commit()

    assert api.post(f"/api/jobs/{j.id}/cancel").status_code == 200
    with session_scope() as s2:
        d2 = s2.get(DevJob, d.id)
        assert (d2.status, d2.error) == ("FAILED", msg("queue.cancelled.dev", "en", id=j.id))
        assert s2.get(Spec, spec.id).status == "READY_FOR_DEV"
        assert s2.get(Job, j.id).status == "cancelled"


def test_archive_with_one_confirm_cancels_the_work_that_has_not_started_and_keeps_the_rows(s, project, api):
    queued, gated = job(s, project, "queued"), job(s, project, "gated")

    r = api.post(f"/api/projects/{project.id}/archive", json={"cancel_waiting": True})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["cancelled_jobs"]) == sorted([queued, gated])
    with session_scope() as s2:
        assert s2.get(Project, project.id).status == "ARCHIVED"
        assert {s2.get(Job, j).status for j in (queued, gated)} == {"cancelled"}
        logged = set(s2.scalars(select(ActivityEvent.entity_id).where(ActivityEvent.entity == "job",
                                                                      ActivityEvent.verb == "cancelled")))
        assert {queued, gated} <= logged


def test_the_archive_confirm_names_exactly_the_runs_the_archive_cancels(s, project, api):
    queued, gated = job(s, project, "queued"), job(s, project, "gated")
    job(s, project, "done")
    other = projects.create(s, "other goal", name="Other", budget_usd=10, start=False)
    job(s, other, "queued")

    said = api.get(f"/api/projects/{project.id}/archive").json()["cancels_jobs"]
    r = api.post(f"/api/projects/{project.id}/archive", json={"cancel_waiting": True})

    assert r.status_code == 200, r.text
    assert sorted(said) == sorted(r.json()["cancelled_jobs"]) == sorted([queued, gated])
    assert api.get(f"/api/projects/{10**8}/archive").status_code == 404


def test_archive_without_the_confirm_still_refuses_while_work_waits(s, project, api):
    queued = job(s, project, "queued")
    assert api.post(f"/api/projects/{project.id}/archive", json={}).status_code == 409
    with session_scope() as s2:
        assert s2.get(Job, queued).status == "queued" and s2.get(Project, project.id).status != "ARCHIVED"


@pytest.mark.parametrize("busy", [{"status": "running"}, {"kind": "execute_action"}])
def test_running_work_or_an_approved_send_still_refuses_archive_and_cancels_nothing(s, project, api, busy):
    waiting = job(s, project, "gated")
    job(s, project, **{"status": "queued", **busy})

    r = api.post(f"/api/projects/{project.id}/archive", json={"cancel_waiting": True})
    assert r.status_code == 409
    with session_scope() as s2:
        assert s2.get(Job, waiting).status == "gated" and s2.get(Project, project.id).status != "ARCHIVED"


def test_the_queue_says_which_jobs_the_owner_can_cancel(s, project, api):
    ids = {job(s, project, "queued"): True, job(s, project, "gated"): True, job(s, project, "running"): False,
           job(s, project, "queued", kind="execute_action"): False}
    listed = {j["id"]: j["cancellable"] for j in api.get("/api/jobs?limit=200").json() if j["id"] in ids}
    assert listed == ids
