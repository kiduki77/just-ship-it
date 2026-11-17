"""Live sending is claimed only when it can really send: Settings and the top bar read what each live connector lacks,
and turning one live says what is missing before the save. Slack goes live only after the owner's connection test
passed with the saved token; in the outbox that test asks Slack's read-only auth.test and posts nothing. No value of a
setting or credential comes back."""
import re
import shutil
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from justshipit.api.app import app
from justshipit.db.models import AppSetting
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import app_settings, notify_state, secrets
from justshipit.settings import settings
from notify_kit import refused, slack_answers
from sqlalchemy import delete

TOKEN = "xoxb-1234567890-ABCDEFGHIJKLMNOP-wxyz"
ROOT = Path(__file__).resolve().parents[1]


def console_reads_the_sentence_of() -> set[int]:
    """The statuses whose `detail` the console shows as it is: every 4xx but 401 and 404, and its outside-service 5xx."""
    outside = re.search(r"const OUTSIDE_SERVICE = (\d+);", (ROOT / "web" / "src" / "api.ts").read_text(encoding="utf-8"))
    return {*(set(range(400, 500)) - {401, 404}), int(outside.group(1))}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(settings(), "smtp_host", "")

    def wipe():
        with session_scope(lease_checked=False) as s:
            s.execute(delete(AppSetting))
        for name in secrets.REGISTRY:
            secrets.clear(name)
        notify_state.clear_slack_refused()
        shutil.rmtree(settings().var_dir / "state", ignore_errors=True)
        shutil.rmtree(settings().var_dir / "outbox" / "slack", ignore_errors=True)
    wipe()
    yield
    wipe()


@pytest.fixture
def console(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def slack_ok():
    return lambda: httpx.Response(200, json={"ok": True, "team": "Acme", "user_id": "U1"})


def connected(console, monkeypatch, *answers) -> list:
    """The owner saves the token and the channel and presses Test the connection while Slack is in the outbox."""
    console.put("/api/settings/secrets/slack_bot_token", json={"value": TOKEN})
    console.put("/api/settings/notifications", json={"slack_channel": "#owner"})
    seen = slack_answers(monkeypatch, *(answers or (slack_ok(),)))
    console.post("/api/settings/test/slack")
    return seen


def test_slack_cannot_be_turned_live_with_no_token_no_channel_and_no_test(console):
    tried = console.put("/api/settings/connectors", params={"dry_run": True}, json={"slack": {"mode": "live"}})
    saved = console.put("/api/settings/connectors", json={"slack": {"mode": "live"}})

    for r in (tried, saved):
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert all(msg(k, "en") in detail for k in ("settings.refuse.live.slack", "settings.gap.slack_token",
                                                     "settings.gap.slack_channel", "settings.gap.slack_untested"))
    slack = console.get("/api/settings").json()["sending"]["slack"]
    assert slack["mode"] == "outbox" and slack["sends"] is False and len(slack["blocks_live"]) == 3
    assert saved.status_code in console_reads_the_sentence_of(), "the console shows the refusal's sentence"


def test_a_token_and_channel_never_tested_cannot_be_turned_live(console):
    console.put("/api/settings/secrets/slack_bot_token", json={"value": TOKEN})
    console.put("/api/settings/notifications", json={"slack_channel": "#owner"})

    r = console.put("/api/settings/connectors", json={"slack": {"mode": "live"}})

    assert r.status_code == 409 and msg("settings.gap.slack_untested", "en") in r.json()["detail"]
    blocks = console.get("/api/settings/sending").json()["slack"]["blocks_live"]
    assert blocks == [msg("settings.gap.slack_untested", "en")]


def test_the_outbox_test_asks_slack_about_the_token_and_posts_nothing(console, monkeypatch):
    seen = connected(console, monkeypatch)

    assert [r.url.path for r in seen] == ["/api/auth.test"], "one read-only check, no chat.postMessage"
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}" and seen[0].content == b""
    assert console.get("/api/settings/sending").json()["slack"]["blocks_live"] == []


def test_a_pass_with_one_channel_does_not_turn_slack_live_on_another(console, monkeypatch):
    connected(console, monkeypatch)
    console.put("/api/settings/notifications", json={"slack_channel": "#launch"})

    r = console.put("/api/settings/connectors", json={"slack": {"mode": "live"}})

    said = msg("settings.gap.slack_channel_untested", "en", channel="#owner")
    assert r.status_code == 409 and said in r.json()["detail"]
    assert console.get("/api/settings/sending").json()["slack"]["blocks_live"] == [said]
    slack_answers(monkeypatch, slack_ok())
    console.post("/api/settings/test/slack")
    assert console.put("/api/settings/connectors", json={"slack": {"mode": "live"}}).status_code == 200


def test_an_outbox_test_slack_could_not_answer_says_nothing_was_posted_and_does_not_pass(console, monkeypatch):
    connected(console, monkeypatch, lambda: httpx.Response(503))

    r = console.post("/api/settings/test/slack")

    assert r.status_code == 502 and r.json()["detail"] == msg("notify.slack.check_unanswered", "en")
    assert msg("settings.gap.slack_untested", "en") in console.get("/api/settings/sending").json()["slack"]["blocks_live"]


def test_slack_is_never_asked_unless_the_owner_presses_the_test(console, monkeypatch):
    seen = slack_answers(monkeypatch, slack_ok())
    console.put("/api/settings/secrets/slack_bot_token", json={"value": TOKEN})
    console.put("/api/settings/notifications", json={"slack_channel": "#owner"})
    console.get("/api/settings")
    console.get("/api/settings/sending")
    console.put("/api/settings/connectors", params={"dry_run": True}, json={"slack": {"mode": "live"}})

    assert seen == []


def test_slack_with_a_tested_token_and_its_channel_goes_live_and_really_sends(console, monkeypatch):
    connected(console, monkeypatch)

    turned = console.put("/api/settings/connectors", json={"slack": {"mode": "live"}})

    assert turned.status_code == 200 and turned.json()["cannot_send"] == []
    assert console.get("/api/settings/sending").json()["slack"] == {"mode": "live", "missing": [], "sends": True,
                                                                    "blocks_live": []}
    assert TOKEN not in turned.text and TOKEN[-12:] not in console.get("/api/settings/sending").text


def test_a_new_token_needs_its_own_test(console, monkeypatch):
    connected(console, monkeypatch)
    console.put("/api/settings/secrets/slack_bot_token", json={"value": TOKEN.replace("wxyz", "abcd")})

    r = console.put("/api/settings/connectors", json={"slack": {"mode": "live"}})

    assert r.status_code == 409 and msg("settings.gap.slack_untested", "en") in r.json()["detail"]
    console.post("/api/settings/test/slack")
    assert console.put("/api/settings/connectors", json={"slack": {"mode": "live"}}).status_code == 200


def test_a_token_slack_refused_cannot_be_turned_live_until_a_test_passes(console, monkeypatch):
    connected(console, monkeypatch, refused("invalid_auth"))

    r = console.put("/api/settings/connectors", json={"slack": {"mode": "live"}})

    assert r.status_code == 409
    assert msg("settings.gap.slack_refused", "en") in r.json()["detail"]
    assert msg("settings.gap.slack_untested", "en") in r.json()["detail"]
    slack_answers(monkeypatch, slack_ok())
    console.post("/api/settings/test/slack")
    assert console.put("/api/settings/connectors", json={"slack": {"mode": "live"}}).status_code == 200


def test_a_refusal_after_a_pass_undoes_the_pass(console, monkeypatch):
    connected(console, monkeypatch, slack_ok(), refused("token_revoked"))
    console.post("/api/settings/test/slack")

    r = console.put("/api/settings/connectors", json={"slack": {"mode": "live"}})

    assert r.status_code == 409 and msg("settings.gap.slack_untested", "en") in r.json()["detail"]


def test_a_slack_already_live_keeps_sending_without_a_test_on_record(console):
    """The gate is on turning Slack live. One the owner turned live before the gate keeps its mode and still sends."""
    console.put("/api/settings/secrets/slack_bot_token", json={"value": TOKEN})
    console.put("/api/settings/notifications", json={"slack_channel": "#owner"})
    with session_scope() as s:
        app_settings.save(s, "connectors", {"slack": {"mode": "live"}}, actor="test")

    slack = console.get("/api/settings/sending").json()["slack"]
    again = console.put("/api/settings/connectors", json={"slack": {"mode": "live"}, "email": {"mode": "off"}})

    assert slack["sends"] is True and slack["mode"] == "live"
    assert again.status_code == 200, "a save that keeps Slack live is not a save that turns it live"


def test_email_live_without_a_mail_server_says_so(console, monkeypatch):
    blocked = console.put("/api/settings/connectors", params={"dry_run": True}, json={"email": {"mode": "live"}})
    [w] = blocked.json()["cannot_send"]
    assert w["connector"] == "email" and "SMTP" in w["text"]

    monkeypatch.setattr(settings(), "smtp_host", "smtp.example.com")
    ready = console.put("/api/settings/connectors", params={"dry_run": True}, json={"email": {"mode": "live"}})
    assert ready.json()["cannot_send"] == []
    assert "smtp.example.com" not in console.get("/api/settings/sending").text


def test_only_a_save_that_turns_a_connector_live_reports_it(console):
    console.put("/api/settings/connectors", json={"email": {"mode": "live"}})
    again = console.put("/api/settings/connectors", json={"email": {"mode": "live"}, "slack": {"mode": "off"}})
    assert again.json()["cannot_send"] == [], "Email was live already: the header says it, the save does not repeat it"
    off = console.put("/api/settings/connectors", json={"email": {"mode": "outbox"}}).json()
    assert off["cannot_send"] == []
    assert console.get("/api/settings/sending").json()["email"]["sends"] is False


def test_the_reasons_follow_the_language(console):
    console.put("/api/settings/general", json={"language": "ko"})
    r = console.put("/api/settings/connectors", params={"dry_run": True}, json={"slack": {"mode": "live"}})
    assert r.status_code == 409
    assert all(msg(k, "ko") in r.json()["detail"] for k in ("settings.refuse.live.slack", "settings.gap.slack_untested"))


def test_a_slack_that_refused_the_token_is_not_claimed_as_sending(console, monkeypatch):
    """Settings said 'Really sending now: Slack notices' after the test button had seen Slack refuse the token, while
    Today said 'Reconnect Slack'. A known refusal is a missing part until a new token is saved."""
    connected(console, monkeypatch)
    console.put("/api/settings/connectors", json={"slack": {"mode": "live"}})
    slack_answers(monkeypatch, refused("invalid_auth"))

    console.post("/api/settings/test/slack")

    slack = console.get("/api/settings/sending").json()["slack"]
    assert slack["sends"] is False and slack["missing"] == [msg("settings.gap.slack_refused", "en")]
    assert console.get("/api/settings").json()["sending"]["slack"] == slack


def test_the_test_button_says_why_slack_refused_in_a_sentence(console, monkeypatch):
    """The console showed 'The server hit an error (502)' for 'Slack refused the bot token': the sentence is the
    owner's to read, so the answer carries it whatever its status."""
    connected(console, monkeypatch, refused("invalid_auth"))

    r = console.post("/api/settings/test/slack")

    assert r.json()["detail"] == msg("notify.slack.auth", "en") and TOKEN not in r.text
    assert r.status_code in console_reads_the_sentence_of(), "the console shows this status's sentence, not a status line"


def test_the_daily_briefing_is_off_until_the_owner_chooses_it():
    """The owner's rule: no briefing goes to Slack unless they pick it in Notifications."""
    assert "daily_briefing" not in app_settings.defaults().notifications.kinds
    assert app_settings.defaults().notifies("daily_briefing") is False


def test_every_5xx_the_server_says_on_purpose_is_one_the_console_reads():
    """A server sentence sent with a 5xx the console does not read reaches the owner as 'The server hit an error'."""
    raised = {int(code) for p in (ROOT / "backend" / "justshipit" / "api").rglob("*.py")
              for code in re.findall(r"HTTPException\(\s*(5\d\d)", p.read_text(encoding="utf-8"))}
    assert raised and raised <= console_reads_the_sentence_of(), raised
