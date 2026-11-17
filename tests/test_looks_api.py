"""The look routes: the style packs the owner picks from in their language, the accent a main colour derives into,
and a business's look, which is the default until the owner picks and then what its next new product is built in."""
import pytest
from fastapi.testclient import TestClient
from justshipit.api.app import app
from justshipit.engineering import style_packs
from people_kit import make_business


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def test_the_packs_come_with_their_name_pitch_and_default_main(api):
    got = api.get("/api/styles").json()

    assert [(p["slug"], p["name"]) for p in got] == [("linear", "Linear style"), ("toss", "Toss style"),
                                                     ("stripe", "Stripe style")]
    assert all(p["pitch"] and p["main"] == style_packs.packs()[p["slug"]].main for p in got)


def test_the_packs_speak_korean_to_a_korean_owner(api, korean_owner):
    got = api.get("/api/styles").json()

    assert [p["name"] for p in got] == ["Linear 스타일", "토스 스타일", "Stripe 스타일"]
    assert got[1]["pitch"] == "한 화면에 결정 하나, 큰 숫자와 쉬운 말."


def test_the_preview_derives_the_nine_roles_and_refuses_a_colour_that_is_not_hex(api):
    got = api.get("/api/styles/linear/derive", params={"main": "#EA580C"}).json()

    assert got["pack"] == "linear" and got["main"] == "#ea580c"
    assert set(got["roles"]) == {"primary", "primary-hover", "primary-press", "on-primary", "primary-text",
                                 "primary-wash", "primary-wash-strong", "primary-border", "highlight"}
    bad = api.get("/api/styles/linear/derive", params={"main": "orange"})
    assert bad.status_code == 422 and bad.json()["detail"] == "These aren't in the right format: main color."
    assert "orange" not in bad.text
    assert api.get("/api/styles/material/derive", params={"main": "#ea580c"}).status_code == 422


def test_a_business_reads_the_default_look_until_the_owner_picks_one(s, api):
    b = make_business(s)
    s.commit()

    before = api.get(f"/api/businesses/{b.id}/look").json()
    assert (before["pack"], before["main"], before["picked"]) == ("toss", style_packs.packs()["toss"].main, False)

    set_ = api.post(f"/api/businesses/{b.id}/look", json={"pack": "stripe", "main": "#EA580C"})
    assert set_.status_code == 200
    after = api.get(f"/api/businesses/{b.id}/look").json()
    assert (after["pack"], after["main"], after["picked"]) == ("stripe", "#ea580c", True)
    assert after["roles"]["primary-border"] == set_.json()["roles"]["primary-border"]


def test_setting_a_look_refuses_a_bad_body_and_a_missing_business(s, api):
    b = make_business(s)
    s.commit()

    assert api.post(f"/api/businesses/{b.id}/look", json={"pack": "toss", "main": "#12"}).status_code == 422
    assert api.post(f"/api/businesses/{b.id}/look", json={"pack": "toss", "main": "#123456", "x": 1}).status_code == 422
    missing = api.post("/api/businesses/999999/look", json={"pack": "toss", "main": "#123456"})
    assert missing.status_code == 404 and missing.json()["detail"] == "There's no business B-999999."
    assert api.get(f"/api/businesses/{b.id}/look").json()["picked"] is False
