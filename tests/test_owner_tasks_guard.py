"""The two ways around the post card an independent review found, and the limits that keep the rest honest: a post
with no card is refused, the channel is the server's to confirm, an outline card holds points and not paragraphs,
every card ends with the owner's own check, and what the prompts quote stays small."""
import pytest
from justshipit.contracts.post_card import ExpectedQuestion, PostCardDraft
from justshipit.contracts.requests import CardOption, DecisionCard, HumanDecision, HumanResponse, RequestStatus
from justshipit.db.models import ActivityEvent, Task
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.operations import jobs
from justshipit.orchestration import context as ctx
from justshipit.services import channels, launch_gate, owner_tasks, requests, tracking
from owner_task_kit import approve, draft, events, open_task, post_card, tasks_of, what
from sqlalchemy import select
from sqlalchemy.orm import object_session
from ask_kit import by_project_manager
from justshipit.contracts.origins import TaskOrigin

HN_PROSE = ("Show HN: Foo - I built Foo because I was tired of Y. It works by Z. Try it at foo.dev. "
            "Please upvote if you like it!")


def _approval(req) -> HumanResponse:
    return HumanResponse(decision=HumanDecision.APPROVE, confirm_text=f"REQ-{req.id}")


def _denied(req, rule: str, mentions: str = "") -> None:
    assert req.status == RequestStatus.DENIED_BY_POLICY.value and req.policy_mode == "deny"
    assert mentions in req.response["note"] and "post_card" not in req.action_params
    fired = [e for e in events("request", req.id) if e.verb == "post_card_refused"]
    assert [e.data["rule"] for e in fired] == [rule]
    assert tasks_of(req.id) == []
    with pytest.raises(requests.ResponseRejected):
        requests.respond(object_session(req), req.id, _approval(req))


def _create(s, project, **kw):
    req = requests.create(s, draft(**kw), from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    return req


# ------------------------------------------------------------------ a post with no card checks nothing

@pytest.mark.parametrize("kind", ["external.social_post", "external.landing_publish"])
def test_a_post_without_a_post_card_is_denied_however_ready_the_prose_in_its_summary(s, project, kind):
    prose = HN_PROSE.replace(" Please upvote if you like it!", "")
    req = _create(s, project, action_kind=kind, post_card=None, summary=prose)
    _denied(req, "post_card_missing", "post card")


@pytest.mark.parametrize("kind", ["external.social_post", "external.landing_publish"])
def test_a_post_without_a_post_card_still_has_its_prose_checked_for_asks(s, project, kind):
    req = _create(s, project, action_kind=kind, post_card=None, summary=HN_PROSE,
                  rationale="Don't forget to upvote us on HN")
    _denied(req, "forbidden_ask", msg("post.ask.votes", "en"))


def test_a_request_that_is_not_a_post_needs_no_post_card(s, project):
    req = _create(s, project, action_kind="external.other", post_card=None, title="변호사 상담 예약")
    assert req.status == RequestStatus.PENDING.value


def _card(**kw) -> DecisionCard:
    base = dict(headline="올릴까요?", owner_does="직접 올려요", why="보호자 채널이에요", metrics=[], recommendation="올립니다",
                reversible=False,
                options=[CardOption(label="올린다", verdict="approve", summary="오너가 올린다", evidence_ids=[], risks=[],
                                    recommended=True)])
    return DecisionCard(**{**base, **kw})


ASK = "Please upvote us"


@pytest.mark.parametrize("kind", ["external.other", "budget.spend", "external.landing_publish"])
@pytest.mark.parametrize("where", ["title", "summary", "rationale", "options", "recommended_option", "payload",
                                   "card.headline", "card.owner_does", "card.why", "card.recommendation", "card.option"])
def test_every_text_the_owner_reads_of_a_request_that_makes_a_task_is_checked_for_asks(s, project, kind, where):
    if where.startswith("card."):
        field = where.removeprefix("card.")
        card = _card(options=[CardOption(label="올린다", verdict="approve", summary=ASK, evidence_ids=[], risks=[], recommended=True)]
                     ) if field == "option" else _card(**{field: ASK})
        kw = {"card": card}
    elif where == "options":
        kw = {"options": ["승인", ASK]}
    elif where == "payload":
        kw = {"action_payload_json": '{"note": "%s"}' % ASK}
    else:
        kw = {where: ASK}
    req = _create(s, project, action_kind=kind, **kw)
    _denied(req, "forbidden_ask", msg("post.ask.votes", "en"))


def test_a_request_that_makes_no_task_is_not_screened(s, project):
    req = _create(s, project, action_kind="internal.other", post_card=None, summary=ASK)
    assert req.status == RequestStatus.PENDING.value


def test_a_goal_that_mentions_stars_does_not_stop_a_request(s, project):
    req = _create(s, project, action_kind="external.other", summary="목표는 GitHub star 100개입니다")
    assert req.status == RequestStatus.PENDING.value


# ------------------------------------------------------------------ the channel is the server's to confirm

def test_the_other_channel_cannot_carry_a_hacker_news_post_around_the_outline_rule(s, project):
    card = post_card("other", where_url="https://news.ycombinator.com/submit",
                     what=what(title="Show HN: Foo", body=HN_PROSE.replace(" Please upvote if you like it!", ""),
                               facts=[], expected_questions=[]))
    _denied(_create(s, project, post_card=card), "where_url", "Hacker News")


@pytest.mark.parametrize("channel,url", [
    ("hacker_news", "https://evil.example/x"),
    ("hacker_news", "https://news.ycombinator.com@evil.com/"),
    ("hacker_news", "https://evil.com\\@news.ycombinator.com/item?id=1"),
    ("hashnode", "https://news.ycombinator.com/submit"),
    ("other", "https://old.reddit.com/r/opensource/submit"),
    ("other", "https://NEWS.ycombinator.com./submit"),
    ("hacker_news", "https://news.ycombinator.com/submit?title=Show+HN&text=please+upvote+us"),
    ("hashnode", "https://hashnode.com/new?text=hello"),
    ("hacker_news", "https://news.ycombinator.com/submit?" + "a" * 600),
])
def test_a_where_url_off_the_channel_or_with_the_text_filled_in_is_denied(s, project, channel, url):
    card = post_card(channel, where_url=url, what=what(title="t", body="b", facts=[], expected_questions=[])
                     if channel in ("hashnode", "other") else what())
    _denied(_create(s, project, post_card=card), "where_url")


def test_an_ask_hidden_in_a_where_url_query_is_found(s, project):
    card = post_card("hashnode", where_url="https://hashnode.com/new?ref=please+upvote+us",
                     what=what(title="t", body="b", facts=[], expected_questions=[]))
    _denied(_create(s, project, post_card=card), "forbidden_ask", msg("post.ask.votes", "en"))


def test_a_task_on_the_other_channel_cannot_be_done_with_another_channels_link(s, project):
    tracking.set_doors(s, project.business_id, [{"kind": "site", "url": "https://my-blog.example/"}])
    s.commit()
    task = open_task(s, project, post_card=post_card(
        "other", where_url="https://my-blog.example/p", what=what(title="t", body="b", facts=[], expected_questions=[])))
    for link in ("https://news.ycombinator.com/item?id=1", "https://NEWS.ycombinator.com./item?id=1",
                 "https://www.reddit.com/r/x/comments/1", "https://github.com/me/repo"):
        with pytest.raises(owner_tasks.OwnerTaskError, match="That's a .* address"):
            owner_tasks.done(s, task.id, link=link)
    assert owner_tasks.done(s, task.id, link="https://my-blog.example/p/1").status == "DONE"


@pytest.mark.parametrize("link", ["https://news.ycombinator.com@evil.com/", "https://evil.com\\@news.ycombinator.com/",
                                  "https://news%2eycombinator.com/item?id=1", "https://" + "a" * 2000 + ".com/"])
def test_an_owner_link_cannot_hide_its_host_or_run_on(s, project, link):
    task = open_task(s, project)
    with pytest.raises(owner_tasks.OwnerTaskError):
        owner_tasks.done(s, task.id, link=link)


# ------------------------------------------------------------------ an outline card holds points, not paragraphs

def _outline(**kw) -> PostCardDraft:
    return post_card("hacker_news", what=what(**kw))


@pytest.mark.parametrize("item,rule", [
    ("가" * 121, "outline_too_long"),
    ("첫 문장입니다. 둘째 문장입니다.", "outline_many_sentences"),
    ("It works. Try it", "outline_many_sentences"),
    ("Why? Because.", "outline_many_sentences"),
    ("Show HN: Foo - a tool for X", "outline_headline"),
    ("show hn foo", "outline_headline"),
    ("Title: Foo", "outline_headline"),
    ("제목: 내 도구", "outline_headline"),
    ('"A tool for X"', "outline_headline"),
    ("“도구”", "outline_headline"),
])
@pytest.mark.parametrize("where", ["facts", "checklist", "talking_points", "question"])
def test_an_outline_item_that_is_a_paragraph_or_a_headline_is_refused_and_the_rule_is_recorded(s, project, where, item,
                                                                                               rule):
    w = {"facts": lambda: what(facts=[item]), "checklist": lambda: what(checklist=[item]),
         "talking_points": lambda: what(expected_questions=[ExpectedQuestion(q="왜?", talking_points=[item])]),
         "question": lambda: what(expected_questions=[ExpectedQuestion(q=item, talking_points=["요점"])])}[where]()
    _denied(_create(s, project, post_card=post_card("hacker_news", what=w)), rule, "takes no sentences")


def test_the_reviewers_prose_in_a_fact_and_a_talking_point_is_refused(s, project):
    prose = "I built this because I was tired of Y and wanted Z; it took me three months and I'd love feedback."
    assert len(prose) < owner_tasks.OUTLINE_ITEM_MAX
    pc = post_card("hacker_news", what=what(facts=["Show HN: Foo - a tool that does X. I built Foo because I was tired of Y."],
                                        expected_questions=[ExpectedQuestion(q="Why?", talking_points=[prose])]))
    assert _create(s, project, post_card=pc).status == RequestStatus.DENIED_BY_POLICY.value


@pytest.mark.parametrize("item", ["MIT 라이선스", "v1.2 설치 한 줄", "가입 없이 써 볼 수 있다.", "Why not X?", "가" * 120])
def test_a_short_point_passes_an_outline_card(s, project, item):
    req = _create(s, project, post_card=_outline(facts=[item]))
    assert req.status == RequestStatus.PENDING.value, req.response


# ------------------------------------------------------------------ the owner is the last check

@pytest.mark.parametrize("channel", [k for k, c in channels.registry().items() if channels.writable(c) or k == "other"])
def test_every_card_on_every_channel_ends_with_the_same_checklist_line(channel):
    mode = channels.get(channel).mode
    w = what() if mode == "outline" else what(title="제목", body="본문", facts=[], expected_questions=[],
                                              checklist=["링크 확인"])
    card, _ = owner_tasks.build_post_card(post_card(channel, what=w), lambda _channel: [], "en")
    checklist = card["what"]["checklist"]
    assert checklist[-1] == owner_tasks.fixed_check("en")
    assert checklist[:-1] == w.checklist


def test_a_draft_card_keeps_the_checklist_the_agent_wrote(s, project):
    task = open_task(s, project, post_card=post_card("hashnode", what=what(title="제목", body="본문", facts=[],
                                                                            expected_questions=[], checklist=["규칙 확인"])))
    assert task.card["what"]["checklist"] == ["규칙 확인", owner_tasks.fixed_check("en")]


# ------------------------------------------------------------------ what the prompts quote stays small

def _finish_many(s, project, n: int) -> None:
    for i in range(n):
        task = open_task(s, project, title=f"게시 {i} " + "가" * 250, post_card=post_card(
            "hashnode", what=what(title=f"제목 {i} of {n}", body="본문", facts=[], expected_questions=[])))
        owner_tasks.done(s, task.id, link="https://hashnode.com/p/" + "a" * 1500, note="n" * 1900,
                         numbers={f"k{j}": j for j in range(20)})
    s.commit()


def test_the_brief_does_not_grow_with_the_number_of_finished_tasks(s, project):
    _finish_many(s, project, 12)
    at_12 = len(ctx.project_brief(s, project))
    _finish_many(s, project, 18)
    section = owner_tasks.status_section(s, project.id)
    at_30 = len(ctx.project_brief(s, project))
    assert at_30 < at_12 + 400, (at_12, at_30)
    assert len(section) < 12000
    assert section.count("[OT-") == owner_tasks.BRIEF_TASKS and "done 30" in section
    assert "a" * 501 not in section and "n" * 201 not in section and "k19" not in section


def test_the_latest_tasks_are_the_ones_the_brief_lists(s, project):
    _finish_many(s, project, 12)
    section = owner_tasks.status_section(s, project.id)
    ids = sorted(t.id for t in tasks_of_project(project.id))
    assert f"OT-{ids[-1]} " in section and f"OT-{ids[0]} " not in section


def tasks_of_project(project_id):
    from justshipit.db.models import OwnerTask
    with session_scope() as s2:
        rows = list(s2.scalars(select(OwnerTask).where(OwnerTask.project_id == project_id)))
        s2.expunge_all()
        return rows


# ------------------------------------------------------------------ reports see every task

def test_the_weekly_review_sees_tasks_that_belong_to_no_project_and_the_count_of_each_business(s, project, monkeypatch):
    loose_req = requests.create(s, draft(title="프로젝트 없는 게시"), from_agent="project_manager", project_id=None, origin=by_project_manager(s, None))
    approve(s, loose_req)
    mine = open_task(s, project, title="내 프로젝트 게시")
    owner_tasks.done(s, mine.id, link="https://news.ycombinator.com/item?id=1")
    s.commit()

    section = owner_tasks.portfolio_section(s)
    assert "Work outside any project" in section and "프로젝트 없는 게시" in section
    assert f"B-{project.business_id}" in section and "done 1" in section and "no business" in section

    seen: dict = {}

    def fake(agent, purpose, prompt, model, **kw):
        seen["prompt"] = prompt
        raise RuntimeError("stop")

    monkeypatch.setattr(jobs, "run_agent", fake)
    with pytest.raises(RuntimeError, match="stop"):
        jobs.portfolio_review()
    assert "Work outside any project" in seen["prompt"] and "프로젝트 없는 게시" in seen["prompt"]


def test_a_project_task_whose_approval_made_an_owner_task_shows_the_owner_tasks_status(s, project):
    t = Task(project_id=project.id, key="post", title="Show HN 게시", owner="marketing", objective="o", status="DONE",
             result={"summary": "s", "findings_markdown": "f"})
    s.add(t)
    s.flush()
    req = requests.create(s, draft(), from_agent="marketing", project_id=project.id, origin=TaskOrigin(t.id))
    approve(s, req)
    s.commit()
    (owner_task,) = tasks_of(req.id)

    assert f"T-{t.id} [owner to-do: OPEN]" in ctx.results_overview(s, project)
    owner_tasks.done(s, owner_task.id, link="https://news.ycombinator.com/item?id=3")
    s.commit()
    assert f"T-{t.id} [owner to-do: DONE]" in ctx.results_overview(s, project)
    owner_tasks.reopen(s, owner_task.id)
    owner_tasks.skip(s, owner_task.id, note="접음")
    s.commit()
    assert f"T-{t.id} [owner to-do: SKIPPED]" in ctx.results_overview(s, project)


def test_a_project_task_with_no_owner_task_keeps_its_own_status(s, project):
    t = Task(project_id=project.id, key="research", title="조사", owner="research", objective="o", status="DONE")
    s.add(t)
    s.commit()
    assert "[DONE] 조사" in ctx.results_overview(s, project)


# ------------------------------------------------------------------ one inbox item per post

def test_a_second_request_for_the_same_post_is_merged_while_the_first_is_pending(s, project):
    first = _create(s, project, title="Show HN 게시", post_card=_outline(facts=["MIT 라이선스", "설치 한 줄"]))
    again = _create(s, project, title="다른 제목으로 다시", post_card=_outline(facts=["MIT 라이선스", "설치 두 줄"]))
    assert again.id == first.id
    other_post = _create(s, project, post_card=_outline(facts=["전혀 다른 사실"]))
    other_channel = _create(s, project, post_card=post_card("linkedin", what=what(facts=["MIT 라이선스"])))
    assert len({first.id, other_post.id, other_channel.id}) == 3


def test_a_post_whose_first_request_was_decided_may_be_requested_again(s, project):
    first = _create(s, project, post_card=_outline(facts=["MIT 라이선스"]))
    requests.respond(s, first.id, _approval(first))
    s.commit()
    assert _create(s, project, post_card=_outline(facts=["MIT 라이선스"])).id != first.id


def test_a_draft_channel_post_is_the_same_post_when_the_title_matches_ignoring_case_and_marks(s, project):
    def card(title):
        return post_card("hashnode", what=what(title=title, body="b", facts=[], expected_questions=[]))
    first = _create(s, project, post_card=card("My Tool: v1!"))
    assert _create(s, project, post_card=card("my tool v1")).id == first.id
    assert _create(s, project, post_card=card("my tool v2")).id != first.id


# ------------------------------------------------------------------ the log of an earlier task with the same id

def test_events_of_an_earlier_task_that_had_the_same_id_are_not_this_tasks(s, project):
    task = open_task(s, project)
    with session_scope() as s2:
        s2.add(ActivityEvent(at=task.created_at.replace(year=task.created_at.year - 1), actor="system",
                             verb="owner_task_created", entity="owner_task", entity_id=task.id, summary="old",
                             data={"request": "REQ-0"}))
    created = [e for e in events("owner_task", task.id) if e.verb == "owner_task_created"]
    assert len(created) == 1 and created[0].data["request"] == f"REQ-{task.request_id}"


# ------------------------------------------------------------------ a launch gate holds for every proposer

GATED = ("show_hn", "product_hunt", "geeknews")


def _meet_the_gate(business_id: int) -> None:
    from justshipit.services import launch_gate, promotion
    from bet_loop_kit import install_people
    install_people(first_value=20)
    with session_scope() as x:
        for item in launch_gate.OWNER_GATE_ITEMS:
            promotion.confirm_gate_item(x, business_id, item, lang="en")


@pytest.fixture
def _no_people():
    from justshipit.services import people_link
    people_link.use(None)
    yield
    people_link.use(None)


def _gated_card(key: str):
    return post_card(key, what=what() if channels.get(key).mode == "outline" else what(
        title="제목", body="본문", facts=[], expected_questions=[], checklist=["링크 확인"]))


@pytest.mark.parametrize("key", GATED)
def test_a_card_for_a_gated_channel_is_denied_with_what_is_missing(s, project, key, _no_people):
    req = _create(s, project, post_card=_gated_card(key))

    _denied(req, "launch_gate", "yet. First finish")
    for label in (launch_gate.gate_label(i, lang="en") for i in ("demo", "outside_install_3", "readme_checked",
                                                                   "first_value_20")):
        assert label in req.response["note"], label


def test_the_same_card_passes_once_the_owner_and_the_people_list_meet_the_gate(s, project, _no_people):
    from bet_loop_kit import reset_world
    s.commit()
    _meet_the_gate(project.business_id)
    try:
        req = _create(s, project, post_card=_gated_card("show_hn"))
    finally:
        reset_world()

    assert req.status == RequestStatus.PENDING.value


def test_a_gate_met_for_one_business_does_not_open_another(s, project, _no_people):
    from justshipit.services import projects
    from bet_loop_kit import reset_world
    other = projects.create(s, "다른 사업", name="다른 사업", budget_usd=10, start=False)
    s.commit()
    _meet_the_gate(other.business_id)
    try:
        req = _create(s, project, post_card=_gated_card("show_hn"))
    finally:
        reset_world()

    _denied(req, "launch_gate", launch_gate.gate_label("demo", lang="en"))


def test_a_gated_card_with_no_business_to_prove_the_gate_is_refused(_no_people):
    with pytest.raises(owner_tasks.CardRefused) as refused:
        owner_tasks.screen(draft(post_card=_gated_card("show_hn")), None, "en")

    assert refused.value.rule == "launch_gate" and launch_gate.gate_label("first_value_20", lang="en") in str(refused.value)


def test_a_channel_without_a_gate_needs_no_business(s):
    card, _ = owner_tasks.screen(draft(post_card=post_card("hacker_news")), None, "en")

    assert card["post_card"]["channel"] == "hacker_news"


def test_the_weekly_promotion_builds_its_cards_through_the_same_gate(s, project, _no_people):
    from justshipit.services import launch_gate
    channel = channels.get("show_hn")

    gate = launch_gate.gate_for(s, project.business_id)

    assert gate(channel) == ["demo", "outside_install_3", "readme_checked", "first_value_20"]
    with pytest.raises(owner_tasks.CardRefused, match="yet. First finish"):
        owner_tasks.build_post_card(_gated_card("show_hn"), gate, "en")
