"""D7: the project overview counts the decisions actually made (persona round 2026-11-04: '결정함 0건 / 기록된 결정 0'
after 5 to 18 decisions)."""
import pytest
from fastapi.testclient import TestClient

from justshipit.api.app import app
from justshipit.contracts.requests import HumanDecision, HumanResponse
from justshipit.db.models import AgentRequest
from justshipit.db.session import session_scope
from justshipit.services import requests


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000", headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def _req(s, pid: int, title: str, status: str = "PENDING", response: dict | None = None) -> int:
    r = AgentRequest(project_id=pid, from_agent="project_manager", type="PROPOSAL", title=title, summary="s", rationale="r",
                     status=status, policy_mode="approve", response=response)
    s.add(r)
    s.flush()
    return r.id


def test_the_owner_s_answers_and_the_project_manager_s_door_rule_calls_count_as_decisions(s, project, api):
    answered = [_req(s, project.id, f"결정 {i}") for i in range(3)]
    project_manager = _req(s, project.id, "CEO가 정한 일", "AUTO_APPROVED", {"responded_by": requests.PROJECT_MANAGER_DECIDED})
    _req(s, project.id, "정책 자동 승인", "AUTO_APPROVED", {"responded_by": "policy"})
    _req(s, project.id, "철회된 요청", "CANCELLED")
    _req(s, project.id, "아직 대기")
    s.commit()
    with session_scope() as s2:
        requests.respond(s2, answered[0], HumanResponse(decision=HumanDecision.APPROVE, chosen_option="6주차"))
        requests.respond(s2, answered[1], HumanResponse(decision=HumanDecision.REJECT))
        requests.respond(s2, answered[2], HumanResponse(decision=HumanDecision.DEFER, note="나중에"))

    decided = api.get(f"/api/projects/{project.id}").json()["decided"]

    assert sorted(x["id"] for x in decided) == sorted([*answered, project_manager])
    by_id = {x["id"]: x for x in decided}
    assert by_id[answered[0]]["chosen_option"] == "6주차" and by_id[answered[0]]["by"] == "owner"
    assert by_id[project_manager]["by"] == "project_manager"
