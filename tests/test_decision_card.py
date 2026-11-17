"""The decision card reads like a report to the owner: the conclusion, what the owner does and why first, then what
approving does and how hard that is to take back, as the server knows it, then the typed options."""
import re

import pytest
from justshipit.api.app import app
from justshipit.contracts.requests import (
    AgentRequestDraft, CardMetric, CardOption, DecisionCard, HumanDecision, HumanResponse, PostEdit, RequestStatus,
    RequestType, RiskLevel,
)
from justshipit.contracts.schema import strict_json_schema
from justshipit.db.models import AgentRequest, Evidence, Job, OwnerTask
from justshipit.i18n import msg
from justshipit.services import requests
from fastapi.testclient import TestClient
from owner_task_kit import LANDING_CARD
from sqlalchemy import select
from ask_kit import contract, by_project_manager
from justshipit.contracts.work_path import HoldKind


def option(label: str, verdict: str, **kw) -> CardOption:
    return CardOption(**{**dict(label=label, verdict=verdict, summary=f"{label}의 결과", evidence_ids=[], risks=[],
                                recommended=False), **kw})


def card(**kw) -> DecisionCard:
    base = dict(
        headline="이메일 10통을 보낼까요?", owner_does="보낼지 정해 주세요", why="동의한 사람에게만 가는 첫 메일이에요",
        metrics=[CardMetric(label="수신 동의", value="10명 중 10명", basis="CRM 기록")],
        options=[option("보내요", "approve", risks=["답이 없을 수 있음"], recommended=True), option("미뤄요", "defer")],
        recommendation="보내는 것을 추천해요. 동의한 사람에게만 가는 첫 메일이에요.", reversible=True)
    return DecisionCard(**{**base, **kw})


def draft(**kw) -> AgentRequestDraft:
    base = dict(type=RequestType.APPROVAL, title="첫 메일", summary="s", rationale="r", options=[],
                recommended_option=None, action_kind="external.email_send", estimated_cost_usd=None,
                risk_level=RiskLevel.LOW, context=contract())
    return AgentRequestDraft(**{**base, **kw})


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def read(api, req) -> dict:
    r = api.get(f"/api/requests/{req.id}")
    assert r.status_code == 200, r.text
    return r.json()


def report(api, req) -> dict:
    return read(api, req)["report"]


def test_the_card_the_agents_fill_is_a_strict_schema_with_the_report_fields_first():
    def check(node, path="card"):
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
    assert "card" in schema["required"]
    check(schema["properties"]["card"])
    props = schema["properties"]["card"]["anyOf"][0]["properties"]
    assert list(props)[:3] == ["headline", "owner_does", "why"]
    assert {"question", "on_approve"}.isdisjoint(props), "the server says what approving does, not the agent"
    option_props = props["options"]["items"]["properties"]
    assert set(option_props) == {"label", "verdict", "summary", "evidence_ids", "risks", "recommended", "line"}
    assert option_props["verdict"]["enum"] == ["approve", "reject", "defer", "answer"]


def test_a_request_without_a_card_is_still_a_valid_draft():
    assert draft().card is None


def test_the_card_is_stored_and_its_typed_options_are_the_requests_choices(s, project):
    req = requests.create(s, draft(card=card()), from_agent="marketing", project_id=project.id,
                          origin=by_project_manager(s, project.id))
    s.commit()

    stored = s.get(AgentRequest, req.id).action_params["card"]
    assert stored["headline"] == "이메일 10통을 보낼까요?" and stored["why"].startswith("동의한 사람")
    assert stored["metrics"] == [{"label": "수신 동의", "value": "10명 중 10명", "basis": "CRM 기록"}]
    assert [(o["label"], o["verdict"]) for o in stored["options"]] == [("보내요", "approve"), ("미뤄요", "defer")]
    assert req.options == ["보내요", "미뤄요"] and req.recommended_option == "보내요", "one set of choices, not two"


def test_the_badge_a_project_manager_tacks_on_is_cut_from_an_options_label():
    assert option("보내요 (프로젝트 매니저 추천)", "approve").label == "보내요"
    assert option("Post (Project Manager's pick)", "approve").label == "Post"


def test_internal_ids_and_names_never_reach_the_lines_the_owner_reads_first(s, project, api):
    leaky = card(headline="**보호자 채널**(지시서 l-972536, l-c4668e)에 올릴까요? EV-12",
                 why="open_questions의 자문 후보 7개, 판정 CONDITIONAL-GO (contested=False)",
                 owner_does="pstack:architect 검토 뒤 registry/projects/p/x.md를 보세요",
                 options=[option("승인 (추천)", "approve", recommended=True), option("반려", "reject")])
    req = requests.create(s, draft(card=leaky, title="게시 l-d4bdf1 확인 a1b2c3d4e5"), from_agent="marketing",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()

    body = report(api, req)

    assert body["headline"] == "보호자 채널에 올릴까요?"
    assert body["why"] == "미해결 질문의 자문 후보 7개, 판정 조건부 진행"
    assert "pstack" not in body["owner_does"] and "registry" not in body["owner_does"]
    assert read(api, req)["title"] == "게시 확인"
    assert [c["label"] for c in body["choices"]] == ["승인", "반려"], "the badge says recommended, not the label"


def test_the_lines_read_first_are_short_and_cut_where_a_reader_would_stop(s, project):
    long = card(headline="가" * 200, why="나" * 80 + ". " + "다" * 200, owner_does="라 " * 200)
    stored = requests.create(s, draft(card=long), from_agent="marketing", project_id=project.id,
                             origin=by_project_manager(s, project.id)).action_params["card"]

    assert len(stored["headline"]) <= 70 and stored["headline"].endswith("…")
    assert stored["why"] == "나" * 80 + "."
    assert len(stored["owner_does"]) <= 90


def test_a_number_nobody_knows_is_left_off_unless_the_card_says_why(s, project):
    c = card(metrics=[CardMetric(label="비용", value="모름", basis=""),
                      CardMetric(label="답 받기까지", value="모름", basis="구청이 아직 답하지 않았어요")])
    stored = requests.create(s, draft(card=c), from_agent="marketing", project_id=project.id,
                             origin=by_project_manager(s, project.id)).action_params["card"]

    assert [m["label"] for m in stored["metrics"]] == ["답 받기까지"]


def test_the_server_keeps_only_evidence_ids_that_exist_and_one_recommendation(s, project):
    real = Evidence(project_id=project.id, agent="research", claim="수신 동의 10명", kind="sourced_claim")
    s.add(real)
    s.flush()
    options = [option("a", "approve", evidence_ids=[f"EV-{real.id}", "EV-99999999", "근거 없음"], recommended=True),
               option("b", "reject", evidence_ids=[f"ev-{real.id}"], recommended=True)]

    req = requests.create(s, draft(card=card(options=options)), from_agent="marketing", project_id=project.id,
                          origin=by_project_manager(s, project.id))

    stored = req.action_params["card"]["options"]
    assert [o["evidence_ids"] for o in stored] == [[f"EV-{real.id}"], [f"EV-{real.id}"]]
    assert [o["recommended"] for o in stored] == [True, False]


def test_a_card_without_a_project_keeps_no_evidence_ids(s, project):
    real = Evidence(project_id=project.id, agent="research", claim="수신 동의 10명", kind="sourced_claim")
    s.add(real)
    s.flush()
    options = [option("a", "approve", evidence_ids=[f"EV-{real.id}"], recommended=True)]

    req = requests.create(s, draft(action_kind=None, type=RequestType.PROPOSAL, card=card(options=options)),
                          from_agent="project_manager", project_id=None, origin=by_project_manager(s, None))

    assert [o["evidence_ids"] for o in req.action_params["card"]["options"]] == [[]]


def test_a_questions_yes_is_an_answer_and_any_other_requests_answer_is_an_approval(s, project):
    q = requests.create(s, draft(type=RequestType.QUESTION, action_kind=None, card=card(
        options=[option("A안", "approve"), option("B안", "answer")])), from_agent="project_manager", project_id=project.id,
        origin=by_project_manager(s, project.id))
    a = requests.create(s, draft(card=card(options=[option("보내요", "answer")])), from_agent="project_manager",
                        project_id=project.id, dedupe=False, origin=by_project_manager(s, project.id))

    assert [o["verdict"] for o in q.action_params["card"]["options"]] == ["answer", "answer"]
    assert [o["verdict"] for o in a.action_params["card"]["options"]] == ["approve"]


def test_a_choice_cannot_be_sent_with_a_decision_it_is_not(s, project):
    req = requests.create(s, draft(type=RequestType.PROPOSAL, action_kind=None, card=card(
        options=[option("올려요", "approve"), option("올리지 않아요", "reject")])), from_agent="marketing",
        project_id=project.id, origin=by_project_manager(s, project.id))

    with pytest.raises(requests.ResponseRejected, match="isn't the decision you chose"):
        requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE, chosen_option="올리지 않아요"))

    requests.respond(s, req.id, HumanResponse(decision=HumanDecision.REJECT, chosen_option="올리지 않아요"))
    assert req.status == RequestStatus.REJECTED.value


def test_what_approving_does_is_the_servers_answer_before_the_decision(s, project, api):
    req = requests.create(s, draft(type=RequestType.APPROVAL, action_kind="dev.merge", card=card(reversible=True)),
                          from_agent="engineer", project_id=project.id, system=True,
                          action_params={"dev_job_id": 4242}, origin=by_project_manager(s, project.id))
    s.commit()

    body = read(api, req)

    assert body["status"] == "PENDING" and body["response"] is None
    assert msg("request.effect.dev_merge", "en") in body["report"]["on_approve"]
    assert body["report"]["undo"] == {"level": "hard", "says": msg("request.undo.hard", "en"),
                                      "how": msg("request.effect.dev_merge.undo", "en"), "by": "server"}
    assert body["card"]["reversible"] is True, "the agent's own claim stays as its narrative"


def test_a_send_says_whether_it_leaves_the_building(s, project, api, email_live):
    live = requests.create(s, draft(card=card()), from_agent="marketing", project_id=project.id,
                           origin=by_project_manager(s, project.id))
    s.commit()
    body = report(api, live)
    assert msg("request.effect.sends.live", "en") in body["on_approve"] and body["undo"]["level"] == "none"


def test_a_send_in_the_outbox_stays_inside_and_is_easy_to_take_back(s, project, api):
    req = requests.create(s, draft(), from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    body = report(api, req)
    assert msg("request.effect.sends.outbox", "en") in body["on_approve"]
    assert body["undo"]["level"] == "easy"


def test_a_question_says_the_answer_is_recorded_and_a_blocked_project_says_it_restarts(s, project, api):
    project.status = "WAITING_HUMAN"
    ask = requests.create(s, draft(type=RequestType.QUESTION, action_kind=None, context=contract(HoldKind.RUN), title="어느 쪽?"),
                          from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()

    body = report(api, ask)
    said = body["on_approve"]

    assert msg("request.effect.answered", "en") in said and msg("request.effect.resumes", "en", id=project.id) in said
    assert body["undo"]["how"] == msg("request.effect.answered.undo", "en"), "the correction has a real place"


def test_a_company_wide_answer_claims_no_way_to_undo_it(s, api):
    ask = requests.create(s, draft(type=RequestType.QUESTION, action_kind=None, title="회사 전체 질문"),
                          from_agent="project_manager", project_id=None, origin=by_project_manager(s, None))
    s.commit()

    assert report(api, ask)["undo"] is None


def test_a_post_says_it_becomes_a_task_and_that_the_task_can_still_be_skipped(s, project, api):
    note = requests.create(s, draft(type=RequestType.PROPOSAL, action_kind=None, title="제안"), from_agent="strategy",
                           project_id=project.id, origin=by_project_manager(s, project.id))
    human = requests.create(s, draft(action_kind="external.landing_publish", title="랜딩 공개", post_card=LANDING_CARD),
                            from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()

    assert msg("request.effect.record", "en") in report(api, note)["on_approve"]
    body = report(api, human)
    assert body["on_approve"].startswith(msg("request.effect.owner_task.publish", "en"))
    assert body["undo"] == {"level": "easy", "says": msg("request.undo.easy", "en"),
                            "how": msg("request.effect.owner_task.undo", "en"), "by": "server"}
    assert body["post"]["what"]["body"] == "랜딩 초안", "the post itself is the card's content"


def test_writing_a_spec_is_easy_to_take_back_and_says_the_build_needs_one_more_approval(s, project, api):
    # A new development ask starts a product plan (test_plans.py); a card filed before plans still says this.
    req = AgentRequest(project_id=project.id, from_agent="project_manager", type="DEVELOPMENT", title="견적 답장 MVP", summary="s",
                       rationale="r", status="PENDING", policy_mode="approve", action_kind="dev.implementation_start",
                       action_params={})
    s.add(req)
    s.commit()

    body = report(api, req)

    assert body["on_approve"].startswith(msg("request.effect.write_spec", "en"))
    assert body["undo"]["level"] == "easy", "C3: a spec is not a one-way door"


@pytest.mark.parametrize("kind,params,level", [
    ("dev.spec_approve", {"spec_id": 4242}, "hard"),
    ("project.reassess", {}, "easy"),
    ("skill.activate_knowledge", {"skill_id": 4242}, "hard"),
    ("internal.task_create", {}, "easy"),
])
def test_how_hard_approving_is_to_take_back_comes_from_what_the_server_will_do(s, project, api, kind, params, level):
    req = requests.create(s, draft(action_kind=kind, card=card(reversible=level != "easy")), from_agent="engineer",
                          project_id=project.id, system=True, action_params=params, dedupe=False,
                          origin=by_project_manager(s, project.id))
    s.commit()

    undo = report(api, req)["undo"]

    assert undo["level"] == level and undo["by"] == "server"


@pytest.mark.parametrize("kind", ["internal.other", "made.up"])
def test_an_action_the_server_does_not_know_leaves_reversibility_to_the_card_marked_as_the_agents(
        s, project, api, kind):
    req = requests.create(s, draft(action_kind=kind, card=card(reversible=False)), from_agent="research",
                          project_id=project.id, dedupe=False, origin=by_project_manager(s, project.id))
    s.commit()

    assert report(api, req)["undo"] == {"level": "none", "says": msg("request.undo.none", "en"), "how": None,
                                          "by": "agent"}


def test_an_old_request_with_no_card_and_no_action_has_no_reversibility_to_claim(s, project, api):
    old = AgentRequest(project_id=project.id, from_agent="project_manager", type="PROPOSAL", title="옛 제안", summary="s",
                       rationale="r", options=[], action_params={}, risk_level="low",
                       policy_mode="approve", status="PENDING")
    s.add(old)
    s.commit()

    assert report(api, old)["undo"] is None


def test_an_old_request_with_no_card_still_reads_as_a_report(s, project, api):
    old = AgentRequest(project_id=project.id, from_agent="research", type="APPROVAL", title="옛 요청",
                       summary="조사 초안을 써요. 나머지 설명.", rationale="r", options=["예", "승인 보류"],
                       recommended_option="예", action_kind="internal.report_draft", action_params={}, risk_level="low",
                       policy_mode="approve", status="PENDING")
    s.add(old)
    s.commit()

    body = read(api, old)

    assert body["card"] is None and body["options"] == ["예", "승인 보류"]
    assert body["report"]["headline"] == "옛 요청" and body["report"]["why"] == "조사 초안을 써요."
    assert [(c["label"], c["verdict"], c["recommended"]) for c in body["report"]["choices"]] == [
        ("예", None, True), ("승인 보류", "defer", False)], "an untyped option is never read as a yes"
    assert old.id in {r["id"] for r in api.get("/api/inbox").json()}
    assert old.id in {r["id"] for r in api.get(f"/api/projects/{project.id}").json()["requests"]}


def test_a_card_stored_before_the_report_fields_reads_into_the_same_shape(s, project, api):
    legacy = {"question": "W1 월요일에 이 글을 올릴까요?", "metrics": [], "on_approve": "글이 올라갑니다", "reversible": True,
              "recommendation": "승인을 권합니다. 지시서가 꼽은 채널입니다.",
              "options": [{"label": "올림", "summary": "올립니다", "evidence_ids": [], "risks": [], "recommended": True},
                          {"label": "올리지 않음", "summary": "안 올립니다", "evidence_ids": [], "risks": [],
                           "recommended": False}]}
    old = AgentRequest(project_id=project.id, from_agent="marketing", type="APPROVAL", title="[게시] 첫 글", summary="s",
                       rationale="r", options=["올림", "내용 수정 후 올림", "올리지 않음"], recommended_option="올림",
                       action_kind=None, action_params={"card": legacy}, risk_level="medium",
                       policy_mode="approve", status="PENDING")
    s.add(old)
    s.commit()

    body = report(api, old)

    assert body["headline"] == "W1 월요일에 이 글을 올릴까요?" and body["why"] == "승인을 권합니다."
    assert [(c["label"], c["verdict"]) for c in body["choices"]] == [("올림", "approve"), ("올리지 않음", "reject")]
    assert body["undo"]["by"] == "agent" and body["owner_does"] == msg("request.owner_does.decide_only", "en")
    assert body["on_approve"].startswith(msg("request.effect.record", "en")), "nothing runs, so nothing says it does"


def test_a_request_whose_card_names_no_owner_part_still_says_what_the_owner_does(s, project, api):
    def old(**kw) -> AgentRequest:
        req = AgentRequest(**{**dict(project_id=project.id, from_agent="strategy", type="APPROVAL", summary="s",
                                     rationale="r", options=[], recommended_option=None, action_kind=None,
                                     action_params={}, risk_level="low", policy_mode="approve",
                                     status="PENDING"), **kw})
        s.add(req)
        s.commit()
        return req

    direction = old(title="의사결정 승인: 원두재고 웹앱: v1.4로 바로 구현", action_kind="project.direction_change")
    post = old(title="[게시] 첫 글", action_kind="external.social_post")
    question = old(title="어느 채널부터?", type="QUESTION", options=["당근", "카페"])

    assert report(api, direction)["headline"] == "원두재고 웹앱: v1.4로 바로 구현"
    spend = old(title="데이터셋 구입", action_kind="internal.other", estimated_cost_usd=12)
    assert report(api, direction)["owner_does"] == msg("request.owner_does.decide_only", "en")
    said = report(api, spend)
    # the company pays for nothing by hand: money on an action with no rule is the owner's payment to-do (Q15)
    assert said["owner_does"] == msg("request.owner_does.spend", "en"), "F11: no company work, no 'carries it out'"
    assert said["on_approve"].startswith(msg("request.effect.owner_task.spend", "en"))
    assert report(api, question)["owner_does"] == msg("request.owner_does.pick", "en")
    assert report(api, post)["owner_does"] == msg("request.owner_does.post", "en")


def test_once_decided_the_request_no_longer_describes_an_approval(s, project, api):
    req = requests.create(s, draft(type=RequestType.PROPOSAL, action_kind=None, card=card()), from_agent="strategy",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()

    r = api.post(f"/api/requests/{req.id}/respond", json={"decision": "approve", "chosen_option": "보내요", "revision": 1})

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVED" and r.json()["report"]["on_approve"] is None
    assert r.json()["report"]["headline"] == "이메일 10통을 보낼까요?", "the report stays with the record"


def test_a_strong_approval_that_only_makes_an_owner_task_at_low_risk_is_one_click(s, project, api):
    low = requests.create(s, draft(action_kind="external.other", title="전단 문구", post_card=LANDING_CARD,
                                   estimated_cost_usd=0), from_agent="marketing", project_id=project.id,
                                   origin=by_project_manager(s, project.id))
    high = requests.create(s, draft(action_kind="external.other", title="계약 문구", risk_level=RiskLevel.HIGH,
                                    post_card=LANDING_CARD), from_agent="marketing", project_id=project.id, dedupe=False,
                                    origin=by_project_manager(s, project.id))
    merge = requests.create(s, draft(action_kind="dev.merge"), from_agent="engineer", project_id=project.id,
                            system=True, action_params={"dev_job_id": 4242}, origin=by_project_manager(s, project.id))
    s.commit()
    assert {low.policy_mode, high.policy_mode, merge.policy_mode} == {"strong_approve"}

    assert [report(api, r)["typed_confirm"] for r in (low, high, merge)] == [False, True, True]
    requests.respond(s, low.id, HumanResponse(decision=HumanDecision.APPROVE))
    assert low.status == RequestStatus.APPROVED.value
    with pytest.raises(requests.ResponseRejected, match="needs one more check"):
        requests.respond(s, high.id, HumanResponse(decision=HumanDecision.APPROVE))


def test_the_owners_rewrite_of_a_post_is_what_the_owner_task_copies(s, project):
    """C2: the owner rewrote the post in the card and approved it, and the task's copy text was still the agent's."""
    req = requests.create(s, draft(action_kind="external.landing_publish", title="랜딩 공개", post_card=LANDING_CARD),
                          from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))

    requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE,
                                              post_edit=PostEdit(title="랜딩 공개", body="오너가 고친 본문")))
    s.commit()

    [task] = s.scalars(select(OwnerTask).where(OwnerTask.request_id == req.id))
    assert task.card["what"]["body"] == "오너가 고친 본문" and task.card["edited_by_owner"] is True
    assert task.card["agent_draft"]["body"] == "랜딩 초안"
    assert req.response["post_edit"] == {"title": "랜딩 공개", "body": "오너가 고친 본문"}


def test_an_unchanged_or_rejected_rewrite_is_not_kept_and_one_with_no_post_is_refused(s, project):
    same = requests.create(s, draft(action_kind="external.landing_publish", title="랜딩 공개", post_card=LANDING_CARD),
                           from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    requests.respond(s, same.id, HumanResponse(decision=HumanDecision.APPROVE, post_edit=PostEdit(body="랜딩 초안")))
    assert same.response["post_edit"] is None

    plain = requests.create(s, draft(), from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    with pytest.raises(requests.ResponseRejected, match="no draft post"):
        requests.respond(s, plain.id, HumanResponse(decision=HumanDecision.APPROVE, post_edit=PostEdit(body="x")))


CASES = [
    ("dev.merge", RequestType.APPROVAL, {"dev_job_id": 4242}, "dev_merge", True),
    ("dev.spec_approve", RequestType.APPROVAL, {"spec_id": 4242}, "spec_approved", True),
    ("external.email_send", RequestType.APPROVAL, {}, "execute_action", False),
    ("project.reassess", RequestType.APPROVAL, {}, "reassess_project", True),
    ("skill.activate_knowledge", RequestType.SKILL, {"skill_id": 4242}, "skill_activate", True),
]


@pytest.mark.parametrize("kind,type_,params,job,system", CASES)
def test_the_description_and_the_dispatch_come_from_the_same_place(s, project, api, kind, type_, params, job, system):
    req = requests.create(s, draft(action_kind=kind, type=type_, title=f"{job} 요청"), from_agent="engineer",
                          project_id=project.id, system=system, action_params=params, dedupe=False,
                          origin=by_project_manager(s, project.id))
    s.commit()
    said = report(api, req)["on_approve"]
    assert said and msg("request.effect.record", "en") not in said and msg("request.effect.by_hand", "en") not in said

    requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE, confirm_text=f"REQ-{req.id}"))
    s.commit()

    assert req.status == RequestStatus.APPROVED.value
    queued = [j.kind for j in s.scalars(select(Job).where(Job.idempotency_key.like(f"%:{req.id}"),
                                                          Job.created_at >= req.created_at))]
    assert queued == [job]


def test_asking_again_after_a_failed_send_keeps_the_card(s, project):
    first = requests.create(
        s, draft(card=card(), action_payload_json='{"to": ["c@example.com"], "subject": "안내", "body": "b"}'),
        from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()

    again = requests.re_request(s, first.id)

    assert again.id != first.id and again.action_params["card"] == first.action_params["card"]


@pytest.mark.parametrize("agent", ["project_manager", "strategy", "marketing", "finance", "product"])
def test_every_agent_that_raises_requests_is_told_to_write_a_report_card(agent):
    from justshipit.agents.executor import system_prompt
    prompt = system_prompt(agent)
    assert "결정 카드" in prompt and all(f in prompt for f in ("headline", "owner_does", "why", "verdict"))
    assert "on_approve" not in prompt and "question`" not in prompt
    assert not re.search(r"\{[a-z_]+\}", prompt), "the preamble is a format string: an unfilled field would reach the model"


@pytest.mark.parametrize("kind,params", [
    ("external.contract_sign", {}), ("dev.deploy_production", {}), ("external.social_post", None),
    ("external.other", {"proposed_action_kind": "bank.wire_transfer"}),
])
def test_only_a_hand_done_catch_all_act_skips_the_typed_id(s, project, api, kind, params):
    """Review: contract signing, production deploys, a kind the owner made strong and a kind the server renamed (an
    agent's invented "wire transfer") were a click under the first rule. Only a hand-done `external.other` is."""
    if params is None:
        assert api.put("/api/settings/policy", json={"overrides": {kind: "strong_approve"}}).status_code == 200
    try:
        strong = api.get("/api/overview").json()["pending"]["strong"]
        posts = kind in ("external.social_post", "external.other")
        req = requests.create(s, draft(action_kind=kind, title="강한 승인", post_card=LANDING_CARD if posts else None),
                              from_agent="marketing", project_id=project.id, action_params=params or None, system=True,
                              origin=by_project_manager(s, project.id))
        s.commit()
        assert req.policy_mode == "strong_approve"

        assert report(api, req)["typed_confirm"] is True
        assert api.get("/api/overview").json()["pending"]["strong"] == strong + 1
        with pytest.raises(requests.ResponseRejected, match="needs one more check"):
            requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE))
    finally:
        assert api.put("/api/settings/policy", json={"overrides": {}}).status_code == 200


@pytest.mark.parametrize("label,verdict", [
    ("no", "answer"), ("사용하지 않음", "answer"), ("Redis 캐시 안 함", "answer"), ("반려", "reject"), ("보류", "defer"),
])
def test_a_questions_no_is_an_answer_not_a_refusal(label, verdict):
    assert requests.infer_verdict(label, question=True) == verdict


@pytest.mark.parametrize("label,verdict", [
    ("취소 정책 승인", None), ("중단된 실험 재개 진행", None), ("올리지 않음", "reject"), ("승인 보류", "defer"),
    ("진행", "approve"),
])
def test_an_old_option_that_says_both_yes_and_no_is_not_guessed(label, verdict):
    assert requests.infer_verdict(label, question=False) == verdict


def test_a_typed_no_on_a_question_is_still_an_answer(s, project):
    q = requests.create(s, draft(type=RequestType.CLARIFICATION, action_kind=None, card=card(
        options=[option("Redis 캐시 써요", "approve"), option("Redis 캐시 안 함", "reject"), option("반려", "reject")])),
        from_agent="engineer", project_id=project.id, origin=by_project_manager(s, project.id))

    assert [o["verdict"] for o in q.action_params["card"]["options"]] == ["answer", "answer", "reject"]


def test_two_options_never_read_the_same():
    c = card(options=[option("가" * 40, "approve"), option("가" * 40 + "나", "reject")])

    assert len({o.label for o in c.options}) == 2


def test_an_old_option_sent_with_a_no_is_not_saved_as_the_owners_answer(s, project):
    old = AgentRequest(project_id=project.id, from_agent="research", type="APPROVAL", title="옛 요청", summary="s",
                       rationale="r", options=["A안", "B안"], recommended_option="A안", action_kind=None,
                       action_params={}, risk_level="low", policy_mode="approve", status="PENDING")
    s.add(old)
    s.flush()

    requests.respond(s, old.id, HumanResponse(decision=HumanDecision.REJECT, chosen_option="A안"))

    assert old.response["chosen_option"] is None
    assert not (project.context or {}).get("owner_answers")
