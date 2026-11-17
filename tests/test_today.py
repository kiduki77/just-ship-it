"""The home's list of what only the owner can do: what it merges, how it orders, and the lines around it."""
from datetime import UTC, datetime, timedelta

import pytest
from justshipit.api.app import app
from justshipit.contracts import owner_text
from justshipit.db.models import (
    ActivityEvent,
    AgentRequest,
    BusinessBrief,
    Job,
    KickoffSession,
    OwnerIdea,
    OwnerTask,
)
from justshipit.db.session import session_scope
from justshipit.services import activity, projects, roadmap, today
from justshipit.services.profile import store as profile_store
from justshipit.services.profile.store import NewValue
from bet_loop_kit import install_people, make_bet, reset_world
from fastapi.testclient import TestClient
from sqlalchemy import delete, update

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def _world():
    """A company with nobody else's leftovers: requests and tasks that belong to no business show on every business, and a
    job another file left waiting on a usage cap would show as the banner."""
    reset_world()
    with session_scope() as s:
        s.execute(update(Job).where(Job.status == "queued", Job.wait_reason.is_not(None)).values(status="done"))
        s.execute(update(AgentRequest).where(AgentRequest.project_id.is_(None), AgentRequest.status == "PENDING")
                  .values(status="CANCELLED"))
        s.execute(delete(OwnerTask).where(OwnerTask.business_id.is_(None)))
    yield
    reset_world()


@pytest.fixture
def business(s):
    b = roadmap.create_business(s, name="오늘 사업", one_liner="오늘 할 일을 모아 주는 도구")
    s.commit()
    return b


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def task(s, business, kind="post", title="게시 하나", due=None) -> OwnerTask:
    t = OwnerTask(business_id=business.id, kind=kind, title=title, card={}, status="OPEN", due_at=due)
    s.add(t)
    s.flush()
    return t


def request(s, business, title="결정 하나", blocking=False, **kw) -> AgentRequest:
    p = projects.create(s, "목표", name=f"{title} 프로젝트", start=False, business=business)
    r = AgentRequest(project_id=p.id, from_agent="project_manager", type="APPROVAL", title=title, summary="요약", rationale="이유",
                     status="PENDING", policy_mode="approve", hold_scope="run" if blocking else "none", **kw)
    s.add(r)
    s.flush()
    return r


def refs(business_id: int, **kw) -> list[str]:
    with session_scope() as s:
        return [i["ref"] for i in today.build(s, business_id, lang="ko", **kw)["items"]]


def test_a_business_with_nothing_for_the_owner_has_an_empty_list(s, business):
    out = today.build(s, business.id, lang="ko")
    assert out["items"] == [] and out["total"] == 0 and out["banner"] is None and out["yesterday"] == []


def test_every_source_that_waits_on_the_owner_becomes_one_item(s, business, monkeypatch):
    req = request(s, business, "광고 집행")
    t = task(s, business, "contact", "연락: alice")
    s.add(OwnerIdea(business_id=business.id, text="가격표 없애기\n자세한 설명", status="TRIAGED",
                    disposition="discard_proposed", reason="이미 정한 범위 밖"))
    make_bet(s, business, status="RUNNING")
    s.add(KickoffSession(business_id=business.id, status="WAITING_OWNER"))
    monkeypatch.setattr(today.notify_state, "slack_down", lambda: {"since": NOW, "why": "invalid_auth"})
    s.commit()

    out = today.build(s, business.id, lang="ko", everything=True)
    by_kind = {i["kind"]: i for i in out["items"]}
    assert set(by_kind) == {"kickoff", "decision", "contact", "idea", "bet", "alert"}
    assert out["total"] == 6
    assert by_kind["decision"]["ref"] == f"REQ-{req.id}" and by_kind["decision"]["action"] == "decide"
    assert by_kind["decision"]["link"] == f"/inbox?req={req.id}"
    assert by_kind["contact"]["ref"] == f"OT-{t.id}" and by_kind["contact"]["label"] == "연락"
    # ?task= names a project task (T-n) on the work pages; an owner to-do's panel is ?ot=
    assert by_kind["contact"]["link"] == f"/my-tasks?ot={t.id}"
    assert by_kind["idea"]["title"] == "가격표 없애기" and "이미 정한 범위 밖" in by_kind["idea"]["meta"]["reason"]
    assert by_kind["kickoff"]["link"].startswith("/kickoff/")


def test_the_order_is_blocking_then_overdue_then_today_then_the_rest(s, business):
    rest = task(s, business, "referral", "나머지", due=NOW + timedelta(days=5))
    today_ = task(s, business, "post", "오늘", due=NOW)
    overdue = task(s, business, "follow_up", "지난 후속", due=NOW - timedelta(days=3))
    blocking = request(s, business, "막힌 결정", blocking=True)
    s.commit()

    assert refs(business.id, everything=True) == [f"REQ-{blocking.id}", f"OT-{overdue.id}", f"OT-{today_.id}",
                                                  f"OT-{rest.id}"]


def test_within_a_tier_the_shorter_job_comes_first_and_the_more_overdue_comes_before_an_equal_one(s, business):
    long = task(s, business, "onboard", "같이 설치", due=NOW - timedelta(days=1))
    short = task(s, business, "follow_up", "후속", due=NOW - timedelta(days=1))
    older = task(s, business, "follow_up", "더 오래된 후속", due=NOW - timedelta(days=9))
    s.commit()

    assert refs(business.id, everything=True) == [f"OT-{older.id}", f"OT-{short.id}", f"OT-{long.id}"]


def test_a_blocking_decision_outranks_an_overdue_follow_up_even_when_it_takes_longer(s, business):
    kick = KickoffSession(business_id=business.id, status="WAITING_OWNER")
    s.add(kick)
    follow = task(s, business, "follow_up", "후속", due=NOW - timedelta(days=2))
    s.commit()

    assert refs(business.id, everything=True)[0] == f"KICKOFF-{kick.id}" and refs(business.id)[1] == f"OT-{follow.id}"


def test_the_home_shows_three_and_says_how_many_there_are(s, business):
    for i in range(5):
        task(s, business, "post", f"게시 {i}")
    s.commit()

    home = today.build(s, business.id, lang="ko")
    assert len(home["items"]) == 3 and home["total"] == 5 and home["total_min"] == 50
    assert len(today.build(s, business.id, lang="ko", everything=True)["items"]) == 5


def test_a_task_that_is_done_or_skipped_is_not_on_the_list(s, business):
    done = task(s, business)
    skipped = task(s, business)
    open_ = task(s, business)
    s.flush()
    done.status, done.done_at = "DONE", NOW
    skipped.status, skipped.done_at = "SKIPPED", NOW
    s.commit()

    assert refs(business.id) == [f"OT-{open_.id}"]


def test_another_business_work_is_not_listed(s, business):
    other = roadmap.create_business(s, name="다른 사업", one_liner="다른 일")
    task(s, other, "post", "남의 게시")
    s.add(OwnerIdea(business_id=other.id, text="x", status="TRIAGED", disposition="discard_proposed"))
    request(s, other, "남의 결정", blocking=True)
    s.commit()

    assert refs(business.id) == []


def test_other_businesses_waiting_on_the_owner_are_counted_beside_the_list(s, business, api):
    busy = roadmap.create_business(s, name="Busy elsewhere", one_liner="x")
    request(s, busy, "Blocking ask", blocking=True)
    request(s, busy, "Plain ask")
    task(s, busy, "post", "Their post")
    todo_only = roadmap.create_business(s, name="Only a to-do", one_liner="x")
    task(s, todo_only, "contact", "Call someone")
    archived = roadmap.create_business(s, name="Archived one", one_liner="x")
    request(s, archived, "Old ask")
    archived.status = "ARCHIVED"
    quiet = roadmap.create_business(s, name="Quiet one", one_liner="x")
    done = request(s, quiet, "Answered")
    done.status = "APPROVED"
    request(s, business, "My own ask")
    s.commit()
    mine = {business.id, busy.id, todo_only.id, archived.id, quiet.id}

    home = api.get(f"/api/businesses/{business.id}/today").json()
    rows = {r["business_id"]: r for r in home["elsewhere"] if r["business_id"] in mine}
    assert rows == {busy.id: {"business_id": busy.id, "name": "Busy elsewhere", "decisions": 2, "todos": 1, "blocking": 1,
                              "turns": 0, "failed": 0},
                    todo_only.id: {"business_id": todo_only.id, "name": "Only a to-do", "decisions": 0, "todos": 1,
                                   "blocking": 0, "turns": 0, "failed": 0}}
    from_busy = api.get(f"/api/businesses/{busy.id}/today").json()
    assert {r["business_id"] for r in from_busy["elsewhere"]} & mine == {business.id, todo_only.id}


def test_work_that_stopped_or_failed_in_another_business_is_counted_beside_the_list(s, business, api):
    """A failure in another business was on no screen of this one: the line and the switcher counted decisions and
    to-dos only (eval F6). A business whose only news is a failure is listed too."""
    broken = roadmap.create_business(s, name="Broken elsewhere", one_liner="x")
    theirs = projects.create(s, "goal", name="Their failed run", start=False, business=broken)
    theirs.status, theirs.error = "FAILED", "boom"
    mine = projects.create(s, "goal", name="My failed run", start=False, business=business)
    mine.status, mine.error = "FAILED", "boom"
    s.commit()

    home = api.get(f"/api/businesses/{business.id}/today").json()
    row = next(r for r in home["elsewhere"] if r["business_id"] == broken.id)
    assert (row["decisions"], row["todos"], row["turns"], row["failed"]) == (0, 0, 0, 1)
    assert business.id not in {r["business_id"] for r in home["elsewhere"]}
    from_broken = api.get(f"/api/businesses/{broken.id}/today").json()
    assert {r["business_id"]: r["failed"] for r in from_broken["elsewhere"]}[business.id] == 1


def test_another_business_waiting_on_a_kickoff_answer_or_a_cycle_start_is_counted_as_a_turn(s, business, api):
    import cycle_kit
    chatting = roadmap.create_business(s, name="Kickoff waits", one_liner="x")
    s.add(KickoffSession(business_id=chatting.id, status="WAITING_OWNER"))
    s.commit()
    proposing = cycle_kit.business(s, name="Proposal waits")
    cycle_kit.proposed(s, proposing)
    s.commit()

    home = api.get(f"/api/businesses/{business.id}/today").json()
    rows = {r["business_id"]: (r["decisions"], r["todos"], r["turns"]) for r in home["elsewhere"]}
    assert rows[chatting.id] == (0, 0, 1) and rows[proposing.id] == (0, 0, 1)
    mine = api.get(f"/api/businesses/{chatting.id}/today").json()
    assert chatting.id not in {r["business_id"] for r in mine["elsewhere"]}
    assert "kickoff" in {i["kind"] for i in mine["items"]}


def test_a_brief_awaiting_approval_is_listed_until_a_kickoff_is_live_or_given_up(s, business):
    roadmap.new_brief_version(s, business.id, sections=roadmap.empty_sections(), assumptions=[], author="project_manager")
    sess = KickoffSession(business_id=business.id, status="DONE")
    s.add(sess)
    s.commit()

    (item,) = today.build(s, business.id, lang="ko")["items"]
    assert item["kind"] == "brief" and item["title"] == "계획 확인하고 시작하기" and item["link"] == f"/kickoff/{sess.id}"

    sess.status = "ABANDONED"
    s.commit()
    assert today.build(s, business.id, lang="ko")["items"] == []


def test_an_approved_brief_asks_nothing_more_until_a_changed_one_waits(s, business):
    brief = roadmap.new_brief_version(s, business.id, sections=roadmap.empty_sections(), assumptions=[], author="project_manager")
    s.commit()
    s.execute(update(BusinessBrief).where(BusinessBrief.id == brief.id).values(approved_at=NOW))
    s.commit()
    assert today.build(s, business.id, lang="ko")["items"] == []

    roadmap.new_brief_version(s, business.id, sections=roadmap.empty_sections(), assumptions=[], author="owner")
    s.commit()
    (item,) = today.build(s, business.id, lang="ko")["items"]
    assert item["title"] == "바뀐 계획 확인하기"


def test_codes_stay_out_of_titles(s, business):
    task(s, business, "post", "BET-3 Show HN 게시: REQ-12 승인됨")
    s.commit()

    (item,) = today.build(s, business.id, lang="ko")["items"]
    assert item["title"] == "Show HN 게시: 승인됨"
    assert owner_text.title("T-shirt 사이즈 P-4") == "T-shirt 사이즈"


def test_yesterday_is_counted_from_the_last_day_of_the_log_and_never_more_than_three_lines(s, business):
    def log(verb, entity, hours_ago=1, actor="system", **data):
        ev = activity.record(s, verb=verb, entity=entity, entity_id=1, project_id=None, business_id=business.id,
                             summary=verb, data=data, actor=actor)
        ev.at = NOW - timedelta(hours=hours_ago)
    for _ in range(4):
        log("people_found", "person")
    log("people_found", "person", hours_ago=30)
    log("bet_retro", "bet")
    log("owner_task_created", "owner_task", kind="post")
    log("owner_task_created", "owner_task", kind="post")
    log("owner_task_created", "owner_task", kind="follow_up")
    log("owner_task_created", "owner_task", actor="owner", kind="post")
    log("idea_triaged", "idea")
    s.commit()

    assert today.build(s, business.id, lang="ko", now=NOW)["yesterday"] == [
        "사람 4명을 찾았어요", "실험 1개를 돌아봤어요", "직접 올릴 글 2개를 내 할 일에 넣었어요"]
    s.execute(delete(ActivityEvent).where(ActivityEvent.business_id == business.id, ActivityEvent.verb != "idea_triaged"))
    s.commit()
    assert today.build(s, business.id, lang="ko", now=NOW)["yesterday"] == ["아이디어 1개를 정리했어요"]


def test_the_number_is_repeat_use_against_the_goal_and_first_value_and_stars_sit_beside_it(s, business):
    install_people(first_value=4, used=(2, 1))
    out = today.build(s, business.id, lang="ko")["number"]
    assert out["repeat"] == 2 and out["first_value"] == 4
    assert out["goal"] == {"text": "두 번 이상 쓴 외부 사용자 10명", "number": 10, "unit": "명", "measure": "repeat",
                           "current": 2}
    assert out["goal_from_brief"] is False and out["stars"] is None
    assert out["link"] == f"/business?b={business.id}"


def test_without_a_people_list_the_number_says_it_is_unknown(s, business):
    from bet_loop_kit import no_people
    no_people()
    out = today.build(s, business.id, lang="ko")["number"]
    assert out["repeat"] is None and out["first_value"] is None


def test_the_banner_names_the_usage_cap_the_work_waits_on(s, business):
    job = Job(gate="direct", kind="run_project", payload={"project_id": 1}, status="queued", wait_reason="usage_cap_day",
              run_after=NOW + timedelta(hours=3))
    s.add(job)
    s.commit()
    try:
        banner = today.build(s, business.id, lang="ko")["banner"]
        assert banner["kind"] == "usage_wait" and "하루 사용량 상한에 닿아" in banner["text"] and banner["text"].endswith("까지 기다려요")
    finally:
        s.delete(job)
        s.commit()


def test_the_route_returns_the_list_and_refuses_a_business_that_does_not_exist(s, business, api):
    for i in range(4):
        task(s, business, "post", f"게시 {i}")
    s.commit()

    home = api.get(f"/api/businesses/{business.id}/today").json()
    assert len(home["items"]) == 3 and home["total"] == 4
    assert len(api.get(f"/api/businesses/{business.id}/today", params={"all": True}).json()["items"]) == 4
    gone = api.get("/api/businesses/99999999/today")
    assert gone.status_code == 404 and "not found" in gone.json()["detail"]


def test_the_server_written_lines_follow_the_owners_language_and_stored_titles_stay(s, business):
    task(s, business, "contact", "민지에게 연락")
    s.commit()

    (en,) = today.build(s, business.id, lang="en")["items"]
    (ko,) = today.build(s, business.id, lang="ko")["items"]
    assert (en["label"], en["meta"]["reason"]) == ("Reach out", "You reach out yourself")
    assert (ko["label"], ko["meta"]["reason"]) == ("연락", "직접 연락해야 해요")
    assert en["title"] == ko["title"] == "민지에게 연락"


def test_the_route_speaks_the_language_chosen_in_settings(s, business, api):
    task(s, business, "contact", "민지에게 연락")
    s.commit()
    assert api.get(f"/api/businesses/{business.id}/today").json()["items"][0]["label"] == "Reach out"
    try:
        assert api.put("/api/settings/general", json={"language": "ko"}).status_code == 200
        assert api.get(f"/api/businesses/{business.id}/today").json()["items"][0]["label"] == "연락"
    finally:
        assert api.put("/api/settings/general", json={}).status_code == 200


def _described(s, business, *, by="project_manager", provenance="guess"):
    profile_store.set_line(s, business.id, "buyer", [NewValue("consumers", None, provenance, "backfill:1")],
                           cause="backfill" if by == "project_manager" else "owner_change", written_by=by, brief_id=None,
                           if_live=None, lang="en")
    s.commit()


def test_a_customer_description_nobody_looked_at_is_one_low_item_that_links_to_the_business(s, business):
    assert today.build(s, business.id, lang="en")["items"] == []
    _described(s, business)

    (item,) = today.build(s, business.id, lang="en")["items"]

    assert (item["kind"], item["ref"], item["title"], item["tier"], item["action"], item["link"]) == (
        "profile", f"PROFILE-{business.id}", "Check who your customers are", "other", "open",
        f"/business?b={business.id}#customers")
    assert item["label"] == "Customers" and "promotion" in item["meta"]["reason"]
    (ko,) = today.build(s, business.id, lang="ko")["items"]
    assert ko["title"] == "고객 설명이 맞는지 확인해 주세요" and ko["label"] == "고객 설명"


def test_the_customer_item_never_blocks_and_sorts_after_what_is_waiting_on_the_owner(s, business):
    _described(s, business)
    request(s, business, "광고 집행", blocking=True)
    task(s, business, "contact", "연락: alice", due=NOW - timedelta(days=1))
    s.commit()

    out = today.build(s, business.id, lang="en", everything=True)

    assert [i["kind"] for i in out["items"]][-1] == "profile" and out["items"][-1]["tier"] == "other"
    assert out["items"][0]["tier"] == "blocking"


def test_the_customer_item_goes_once_the_owner_acts_on_the_description_and_stays_gone(s, business):
    _described(s, business)
    profile_store.set_line(s, business.id, "buyer", [], cause="owner_not_right", written_by="owner", brief_id=None,
                           if_live=None, lang="en")
    s.commit()

    assert today.build(s, business.id, lang="en")["items"] == []
    profile_store.set_line(s, business.id, "buyer", [NewValue("developers", None, "guess", "plan:2")], cause="plan",
                           written_by="project_manager", brief_id=None, if_live=None, lang="en")
    s.commit()
    assert today.build(s, business.id, lang="en")["items"] == [], "a later guess does not bring it back"


def test_there_is_no_customer_item_before_anything_is_written_or_for_another_business(s, business):
    other = roadmap.create_business(s, name="다른 사업", one_liner="x")
    _described(s, other)

    assert today.build(s, business.id, lang="en")["items"] == []
    assert [i["ref"] for i in today.build(s, other.id, lang="en")["items"]] == [f"PROFILE-{other.id}"]


def test_a_card_filed_after_the_order_was_read_is_listed_and_takes_its_place_on_the_next_read(s, business, monkeypatch):
    late = request(s, business, "방금 올라온 결정", blocking=True)
    s.commit()
    monkeypatch.setattr(today.your_turn, "order_of", lambda *a, **kw: {})

    assert f"REQ-{late.id}" in refs(business.id, everything=True)
