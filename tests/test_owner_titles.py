"""A title stored the way an agent wrote it reads as the owner's title on every screen. The live 결정 panel headed
REQ-40 '의사결정 승인: 원두재고 웹앱: …', REQ-50 ended '(REQ-32)', and cards and the feed read 'REQ-36 30일 자동 삭제',
'(REQ-26 대체)' and 'T-30 저장본'."""
import pytest
from justshipit.api.app import app
from justshipit.contracts.requests import AgentRequestDraft, RequestType, RiskLevel
from justshipit.db.models import ActivityEvent, AgentRequest, Decision, Task
from justshipit.services import requests
from fastapi.testclient import TestClient
from sqlalchemy import update
from ask_kit import contract, by_project_manager

RAW = "의사결정 승인: 전단 앞면 (REQ-26 대체)"
SHOWN = "전단 앞면"


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


@pytest.fixture
def stored(s, project):
    """A request, a task and a decision whose stored titles carry codes, as rows from before the contracts did."""
    req = requests.create(s, AgentRequestDraft(
        type=RequestType.APPROVAL, title="전단 앞면", summary="s", rationale="r", options=["승인", "반려"],
        recommended_option="승인", action_kind="project.direction_change", estimated_cost_usd=None,
        risk_level=RiskLevel.LOW, context=contract()), from_agent="project_manager", project_id=project.id,
        origin=by_project_manager(s, project.id))
    task = Task(project_id=project.id, key="flyer", title="T-30 저장본", owner="marketing", objective="o",
                acceptance_criteria=[], status="READY")
    dec = Decision(project_id=project.id, title="REQ-36 30일 자동 삭제", decision="adopt", rationale="r",
                   supporting_evidence_ids=[], dissenting_evidence_ids=[], dissent_summary="", open_questions=[],
                   approval_required=False, status="EFFECTIVE")
    s.add_all([task, dec])
    s.flush()
    s.execute(update(AgentRequest).where(AgentRequest.id == req.id).values(title=RAW, status="APPROVED"))
    s.add(ActivityEvent(actor="project_manager", verb="created", entity="task", entity_id=task.id, project_id=project.id,
                        summary="x", data={"ref": f"T-{task.id}", "title": "OT-14에 붙일 장소별 QR"}))
    s.commit()
    return project, req.id, task.id


def test_a_request_reads_with_the_owner_title_in_its_panel_and_lists(api, stored):
    project, rid, _ = stored

    assert api.get(f"/api/requests/{rid}").json()["title"] == SHOWN
    assert {r["title"] for r in api.get("/api/inbox", params={"include_resolved": True}).json() if r["id"] == rid} == {SHOWN}


def test_the_projects_decided_list_tasks_and_decisions_read_without_codes(api, stored):
    project, rid, tid = stored

    body = api.get(f"/api/projects/{project.id}").json()

    assert {d["title"] for d in body["decided"]} == {SHOWN, "30일 자동 삭제"}
    assert [t["title"] for t in body["tasks"] if t["id"] == tid] == ["저장본"]
    assert [d["title"] for d in body["decisions"]] == ["30일 자동 삭제"]
    assert [t["title"] for t in api.get("/api/tasks", params={"project_id": project.id}).json()["tasks"]] == ["저장본"]


def test_the_feed_reads_an_events_title_without_codes(api, stored):
    project, _, tid = stored

    events = api.get("/api/activity", params={"project_id": project.id, "entity": "task"}).json()["events"]

    assert {e["data"]["title"] for e in events if e["entity_id"] == tid} == {"붙일 장소별 QR", "저장본"}
