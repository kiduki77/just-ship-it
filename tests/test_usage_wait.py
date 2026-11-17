"""Usage caps turn a refused call into a wait: window math in the owner's timezone, the owner-facing reserve,
and a used-up subscription as the same signal."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from justshipit.agents import executor
from justshipit.db.models import AppSetting, LLMCallRecord
from justshipit.db.session import session_scope
from justshipit.runtime import quota
from justshipit.runtime.base import LLMError
from justshipit.services import app_settings, budget, usage
from justshipit.services.usage import MustWait
from sqlalchemy import delete

KST = timezone(timedelta(hours=9))


def kst(*args) -> datetime:
    return datetime(*args, tzinfo=KST)


@pytest.fixture(autouse=True)
def clean():
    def wipe():
        with session_scope(lease_checked=False) as s:
            s.execute(delete(AppSetting))
            s.execute(delete(LLMCallRecord))
    wipe()
    yield
    wipe()


def caps(**body) -> None:
    with session_scope(lease_checked=False) as s:
        app_settings.save(s, "usage", body, actor="test")


def spend(usd: float, at: datetime) -> None:
    with session_scope(lease_checked=False) as s:
        s.add(LLMCallRecord(call_id=uuid.uuid4().hex, agent="research", purpose="work", provider="claude",
                            status="ok", cost_usd=usd, created_at=at))


def test_the_day_turns_at_local_midnight_and_the_week_on_monday_in_seoul():
    just_before = usage.windows(kst(2026, 11, 3, 23, 59, 59), "Asia/Seoul")
    assert just_before.day_start == kst(2026, 11, 3)
    assert just_before.next_day == kst(2026, 11, 4)
    assert just_before.week_start == kst(2026, 11, 2)
    assert just_before.next_week == kst(2026, 11, 9)

    midnight = usage.windows(kst(2026, 11, 4, 0, 0, 0), "Asia/Seoul")
    assert midnight.day_start == kst(2026, 11, 4)
    assert midnight.week_start == kst(2026, 11, 2)

    sunday = usage.windows(kst(2026, 11, 8, 23, 59, 59), "Asia/Seoul")
    monday = usage.windows(kst(2026, 11, 9, 0, 0, 0), "Asia/Seoul")
    assert (sunday.week_start, sunday.next_week) == (kst(2026, 11, 2), kst(2026, 11, 9))
    assert (monday.week_start, monday.next_week) == (kst(2026, 11, 9), kst(2026, 11, 16))


def test_the_owner_zone_decides_the_day_not_utc():
    four_pm_utc = datetime(2026, 11, 3, 16, 0, tzinfo=timezone.utc)
    seoul = usage.windows(four_pm_utc, "Asia/Seoul")
    assert seoul.day_start == kst(2026, 11, 4)
    assert usage.windows(four_pm_utc, "UTC").day_start == datetime(2026, 11, 3, tzinfo=timezone.utc)


def test_a_spend_before_midnight_no_longer_counts_after_it():
    cfg = app_settings.UsageSettings(day_usd_eq=10, timezone="Asia/Seoul")
    spend(9, kst(2026, 11, 3, 23, 30))
    with session_scope(lease_checked=False) as s:
        usage.check(s, cfg, "decision", 0.0, now=kst(2026, 11, 3, 23, 59))
        with pytest.raises(MustWait) as wait:
            usage.check(s, cfg, "work", 0.0, now=kst(2026, 11, 3, 23, 59))
        assert wait.value.reason == "usage_cap_day"
        assert wait.value.until == kst(2026, 11, 4)
        usage.check(s, cfg, "work", 0.0, now=kst(2026, 11, 4, 0, 0))


def test_a_week_cap_holds_until_monday_midnight_even_when_the_day_is_free():
    cfg = app_settings.UsageSettings(day_usd_eq=1000, week_usd_eq=100, timezone="Asia/Seoul")
    spend(90, kst(2026, 11, 8, 23, 30))
    with session_scope(lease_checked=False) as s:
        with pytest.raises(MustWait) as wait:
            usage.check(s, cfg, "work", 0.0, now=kst(2026, 11, 8, 23, 59))
        assert (wait.value.reason, wait.value.until) == ("usage_cap_week", kst(2026, 11, 9))
        usage.check(s, cfg, "work", 0.0, now=kst(2026, 11, 9, 0, 0))


def test_with_both_windows_over_the_wait_is_for_the_later_one():
    cfg = app_settings.UsageSettings(day_usd_eq=10, week_usd_eq=20, timezone="Asia/Seoul")
    spend(30, kst(2026, 11, 4, 9))
    with session_scope(lease_checked=False) as s:
        with pytest.raises(MustWait) as wait:
            usage.check(s, cfg, "decision", 0.0, now=kst(2026, 11, 4, 10))
    assert (wait.value.reason, wait.value.until) == ("usage_cap_week", kst(2026, 11, 9))


def test_background_stops_at_the_reserve_while_the_owner_still_gets_answers():
    caps(day_usd_eq=100, owner_reserve_pct=20)
    spend(85, datetime.now(timezone.utc))

    with pytest.raises(MustWait) as wait:
        budget.reserve(None, None, "work")
    assert wait.value.reason == "usage_cap_day"
    assert "20%" in wait.value.detail

    held = budget.reserve(None, None, "decision")
    budget.settle(None, None, held, 0.0)


def test_no_cap_means_no_limit():
    spend(100000, datetime.now(timezone.utc))
    held = budget.reserve(None, None, "work")
    budget.settle(None, None, held, 0.0)


def test_calls_in_flight_count_against_the_cap_before_they_settle():
    caps(day_usd_eq=10, owner_reserve_pct=20)
    first = budget.reserve(None, None, "dev")
    second = budget.reserve(None, None, "dev")
    try:
        with pytest.raises(MustWait):
            budget.reserve(None, None, "dev")
    finally:
        budget.settle(None, None, first, 0.0)
        budget.settle(None, None, second, 0.0)
    third = budget.reserve(None, None, "dev")
    budget.settle(None, None, third, 0.0)


def test_unlisted_purposes_are_background():
    caps(day_usd_eq=100, owner_reserve_pct=20)
    spend(85, datetime.now(timezone.utc))
    assert usage.purpose("anything new").owner_facing is False
    with pytest.raises(MustWait):
        budget.gate("anything new")
    budget.gate("report")


def test_the_usage_view_reports_today_and_this_week_against_the_caps():
    caps(day_usd_eq=50, week_usd_eq=200)
    spend(12.5, datetime.now(timezone.utc))
    view = budget.usage_view()
    assert view["day"]["cap_usd_eq"] == 50 and view["day"]["used_usd_eq"] == 12.5
    assert view["week"]["cap_usd_eq"] == 200 and view["week"]["used_usd_eq"] == 12.5
    assert view["day"]["used_pct"] == 25.0
    assert view["timezone"] == "Asia/Seoul" and view["day"]["background_limit_pct"] == 80


def test_a_quota_error_from_the_cli_becomes_a_wait_and_marks_the_provider(owner_asked, monkeypatch):
    import time
    resets = time.time() + 7200

    def refused(*a, **kw):
        raise executor.AgentFailed("project_manager/plan: claude call failed") from LLMError(
            f"claude returned no JSON (exit 1): Claude AI usage limit reached|{int(resets)}", quota=True)
    monkeypatch.setattr(executor, "_run_once", refused)
    monkeypatch.setattr(executor.queue, "check_lease", lambda: None)

    with pytest.raises(MustWait) as wait:
        executor.run_agent("project_manager", "plan", "p", str, project_id=None)

    assert wait.value.reason == "subscription_limit"
    assert wait.value.until == datetime.fromtimestamp(int(resets), tz=timezone.utc)
    assert quota.down_until("claude") == int(resets)


def test_other_agent_failures_stay_failures(owner_asked, monkeypatch):
    def broken(*a, **kw):
        raise executor.AgentFailed("project_manager/plan: output failed validation")
    monkeypatch.setattr(executor, "_run_once", broken)
    monkeypatch.setattr(executor.queue, "check_lease", lambda: None)
    with pytest.raises(executor.AgentFailed):
        executor.run_agent("project_manager", "plan", "p", str, project_id=None)
