"""Cleaning up a business: duplicate cards close into the card that decides them, and a business is archived and
brought back. 견적봇, 원두재고 and 동네산책 (critic round, 2026-11-04) are the cases."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from justshipit import cli
from justshipit.contracts.handoff import SpecStatus
from justshipit.contracts.requests import AgentRequestDraft, RequestStatus, RequestType, RiskLevel
from justshipit.db.models import AgentRequest, Bet, Job, Project, Schedule, Spec
from justshipit.db.session import session_scope
from justshipit.engineering import specs, status
from justshipit.services import matters, projects, requests, roadmap
from sqlalchemy import select
from typer.testing import CliRunner

BY = "claude (오너 위임)"


@pytest.fixture
def biz(s):
    b = roadmap.create_business(s, name=f"정리 {uuid4().hex[:6]}", one_liner="정리")
    p = projects.create(s, "목표", name=b.name, budget_usd=10, start=False, business=b)
    return b, p


def card(s, p, *, title="t", type_=RequestType.APPROVAL.value, kind="internal.other", status_="PENDING",
         response=None, blocking=False) -> AgentRequest:
    r = AgentRequest(project_id=p.id, from_agent="product", type=type_, title=title, summary="s", rationale="r",
                     action_kind=kind, status=status_, response=response, policy_mode="approve",
                     hold_scope="run" if blocking else "none")
    s.add(r)
    s.flush()
    return r


def spec_card(s, p, n: int, *, approved=False) -> Spec:
    spec = Spec(spec_key=f"CLN-{uuid4().hex[:4]}-{n}", project_id=p.id, title=f"명세 {n}", registry_path="specs/x",
                status=(SpecStatus.READY_FOR_DEV if approved else SpecStatus.IN_REVIEW).value, draft_commit_sha="abc",
                approved_commit_sha="abc" if approved else None, target_repository="quote-bot", handoff={})
    s.add(spec)
    s.flush()
    asked = specs._ask_approval(s, spec, AgentRequestDraft(
        type=RequestType.APPROVAL, title=f"명세 승인 및 개발 착수: {spec.spec_key}", summary="s", rationale="r",
        options=[], recommended_option=None, action_kind="dev.spec_approve", estimated_cost_usd=None,
        risk_level=RiskLevel.MEDIUM))
    if approved:
        asked.status, asked.response = RequestStatus.APPROVED.value, {"decision": "approve"}
    return spec


def test_older_spec_cards_close_into_the_newest_and_their_specs_fold_away(s, biz):
    """견적봇 REQ-50..52: three spec cards for one product. The newest decides; the others close into it."""
    b, p = biz
    older, middle, newest = (spec_card(s, p, n) for n in (4, 9, 10))

    found = matters.duplicates(s, b.id, lang="ko")
    assert [(d.card.id, d.into.id) for d in found] == [(older.approval_request_id, newest.approval_request_id),
                                                       (middle.approval_request_id, newest.approval_request_id)]
    assert requests.close_duplicates(s, found, by=BY) == [older.approval_request_id, middle.approval_request_id]

    closed = s.get(AgentRequest, older.approval_request_id)
    assert closed.status == RequestStatus.CANCELLED.value
    assert closed.response == {"decision": "superseded", "into": f"REQ-{newest.approval_request_id}", "by": BY,
                               "responded_by": BY, "note": f"같은 제품의 명세는 최신 명세 {newest.spec_key} 카드 하나에서 결정해요."}
    assert [status.spec_state(s, sp) for sp in (older, middle, newest)] == ["SUPERSEDED", "SUPERSEDED", "IN_REVIEW"]
    assert [st["ref"] for st in status.board(s, business_id=b.id, lang="ko")] == [newest.spec_key]
    assert requests.current(s, closed).id == newest.approval_request_id


def test_an_english_owner_reads_why_a_card_closed_and_why_a_close_is_refused_in_english(s, biz):
    b, p = biz
    older, newest = (spec_card(s, p, n) for n in (1, 2))
    requests.close_duplicates(s, matters.duplicates(s, b.id, lang="en"), by=BY)
    assert s.get(AgentRequest, older.approval_request_id).response["note"] == (
        f"This product's spec is decided on one card, the newest spec {newest.spec_key}.")
    with pytest.raises(ValueError, match="must be newer than"):
        matters.replaced(s, b.id, [(newest.approval_request_id, older.approval_request_id)], lang="en")


def test_development_asks_an_approved_spec_builds_close_into_its_card(s, biz):
    """원두재고 REQ-44..49: six asks for what the approved BUSINE-0001 (J-1) built, after a rejected duplicate spec."""
    b, p = biz
    built = spec_card(s, p, 1, approved=True)
    rejected = spec_card(s, p, 2)
    s.get(AgentRequest, rejected.approval_request_id).status = RequestStatus.REJECTED.value
    asks = [card(s, p, title=f"개발 제안: {t}", type_="DEVELOPMENT", kind="dev.implementation_start")
            for t in ("정본 표", "JS 이식", "Cloudflare Pages 배포")]

    found = matters.duplicates(s, b.id, lang="ko")
    assert {d.into.id for d in found} == {built.approval_request_id}
    requests.close_duplicates(s, found, by=BY)

    assert all(r.status == RequestStatus.CANCELLED.value for r in asks)
    assert asks[0].response["note"] == f"승인한 명세 {built.spec_key}에서 이미 다루는 개발이에요."
    assert s.get(AgentRequest, built.approval_request_id).status == RequestStatus.APPROVED.value, "its card is untouched"


def test_development_asks_with_no_spec_keep_the_newest(s, biz):
    b, p = biz
    asks = [card(s, p, title=f"개발 제안 {i}", type_="DEVELOPMENT") for i in range(3)]
    requests.close_duplicates(s, matters.duplicates(s, b.id, lang="ko"), by=BY)
    assert [r.status for r in asks] == ["CANCELLED", "CANCELLED", "PENDING"]


def test_a_card_a_later_one_replaced_closes_and_keeps_the_owners_answer(s, biz):
    """동네산책 REQ-22 and REQ-26: deferred flyers the approved REQ-28 replaced. Rejected ones are the owner's answer."""
    b, p = biz
    first = card(s, p, title="전단", status_="DEFERRED", response={"decision": "defer", "note": "토요일 전에"})
    kept = card(s, p, title="전단 A5 (대체)", status_="APPROVED", response={"decision": "approve"})
    said_no = card(s, p, title="전단 A5 + QR", status_="REJECTED", response={"decision": "reject"})

    requests.close_duplicates(s, matters.replaced(s, b.id, [(first.id, kept.id)], lang="ko"), by=BY)

    assert first.status == RequestStatus.CANCELLED.value
    assert first.response["note"] == "토요일 전에" and first.response["superseded_by"] == f"REQ-{kept.id}"
    assert first.response["closed_by"] == BY
    assert requests.current(s, first).id == kept.id
    assert matters.replaced(s, b.id, [(first.id, kept.id)], lang="ko") == [], "naming the pair again changes nothing"
    with pytest.raises(ValueError):
        matters.replaced(s, b.id, [(said_no.id, kept.id)], lang="ko")
    with pytest.raises(ValueError):
        matters.replaced(s, b.id, [(kept.id, first.id)], lang="ko")
    other = roadmap.create_business(s, name=f"남 {uuid4().hex[:6]}", one_liner="남")
    with pytest.raises(ValueError):
        matters.replaced(s, other.id, [(first.id, kept.id)], lang="ko")


def test_closing_again_closes_nothing_and_a_card_answered_meanwhile_stays(s, biz):
    b, p = biz
    a, answered, _newest = (card(s, p, title=f"개발 {i}", type_="DEVELOPMENT") for i in range(3))
    found = matters.duplicates(s, b.id, lang="ko")
    answered.status = RequestStatus.APPROVED.value
    s.flush()

    assert requests.close_duplicates(s, found, by=BY) == [a.id]
    assert answered.status == RequestStatus.APPROVED.value
    assert requests.close_duplicates(s, matters.duplicates(s, b.id, lang="ko"), by=BY) == []


def test_a_blocking_duplicate_hands_its_block_to_the_card_that_stays(s, biz):
    b, p = biz
    blocking = card(s, p, title="개발 1", type_="DEVELOPMENT", blocking=True)
    keep = card(s, p, title="개발 2", type_="DEVELOPMENT")
    p.status = "WAITING_HUMAN"
    requests.close_duplicates(s, matters.duplicates(s, b.id, lang="ko"), by=BY)
    s.flush()
    assert blocking.status == RequestStatus.CANCELLED.value and keep.blocking is True
    assert s.scalar(select(Job).where(Job.kind == "resume_project", Job.payload["project_id"].as_integer() == p.id)) \
        is None, "the project still waits on the owner"


def test_the_command_lists_on_a_dry_run_and_closes_for_real(s, biz):
    b, p = biz
    a, keep = (card(s, p, title=f"개발 {i}", type_="DEVELOPMENT") for i in range(2))
    s.commit()
    args = ["requests", "close-duplicates", "--business", str(b.id), "--by", BY]

    dry = CliRunner().invoke(cli.app, [*args, "--dry-run"])
    assert dry.exit_code == 0 and f"REQ-{a.id} (PENDING) -> REQ-{keep.id}" in dry.output
    with session_scope() as fresh:
        assert fresh.get(AgentRequest, a.id).status == "PENDING"

    real = CliRunner().invoke(cli.app, args)
    assert real.exit_code == 0 and f"closed 1: REQ-{a.id}" in real.output
    with session_scope() as fresh:
        assert fresh.get(AgentRequest, a.id).response["responded_by"] == BY


def _schedule(s, name: str, kind: str, payload: dict) -> Schedule:
    sc = Schedule(name=name, kind=kind, payload=payload, interval_hours=24, enabled=True,
                  next_run_at=datetime.now(timezone.utc))
    s.add(sc)
    s.flush()
    return sc


def test_archiving_a_business_stops_what_spends_and_unarchive_brings_it_back(s, biz):
    b, p = biz
    pending = card(s, p, title="게시 승인")
    bet = Bet(business_id=b.id, title="도전", pitch="p", status="REVIEW", verdict="routine")
    s.add(bet)
    s.flush()
    own = _schedule(s, f"B-{b.id} 사람 찾기", "find_people", {"business_id": b.id})
    routine = _schedule(s, f"BET-{bet.id} 정기", "routine_run", {"bet_id": bet.id})
    watch = _schedule(s, f"P-{p.id} 변화 감시", "evidence_watch", {"project_id": p.id})
    other = roadmap.create_business(s, name=f"다른 {uuid4().hex[:6]}", one_liner="다른")
    theirs = _schedule(s, f"B-{other.id} 사람 찾기", "find_people", {"business_id": other.id})

    done = projects.archive_business(s, b.id, lang="ko", by=BY)
    s.flush()

    assert done["projects"] == [p.id] and done["cancelled_requests"] == [pending.id]
    assert sorted(done["disabled_schedules"]) == sorted([own.id, routine.id, watch.id])
    assert b.status == "ARCHIVED" and p.status == "ARCHIVED" and theirs.enabled is True
    assert pending.response == {"decision": "withdrawn", "by": BY, "responded_by": BY, "note": "사업 보관"}
    assert b.id not in {x["id"] for x in roadmap.business_list(s, include_test=True)}
    assert b.id in {x["id"] for x in roadmap.business_list(s, include_archived=True, include_test=True)}
    assert projects.archive_business(s, b.id, lang="ko", by=BY) == {"projects": [], "cancelled_requests": [],
                                                                     "disabled_schedules": []}

    back = projects.unarchive_business(s, b.id, lang="ko")
    s.flush()
    assert back == {"projects": [p.id], "enabled_schedules": [own.id]}
    assert b.status == "ACTIVE" and s.get(Project, p.id).status != "ARCHIVED"
    assert routine.enabled is False and watch.enabled is False and pending.status == "CANCELLED"


def test_a_project_archived_on_its_own_stays_archived_when_its_business_returns(s, biz):
    b, p = biz
    projects.archive(s, p.id)
    projects.archive_business(s, b.id, lang="ko")
    assert projects.unarchive_business(s, b.id, lang="ko")["projects"] == []
    assert p.status == "ARCHIVED"


def test_archiving_a_business_waits_for_its_jobs(s, biz):
    b, _ = biz
    s.add(Job(gate="direct", kind="find_people", payload={"business_id": b.id}, status="queued"))
    s.flush()
    with pytest.raises(ValueError, match=f"B-{b.id}"):
        projects.archive_business(s, b.id, lang="ko")
    assert b.status == "ACTIVE"
    s.rollback()  # the queued job must not reach a worker test


def test_the_routines_page_leaves_out_an_archived_business(s, biz, api_token):
    """The 정기 업무 page kept an archived business's stopped schedules, with 지금 실행 and the toggle still live."""
    from fastapi.testclient import TestClient

    from justshipit.api.app import app
    from justshipit.db.models import Kpi
    b, p = biz
    bet = Bet(business_id=b.id, title="도전", pitch="p", status="REVIEW", verdict="routine")
    s.add(bet)
    s.flush()
    mine = [_schedule(s, f"B-{b.id} 주간 알리기", "promotion_week", {"business_id": b.id}),
            _schedule(s, f"BET-{bet.id} 정기", "routine_run", {"bet_id": bet.id}),
            _schedule(s, f"P-{p.id} KPI 리뷰", "kpi_review", {"project_id": p.id})]
    company = _schedule(s, f"전사 {uuid4().hex[:6]}", "portfolio_review", {})
    kpi = Kpi(project_id=p.id, name="사용 카페 수", target=2)
    s.add(kpi)
    projects.archive_business(s, b.id, lang="ko")
    s.commit()
    api = TestClient(app, base_url="http://127.0.0.1:8000",
                     headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})

    shown = {x["id"] for x in api.get("/api/schedules").json()}
    assert company.id in shown and not shown & {sc.id for sc in mine}
    assert kpi.id not in {x["id"] for x in api.get("/api/kpis").json()}


def test_the_archive_command_records_who_archived_the_business(s, biz):
    from justshipit.db.models import ActivityEvent
    b, _ = biz
    s.commit()
    out = CliRunner().invoke(cli.app, ["business", "archive", str(b.id), "--by", BY])
    assert out.exit_code == 0, out.output
    with session_scope() as fresh:
        actors = set(fresh.scalars(select(ActivityEvent.actor).where(
            ActivityEvent.entity == "business", ActivityEvent.entity_id == b.id, ActivityEvent.verb == "status")))
    assert actors == {BY}
