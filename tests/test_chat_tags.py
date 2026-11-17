"""What a conversation is about: a merge card is about its build, so every merge card of one build is one conversation,
and each kind of subject is answered by the role that owns it."""
from __future__ import annotations

import pytest

from justshipit.db.models import AgentRequest, DevJob, OwnerTask, Spec, Task
from justshipit.services import chat, chat_tags


def _build(s, project) -> DevJob:
    spec = Spec(spec_key=f"SPEC-chat-{project.id}", project_id=project.id, title="uxscope", registry_path="specs/x",
                handoff={"target": {"repository": "uxscope"}})
    s.add(spec)
    s.flush()
    job = DevJob(spec_id=spec.id, status="DELIVERED", branch="ai/projec-0001-a1", steps=[], result={})
    s.add(job)
    s.flush()
    return job


def _merge_card(s, project, job: DevJob) -> AgentRequest:
    req = AgentRequest(project_id=project.id, from_agent="product", type="APPROVAL", title="Merge", summary="s",
                       rationale="r", action_kind="dev.merge", action_params={"dev_job_id": job.id}, policy_mode="strong")
    s.add(req)
    s.flush()
    return req


def test_every_merge_card_of_a_build_is_one_conversation_about_the_build(s, project, own_jobs_settled):
    job = _build(s, project)
    first, second = _merge_card(s, project, job), _merge_card(s, project, job)

    a = chat_tags.resolve(s, "request", str(first.id), None)
    b = chat_tags.resolve(s, "request", str(second.id), None)

    assert (a.kind, a.ref) == (b.kind, b.ref) == ("dev_job", str(job.id))
    assert a.scope == chat_tags.Scope(project.id, project.business_id)
    sent = chat.send(s, subject=a, to_agent=None, body="npm install fails", client_id=None, lang="en")
    assert chat.find_thread(s, b).id == sent["thread_id"]
    assert chat_tags.owner_agent(s, a) == "engineer"
    facts = chat_tags.facts(s, a.kind, a.ref)
    assert {"k": "branch", "v": "ai/projec-0001-a1"} in facts and {"k": "product", "v": "uxscope"} in facts
    assert {"k": "merge", "ref": f"REQ-{second.id}"} in facts, "the merge card waiting now"
    assert chat_tags.work(s, a) == ("dev_fix",)
    s.rollback()


def test_a_card_asking_what_to_fix_is_about_its_build_too(s, project):
    from justshipit.contracts.handoff import FIX_QUESTION_KEY
    job = _build(s, project)
    req = AgentRequest(project_id=project.id, from_agent="product", type="QUESTION", title="What to fix", summary="s",
                       rationale="r", action_params={FIX_QUESTION_KEY: job.id}, policy_mode="ask")
    s.add(req)
    s.flush()

    sub = chat_tags.resolve(s, "request", str(req.id), None)

    assert (sub.kind, sub.ref) == ("dev_job", str(job.id))
    s.rollback()


def test_each_kind_is_answered_by_the_role_that_owns_it(s, project):
    job = _build(s, project)
    task = Task(project_id=project.id, key="k-tags", title="Survey", owner="research", objective="o")
    other = AgentRequest(project_id=project.id, from_agent="marketing", type="APPROVAL", title="Post", summary="s",
                         rationale="r", action_kind="external.social_post", policy_mode="ask")
    todo = OwnerTask(project_id=project.id, business_id=project.business_id, kind="post", title="Post it")
    s.add_all([task, other, todo])
    s.flush()
    spec = s.get(Spec, job.spec_id)

    def who(kind, ref, business_id=None):
        return chat_tags.owner_agent(s, chat_tags.resolve(s, kind, ref, business_id))

    assert who("dev_job", str(job.id)) == "engineer"
    assert who("page", "engineering") == "engineer"
    assert who("spec", str(spec.id)) == "product"
    assert who("task", str(task.id)) == "research"
    assert who("request", str(other.id)) == "marketing"
    assert who("owner_task", str(todo.id)) == "project_manager"
    assert who("project", str(project.id)) == "project_manager"
    assert who("general", None) == "project_manager"
    assert who("page", "inbox") == "project_manager"
    s.rollback()


def test_a_page_is_placed_in_the_business_on_screen_and_general_in_none(s, project):
    page = chat_tags.resolve(s, "page", "engineering", project.business_id)
    general = chat_tags.resolve(s, "general", None, project.business_id)

    assert page.business_id == project.business_id and general.business_id is None
    with pytest.raises(chat_tags.UnknownSubject):
        chat_tags.resolve(s, "page", "nowhere", None)
    with pytest.raises(chat_tags.UnknownSubject):
        chat_tags.resolve(s, "request", "999999999", None)


@pytest.mark.parametrize(("agent", "intent", "kind", "hop", "ok"), [
    ("engineer", "work", "dev_job", 1, True),
    ("marketing", "work", "dev_job", 1, False),
    ("engineer", "work", "project", 1, False),
    ("engineer", "escalate", "dev_job", 1, True),
    ("engineer", "escalate", "dev_job", 3, False),
    ("project_manager", "escalate", "general", 1, False),
    ("project_manager", "handoff", "general", 2, True),
    ("engineer", "handoff", "dev_job", 1, False),
    ("research", "decision", "task", 3, True),
    ("research", "answer", "task", 3, True),
])
def test_what_a_reply_may_cause(agent, intent, kind, hop, ok):
    assert chat_tags.allowed(agent, intent, kind, hop=hop, max_hops=3) is ok


def test_a_business_that_does_not_exist_places_no_conversation(s, project):
    from fastapi import HTTPException
    from justshipit.api import chat as chat_api

    with pytest.raises(chat_tags.UnknownSubject):
        chat_tags.resolve(s, "page", "home", 2_000_000_000)
    with pytest.raises(HTTPException) as refused:
        chat_api.send(chat_api.MessageIn(subject=chat_api.SubjectIn(kind="page", ref="home", business_id=2_000_000_000),
                                         body="hello"))
    assert refused.value.status_code == 404


def test_a_request_with_no_project_is_one_conversation_whatever_business_the_screen_shows(s, project):
    from justshipit.db.models import Business
    first, second = Business(slug="tags-a", name="[TEST] a", one_liner="x"), Business(slug="tags-b", name="[TEST] b",
                                                                                       one_liner="x")
    s.add_all([first, second])
    req = AgentRequest(project_id=None, from_agent="project_manager", type="QUESTION", title="q", summary="s", rationale="r",
                       policy_mode="approve")
    s.add(req)
    s.flush()

    a = chat_tags.resolve(s, "request", str(req.id), first.id)
    b = chat_tags.resolve(s, "request", str(req.id), second.id)

    assert a == b and a.business_id is None
    s.rollback()


def _filed_card(s, project, *, link=None, **kw) -> AgentRequest:
    from justshipit.db.models import RequestLink
    req = AgentRequest(project_id=project.id, from_agent="project_manager", type="QUESTION", title="What next?", summary="s",
                       rationale="r", policy_mode="approve", **kw)
    s.add(req)
    s.flush()
    if link is not None:
        kind, target, known = link
        s.add(RequestLink(request_id=req.id, rel="raised_by", target_kind=kind, target_id=target, revision=1,
                          source="agent", known=known))
        s.flush()
    return req


def test_a_request_names_its_project_and_the_step_that_filed_it(s, project):
    task = Task(project_id=project.id, key="k-filed", title="Survey the market", owner="research", objective="o")
    s.add(task)
    s.flush()
    req = _filed_card(s, project, link=("task", task.id, "recorded"))

    facts = chat_tags.facts(s, "request", str(req.id))

    assert facts == [
        {"k": "request", "ref": f"REQ-{req.id}", "v": "What next?"},
        {"k": "project", "ref": f"P-{project.id}", "v": project.name},
        {"k": "filed", "key": "path.step.task", "vars": {"title": "Survey the market"}, "known": "recorded"}]
    s.rollback()


def test_a_card_filed_in_team_chat_says_so(s, project):
    req = _filed_card(s, project, link=("chat_message", 7, "recorded"))

    assert chat_tags.facts(s, "request", str(req.id))[-1] == {
        "k": "filed", "key": "path.step.chat", "vars": {}, "known": "recorded"}
    s.rollback()


def test_a_step_the_records_do_not_hold_reads_as_not_recorded_never_guessed(s, project):
    bare = _filed_card(s, project)
    dangling = _filed_card(s, project, link=("task", 2_000_000_000, "recorded"))

    for req in (bare, dangling):
        assert chat_tags.facts(s, "request", str(req.id))[-1] == {"k": "filed", "known": "unknown"}
    s.rollback()


def test_a_card_that_belongs_to_no_project_has_no_project_chip(s):
    req = AgentRequest(project_id=None, from_agent="project_manager", type="QUESTION", title="q", summary="s", rationale="r",
                       policy_mode="approve")
    s.add(req)
    s.flush()

    assert [f["k"] for f in chat_tags.facts(s, "request", str(req.id))] == ["request", "filed"]
    s.rollback()


def test_the_dock_still_has_its_facts_when_the_card_context_cannot_be_read(s, project, monkeypatch):
    req = _filed_card(s, project)

    def broken(*a, **kw):
        raise RuntimeError("no graph")
    monkeypatch.setattr(chat_tags.work_path, "card_for", broken)

    assert chat_tags.facts(s, "request", str(req.id)) == [{"k": "request", "ref": f"REQ-{req.id}", "v": "What next?"}]
    s.rollback()


def test_reading_the_facts_of_a_card_changes_no_link(s, project):
    from justshipit.db.models import RequestLink
    from sqlalchemy import func, select
    req = _filed_card(s, project, link=("chat_message", 7, "recorded"))
    before = s.scalar(select(func.count()).select_from(RequestLink))

    chat_tags.facts(s, "request", str(req.id))
    chat_tags.resolve(s, "request", str(req.id), None)

    assert s.scalar(select(func.count()).select_from(RequestLink)) == before
    assert [chat_tags.allowed("project_manager", i, "request", hop=1, max_hops=3) for i in ("work", "escalate", "handoff")] == [
        False, False, True]
    s.rollback()
