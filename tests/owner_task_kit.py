"""Shared builders for the owner task tests."""
from justshipit.contracts.post_card import ExpectedQuestion, PostCardDraft, PostWhat, PostWhen
from justshipit.contracts.requests import (
    AgentAskDraft,
    HumanDecision,
    HumanResponse,
    RequestType,
    RiskLevel,
)
from justshipit.db.models import ActivityEvent, OwnerTask
from justshipit.db.session import session_scope
from justshipit.services import channels, requests
from sqlalchemy import select
from ask_kit import contract, by_project_manager


def draft(**kw) -> AgentAskDraft:
    kind = kw.get("action_kind", "external.social_post")
    if kind == "external.social_post":
        kw.setdefault("post_card", post_card())
    elif kind == "external.landing_publish":
        kw.setdefault("post_card", LANDING_CARD)
    base = dict(type=RequestType.APPROVAL, title="Show HN 게시", summary="Show HN에 올립니다", rationale="첫 100명을 위한 도전",
                options=[], recommended_option=None, action_kind="external.social_post", estimated_cost_usd=None,
                risk_level=RiskLevel.LOW, context=contract())
    return AgentAskDraft(**{**base, **kw})


def what(**kw) -> PostWhat:
    base = dict(title=None, body=None, facts=["MIT 라이선스", "설치 한 줄"],
                expected_questions=[ExpectedQuestion(q="왜 직접 만들었나요?", talking_points=["기존 도구의 한계"])],
                checklist=["가입 없이 써 볼 수 있는지 확인"])
    return PostWhat(**{**base, **kw})


def post_card(channel="hacker_news", **kw) -> PostCardDraft:
    domains = known.domains if (known := channels.get(channel)) else ()
    base = dict(channel=channel, where_url=f"https://{domains[0]}/submit" if domains else None,
                when=PostWhen(window="화~목 오전 9~11시(미국 동부)", reason="추정: 미국 개발자가 접속하는 시간"),
                why="도전 BET-1의 가설: Show HN이 star 30개를 만든다", what=what())
    return PostCardDraft(**{**base, **kw})


LANDING_CARD = post_card("other", where_url=None, what=what(title="랜딩 공개", body="랜딩 초안", facts=[],
                                                          expected_questions=[]))


def approve(s, req, **kw):
    return requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE, **kw))


def tasks_of(request_id: int) -> list[OwnerTask]:
    with session_scope() as s:
        found = list(s.scalars(select(OwnerTask).where(OwnerTask.request_id == request_id)))
        s.expunge_all()
        return found


def events(entity: str, entity_id: int) -> list[ActivityEvent]:
    """The events of one record. An owner task's id restarts when a migration test drops and recreates the table, and
    the events of an earlier task with the same id stay in the log, so only events from the task's own life count."""
    with session_scope() as s:
        q = select(ActivityEvent).where(ActivityEvent.entity == entity, ActivityEvent.entity_id == entity_id)
        if entity == "owner_task":
            q = q.where(ActivityEvent.at >= select(OwnerTask.created_at).where(OwnerTask.id == entity_id).scalar_subquery())
        found = list(s.scalars(q.order_by(ActivityEvent.id)))
        s.expunge_all()
        return found


def as_stored_before_the_rules_check(task: OwnerTask, channel: str) -> OwnerTask:
    """A to-do for a place whose rules were not read, as one stored before that check: the company makes no such card
    now, so the task is made through a checked place and its card is filed under `channel` afterwards."""
    with session_scope() as s:
        row = s.get(OwnerTask, task.id)
        row.card = {**row.card, "channel": channel}
        s.flush()
        s.expunge(row)
    return row


def open_task(s, project, **kw) -> OwnerTask:
    req = requests.create(s, draft(**kw), from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    approve(s, req, confirm_text=f"REQ-{req.id}")
    s.commit()
    return tasks_of(req.id)[0]
