import os
import re
import stat

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from typer.testing import CliRunner

from justshipit import cli
from justshipit.api import access
from justshipit.api import app as app_module
from justshipit.api.app import app
from justshipit.db.models import Project, Schedule
from justshipit.settings import settings
from ask_kit import by_project_manager

CONSOLE = {"X-Ship": "1"}


@pytest.fixture(autouse=True)
def loopback_mode():
    app.state.loopback_only = True
    yield
    app.state.loopback_only = True


def _logged_in(base: str) -> TestClient:
    browser = TestClient(app, base_url=base)
    assert browser.get(f"/?{access.LOGIN_PARAM}={_code()}", follow_redirects=False).status_code == 303
    return browser


@pytest.fixture
def local(api_token):
    """The owner's logged-in browser on this machine."""
    return _logged_in("http://127.0.0.1:8000")


@pytest.fixture
def remote(api_token):
    """A browser on the LAN that has not logged in yet."""
    app.state.loopback_only = False
    return TestClient(app, base_url="http://192.168.0.10:8000")


def _code() -> str:
    return access.mint_login_code(settings().var_dir)


def test_a_non_browser_client_cannot_forge_a_loopback_request_to_approve(s, project):
    from justshipit.db.models import AgentRequest
    from justshipit.services import requests
    from test_governance import draft

    r = requests.create(s, draft(action_kind="external.ads_run", estimated_cost_usd=100), from_agent="marketing",
                        project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    assert r.policy_mode == "strong_approve"
    # curl, a script, or an engineer container reaching the port as host.docker.internal can write any header.
    forger = TestClient(app, base_url="http://host.docker.internal:8000")
    forged = {"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:8000", "X-Ship": "1"}
    body = {"decision": "approve", "confirm_text": f"REQ-{r.id}", "max_amount_usd": 100, "responded_by": "owner"}
    answer = forger.post(f"/api/requests/{r.id}/respond", json=body, headers=forged)
    assert answer.status_code == 401, answer.text
    assert forger.get("/api/inbox", headers=forged).status_code == 401
    s.expire_all()
    assert s.get(AgentRequest, r.id).status == "PENDING"


def _status(s, project) -> str:
    s.expire_all()
    return s.get(Project, project.id).status


@pytest.mark.parametrize("origin", ["https://evil.example", None])
def test_a_cross_site_form_post_is_refused(s, project, local, origin):
    s.commit()
    headers = {"Content-Type": "application/x-www-form-urlencoded"} | ({"Origin": origin} if origin else {})
    r = local.post(f"/api/projects/{project.id}/archive", headers=headers, content=b"")
    assert r.status_code == 403, r.text
    assert _status(s, project) != "ARCHIVED"


def test_a_rebound_host_is_refused_even_with_the_console_header(s, project):
    s.commit()
    evil = TestClient(app, base_url="http://evil.example:8000")
    assert evil.get("/api/projects").status_code == 403
    r = evil.post(f"/api/projects/{project.id}/archive", headers={**CONSOLE, "Origin": "http://evil.example:8000"})
    assert r.status_code == 403, r.text
    assert _status(s, project) != "ARCHIVED"


def test_a_header_from_another_origin_is_refused(s, project, local):
    s.commit()
    r = local.post(f"/api/projects/{project.id}/archive", headers={**CONSOLE, "Origin": "https://evil.example"})
    assert r.status_code == 403, r.text
    assert _status(s, project) != "ARCHIVED"


# `localhost` is not among them: the product preview is opened there (tests/test_http_surface.py).
@pytest.mark.parametrize("base", ["http://127.0.0.1:8000", "http://[::1]:8000"])
def test_the_consoles_own_requests_succeed(s, project, base, api_token):
    s.commit()
    console = _logged_in(base)
    assert console.get("/api/projects").status_code == 200
    r = console.post(f"/api/projects/{project.id}/archive", headers={**CONSOLE, "Origin": base})
    assert r.status_code == 200, r.text
    assert _status(s, project) == "ARCHIVED"


@pytest.mark.parametrize("loopback", [True, False])
def test_every_api_request_needs_the_token_in_every_bind_mode(s, project, api_token, loopback):
    s.commit()
    app.state.loopback_only = loopback
    client = TestClient(app, base_url="http://127.0.0.1:8000" if loopback else "http://192.168.0.10:8000")
    refused = client.get("/api/projects")
    assert refused.status_code == 401 and "ship api --login" in refused.json()["detail"]
    assert client.get("/api/projects", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/events").status_code == 401, "the live event stream needs it too"
    assert client.get("/api/openapi.json").status_code == 401
    assert client.get("/api/projects", headers={"Authorization": f"Bearer {api_token}"}).status_code == 200
    assert client.get("/").status_code != 401, "the console itself loads, so it can say how to log in"


def test_the_login_link_trades_a_one_time_code_for_the_cookie(s, project, remote, api_token):
    s.commit()
    code = _code()
    login = remote.get(f"/projects?{access.LOGIN_PARAM}={code}&tab=x", follow_redirects=False)
    assert login.status_code == 303
    assert login.headers["location"] == "/projects?tab=x"
    cookie = login.headers["set-cookie"].lower()
    assert f"{access.COOKIE}=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert remote.get("/api/projects").status_code == 200
    r = remote.post(f"/api/projects/{project.id}/archive", headers={**CONSOLE, "Origin": "http://192.168.0.10:8000"})
    assert r.status_code == 200, r.text
    assert _status(s, project) == "ARCHIVED"

    stranger = TestClient(app, base_url="http://192.168.0.10:8000")
    reused = stranger.get(f"/?{access.LOGIN_PARAM}={code}", follow_redirects=False)
    assert reused.status_code == 401 and "ship api --login" in reused.text, "a code works once"
    assert "set-cookie" not in reused.headers
    assert remote.get(f"/?{access.LOGIN_PARAM}={code}", follow_redirects=False).status_code == 303, \
        "an old link in a logged-in browser just goes to the console"


def test_a_login_code_expires(remote):
    code = access.mint_login_code(settings().var_dir, ttl_s=-1)
    assert remote.get(f"/?{access.LOGIN_PARAM}={code}", follow_redirects=False).status_code == 401
    assert remote.get("/api/projects").status_code == 401


def test_a_cookie_still_needs_the_console_header(s, project, local):
    s.commit()
    assert local.post(f"/api/projects/{project.id}/archive").status_code == 403
    assert _status(s, project) != "ARCHIVED"


def test_rotating_the_token_ends_every_login_and_unused_link(s, project, local, api_token):
    s.commit()
    unused = _code()
    assert local.get("/api/projects").status_code == 200
    out = CliRunner().invoke(cli.app, ["api-token", "rotate"])
    assert out.exit_code == 0, out.output
    new = (settings().var_dir / "api_token").read_text().strip()
    assert new != api_token and new not in out.output
    assert stat.S_IMODE(os.stat(settings().var_dir / "api_token").st_mode) == 0o600
    assert local.get("/api/projects").status_code == 401, "the old cookie"
    bearer = TestClient(app, base_url="http://127.0.0.1:8000")
    assert bearer.get("/api/projects", headers={"Authorization": f"Bearer {api_token}"}).status_code == 401
    assert bearer.get("/api/projects", headers={"Authorization": f"Bearer {new}"}).status_code == 200
    assert bearer.get(f"/?{access.LOGIN_PARAM}={unused}", follow_redirects=False).status_code == 401


def _serve(monkeypatch, *args: str) -> tuple[dict, str]:
    started = {}
    def run(server):
        started.update(target=server.config.app, log_config=server.config.log_config, host=server.config.host,
                       port=server.config.port)
    monkeypatch.setattr(app_module.ConsoleServer, "run", run)
    out = CliRunner().invoke(cli.app, ["api", *args])
    assert out.exit_code == 0, out.output
    return started, out.output


def _link_code(output: str) -> str:
    return re.search(rf"/\?{access.LOGIN_PARAM}=([\w-]+)", output).group(1)


def test_api_prints_a_login_link_that_works_and_never_the_token(monkeypatch, api_token):
    started, out = _serve(monkeypatch)
    assert started["target"].state.loopback_only
    assert api_token not in out
    assert "http://127.0.0.1:8000/?login=" in out
    browser = TestClient(app, base_url="http://127.0.0.1:8000")
    assert browser.get(f"/?login={_link_code(out)}", follow_redirects=False).status_code == 303
    assert browser.get("/api/projects").status_code == 200


def test_api_login_mints_a_fresh_link_without_starting_a_server(monkeypatch, api_token):
    monkeypatch.setattr(app_module.ConsoleServer, "run", lambda *a, **kw: pytest.fail("started a server"))
    out = CliRunner().invoke(cli.app, ["api", "--login", "--port", "8051"])
    assert out.exit_code == 0, out.output
    assert "http://127.0.0.1:8051/?login=" in out.output and api_token not in out.output
    browser = TestClient(app, base_url="http://127.0.0.1:8051")
    assert browser.get(f"/?login={_link_code(out.output)}", follow_redirects=False).status_code == 303


def _first_start_after(var_dir, token: str | None) -> None:
    """var/ as a version left it that put the token itself in the console cookie, or as a fresh install."""
    for name in ("api_token", "cookie_is_hmac"):
        (var_dir / name).unlink(missing_ok=True)
    if token:
        (var_dir / "api_token").write_text(token)


def test_the_first_start_after_the_cookie_upgrade_rotates_the_token_once(monkeypatch):
    """Until the cookie became HMAC(token), every 127.0.0.1 port received the token itself, so a copy of it may
    still work as a Bearer. The first start rotates it and says why; later starts leave it alone."""
    var_dir = settings().var_dir
    leaked = "token-the-old-cookie-carried"
    _first_start_after(var_dir, leaked)
    thief = TestClient(app, base_url="http://127.0.0.1:8000")
    assert thief.get("/api/projects", headers={"Authorization": f"Bearer {leaked}"}).status_code == 200

    _, out = _serve(monkeypatch)

    assert thief.get("/api/projects", headers={"Authorization": f"Bearer {leaked}"}).status_code == 401
    assert "cookie" in out and "made anew" in out and "Log in to the console again" in out, out
    browser = TestClient(app, base_url="http://127.0.0.1:8000")
    assert browser.get(f"/?login={_link_code(out)}", follow_redirects=False).status_code == 303
    assert browser.get("/api/projects").status_code == 200
    _, again = _serve(monkeypatch)
    assert "made anew" not in again
    assert browser.get("/api/projects").status_code == 200, "rotated once, not at every start"


def test_a_fresh_install_starts_without_a_rotation(monkeypatch):
    _first_start_after(settings().var_dir, None)
    _, out = _serve(monkeypatch)
    token = (settings().var_dir / "api_token").read_text()
    assert "made anew" not in out
    _serve(monkeypatch)
    assert (settings().var_dir / "api_token").read_text() == token


def test_api_on_a_lan_address_warns_and_drops_the_host_check(monkeypatch):
    started, out = _serve(monkeypatch, "--host", "0.0.0.0", "--port", "8123")
    assert not started["target"].state.loopback_only
    assert "Warning" in out and "http://<this PC's address>:8123/?login=" in out
    assert "cookie" in out and "Tailscale" in out and "SSH tunnel" in out, "http on a shared network exposes the cookie"
    _, quiet = _serve(monkeypatch)
    assert "Warning" not in quiet


@pytest.mark.parametrize("scheme,secure", [("https", True), ("http", False)])
def test_the_login_cookie_is_secure_when_the_request_came_over_https(scheme, secure, api_token):
    browser = TestClient(app, base_url=f"{scheme}://127.0.0.1:8000")
    login = browser.get(f"/?{access.LOGIN_PARAM}={_code()}", follow_redirects=False)
    assert login.status_code == 303
    assert ("secure" in login.headers["set-cookie"].lower()) is secure


def test_the_server_log_never_holds_a_login_code_or_the_token(monkeypatch, capsys, api_token):
    import threading
    import time

    import httpx
    import uvicorn

    started, out = _serve(monkeypatch, "--port", "0")
    code = _link_code(out)
    capsys.readouterr()
    config = uvicorn.Config(started["target"], host="127.0.0.1", port=0, log_config=started["log_config"])
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        assert httpx.get(f"{base}/?login={code}", follow_redirects=False).status_code == 303
        assert httpx.get(f"{base}/api/projects?token={api_token}").status_code == 401
    finally:
        server.should_exit = True
        thread.join(5)
    logged = capsys.readouterr()
    lines = logged.out + logged.err
    assert '"GET / HTTP/1.1" 303' in lines and '"GET /api/projects HTTP/1.1" 401' in lines, lines
    assert code not in lines and api_token not in lines


def test_reading_schedules_writes_nothing(s, project, local):
    project.status = "DONE"
    s.commit()
    assert local.get("/api/schedules").status_code == 200
    watch = select(func.count()).select_from(Schedule).where(Schedule.name == f"P-{project.id} change watch")
    assert s.scalar(watch) == 0, "a GET must not create the schedules the worker's tick derives"


def test_console_serves_nothing_outside_the_built_dist(tmp_path, local, monkeypatch):
    from justshipit.api import app as app_module

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>console")
    (dist / "logo.svg").write_text("<svg/>")
    secret = tmp_path / "credentials.json"
    secret.write_text("{\"accessToken\": \"sk-secret\"}")
    (dist / "link.json").symlink_to(secret)
    monkeypatch.setattr(app_module, "_dist", dist)

    assert local.get("/logo.svg").text == "<svg/>"
    for path in ("/..%2Fcredentials.json", "/..%252Fcredentials.json", "/%2E%2E/credentials.json",
                 "/link.json", "/assets/../../credentials.json", f"/{secret}"):
        r = local.get(path)
        assert "sk-secret" not in r.text, path


def test_an_api_address_the_server_does_not_know_answers_404_not_the_console_page(tmp_path, local, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>console")
    monkeypatch.setattr(app_module, "_dist", dist)

    gone = local.get("/api/chat/no-such-thing")
    assert gone.status_code == 404
    assert "doctype" not in gone.text.lower()
    page = local.get("/some/console/route")
    assert page.status_code == 200 and "doctype" in page.text.lower()
    assert "no-cache" in page.headers.get("cache-control", "")


def test_run_factory_queues_one_job_while_one_is_pending(s, local):
    from justshipit.db.models import Job

    before = s.scalar(select(func.count()).select_from(Job).where(Job.kind == "skill_factory"))
    first = local.post("/api/skills/run-factory", headers=CONSOLE)
    second = local.post("/api/skills/run-factory", headers=CONSOLE)
    assert first.status_code == 200
    assert second.status_code == 409 and "Skill Factory" in second.json()["detail"], second.text
    assert s.scalar(select(func.count()).select_from(Job).where(Job.kind == "skill_factory")) == before + 1
    s.get(Job, first.json()["job_id"]).status = "done"
    s.commit()
    assert local.post("/api/skills/run-factory", headers=CONSOLE).status_code == 200, "a finished run can be repeated"


def test_a_symlink_at_the_token_path_is_refused_and_its_target_untouched(tmp_path):
    from justshipit.api import access

    target = tmp_path / "owners-file"
    target.write_text("not a token")
    target.chmod(0o644)
    (tmp_path / "api_token").symlink_to(target)
    with pytest.raises(access.TokenFileError) as refused:
        access.ensure_token(tmp_path)
    assert "var/api_token" in str(refused.value) and "symbolic link" in str(refused.value)
    assert target.read_text() == "not a token" and stat.S_IMODE(target.stat().st_mode) == 0o644


def test_a_new_token_file_is_private_and_reused(tmp_path):
    from justshipit.api import access

    token = access.ensure_token(tmp_path)
    assert stat.S_IMODE((tmp_path / "api_token").stat().st_mode) == 0o600
    assert len(token) >= 32 and access.ensure_token(tmp_path) == token


def test_websockets_are_refused_before_any_route_sees_them(local):
    from starlette.websockets import WebSocket, WebSocketDisconnect

    reached = []

    @app.websocket("/api/ws-probe")
    async def probe(ws: WebSocket):
        reached.append(True)
        await ws.accept()
        await ws.send_text("in")

    try:
        with pytest.raises(WebSocketDisconnect), local.websocket_connect("/api/ws-probe") as ws:
            ws.receive_text()
    finally:
        app.router.routes.pop()
    assert reached == []


@pytest.fixture
def windows_os(monkeypatch):
    """CPython on Windows, the owner's platform, has none of these."""
    import fcntl

    for name in ("O_NOFOLLOW", "getuid", "getgid"):
        monkeypatch.delattr(os, name)
    real_fchmod = os.fchmod

    def windows_fchmod(fd, mode):
        if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY:
            raise PermissionError(13, "Access is denied")
        real_fchmod(fd, mode)

    monkeypatch.setattr(os, "fchmod", windows_fchmod)
    monkeypatch.setattr(access, "_POSIX_MODES", False)


def test_the_console_starts_logs_in_and_rotates_without_posix_only_calls(monkeypatch, api_token, windows_os):
    _, out = _serve(monkeypatch)
    browser = TestClient(app, base_url="http://127.0.0.1:8000")
    assert browser.get(f"/?login={_link_code(out)}", follow_redirects=False).status_code == 303
    assert browser.get("/api/projects").status_code == 200
    rotated = CliRunner().invoke(cli.app, ["api-token", "rotate"])
    assert rotated.exit_code == 0, rotated.output
    assert browser.get("/api/projects").status_code == 401, "rotation still ends the login"
    again = CliRunner().invoke(cli.app, ["api", "--login"])
    assert browser.get(f"/?login={_link_code(again.output)}", follow_redirects=False).status_code == 303
    assert browser.get("/api/projects").status_code == 200


def test_links_are_refused_without_o_nofollow(tmp_path, windows_os):
    target = tmp_path / "owners-file"
    target.write_text("not a token")
    (tmp_path / "api_token").symlink_to(target)
    with pytest.raises(access.TokenFileError):
        access.ensure_token(tmp_path)
    assert target.read_text() == "not a token"

    code = access.mint_login_code(tmp_path)
    planted = next((tmp_path / "login_codes").iterdir())
    elsewhere = tmp_path / "elsewhere"
    planted.rename(elsewhere)
    planted.symlink_to(elsewhere)
    assert not access.redeem_login_code(tmp_path, code), "a code file must be the file itself, not a link"


def test_an_open_event_stream_ends_when_the_token_is_rotated(monkeypatch, api_token):
    import threading
    import time

    import httpx
    import uvicorn

    from justshipit.api import app as app_module

    monkeypatch.setattr(app_module, "_SSE_WAKE_S", 0.05)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.02)
        base = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
        with httpx.stream("GET", f"{base}/api/events", headers={"Authorization": f"Bearer {api_token}"},
                          timeout=5) as stream:
            lines = stream.iter_lines()
            assert next(lines) == "event: ready"
            wakes = 0
            while wakes < 3:  # a keepalive, or an event that another test on this database committed
                wakes += next(lines) != ""
            access.rotate_token(settings().var_dir)
            deadline = time.monotonic() + 3
            for _ in lines:
                assert time.monotonic() < deadline, "the stream outlived the token rotation"
    finally:
        server.should_exit = True
        thread.join(5)


def test_a_leaked_cookie_is_not_the_token_and_works_only_as_the_cookie(api_token):
    browser = _logged_in("http://127.0.0.1:8000")
    cookie = browser.cookies[access.COOKIE]
    assert api_token not in cookie, "every port on 127.0.0.1 (a dev server) receives this cookie"
    assert browser.get("/api/projects").status_code == 200
    script = TestClient(app, base_url="http://127.0.0.1:8000")
    assert script.get("/api/projects", headers={"Authorization": f"Bearer {cookie}"}).status_code == 401
    assert script.get("/api/projects", headers={"Authorization": f"Bearer {api_token}"}).status_code == 200


def test_the_swagger_oauth2_redirect_page_is_not_served(local):
    assert "swaggerUIRedirectOauth2" not in local.get("/docs/oauth2-redirect").text
