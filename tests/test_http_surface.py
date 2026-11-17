"""The console's HTTP surface as the pre-release security review left it (roadmap step 0.8): a request path never
reaches a file outside the build, every response carries the browser's guard headers, no page loads code from another
site, the preview of agent-written code never receives the console cookie, and the API records an answer only as the
owner's or as one made on the owner's behalf."""
import re
from pathlib import Path

import pytest
from ask_kit import by_project_manager
from fastapi.testclient import TestClient
from justshipit import cli
from justshipit.api import access
from justshipit.api import app as app_module
from justshipit.api.app import app
from justshipit.db.models import AgentRequest, DevJob
from justshipit.engineering import try_it, worker
from justshipit.i18n.match import responders
from justshipit.services import requests
from justshipit.settings import settings
from test_decision_safety import draft

CONSOLE = "http://127.0.0.1:8000"


@pytest.fixture(autouse=True)
def loopback_mode():
    app.state.loopback_only = True
    yield
    app.state.loopback_only = True


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url=CONSOLE, headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


@pytest.fixture
def dist(tmp_path, monkeypatch):
    built = tmp_path / "dist"
    built.mkdir()
    (built / "index.html").write_text("<!doctype html>console")
    (built / "logo.svg").write_text("<svg/>")
    monkeypatch.setattr(app_module, "_dist", built)
    return built


@pytest.fixture
def touched(dist, monkeypatch):
    """Every path outside the build that a request made the server look up. The lookup itself is refused here, so a
    test never reaches another machine."""
    outside: list[str] = []
    for name in ("resolve", "is_file"):
        real = getattr(Path, name)

        def watched(self, *args, _real=real, **kwargs):
            if not str(self).startswith(str(dist)):
                outside.append(str(self))
                raise OSError("looked up outside the build")
            return _real(self, *args, **kwargs)

        monkeypatch.setattr(Path, name, watched)
    return outside


@pytest.mark.parametrize("path", [
    "///evil.example/share/x",             # joined to the build on Windows this is the UNC path \\evil.example\share\x
    "/%5C%5Cevil.example%5Cshare%5Cx",
    "/%5Cevil.example/share/x",
    "/logo.svg%5C..%5C..%5Cx",
    "/C:/Windows/win.ini",
    "/logo.svg:stream",
    "/%2E%2E/credentials.json",
    "/a/%2E%2E/%2E%2E/credentials.json",
])
def test_a_request_path_never_reaches_a_file_outside_the_build(touched, path):
    page = TestClient(app, base_url=CONSOLE).get(CONSOLE + path)

    assert touched == []
    assert (page.status_code, page.text) == (200, "<!doctype html>console"), "it is a console address like any other"


def test_a_file_of_the_build_is_still_served(touched):
    client = TestClient(app, base_url=CONSOLE)

    assert client.get("/logo.svg").text == "<svg/>"
    assert client.get("/projects/3/tasks").text == "<!doctype html>console"
    assert touched == []


def test_an_unsafe_path_under_api_is_still_not_a_console_page(touched, api):
    gone = api.get(CONSOLE + "/api/%5C%5Cevil.example%5Cshare%5Cx")

    assert gone.status_code == 404 and "doctype" not in gone.text.lower()
    assert touched == []


GUARDS = {"x-content-type-options": "nosniff", "referrer-policy": "no-referrer", "x-frame-options": "DENY"}


def test_every_response_carries_the_guard_headers(dist, api, api_token):
    anonymous = TestClient(app, base_url=CONSOLE)
    answers = {"the console page": anonymous.get("/projects"), "a file of the build": anonymous.get("/logo.svg"),
               "a refusal": anonymous.get("/api/projects"), "data": api.get("/api/projects"),
               "an unknown api address": api.get("/api/no-such-thing"),
               "a rebound host": TestClient(app, base_url="http://evil.example:8000").get("/")}

    assert [r.status_code for r in answers.values()] == [200, 200, 401, 200, 404, 403]
    for what, r in answers.items():
        assert {k: r.headers.get(k) for k in GUARDS} == GUARDS, what
        policy = dict(part.strip().split(" ", 1) for part in r.headers["content-security-policy"].split(";"))
        assert policy["default-src"] == "'self'" and policy["script-src"] == "'self'", what
        assert policy["frame-ancestors"] == "'none'" and policy["base-uri"] == "'none'", what
        assert policy["connect-src"] == "'self'" and policy["form-action"] == "'self'", what
        assert policy["img-src"] == "'self' data:", f"{what}: the console's style holds one data: icon"
        assert "unsafe-eval" not in r.headers["content-security-policy"], what
        assert "http" not in r.headers["content-security-policy"], f"{what}: no other site is allowed"


def test_data_is_never_kept_by_the_browser_and_a_page_keeps_its_own_rule(dist, api):
    assert api.get("/api/projects").headers["cache-control"] == "no-store"
    assert TestClient(app, base_url=CONSOLE).get("/api/projects").headers["cache-control"] == "no-store"
    assert api.get("/projects").headers["cache-control"] == "no-cache", "the console page is not data"


async def _started(scope_path: str, headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    """The headers a response that starts with `headers` leaves the guard with."""
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await access.guarded_send(send, scope_path)({"type": "http.response.start", "status": 200, "headers": headers})
    return {k.decode(): v.decode() for k, v in sent[0]["headers"]}


@pytest.mark.anyio
async def test_a_response_that_set_its_own_policy_or_cache_rule_keeps_it():
    """The captures of a build are served with `Content-Security-Policy: sandbox` and a short private cache, and the
    event stream with `no-cache` (api/console.py, api/app.py)."""
    shot = await _started("/api/dev-jobs/1/screens/a.png", [
        (b"content-security-policy", b"sandbox"), (b"cache-control", b"private, max-age=300")])
    stream = await _started("/api/events", [(b"cache-control", b"no-cache"), (b"content-type", b"text/event-stream")])

    assert (shot["content-security-policy"], shot["cache-control"]) == ("sandbox", "private, max-age=300")
    assert shot["x-frame-options"] == "DENY" and shot["x-content-type-options"] == "nosniff"
    assert stream["cache-control"] == "no-cache" and stream["content-type"] == "text/event-stream"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_no_page_of_the_console_loads_code_from_another_site(api):
    assert api.get("/api/docs").status_code == 404
    assert api.get("/api/redoc").status_code == 404
    assert api.get("/docs").status_code != 200 or "swagger" not in api.get("/docs").text.lower()
    assert api.get("/api/openapi.json").json()["info"]["title"] == "Just Ship It"
    assert "/api/docs" not in app_module._NO_BUILD
    for page in (app_module._NO_BUILD, access._LOGIN_UNUSABLE):
        assert not re.search(r"<script|https?://(?!127\.0\.0\.1)", page)


def test_the_owner_opens_the_preview_on_a_host_the_console_cookie_never_goes_to():
    """A cookie goes to every port of its host. The preview is code an agent wrote, so the owner's link names
    `localhost` and the console answers on 127.0.0.1 only."""
    job = DevJob(id=1, branch="ai/x", result={"try": {"preview": True, "ci_setup": True}})

    assert try_it.card(job, "x")["address"] == try_it.OWNER_PREVIEW_ADDRESS == "http://localhost:4173"
    assert try_it.PREVIEW_ADDRESS == "http://127.0.0.1:4173", "the probes inside the sandbox keep the address it binds"
    assert try_it.PREVIEW_ADDRESS in worker._capture("en") and "localhost" not in worker._capture("en")
    walkthrough = (settings().root / "web" / "src" / "components" / "Walkthrough.tsx").read_text(encoding="utf-8")
    assert re.search(r'^const PREVIEW = "(.+)";$', walkthrough, re.MULTILINE).group(1) == try_it.OWNER_PREVIEW_ADDRESS


def test_the_console_is_not_served_on_localhost(dist, api_token):
    by_name = TestClient(app, base_url="http://localhost:8000")
    code = access.mint_login_code(settings().var_dir)

    moved = by_name.get(f"/inbox?req=3&{access.LOGIN_PARAM}={code}", follow_redirects=False)

    assert moved.status_code == 307
    assert moved.headers["location"] == f"{CONSOLE}/inbox?req=3&{access.LOGIN_PARAM}={code}"
    assert "set-cookie" not in moved.headers, "no login cookie is ever set for localhost"
    assert by_name.get("/api/projects", headers={"Authorization": f"Bearer {api_token}"}).status_code == 403
    assert by_name.post("/api/projects", headers={"Authorization": f"Bearer {api_token}", "X-Ship": "1"},
                        json={"goal": "x"}).status_code == 403
    at_address = TestClient(app, base_url=CONSOLE)
    assert at_address.get(f"/?{access.LOGIN_PARAM}={code}", follow_redirects=False).status_code == 303, \
        "the code was not used up on the way"
    assert at_address.get("/api/projects").status_code == 200


def _card(s, project) -> AgentRequest:
    req = requests.create(s, draft(action_kind="external.email_send", estimated_cost_usd=0), from_agent="research",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    return req


@pytest.mark.parametrize("by", ["system", "policy", "project_manager", responders.PROJECT_MANAGER_DECIDED, ""])
def test_the_api_records_no_answer_as_anyone_but_the_owner(s, project, api, by):
    req = _card(s, project)

    refused = api.post(f"/api/requests/{req.id}/respond", json={"decision": "reject", "responded_by": by})

    assert refused.status_code == 422, refused.text
    s.expire_all()
    assert (s.get(AgentRequest, req.id).status, s.get(AgentRequest, req.id).response) == ("PENDING", None)


@pytest.mark.parametrize("by", [None, "owner", cli.DELEGATED])
def test_the_api_records_the_owners_answer_and_one_made_on_the_owners_behalf(s, project, api, by):
    req = _card(s, project)

    answered = api.post(f"/api/requests/{req.id}/respond",
                        json={"decision": "reject", **({"responded_by": by} if by else {})})

    assert answered.status_code == 200, answered.text
    s.expire_all()
    assert s.get(AgentRequest, req.id).response["responded_by"] == (by or "owner")
