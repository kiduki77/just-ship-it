"""A check only the owner can make by hand (a test on their own device or account). Maya approved "a 15-minute Slack
copy-paste test on your own workspace" (REQ-58) and got no to-do, no place to write what she found, and a spec card
that never said the test was still open. Approving one now makes a to-do that takes the result, the result reaches the
plan and the spec writer whole, and a spec card waiting in the same project says which checks are still open."""
import pytest
from justshipit.api.app import app
from justshipit.contracts.planning import RealignOut
from justshipit.contracts.requests import RequestType, RiskLevel
from justshipit.db.models import Job, Project
from justshipit.db.session import session_scope
from justshipit.engineering import owner_trace
from justshipit.i18n import msg
from justshipit.orchestration import realign
from justshipit.services import owner_tasks, requests
from fastapi.testclient import TestClient
from lang_kit import owner_speaks
from owner_task_kit import approve, draft, tasks_of
from sqlalchemy import select
from ask_kit import by_project_manager

FOUND = ("Pasted the 12 test messages from #general into the parser. 9 came out right. The 3 that broke all had a "
         "threaded reply quoted inside them, and Slack adds the 'replied to a thread' line on copy. ") * 40


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def check_draft(title="Slack copy-paste test", **kw):
    return draft(type=RequestType.RESOURCE, title=title, summary="Copy 10 messages from your own Slack and paste them",
                 rationale="The parser is a guess until it reads real Slack text", action_kind="owner.check",
                 post_card=None, risk_level=RiskLevel.LOW, **kw)


def open_check(s, project, **kw):
    req = requests.create(s, check_draft(**kw), from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))
    approve(s, req)
    s.commit()
    return req, tasks_of(req.id)


def realign_jobs(project_id):
    with session_scope() as s2:
        return list(s2.scalars(select(Job).where(Job.kind == "realign_project",
                                                 Job.payload["project_id"].as_integer() == project_id)))


def test_approving_a_hands_on_check_makes_one_check_to_do_linked_to_it(s, project, api):
    req = requests.create(s, check_draft(), from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    assert api.get(f"/api/requests/{req.id}").json()["report"]["on_approve"].startswith(
        msg("request.effect.owner_task.check", "en")), "the card says approving adds a to-do, not that it runs nothing"

    approve(s, req)
    owner_tasks.create_for_request(s, req)
    s.commit()

    (task,) = tasks_of(req.id)
    assert (task.kind, task.status, task.request_id) == ("check", "OPEN", req.id)


def test_a_check_is_not_done_until_it_says_what_the_owner_found(s, project, api):
    _, (task,) = open_check(s, project)
    url = f"/api/owner-tasks/{task.id}"
    shown = api.get(url).json()
    assert (shown["result_needs"], shown["note_max"]) == ("note", 20_000)

    for empty in ({}, {"note": "   "}, {"link": "https://example.com/screenshot.png"}):
        refused = api.post(f"{url}/done", json=empty)
        assert refused.status_code == 409 and refused.json()["detail"] == msg("owner_task.error.note_needed", "en")
    assert api.post(f"{url}/done", json={"note": "x" * 20_001}).status_code == 409

    ok = api.post(f"{url}/done", json={"note": FOUND})
    assert ok.status_code == 200 and ok.json()["result"]["note"] == FOUND.strip()


def test_a_post_still_takes_a_short_note_and_needs_its_link_or_a_note(s, project, api):
    req = requests.create(s, draft(), from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    approve(s, req)
    s.commit()
    (post,) = tasks_of(req.id)
    shown = api.get(f"/api/owner-tasks/{post.id}").json()
    assert (shown["result_needs"], shown["note_max"]) == ("link_or_note", 2000)
    assert api.post(f"/api/owner-tasks/{post.id}/done", json={
        "link": "https://news.ycombinator.com/item?id=1", "note": "x" * 2001}).status_code == 409


def test_what_the_owner_found_reaches_the_spec_writer_whole_and_the_brief_as_data(s, project):
    project.status = "RUNNING"
    _, (task,) = open_check(s, project)
    owner_tasks.done(s, task.id, note=FOUND)
    s.commit()

    ref = f"OT-{task.id}"
    for lang in ("en", "ko"):
        sources = {x.ref: x for x in owner_trace.owner_sources(s, s.get(Project, project.id), lang)}
        assert ref in sources and FOUND.strip() in sources[ref].text, "the owner's words whole, under a language-free id"
        assert sources[ref].origin == msg("build.trace.from_check", lang), "the spec card says where it came from"
    with owner_speaks("ko"):
        section = owner_tasks.status_section(s, project.id)
    start = section.index(f"<<<데이터 시작: {ref}")
    assert FOUND[:150] in section[start:section.index(f"<<<데이터 끝: {ref}")], "the result sits inside its fence"
    jobs = realign_jobs(project.id)
    assert len(jobs) == 1 and jobs[0].payload["owner_task"] == task.id


def test_realigning_after_a_check_reads_the_result_whole(s, project, monkeypatch):
    project.status = "RUNNING"
    _, (task,) = open_check(s, project)
    owner_tasks.done(s, task.id, note=FOUND)
    s.commit()
    seen = {}
    monkeypatch.setattr(realign, "run_agent", lambda agent, purpose, prompt, *a, **k: seen.update(prompt=prompt)
                        or RealignOut(assessment="the parser holds", goal_interpretation="", out_of_scope_additions=[],
                                      cancel_task_ids=[], revise_tasks=[], new_tasks=[], withdraw_requests=[]))

    record = realign.realign(project.id, owner_task=task.id)

    assert record["owner_task"] == f"OT-{task.id}"
    trigger = seen["prompt"].split("## Current tasks")[0]
    assert f"<<<데이터 시작: OT-{task.id}" in trigger and FOUND.strip() in trigger


def test_a_spec_card_says_which_checks_are_still_open_until_they_are_done(s, project, api):
    spec = requests.create(s, draft(type=RequestType.APPROVAL, action_kind="dev.spec_approve", title="Spec: quote bot",
                                    post_card=None), from_agent="engineer", project_id=project.id, system=True,
                           action_params={"spec_id": 4242}, dedupe=False, origin=by_project_manager(s, project.id))
    pending = requests.create(s, check_draft(title="Paste test"), from_agent="project_manager", project_id=project.id,
                              origin=by_project_manager(s, project.id))
    s.commit()

    def says():
        return api.get(f"/api/requests/{spec.id}").json()["report"]["on_approve"]

    assert "'Paste test'" in says(), "a check still waiting for approval is open"
    approve(s, pending)
    s.commit()
    (task,) = tasks_of(pending.id)
    assert "'Paste test'" in says(), "an approved check whose to-do is open is still open"
    with owner_speaks("ko"):
        assert "'Paste test'" in says()

    owner_tasks.done(s, task.id, note="9 of 12 parsed")
    s.commit()
    assert "Paste test" not in says() and says().startswith(msg("request.effect.spec_approved", "en"))
