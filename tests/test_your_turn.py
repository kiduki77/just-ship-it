"""The owner's decisions go in the order of what each frees, and every project's progress rolls up into an outline, both
read from the one work graph. A card that holds the run comes before one that holds a plan step before one that holds
tasks, the one that frees more comes first, and the To do page's blocking tier uses the same order."""
from datetime import UTC, datetime, timedelta

import pytest
from bet_loop_kit import make_business
from fastapi.testclient import TestClient
from justshipit.api.app import app
from justshipit.contracts.work_path import HoldPhase, Rel, TargetKind
from justshipit.db.models import CycleStage, OwnerTask, RequestLink
from justshipit.services import projects, today, your_turn
from sqlalchemy import select
from test_card_context import card, task

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


@pytest.fixture
def biz(s):
    return make_business(s)


def project(s, biz, name="비교 실험", status="RUNNING", **kw):
    p = projects.create(s, "다음 기능을 정한다", name=name, budget_usd=10, start=False)
    p.business_id, p.status, p.bet_id = biz.id, status, None
    for k, v in kw.items():
        setattr(p, k, v)
    s.flush()
    return p


def hold(s, req, t, phase=HoldPhase.START):
    s.add(RequestLink(request_id=req.id, rel=Rel.HOLDS.value, target_kind=TargetKind.TASK.value, target_id=t.id,
                      phase=phase.value, revision=1, source="agent", known="recorded"))
    s.flush()


def order(s, biz) -> list[int]:
    return [c.id for c in your_turn.your_turn(s, biz.id, NOW).cards]


def test_a_card_that_holds_the_run_goes_before_a_plan_step_before_tasks_before_nothing(s, biz):
    p = project(s, biz)
    t = task(s, p, "a", "PROPOSED")
    nothing = card(s, p, title="n", scope="none")
    tasks_card = card(s, p, title="t", scope="tasks")
    hold(s, tasks_card, t)
    planning = card(s, p, title="p", scope="planning")
    run = card(s, p, title="r", scope="run")

    assert order(s, biz) == [run.id, planning.id, tasks_card.id, nothing.id]


def test_inside_one_scope_the_card_that_frees_more_goes_first_then_the_earlier_expiry_then_the_id(s, biz):
    p = project(s, biz)
    small, big, later, sooner = (card(s, p, title=x, scope="tasks") for x in "abcd")
    one, two, three = (task(s, p, k, "PROPOSED") for k in "xyz")
    hold(s, small, one)
    hold(s, big, two)
    hold(s, big, three)
    later.expires_at, sooner.expires_at = NOW + timedelta(days=3), NOW + timedelta(days=1)
    s.flush()

    # `later` and `sooner` hold nothing live, so they sort as none, by expiry.
    assert order(s, biz) == [big.id, small.id, sooner.id, later.id]


def test_frees_counts_the_held_tasks_nothing_else_holds_and_behind_the_tasks_after_them(s, biz):
    p = project(s, biz)
    a, b = task(s, p, "a", "PROPOSED"), task(s, p, "b", "PROPOSED")
    after = task(s, p, "c", "PROPOSED", depends_on=[a.id])
    later = task(s, p, "d", "PROPOSED", depends_on=[after.id])
    req = card(s, p, scope="tasks")
    hold(s, req, a)
    hold(s, req, b)

    (turn,) = your_turn.your_turn(s, biz.id, NOW).cards

    assert (turn.frees, turn.behind) == (2, 2) and later.id and not turn.nothing_live


def test_a_task_held_by_two_cards_is_in_neither_cards_frees(s, biz):
    p = project(s, biz)
    both, alone = task(s, p, "a", "PROPOSED"), task(s, p, "b", "PROPOSED")
    first, second = card(s, p, title="one", scope="tasks"), card(s, p, title="two", scope="tasks")
    for req in (first, second):
        hold(s, req, both)
    hold(s, first, alone)

    by_id = {c.id: c for c in your_turn.your_turn(s, biz.id, NOW).cards}

    assert (by_id[first.id].frees, by_id[second.id].frees) == (1, 0)


def test_a_run_card_frees_every_task_that_has_not_ended_and_a_planning_card_the_unstarted_ones_and_the_next_step(s, biz):
    p = project(s, biz)
    for key, status in (("a", "PROPOSED"), ("b", "READY"), ("c", "RUNNING"), ("d", "DONE"), ("e", "CANCELLED")):
        task(s, p, key, status)
    run, planning = card(s, p, title="r", scope="run"), card(s, p, title="p", scope="planning")

    by_id = {c.id: c for c in your_turn.your_turn(s, biz.id, NOW).cards}

    assert by_id[run.id].frees == 3 and by_id[planning.id].frees == 3


def test_a_card_whose_holds_are_all_moot_sorts_as_none_and_says_it_holds_nothing_now(s, biz):
    p = project(s, biz)
    gone = task(s, p, "a", "CANCELLED")
    moot = card(s, p, title="moot", scope="tasks")
    hold(s, moot, gone)
    plain = card(s, p, title="plain", scope="none", expires_at=NOW + timedelta(days=9))

    cards = {c.id: c for c in your_turn.your_turn(s, biz.id, NOW).cards}

    assert cards[moot.id].nothing_live and cards[moot.id].effective == "none" and cards[moot.id].scope == "tasks"
    assert cards[moot.id].effective == cards[plain.id].effective
    assert order(s, biz) == [plain.id, moot.id]  # both sort as none, so by expiry, the one with no date last

    items = {i.ref: i for i in today.collect(s, biz.id, NOW, "en")}
    assert items[f"REQ-{moot.id}"].reason == "Holds nothing now; it can still be answered"


def test_the_to_do_list_orders_its_blocking_decisions_by_what_they_free(s, biz):
    p = project(s, biz)
    one, two = task(s, p, "a", "PROPOSED"), task(s, p, "b", "PROPOSED")
    few, many = card(s, p, title="few", scope="tasks"), card(s, p, title="many", scope="tasks")
    hold(s, few, one)
    hold(s, many, two)
    other = task(s, p, "c", "PROPOSED")
    hold(s, many, other)

    refs = [i.ref for i in today.collect(s, biz.id, NOW, "en") if i.kind == "decision"]

    assert refs == [f"REQ-{many.id}", f"REQ-{few.id}"]


def test_the_outline_counts_done_over_all_but_cancelled_and_replaced_and_sums_up_to_the_stage_and_cycle(s, biz):
    stage = s.scalar(select(CycleStage).where(CycleStage.business_id == biz.id))
    first = project(s, biz, "first", stage_id=stage.id)
    second = project(s, biz, "second", stage_id=stage.id)
    for key, status in (("a", "DONE"), ("b", "DONE"), ("c", "RUNNING"), ("d", "FAILED"), ("e", "CANCELLED"),
                        ("f", "REPLACED")):
        task(s, first, key, status)
    held = task(s, second, "g", "PROPOSED")
    req = card(s, second, scope="tasks")
    hold(s, req, held)
    s.add(OwnerTask(business_id=biz.id, project_id=second.id, kind="post", title="올리기", status="OPEN"))
    s.flush()

    out = your_turn.your_turn(s, biz.id, NOW).outline

    cycle = out.children[0]
    assert cycle.kind == "cycle" and [c.kind for c in cycle.children] == ["stage"]
    projects_ = {n.id: n for n in cycle.children[0].children}
    f, sec = projects_[first.id], projects_[second.id]
    assert f.counts.model_dump() == {"done": 2, "total": 4, "held": 0, "failed": 1, "cards": 0, "todos": 0}
    assert [a.status for a in f.attention] == ["FAILED"]
    assert sec.counts.model_dump() == {"done": 0, "total": 1, "held": 1, "failed": 0, "cards": 1, "todos": 1}
    assert [c.id for c in sec.cards] == [req.id]
    assert cycle.children[0].counts.model_dump() == {"done": 2, "total": 5, "held": 1, "failed": 1, "cards": 1, "todos": 1}
    assert cycle.counts == cycle.children[0].counts == out.counts
    assert out.goal and "30" in out.goal


def test_an_archived_project_is_left_out_of_the_outline(s, biz):
    project(s, biz, "gone", status="ARCHIVED")
    live = project(s, biz, "live")

    assert [n.id for n in your_turn.your_turn(s, biz.id, NOW).outline.children] == [live.id]


def test_the_route_returns_the_cards_in_order_and_the_outline(s, biz, api):
    p = project(s, biz)
    t = task(s, p, "a", "PROPOSED")
    small, run = card(s, p, title="small", scope="tasks"), card(s, p, title="run", scope="run")
    hold(s, small, t)
    s.commit()

    body = api.get(f"/api/businesses/{biz.id}/your-turn").json()

    assert [c["id"] for c in body["cards"]] == [run.id, small.id] and [c["rank"] for c in body["cards"]] == [1, 2]
    assert body["cards"][0]["ref"] == f"REQ-{run.id}" and body["cards"][1]["frees"] == 1
    assert body["outline"]["children"][0]["kind"] == "project" and body["outline"]["children"][0]["counts"]["cards"] == 2
    assert api.get("/api/businesses/999999/your-turn").status_code == 404
