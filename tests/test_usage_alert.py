"""The owner sees what waits on usage and until when, in their own timezone; the alert goes when nothing waits."""
from datetime import datetime, timedelta, timezone

import pytest
from justshipit.api import console
from justshipit.api.app import app
from justshipit.db.models import AppSetting, Job
from justshipit.db.session import session_scope
from justshipit.services.usage import MustWait
from fastapi.testclient import TestClient
from sqlalchemy import delete, text
from test_usage_defer import enqueue, run_once, settle_queue


def _settle_waiting_jobs() -> None:
    """Other test files leave jobs waiting on a cap, and this file counts the waiting ones."""
    settle_queue()
    with session_scope() as s:
        s.execute(text("UPDATE jobs SET status = 'done', locked_by = NULL "
                       "WHERE payload ? 'usage_defer_test' OR wait_reason IS NOT NULL"))
        s.execute(delete(AppSetting))


@pytest.fixture(autouse=True)
def _clean():
    _settle_waiting_jobs()
    yield
    _settle_waiting_jobs()


def usage_waits() -> list[dict]:
    return [a for a in console.alerts(include_test=True) if a["kind"] == "usage_wait"]


def wait_until(hours: float, reason="usage_cap_day") -> MustWait:
    return MustWait(datetime.now(timezone.utc) + timedelta(hours=hours), reason, "상한")


def defer_a_run(project_id: int, hours: float = 9, reason="usage_cap_day") -> int:
    jid = enqueue("run_project", project_id=project_id)
    run_once("run_project", lambda p: (_ for _ in ()).throw(wait_until(hours, reason)))
    return jid


def test_the_alert_names_the_reason_the_time_in_seoul_and_the_waiting_project(korean_owner, s, project):
    s.commit()
    defer_a_run(project.id)

    (alert,) = usage_waits()

    assert alert["reason"] == "usage_cap_day" and alert["severity"] == "warn"
    assert alert["timezone"] == "Asia/Seoul" and "(KST)" in alert["until_local"]
    assert "하루 사용량 상한" in alert["detail"] and alert["until_local"] in alert["detail"]
    assert alert["projects"] == [{"project_id": project.id, "project": project.name}]
    assert [j["job_kind"] for j in alert["jobs"]] == ["retry_project"]


def test_the_alert_goes_when_the_time_has_come(s, project):
    s.commit()
    jid = defer_a_run(project.id)
    assert usage_waits()

    with session_scope() as s2:
        s2.get(Job, jid).run_after = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert usage_waits() == []


def test_the_alert_goes_when_the_job_is_claimed_again(s, project):
    s.commit()
    jid = defer_a_run(project.id)
    with session_scope() as s2:
        s2.get(Job, jid).run_after = datetime.now(timezone.utc) - timedelta(seconds=1)
    run_once("retry_project", lambda p: (_ for _ in ()).throw(RuntimeError("still failing")))
    assert usage_waits() == []


def test_one_alert_covers_every_waiting_job_and_shows_the_later_wait(s, project):
    s.commit()
    defer_a_run(project.id, hours=2, reason="usage_cap_day")
    enqueue("kpi_review", project_id=project.id)
    run_once("kpi_review", lambda p: (_ for _ in ()).throw(wait_until(30, "usage_cap_week")))

    (alert,) = usage_waits()

    assert alert["reason"] == "usage_cap_week"
    assert len(alert["jobs"]) == 2


def test_a_subscription_limit_is_named_as_such(korean_owner, s, project):
    s.commit()
    defer_a_run(project.id, hours=3, reason="subscription_limit")
    (alert,) = usage_waits()
    assert alert["reason"] == "subscription_limit" and alert["reason_label"] == "구독 사용 한도"


def test_no_waiting_jobs_no_alert():
    assert usage_waits() == []


def test_the_costs_read_model_carries_today_and_this_week_for_the_screen(api_token):
    client = TestClient(app, base_url="http://127.0.0.1:8000",
                        headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})
    client.put("/api/settings/usage", json={"day_usd_eq": 40, "week_usd_eq": 200})

    resp = client.get("/api/costs")
    body = resp.json()
    assert resp.status_code == 200, resp.text

    assert body["usage"]["day"]["cap_usd_eq"] == 40 and body["usage"]["week"]["cap_usd_eq"] == 200
    assert {"used_usd_eq", "used_pct", "resets_at"} <= body["usage"]["day"].keys()


def test_raising_the_cap_in_settings_lets_the_waiting_work_try_now(s, project, api_token):
    s.commit()
    jid = defer_a_run(project.id, hours=9, reason="usage_cap_day")
    client = TestClient(app, base_url="http://127.0.0.1:8000",
                        headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})

    assert client.put("/api/settings/usage", json={"day_usd_eq": 500}).status_code == 200

    with session_scope() as s2:
        assert s2.get(Job, jid).run_after <= datetime.now(timezone.utc)
    assert usage_waits() == []
