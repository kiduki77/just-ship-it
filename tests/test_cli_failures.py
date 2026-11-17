"""A failed `claude -p` call: what marks the subscription used up, when it comes back, and what the call cost."""
import json
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from justshipit.agents import executor
from justshipit.db.models import BudgetMonth, LLMCallRecord
from justshipit.db.session import session_scope
from justshipit.runtime import claude_cli, quota
from justshipit.runtime.base import LLMError
from justshipit.db.models import Job
from justshipit.services import budget, queue
from justshipit.services.usage import MustWait
from pydantic import BaseModel
from sqlalchemy import select

pytestmark = pytest.mark.usefixtures("made_up_kinds", "owner_asked")


SEOUL = ZoneInfo("Asia/Seoul")


class Answer(BaseModel):
    text: str


@pytest.fixture(autouse=True)
def cli(monkeypatch):
    """The CLI answers with whatever `cli.out` holds, as JSON on stdout, or `cli.text` as plain stdout."""
    monkeypatch.setattr(claude_cli, "resolve_bin", lambda *a: "claude")

    class Fake:
        out: list = []
        stderr = b""

    def run(*a, **kw):
        body = Fake.out.pop(0)
        stdout = body.encode() if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode()
        return subprocess.CompletedProcess(a, 1, stdout=stdout, stderr=Fake.stderr)

    monkeypatch.setattr(claude_cli, "run_process", run)
    monkeypatch.setattr("justshipit.runtime._backoff_s", lambda attempt: 0)
    yield Fake


def _calls(call_ids_before: set[str]) -> list[LLMCallRecord]:
    with session_scope() as s:
        return [r for r in s.scalars(select(LLMCallRecord).order_by(LLMCallRecord.id)) if r.call_id not in call_ids_before]


def _call_ids() -> set[str]:
    with session_scope() as s:
        return set(s.scalars(select(LLMCallRecord.call_id)))


def test_a_model_writing_about_quotas_is_a_failed_call_not_a_used_up_subscription(cli):
    cli.out = [{"type": "result", "subtype": "success", "is_error": False, "structured_output": None,
                "result": "Import quotas: the usage limit reached in 2025 was a quota of 30,000 tons; "
                          "the rate limit resets 3am.", "total_cost_usd": 0.12}]
    with pytest.raises(executor.AgentFailed):
        executor.run_agent("research", "work", f"p-{uuid.uuid4()}", Answer, project_id=None)
    assert quota.down_until("claude") is None


def test_the_clis_own_limit_error_marks_claude_down_until_the_dated_reset(cli):
    reset = (datetime.now(SEOUL) + timedelta(days=3)).replace(hour=3, minute=0, second=0, microsecond=0)
    cli.out = [{"type": "result", "subtype": "success", "is_error": True, "api_error_status": 429,
                "result": f"You've hit your weekly limit · resets {reset:%b} {reset.day}, 3am (Asia/Seoul)",
                "total_cost_usd": 0}]
    with pytest.raises(MustWait) as wait:
        executor.run_agent("research", "work", f"p-{uuid.uuid4()}", Answer, project_id=None)
    assert (wait.value.reason, wait.value.until) == ("subscription_limit", reset)
    assert quota.down_until("claude") == reset.timestamp()


def test_a_limit_message_on_stderr_without_json_is_the_clis_error(cli):
    cli.out = [""]
    cli.stderr = b"Claude AI usage limit reached|1893456000"
    with pytest.raises(MustWait) as wait:
        executor.run_agent("research", "work", f"p-{uuid.uuid4()}", Answer, project_id=None)
    assert wait.value.until == datetime.fromtimestamp(1893456000, tz=timezone.utc)


def test_model_text_naming_a_timeout_is_not_retried(cli):
    cli.out = [{"is_error": False, "structured_output": None, "result": "The request timeout was 503 seconds"}]
    with pytest.raises(LLMError) as err:
        claude_cli.ClaudeRunner().run(claude_cli.LLMCall("research", "s", "p", {}))
    assert not err.value.transient and not err.value.quota


@pytest.mark.parametrize("said", [
    "You've hit your limit · resets 3am (Asia/Seoul)", "You've hit your 5-hour limit · resets 12:20am (Asia/Seoul)",
    "You've hit your weekly limit · resets Nov 4, 8pm (Asia/Seoul)", "Claude AI usage limit reached|1761789600",
])
def test_every_limit_message_the_cli_prints_is_a_quota(said):
    assert quota.is_quota_error(said)


@pytest.mark.parametrize("text, now, expected", [
    ("You've hit your 5-hour limit · resets 12:20am (Asia/Seoul)", datetime(2026, 11, 4, 22, 0, tzinfo=SEOUL),
     datetime(2026, 11, 5, 0, 20, tzinfo=SEOUL)),
    ("You've hit your 5-hour limit · resets 10:20am (UTC)", datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc),
     datetime(2026, 11, 4, 10, 20, tzinfo=timezone.utc)),
    ("You've hit your weekly limit · resets Nov 4, 8pm (Asia/Seoul)", datetime(2026, 11, 1, 12, 0, tzinfo=SEOUL),
     datetime(2026, 11, 4, 20, 0, tzinfo=SEOUL)),
    ("Your limit resets Jan 2, 3am (Asia/Seoul)", datetime(2026, 12, 30, 12, 0, tzinfo=SEOUL),
     datetime(2027, 1, 2, 3, 0, tzinfo=SEOUL)),
    ("Claude usage limit reached. Your limit resets 3am (Asia/Seoul)", datetime(2026, 10, 30, 0, 20, tzinfo=SEOUL),
     datetime(2026, 10, 30, 3, 0, tzinfo=SEOUL)),
])
def test_reset_times_the_cli_prints_are_read_in_the_zone_it_names(text, now, expected):
    assert quota.parse_reset(text, now) == expected.timestamp()


def test_a_reset_it_cannot_read_waits_an_hour():
    now = datetime.now(timezone.utc).timestamp()
    assert now + 3500 < quota.parse_reset("You've hit your limit · resets Feb 30, 3am") < now + 3700


def test_a_failed_call_books_the_cost_the_cli_reported(cli):
    before = _call_ids()
    with session_scope() as s:
        month = s.get(BudgetMonth, budget._month())
        spent = month.spent_usd if month else 0.0
    cli.out = [{"is_error": True, "subtype": "error_max_turns", "total_cost_usd": 0.75}]
    with pytest.raises(executor.AgentFailed):
        executor.run_agent("research", "work", f"p-{uuid.uuid4()}", Answer, project_id=None)
    (record,) = _calls(before)
    assert (record.status, record.cost_usd) == ("error", 0.75)
    with session_scope() as s:
        assert s.get(BudgetMonth, budget._month()).spent_usd == pytest.approx(spent + 0.75)


def test_a_retried_attempt_is_booked_with_the_one_that_answered(cli):
    before = _call_ids()
    cli.out = [{"is_error": True, "api_error_status": 529, "result": "API Error: overloaded", "total_cost_usd": 0.2},
               {"structured_output": {"text": "ok"}, "usage": {}, "total_cost_usd": 0.3, "modelUsage": {}}]
    assert executor.run_agent("research", "work", f"p-{uuid.uuid4()}", Answer, project_id=None) == Answer(text="ok")
    (record,) = _calls(before)
    assert (record.status, record.cost_usd) == ("ok", pytest.approx(0.5))


def test_a_job_that_keeps_waiting_on_usage_fails_instead_of_waiting_forever(monkeypatch):
    monkeypatch.setattr(queue, "max_deferrals", lambda: 2)
    wait = MustWait(datetime.now(timezone.utc) + timedelta(hours=1), "subscription_limit", "구독 사용 한도", "claude")
    with session_scope() as s:
        jid = queue.enqueue(s, "defer_probe", {"probe": str(uuid.uuid4())}).id
    seen = []
    for _ in range(3):
        with session_scope() as s:
            s.get(Job, jid).run_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        with session_scope() as s:
            job = queue.claim(s, "worker-defer", ["defer_probe"])
            assert job.id == jid
            queue.defer(s, jid, job.lease_token, wait)
        with session_scope() as s:
            seen.append(s.get(Job, jid).status)
    assert seen == ["queued", "queued", "failed"]
    with session_scope() as s:  # later tests count the console's failure alerts
        s.get(Job, jid).alert_dismissed_at = datetime.now(timezone.utc)
