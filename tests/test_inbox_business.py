"""The decision list a business shows: its own requests, the company-wide ones, and nobody else's."""
import pytest
from justshipit.api.app import app
from justshipit.db.models import AgentRequest
from justshipit.db.session import session_scope
from justshipit.services import projects, roadmap
from fastapi.testclient import TestClient
from sqlalchemy import update


@pytest.fixture(autouse=True)
def _quiet():
    with session_scope() as s:
        s.execute(update(AgentRequest).where(AgentRequest.status == "PENDING").values(status="CANCELLED"))
    yield


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def _request(s, title, project_id, status="PENDING") -> AgentRequest:
    r = AgentRequest(project_id=project_id, from_agent="project_manager", type="APPROVAL", title=title, summary="s", rationale="r",
                     status=status, policy_mode="approve")
    s.add(r)
    s.flush()
    return r


@pytest.fixture
def world(s):
    a = roadmap.create_business(s, name="사업 가", one_liner="가")
    b = roadmap.create_business(s, name="사업 나", one_liner="나")
    pa = projects.create(s, "목표", name="가 프로젝트", start=False, business=a)
    pb = projects.create(s, "목표", name="나 프로젝트", start=False, business=b)
    mine = _request(s, "가의 결정", pa.id)
    other = _request(s, "나의 결정", pb.id)
    loose = _request(s, "전사 결정", None)
    done = _request(s, "가가 끝낸 결정", pa.id, status="APPROVED")
    s.commit()
    return a, mine, other, loose, done


def test_a_business_sees_its_own_pending_requests_and_the_company_wide_ones(world, api):
    a, mine, other, loose, _ = world
    ids = {r["id"] for r in api.get(f"/api/inbox?business_id={a.id}").json()}
    assert ids == {mine.id, loose.id} and other.id not in ids


def test_without_a_business_every_pending_request_is_listed(world, api):
    _, mine, other, loose, _ = world
    assert {r["id"] for r in api.get("/api/inbox").json()} == {mine.id, other.id, loose.id}


def test_the_resolved_list_is_scoped_the_same_way(world, api):
    a, mine, other, loose, done = world
    ids = {r["id"] for r in api.get(f"/api/inbox?business_id={a.id}&include_resolved=true").json()}
    assert {mine.id, loose.id, done.id} <= ids and other.id not in ids
