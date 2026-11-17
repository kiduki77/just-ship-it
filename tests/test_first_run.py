import os
import shutil

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from justshipit import cli
from justshipit.api import app as api_module


def test_without_a_console_build_the_root_page_says_how_to_build(monkeypatch, tmp_path):
    monkeypatch.setattr(api_module, "_dist", tmp_path / "missing-dist")
    r = TestClient(api_module.app, base_url="http://127.0.0.1:8000").get("/")
    assert r.headers["content-type"].startswith("text/html")
    assert r.status_code == 503
    assert "cd web &amp;&amp; npm ci &amp;&amp; npm run build" in r.text
    assert "console" in r.text


def _fake_bin(dirpath, name: str, out: str, code: int = 0) -> None:
    script = dirpath / name
    script.write_text(f"#!/bin/sh\necho '{out}'\nexit {code}\n", encoding="utf-8")
    script.chmod(0o755)


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", str(d))
    return d


def _check(*args: str) -> str:
    out = CliRunner().invoke(cli.app, list(args))
    assert out.exit_code == 0, out.output
    return out.output


def test_sandbox_check_confirms_the_subscription_login(bin_dir):
    _fake_bin(bin_dir, "claude", '{"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty"}')
    out = _check("sandbox", "check")
    assert "claude: installed, subscription login confirmed" in out
    assert "codex" not in out


def test_sandbox_check_says_how_to_install_or_log_in(bin_dir):
    _fake_bin(bin_dir, "claude", '{"loggedIn": false}', code=1)
    out = _check("sandbox", "check")
    assert "claude: not logged in" in out and "`claude`" in out


def test_a_slow_or_unrunnable_cli_is_reported_as_such(bin_dir, monkeypatch):
    monkeypatch.setattr(cli, "_LOGIN_CHECK_TIMEOUT_S", 1)
    slow = bin_dir / "claude"
    slow.write_text(f"#!/bin/sh\nexec {shutil.which('sleep', path=os.defpath)} 5\n", encoding="utf-8")
    slow.chmod(0o755)
    out = _check("sandbox", "check")
    assert "claude: check timed out" in out and "claude auth status" in out
    assert "not logged in" not in out and "not installed" not in out


def test_an_api_key_login_is_flagged_without_echoing_the_key(bin_dir):
    _fake_bin(bin_dir, "claude", '{"loggedIn": true, "authMethod": "api_key", "apiProvider": "firstParty"}')
    out = _check("sandbox", "check")
    assert "claude: authenticated with an API key" in out
    assert "sk-" not in out


def test_init_reports_the_cli_logins(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    _fake_bin(tmp_path, "claude", '{"loggedIn": false}', code=1)
    from justshipit.services import credentials
    monkeypatch.setattr(credentials, "ensure", lambda _env_path: [])
    monkeypatch.setattr(cli, "_recreate_compose_services", lambda: None)
    out = _check("init")
    assert "claude: not logged in" in out


def test_sandbox_check_shows_the_host_git_probe(bin_dir, monkeypatch):
    real = shutil.which("git", path=os.defpath)
    (bin_dir / "git").write_text(f'#!/bin/sh\nexec {real} "$@"\n', encoding="utf-8")
    (bin_dir / "git").chmod(0o755)
    assert "git: ok" in _check("sandbox", "check") and "passed the engineer clone fetch safety check" in _check("sandbox", "check")
    (bin_dir / "git").write_text(f'#!/bin/sh\nGIT_NO_LAZY_FETCH=0 exec {real} "$@"\n', encoding="utf-8")
    assert "Update git to 2.45.1" in _check("sandbox", "check")
    monkeypatch.setenv("PATH", str(bin_dir / "nowhere"))
    assert "Can't find git" in _check("sandbox", "check")
