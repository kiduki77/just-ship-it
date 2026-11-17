"""The sender judges a notice when it runs: notices go first, a handled subject sends nothing, quiet hours and a
refused token hold it, and a post whose outcome is unknown is not repeated blindly."""
import json
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
from justshipit.api import console as console_api
from justshipit.api.app import app
from justshipit.contracts import notify as notices
from justshipit.contracts.planning import RequestMergeOut
from justshipit.contracts.requests import AgentRequestDraft, RequestStatus, RequestType, RiskLevel
from justshipit.contracts.work_path import HoldKind
from justshipit.db.models import AgentRequest, Job, OwnerTask
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.operations import notify
from justshipit.services import app_settings, notify_state, queue, requests, secrets
from justshipit.settings import settings
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
from sqlalchemy import text
from ask_kit import by_task, contract

GONE = 910000  # a request or task that does not exist, so a notice about it has nothing to be judged against


def now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def clean():
    wipe()
    yield
    wipe()
    with session_scope() as s:
        s.execute(text("DELETE FROM jobs WHERE payload ? 'hold_test'"))


@pytest.fixture
def console(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def live(monkeypatch, *answers) -> list[httpx.Request]:
    secrets.put("slack_bot_token", TOKEN)
    return slack_answers(monkeypatch, *answers)


def quiet_now() -> dict:
    """A window around now in the owner's timezone, which is where quiet hours are read, not the host's."""
    local = now().astimezone(ZoneInfo(app_settings.effective().usage.timezone))
    return {"start": f"{local - timedelta(hours=1):%H:%M}", "end": f"{local + timedelta(hours=1):%H:%M}"}


def queue_notice(n: int = GONE + 7, *, kind: str = "decision_needed") -> Job:
    notice = (notices.decision_needed(n, f"결정 {n}", "요약") if kind == "decision_needed"
              else notices.owner_task(n, f"할 일 {n}", "post"))
    with session_scope() as s:
        job = queue.notify(s, notice)
        s.flush()
        s.expunge(job)
        return job


def request_for(s, project, *, blocking: bool = True, title: str | None = None, agent: str = "project_manager") -> AgentRequest:
    draft = AgentRequestDraft(type=RequestType.APPROVAL, title=title or f"결정 {time.time_ns()}", summary="s",
                              rationale="r", options=[], recommended_option=None, action_kind=None,
                              estimated_cost_usd=None, risk_level=RiskLevel.LOW,
                              context=contract(HoldKind.THIS_TASK if blocking else HoldKind.NOTHING))
    req = requests.create(s, draft, from_agent=agent, project_id=project.id, origin=by_task(s, project.id))
    s.commit()
    return req


def read_job(job_id: int) -> Job:
    with session_scope() as s:
        job = s.get(Job, job_id)
        s.expunge(job)
        return job


# ------------------------------------------------------------------ notices go first

def test_a_notice_is_claimed_before_older_work():
    with session_scope() as s:
        older = queue.enqueue(s, "kpi_review", {"project_id": None, "hold_test": True}, by_owner=True)
        s.flush()
        older_id = older.id
    choose()
    queue_notice(GONE + 21)
    with session_scope() as s:
        s.add(Job(gate="direct", kind="daily_briefing", payload={"date": "2026-11-03"}, idempotency_key="notify:daily_briefing:x:run"))

    with session_scope() as s:
        order = [queue.claim(s, "worker-first").kind for _ in range(2)]

    assert set(order) == {"notify", "daily_briefing"}, "both notices came ahead of the older kpi_review"
    assert read_job(older_id).status == "queued"


# ------------------------------------------------------------------ a subject already handled

def test_a_decision_answered_before_the_notice_runs_sends_nothing(s, project):
    choose()
    req = request_for(s, project)
    [job] = notice_jobs()
    with session_scope() as tx:
        tx.get(AgentRequest, req.id).status = RequestStatus.APPROVED.value

    done = run_job()

    assert (done.id, done.status, done.last_error) == (job.id, "done", None) and outbox_files() == []


def test_a_decision_still_waiting_is_sent(s, project):
    choose()
    request_for(s, project)

    done = run_job()

    assert done.status == "done" and len(outbox_files()) == 1


def test_a_request_merged_into_another_sends_nothing_for_the_merged_one(s, project):
    choose()
    kept = request_for(s, project, blocking=False, title="도메인 구매 승인")
    merged = request_for(s, project, title="인터뷰 참가자 모집 연락", agent="research")
    assert [j.payload["ref"] for j in notice_jobs()] == [f"REQ-{merged.id}"]

    with session_scope() as tx:
        requests.consolidate(tx, project.id, [RequestMergeOut(keep_id=kept.id, merge_ids=[merged.id],
                                                              merged_title="합친 결정", reason="같은 결정")], [])
    _, second = notice_jobs()

    assert second.payload["ref"] == f"REQ-{kept.id}", "the kept request became blocking and was told"
    run_job()
    run_job()
    [path] = outbox_files()
    assert json.loads(path.read_text(encoding="utf-8"))["ref"] == f"REQ-{kept.id}"


def test_an_owner_task_done_before_the_notice_runs_sends_nothing(s, project):
    choose(kinds=["owner_task"])
    s.commit()
    with session_scope() as tx:
        task = OwnerTask(project_id=project.id, kind="post", title="Show HN 게시", card={}, status="OPEN")
        tx.add(task)
        tx.flush()
        queue.notify(tx, notices.owner_task(task.id, task.title, "post"))
        task_id = task.id
    with session_scope() as tx:
        row = tx.get(OwnerTask, task_id)
        row.status, row.done_at = "DONE", now()

    done = run_job()

    assert done.status == "done" and outbox_files() == []


# ------------------------------------------------------------------ quiet hours at send time

def test_quiet_hours_that_began_after_the_notice_was_queued_hold_it(monkeypatch):
    choose()
    queue_notice()
    choose(quiet_hours=quiet_now())

    held = run_job()

    assert held.status == "queued" and held.attempts == 0 and outbox_files() == []
    assert 55 * 60 < (held.run_after - now()).total_seconds() <= 61 * 60
    choose(quiet_hours=None)
    retry_now(held.id)
    assert run_job().status == "done" and len(outbox_files()) == 1


def test_a_retry_that_lands_in_quiet_hours_waits_for_their_end(monkeypatch):
    choose(mode="live")
    seen = live(monkeypatch, httpx.ConnectError("refused"), ok())
    queue_notice()
    failed = run_job()
    assert failed.status == "queued" and len(seen) == 1

    choose(mode="live", quiet_hours=quiet_now())
    retry_now(failed.id)
    held = run_job()

    assert held.status == "queued" and len(seen) == 1, "no post inside quiet hours"
    assert (held.run_after - now()).total_seconds() > 55 * 60


# ------------------------------------------------------------------ a post whose outcome is unknown

@pytest.mark.parametrize("answer", [httpx.ReadTimeout("slow"), lambda: httpx.Response(503), httpx.RemoteProtocolError("cut")])
def test_a_post_that_may_have_landed_is_retried_once_and_then_stops(monkeypatch, answer):
    choose(mode="live")
    seen = live(monkeypatch, answer)
    job = queue_notice()

    first = run_job()
    retry_now(job.id)
    second = run_job()

    assert (first.status, second.status) == ("queued", "failed") and len(seen) == 2
    assert second.last_error.endswith(msg("notify.slack.gave_up", "en", error="").strip())
    assert TOKEN not in second.last_error


def test_a_retry_after_an_unconfirmed_post_that_succeeds_is_the_one_message_the_owner_sees(monkeypatch):
    choose(mode="live")
    seen = live(monkeypatch, httpx.ReadTimeout("slow"), ok())
    job = queue_notice()

    run_job()
    retry_now(job.id)

    assert run_job().status == "done" and len(seen) == 2


def test_a_post_that_never_left_the_machine_is_retried_up_to_the_limit(monkeypatch):
    choose(mode="live")
    seen = live(monkeypatch, httpx.ConnectError("refused"))
    job = queue_notice()

    states = []
    for _ in range(queue.NOTIFY_ATTEMPTS):
        retry_now(job.id)
        states.append(run_job().status)

    assert states == ["queued"] * 4 + ["failed"] and len(seen) == queue.NOTIFY_ATTEMPTS


# ------------------------------------------------------------------ a refused token

def test_a_refused_token_stops_every_call_and_holds_the_notices_until_the_owner_fixes_it(korean_owner, monkeypatch, console):
    choose(mode="live")
    seen = live(monkeypatch, refused("invalid_auth"), ok())
    first, second = queue_notice(GONE + 1), queue_notice(GONE + 2)

    held_first = run_job()
    held_second = run_job()

    assert (held_first.status, held_second.status, len(seen)) == ("queued", "queued", 1)
    assert held_first.attempts == 0 and (held_first.run_after - now()).total_seconds() > 50 * 60
    assert notify_state.slack_refused() is not None
    [alert] = [a for a in console_api.alerts(include_test=True) if a["kind"] == "slack_disconnected"]
    assert alert["title"] == "Slack 연결 끊김" and msg("notify.slack.auth", "ko") in alert["detail"]
    assert TOKEN not in alert["detail"]
    held_ids = {first.id, second.id}
    assert not [a for a in console_api.alerts(include_test=True)
                if a["kind"] == "job_failed" and a.get("job_id") in held_ids]

    saved = console.put("/api/settings/secrets/slack_bot_token", json={"value": TOKEN})

    assert saved.status_code == 200 and notify_state.slack_refused() is None
    assert not [a for a in console_api.alerts(include_test=True) if a["kind"] == "slack_disconnected"]
    assert (run_job().status, run_job().status, len(seen)) == ("done", "done", 3)
    assert {first.id, second.id} == {j.id for j in notice_jobs()}


def test_the_test_button_that_succeeds_lets_the_held_notices_go(monkeypatch, console):
    choose(mode="live")
    seen = live(monkeypatch, refused("invalid_auth"), ok())
    queue_notice()
    run_job()
    assert notify_state.slack_refused() is not None

    r = console.post("/api/settings/test/slack")

    assert r.status_code == 200 and notify_state.slack_refused() is None
    assert run_job().status == "done" and len(seen) == 3


def test_the_test_button_that_is_refused_marks_slack_disconnected(monkeypatch, console):
    choose(mode="live")
    live(monkeypatch, refused("token_revoked"))

    r = console.post("/api/settings/test/slack")

    assert r.status_code == 502 and notify_state.slack_refused() is not None


def test_a_refusal_that_is_not_about_the_token_does_not_disconnect_slack(monkeypatch):
    choose(mode="live")
    live(monkeypatch, refused("channel_not_found"))
    queue_notice()

    assert run_job().status == "queued" and notify_state.slack_refused() is None


# ------------------------------------------------------------------ a 429

def test_a_429_longer_than_ten_seconds_is_left_to_the_queue(monkeypatch):
    seen = live(monkeypatch, too_many("11"))

    with pytest.raises(notify.NotifyError, match="11"):
        notify.post_message("#company", "안녕", lang="en", sleep=lambda s_: pytest.fail("slept"))

    assert len(seen) == 1


def test_a_429_of_ten_seconds_is_waited_and_the_lease_is_confirmed_after_the_sleep(monkeypatch):
    seen = live(monkeypatch, too_many("10"), ok())
    steps: list[str] = []
    monkeypatch.setattr(queue, "confirm_lease", lambda: steps.append("confirm"))

    notify.post_message("#company", "안녕", lang="en", sleep=lambda s_: steps.append(f"sleep {s_}"))

    assert steps == ["sleep 10", "confirm"] and len(seen) == 2


def test_the_test_button_does_not_sleep_on_a_429(monkeypatch, console):
    choose(mode="live")
    seen = live(monkeypatch, too_many("2"))
    monkeypatch.setattr(time, "sleep", lambda s_: pytest.fail("the owner was made to wait"))

    r = console.post("/api/settings/test/slack")

    assert r.status_code == 429 and r.json()["detail"] == msg("notify.slack.busy_test", "en") and len(seen) == 1


# ------------------------------------------------------------------ a token that cannot be a header value

@pytest.mark.parametrize("stored", ["xoxb-ABCDEFéGHIJ", "xoxb-SECRET\nSECRETTAIL", "xoxb-SECRET\rX-Evil: 1", "xoxb-한글토큰"])
def test_a_stored_token_with_foreign_characters_is_refused_and_the_test_button_a_4xx(monkeypatch, console, stored):
    choose(mode="live")
    seen = slack_answers(monkeypatch, ok())
    path = settings().var_dir / "secrets" / "slack_bot_token"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stored, encoding="utf-8")

    with pytest.raises(notify.BadToken) as raised:
        notify.post_message("#company", "안녕", lang="ko")
    r = console.post("/api/settings/test/slack")

    assert str(raised.value) == msg("notify.slack.bad_token", "ko") and stored.strip()[6:12] not in str(raised.value)
    assert r.status_code == 422 and r.json()["detail"] == msg("notify.slack.bad_token", "en") and stored[6:12] not in r.text
    assert seen == [] and notify_state.slack_refused() is not None


def test_the_briefing_job_is_held_in_quiet_hours_like_any_notice(monkeypatch):
    choose(kinds=["daily_briefing"], quiet_hours=quiet_now())
    with session_scope() as s:
        s.add(Job(gate="direct", kind="daily_briefing", payload={"date": "2026-11-03"}, idempotency_key="notify:daily_briefing:2026-11-03:run"))
    monkeypatch.setattr(notify, "briefing_text", lambda s_, now=None, window=None, *, lang: "오늘 할 일")

    held = run_job("daily_briefing")

    assert held.status == "queued" and outbox_files() == []
    assert notify_state.briefing_window(now()).since is None, "a briefing that was held was not sent"
