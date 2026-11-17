"""The owner approves exactly what they read, and a directive never quietly stops development. Each test replays a
finding of the security review of the critic-round branch (2026-11-04)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from justshipit.api.app import app
from justshipit.contracts.handoff import SpecStatus
from justshipit.contracts.planning import RequestMergeOut, RequestWithdrawOut
from justshipit.contracts.requests import (
    AgentRequestDraft, CardOption, DecisionCard, HumanDecision, HumanResponse, RequestStatus, RequestType, RiskLevel,
)
from justshipit.db.models import AgentRequest, DevJob, Job, LLMCallRecord, OwnerTask, Plan, Project, Spec
from justshipit.engineering import specs
from justshipit.i18n import msg
from justshipit.services import budget, requests, tracking
from test_one_card import asked, building_with_a_rejected_duplicate, cards, dev, draft, flyer, notes, spec_for
from ask_kit import by_task, contract, by_project_manager
from justshipit.contracts.work_path import HoldKind


def option(label: str, verdict: str) -> CardOption:
    return CardOption(label=label, verdict=verdict, summary=f"{label}의 결과", evidence_ids=[], risks=[],
                      recommended=False)


def card(*options: CardOption) -> DecisionCard:
    return DecisionCard(headline="안내 메일을 보낼까요?", owner_does="보낼지 정해 주세요", why="첫 안내예요", metrics=[],
                        options=list(options), recommendation="보내는 것을 추천해요.", reversible=False)


def email(**kw) -> AgentRequestDraft:
    mail = '{"to": ["a@example.com"], "subject": "안내", "body": "안녕하세요"}'
    return draft(**{"title": "사장님께 안내 메일", "action_kind": "external.email_send", "action_payload_json": mail,
                    **kw})


def sends(s, req) -> list[Job]:
    s.flush()
    return list(s.scalars(select(Job).where(Job.idempotency_key == f"execute_action:{req.id}")))


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


# ------------------------------------------------------------------ M1: an approve verdict on a declining option

def test_an_option_that_says_not_to_send_is_never_an_approval_whatever_the_agent_typed(s, project):
    req = requests.create(s, email(card=card(option("보내요", "approve"), option("지금은 보내지 않음", "approve"))),
                          from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))

    stored = {o["label"]: o["verdict"] for o in req.action_params["card"]["options"]}
    assert stored == {"보내요": "approve", "지금은 보내지 않음": None}
    assert {c["label"]: c["verdict"] for c in requests.choices(req)}["지금은 보내지 않음"] == "reject"
    with pytest.raises(requests.ResponseRejected):
        requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE, chosen_option="지금은 보내지 않음"))
    assert sends(s, req) == [] and req.status == RequestStatus.PENDING.value


def test_approving_a_declining_option_whose_words_say_both_is_refused_when_approval_acts(s, project):
    """"지금은 진행하지 않음" says 진행 and 않음, so its words type nothing; approving it would still send."""
    req = requests.create(s, email(card=card(option("보내요", "approve"), option("지금은 진행하지 않음", "approve"))),
                          from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))

    assert {c["label"]: c["verdict"] for c in requests.choices(req)}["지금은 진행하지 않음"] is None
    with pytest.raises(requests.ResponseRejected, match="says not to do it"):
        requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE, chosen_option="지금은 진행하지 않음"))
    requests.respond(s, req.id, HumanResponse(decision=HumanDecision.REJECT, chosen_option="지금은 진행하지 않음"))
    assert req.status == RequestStatus.REJECTED.value and sends(s, req) == []


@pytest.mark.parametrize("label", ["지금은 보내지 않음", "올리지 않음", "보류", "나중에 다시 보기", "승인 보류", "반려",
                                   "send_later", "이번 주는 건너뛰기"])
def test_the_owner_task_and_the_approval_read_a_decline_the_same_way(label):
    from justshipit.services import owner_tasks, verdicts

    assert verdicts.declines(label) and owner_tasks.declines is verdicts.declines
    assert verdicts.infer_verdict(label, question=False) in (HumanDecision.REJECT, HumanDecision.DEFER, None)


# ------------------------------------------------------------------ L5: options keyed by what was stored

def test_two_code_named_options_stay_two_distinct_choices_and_are_matched_by_key(s, project):
    req = requests.create(s, email(options=["send_now", "send_later"]), from_agent="marketing", project_id=project.id,
                          origin=by_project_manager(s, project.id))

    offered = requests.choices(req)
    assert [c["key"] for c in offered] == ["send_now", "send_later"]
    assert all(c["label"] for c in offered) and len({c["label"] for c in offered}) == 2
    with pytest.raises(requests.ResponseRejected):
        requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE, chosen_option="send_later"))
    requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE, chosen_option="send_now"))
    assert req.status == RequestStatus.APPROVED.value and req.response["chosen_option"] == "send_now"


def test_an_approval_naming_an_option_the_card_never_offered_is_refused(s, project):
    req = requests.create(s, email(options=["보내요", "미뤄요"]), from_agent="marketing", project_id=project.id,
                          origin=by_project_manager(s, project.id))

    with pytest.raises(requests.ResponseRejected, match="isn't an option on this card"):
        requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE, chosen_option="다른 사람에게 보내요"))
    assert sends(s, req) == []


# ------------------------------------------------------------------ M2: the card revised between read and approve

def _revisable(s, project, cost: float, **kw) -> AgentRequest:
    return requests.create(s, draft(title="시장 조사 패널 구매", action_kind="internal.other", estimated_cost_usd=cost,
                                    **kw), from_agent="research", project_id=project.id, origin=by_project_manager(s, project.id))


def test_an_approval_of_the_card_the_owner_read_is_refused_once_it_was_revised(s, project, api):
    first = _revisable(s, project, 5)
    s.commit()
    read = api.get(f"/api/requests/{first.id}").json()
    assert read["revision"] == 1

    again = _revisable(s, project, 500, replaces=f"REQ-{first.id}")
    s.commit()
    assert again.id == first.id and requests.revision(again) == 2

    stale = api.post(f"/api/requests/{first.id}/respond", json={"decision": "approve", "revision": 1})
    assert stale.status_code == 409 and stale.json()["detail"] == msg("request.stale", "en")
    fresh = api.post(f"/api/requests/{first.id}/respond",
                     json={"decision": "approve", "revision": 2, "confirm_text": f"REQ-{first.id}"})
    assert fresh.status_code == 200, fresh.text


def test_a_revision_never_asks_for_less_than_the_card_already_did(s, project):
    first = _revisable(s, project, 500)
    _revisable(s, project, 5, replaces=f"REQ-{first.id}")

    assert first.estimated_cost_usd == 500


def test_a_revision_without_a_card_drops_the_card_it_replaces(s, project):
    first = _revisable(s, project, 5, card=card(option("사요", "approve"), option("사지 않음", "reject")))
    _revisable(s, project, 5, replaces=f"REQ-{first.id}")

    assert "card" not in first.action_params
    assert requests.report(s, first, "en")["headline"] == "시장 조사 패널 구매"


# ------------------------------------------------------------------ M3: the one-click strong approval

@pytest.mark.parametrize("cost, post, typed", [(0, True, False), (None, True, False), (12, True, True), (0, False, True)])
def test_only_a_free_post_the_owner_publishes_by_hand_skips_the_typed_id(s, project, cost, post, typed):
    """A cost left out is no money spent: the owner posts it by hand (persona round 3, REQ-88)."""
    kw = {"estimated_cost_usd": cost}
    req = (requests.create(s, flyer("토요일 전단", **kw), from_agent="marketing", project_id=project.id,
                           origin=by_project_manager(s, project.id)) if post else
           requests.create(s, draft(title="동네 가게 방문", action_kind="external.other", risk_level=RiskLevel.LOW, **kw),
                           from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id)))

    assert req.policy_mode == "strong_approve"
    assert requests.typed_confirm(req) is typed


# ------------------------------------------------------------------ M4: a withdrawn spec card

def test_neither_the_project_manager_cleanup_nor_a_directive_withdraws_a_spec_card(s, project, monkeypatch):
    from justshipit.contracts.planning import DirectiveRuleOut, RealignOut
    from justshipit.db.session import session_scope
    from justshipit.orchestration import realign
    from justshipit.services import control

    first = requests.create(s, dev("견적봇 MVP"), from_agent="product", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    spec = spec_for(s, project, first)
    other = requests.create(s, draft(title="견적봇 명세 검토 메모"), from_agent="project_manager", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    s.commit()

    done = requests.consolidate(s, project.id, [RequestMergeOut(keep_id=other.id, merge_ids=[spec.approval_request_id],
                                                                merged_title="t", reason="r")],
                                [RequestWithdrawOut(id=spec.approval_request_id, reason="최종 결정으로 불필요")])
    assert done == {"merged": [], "withdrawn": []}
    s.commit()

    with session_scope() as x:
        n = control.add_directive(x, project.id, "명세 승인은 다음에 하겠습니다")["n"]
    monkeypatch.setattr(realign, "run_agent", lambda *a, **k: RealignOut(
        assessment="a", goal_interpretation="", out_of_scope_additions=[], cancel_task_ids=[], revise_tasks=[],
        new_tasks=[], withdraw_requests=[RequestWithdrawOut(id=spec.approval_request_id, reason="r")],
        directive_rules=[DirectiveRuleOut(label="명세 승인", keywords=["명세", "승인"])]))
    project_row = s.get(Project, project.id)
    project_row.status = "RUNNING"
    s.commit()
    realign.realign(project.id, directive=n)

    s.expire_all()
    assert s.get(AgentRequest, spec.approval_request_id).status == RequestStatus.PENDING.value


def _withdrawn_spec(s, project):
    """What a cleanup or a directive left before spec cards were spared: the spec in review, its card withdrawn."""
    first = asked(s, project, "견적봇 MVP")
    spec = spec_for(s, project, first)
    old = s.get(AgentRequest, spec.approval_request_id)
    old.status, old.response = RequestStatus.CANCELLED.value, {"decision": "withdrawn", "by": "project_manager", "note": "r"}
    s.flush()
    return first, spec, old


def test_a_development_ask_after_a_spec_card_was_withdrawn_asks_for_that_spec_again(s, project):
    first, spec, old = _withdrawn_spec(s, project)

    requests.create(s, dev("견적봇 MVP 착수"), from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))

    [again] = cards(s, project)
    assert again.id != old.id and again.action_params["spec_id"] == spec.id and spec.approval_request_id == again.id
    assert "withdrawn spec card" in notes(s, project, "request_folded")[-1].summary


def test_spec_writing_for_a_product_whose_spec_card_was_withdrawn_asks_for_that_spec_again(s, project, monkeypatch):
    first, spec, old = _withdrawn_spec(s, project)
    ask = AgentRequest(project_id=project.id, from_agent="product", type="DEVELOPMENT", title="v1 again", summary="s",
                       rationale="r", action_kind="dev.implementation_start", policy_mode="auto", status="APPROVED")
    s.add(ask)
    s.commit()
    monkeypatch.setattr(specs, "run_agent", lambda *a, **k: pytest.fail("the spec in review is asked again, not rewritten"))

    assert specs.write_spec(project.id, ask.id) == spec.id

    s.expire_all()
    renewed = s.get(AgentRequest, s.get(Spec, spec.id).approval_request_id)
    assert renewed.id != old.id and renewed.status == RequestStatus.PENDING.value


def test_the_note_says_development_is_underway_only_while_a_dev_job_runs(s, project):
    first = asked(s, project, "원두재고 MVP")
    spec = spec_for(s, project, first, status=SpecStatus.READY_FOR_DEV)
    requests.respond(s, spec.approval_request_id, HumanResponse(decision=HumanDecision.APPROVE))
    job = DevJob(spec_id=spec.id, status="FAILED", attempt=1)
    s.add(job)
    s.flush()

    requests.create(s, dev("원두재고 정본 표 복구"), from_agent="product", project_id=project.id, origin=by_project_manager(s, project.id))
    idle = notes(s, project, "request_folded")[-1].summary
    job.status = "IMPLEMENTING"
    requests.create(s, dev("원두재고 배포"), from_agent="product", project_id=project.id, origin=by_project_manager(s, project.id))
    busy = notes(s, project, "request_folded")[-1].summary

    assert "Building from" not in idle and "with no build running now" in idle
    assert "Building from" in busy


# ------------------------------------------------------------------ M5: the development share of the budget

def _spend(s, project, purpose: str, usd: float) -> None:
    s.add(LLMCallRecord(call_id=f"c-{project.id}-{purpose}-{usd}", project_id=project.id, agent="product",
                        purpose=purpose, provider="claude", status="ok", cost_usd=usd))
    s.flush()


def test_the_share_kept_for_development_shrinks_by_what_development_spent(s, project):
    first = requests.create(s, dev("견적봇 MVP"), from_agent="product", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    assert budget.dev_hold(s, project.id) == budget.dev_need_usd(), "its product plan is being written"

    _spend(s, project, "spec", 3.0)
    _spend(s, project, "work", 2.0)

    assert budget.dev_hold(s, project.id) == pytest.approx(budget.dev_need_usd() - 3.0)
    assert isinstance(first, Plan)


def test_a_rejected_spec_keeps_nothing_back_and_the_next_ask_drafts_a_new_spec(s, project):
    first = asked(s, project, "원두재고 MVP")
    spec = spec_for(s, project, first)
    assert budget.dev_hold(s, project.id) == budget.dev_need_usd()
    requests.respond(s, spec.approval_request_id, HumanResponse(decision=HumanDecision.REJECT, note="범위가 너무 넓어요"))

    assert budget.dev_hold(s, project.id) == 0.0
    later = requests.create(s, dev("원두재고 MVP 범위 축소안"), from_agent="product", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    assert isinstance(later, Plan), "the new spec starts from a new product plan"
    assert budget.dev_hold(s, project.id) == budget.dev_need_usd()


@pytest.mark.parametrize("job_status", ["QUEUED", "IMPLEMENTING"])
def test_a_rejected_later_spec_leaves_the_share_of_the_build_under_way(s, project, job_status):
    """원두재고: rejecting a duplicate spec released the development share while J-1 was building BUSINE-0001."""
    building_with_a_rejected_duplicate(s, project, job_status)

    assert budget.dev_hold(s, project.id) == budget.dev_need_usd()


def test_spec_writing_that_ended_without_a_spec_keeps_nothing_back_and_takes_a_new_ask(s, project):
    first = asked(s, project, "견적봇 MVP")
    s.flush()
    job = s.scalar(select(Job).where(Job.idempotency_key == f"write_spec:{first.id}"))
    job.status = "dead"
    s.flush()

    assert budget.dev_hold(s, project.id) == 0.0
    again = requests.create(s, dev("견적봇 MVP 다시"), from_agent="product", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    assert isinstance(again, Plan)


# ------------------------------------------------------------------ L1: a different post after the approval

def test_a_new_text_for_an_approved_post_is_a_new_decision_not_an_update_on_its_task(s, project):
    first = requests.create(s, flyer("토요일 현장 전단"), from_agent="marketing", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    requests.respond(s, first.id, HumanResponse(decision=HumanDecision.APPROVE, confirm_text=f"REQ-{first.id}"))
    [task] = s.scalars(select(OwnerTask).where(OwnerTask.request_id == first.id))

    same = requests.create(s, flyer("토요일 현장 전단", replaces=f"REQ-{first.id}"), from_agent="marketing",
                           project_id=project.id, origin=by_project_manager(s, project.id))
    reworded = flyer("토요일 현장 전단", replaces=f"REQ-{first.id}")
    reworded.post_card.what.body = "할인 쿠폰을 드려요"
    other = requests.create(s, reworded, from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))

    assert same.id == first.id and len(task.card["updates"]) == 1
    assert other.id != first.id and other.status == RequestStatus.PENDING.value
    assert other.action_params["post_card"]["what"]["body"] == "할인 쿠폰을 드려요"


# ------------------------------------------------------------------ L2: replaces lands on the matter's head

def test_replaces_never_rewrites_a_later_card_of_the_matter_the_author_may_not_touch(s, project):
    mine = requests.create(s, flyer("토요일 현장 전단"), from_agent="marketing", project_id=project.id,
                           origin=by_project_manager(s, project.id))
    requests.respond(s, mine.id, HumanResponse(decision=HumanDecision.DEFER))
    theirs = requests.create(s, flyer("토요일 현장 전단"), from_agent="project_manager", project_id=project.id,
                             origin=by_project_manager(s, project.id))
    assert theirs.id != mine.id and theirs.action_params["matter"] == mine.action_params["matter"]

    mine_again = requests.create(s, flyer("토요일 현장 전단 고친 안", replaces=f"REQ-{mine.id}"), from_agent="marketing",
                                 project_id=project.id, origin=by_project_manager(s, project.id))

    assert theirs.title == "토요일 현장 전단", "the ceo's card is not rewritten by another author"
    assert mine_again.id != theirs.id


def test_replacing_a_blocking_card_keeps_the_waiting_project_waiting_on_the_new_one(s, project):
    mail = '{"to": ["a@example.com"], "subject": "안내", "body": "%s"}'
    first = requests.create(s, email(context=contract(HoldKind.THIS_TASK), action_payload_json=mail % "첫 안"), from_agent="marketing",
                            project_id=project.id, origin=by_task(s, project.id))
    s.get(Project, project.id).status = "WAITING_HUMAN"
    again = requests.create(s, email(title="사장님께 안내 메일 고친 안", action_payload_json=mail % "고친 안",
                                     replaces=f"REQ-{first.id}"), from_agent="marketing", project_id=project.id,
                                     origin=by_project_manager(s, project.id))

    assert again.id != first.id and again.blocking
    requests.respond(s, again.id, HumanResponse(decision=HumanDecision.REJECT))
    s.flush()
    assert s.scalar(select(Job).where(Job.kind == "resume_project", Job.payload["project_id"].as_integer() == project.id))


# ------------------------------------------------------------------ L3: a task in review across the card change

OLD_CARD = {"question": "이 전단을 붙일까요?", "metrics": [], "recommendation": "붙이는 것을 추천해요",
            "on_approve": "오너 할 일이 생겨요", "reversible": True,
            "options": [{"label": "붙여요", "summary": "s", "evidence_ids": [], "risks": [], "recommended": True},
                        {"label": "보류", "summary": "s", "evidence_ids": [], "risks": [], "recommended": False}]}


def test_a_task_reviewed_after_the_upgrade_raises_its_old_format_requests(s, project):
    from justshipit.contracts.work import TaskStatus
    from justshipit.db.models import Task
    from justshipit.orchestration import company_graph

    stored = flyer("토요일 현장 전단").model_dump(mode="json")
    stored["card"] = OLD_CARD
    t = Task(project_id=project.id, key="flyer", title="전단", owner="marketing", objective="o",
             acceptance_criteria=["c"], status=TaskStatus.REVIEW.value,
             result={"summary": "s", "requests": [stored]})
    s.add(t)
    s.flush()

    company_graph._finish(s, s.get(Project, project.id), t, company_graph.PASSED, "ko")
    company_graph.release_held(s, s.get(Project, project.id))   # the round settles

    [req] = cards(s, project)
    assert req.action_params["card"]["question"] == "이 전단을 붙일까요?"
    assert requests.report(s, req, "en")["headline"] == "이 전단을 붙일까요?"
    assert [c["verdict"] for c in requests.choices(req)] == [None, "defer"]


# ------------------------------------------------------------------ L4: an old development request

def test_approving_an_old_development_request_still_starts_the_spec(s, project):
    old = AgentRequest(project_id=project.id, from_agent="product", type="DEVELOPMENT", title="견적봇 MVP", summary="s",
                       rationale="r", action_kind="internal.other", policy_mode="approve", status="PENDING")
    s.add(old)
    s.flush()

    requests.respond(s, old.id, HumanResponse(decision=HumanDecision.APPROVE))

    assert s.scalar(select(Job).where(Job.idempotency_key == f"write_spec:{old.id}")) is not None


# ------------------------------------------------------------------ the review of this branch (opus, 2026-11-04)

def test_approving_a_declining_option_of_an_escalation_that_sends_is_refused_too(s, project):
    """Only a question's approval records an answer; an escalation's approval sends the mail all the same."""
    req = requests.create(s, email(type=RequestType.ESCALATION,
                                   card=card(option("보내요", "approve"), option("지금은 진행하지 않음", "approve"))),
                          from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))

    with pytest.raises(requests.ResponseRejected, match="says not to do it"):
        requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE, chosen_option="지금은 진행하지 않음"))
    assert sends(s, req) == [] and req.status == RequestStatus.PENDING.value


@pytest.mark.parametrize("label", ["경쟁사 제외하고 게시", "광고 중단 공지 메일 발송", "재검토 요청 메일 보내기",
                                   "No-code 툴 구매", "예약 취소 안내 메일 발송"])
def test_an_option_that_acts_while_naming_a_negative_word_is_not_a_decline(label):
    from justshipit.services import verdicts

    assert not verdicts.declines(label)


def test_an_approved_post_sent_to_another_place_is_a_new_decision(s, project):
    first = requests.create(s, flyer("토요일 현장 전단"), from_agent="marketing", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    requests.respond(s, first.id, HumanResponse(decision=HumanDecision.APPROVE, confirm_text=f"REQ-{first.id}"))
    tracking.set_doors(s, project.business_id, [{"kind": "site", "url": "https://cafe.example.com/"}])
    moved = flyer("토요일 현장 전단", replaces=f"REQ-{first.id}")
    moved.post_card.where_url = "https://cafe.example.com/board"

    other = requests.create(s, moved, from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))

    assert other.id != first.id and other.status == RequestStatus.PENDING.value


def test_an_approval_that_names_no_revision_is_refused_by_the_api(s, project, api):
    req = _revisable(s, project, 5)
    s.commit()

    blind = api.post(f"/api/requests/{req.id}/respond", json={"decision": "approve"})
    assert blind.status_code == 409
    s.expire_all()
    assert s.get(AgentRequest, req.id).status == RequestStatus.PENDING.value


def test_a_merge_into_a_card_makes_an_earlier_read_of_it_stale(s, project):
    keep = _revisable(s, project, 5)
    dup = requests.create(s, draft(title="세그먼트 검증 인터뷰 모집", action_kind="external.interview_recruit"),
                          from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    assert dup.id != keep.id

    requests.consolidate(s, project.id, [RequestMergeOut(keep_id=keep.id, merge_ids=[dup.id], merged_title="패널 구매",
                                                         reason="같은 결정")], [])

    assert requests.revision(keep) == 2
    with pytest.raises(requests.ResponseRejected, match="The card changed"):
        requests.respond(s, keep.id, HumanResponse(decision=HumanDecision.APPROVE, revision=1))
