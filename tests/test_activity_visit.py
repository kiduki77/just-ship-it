""""Since your last visit" is kept on the server for the owner, so the owner's two computers share it, per business."""
import uuid

import pytest
from fastapi.testclient import TestClient
from justshipit.api.app import app
from justshipit.services import app_settings


def client(api_token) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


@pytest.fixture
def business_id() -> int:
    return uuid.uuid4().int % 10**9


def test_the_second_computer_sees_the_visit_the_first_one_made(api_token, business_id):
    first_pc, second_pc = client(api_token), client(api_token)
    assert first_pc.post("/api/activity/visit", json={"business_id": business_id}).json() == {"previous": None}

    seen = second_pc.post("/api/activity/visit", json={"business_id": business_id}).json()["previous"]
    assert seen is not None
    assert second_pc.post("/api/activity/visit", json={"business_id": business_id}).json()["previous"] >= seen


def test_each_business_keeps_its_own_visit(api_token, business_id):
    pc = client(api_token)
    pc.post("/api/activity/visit", json={"business_id": business_id})
    assert pc.post("/api/activity/visit", json={"business_id": business_id + 1}).json() == {"previous": None}


def test_the_visits_are_not_a_settings_section(api_token, business_id):
    client(api_token).post("/api/activity/visit", json={"business_id": business_id})
    assert app_settings.VISITS not in app_settings.stored()
    assert app_settings.VISITS not in client(api_token).get("/api/settings").json()["overrides"]
