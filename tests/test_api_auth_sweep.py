"""Every /api route sits behind the access guard, whatever router added it. A route added later is swept in with no
edit here: without the token it answers 401 and does not run, and a write that did not come from the console is
refused even with the token."""
import re

import pytest
from justshipit.api.app import app
from fastapi.testclient import TestClient
from starlette.routing import Mount

CONSOLE = {"X-Ship": "1"}
SAFE = {"GET", "HEAD", "OPTIONS"}


def api_routes() -> list[tuple[str, str]]:
    """(method, concrete path) for every operation the app serves under /api. The OpenAPI document is the one list
    that holds every router's routes whichever way FastAPI nests them; the SPA catch-all is the only route it omits."""
    methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    found = [(m.upper(), re.sub(r"\{[^}]+\}", "1", path)) for path, item in app.openapi()["paths"].items()
             for m in item if m.upper() in methods]
    return found + [("GET", "/api/openapi.json"), ("GET", "/api/docs")]


ROUTES = api_routes()
WRITES = [(m, p) for m, p in ROUTES if m not in SAFE]


@pytest.fixture(params=[True, False], ids=["loopback", "lan"])
def client(request):
    was = app.state.loopback_only
    app.state.loopback_only = request.param
    yield TestClient(app, base_url="http://127.0.0.1:8000" if request.param else "http://192.168.0.10:8000",
                     raise_server_exceptions=False)
    app.state.loopback_only = was


def test_the_sweep_sees_the_routes_it_is_meant_to_cover():
    paths = {p for _, p in ROUTES}
    assert len(ROUTES) >= 100 and len(WRITES) >= 50
    assert {"/api/projects", "/api/events", "/api/inbox"} <= paths
    assert {m for m, _ in ROUTES} >= {"GET", "POST", "DELETE"}
    assert [r.path for r in app.routes if isinstance(r, Mount) and r.path.startswith("/api")] == [], \
        "a mount under /api is not in the OpenAPI document this sweep reads"


def test_no_api_route_answers_without_the_token(client, api_token):
    open_routes = [f"{m} {p} -> {r.status_code}" for m, p in ROUTES
                   if (r := client.request(m, p)).status_code != 401]

    assert open_routes == []


def test_a_wrong_token_is_no_better_than_none(client, api_token):
    open_routes = [f"{m} {p} -> {r.status_code}" for m, p in ROUTES
                   if (r := client.request(m, p, headers={"Authorization": "Bearer wrong"})).status_code != 401]

    assert open_routes == []


def test_a_write_from_another_origin_is_refused_even_with_the_token(client, api_token):
    base = {"Authorization": f"Bearer {api_token}"}
    foreign = {**base, **CONSOLE, "Origin": "https://evil.example"}
    allowed = [f"{m} {p} -> {r.status_code}" for m, p in WRITES
               if (r := client.request(m, p, headers=foreign)).status_code != 403]

    assert allowed == []


def test_a_write_without_the_console_header_is_refused_even_with_the_token(client, api_token):
    forms = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/x-www-form-urlencoded"}
    allowed = [f"{m} {p} -> {r.status_code}" for m, p in WRITES
               if (r := client.request(m, p, headers=forms, content=b"")).status_code != 403]

    assert allowed == []
