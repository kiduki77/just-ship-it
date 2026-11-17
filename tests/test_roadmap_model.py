"""Business, brief, ladder, track, bet and cycle model: bet lifecycle, owner edits, the migration backfill."""
import importlib.util
import json
import threading
import time
from pathlib import Path

import psycopg
import pytest
from justshipit.api import console
from justshipit.api.app import app
from justshipit.contracts.requests import AgentRequestDraft, HumanDecision, HumanResponse, RequestType, RiskLevel
from justshipit.db.models import ActivityEvent, Bet, Business, Project, Task
from justshipit.db.session import engine, session_scope
from justshipit.operations import jobs
from justshipit.services import activity, control, events, projects, requests, roadmap
from justshipit.services.control import ControlError
from justshipit.services.roadmap import BET_TRANSITIONS, TERMINAL_BET_STATUSES, BetStatus
from justshipit.settings import settings
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from ask_kit import contract, by_project_manager

VERSIONS = Path(__file__).resolve().parents[1] / "backend/justshipit/db/migrations/versions"


def _migration(name: str = "8c41f0d2e7a9_business_roadmap"):
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def business(s):
    b = roadmap.create_business(s, name="빵집", one_liner="동네 빵집을 구독으로")
    s.commit()
    return b


def _bet(s, bid: int, status: BetStatus = BetStatus.PROPOSED, **kw) -> Bet:
    bet = roadmap.create_bet(s, bid, title="시식 행사", pitch="이웃 20명에게 시식", **kw)
    bet.status = status.value
    s.commit()
    return bet


# ---------------------------------------------------------------- bet lifecycle

def test_every_legal_transition_moves_and_stamps(s, business):
    stamps = {BetStatus.PICKED: "picked_at", BetStatus.RUNNING: "started_at", BetStatus.REVIEW: "finished_at",
              BetStatus.DONE: "finished_at", BetStatus.STOPPED: "finished_at", BetStatus.DROPPED: "finished_at"}
    legal = [(a, b) for a, targets in BET_TRANSITIONS.items() for b in targets]
    assert len(legal) == 13
    for frm, to in legal:
        bet = _bet(s, roadmap.create_business(s, name=f"{frm.value}-{to.value}", one_liner="x").id, frm)
        with session_scope() as x:
            roadmap.move_bet(x, bet.id, to)
        s.refresh(bet)
        assert bet.status == to.value, (frm, to)
        if to in stamps:
            assert getattr(bet, stamps[to]) is not None, (frm, to)
        elif to == BetStatus.PROPOSED:
            assert bet.picked_at is None and bet.cycle_id is None


@pytest.mark.parametrize("frm,to", [(BetStatus.PROPOSED, BetStatus.DONE), (BetStatus.DONE, BetStatus.RUNNING),
                                    (BetStatus.DROPPED, BetStatus.PICKED)])
def test_illegal_transitions_are_refused_and_leave_the_row(s, business, frm, to):
    bet = _bet(s, business.id, frm)
    with pytest.raises(ControlError), session_scope() as x:
        roadmap.move_bet(x, bet.id, to)
    s.refresh(bet)
    assert bet.status == frm.value and bet.finished_at is None


def test_moving_to_the_current_status_is_a_no_op(s, business):
    bet = _bet(s, business.id, BetStatus.PICKED)
    with session_scope() as x:
        roadmap.move_bet(x, bet.id, "PICKED")
    s.refresh(bet)
    assert bet.picked_at is None and bet.status == "PICKED"
    with pytest.raises(ControlError), session_scope() as x:
        roadmap.move_bet(x, bet.id, "FLYING")


def test_terminal_statuses_have_no_exits():
    assert all(not BET_TRANSITIONS[st] for st in TERMINAL_BET_STATUSES)


def test_stopping_a_bet_pauses_its_run(s, business):
    bet = _bet(s, business.id, BetStatus.RUNNING)
    run = Project(slug="run-for-bet", name="시식 실행", goal="g", status="RUNNING", budget_usd=5,
                  business_id=business.id, bet_id=bet.id)
    s.add(run)
    s.commit()
    with activity.as_actor("owner"), session_scope() as x:
        roadmap.move_bet(x, bet.id, BetStatus.STOPPED, reason="예산 소진")
    s.refresh(bet), s.refresh(run)
    assert bet.status == "STOPPED" and bet.status_reason == "예산 소진"
    assert (run.context or {}).get("paused"), "an in-flight run is held when its bet stops"
    ev = s.scalars(select(ActivityEvent).where(ActivityEvent.entity == "bet", ActivityEvent.entity_id == bet.id,
                                               ActivityEvent.verb == "status").order_by(ActivityEvent.id)).all()
    assert ev and ev[-1].actor == "owner" and ev[-1].data["to"] == "STOPPED" and ev[-1].business_id == business.id


def test_illegal_move_through_the_console_is_409(s, business):
    bet = _bet(s, business.id, BetStatus.PROPOSED)
    with pytest.raises(HTTPException) as exc:
        console.bet_move(bet.id, console.BetMoveIn(to="DONE"))
    assert exc.value.status_code == 409
    s.refresh(bet)
    assert bet.status == "PROPOSED"


# ---------------------------------------------------------------- owner edits

def test_lists_get_ids_and_a_rung_in_use_cannot_be_removed(s, business):
    with session_scope() as x:
        b = roadmap.update_lists(x, business.id, lang="ko", rungs=[{"name": "문제 확인", "exit_signal": "인터뷰 10건"},
                                                        {"name": "첫 손님", "exit_signal": "결제 3건"}],
                                 tracks=[{"name": "제품"}, {"name": "손님"}])
        rungs, tracks = b.rungs, b.tracks
    assert all(r["id"].startswith("r-") for r in rungs) and all(t["id"].startswith("t-") for t in tracks)
    s.expire_all()
    bet = _bet(s, business.id, BetStatus.RUNNING, rung_id=rungs[0]["id"], track_id=tracks[1]["id"])
    with pytest.raises(ControlError, match=f"BET-{bet.id}"), session_scope() as x:
        roadmap.update_lists(x, business.id, lang="ko", rungs=rungs[1:])
    with pytest.raises(ControlError), session_scope() as x:
        roadmap.update_lists(x, business.id, lang="ko", checkpoints=[{"on": "2027-01-31", "title": "90일 판정", "rung_id": "r-nope"}])
    with session_scope() as x:
        roadmap.update_lists(x, business.id, lang="ko", rungs=[{**rungs[0], "reached_at": "2026-11-01"}, rungs[1]],
                             checkpoints=[{"on": "2027-01-31", "title": "90일 판정"}])
    s.expire_all()
    view = roadmap.roadmap(s, business.id, lang="ko")
    assert [r["state"] for r in view["rungs"]] == ["reached", "current"]
    assert view["checkpoints"][0]["id"].startswith("c-") and "days_until" in view["checkpoints"][0]
    with pytest.raises(ControlError), session_scope() as x:
        roadmap.create_bet(x, business.id, title="x", pitch="y", track_id="t-missing")


def test_bet_promise_is_frozen_once_it_runs(s, business):
    bet = _bet(s, business.id, BetStatus.PICKED)
    with session_scope() as x:
        roadmap.update_bet(x, bet.id, appetite_days=14, budget_usd=20)
    with session_scope() as x:
        roadmap.move_bet(x, bet.id, BetStatus.RUNNING)
    with pytest.raises(ControlError), session_scope() as x:
        roadmap.update_bet(x, bet.id, budget_usd=40)
    with session_scope() as x:
        roadmap.update_bet(x, bet.id, hill="downhill", health="on_track")
    s.refresh(bet)
    assert (bet.appetite_days, bet.budget_usd, bet.hill, bet.health) == (14, 20, "downhill", "on_track")
    health_events = s.scalars(select(ActivityEvent).where(ActivityEvent.entity == "bet", ActivityEvent.entity_id == bet.id,
                                                          ActivityEvent.verb == "health")).all()
    assert health_events and health_events[-1].data["to"] == "on_track"


def test_brief_versions_and_one_open_draft(s, business):
    sections = [{"key": "customer_problem", "title": "대상 고객과 문제", "lines": [
        {"text": "동네 맞벌이 가정", "provenance": "said", "source": "REQ-46"},
        {"text": "", "provenance": "blank"}]},
        {"key": "goal_deadline", "title": "목표와 기한", "lines": [{"text": "석 달에 100명", "provenance": "said"}]}]
    with session_scope() as x:
        v1 = roadmap.new_brief_version(x, business.id, sections=sections, assumptions=[{"text": "주 1회 산다"}], author="owner")
        v1_id = v1.id
    with pytest.raises(ControlError), session_scope() as x:
        roadmap.new_brief_version(x, business.id, sections=sections, assumptions=[], author="owner")
    with pytest.raises(ControlError), session_scope() as x:
        roadmap.new_brief_version(x, business.id, sections=[{"key": "k", "title": "t", "lines": [
            {"text": "글이 있는데", "provenance": "blank"}]}], assumptions=[], author="owner", approved=True)
    with session_scope() as x:
        roadmap.approve_brief(x, v1_id, lang="ko")
        v2 = roadmap.new_brief_version(x, business.id, sections=sections, assumptions=[], author="project_manager", approved=True)
    assert v2.version == 2
    assert roadmap.current_brief(s, business.id).version == 2
    assert roadmap.roadmap(s, business.id, lang="ko")["brief"]["sections"][0]["lines"][0]["id"].startswith("l-")


def test_one_open_cycle_per_business(s, business):
    from justshipit.services import cycle
    with session_scope() as x:
        c = cycle.open_cycle(x, business.id, lang="ko")
    assert (c.number, c.state, (c.ends_on - c.starts_on).days) == (1, "draft", 14)
    with session_scope() as x:
        assert cycle.open_cycle(x, business.id, lang="ko").id == c.id, "a second open returns the open one"
    bet = _bet(s, business.id)
    with session_scope() as x:
        roadmap.move_bet(x, bet.id, BetStatus.PICKED, cycle_id=c.id)
    s.refresh(bet)
    assert bet.cycle_id == c.id
    assert roadmap.roadmap(s, business.id, lang="ko")["cycle"]["number"] == 1


# ---------------------------------------------------------------- projects become bets

def test_new_project_gets_a_business_and_a_bet(s):
    started = projects.create(s, "첫 사업", name="첫 사업", budget_usd=10)
    waiting = projects.create(s, "두 번째", name="두 번째", budget_usd=10, start=False)
    s.commit()
    for p, status in ((started, "RUNNING"), (waiting, "PICKED")):
        bet = s.get(Bet, p.bet_id)
        assert bet.business_id == p.business_id and bet.status == status and bet.pitch == p.goal
    assert s.get(Business, started.business_id).slug == started.slug
    assert roadmap.adopt_project(s, started, running=True).id == started.bet_id, "adopting again changes nothing"


def test_backfill_links_two_projects_and_a_second_run_changes_nothing(s):
    parent = Project(slug="bf-parent", name="부모", goal="목표 한 줄\n둘째 줄", status="DONE", budget_usd=10)
    s.add(parent)
    s.flush()
    child = Project(slug="bf-child", name="부모 — 재평가", goal="재평가", status="ARCHIVED", budget_usd=5,
                    context={"parent_project_id": parent.id})
    s.add(child)
    s.commit()
    mig = _migration()

    def counts(conn):
        return [conn.execute(text(f"select count(*) from {t}")).scalar() for t in ("businesses", "bets", "activity_events")]
    with engine().begin() as conn:
        mig.backfill(conn)
        first = counts(conn)
        mig.backfill(conn)
        assert counts(conn) == first
    s.refresh(parent), s.refresh(child)
    assert parent.business_id == child.business_id and parent.bet_id != child.bet_id
    pb, cb = s.get(Bet, parent.bet_id), s.get(Bet, child.bet_id)
    assert (pb.status, pb.origin, cb.status, cb.origin) == ("DONE", "owner", "STOPPED", "ceo")
    assert s.get(Business, parent.business_id).one_liner == "목표 한 줄"
    created = s.scalars(select(ActivityEvent).where(ActivityEvent.entity == "bet", ActivityEvent.verb == "created",
                                                    ActivityEvent.entity_id.in_([pb.id, cb.id]))).all()
    assert len(created) == 2 and all(e.data["backfill"] and e.business_id == parent.business_id for e in created)


# ---------------------------------------------------------------- read models

def test_business_list_and_roadmap_show_the_run(s):
    p = projects.create(s, "손님 찾기", name="손님 찾기", budget_usd=10)
    test = projects.create(s, "검증", name="[TEST] 로드맵", budget_usd=1, start=False)
    s.commit()
    shown = {b["id"] for b in console.business_list()}
    assert p.business_id in shown and test.business_id not in shown
    assert test.business_id in {b["id"] for b in console.business_list(include_test=True)}
    mine = next(b for b in console.business_list() if b["id"] == p.business_id)
    assert mine["bets_running"] == 1 and mine["project_ids"] == [p.id] and mine["current_rung"] is None
    view = console.business_roadmap(p.business_id)
    assert (view["rungs"], view["tracks"], view["models"]) == ([], [], [])
    bet = view["bets"][0]
    assert bet["run"]["project_id"] == p.id and bet["id"] in view["lanes"]["now"]
    with pytest.raises(HTTPException) as exc:
        console.business_roadmap(10**9)
    assert exc.value.status_code == 404


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"}, raise_server_exceptions=False)


def _approved_reassessment(s, project_id: int) -> int:
    req = requests.create(s, AgentRequestDraft(
        type=RequestType.PROPOSAL, title="재평가 제안", summary="경쟁사 가격 인하", rationale="변화 감시",
        options=["재평가 프로젝트 시작"], recommended_option="재평가 프로젝트 시작", action_kind="project.reassess",
        estimated_cost_usd=None, risk_level=RiskLevel.MEDIUM, context=contract(),
    ), from_agent="research", project_id=project_id, system=True, origin=by_project_manager(s, project_id))
    requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE))
    s.commit()
    return req.id


def test_reassessment_child_joins_its_parents_business(s):
    parent = projects.create(s, "동네 빵집 구독", name="빵집 구독", budget_usd=10, start=False)
    s.commit()
    businesses_before = s.scalar(select(func.count()).select_from(Business))
    jobs.reassess_project(parent.id, _approved_reassessment(s, parent.id))
    s.expire_all()
    child = s.scalar(select(Project).where(Project.context["parent_project_id"].as_integer() == parent.id))
    assert child.business_id == parent.business_id
    assert s.get(Bet, child.bet_id).origin == "project_manager"
    assert s.scalar(select(func.count()).select_from(Business)) == businesses_before


@pytest.mark.parametrize("field,value", [("hill", "sideways"), ("health", "great"), ("run_mode", "robot"),
                                         ("title", "   "), ("pitch", " ")])
def test_bad_bet_edits_are_409_and_leave_the_row(s, business, api, field, value):
    bet = _bet(s, business.id, BetStatus.PICKED)
    before = getattr(bet, field)
    r = api.post(f"/api/bets/{bet.id}/edit", json={field: value})
    assert r.status_code == 409, r.text
    s.refresh(bet)
    assert getattr(bet, field) == before


def test_an_overlong_rung_id_is_refused_before_a_bet_can_use_it(s, business, api):
    long_id = "r-" + "x" * 28
    r = api.post(f"/api/businesses/{business.id}/lists", json={"rungs": [{"id": long_id, "name": "긴 칸"}]})
    assert r.status_code == 409, r.text
    s.refresh(business)
    assert business.rungs == []
    r = api.post(f"/api/businesses/{business.id}/bets", json={"title": "t", "pitch": "p", "rung_id": long_id})
    assert r.status_code == 409, r.text


def test_a_bet_move_returns_the_row_as_stored(s, business, api):
    bet = _bet(s, business.id)
    for to in ("PICKED", "RUNNING"):
        moved = api.post(f"/api/bets/{bet.id}/move", json={"to": to}).json()
        stored = next(b for b in api.get(f"/api/businesses/{business.id}/roadmap").json()["bets"] if b["id"] == bet.id)
        assert moved["updated_at"] == stored["updated_at"], to


def test_new_events_carry_their_business(s):
    p = projects.create(s, "손님 인터뷰", name="인터뷰", budget_usd=10, start=False)
    s.add(Task(project_id=p.id, key="T1", title="인터뷰 10건", owner="research", objective="o"))
    s.flush()
    control.pause(s, p.id, "확인")
    s.commit()
    rows = s.scalars(select(ActivityEvent).where(ActivityEvent.project_id == p.id)).all()
    assert {(e.entity, e.verb) for e in rows} >= {("project", "created"), ("task", "created"), ("project", "paused")}
    assert all(e.business_id == p.business_id for e in rows), [(e.entity, e.verb, e.business_id) for e in rows]


def _sequence_marks() -> dict[str, int]:
    with engine().begin() as conn:
        return dict(conn.execute(text("SELECT sequencename, last_value FROM pg_sequences "
                                      "WHERE schemaname = 'public' AND last_value IS NOT NULL")).all())


def _keep_ids_climbing(marks: dict[str, int]) -> None:
    """A downgrade drops the tables and the upgrade makes them again, so their ids count from 1 again. The rows
    nothing dropped (the activity log, the jobs) still name the old ids, and every later test file in this database
    that creates enough businesses, people or bets would meet a stranger's events under its own new id (a home
    that reported yesterday's people for a business with none). The ids go on from where they were."""
    with engine().begin() as conn:
        for name, last in marks.items():
            conn.execute(text("""
                SELECT setval(to_regclass(quote_ident(CAST(:name AS text))), CAST(:last AS bigint))
                WHERE CAST(:last AS bigint) > COALESCE((SELECT last_value FROM pg_sequences
                                                        WHERE schemaname = 'public' AND sequencename = :name), 0)"""),
                         {"name": name, "last": last})


def test_a_downgrade_and_upgrade_keep_every_events_business(s):
    p = projects.create(s, "다시 올리기", name="다시 올리기", budget_usd=10, start=False)
    loose = roadmap.create_bet(s, p.business_id, title="프로젝트 없는 도전", pitch="p")
    roadmap.update_bet(s, loose.id, hill="uphill")
    s.commit()
    before = s.scalars(select(ActivityEvent.id).where(ActivityEvent.business_id.is_not(None))).all()
    s.commit()
    assert before
    mig, kickoff_mig = _migration(), _migration("b3d9a1c7e5f2_kickoff_sessions")
    owner_tasks_mig = _migration("c4e8a2d6f1b9_owner_tasks")
    ideas_mig = _migration("d7a3c9e1b254_owner_ideas")
    people_mig = _migration("a7c2e9d4b681_people")
    bet_loop_mig = _migration("d7a3f5c1e982_bet_loop")
    listening_mig = _migration("b5e2d8f4a617_bet_listening")
    idea_reviews_mig = _migration("e3b7d1a9c428_idea_reviews")
    product_mig = _migration("c4e8a2f6b913_business_product")
    documents_mig = _migration("c2f7a9d4e815_documents")
    call_business_mig = _migration("d4a8e2c6b917_llm_call_business")
    cycle_stages_mig = _migration("e6c3a9f1b204_cycle_stages")
    look_mig = _migration("1e082314908b_business_look")
    marks = _sequence_marks()
    top_business = s.scalar(select(func.max(Business.id)))
    s.commit()
    try:
        with engine().begin() as conn, Operations.context(MigrationContext.configure(conn)):
            look_mig.downgrade()
            cycle_stages_mig.downgrade()
            call_business_mig.downgrade()
            documents_mig.downgrade()
            product_mig.downgrade()
            idea_reviews_mig.downgrade()
            listening_mig.downgrade()
            bet_loop_mig.downgrade()
            people_mig.downgrade()
            ideas_mig.downgrade()
            owner_tasks_mig.downgrade()
            kickoff_mig.downgrade()
            mig.downgrade()
            mig.upgrade()
            kickoff_mig.upgrade()
            owner_tasks_mig.upgrade()
            ideas_mig.upgrade()
            people_mig.upgrade()
            bet_loop_mig.upgrade()
            listening_mig.upgrade()
            idea_reviews_mig.upgrade()
            product_mig.upgrade()
            documents_mig.upgrade()
            call_business_mig.upgrade()
            cycle_stages_mig.upgrade()
            look_mig.upgrade()
    finally:
        _keep_ids_climbing(marks)
    assert s.scalar(text("SELECT nextval('businesses_id_seq')")) > top_business, "a new business reused an old id"
    lost = s.scalars(select(ActivityEvent.id).where(ActivityEvent.id.in_(before), ActivityEvent.business_id.is_(None)))
    assert not lost.all()
    assert not s.scalars(select(ActivityEvent.id).where(ActivityEvent.data.has_key("business_id"))).all()


def test_a_rung_a_checkpoint_points_at_cannot_be_removed(s, business):
    with session_scope() as x:
        rungs = roadmap.update_lists(x, business.id, lang="ko", rungs=[{"name": "문제 확인"}, {"name": "첫 손님"}]).rungs
    with session_scope() as x:
        roadmap.update_lists(x, business.id, lang="ko", checkpoints=[{"on": "2027-01-31", "title": "90일 판정",
                                                           "kind": "rung_exit", "rung_id": rungs[0]["id"]}])
    with pytest.raises(ControlError, match="90일 판정"), session_scope() as x:
        roadmap.update_lists(x, business.id, lang="ko", rungs=rungs[1:])
    s.refresh(business)
    assert [r["id"] for r in business.rungs] == [r["id"] for r in rungs]


def test_brief_line_ids_are_unique(s, business):
    lines = [{"id": "l-1", "text": "맞벌이 가정", "provenance": "said"},
             {"id": "l-1", "text": "주 1회", "provenance": "guess"}]
    with pytest.raises(ControlError), session_scope() as x:
        roadmap.new_brief_version(x, business.id, sections=[{"key": "a", "title": "손님", "lines": lines[:1]},
                                                            {"key": "b", "title": "구매", "lines": lines[1:]}],
                                  assumptions=[], author="owner")


def test_a_bet_cannot_land_on_a_track_removed_at_the_same_moment(s, business):
    with session_scope() as x:
        track = roadmap.update_lists(x, business.id, lang="ko", tracks=[{"name": "제품"}]).tracks[0]["id"]
    outcome = {}

    def propose():
        try:
            with session_scope() as y:
                roadmap.create_bet(y, business.id, title="t", pitch="p", track_id=track)
            outcome["result"] = "created"
        except ControlError:
            outcome["result"] = "refused"

    with session_scope() as x:
        roadmap.update_lists(x, business.id, lang="ko", tracks=[])
        x.flush()
        racer = threading.Thread(target=propose)
        racer.start()
        deadline = time.monotonic() + 10
        waiting = text("select count(*) from pg_stat_activity where datname = current_database() "
                       "and wait_event_type = 'Lock'")
        while not x.scalar(waiting):
            assert time.monotonic() < deadline, "the second writer never waited on the business"
            time.sleep(0.05)
    racer.join(10)
    assert outcome == {"result": "refused"}
    assert not s.scalars(select(Bet).where(Bet.track_id == track)).all()


def test_a_health_change_is_logged_once(s, business):
    bet = _bet(s, business.id, BetStatus.RUNNING)
    mine = (ActivityEvent.entity == "bet", ActivityEvent.entity_id == bet.id)
    before = s.scalar(select(func.count()).select_from(ActivityEvent).where(*mine))
    with session_scope() as x:
        roadmap.update_bet(x, bet.id, health="at_risk")
    after = s.scalars(select(ActivityEvent).where(*mine).order_by(ActivityEvent.id)).all()
    assert len(after) == before + 1 and after[-1].data["to"] == "at_risk"


def test_roadmap_changes_reach_the_live_feed(s, business):
    from justshipit.services import cycle
    heard: set[str] = set()
    with psycopg.connect(settings().checkpoint_dsn, autocommit=True) as listener:
        listener.execute(f"LISTEN {events.CHANNEL}")
        with session_scope() as x:
            roadmap.create_bet(x, business.id, title="t", pitch="p")
            cycle.open_cycle(x, business.id, lang="ko")
            roadmap.update_lists(x, business.id, lang="ko", tracks=[{"name": "손님"}])
        for note in listener.notifies(timeout=5):
            heard.add(json.loads(note.payload)["table"])
            if {"bets", "cycles", "businesses"} <= heard:
                break
    assert {"bets", "cycles", "businesses"} <= heard


def test_a_blank_project_name_falls_back_to_the_goal(s):
    blank = projects.create(s, "동네 빵집을 구독으로", name="   ", budget_usd=10, start=False)
    assert s.get(Business, blank.business_id).name == "동네 빵집을 구독으로"


def test_a_taken_full_length_slug_still_fits(s):
    slug = "a" * 80
    roadmap.create_business(s, name="먼저 있던 사업", one_liner="x", slug=slug)
    long = projects.create(s, "긴 이름", name="긴 이름", slug=slug, budget_usd=10, start=False)
    s.commit()
    taken = s.get(Business, long.business_id).slug
    assert taken != slug and len(taken) <= 80
