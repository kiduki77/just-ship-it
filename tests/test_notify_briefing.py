"""The daily briefing: built from what exists, sent only when there is something to say, once a local day."""
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from justshipit.contracts.requests import AgentRequestDraft, RequestType, RiskLevel
from justshipit.db.models import OwnerTask
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.operations import notify
from justshipit.services import app_settings, notify_state, projects, requests
from notify_kit import choose, notice_jobs, outbox_files, run_job, wipe
from sqlalchemy import text
from test_korean_style import smells_in
from ask_kit import by_task, contract
from justshipit.contracts.work_path import HoldKind

SEOUL = ZoneInfo("Asia/Seoul")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    wipe()
    monkeypatch.setattr(notify, "BRIEFING_LINES", [])
    yield
    wipe()


def blocking_request(s, project, title: str = "도메인 구매 승인"):
    draft = AgentRequestDraft(type=RequestType.APPROVAL, title=title, summary="s", rationale="r", options=[],
                              recommended_option=None, action_kind=None, estimated_cost_usd=None,
                              risk_level=RiskLevel.LOW, context=contract(HoldKind.THIS_TASK))
    return requests.create(s, draft, from_agent="project_manager", project_id=project.id, origin=by_task(s, project.id))


def local(y, mo, d, h, mi) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=SEOUL).astimezone(timezone.utc)


def pending_count(s) -> int:
    return len(requests.pending(s))


@pytest.fixture
def own_requests_only(monkeypatch, project):
    """The briefing lists the first three requests it finds, and a shared test database can hold others that are
    older. Read the pending requests of this test's project only."""
    every = requests.pending
    monkeypatch.setattr(requests, "pending", lambda s, project_id=None: every(s, project_id=project.id))


# ------------------------------------------------------------------ the text

def test_the_briefing_lists_the_decisions_failures_tasks_and_usage(s, project, own_requests_only):
    before = pending_count(s)
    req = blocking_request(s, project)
    s.add(OwnerTask(project_id=project.id, kind="post", title="Show HN 게시", card={}, status="OPEN"))
    projects.create(s, "실패한 목표", name="실패한 프로젝트", budget_usd=1, start=False).status = "FAILED"
    app_settings.save(s, "usage", {"day_usd_eq": 50, "week_usd_eq": 200}, actor="test")
    s.commit()

    text_out = notify.briefing_text(s, lang="ko")

    assert f"기다리는 결정 {before + 1}건(그중 " in text_out
    assert f"· REQ-{req.id} 도메인 구매 승인" in text_out
    assert re.search(r"실패한 일: 프로젝트 [1-9]\d*건", text_out)
    assert re.search(r"남은 내 할 일 [1-9]\d*개", text_out)
    assert re.search(r"사용량\(정가 환산\): 오늘 \$[\d.]+/\$50\(\d+%\), 이번 주 \$[\d.]+/\$200\(\d+%\)", text_out)
    assert smells_in(text_out) == set(), text_out

    english = notify.briefing_text(s, lang="en")
    assert f"· REQ-{req.id} 도메인 구매 승인" in english, "a request's title stays as written"
    assert not any("가" <= c <= "힣" for c in english.replace("도메인 구매 승인", "")), english
    assert re.search(r"Usage at list price: today \$[\d.]+ of \$50 \(\d+%\), this week \$[\d.]+ of \$200 \(\d+%\)", english)


def test_usage_without_a_cap_says_so(s, project):
    blocking_request(s, project)
    s.flush()

    assert re.search(r"오늘 \$[\d.]+\(상한 없음\), 이번 주 \$[\d.]+\(상한 없음\)", notify.briefing_text(s, lang="ko"))
    assert re.search(r"today \$[\d.]+ \(no cap\), this week \$[\d.]+ \(no cap\)", notify.briefing_text(s, lang="en"))


def test_another_module_adds_its_line_in_the_owners_language(s, project):
    notify.add_briefing_line(lambda session, lang: {"en": "2 ideas to confirm", "ko": "확인할 아이디어 2건"}[lang])
    notify.add_briefing_line(lambda session, lang: None)

    assert "확인할 아이디어 2건" in notify.briefing_text(s, lang="ko")
    assert "2 ideas to confirm" in notify.briefing_text(s, lang="en")


def nothing_waits(monkeypatch):
    monkeypatch.setattr(requests, "pending", lambda s, project_id=None: [])
    monkeypatch.setattr(notify, "_count", lambda s, query: 0)
    monkeypatch.setattr(notify, "_failed_dev_jobs", lambda s, window: 0)


def test_with_nothing_to_say_there_is_no_briefing_even_though_usage_exists(s, monkeypatch):
    nothing_waits(monkeypatch)
    app_settings.save(s, "usage", {"day_usd_eq": 50}, actor="test")
    s.commit()

    assert notify.briefing_text(s, lang="en") is None


def test_an_empty_briefing_is_skipped_and_sends_nothing(monkeypatch):
    choose(kinds=["daily_briefing"])
    nothing_waits(monkeypatch)

    assert notify.send_briefing("2026-11-03") == "empty"
    assert outbox_files() == []


def test_a_briefing_with_something_to_say_goes_out_once_with_its_date(s, project, own_requests_only):
    choose(kinds=["daily_briefing"])
    blocking_request(s, project)
    s.commit()

    assert notify.send_briefing("2026-11-03") == "outbox"
    assert notify.send_briefing("2026-11-03") == "empty", "the same items are not news a second time"

    [path] = outbox_files()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["key"] == "notify:daily_briefing:2026-11-03:sent"
    assert f"*{msg('notify.label.daily_briefing', 'en')}* · 2026-11-03" in saved["text"]
    assert msg("notify.briefing.title", "en") in saved["text"] and "도메인 구매 승인" in saved["text"]
    assert len(saved["text"].splitlines()) >= 5


# ------------------------------------------------------------------ the schedule

def schedule(now: datetime):
    with session_scope() as s:
        job = notify.schedule_briefing(s, now)
        s.flush()
        return None if job is None else (job.idempotency_key, dict(job.payload))


def test_the_briefing_is_queued_once_a_local_day_after_the_briefing_time():
    choose(kinds=["daily_briefing"], briefing_time="09:00")

    assert schedule(local(2026, 11, 3, 8, 59)) is None
    assert schedule(local(2026, 11, 3, 9, 0)) == ("notify:daily_briefing:2026-11-03:run", {"date": "2026-11-03"})
    assert schedule(local(2026, 11, 3, 9, 1)) is None
    assert schedule(local(2026, 11, 3, 23, 59)) is None
    assert schedule(local(2026, 11, 4, 9, 30)) == ("notify:daily_briefing:2026-11-04:run", {"date": "2026-11-04"})
    assert len(notice_jobs("daily_briefing")) == 2


def test_a_worker_back_after_the_time_still_sends_that_days_briefing():
    choose(kinds=["daily_briefing"], briefing_time="09:00")

    assert schedule(local(2026, 11, 3, 14, 20)) is not None


def test_the_time_is_read_in_the_owners_timezone(s):
    choose(kinds=["daily_briefing"], briefing_time="09:00")
    app_settings.save(s, "usage", {"timezone": "UTC"}, actor="test")
    s.commit()

    assert schedule(local(2026, 11, 3, 9, 30)) is None, "09:30 in Seoul is 00:30 in UTC"
    assert schedule(datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)) is not None


def test_a_briefing_the_owner_did_not_choose_is_not_queued():
    choose(kinds=["decision_needed"])

    assert schedule(local(2026, 11, 3, 10, 0)) is None
    choose(mode="off", kinds=["daily_briefing"])
    assert schedule(local(2026, 11, 3, 10, 0)) is None
    assert notice_jobs("daily_briefing") == []


def test_a_briefing_due_in_quiet_hours_waits_for_them_to_end():
    choose(kinds=["daily_briefing"], briefing_time="09:00", quiet_hours={"start": "08:00", "end": "10:00"})

    with session_scope() as s:
        job = notify.schedule_briefing(s, local(2026, 11, 3, 9, 0))
        s.flush()
        waits = (job.run_after - datetime.now(timezone.utc)).total_seconds()

    assert 3500 < waits <= 3605


def test_the_queued_briefing_runs_through_the_worker_and_lands_in_the_outbox(s, project):
    choose(kinds=["daily_briefing"])
    blocking_request(s, project)
    s.commit()
    assert schedule(local(2026, 11, 3, 9, 0)) is not None

    done = run_job("daily_briefing")

    assert done.status == "done" and done.last_error is None
    [path] = outbox_files()
    assert json.loads(path.read_text(encoding="utf-8"))["key"] == "notify:daily_briefing:2026-11-03:sent"


# ------------------------------------------------------------------ only what is new

def age_everything(s) -> None:
    """Make whatever other tests left in the database old news: ten days old."""
    for table, column in (("projects", "updated_at"), ("dev_jobs", "updated_at"), ("owner_tasks", "created_at"),
                          ("agent_requests", "created_at")):
        s.execute(text(f"UPDATE {table} SET {column} = now() - interval '10 days'"))
    s.commit()
    s.expire_all()


def last_briefing(days_ago: float, rollup_days_ago: float = 1) -> None:
    now = datetime.now(timezone.utc)
    notify_state._write("briefing", {"at": (now - timedelta(days=days_ago)).isoformat(),
                                     "rollup_at": (now - timedelta(days=rollup_days_ago)).isoformat()})


def failed_project(s, name: str):
    p = projects.create(s, "실패한 목표", name=name, budget_usd=1, start=False)
    p.status = "FAILED"
    s.commit()
    return p


def test_an_old_failure_is_not_in_the_daily_briefing_but_a_new_one_is(s):
    old = failed_project(s, "오래된 실패")
    age_everything(s)
    last_briefing(1, rollup_days_ago=1)
    assert notify.briefing_text(s, lang="ko") is None, "a failure the last briefing already counted is not news"

    failed_project(s, "새 실패")

    assert "실패한 일: 프로젝트 1건, 개발 0건" in notify.briefing_text(s, lang="ko")
    assert old.status == "FAILED"


def test_a_failure_that_changed_since_the_last_briefing_counts_again(s):
    old = failed_project(s, "다시 실패한 프로젝트")
    age_everything(s)
    last_briefing(1)
    s.get(type(old), old.id).error = "또 실패했습니다"
    s.commit()

    assert "프로젝트 1건" in notify.briefing_text(s, lang="ko")


def test_requests_and_owner_tasks_count_only_when_they_came_after_the_last_briefing(s, project):
    req = blocking_request(s, project)
    s.add(OwnerTask(project_id=project.id, kind="post", title="Show HN 게시", card={}, status="OPEN"))
    s.commit()
    age_everything(s)
    last_briefing(1)
    assert notify.briefing_text(s, lang="ko") is None

    blocking_request(s, project, "인터뷰 모집 연락")
    s.add(OwnerTask(project_id=project.id, kind="post", title="블로그 게시", card={}, status="OPEN"))
    s.commit()

    text_out = notify.briefing_text(s, lang="ko")
    assert "기다리는 결정 1건" in text_out and "내 할 일 1개" in text_out
    assert f"REQ-{req.id}" not in text_out


def test_once_a_week_the_briefing_lists_what_has_stayed_open(s):
    failed_project(s, "오래 열린 실패")
    age_everything(s)

    last_briefing(1, rollup_days_ago=3)
    assert notify.briefing_text(s, lang="ko") is None
    last_briefing(1, rollup_days_ago=8)
    weekly = notify.briefing_text(s, lang="ko")

    assert weekly.startswith(msg("notify.briefing.weekly", "ko"))
    assert re.search(r"실패한 일: 프로젝트 [1-9]\d*건", weekly)


def test_a_briefing_that_went_out_is_the_new_start_and_a_skipped_one_is_not(s, project):
    choose(kinds=["daily_briefing"])
    blocking_request(s, project)
    s.commit()
    assert notify_state.briefing_window(datetime.now(timezone.utc)).since is None

    assert notify.send_briefing("2026-11-03") == "outbox"

    window = notify_state.briefing_window(datetime.now(timezone.utc))
    assert window.since is not None and not window.everything, "the first briefing was the roll-up"
    before = window.since
    assert notify.send_briefing("2026-11-04") == "empty"
    assert notify_state.briefing_window(datetime.now(timezone.utc)).since == before


def test_a_briefing_the_owner_no_longer_wants_does_not_move_the_last_time(s, project):
    choose(kinds=["daily_briefing"])
    blocking_request(s, project)
    s.commit()
    choose(kinds=["decision_needed"])

    assert notify.send_briefing("2026-11-03") == "dropped"
    assert notify_state.briefing_window(datetime.now(timezone.utc)).since is None
