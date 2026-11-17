"""One matter, one card. Each test replays a duplicate the 2026-11-04 critic round found on the live console."""
import pytest
from sqlalchemy import select

from justshipit.contracts.handoff import RequirementOut, SpecDraftOut, SpecStatus
from story_kit import JOURNEYS
from story_kit import P1 as P1_STORIES
from justshipit.contracts.requests import (
    AgentRequestDraft, HumanDecision, HumanResponse, RequestStatus, RequestType, RiskLevel,
)
from justshipit.db.models import ActivityEvent, AgentRequest, DevJob, Job, OwnerTask, Plan, Spec
from justshipit.db.session import session_scope
from justshipit.engineering import specs
from justshipit.services import matters, queue, requests
from owner_task_kit import post_card, what
from ask_kit import by_project_manager, contract
from justshipit.contracts.origins import TaskOrigin
from justshipit.contracts.work_path import HoldKind


def draft(**kw) -> AgentRequestDraft:
    base = dict(type=RequestType.APPROVAL, title="t", summary="s", rationale="r", options=[],
                recommended_option=None, action_kind=None, estimated_cost_usd=None,
                risk_level=RiskLevel.MEDIUM, context=contract())
    return AgentRequestDraft(**{**base, **kw})


def dev(title: str, kind: str | None = "internal.other") -> AgentRequestDraft:
    return draft(type=RequestType.DEVELOPMENT, title=title, summary=f"{title} 만들기", action_kind=kind)


def flyer(title: str, **kw) -> AgentRequestDraft:
    return draft(title=title, action_kind="external.other", risk_level=RiskLevel.LOW,
                 post_card=post_card("other", where_url=None, what=what(title=title, body="전단 문구", facts=[],
                                                                       expected_questions=[])), **kw)


def cards(s, project) -> list[AgentRequest]:
    s.flush()
    return list(s.scalars(select(AgentRequest).where(AgentRequest.project_id == project.id,
                                                     AgentRequest.status == RequestStatus.PENDING.value)))


def notes(s, project, verb: str) -> list[ActivityEvent]:
    s.flush()
    return list(s.scalars(select(ActivityEvent).where(ActivityEvent.verb == verb, ActivityEvent.project_id == project.id).order_by(ActivityEvent.id)))


def asked(s, project, title: str) -> AgentRequest:
    """A development ask filed before product plans, approved with its spec being written: what `create` made of a
    development draft then. A new one starts a plan (test_plans.py)."""
    req = AgentRequest(project_id=project.id, from_agent="product", type="DEVELOPMENT", title=title,
                       summary=f"{title} 만들기", rationale="r", action_kind="dev.implementation_start",
                       policy_mode="auto", status=RequestStatus.AUTO_APPROVED.value,
                       action_params={"matter": f"dev:{matters.product_scope(s, project.id)[0]}"})
    s.add(req)
    s.flush()
    queue.enqueue(s, "write_spec", {"project_id": project.id, "request_id": req.id},
                  idempotency_key=f"write_spec:{req.id}")
    return req


def spec_for(s, project, req: AgentRequest, *, status=SpecStatus.IN_REVIEW, repo="quote-bot") -> Spec:
    """What write_spec leaves behind: the spec and its one approval card."""
    spec = Spec(spec_key=f"ONE-{req.id}", project_id=project.id, title="견적봇 MVP", registry_path="specs/one",
                status=status.value, draft_commit_sha="abc", target_repository=repo, request_id=req.id,
                handoff={})
    s.add(spec)
    s.flush()
    specs._ask_approval(s, spec, draft(title=f"명세 승인 및 개발 착수: {spec.spec_key}", action_kind="dev.spec_approve"))
    return spec


def test_the_owner_never_sees_a_development_ask_and_its_product_plan_starts_at_once(s, project):
    """견적봇 REQ-7: a development ask was a card of its own, and then the spec was a second card for the same call.
    Now the ask is no card: its product plan is written, and the plan's card is the owner's first decision."""
    first = requests.create(s, dev("전달 주소 + mailto 답장 링크 MVP 개발 스펙 승인"), from_agent="product",
                            project_id=project.id, origin=by_project_manager(s, project.id))

    assert isinstance(first, Plan)
    assert s.scalar(select(Job).where(Job.idempotency_key == f"write_plan:{first.id}")) is not None
    assert s.scalar(select(Job).where(Job.kind == "write_spec",
                                      Job.payload["project_id"].as_integer() == project.id)) is None
    assert cards(s, project) == []


def test_every_later_development_ask_for_the_product_folds_into_its_one_matter(s, project):
    """견적봇: REQ-7, REQ-8 (same task), REQ-30 (CEO) and REQ-32..38 (report) were ten approvals and eight specs."""
    first = asked(s, project, "전달 주소 + mailto 답장 링크 MVP 개발 스펙 승인")
    again = requests.create(s, dev("전달 주소 + mailto 답장 링크 MVP 스펙 확정 승인"), from_agent="product",
                            project_id=project.id, origin=by_project_manager(s, project.id))
    project_manager = requests.create(s, dev("견적봇 MVP 구현 시작", "dev.implementation_start"), from_agent="project_manager",
                          project_id=project.id, origin=by_project_manager(s, project.id))

    assert again.id == first.id == project_manager.id
    assert s.scalar(select(Job).where(Job.idempotency_key == f"write_spec:{again.id}")).id \
        == s.scalar(select(Job).where(Job.idempotency_key == f"write_spec:{first.id}")).id
    assert len(notes(s, project, "request_folded")) == 2

    card = s.get(AgentRequest, spec_for(s, project, first).approval_request_id)
    for title in ("수신 Worker와 동의서 게이트", "견적 분류·초안 생성", "알림(메일 기본, 웹 푸시 보조)"):
        assert requests.create(s, dev(f"개발 제안: {title}", "dev.implementation_start"), from_agent="product",
                               project_id=project.id, origin=by_project_manager(s, project.id)).id == first.id

    assert [r.id for r in cards(s, project)] == [card.id], "the spec card is the one development decision"
    assert "· product]" not in card.rationale, "who asked, in owner words"
    assert [n["title"] for n in card.action_params["extra_needs"]] == [
        "개발 제안: 수신 Worker와 동의서 게이트", "개발 제안: 견적 분류·초안 생성", "개발 제안: 알림(메일 기본, 웹 푸시 보조)"]


def test_an_active_or_failed_build_takes_no_new_development_card(s, project):
    """원두재고: REQ-44..49 asked for six features J-1 had already built."""
    first = asked(s, project, "원두재고 스펙 v1.2 승인과 개발 시작")
    spec = spec_for(s, project, first, status=SpecStatus.READY_FOR_DEV)
    requests.respond(s, spec.approval_request_id, HumanResponse(decision=HumanDecision.APPROVE))

    later = [requests.create(s, dev(f"개발 제안: {t}", "dev.implementation_start"), from_agent="product",
                             project_id=project.id,
                             origin=by_project_manager(s, project.id)) for t in ("정본 표 JSON 복구", "계산 엔진 JS 이식", "Cloudflare Pages 배포")]

    assert {r.id for r in later} == {first.id} and cards(s, project) == []
    assert "There's an approved spec" in notes(s, project, "request_folded")[-1].summary


def test_a_rejected_spec_is_a_no_to_that_spec_and_the_next_ask_drafts_another(s, project):
    """원두재고 REQ-43: the owner rejected a duplicate spec. A rejection turned every later development ask into a note,
    so the product could never be built (security review M5); the next ask now drafts a new spec, in the product's
    one repository (write_spec keeps it). The new spec starts from a new product plan."""
    first = asked(s, project, "원두재고 MVP")
    spec = spec_for(s, project, first)
    requests.respond(s, spec.approval_request_id, HumanResponse(decision=HumanDecision.REJECT, note="중복 명세"))

    later = requests.create(s, dev("원두재고 MVP 다시"), from_agent="product", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    assert isinstance(later, Plan) and later.status == "DRAFT"
    assert matters.product_repo(s, project.id) == "quote-bot"


def building_with_a_rejected_duplicate(s, project, job_status: str, error: str | None = None):
    """원두재고: BUSINE-0001 approved and J-1 on it, then a duplicate spec of an older ask the owner rejected."""
    first = asked(s, project, "원두재고 MVP")
    built = spec_for(s, project, first, status=SpecStatus.READY_FOR_DEV)
    requests.respond(s, built.approval_request_id, HumanResponse(decision=HumanDecision.APPROVE))
    built.approved_commit_sha = built.draft_commit_sha   # what on_spec_approved leaves before it queues the job
    s.add(DevJob(spec_id=built.id, status=job_status, attempt=1, error=error))
    older = AgentRequest(project_id=project.id, from_agent="product", type="DEVELOPMENT", title="원두재고 MVP 중복",
                         summary="s", rationale="r", action_kind="dev.implementation_start", policy_mode="auto",
                         status="APPROVED")
    s.add(older)
    s.flush()
    duplicate = spec_for(s, project, older)
    requests.respond(s, duplicate.approval_request_id, HumanResponse(decision=HumanDecision.REJECT, note="중복 명세"))
    return first, built


@pytest.mark.parametrize("job_status, error, says", [
    ("IMPLEMENTING", None, "Building from the approved spec"),
    ("FAILED", "예산이 모자라 개발을 멈췄어요. 프로젝트 예산", "with no build running now"),
    ("DELIVERED", None, "waits for the merge decision"),
])
def test_a_rejected_later_spec_leaves_development_asks_on_the_spec_being_built(s, project, job_status, error, says):
    """원두재고 REQ-44..49: six development proposals, each a card and a new spec, for scope J-1 was building."""
    first, built = building_with_a_rejected_duplicate(s, project, job_status, error)
    titles = ("정본 표 JSON 복구", "계산 엔진 JS 이식", "재고 화면", "발주 알림", "Cloudflare Pages 배포", "사용 안내")

    later = [requests.create(s, dev(f"개발 제안: {t}", "dev.implementation_start"), from_agent="product",
                             project_id=project.id, origin=by_project_manager(s, project.id)) for t in titles]

    assert {r.id for r in later} == {first.id} and cards(s, project) == []
    assert list(s.scalars(select(Job.idempotency_key).where(
        Job.kind == "write_spec", Job.payload["project_id"].as_integer() == project.id))) == [f"write_spec:{first.id}"]
    card = s.get(AgentRequest, built.approval_request_id)
    assert [n["title"] for n in card.action_params["extra_needs"]] == [f"개발 제안: {t}" for t in titles], \
        "the approved spec's next version takes the needs up"
    assert says in notes(s, project, "request_folded")[-1].summary
    assert matters.live_spec(s, project.id).id == built.id, "write_spec keeps the spec being built"


def test_a_rejected_later_spec_after_the_built_one_merged_lets_the_next_ask_draft_a_spec(s, project):
    first, built = building_with_a_rejected_duplicate(s, project, "MERGED")
    built.status = SpecStatus.MERGED.value

    later = requests.create(s, dev("원두재고 v2: 발주 알림"), from_agent="product", project_id=project.id,
                            origin=by_project_manager(s, project.id))

    assert isinstance(later, Plan) and later.status == "DRAFT"
    assert matters.live_spec(s, project.id) is None


def test_a_deferred_spec_is_asked_again_on_the_next_development_ask(s, project):
    first = asked(s, project, "견적봇 MVP")
    spec = spec_for(s, project, first)
    old = spec.approval_request_id
    requests.respond(s, old, HumanResponse(decision=HumanDecision.DEFER, note="다음 주에"))

    requests.create(s, dev("견적봇 MVP 착수"), from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))

    [card] = cards(s, project)
    assert card.id != old and card.action_params["spec_id"] == spec.id and spec.approval_request_id == card.id
    assert s.get(AgentRequest, old).status == RequestStatus.CANCELLED.value
    assert s.get(AgentRequest, old).response["note"] == "다음 주에", "the owner's answer stays on the old card"


def test_a_merged_product_takes_its_next_version_as_a_new_development_ask(s, project):
    first = asked(s, project, "견적봇 v1")
    spec_for(s, project, first, status=SpecStatus.MERGED)

    v2 = requests.create(s, dev("견적봇 v2: 알림"), from_agent="product", project_id=project.id,
                         origin=by_project_manager(s, project.id))

    assert isinstance(v2, Plan) and v2.status == "DRAFT"
    assert s.scalar(select(Job).where(Job.idempotency_key == f"write_plan:{v2.id}")) is not None


def test_an_owner_rule_on_development_still_makes_one_plan_that_later_asks_join(s, project):
    """An owner rule on dev.* once brought back a card for spec writing. The owner approves every plan before its spec
    now, so the ask is one plan, and a later ask joins it."""
    from justshipit.db.models import Business
    s.get(Business, project.business_id).door_policy = [{"action": "dev.*", "decider": "owner"}]
    first = requests.create(s, dev("견적봇 MVP"), from_agent="product", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    again = requests.create(s, dev("견적봇 알림"), from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))

    assert isinstance(first, Plan) and again.id == first.id
    assert [n["title"] for n in first.need["extra_needs"]] == ["견적봇 알림"]
    assert s.scalar(select(Job).where(Job.kind == "write_spec",
                                      Job.payload["project_id"].as_integer() == project.id)) is None


def _spec_out(repo: str) -> SpecDraftOut:
    return SpecDraftOut(title="견적봇 MVP", prd_markdown="p", requirements=[
        RequirementOut(id="REQ-1", statement="s", priority="must", rationale="r")], acceptance_criteria=["c"],
        design_notes_markdown="d", suggested_target_repository=repo,
        suggested_test_command=None, suggested_test_image=None, user_stories=P1_STORIES, journeys=JOURNEYS)


def test_write_spec_keeps_one_spec_and_one_repository_per_product(s, project, monkeypatch):
    """견적봇: BUSINE2-0001..0010 for one MVP, in `quote-bot` and `quote-reply-bot`."""
    asked = []
    monkeypatch.setattr(specs, "run_agent", lambda *a, **k: asked.append(a) or _spec_out("quote-reply-bot"))
    a = AgentRequest(project_id=project.id, from_agent="product", type="DEVELOPMENT", title="v1", summary="s",
                     rationale="r", action_kind="dev.implementation_start", policy_mode="auto", status="APPROVED")
    b = AgentRequest(project_id=project.id, from_agent="product", type="DEVELOPMENT", title="v1 again", summary="s",
                     rationale="r", action_kind="dev.implementation_start", policy_mode="auto", status="APPROVED")
    s.add_all([a, b])
    s.flush()
    old = spec_for(s, project, a, status=SpecStatus.MERGED, repo="quote-bot")
    old_card = s.get(AgentRequest, old.approval_request_id)
    old_card.action_params = {**old_card.action_params, "extra_needs": [{"title": "초안 도착 알림", "problem": "p"}]}
    s.commit()

    first = specs.write_spec(project.id, b.id)
    second = specs.write_spec(project.id, b.id)
    c = AgentRequest(project_id=project.id, from_agent="project_manager", type="DEVELOPMENT", title="dup", summary="s",
                     rationale="r", action_kind="dev.implementation_start", policy_mode="auto", status="APPROVED")
    s.add(c)
    s.commit()
    third = specs.write_spec(project.id, c.id)

    with session_scope() as t:
        new = t.get(Spec, first)
        assert new.id != old.id and new.target_repository == "quote-bot", "a later version keeps the repository"
        assert t.get(AgentRequest, new.approval_request_id).status == RequestStatus.PENDING.value
    assert second == first == third and len(asked) == 1, "no second draft while the spec is in review"
    assert "초안 도착 알림" in asked[0][2], "a need left on the earlier version's card reaches the next one"


def _task(s, project, key="posts"):
    from justshipit.contracts.work import TaskDraft
    from justshipit.services import tasks
    return tasks.create_from_drafts(s, project, [TaskDraft(
        key=key, title="게시 글", objective="o", owner="marketing", depends_on=[], acceptance_criteria=["a"])],
        origin="ceo_plan", plan_round=1)[0][0]


def test_a_rework_replaces_the_cards_its_earlier_pass_raised(s, project):
    """동네산책 T-26: rework raised REQ-17..21 beside the still open REQ-11..15, five posts on ten cards."""
    t = _task(s, project)
    earlier = requests.from_agent_drafts(s, [flyer("보호자 첫 글"), flyer("단톡방 글")], agent="marketing",
                                         project_id=project.id, origin=TaskOrigin(t.id))
    now = requests.from_agent_drafts(s, [flyer("보호자분들, 평일 산책 어떻게 하세요?"), flyer("단톡방 안내 글")],
                                     agent="marketing", project_id=project.id, origin=TaskOrigin(t.id))

    assert requests.supersede_iteration(s, t.id, earlier, now) == sorted(earlier)
    assert {r.id for r in cards(s, project)} == set(now)


def test_a_rework_that_only_adds_a_card_leaves_the_open_ones(s, project):
    t = _task(s, project)
    earlier = requests.from_agent_drafts(s, [flyer("보호자 첫 글"), flyer("단톡방 글")], agent="marketing",
                                         project_id=project.id, origin=TaskOrigin(t.id))
    now = requests.from_agent_drafts(s, [flyer("산책 모임 소개 글")], agent="marketing", project_id=project.id,
                                     origin=TaskOrigin(t.id))

    assert requests.supersede_iteration(s, t.id, earlier, now) == []
    assert len(cards(s, project)) == 3


def test_no_agent_can_replace_a_card_the_server_tied_to_a_spec(s, project):
    first = asked(s, project, "견적봇 MVP")
    card = s.get(AgentRequest, spec_for(s, project, first).approval_request_id)

    other = requests.create(s, draft(title="명세 승인 (수정본)", action_kind="dev.spec_approve",
                                     replaces=f"REQ-{card.id}"), from_agent="product", project_id=project.id,
                                     origin=by_project_manager(s, project.id))

    assert card.status == RequestStatus.PENDING.value and other.id != card.id
    assert other.action_kind == "internal.other", "an agent still cannot file a spec approval"


def test_a_task_whose_card_another_task_replaced_follows_the_owners_answer_there(s, project):
    """The task gate reads the task's own newest card; when another task replaced it, the decision lives on."""
    a, b = _task(s, project, "a"), _task(s, project, "b")
    first = requests.create(s, flyer("토요일 현장 전단", context=contract(HoldKind.THIS_TASK)), from_agent="marketing", project_id=project.id,
                            origin=TaskOrigin(a.id))
    requests.respond(s, first.id, HumanResponse(decision=HumanDecision.DEFER))
    again = requests.create(s, flyer("토요일 현장 전단 전문", replaces=f"REQ-{first.id}"), from_agent="marketing",
                            project_id=project.id, origin=TaskOrigin(b.id))
    requests.respond(s, again.id, HumanResponse(decision=HumanDecision.APPROVE, confirm_text=f"REQ-{again.id}"))

    assert requests.current(s, first).id == again.id


def test_a_revision_that_costs_more_than_the_approval_is_a_new_card(s, project):
    first = requests.create(s, flyer("토요일 현장 전단"), from_agent="marketing", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    requests.respond(s, first.id, HumanResponse(decision=HumanDecision.APPROVE, confirm_text=f"REQ-{first.id}"))
    again = requests.create(s, flyer("토요일 현장 전단 컬러 인쇄", replaces=f"REQ-{first.id}", estimated_cost_usd=30),
                            from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))

    assert again.id != first.id and again.status == RequestStatus.PENDING.value
    assert first.status == RequestStatus.APPROVED.value, "what the owner approved stays approved"


def test_a_revision_of_an_open_post_card_updates_that_card(s, project):
    first = requests.create(s, flyer("토요일 현장 전단"), from_agent="marketing", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    again = requests.create(s, flyer("토요일 현장 전단 A5 앞면 전문", replaces=f"REQ-{first.id}"), from_agent="marketing",
                            project_id=project.id, origin=by_project_manager(s, project.id))

    assert again.id == first.id and first.title == "토요일 현장 전단 A5 앞면 전문"
    assert first.action_params["revision"] == 2
    assert first.action_params["post_card"]["what"]["title"] == "토요일 현장 전단 A5 앞면 전문"
    assert len(cards(s, project)) == 1
    later = requests.create(s, flyer("토요일 현장 전단 A5 앞면 전문"), from_agent="marketing", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    assert later.id == first.id, "the revised title finds the card without naming it"


def test_a_revised_email_replaces_its_card_so_the_owner_never_approves_unread_text(s, project):
    mail = '{"to": ["a@example.com"], "subject": "안내", "body": "%s"}'
    first = requests.create(s, draft(title="사장님께 안내 메일", action_kind="external.email_send",
                                     action_payload_json=mail % "첫 안"), from_agent="marketing", project_id=project.id,
                                     origin=by_project_manager(s, project.id))
    again = requests.create(s, draft(title="사장님께 안내 메일 고친 안", action_kind="external.email_send",
                                     action_payload_json=mail % "고친 안", replaces=f"REQ-{first.id}"),
                            from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))

    assert again.id != first.id and [r.id for r in cards(s, project)] == [again.id]
    assert first.action_params["payload"]["body"] == "첫 안", "the card the owner read keeps its text"
    with pytest.raises(requests.ResponseRejected):
        requests.respond(s, first.id, HumanResponse(decision=HumanDecision.APPROVE))


def test_one_flyer_stays_one_card_and_one_owner_task(s, project):
    """동네산책: REQ-22, 26, 28, 29 and 31 were five cards for one flyer. A revision of the approved text is one new
    decision (security review L1: the owner approves the words they read), and later revisions revise that card."""
    r22 = requests.create(s, flyer("토요일 현장 전단 문구와 QR 배치 승인"), from_agent="marketing", project_id=project.id,
                          origin=by_project_manager(s, project.id))
    requests.respond(s, r22.id, HumanResponse(decision=HumanDecision.DEFER, note="전문을 붙여 다시 올려 주세요"))
    r26 = requests.create(s, flyer("[게시] 토요일 현장 전단 A 비치", replaces=f"REQ-{r22.id}"), from_agent="marketing",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    assert r26.id != r22.id and s.get(AgentRequest, r22.id).status == RequestStatus.CANCELLED.value
    requests.respond(s, r26.id, HumanResponse(decision=HumanDecision.APPROVE, confirm_text=f"REQ-{r26.id}"))
    [task] = s.scalars(select(OwnerTask).where(OwnerTask.request_id == r26.id))

    assert requests.create(s, flyer("[게시] 토요일 현장 전단 A 비치", replaces=f"REQ-{r26.id}"), from_agent="marketing",
                           project_id=project.id,
                           origin=by_project_manager(s, project.id)).id == r26.id, "the same text again goes onto the owner task"
    revised = [requests.create(s, flyer(title, replaces=f"REQ-{r26.id}"), from_agent="marketing", project_id=project.id,
                               origin=by_project_manager(s, project.id))
               for title in ("[게시] 토요일 현장 전단 A5 앞면 전문 + QR 3종", "[게시] 토요일 현장 전단 A5 앞면 전문 제출본")]

    assert revised[0].id == revised[1].id != r26.id
    assert [r.id for r in cards(s, project)] == [revised[0].id]
    assert revised[0].title == "[게시] 토요일 현장 전단 A5 앞면 전문 제출본"
    assert [u["title"] for u in task.card["updates"]] == ["[게시] 토요일 현장 전단 A 비치"]
    assert len(notes(s, project, "owner_task_updated")) == 1


def _approved_flyer(s, project) -> tuple[AgentRequest, OwnerTask]:
    first = requests.create(s, flyer("토요일 현장 전단"), from_agent="marketing", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    requests.respond(s, first.id, HumanResponse(decision=HumanDecision.APPROVE, confirm_text=f"REQ-{first.id}"))
    [task] = s.scalars(select(OwnerTask).where(OwnerTask.request_id == first.id))
    return first, task


def _open_tasks(s, project) -> list[OwnerTask]:
    s.flush()
    return list(s.scalars(select(OwnerTask).where(OwnerTask.project_id == project.id, OwnerTask.status == "OPEN")))


def test_an_approved_revision_of_a_post_takes_over_its_to_do(s, project):
    """Maya REQ-55 and REQ-69: the owner approved a post, a follow-up task drafted other words naming it, and approving
    those made a second open to-do for the same post next to the first."""
    first, task = _approved_flyer(s, project)
    again = requests.create(s, flyer("토요일 현장 전단 고친 안", replaces=f"REQ-{first.id}"), from_agent="marketing",
                            project_id=project.id, origin=by_project_manager(s, project.id))

    assert again.id != first.id and again.status == RequestStatus.PENDING.value
    assert requests.read_model(s, again, "en", None)["report"]["revises"] == f"The card you already approved: {first.title}"
    assert first.status == RequestStatus.APPROVED.value, "what the owner approved stands until they decide the revision"

    requests.respond(s, again.id, HumanResponse(decision=HumanDecision.APPROVE, confirm_text=f"REQ-{again.id}"))

    assert [(t.id, t.request_id) for t in _open_tasks(s, project)] == [(task.id, again.id)]
    assert task.title == again.title and task.card["what"]["title"] == "토요일 현장 전단 고친 안"
    assert first.response["superseded_by"] == f"REQ-{again.id}"


def test_a_declined_revision_leaves_the_approved_post_and_its_to_do(s, project):
    first, task = _approved_flyer(s, project)
    again = requests.create(s, flyer("토요일 현장 전단 고친 안", replaces=f"REQ-{first.id}"), from_agent="marketing",
                            project_id=project.id, origin=by_project_manager(s, project.id))
    requests.respond(s, again.id, HumanResponse(decision=HumanDecision.REJECT, note="처음 문구가 좋아요"))

    assert [(t.id, t.request_id) for t in _open_tasks(s, project)] == [(task.id, first.id)]
    later = requests.create(s, flyer("토요일 현장 전단 세 번째 안", replaces=f"REQ-{again.id}"), from_agent="marketing",
                            project_id=project.id, origin=by_project_manager(s, project.id))
    assert later.id not in (first.id, again.id) and later.status == RequestStatus.PENDING.value, (
        "the declined revision is not the matter's head: the approved post still is")
    assert first.title in requests.read_model(s, later, "en", None)["report"]["revises"]


def test_a_revision_card_says_it_changes_the_open_to_do_and_what_a_no_keeps(s, project):
    """The card said 'Adds a to-do' while approving it changes the one already open, and never said what a no does."""
    first, task = _approved_flyer(s, project)
    again = requests.create(s, flyer("토요일 현장 전단 고친 안", replaces=f"REQ-{first.id}"), from_agent="marketing",
                            project_id=project.id, origin=by_project_manager(s, project.id))

    says = requests.read_model(s, again, "en", None)["report"]["on_approve"]
    assert "Adds a to-do" not in says
    assert first.title in says and "changes to this version" in says and "stays as you approved it" in says


def test_saying_no_to_a_revision_keeps_the_approved_to_do_as_the_card_said(s, project):
    """L9 gap 1: a 'no' to a revision card ('Don't post it' reads as a no) leaves the approved post's to-do as approved,
    which is what the card now says; the to-do is not closed and keeps the text the owner approved."""
    first, task = _approved_flyer(s, project)
    again = requests.create(s, flyer("토요일 현장 전단 고친 안", replaces=f"REQ-{first.id}",
                                     options=["Post the new text", "Don't post it"]),
                            from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    requests.respond(s, again.id, HumanResponse(decision=HumanDecision.REJECT, chosen_option="Don't post it"))

    assert [(t.id, t.request_id, t.title) for t in _open_tasks(s, project)] == [(task.id, first.id, first.title)]
    assert "superseded_by" not in (first.response or {})


def test_a_declining_pick_on_a_revision_says_the_approved_to_do_stays(s, project):
    """create_for_request returned 'no to-do was made' for a declining pick on a revision, while the approved to-do it
    revised stays open: the activity line says that to-do stays as approved."""
    from justshipit.services import owner_tasks
    first, task = _approved_flyer(s, project)
    again = requests.create(s, flyer("토요일 현장 전단 고친 안", replaces=f"REQ-{first.id}"), from_agent="marketing",
                            project_id=project.id, origin=by_project_manager(s, project.id))
    again.status, again.response = RequestStatus.APPROVED.value, {"decision": "approve", "chosen_option": "Not now"}

    assert owner_tasks.create_for_request(s, again) is None
    assert [(t.id, t.request_id) for t in _open_tasks(s, project)] == [(task.id, first.id)]
    assert notes(s, project, "owner_task_not_needed") == []
    [kept] = notes(s, project, "owner_task_kept")
    assert kept.entity_id == task.id and "Not now" in kept.summary


def test_the_revision_the_owner_declined_is_not_asked_again(s, project):
    first, task = _approved_flyer(s, project)
    again = requests.create(s, flyer("토요일 현장 전단 고친 안", replaces=f"REQ-{first.id}"), from_agent="marketing",
                            project_id=project.id, origin=by_project_manager(s, project.id))
    requests.respond(s, again.id, HumanResponse(decision=HumanDecision.REJECT, note="처음 문구가 좋아요"))

    same = requests.create(s, flyer("토요일 현장 전단 고친 안", replaces=f"REQ-{again.id}"), from_agent="marketing",
                           project_id=project.id, origin=by_project_manager(s, project.id))
    assert same.id in (first.id, again.id), "the owner said no to these words: the same ask is a note, not a new card"
    assert [(t.id, t.request_id) for t in _open_tasks(s, project)] == [(task.id, first.id)]


def test_a_flyer_whose_title_holds_a_place_word_is_refused_and_told_why_and_a_landing_page_is_not(s, project):
    refused = requests.create(s, flyer("당근알바 도우미 공고"), from_agent="marketing", project_id=project.id,
                              origin=by_project_manager(s, project.id))
    assert refused.status == "DENIED_BY_POLICY" and "Daangn jobs" in refused.response["note"] and "rules were not read" in refused.response["note"]
    assert cards(s, project) == []
    page = draft(title="당근 주스 랜딩", action_kind="external.landing_publish", risk_level=RiskLevel.LOW,
                 post_card=post_card("other", where_url=None, what=what(title="당근 주스 랜딩", body="소개", facts=[],
                                                                        expected_questions=[])))
    assert requests.create(s, page, from_agent="marketing", project_id=project.id,
                           origin=by_project_manager(s, project.id)).status == "PENDING"


def test_the_same_post_asked_again_after_the_owner_rejected_it_is_a_note(s, project):
    first = requests.create(s, flyer("동네 도우미 공고"), from_agent="marketing", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    requests.respond(s, first.id, HumanResponse(decision=HumanDecision.REJECT, note="허위 구인이에요"))

    assert requests.create(s, flyer("동네 도우미 공고"), from_agent="marketing", project_id=project.id,
                           origin=by_project_manager(s, project.id)).id == first.id
    assert cards(s, project) == []


def test_a_record_with_nothing_for_the_owner_to_do_is_an_activity_note(s, project):
    """견적봇 REQ-10: "REQ-7과 REQ-8에서 오너가 승인한 같은 결정이다 … 기록만 남기며" was a decision card."""
    ids = requests.from_agent_drafts(s, [draft(title="MVP 개발 스펙 확정 기록", owner_action=False)], agent="product",
                                     project_id=project.id, origin=by_project_manager(s, project.id))

    assert ids == [] and cards(s, project) == []
    assert notes(s, project, "agent_notice")[-1].summary.startswith("MVP 개발 스펙 확정 기록")


@pytest.mark.parametrize("kind", [None, "internal.task_create", "internal.other"])
def test_a_development_ask_is_one_action_whatever_the_agent_named(s, project, kind):
    """원두재고 REQ-39 was a DEVELOPMENT request under internal.task_create, REQ-5 under internal.other. Each is a
    development ask, so each starts the product's plan."""
    r = requests.create(s, dev("T-30 원문을 개발 착수 패키지에 첨부", kind), from_agent="product", project_id=project.id,
                        origin=by_project_manager(s, project.id))
    assert isinstance(r, Plan)


def test_an_approval_to_start_development_is_the_same_development_matter(s, project):
    """견적봇 REQ-30 was the CEO's own "구현 시작" approval for the MVP the owner had approved at REQ-7."""
    first = requests.create(s, dev("견적봇 MVP"), from_agent="product", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    start = requests.create(s, draft(title="견적봇 MVP 구현 시작", action_kind="dev.implementation_start"),
                            from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))
    assert start.id == first.id and cards(s, project) == []


def test_the_final_report_raises_one_development_ask_for_all_its_needs(s, project, monkeypatch, korean_owner):
    """견적봇 REQ-32..38: the report made one card per development need, seven for one MVP."""
    from justshipit.contracts.planning import DevelopmentNeedOut, FinalReportOut
    from justshipit.db.models import Artifact
    from justshipit.orchestration import company_graph

    needs = [DevelopmentNeedOut(title=t, problem=f"{t} 필요", why_software="w", expected_value="v", priority=p)
             for t, p in (("알림", "P2"), ("수신 Worker", "P0"), ("초안 화면", "P1"))]

    def keep(sess, p, kind, title, rel, content):
        art = Artifact(project_id=p.id, kind=kind, title=title, path=rel, commit_sha="x")
        sess.add(art)
        sess.flush()
        return art

    monkeypatch.setattr(company_graph, "run_agent", lambda *a, **k: FinalReportOut(
        title="결과", executive_summary="요약", recommendation="GO", report_markdown="r", validation_plan=[],
        development_needs=needs, kpis=[]))
    monkeypatch.setattr(company_graph, "_write_artifact", keep)
    monkeypatch.setattr(company_graph.memory, "index_project", lambda *a, **k: None)
    s.commit()
    company_graph.report({"project_id": project.id})

    with session_scope() as t:
        # A new build starts one product plan for all the needs, which the owner approves before any spec.
        [plan] = t.scalars(select(Plan).where(Plan.project_id == project.id))
        assert t.scalar(select(AgentRequest.id).where(AgentRequest.project_id == project.id,
                                                      AgentRequest.type == RequestType.DEVELOPMENT.value)) is None
        assert plan.title == "수신 Worker" and plan.status == "DRAFT"
        assert [n["title"] for n in plan.need["needs"]] == ["수신 Worker", "초안 화면", "알림"]
