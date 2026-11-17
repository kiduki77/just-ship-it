import json
import time

import pytest
from justshipit.runtime import host_auth
from justshipit.runtime.base import LLMError


@pytest.fixture
def creds(tmp_path, monkeypatch):
    path = tmp_path / ".credentials.json"
    monkeypatch.setattr(host_auth, "credentials_path", lambda: path)
    monkeypatch.setattr(host_auth, "settings", lambda: type("S", (), {"claude_oauth_token": "", "claude_bin": ""})())
    monkeypatch.setattr(host_auth.time, "sleep", lambda s: None)

    def write(token: str, valid_s: int) -> None:
        path.write_text(json.dumps({"claudeAiOauth": {"accessToken": token, "refreshToken": "r-secret",
                                                      "expiresAt": (time.time() + valid_s) * 1000}}))
    return write


def test_fresh_access_token_is_used_without_refresh(creds, monkeypatch):
    creds("at-1", 3600)
    monkeypatch.setattr(host_auth, "_refresh_host", lambda: pytest.fail("no refresh needed"))
    assert host_auth.access_token(1020) == "at-1"


def test_near_expiry_refreshes_through_host_cli(creds, monkeypatch):
    creds("at-old", 60)
    monkeypatch.setattr(host_auth, "_refresh_host", lambda: creds("at-new", 8 * 3600))
    assert host_auth.access_token(1020) == "at-new"


def test_failed_refresh_is_transient_error(creds, monkeypatch):
    creds("at-old", 60)
    monkeypatch.setattr(host_auth, "_refresh_host", lambda: None)
    with pytest.raises(LLMError) as exc:
        host_auth.access_token(1020)
    assert exc.value.transient


def test_status_never_reveals_tokens(creds):
    creds("at-secret", 3600)
    assert "secret" not in host_auth.status("ko") and "Claude Code 로그인(" in host_auth.status("ko")


def test_long_lived_token_never_reaches_the_container(creds, monkeypatch):
    from justshipit.settings import Settings

    monkeypatch.setenv("SHIP_CLAUDE_OAUTH_TOKEN", "sk-ant-oat01-long-lived")
    monkeypatch.setattr(host_auth, "settings", lambda: Settings())
    creds("at-short", 3600)
    assert host_auth.access_token(1020) == "at-short"
    host_auth.credentials_path().unlink()
    with pytest.raises(LLMError):
        host_auth.access_token(1020)


def test_waiting_for_a_refresh_does_not_block_calls_the_current_token_still_covers(creds, monkeypatch):
    import threading

    creds("at-old", 60)
    sleeping, release = threading.Event(), threading.Event()
    monkeypatch.setattr(host_auth.time, "sleep", lambda s: (sleeping.set(), release.wait(10)))
    monkeypatch.setattr(host_auth, "_refresh_host", lambda: creds("at-new", 8 * 3600))
    long_call = threading.Thread(target=host_auth.access_token, args=(1020,))
    long_call.start()
    assert sleeping.wait(5)
    got: list[str] = []
    short_call = threading.Thread(target=lambda: got.append(host_auth.access_token(30)))
    short_call.start()
    short_call.join(2)
    release.set()
    long_call.join(5)
    short_call.join(5)
    assert got == ["at-old"]
