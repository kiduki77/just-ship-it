"""Owner text stored before the contracts scrubbed it reads clean at read time: owner tasks, today items and the
places a post goes. The live 내 할 일 showed '왜: 지시서 l-d4bdf1 …', titles ending '(direct)', '동의서 게이트 ( )' and
'어디에: 그 밖의 채널' for 당근 posts."""
import pytest
from justshipit.api.app import app
from justshipit.contracts import owner_text
from justshipit.contracts.planning import ProjectManagerReviewOut, DecisionOut, RealignOut, RequestMergeOut
from justshipit.contracts.work import ReviewOut
from justshipit.contracts.work_path import ReviewKind
from justshipit.db.models import OwnerTask
from justshipit.services import channels, owner_tasks, review_rounds
from fastapi.testclient import TestClient


def _task(**kw) -> OwnerTask:
    return OwnerTask(id=1, business_id=None, project_id=None, request_id=None, kind=kw.pop("kind", "other"),
                     title=kw.pop("title", "할 일"), card=kw.pop("card", {}), status="OPEN", **kw)


def test_a_stored_why_loses_brief_line_ids():
    seen = owner_tasks.view(_task(card={"why": "지시서 l-d4bdf1 보호자 채널, 6주 30명 목표(l-3add5b)의 1순위 채널 가설"}), "en")

    assert seen["card"]["why"] == "지시서 보호자 채널, 6주 30명 목표의 1순위 채널 가설"


def test_a_person_tasks_title_names_the_place_in_the_owners_language():
    task = _task(kind="contact", title="연락: 오너가 아는 금형 공장 사장 6명 (direct)")

    assert owner_tasks.view(task, "en")["title"] == "연락: 오너가 아는 금형 공장 사장 6명 (In person)"
    assert owner_tasks.view(task, "ko")["title"] == "연락: 오너가 아는 금형 공장 사장 6명 (직접 만남)"


@pytest.mark.parametrize("title, key", [
    ("당근 동네생활(망원) 보호자 첫 글 직접 올리기", "daangn_community"),
    ("당근알바 도우미 공고를 시험 1건 올릴지 결정", "daangn_jobs"),
    ("망원 맘카페(네이버카페) 소개 글", "naver_cafe"),
])
def test_a_post_filed_under_other_reads_as_the_place_it_names(title, key):
    other = channels.get("other")
    card = {"channel": "other", "channel_name": other.names["ko"], "rules_summary": other.rules["ko"], "mode": "draft",
            "what": {"title": title, "body": "본문"}}

    for lang in ("en", "ko"):
        seen = owner_tasks.view(_task(kind="post", title=title, card=card), lang)["card"]

        assert (seen["channel"], seen["channel_name"]) == (key, channels.get(key).names[lang])
        assert seen["rules_summary"] == channels.get(key).rules[lang] and seen["mode"] == "draft"


def test_a_post_that_names_no_place_stays_other():
    card = {"channel": "other", "channel_name": "그 밖의 채널", "what": {"title": "토요일 현장 전단", "body": "b"}}

    assert owner_tasks.view(_task(kind="post", title="토요일 현장 전단", card=card), "en")["card"]["channel"] == "other"


def test_a_link_on_one_places_host_decides_the_place():
    assert channels.named("글", "https://cafe.naver.com/mangwon/1").key == "naver_cafe"
    assert channels.named("당근알바 공고", "https://www.daangn.com/kr/jobs/1").key == "daangn_jobs"


@pytest.mark.parametrize("raw, shown", [
    ("수신 Worker와 동의서 게이트 (REQ-32)", "수신 Worker와 동의서 게이트"),
    ("BET-3 회고 (REQ-26, OT-4)", "회고"),
    ("REQ-5: 명세 작성", "명세 작성"),
])
def test_a_home_line_without_its_codes_leaves_no_empty_brackets(raw, shown):
    assert owner_text.title(raw) == shown


def test_a_stored_owner_task_title_loses_other_records_codes():
    seen = owner_tasks.view(_task(kind="post", title="[게시] 토요일 전단 — REQ-28/OT-14 문구 제출본 (REQ-26 대체)"), "en")

    assert seen["title"] == "[게시] 토요일 전단 — 문구 제출본"


LEAK = "성공 기준 2개 충족 (l-20993e). 근거는 EV-12예요."
CLEAN = "성공 기준 2개 충족. 근거는 EV-12예요."


def test_a_ceo_review_is_scrubbed_when_the_project_manager_writes_it():
    """P-1's 결정 tab, CEO 검토 기록 round 3, showed '(l-20993e)': `assessment` was a plain str."""
    out = ProjectManagerReviewOut(assessment=LEAK, unmet_success_criteria=["첫 결제 l-3add5b"], action="complete",
                       meeting_agenda=None, meeting_participants=[], follow_up_tasks=[], requests=[])

    assert (out.assessment, out.unmet_success_criteria) == (CLEAN, ["첫 결제"])


def test_the_other_planning_and_review_text_the_owner_reads_is_scrubbed_too():
    realign = RealignOut(assessment=LEAK, goal_interpretation=LEAK, out_of_scope_additions=[], cancel_task_ids=[],
                         revise_tasks=[], new_tasks=[], withdraw_requests=[])
    decision = DecisionOut(title="채택 l-20993e", decision="adopt", rationale=LEAK, supporting_evidence_ids=[],
                           dissenting_evidence_ids=[], dissent_summary=LEAK, open_questions=[LEAK], action_tasks=[],
                           approval_required=True, approval_reason=LEAK)
    merge = RequestMergeOut(keep_id=1, merge_ids=[2], merged_title="전단 승인 (REQ-26 대체)", reason=LEAK)
    review = ReviewOut(verdict="rework", criteria_met=[], criteria_unmet=[], claim_checks=[],
                       counter_arguments=[LEAK], rework_instructions=LEAK, contested=False)

    assert {realign.assessment, realign.goal_interpretation} == {CLEAN}
    assert decision.title == "채택"
    assert {decision.rationale, decision.dissent_summary, decision.approval_reason, *decision.open_questions} == {CLEAN}
    assert (merge.merged_title, merge.reason) == ("전단 승인", CLEAN)
    assert {review.rework_instructions, *review.counter_arguments} == {CLEAN}


def test_a_stored_ceo_review_and_realignment_read_without_brief_line_ids(s, project, api_token):
    review_rounds.add(s, project.id, ReviewKind.REVIEW, plan_round=3, assessment=LEAK, action="complete")
    project.context = {**(project.context or {}), "realignments": [
        {"at": "2026-11-04T00:00:00+00:00", "request": "REQ-1", "assessment": LEAK,
         "cancelled": [], "revised": [], "added": [], "withdrawn": []}]}
    s.commit()
    api = TestClient(app, base_url="http://127.0.0.1:8000",
                     headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})

    context = api.get(f"/api/projects/{project.id}").json()["project"]["context"]

    assert (context["ceo_reviews"][0]["assessment"], context["realignments"][0]["assessment"]) == (CLEAN, CLEAN)
