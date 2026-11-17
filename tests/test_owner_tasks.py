"""Owner tasks: an approval of an action nobody can carry out but the owner becomes a task, with a card the registry
fills, and a task moves only along the transition table."""
import re

import pytest
from justshipit.contracts.post_card import ExpectedQuestion, PostCardDraft
from justshipit.contracts.requests import (
    AgentRequestDraft,
    HumanDecision,
    HumanResponse,
    PolicyMode,
    RequestStatus,
    RequestType,
)
from justshipit.contracts.schema import strict_json_schema
from justshipit.db.models import AppSetting, OwnerTask
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import app_settings, channels, owner_tasks, policy, requests
from owner_task_kit import approve, as_stored_before_the_rules_check, draft, events, open_task, post_card, tasks_of, what
from sqlalchemy import delete, func, select
from ask_kit import by_project_manager


@pytest.fixture(autouse=True)
def _clean_settings():
    yield
    with session_scope() as s:
        s.execute(delete(AppSetting))



# ------------------------------------------------------------------ creation

def test_approving_a_request_nobody_but_the_owner_can_carry_out_makes_one_task(s, project):
    req = requests.create(s, draft(), from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    assert tasks_of(req.id) == [], "a pending request is not yet a task"
    approve(s, req)
    s.commit()

    (task,) = tasks_of(req.id)
    assert (task.kind, task.status, task.title) == ("post", "OPEN", "Show HN 게시")
    assert task.project_id == project.id and task.business_id == project.business_id
    assert task.result is None and task.done_at is None
    created = [e for e in events("owner_task", task.id) if e.verb == "owner_task_created"]
    assert len(created) == 1 and created[0].data["request"] == f"REQ-{req.id}"


def test_the_task_is_made_once_however_often_it_is_asked_for(s, project):
    req = requests.create(s, draft(), from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    approve(s, req)
    again = owner_tasks.create_for_request(s, req)
    s.commit()

    (task,) = tasks_of(req.id)
    assert again.id == task.id
    with session_scope() as s2:
        assert s2.scalar(select(func.count()).select_from(OwnerTask).where(OwnerTask.request_id == req.id)) == 1


@pytest.mark.parametrize("action,kind", [
    ("external.social_post", "post"), ("external.landing_publish", "publish"), ("dev.deploy_production", "publish"),
    ("budget.spend", "spend"), ("external.ads_run", "spend"), ("data.purchase", "spend"), ("external.other", "other"),
    ("external.contract_sign", "other"),
])
def test_each_action_a_person_carries_out_has_a_kind(s, project, action, kind):
    req = requests.create(s, draft(action_kind=action, title=f"{action} 요청"), from_agent="project_manager",
                          project_id=project.id, system=True, origin=by_project_manager(s, project.id))
    approve(s, req, confirm_text=f"REQ-{req.id}")
    s.commit()
    assert [t.kind for t in tasks_of(req.id)] == [kind]


@pytest.mark.parametrize("action", ["external.email_send", "external.calendar_create", "crm.contact_upsert",
                                    "internal.other", "dev.spec_approve", None])
def test_an_action_with_a_connector_or_no_person_to_do_it_makes_no_task(s, project, action):
    req = requests.create(s, draft(action_kind=action, title="다른 일"), from_agent="project_manager", project_id=project.id,
                          origin=by_project_manager(s, project.id))
    approve(s, req)
    s.commit()
    assert tasks_of(req.id) == []


def test_a_rejected_or_deferred_request_makes_no_task(s, project):
    for decision in (HumanDecision.REJECT, HumanDecision.DEFER):
        req = requests.create(s, draft(title=f"게시 {decision}", post_card=post_card(what=what(facts=[f"사실 {decision}"]))),
                              from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
        requests.respond(s, req.id, HumanResponse(decision=decision))
        s.commit()
        assert tasks_of(req.id) == []


def test_an_approval_the_server_makes_alone_makes_the_task_too(s, project):
    app_settings.save(s, "policy", {"overrides": {"external.social_post": "auto"}}, actor="test")
    s.commit()
    req = requests.create(s, draft(type=RequestType.CLARIFICATION), from_agent="marketing", project_id=project.id,
                          origin=by_project_manager(s, project.id))
    s.commit()
    assert req.status == RequestStatus.AUTO_APPROVED.value and req.response is None
    assert [t.status for t in tasks_of(req.id)] == ["OPEN"]


def test_an_approval_the_project_manager_makes_under_a_door_rule_makes_the_task_too(s, project, monkeypatch):
    rule = {"id": "d-1", "action": "external.social_post", "decider": "project_manager", "note": "", "source": "brief:v1:l-1"}
    monkeypatch.setattr(policy, "decide", lambda *a, **k: policy.Verdict(PolicyMode.AUTO, rule=rule))
    req = requests.create(s, draft(), from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    assert req.status == RequestStatus.AUTO_APPROVED.value and req.response["responded_by"] == requests.PROJECT_MANAGER_DECIDED
    assert [t.status for t in tasks_of(req.id)] == ["OPEN"]


def test_the_owner_pinning_a_door_rule_never_lets_the_project_manager_skip_the_owner_for_a_post(s, project):
    from justshipit.db.models import Business
    s.get(Business, project.business_id).door_policy = [
        {"id": "d-1", "action": "external.social_post", "decider": "project_manager", "source": "brief:v1:l-1"}]
    s.commit()
    req = requests.create(s, draft(), from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    assert req.status == RequestStatus.PENDING.value and tasks_of(req.id) == []


def test_a_request_outside_any_business_still_makes_a_task(s):
    req = requests.create(s, draft(), from_agent="project_manager", project_id=None, origin=by_project_manager(s, None))
    approve(s, req)
    s.commit()
    (task,) = tasks_of(req.id)
    assert task.business_id is None and task.project_id is None


def test_the_task_of_a_request_without_a_post_card_carries_what_the_request_said(s, project):
    task = open_task(s, project, summary="랜딩 페이지를 공개합니다", rationale="대기 명단이 필요합니다",
                     action_kind="external.other", title="랜딩 공개")
    assert task.card["what"] == "랜딩 페이지를 공개합니다" and task.card["why"] == "대기 명단이 필요합니다"
    assert "channel" not in task.card


# ------------------------------------------------------------------ the post card

def test_the_registry_fills_the_rules_and_the_mode_and_the_card_reaches_the_task(s, project):
    task = open_task(s, project, post_card=post_card("hashnode", what=what(title="제목 초안", body="본문 초안", facts=[],
                                                                            expected_questions=[], checklist=[])))
    c = task.card
    assert (c["channel"], c["mode"], c["channel_name"]) == ("hashnode", "draft", "Hashnode")
    assert c["rules_summary"] == channels.get("hashnode").rules["en"] and "take responsibility" in c["rules_summary"]
    assert c["rules_basis"] == "verified" and c["rules_source"] == "https://hashnode.com/code-of-conduct"
    assert c["when"]["window"] and c["when"]["reason"] and c["why"].startswith("도전 BET-1")
    assert c["what"] == {"title": "제목 초안", "body": "본문 초안", "checklist": [owner_tasks.fixed_check("en")]}
    assert c["where_url"] == "https://hashnode.com/submit"


def test_an_outline_channel_gets_no_sentence_to_post_and_the_server_says_it_dropped_them(s, project):
    req = requests.create(s, draft(post_card=post_card("hacker_news", what=what(title="Show HN: 내 도구",
                                                                            body="저는 이 도구를 만들었습니다."))),
                          from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    stored = req.action_params["post_card"]
    assert stored["what"].keys() == {"facts", "expected_questions", "checklist"}
    assert stored["stripped"] == ["title", "body"]
    assert "Show HN: 내 도구" not in repr(stored) and "만들었습니다" not in repr(stored)
    dropped = [e for e in events("request", req.id) if e.verb == "post_card_stripped"]
    assert len(dropped) == 1 and dropped[0].data["dropped"] == ["title", "body"]

    approve(s, req)
    s.commit()
    (task,) = tasks_of(req.id)
    assert task.card["mode"] == "outline" and task.card["what"]["facts"] == ["MIT 라이선스", "설치 한 줄"]
    assert task.card["what"]["expected_questions"][0]["q"] == "왜 직접 만들었나요?"
    assert task.card["stripped"] == ["title", "body"]


def test_an_outline_card_that_sent_nothing_but_prose_is_refused(s, project):
    empty = what(title="t", body="b", facts=[], expected_questions=[], checklist=[])
    req = requests.create(s, draft(post_card=post_card("hacker_news", what=empty)), from_agent="marketing",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    assert req.status == RequestStatus.DENIED_BY_POLICY.value and "takes no sentences" in req.response["note"]


def test_a_draft_channel_needs_a_title_and_a_body(s, project):
    req = requests.create(s, draft(post_card=post_card("hashnode", what=what(title="제목만"))), from_agent="marketing",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    assert req.status == RequestStatus.DENIED_BY_POLICY.value and "needs a draft title and body" in req.response["note"]


def test_the_model_cannot_choose_the_rules_or_the_mode(s, project):
    assert "rules_summary" not in PostCardDraft.model_fields and "mode" not in PostCardDraft.model_fields
    with pytest.raises(ValueError):
        post_card("not_a_channel")


@pytest.mark.parametrize("where", ["facts", "checklist", "why", "question", "title", "body"])
def test_a_card_that_begs_for_votes_stars_or_reviews_is_refused_with_the_reason(s, project, where):
    ask = "많이 업보트해 주세요"
    w = what(title="제목", body="본문", facts=["MIT"], expected_questions=[], checklist=["링크 확인"])
    if where == "facts":
        w = what(facts=[ask])
    elif where == "checklist":
        w = what(checklist=[ask])
    elif where == "question":
        w = what(expected_questions=[ExpectedQuestion(q="질문", talking_points=[ask])])
    elif where in ("title", "body"):
        w = what(title=ask if where == "title" else "제목", body=ask if where == "body" else "본문", facts=[],
                 expected_questions=[], checklist=[])
    card = post_card("hashnode" if where in ("title", "body") else "hacker_news", what=w,
                     why=ask if where == "why" else "도전의 가설")
    req = requests.create(s, draft(post_card=card), from_agent="marketing", project_id=project.id,
                          origin=by_project_manager(s, project.id))
    s.commit()

    assert req.status == RequestStatus.DENIED_BY_POLICY.value and req.policy_mode == "deny"
    assert msg("post.ask.votes", "en") in req.response["note"] and req.response["responded_by"] == "system"
    assert "post_card" not in req.action_params
    assert [e.verb for e in events("request", req.id) if e.verb == "post_card_refused"] == ["post_card_refused"]
    assert tasks_of(req.id) == []


def test_a_refused_card_cannot_be_approved_into_a_task(s, project):
    req = requests.create(s, draft(post_card=post_card(what=what(facts=["star를 눌러 주세요"]))), from_agent="marketing",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    with pytest.raises(requests.ResponseRejected):
        approve(s, req)
    assert tasks_of(req.id) == []


def test_a_goal_that_mentions_stars_is_not_an_ask(s, project):
    req = requests.create(s, draft(post_card=post_card(why="이 도전의 목표는 GitHub star 100개입니다")),
                          from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    assert req.status == RequestStatus.PENDING.value


def test_a_link_that_is_not_http_is_refused_on_the_card(s, project):
    req = requests.create(s, draft(post_card=post_card(where_url="javascript:alert(1)")), from_agent="marketing",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    assert req.status == RequestStatus.DENIED_BY_POLICY.value and "http" in req.response["note"]


def test_a_post_card_on_an_action_that_is_not_a_post_is_ignored(s, project):
    req = requests.create(s, draft(action_kind="internal.other", post_card=post_card()), from_agent="marketing",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    assert "post_card" not in req.action_params and req.status == RequestStatus.PENDING.value


def test_two_posts_with_alike_titles_are_both_kept(s, project):
    a = requests.create(s, draft(title="Show HN 게시 준비", post_card=post_card("hacker_news")), from_agent="marketing",
                        project_id=project.id, origin=by_project_manager(s, project.id))
    b = requests.create(s, draft(title="Show HN 게시 준비", post_card=post_card("linkedin")), from_agent="marketing",
                        project_id=project.id, origin=by_project_manager(s, project.id))
    assert a.id != b.id


def test_the_post_card_the_agents_fill_is_a_strict_schema_all_the_way_down():
    def check(node, path="post_card"):
        if isinstance(node, list):
            for i, x in enumerate(node):
                check(x, f"{path}[{i}]")
        elif isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                assert node["additionalProperties"] is False, path
                assert node["required"] == list(node["properties"]), path
            for k, v in node.items():
                check(v, f"{path}.{k}")

    schema = strict_json_schema(AgentRequestDraft)
    assert "post_card" in schema["required"]
    card = schema["properties"]["post_card"]["anyOf"][0]
    check(card)
    assert set(card["properties"]) == {"channel", "where_url", "when", "why", "what"}
    assert set(card["properties"]["what"]["properties"]) == {"title", "body", "facts", "expected_questions", "checklist"}
    assert "show_hn" in card["properties"]["channel"]["enum"] and "other" in card["properties"]["channel"]["enum"]


def test_what_approving_does_is_said_in_plain_words(s, project):
    req = requests.create(s, draft(), from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    assert msg("request.effect.owner_task.post_outline", "en") in requests.on_approve(s, req, "en")


# ------------------------------------------------------------------ moving a task

def test_the_transition_table_has_exactly_the_four_edges_and_only_the_owner_takes_them():
    assert set(owner_tasks.TRANSITIONS) == {("OPEN", "DONE"), ("OPEN", "SKIPPED"), ("DONE", "OPEN"), ("SKIPPED", "OPEN")}
    assert all(who == {"owner"} for who in owner_tasks.TRANSITIONS.values())


def test_a_post_needs_its_link_or_a_note_before_it_is_done(s, project):
    task = open_task(s, project)
    for link, note in ((None, None), ("", "  "), ("   ", "")):
        with pytest.raises(owner_tasks.OwnerTaskError, match="link"):
            owner_tasks.done(s, task.id, link=link, note=note)
    s.rollback()
    with session_scope() as s2:
        assert s2.get(OwnerTask, task.id).status == "OPEN"


def test_a_post_made_from_a_phone_is_done_with_a_note_and_no_link(s, project):
    """박정숙 posted from her phone and could not fetch the link; OT-34 refused to finish ("올린 글의 링크 · 꼭
    필요해요"). The note is the record, and a link still counts when she has one."""
    task = open_task(s, project)
    assert owner_tasks.view(task, "ko")["result_needs"] == "link_or_note"
    done = owner_tasks.done(s, task.id, note="폰으로 올렸어요")
    assert done.status == "DONE" and done.result == {"link": None, "numbers": {}, "note": "폰으로 올렸어요"}
    assert "폰으로 올렸어요" in owner_tasks.result_text(done.result, "ko")


def test_done_keeps_the_link_the_numbers_and_the_note_and_says_so_in_the_log(s, project):
    task = open_task(s, project)
    done = owner_tasks.done(s, task.id, link="https://news.ycombinator.com/item?id=42",
                            numbers={"points": 31, "comments": 12.0}, note="오전 10시에 올림")
    s.commit()
    assert done.status == "DONE" and done.done_at is not None
    assert done.result == {"link": "https://news.ycombinator.com/item?id=42",
                           "numbers": {"points": 31, "comments": 12.0}, "note": "오전 10시에 올림"}
    (ev,) = [e for e in events("owner_task", task.id) if e.verb == "owner_task_done"]
    assert ev.data["result"]["link"].endswith("id=42") and ev.actor == "owner"


@pytest.mark.parametrize("link", ["ftp://news.ycombinator.com/x", "news.ycombinator.com/item?id=1",
                                  "https://example.com/item?id=1", "javascript:alert(1)"])
def test_the_link_must_be_http_and_on_the_channels_domain(s, project, link):
    task = open_task(s, project)
    with pytest.raises(owner_tasks.OwnerTaskError):
        owner_tasks.done(s, task.id, link=link)


def test_the_link_is_checked_against_the_channel_of_that_task(s, project):
    task = as_stored_before_the_rules_check(open_task(s, project), "reddit")
    with pytest.raises(owner_tasks.OwnerTaskError, match="Reddit"):
        owner_tasks.done(s, task.id, link="https://news.ycombinator.com/item?id=1")
    assert owner_tasks.done(s, task.id, link="https://www.reddit.com/r/opensource/comments/x").status == "DONE"


def test_a_task_that_is_not_a_post_can_be_done_without_a_link(s, project):
    task = open_task(s, project, action_kind="external.landing_publish", title="랜딩 공개")
    assert task.kind == "publish"
    assert owner_tasks.done(s, task.id, note="공개함").result == {"link": None, "numbers": {}, "note": "공개함"}


@pytest.mark.parametrize("numbers", [{"": 1}, {"점수": "많음"}, {"점수": True}, {"점수": float("nan")},
                                     {f"k{i}": i for i in range(21)}])
def test_numbers_must_be_named_and_numeric(s, project, numbers):
    task = open_task(s, project, action_kind="external.landing_publish", title="랜딩 공개")
    with pytest.raises(owner_tasks.OwnerTaskError):
        owner_tasks.done(s, task.id, numbers=numbers)


def test_a_task_is_done_once_and_skipped_only_while_open(s, project):
    task = open_task(s, project)
    owner_tasks.done(s, task.id, link="https://news.ycombinator.com/item?id=1")
    with pytest.raises(owner_tasks.OwnerTaskError, match="can't change to"):
        owner_tasks.done(s, task.id, link="https://news.ycombinator.com/item?id=2")
    with pytest.raises(owner_tasks.OwnerTaskError, match="can't change to"):
        owner_tasks.skip(s, task.id, note="그만")


def test_skipping_needs_a_reason_and_the_owner_can_reopen_a_skipped_task(s, project):
    task = open_task(s, project)
    with pytest.raises(owner_tasks.OwnerTaskError, match="why you're skipping"):
        owner_tasks.skip(s, task.id, note="  ")
    skipped = owner_tasks.skip(s, task.id, note="이번 주기에는 하지 않기로 함")
    assert skipped.status == "SKIPPED" and skipped.done_at is not None
    assert skipped.result == {"link": None, "numbers": {}, "note": "이번 주기에는 하지 않기로 함"}
    with pytest.raises(owner_tasks.OwnerTaskError):
        owner_tasks.done(s, task.id, link="https://news.ycombinator.com/item?id=1")
    reopened = owner_tasks.reopen(s, task.id)
    assert (reopened.status, reopened.result, reopened.done_at) == ("OPEN", None, None)


def test_reopening_a_done_task_clears_the_result_so_nothing_quotes_it_and_the_log_keeps_it(s, project):
    task = open_task(s, project)
    owner_tasks.done(s, task.id, link="https://news.ycombinator.com/item?id=7", numbers={"points": 5})
    reopened = owner_tasks.reopen(s, task.id)
    s.commit()
    assert (reopened.status, reopened.result, reopened.done_at) == ("OPEN", None, None)
    (ev,) = [e for e in events("owner_task", task.id) if e.verb == "owner_task_reopened"]
    assert ev.data["earlier_result"]["numbers"] == {"points": 5}
    assert owner_tasks.done(s, task.id, link="https://news.ycombinator.com/item?id=8").status == "DONE"


def test_an_open_task_cannot_be_reopened(s, project):
    task = open_task(s, project)
    with pytest.raises(owner_tasks.OwnerTaskError, match="can't change to"):
        owner_tasks.reopen(s, task.id)


@pytest.mark.parametrize("act", ["done", "skip", "reopen"])
def test_only_the_owner_moves_a_task(s, project, act):
    task = open_task(s, project)
    if act == "reopen":
        owner_tasks.done(s, task.id, link="https://news.ycombinator.com/item?id=1")
    call = {"done": lambda: owner_tasks.done(s, task.id, link="https://news.ycombinator.com/item?id=1", by="project_manager"),
            "skip": lambda: owner_tasks.skip(s, task.id, note="n", by="marketing"),
            "reopen": lambda: owner_tasks.reopen(s, task.id, by="project_manager")}[act]
    with pytest.raises(owner_tasks.OwnerTaskError, match="Only you"):
        call()


def test_a_task_that_does_not_exist_is_not_found(s):
    with pytest.raises(owner_tasks.OwnerTaskNotFound):
        owner_tasks.get(s, 99999999, lang="en")


@pytest.mark.parametrize("link", ["https://news.ycombinator.com/submit", "https://news.ycombinator.com/newest",
                                  "https://news.ycombinator.com/item?id=abc", "https://news.ycombinator.com/user?id=me"])
def test_a_hacker_news_post_link_must_be_the_posts_own_page(s, project, link):
    """A submit page was kept as the post's link and reactions were collected from it (eval F3)."""
    task = open_task(s, project)
    with pytest.raises(owner_tasks.OwnerTaskError, match=r"item\?id="):
        owner_tasks.done(s, task.id, link=link)
    assert owner_tasks.done(s, task.id, link="https://news.ycombinator.com/item?id=41234567").status == "DONE"


@pytest.mark.parametrize("link", ["https://www.reddit.com/r/opensource/submit", "https://www.reddit.com/r/opensource/",
                                  "https://www.reddit.com/user/me"])
def test_a_reddit_post_link_must_be_its_comments_page(s, project, link):
    task = as_stored_before_the_rules_check(open_task(s, project), "reddit")
    with pytest.raises(owner_tasks.OwnerTaskError, match="comments"):
        owner_tasks.done(s, task.id, link=link)
    assert owner_tasks.done(s, task.id, link="https://old.reddit.com/r/opensource/comments/abc123/my_tool/?utm=1").status == "DONE"


def test_the_post_form_reads_the_shape_the_server_checks(s, project):
    task = open_task(s, project)
    shape = owner_tasks.view(task, "en")["post_link"]
    assert shape["example"].startswith("news.ycombinator.com/item")
    assert [re.fullmatch(p, "news.ycombinator.com/item?id=1", re.IGNORECASE) is not None for p in shape["patterns"]] == [True]
    other = open_task(s, project, post_card=post_card("hashnode", where_url=None, what=what(title="t", body="b")))
    assert owner_tasks.view(other, "en")["post_link"] is None, "a channel whose post address is unknown takes any page"
