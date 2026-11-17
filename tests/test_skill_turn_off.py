"""The owner turns a skill that is on off: its agents stop reading it, the record keeps it as retired by the owner, and
only a skill that is on can be turned off."""
import uuid

import pytest
from fastapi.testclient import TestClient
from justshipit.api.app import app
from justshipit.capability import factory
from justshipit.db.models import Skill
from justshipit.db.session import session_scope
from justshipit.services import registry


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def skill(status: str, body: str = "Check the unit economics first.") -> int:
    name = f"off-test-{uuid.uuid4().hex[:8]}"
    path = f"skills/approved/{name}"
    registry.write_files({f"{path}/SKILL.md": f"---\nname: {name}\n---\n{body}\n"}, "test skill")
    with session_scope() as s:
        k = Skill(name=name, kind="knowledge", status=status, owner_agent="skill_builder", target_agents=["project_manager"],
                  registry_path=path)
        s.add(k)
        s.flush()
        return k.id


def listed(api, sid: int) -> dict:
    return next(k for k in api.get("/api/skills").json()["skills"] if k["id"] == sid)


def test_a_skill_that_is_on_is_turned_off_and_its_agents_stop_reading_it(api):
    sid = skill("ACTIVE", body="Marker-for-turn-off-test.")
    assert "Marker-for-turn-off-test." in factory.active_skill_section("project_manager")

    r = api.post(f"/api/skills/{sid}/turn-off")
    assert r.status_code == 200 and r.json() == {"id": sid, "status": "DEPRECATED"}
    assert "Marker-for-turn-off-test." not in factory.active_skill_section("project_manager")
    row = listed(api, sid)
    assert row["status"] == "DEPRECATED" and row["turned_off_at"] is not None


def test_a_skill_a_newer_version_replaced_does_not_read_as_turned_off_by_the_owner(api):
    sid = skill("DEPRECATED")
    assert listed(api, sid)["turned_off_at"] is None


@pytest.mark.parametrize("status", ["AWAITING_APPROVAL", "DEPRECATED", "REJECTED"])
def test_only_a_skill_that_is_on_can_be_turned_off(api, status):
    sid = skill(status)
    assert api.post(f"/api/skills/{sid}/turn-off").status_code == 409
    with session_scope() as s:
        assert s.get(Skill, sid).status == status
    assert api.post("/api/skills/99999999/turn-off").status_code == 404
