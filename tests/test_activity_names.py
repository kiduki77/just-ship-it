"""The log names the record each change is about. Rows stored without a title (older ones, or written by code that kept
none) read "Task: now Failed" and "Decision: now Approved" on the Activity page; the server fills the title in from the
record when it serves them."""
from justshipit.db.models import ActivityEvent, Task
from justshipit.services import activity, requests
from owner_task_kit import draft
from ask_kit import by_project_manager


def untitled(s, entity, entity_id, project_id, **data):
    ev = ActivityEvent(actor="system", verb="status", entity=entity, entity_id=entity_id, project_id=project_id,
                       summary="x", data={"field": "status", "to": "FAILED", **data})
    s.add(ev)
    s.flush()
    return ev


def test_a_status_change_stored_without_a_title_is_served_with_its_tasks_title(s, project):
    task = Task(project_id=project.id, key="a", title="Interview five people", objective="o", owner="research", acceptance_criteria=["c"],
                status="FAILED")
    s.add(task)
    s.flush()
    ev = untitled(s, "task", task.id, project.id, ref=f"T-{task.id}")

    assert activity.named(s, [ev])[0]["title"] == "Interview five people"
    assert "title" not in ev.data, "the stored row is not changed"


def test_a_decision_and_a_build_are_named_too_and_a_stored_title_wins(s, project):
    req = requests.create(s, draft(action_kind="internal.other", title="Send the survey"), from_agent="project_manager",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    named = untitled(s, "request", req.id, project.id)
    kept = untitled(s, "request", req.id, project.id, title="As it was written")

    out = activity.named(s, [named, kept])

    assert [d["title"] for d in out] == ["Send the survey", "As it was written"]


def test_a_record_that_is_gone_stays_unnamed(s, project):
    assert "title" not in activity.named(s, [untitled(s, "task", 99999999, project.id)])[0]
