"""A project the console makes joins the business it is made in. Before, it started a business of its own, with no plan,
and the owner found that out two screens later."""
import pytest
from justshipit.api.app import app
from justshipit.db.models import Business, Job, Project
from justshipit.services import projects, roadmap
from bet_loop_kit import make_business
from fastapi.testclient import TestClient


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


@pytest.fixture
def no_queued_runs(s):
    """A project made through the API queues its run: the queue is put back as found, so no other test reads it."""
    before = {j for (j,) in s.query(Job.id)}
    yield
    s.rollback()
    for job in s.query(Job).filter(Job.id.notin_(before)):
        s.delete(job)
    s.commit()


def count_businesses(s) -> int:
    return s.query(Business).count()


def test_a_project_made_for_a_business_joins_it_and_makes_no_business_of_its_own(s, api, no_queued_runs):
    b = make_business(s)
    before = count_businesses(s)

    r = api.post("/api/projects", json={"goal": "서브레딧 두 곳에 올려 보기", "business_id": b.id})

    assert r.status_code == 200, r.text
    s.expire_all()
    assert s.get(Project, r.json()["id"]).business_id == b.id
    assert count_businesses(s) == before


def test_a_project_for_a_business_with_no_approved_plan_is_refused_and_says_so(s, api):
    b = roadmap.create_business(s, name="계획 없는 사업", one_liner="x")
    s.commit()
    before = count_businesses(s)

    r = api.post("/api/projects", json={"goal": "뭔가 해 보기", "business_id": b.id})

    assert r.status_code == 409 and "approve the plan first" in r.json()["detail"]
    assert count_businesses(s) == before


def test_a_project_for_an_archived_or_unknown_business_is_refused(s, api):
    b = make_business(s, name="보관할 사업")
    projects.archive_business(s, b.id, lang="en")
    s.commit()

    assert api.post("/api/projects", json={"goal": "g", "business_id": b.id}).status_code == 409
    assert api.post("/api/projects", json={"goal": "g", "business_id": 10**9}).status_code == 404
