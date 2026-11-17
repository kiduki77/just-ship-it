"""A project's path view: the nodes and edges the console draws, with no coordinates. The chain to the focused card is
always drawn, a project draws at most 20 nodes by default and 60 unfolded, the ids and the revision follow the graph, and
a project past 150 nodes comes back as its outline."""
from datetime import UTC, datetime

import pytest
from bet_loop_kit import make_bet, make_business
from fastapi.testclient import TestClient
from justshipit.api.app import app
from justshipit.contracts.work_path import HoldPhase, Rel, TargetKind
from justshipit.db.models import CycleStage, RequestLink
from justshipit.services import path_view, projects
from sqlalchemy import select
from test_card_context import card, link, task

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


@pytest.fixture
def proj(s):
    business = make_business(s)
    bet = make_bet(s, business)
    p = projects.create(s, "다음 기능을 정한다", name="비교 실험", budget_usd=10, start=False)
    p.business_id, p.bet_id, p.status = business.id, bet.id, "RUNNING"
    p.stage_id = s.scalar(select(CycleStage.id).where(CycleStage.business_id == business.id))
    s.flush()
    return p


def hold(s, req, t, phase=HoldPhase.START):
    s.add(RequestLink(request_id=req.id, rel=Rel.HOLDS.value, target_kind=TargetKind.TASK.value, target_id=t.id,
                      phase=phase.value, revision=1, source="agent", known="recorded"))
    s.flush()


def many(s, proj, n, status="DONE"):
    """A chain of `n` tasks, each after the one before."""
    made = []
    for i in range(n):
        made.append(task(s, proj, f"k{len(made)}-{i}", status, depends_on=[made[-1].id] if made else []))
    return made


def view(s, proj, **kw):
    return path_view.path_view(s, proj.id, now=NOW, **kw)


def project_row(outline, project_id):
    """The project's row, wherever it sits: under its cycle and stage when the business has them, else at the top."""
    todo = list(outline.children)
    while todo:
        node = todo.pop()
        if node.kind == "project" and node.id == project_id:
            return node
        todo.extend(node.children)
    raise AssertionError(f"P-{project_id} is not in the outline")


def ids(v) -> set[str]:
    return {n.id for n in v.nodes}


def test_a_small_project_draws_whole_with_its_chain_and_no_coordinates(s, proj):
    asker = task(s, proj, "compare", "DONE")
    held = task(s, proj, "build", "PROPOSED", depends_on=[asker.id])
    req = card(s, proj, scope="tasks")
    link(s, req, Rel.RAISED_BY, TargetKind.TASK, asker.id)
    hold(s, req, held)

    v = view(s, proj, focus=req.id)

    assert not v.too_big and v.focus == f"r:{req.id}" and v.can_narrow and v.total == v.shown
    by_id = {n.id: n for n in v.nodes}
    assert sorted(n.kind for n in v.nodes if n.chain) == sorted(
        ["goal", "stage", "experiment", "project", "task", "decision"])
    assert by_id[f"r:{req.id}"].focus and by_id[f"r:{req.id}"].waiting and by_id[f"r:{req.id}"].frees == 1
    assert by_id[f"t:{held.id}"].held and not by_id[f"t:{asker.id}"].held
    edges = {(e.kind, e.source, e.target): e for e in v.edges}
    assert edges[("raised_by", f"t:{asker.id}", f"r:{req.id}")].known == "recorded"
    assert edges[("holds", f"r:{req.id}", f"t:{held.id}")].phase == "start"
    assert ("depends_on", f"t:{asker.id}", f"t:{held.id}") in edges
    assert not any({"x", "y", "position"} & set(n.model_dump()) for n in v.nodes)


def test_a_finish_hold_is_a_holds_result_edge(s, proj):
    waiting = task(s, proj, "ship", "APPROVAL_REQUIRED")
    req = card(s, proj, scope="tasks")
    hold(s, req, waiting, HoldPhase.FINISH)

    edge = next(e for e in view(s, proj).edges if e.kind == "holds")

    assert (edge.label_key, edge.phase) == ("path.edge.holds_result", "finish")


def test_an_old_card_with_no_raised_by_sits_beside_its_project_on_a_not_recorded_edge(s, proj):
    req = card(s, proj, scope="run")

    v = view(s, proj, focus=req.id)

    edges = {(e.kind, e.source, e.target): e for e in v.edges}
    raised = edges[("raised_by", f"p:{proj.id}", f"r:{req.id}")]
    assert (raised.known, raised.label_key) == ("unknown", "path.edge.raised_by_unknown")
    assert edges[("holds", f"r:{req.id}", f"p:{proj.id}")].label_key == "path.edge.holds_run"


def test_the_focus_chain_never_folds_however_much_else_there_is(s, proj):
    old = many(s, proj, 70)
    ask = old[3]
    req = card(s, proj, scope="tasks")
    link(s, req, Rel.RAISED_BY, TargetKind.TASK, ask.id)
    hold(s, req, task(s, proj, "held", "PROPOSED"))
    for i in range(3):
        hold(s, card(s, proj, title="other", scope="tasks"), task(s, proj, f"o{i}", "PROPOSED"))

    v = view(s, proj, focus=req.id)

    drawn = {n.id for n in v.nodes if n.kind != "group"}
    assert {f"t:{ask.id}", f"r:{req.id}", f"p:{proj.id}"} <= drawn
    assert sorted(n.kind for n in v.nodes if n.chain) == sorted(
        ["goal", "stage", "experiment", "project", "task", "decision"])
    group = next(n for n in v.nodes if n.id == "f:tasks_done")
    assert f"t:{ask.id}" not in group.members and group.count == len(group.members) > 0


def test_a_project_draws_at_most_20_nodes_by_default_and_every_pending_card_with_the_tasks_it_holds(s, proj):
    chain = many(s, proj, 50)
    cards = []
    for i in range(3):
        req = card(s, proj, title=f"q{i}", scope="tasks")
        hold(s, req, task(s, proj, f"h{i}", "PROPOSED"))
        cards.append(req)

    v = view(s, proj)

    assert v.shown <= 20 < v.total
    assert {f"r:{c.id}" for c in cards} <= ids(v)
    assert sum(1 for n in v.nodes if n.kind == "task" and n.held) == 3
    assert f"t:{chain[-1].id}" in ids(v) and f"t:{chain[0].id}" not in ids(v)   # a done neighbour, the oldest folds
    groups = [n for n in v.nodes if n.kind == "group"]
    assert sum(n.count for n in groups) + sum(1 for n in v.nodes if n.kind != "group") == v.total


def test_unfolding_a_group_opens_its_members_up_to_60_nodes(s, proj):
    many(s, proj, 100)

    folded = view(s, proj)
    opened = view(s, proj, unfold=["f:tasks_done"])
    everything = view(s, proj, unfold=["all"])

    assert folded.shown <= 20 and opened.shown == 60 and everything.shown == 60
    assert opened.unfolded == ["f:tasks_done"]
    drawn = lambda v: {n.id for n in v.nodes if n.kind != "group"}
    assert drawn(folded) < drawn(opened)
    rest = next(n for n in opened.nodes if n.id == "f:tasks_done")
    assert rest.count == len(rest.members) == opened.total - len(drawn(opened))


def test_a_link_to_a_folded_node_ends_at_its_group(s, proj):
    chain = many(s, proj, 40)
    held = task(s, proj, "held", "PROPOSED", depends_on=[chain[-1].id])
    hold(s, card(s, proj, scope="tasks"), held)

    v = view(s, proj)

    group_edges = [e for e in v.edges if e.via_group]
    assert group_edges and all("f:" in e.source or "f:" in e.target for e in group_edges)
    assert {e.source for e in v.edges} | {e.target for e in v.edges} <= ids(v)


def test_ids_survive_a_revision_and_rev_changes_only_when_the_graph_does(s, proj):
    a = task(s, proj, "a", "RUNNING")
    b = task(s, proj, "b", "PROPOSED", depends_on=[a.id])
    req = card(s, proj, scope="tasks")
    hold(s, req, b)
    before = view(s, proj, focus=req.id)

    same = view(s, proj)
    a.status = "DONE"
    c = task(s, proj, "c", "PROPOSED", depends_on=[b.id])
    s.flush()
    after = view(s, proj, focus=req.id)

    assert same.rev == before.rev and before.rev != after.rev
    assert ids(before) <= ids(after) and {f"t:{a.id}", f"t:{b.id}", f"r:{req.id}"} <= ids(before)
    assert f"t:{c.id}" in ids(after) and f"t:{c.id}" not in ids(before)
    assert {e.id for e in before.edges if e.kind != "contains"} <= {e.id for e in after.edges}


def test_a_project_past_150_nodes_is_too_big_and_returns_its_outline_instead(s, proj):
    many(s, proj, 160)

    v = view(s, proj)

    assert v.too_big and v.nodes == [] and v.edges == [] and v.total > 150 and not v.can_narrow
    project = project_row(v.outline, proj.id)
    assert project.counts.total == 160 and project.counts.done == 160


def test_the_neighbourhood_of_a_card_draws_a_project_that_is_too_big(s, proj):
    chain = many(s, proj, 160)
    req = card(s, proj, scope="tasks")
    link(s, req, Rel.RAISED_BY, TargetKind.TASK, chain[100].id)
    hold(s, req, task(s, proj, "held", "PROPOSED", depends_on=[chain[100].id]))

    whole = view(s, proj, focus=req.id)
    near = view(s, proj, focus=req.id, neighbourhood=True)

    assert whole.too_big and whole.can_narrow
    assert not near.too_big and near.neighbourhood and not near.can_narrow and near.total < 30
    assert {f"t:{chain[100].id}", f"r:{req.id}"} <= ids(near) and f"t:{chain[0].id}" not in ids(near)


def test_a_focus_that_is_not_a_card_of_the_project_is_not_found(s, proj):
    other = projects.create(s, "x", name="other", budget_usd=1, start=False)
    elsewhere = card(s, other)

    with pytest.raises(path_view.NotFound):
        view(s, proj, focus=elsewhere.id)


def test_the_route_returns_the_view_and_refuses_what_it_cannot_find(s, proj, api):
    asker = task(s, proj, "compare", "DONE")
    req = card(s, proj, scope="tasks")
    link(s, req, Rel.RAISED_BY, TargetKind.TASK, asker.id)
    hold(s, req, task(s, proj, "held", "PROPOSED"))
    s.commit()

    body = api.get(f"/api/projects/{proj.id}/path", params={"focus": f"REQ-{req.id}"}).json()

    assert body["focus"] == f"r:{req.id}" and not body["too_big"] and body["limits"] == {"default": 20, "unfolded": 60,
                                                                                         "too_big": 150}
    assert {f"t:{asker.id}", f"r:{req.id}"} <= {n["id"] for n in body["nodes"] if n["chain"]}
    assert {e["kind"] for e in body["edges"]} >= {"raised_by", "holds", "contains"}
    assert body["outline"]["children"][0]["counts"]["cards"] == 1
    assert api.get(f"/api/projects/{proj.id}/path", params={"focus": "REQ-999999"}).status_code == 404
    assert api.get(f"/api/projects/{proj.id}/path", params={"focus": "nonsense"}).status_code == 422
    assert api.get("/api/projects/999999/path").status_code == 404


def test_the_neighbourhood_reaches_the_project_but_never_goes_on_through_it(s, proj):
    here = card(s, proj, scope="run")
    for _ in range(3):
        card(s, proj, scope="run")

    near = view(s, proj, focus=here.id, neighbourhood=True)

    assert {n.id for n in near.nodes if n.kind == "decision"} == {f"r:{here.id}"}
