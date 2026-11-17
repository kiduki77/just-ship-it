"""Slack notices: what each connector mode does with a notice, the owner's preferences, a failing Slack, and the
token staying out of every place the failure could be written."""
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
from justshipit.api.app import app
from justshipit.contracts import notify as notices
from justshipit.db.models import ActivityEvent, Job
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.operations import notify
from justshipit.services import app_settings, queue, secrets
from fastapi.testclient import TestClient
from notify_kit import (
    TOKEN,
    choose,
    notice_jobs,
    ok,
    outbox_files,
    refused,
    retry_now,
    run_job,
    slack_answers,
    too_many,
    wipe,
)
from sqlalchemy import select
from test_korean_style import smells_in

SEOUL = ZoneInfo("Asia/Seoul")


@pytest.fixture(autouse=True)
def clean():
    wipe()
    yield
    wipe()


@pytest.fixture
def console(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def decision(n: int = 910007) -> dict:
    return notices.decision_needed(n, "도메인 구매 승인", "새 도메인 12달러").model_dump(mode="json")


def live(monkeypatch, *answers) -> list[httpx.Request]:
    secrets.put("slack_bot_token", TOKEN)
    choose(mode="live")
    return slack_answers(monkeypatch, *answers)


# ------------------------------------------------------------------ modes

def test_slack_off_drops_the_notice_without_touching_the_network(monkeypatch):
    choose(mode="off")
    monkeypatch.setattr(notify, "_client", lambda: pytest.fail("Slack was called while the connector is off"))

    assert notify.deliver(decision()) == "dropped"
    assert outbox_files() == []
    with session_scope() as s:
        assert queue.notify(s, notices.Notice(**decision())) is None


def test_outbox_writes_one_file_with_the_message_and_a_console_link():
    choose()

    assert notify.deliver(decision()) == "outbox"
    assert notify.deliver(decision()) == "outbox"

    [path] = outbox_files()
    assert ":" not in path.name
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["channel"] == "#company" and saved["key"] == "notify:decision_needed:REQ-910007:pending"
    assert f"*{msg('notify.label.decision_needed', 'en')}* · REQ-910007" in saved["text"]
    assert "도메인 구매 승인" in saved["text"], "the agent's title stays as written"
    assert f"<http://127.0.0.1:8000/inbox?req=910007|{msg('notify.view', 'en')}>" in saved["text"]


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_notice_is_put_into_words_when_it_is_sent_in_the_owners_language_then(lang):
    queued = notices.owner_task(910007, "Show HN post", "post").model_dump(mode="json")
    choose(kinds=["owner_task"])
    with session_scope() as s:
        app_settings.save(s, "general", {"language": lang}, actor="test")

    notify.deliver(queued)

    [path] = outbox_files()
    said = json.loads(path.read_text(encoding="utf-8"))["text"]
    assert msg("notify.label.owner_task", lang) in said and msg("owner_task.kind.post", lang) in said
    if lang == "en":
        assert not any("가" <= c <= "힣" for c in said), said
    else:
        assert smells_in(said) == set(), said


def test_a_notice_queued_as_plain_text_before_the_catalog_still_reads():
    stored = {"kind": "failed", "ref": "P-1", "title": "실행이 실패했습니다", "body": "이유", "link": "/",
              "dedupe_key": "notify:failed:P-1:job1"}

    text_out = notify.render(notices.Notice.model_validate(stored), "http://127.0.0.1:8000", "en")

    assert "실행이 실패했습니다" in text_out and msg("notify.label.failed", "en") in text_out


def test_live_posts_to_the_channel_with_the_stored_token(monkeypatch):
    seen = live(monkeypatch, ok())

    assert notify.deliver(decision()) == "sent"

    [request] = seen
    assert str(request.url) == notify.POST_URL
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    body = json.loads(request.content)
    assert body["channel"] == "#company" and "REQ-910007" in body["text"] and "token" not in body
    assert outbox_files() == []


def test_the_link_uses_the_console_address_the_owner_set():
    choose(console_url="https://company.example.com/")

    notify.deliver(decision())

    [path] = outbox_files()
    assert (f"<https://company.example.com/inbox?req=910007|{msg('notify.view', 'en')}>"
            in json.loads(path.read_text(encoding="utf-8"))["text"])


def test_the_console_address_must_be_a_web_address(korean_owner, console):
    refused_body = console.put("/api/settings/notifications", json={"console_url": "ftp://x"})
    assert refused_body.status_code == 422 and "콘솔 주소" in refused_body.json()["detail"]

    saved = console.put("/api/settings/notifications", json={"console_url": "https://company.example.com/"})
    assert saved.status_code == 200 and saved.json()["effective"]["console_url"] == "https://company.example.com"


def test_an_agents_title_cannot_make_slack_markup():
    n = notices.notice("failed", "P-1", "job1", "<!channel> 열어 <http://evil.example|여기> & 끝", "<@U123>")

    text_out = notify.render(n, "http://127.0.0.1:8000", "ko")

    assert "<!channel>" not in text_out and "<@U123>" not in text_out and "<http://evil.example" not in text_out
    assert "&lt;!channel&gt;" in text_out and "&amp;" in text_out
    assert text_out.count("<") == 1 and text_out.endswith("|콘솔에서 보기>")


# ------------------------------------------------------------------ preferences

def test_a_kind_the_owner_did_not_choose_is_dropped_at_the_source_and_at_the_sender():
    choose(kinds=["failed"])

    with session_scope() as s:
        assert queue.notify(s, notices.Notice(**decision())) is None
    assert notice_jobs() == []
    assert notify.deliver(decision()) == "dropped"
    assert outbox_files() == []


def test_a_chosen_kind_is_queued():
    choose(kinds=["decision_needed", "daily_briefing"])

    with session_scope() as s:
        job = queue.notify(s, notices.Notice(**decision()))
        assert job is not None and job.idempotency_key == "notify:decision_needed:REQ-910007:pending"


@pytest.mark.parametrize("hour, minute, waits", [(23, 30, 7.5 * 3600), (22, 0, 9 * 3600), (3, 0, 4 * 3600),
                                                (7, 0, 0), (12, 0, 0), (21, 59, 0)])
def test_quiet_hours_across_midnight_wait_for_their_end(hour, minute, waits):
    quiet = app_settings.NotificationSettings(quiet_hours={"start": "22:00", "end": "07:00"})
    at = datetime(2026, 11, 3, hour, minute, tzinfo=SEOUL).astimezone(timezone.utc)

    assert quiet.quiet_wait_s("Asia/Seoul", at) == waits


def test_quiet_hours_within_a_day_and_none_at_all():
    at = datetime(2026, 11, 3, 13, 30, tzinfo=SEOUL).astimezone(timezone.utc)

    assert app_settings.NotificationSettings(quiet_hours={"start": "13:00", "end": "14:00"}
                                             ).quiet_wait_s("Asia/Seoul", at) == 1800
    assert app_settings.NotificationSettings().quiet_wait_s("Asia/Seoul", at) == 0
    assert app_settings.NotificationSettings(quiet_hours={"start": "08:00", "end": "08:00"}
                                             ).quiet_wait_s("Asia/Seoul", at) == 0


def test_a_notice_in_quiet_hours_is_held_until_they_end_and_not_dropped():
    now = datetime.now(SEOUL)
    choose(quiet_hours={"start": f"{now - timedelta(hours=1):%H:%M}", "end": f"{now + timedelta(hours=1):%H:%M}"})

    with session_scope() as s:
        job = queue.notify(s, notices.Notice(**decision()))
        s.flush()
        waits = (job.run_after - datetime.now(timezone.utc)).total_seconds()
    with session_scope() as s:
        assert queue.claim(s, "worker-quiet", ["notify"]) is None

    assert 55 * 60 < waits <= 61 * 60


def test_a_settings_read_that_fails_does_not_fail_the_change_it_reports(monkeypatch):
    def broken():
        raise RuntimeError("database away")
    monkeypatch.setattr(app_settings, "effective", broken)

    with session_scope() as s:
        job = queue.notify(s, notices.Notice(**decision()))

    assert job is not None and job.idempotency_key == "notify:decision_needed:REQ-910007:pending"


# ------------------------------------------------------------------ a Slack that refuses

def test_a_429_waits_the_retry_after_and_sends_once(monkeypatch):
    seen = live(monkeypatch, too_many("2"), ok())
    slept: list[float] = []

    notify.post_message("#company", "안녕", lang="en", sleep=slept.append)

    assert slept == [2] and len(seen) == 2


def test_a_429_that_asks_for_a_long_wait_is_left_to_the_queue(monkeypatch):
    seen = live(monkeypatch, too_many("120"))
    slept: list[float] = []

    with pytest.raises(notify.NotifyError, match="120"):
        notify.post_message("#company", "안녕", lang="en", sleep=slept.append)

    assert slept == [] and len(seen) == 1


def test_a_slack_that_keeps_saying_429_gives_up_after_a_few_tries(monkeypatch):
    seen = live(monkeypatch, too_many("1"))
    slept: list[float] = []

    with pytest.raises(notify.NotifyError, match=msg("notify.slack.busy", "ko", s=1)):
        notify.post_message("#company", "안녕", lang="ko", sleep=slept.append)

    assert slept == [1, 1] and len(seen) == notify.TRIES


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("answer, key, values", [
    (refused("invalid_auth"), "notify.slack.auth", {}), (refused("token_revoked"), "notify.slack.auth", {}),
    (lambda: httpx.Response(401), "notify.slack.auth", {}),
    (refused("channel_not_found"), "notify.slack.channel_not_found", {}),
    (refused("not_in_channel"), "notify.slack.not_in_channel", {}),
    (refused("missing_scope"), "notify.slack.missing_scope", {}),
    (refused("weird_code"), "notify.slack.refused", {"code": "weird_code"}),
    (lambda: httpx.Response(503), "notify.slack.server_error", {"status": 503}),
    (lambda: httpx.Response(200, text="<html>"), "notify.slack.unreadable", {}),
    (httpx.ConnectTimeout("slow"), "notify.slack.cant_connect", {"error": "ConnectTimeout"}),
])
def test_a_refusal_says_what_to_do_in_the_owners_language_without_the_token(monkeypatch, answer, key, values, lang):
    live(monkeypatch, answer)

    with pytest.raises(notify.NotifyError) as raised:
        notify.post_message("#company", "안녕", lang=lang)

    message = str(raised.value)
    assert message == msg(key, lang, **values) and TOKEN not in message
    assert any("가" <= c <= "힣" for c in message) == (lang == "ko")


def test_a_live_notice_without_a_token_or_a_channel_says_what_to_set(monkeypatch):
    choose(mode="live")
    slack_answers(monkeypatch, ok())
    with pytest.raises(notify.NotifyError, match=msg("notify.slack.no_token", "en")):
        notify.deliver(decision())

    secrets.put("slack_bot_token", TOKEN)
    choose(mode="live", channel="")
    with pytest.raises(notify.NotifyError, match=msg("notify.slack.no_channel", "en")):
        notify.deliver(decision())


def test_the_token_reaches_no_log_no_job_error_and_no_event(monkeypatch, caplog):
    seen = live(monkeypatch, httpx.ConnectError("refused"), refused("not_in_channel"), ok())
    caplog.set_level(logging.DEBUG)
    with session_scope() as s:
        queue.notify(s, notices.Notice(**decision()))

    first = run_job()
    retry_now(first.id)
    second = run_job()
    retry_now(first.id)
    third = run_job()

    assert (first.status, second.status, third.status) == ("queued", "queued", "done") and len(seen) == 3
    assert msg("notify.slack.cant_connect", "en", error="ConnectError") in first.last_error
    assert msg("notify.slack.not_in_channel", "en") in second.last_error and third.last_error is None
    with session_scope() as s:
        everything = [j.last_error or "" for j in s.scalars(select(Job))] + [
            f"{e.summary} {e.data}" for e in s.scalars(select(ActivityEvent))]
    assert TOKEN not in caplog.text and not any(TOKEN in text_ for text_ in everything)
    assert TOKEN in seen[0].headers["Authorization"], "the check above would pass trivially without the token in use"


# ------------------------------------------------------------------ one message across retries

def test_a_notice_is_one_message_across_a_retry_a_repeat_and_a_second_source(monkeypatch):
    seen = live(monkeypatch, httpx.ConnectError("refused"), ok())
    with session_scope() as s:
        assert queue.notify(s, notices.Notice(**decision())) is not None
    with session_scope() as s:
        assert queue.notify(s, notices.Notice(**decision())) is None

    failed = run_job()
    retry_now(failed.id)
    sent = run_job()
    with session_scope() as s:
        assert queue.notify(s, notices.Notice(**decision())) is None
        assert queue.claim(s, "worker-notify-test", ["notify"]) is None

    assert (failed.status, sent.status, sent.attempts) == ("queued", "done", 2)
    assert len(notice_jobs()) == 1
    assert len(seen) == 2


# ------------------------------------------------------------------ the test button

def test_the_test_button_on_a_connector_that_is_off_refuses_in_the_owners_language(console):
    choose(mode="off")

    assert console.post("/api/settings/test/slack").json()["detail"] == msg("notify.test.off", "en")
    with session_scope() as s:
        app_settings.save(s, "general", {"language": "ko"}, actor="test")
    r = console.post("/api/settings/test/slack")

    assert r.status_code == 409 and r.json()["detail"] == msg("notify.test.off", "ko")


def test_the_test_button_in_outbox_mode_checks_the_token_and_keeps_the_message(console, monkeypatch):
    choose()
    secrets.put("slack_bot_token", TOKEN)
    seen = slack_answers(monkeypatch, ok())

    r = console.post("/api/settings/test/slack")

    assert r.status_code == 200 and r.json()["mode"] == "outbox" and "outbox" in r.json()["message"]
    assert [q.url.path for q in seen] == ["/api/auth.test"], "nothing posted to the channel"
    [path] = outbox_files()
    assert path.name not in r.json()["message"] and "var" not in r.json()["message"], "no server file path for the owner"
    assert "Send right away" in r.json()["message"], "names the control the owner sees, not the internal mode"
    assert msg("notify.test.title", "en") in json.loads(path.read_text(encoding="utf-8"))["text"]


def test_the_test_button_live_sends_one_message_and_returns_no_token(console, monkeypatch):
    seen = live(monkeypatch, ok())

    r = console.post("/api/settings/test/slack")

    assert r.status_code == 200 and r.json()["mode"] == "live" and "#company" in r.json()["message"]
    assert len(seen) == 1 and msg("notify.test.title", "en") in json.loads(seen[0].content)["text"]
    assert TOKEN not in r.text
    with session_scope() as s:
        event = list(s.scalars(select(ActivityEvent).where(ActivityEvent.verb == "connection_tested")))[-1]
        assert TOKEN not in f"{event.summary} {event.data}" and event.actor == "owner"


def test_the_test_button_reports_a_slack_that_refuses_without_the_token(console, monkeypatch):
    live(monkeypatch, refused("invalid_auth"))

    r = console.post("/api/settings/test/slack")

    assert r.status_code == 502 and r.json()["detail"] == msg("notify.slack.auth", "en") and TOKEN not in r.text


def test_the_test_button_in_outbox_mode_without_a_token_says_to_save_one(console, monkeypatch):
    choose()
    seen = slack_answers(monkeypatch, ok())

    r = console.post("/api/settings/test/slack")

    assert r.status_code == 502 and r.json()["detail"] == msg("notify.slack.no_token", "en") and seen == []
    assert outbox_files() == []


def test_slack_live_is_a_mode_the_settings_accept_after_a_passed_test(console, monkeypatch):
    assert "live" in app_settings.SUPPORTED_MODES["slack"]
    choose()
    secrets.put("slack_bot_token", TOKEN)
    slack_answers(monkeypatch, ok())
    console.post("/api/settings/test/slack")

    r = console.put("/api/settings/connectors", json={"slack": {"mode": "live"}})

    assert r.status_code == 200 and r.json()["effective"]["slack"]["mode"] == "live"


def test_a_notice_that_cannot_be_sent_shows_in_the_console_as_a_failed_slack_job(monkeypatch):
    from justshipit.api import console as console_api
    live(monkeypatch, refused("channel_not_found"))
    with session_scope() as s:
        job = queue.notify(s, notices.Notice(**decision(910008)))
        job.max_attempts = 1

    failed = run_job()

    [alert] = [a for a in console_api.alerts(include_test=True) if a["kind"] == "job_failed" and a["job_id"] == failed.id]
    assert failed.status == "failed" and alert["title"] == msg("console.job.notify", "en")
    assert msg("notify.slack.channel_not_found", "en") in alert["detail"] and TOKEN not in alert["detail"]
