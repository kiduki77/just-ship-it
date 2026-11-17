"""A to-do that holds more than one action, or runs longer than one sitting, reads as ordered steps the owner ticks off
one sitting at a time (persona round 6: OT-44 "오세린님과 이야기하기" held a message to send, four questions and two
points; OT-49 held eleven numbered lines of a notebook to set up; OT-39 put "Steps: 1) … 4)" inside one paragraph).
The Today list counts the minutes of the steps left, so a to-do half done takes half a day's time."""
import pytest
from people_kit import add, make_business
from sqlalchemy import update

from justshipit.db.models import AgentRequest, OwnerTask
from justshipit.services import owner_tasks, people_tasks, today, todo_steps
from test_decision_card import api  # noqa: F401 (a fixture)

MESSAGE = {"kind": "message", "to": "민지 님", "body": "민지 님, 요즘 반찬은 어디서 사세요? 편하실 때 답 주세요."}
NOTEBOOK = ("오늘 저녁부터 매일 적어요.\n\n1. 공책 한 쪽을 하루로 써요.\n2. 세로줄을 그어 칸 7개를 만들어요.\n"
            "3. 반찬마다 한 줄씩 써요.\n\n다 적으면 사진을 찍어 두세요.")
GMAIL = ("Run a 10-minute test. Steps: 1) Pick a conversation with at least one reply. 2) Tick it and forward it as an "
         "attachment. 3) Count the .eml files. Tell us the counts.")


@pytest.fixture(autouse=True)
def _quiet(korean_owner, s):
    """A company-wide card another test left waiting is on every business's list."""
    s.execute(update(AgentRequest).where(AgentRequest.project_id.is_(None), AgentRequest.status == "PENDING")
              .values(status="CANCELLED"))
    s.commit()


def _first_talk(s) -> tuple[int, int]:
    b = make_business(s, name="정숙이네 반찬")
    minji = add(s, b, "minji", source="warm", platform="direct")
    task = people_tasks.create_task(s, minji.id, "contact", lang="ko", actor="system")
    task.card = {**task.card, "handout": MESSAGE}
    s.commit()
    return b.id, task.id


def _item(s, business_id: int, task_id: int) -> dict:
    s.expire_all()
    return next(i for i in today.build(s, business_id, lang="ko", everything=True)["items"] if i["ref"] == f"OT-{task_id}")


def _steps(api, task_id: int) -> list[dict]:
    r = api.get(f"/api/owner-tasks/{task_id}")
    assert r.status_code == 200, r.text
    return r.json()["steps"]


def test_a_first_talk_with_a_message_is_two_steps_send_then_talk(s, api):
    bid, tid = _first_talk(s)
    steps = _steps(api, tid)
    assert [(x["key"], x["text"], x["done"]) for x in steps] == [
        ("send", "아래 메시지를 보내요", False), ("talk", "이야기하면서 아래 질문 4개를 물어봐요", False)]
    assert all(x["minutes"] <= todo_steps.SITTING_MIN for x in steps), "each step fits one sitting"
    item = _item(s, bid, tid)
    assert item["meta"]["estimate_min"] == steps[0]["minutes"], "Today plans the next sitting, not the whole to-do"
    assert item["meta"]["steps"] == {"done": 0, "total": 2}


def test_ticking_a_step_keeps_it_and_the_list_counts_only_what_is_left(s, api):
    bid, tid = _first_talk(s)
    r = api.post(f"/api/owner-tasks/{tid}/steps", json={"key": "send", "done": True})
    assert r.status_code == 200, r.text
    assert [x["done"] for x in r.json()["steps"]] == [True, False]
    assert [x["done"] for x in _steps(api, tid)] == [True, False], "the tick is kept"
    talk = _steps(api, tid)[1]["minutes"]
    item = _item(s, bid, tid)
    assert (item["meta"]["estimate_min"], item["meta"]["steps"]) == (talk, {"done": 1, "total": 2})

    assert api.post(f"/api/owner-tasks/{tid}/steps", json={"key": "send", "done": False}).json()["steps"][0]["done"] is False


def test_a_step_the_to_do_does_not_have_or_a_closed_to_do_cannot_be_ticked(s, api):
    _, tid = _first_talk(s)
    assert api.post(f"/api/owner-tasks/{tid}/steps", json={"key": "nope", "done": True}).status_code == 409
    owner_tasks.skip(s, tid, note="이번 주는 못 해요")
    s.commit()
    assert api.post(f"/api/owner-tasks/{tid}/steps", json={"key": "send", "done": True}).status_code == 409


def test_numbered_lines_become_the_steps_in_order(s, api):
    b = make_business(s, name="정숙이네 반찬")
    notebook = OwnerTask(business_id=b.id, kind="other", title="공책 장부 적기", status="OPEN",
                         card={"what": "오늘부터 적을게요", "handout": {"kind": "steps", "to": None, "body": NOTEBOOK}})
    gmail = OwnerTask(business_id=b.id, kind="check", title="Test forwarding", status="OPEN",
                      card={"what": "Run the test", "background": GMAIL})
    s.add_all([notebook, gmail])
    s.commit()
    assert [x["text"] for x in _steps(api, notebook.id)] == [
        "공책 한 쪽을 하루로 써요.", "세로줄을 그어 칸 7개를 만들어요.", "반찬마다 한 줄씩 써요."]
    assert [x["text"] for x in _steps(api, gmail.id)] == [
        "Pick a conversation with at least one reply.", "Tick it and forward it as an attachment.",
        "Count the .eml files. Tell us the counts."]
    from_ = {tid: api.get(f"/api/owner-tasks/{tid}").json()["steps_from"] for tid in (notebook.id, gmail.id)}
    assert from_ == {notebook.id: "handout", gmail.id: "background"}, "the console folds the text the steps repeat"


def test_a_to_do_that_is_one_short_action_has_no_steps(s, api):
    b = make_business(s, name="정숙이네 반찬")
    minji = add(s, b, "minji", source="warm", platform="direct", stage="talked")
    follow = people_tasks.create_task(s, minji.id, "follow_up", lang="ko", actor="system")
    one = OwnerTask(business_id=b.id, kind="other", title="1.5시간 걸리는 일", status="OPEN",
                    card={"what": "1.5시간쯤 걸려요. 2. 이건 단계가 아니에요."})
    s.add(one)
    s.commit()
    assert _steps(api, follow.id) == [] and _steps(api, one.id) == []
    assert "steps" not in _item(s, b.id, follow.id)["meta"]


def test_options_listed_in_a_decisions_words_are_not_steps(s, api):
    """A request's summary enumerates the options the owner chose between; the to-do keeps it as its background."""
    b = make_business(s, name="정숙이네 반찬")
    chose = OwnerTask(business_id=b.id, kind="post", title="먼저 올릴 곳", status="OPEN",
                      card={"what": "단톡방에 올려요", "background": "Decide where to post first: 1) the apartment group "
                                                                 "chat, 2) the market cafe. Both are free."})
    s.add(chose)
    s.commit()
    assert _steps(api, chose.id) == []


def test_a_step_keeps_its_indented_lines_and_two_same_lines_are_two_steps(s, api):
    b = make_business(s, name="정숙이네 반찬")
    body = "1. 공책을 펴요.\n   첫 쪽은 비워 둬요.\n이건 안내 문단이에요.\n2. 날짜를 적어요.\n3. 날짜를 적어요.\n다 됐어요."
    task = OwnerTask(business_id=b.id, kind="other", title="장부", status="OPEN",
                     card={"handout": {"kind": "steps", "to": None, "body": body}})
    s.add(task)
    s.commit()
    steps = _steps(api, task.id)
    assert [x["text"] for x in steps] == ["공책을 펴요. 첫 쪽은 비워 둬요.", "날짜를 적어요.", "날짜를 적어요."]
    assert len({x["key"] for x in steps}) == 3
    r = api.post(f"/api/owner-tasks/{task.id}/steps", json={"key": steps[1]["key"], "done": True})
    assert [x["done"] for x in r.json()["steps"]] == [False, True, False]
