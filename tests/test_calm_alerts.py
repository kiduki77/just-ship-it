"""A task the company is already handling is a calm line, not a red failure; the reviewer's words wait under details.

Persona round 3: 박정숙's dashboard opened with a red "확인할 일 2건 … 업무가 실패했어요" while the company was redoing
the work (T-92, then T-100), and opening it showed the reviewer's 음슴체 ("…시간이 완전하지 않음… 실제보다 작게
보임")."""
import pytest
from justshipit.api import console
from justshipit.db.models import Task
from justshipit.i18n import msg
from ask_kit import by_project_manager

REVIEWER = "검토를 통과하지 못했어요: 픽업 시간이 완전하지 않음; 남는 수량이 실제보다 작게 보임"


def _failed(s, project, status: str, *, error: str = REVIEWER, review: dict | None = None) -> Task:
    project.status = status
    t = Task(project_id=project.id, key="k", title="세트 흐름과 카톡 문구에 금요일 8시 픽업 반영", owner="product",
             objective="o", acceptance_criteria=[], status="FAILED", error=error,
             review={"verdict": "unmet"} if review is None else review)
    s.add(t)
    s.commit()
    return t


def _alert(t: Task) -> dict:
    (a,) = [a for a in console.alerts(include_test=True) if a["kind"] == "task_failed" and a["task_id"] == t.id]
    return a


@pytest.mark.parametrize("status", ["RUNNING", "WAITING_HUMAN"])
def test_a_failed_task_of_a_running_project_is_calm_and_says_nothing_is_needed(korean_owner, s, project, status):
    t = _failed(s, project, status)
    a = _alert(t)
    assert (a["severity"], a["handled"]) == ("info", True)
    assert a["reason"] == msg("console.alert.task_handled", "ko") and "지금 할 일은 없어요" in a["reason"]
    assert "않음" not in a["reason"]
    failure = console.task_detail(t.id)["failure"]
    assert failure == {"handled": True, "says": msg("console.alert.task_handled", "ko")}


def test_the_english_owner_reads_it_in_english(s, project):
    t = _failed(s, project, "RUNNING")
    assert _alert(t)["reason"] == msg("console.alert.task_handled", "en")


def test_a_task_that_failed_outside_its_review_still_asks_the_owner(korean_owner, s, project):
    """A sandbox that is not built, or a CLI login gone, fails the task before any review: the owner may have to act."""
    t = _failed(s, project, "RUNNING", error="AgentFailed: sandbox image missing, run `ship sandbox build`", review={})
    a = _alert(t)
    assert (a["severity"], a["handled"]) == ("bad", False)
    assert a["reason"] != msg("console.alert.task_handled", "ko")


@pytest.mark.parametrize("status", ["FAILED", "PAUSED"])
def test_a_failed_task_of_a_stopped_project_still_asks_the_owner(korean_owner, s, project, status):
    t = _failed(s, project, status)
    a = _alert(t)
    assert (a["severity"], a["handled"]) == ("bad", False)
    assert console.task_detail(t.id)["failure"]["handled"] is False


# ------------------------------------------------------------------ a card the CEO withdrew says who and why

def test_a_card_the_project_manager_withdrew_says_who_and_why_in_one_owner_line(korean_owner, s, project):
    """REQ-76 read "취소 · 시스템이 정함" in the list and "철회했어요(CEO)" when opened, right as 박정숙 was approving."""
    from justshipit.services import requests
    from owner_task_kit import draft
    req = requests.create(s, draft(), from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    why = "예전 픽업 시간으로 쓴 글이라 고친 글로 다시 올려요."
    assert requests.withdraw_pending(s, req.id, by="project_manager", note=why)
    closed = requests.report(s, req, "ko")["closed"]
    assert closed == msg("request.closed.withdrawn_ceo", "ko", why=why)
    assert closed.startswith("프로젝트 매니저가") and why in closed
    assert requests.report(s, req, "en")["closed"].startswith("The Project Manager withdrew")
