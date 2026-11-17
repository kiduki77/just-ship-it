"""The owner task routes: the list, one task, and done, skip and reopen, with refusals in the owner's language."""
import pytest
from justshipit.api.app import app
from justshipit.db.models import OwnerTask
from justshipit.services import events as live
from justshipit.services import owner_tasks
from fastapi.testclient import TestClient
from lang_kit import owner_speaks
from owner_task_kit import events, open_task


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def test_the_list_counts_every_status_whichever_tab_is_showing(s, project, api):
    a, b, c = (open_task(s, project, title=f"게시 {i}") for i in range(3))
    owner_tasks.done(s, b.id, link="https://news.ycombinator.com/item?id=1")
    owner_tasks.skip(s, c.id, note="안 함")
    s.commit()

    everything = api.get("/api/owner-tasks", params={"business_id": project.business_id}).json()
    assert everything["counts"] == {"OPEN": 1, "DONE": 1, "SKIPPED": 1} and everything["open"] == 1
    assert everything["items"][0]["id"] == a.id, "open tasks come first"
    only_done = api.get("/api/owner-tasks", params={"status": "DONE", "business_id": project.business_id}).json()
    assert [t["id"] for t in only_done["items"]] == [b.id] and only_done["counts"]["OPEN"] == 1
    assert only_done["items"][0]["ref"] == f"OT-{b.id}" and only_done["items"][0]["status_label"] == owner_tasks.status_label("DONE", "en")


def test_the_api_walks_a_post_from_open_to_done_and_back(s, project, api):
    task = open_task(s, project)
    url = f"/api/owner-tasks/{task.id}"
    assert api.get(url).json()["card"]["channel"] == "hacker_news"

    missing = api.post(f"{url}/done", json={"note": "  "})
    assert missing.status_code == 409 and "link" in missing.json()["detail"]
    wrong = api.post(f"{url}/done", json={"link": "https://example.com/x"})
    assert wrong.status_code == 409 and "isn't a Hacker News" in wrong.json()["detail"]

    ok = api.post(f"{url}/done", json={"link": "https://news.ycombinator.com/item?id=99", "numbers": {"points": 3}})
    assert ok.status_code == 200 and ok.json()["status"] == "DONE" and ok.json()["result"]["numbers"] == {"points": 3}
    assert api.post(f"{url}/done", json={"link": "https://news.ycombinator.com/item?id=99"}).status_code == 409

    reopened = api.post(f"{url}/reopen")
    assert reopened.status_code == 200 and reopened.json()["status"] == "OPEN" and reopened.json()["result"] is None
    skipped = api.post(f"{url}/skip", json={"note": "접음"})
    assert skipped.status_code == 200 and skipped.json()["status"] == "SKIPPED"


def test_the_api_refuses_in_the_owners_language_and_the_acting_owner_is_logged(s, project, api):
    task = open_task(s, project)
    assert api.post(f"/api/owner-tasks/{task.id}/skip", json={}).status_code == 409
    gone = api.get("/api/owner-tasks/99999999")
    assert gone.status_code == 404 and gone.json()["detail"] == "OT-99999999 not found."
    with owner_speaks("ko"):
        assert api.get("/api/owner-tasks/99999999").json()["detail"] == "OT-99999999를 찾지 못했어요."
    assert api.get("/api/owner-tasks", params={"status": "WRONG"}).status_code == 400
    api.post(f"/api/owner-tasks/{task.id}/skip", json={"note": "접음"})
    (ev,) = [e for e in events("owner_task", task.id) if e.verb == "owner_task_skipped"]
    assert ev.actor == "owner" and ev.business_id == project.business_id


def test_the_console_listens_for_owner_tasks():
    assert "owner_tasks" in live._WATCHED
    assert live._describe(OwnerTask(id=3, status="OPEN", title="t", kind="post"))["table"] == "owner_tasks"


def test_an_unknown_status_in_the_list_is_a_bad_request_not_a_conflict(api):
    r = api.get("/api/owner-tasks", params={"status": "DOING"})
    assert r.status_code == 400 and "Unknown status" in r.json()["detail"] and "DOING" not in r.json()["detail"]


@pytest.mark.parametrize("path,body", [
    ("done", {"link": 5}),
    ("done", {"link": "https://news.ycombinator.com/item?id=1", "extra": "SECRET-INPUT"}),
    ("done", {"numbers": "SECRET-INPUT"}),
    ("done", ["SECRET-INPUT"]),
    ("skip", {"note": ["SECRET-INPUT"]}),
    ("skip", "SECRET-INPUT"),
])
def test_a_malformed_body_is_refused_in_the_owners_language_without_repeating_what_was_sent(s, project, api, path,
                                                                                            body):
    task = open_task(s, project)
    with owner_speaks("ko"):
        r = api.post(f"/api/owner-tasks/{task.id}/{path}", json=body)
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str) and "SECRET-INPUT" not in r.text and "input" not in r.text
    assert any("가" <= ch <= "힣" for ch in r.json()["detail"])


def test_an_owner_reopens_a_skipped_task_through_the_api(s, project, api):
    task = open_task(s, project)
    url = f"/api/owner-tasks/{task.id}"
    assert api.post(f"{url}/skip", json={"note": "접음"}).status_code == 200
    again = api.post(f"{url}/reopen")
    assert again.status_code == 200 and again.json()["status"] == "OPEN" and again.json()["result"] is None


def test_a_done_without_a_body_is_a_refusal_about_the_link_not_a_framework_error(s, project, api):
    task = open_task(s, project)
    r = api.post(f"/api/owner-tasks/{task.id}/done")
    assert r.status_code == 409 and "link" in r.json()["detail"]
