"""The dashboard's board: one shape for every state a business's cycle can be in, the owner's decisions through the API,
what waits at a gate named one by one, and "working" meaning working."""
import cycle_kit
import pytest
from justshipit.contracts.cycle import CycleBoardOut
from justshipit.contracts.handoff import DevJobStatus
from justshipit.db.models import CycleProposal, DevJob, Job, LLMCallRecord, Spec, Task
from justshipit.services import cycle, cycle_board, projects, queue, requests, work_now
from fastapi.testclient import TestClient
from sqlalchemy import select
from ask_kit import by_project_manager


pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def _board(s, b) -> CycleBoardOut:
    return CycleBoardOut.model_validate(cycle_board.board(s, b.id, lang="en").model_dump())


def _settle(s, st) -> None:
    for p in cycle.stage_projects(s, st.id):
        p.status = "DONE"
    s.execute(Job.__table__.update().where(Job.stage_id == st.id, Job.kind.notin_(cycle._PROJECT_MANAGER_KINDS)).values(status="done"))


def _gate(s, c):
    st = cycle_kit.running(s, c)
    _settle(s, st)
    cycle.tick(s, lang="en")
    cycle.store_summary(s, st.id, st.round, None, lang="en")
    return st


def test_every_state_has_one_board_shape_and_its_own_sentence(s):
    seen = {}
    b = cycle_kit.business(s, "보드 없음")
    seen["none"] = _board(s, b)
    projects.start_business(s, b.id, lang="en")
    seen["proposing"] = _board(s, b)

    b = cycle_kit.business(s, "보드 제안")
    c = cycle_kit.proposed(s, b)
    seen["proposal"] = _board(s, b)
    cycle.decide(s, c.id, cycle.Revise(note="validate first", base_version=1), by="owner", lang="en")
    seen["revising"] = _board(s, b)
    cycle.fail_proposal(s, c.id, 2, "the model timed out")
    board = _board(s, b)
    assert board.state == "proposal" and board.waiting[0].revision_failed
    assert board.waiting[0].version == 1 and board.waiting[0].actions == ["start_cycle", "revise", "retry_proposal"]

    b = cycle_kit.business(s, "보드 실패")
    c = projects.start_business(s, b.id, lang="en")
    cycle.fail_proposal(s, c.id, 1, "the model timed out")
    seen["proposal_failed"] = _board(s, b)

    b = cycle_kit.business(s, "보드 이어받기")
    c = projects.start_business(s, b.id, lang="en")
    c.mode = "adopt"
    cycle.store_proposal(s, c.id, 1, cycle_kit.proposal_out(), {"live": {}}, lang="en")
    seen["legacy"] = _board(s, b)

    b = cycle_kit.business(s, "보드 진행")
    c = cycle_kit.started(s, b)
    seen["running"] = _board(s, b)
    st = cycle_kit.running(s, c)
    _settle(s, st)
    cycle.tick(s, lang="en")
    seen["closing"] = _board(s, b)
    cycle.store_summary(s, st.id, st.round, None, lang="en")
    seen["gate"] = _board(s, b)
    cycle.decide(s, c.id, cycle.StartNext(after_stage_id=st.id, version=cycle_kit.version(s, c.id)), by="owner", lang="en")
    nxt = cycle_kit.running(s, c)
    cycle.decide(s, c.id, cycle.StopStage(stage_id=nxt.id), by="owner", lang="en")
    seen["stage_stopped"] = _board(s, b)
    cycle.decide(s, c.id, cycle.StopHere(after_stage_id=nxt.id), by="owner", lang="en")
    seen["stopped"] = _board(s, b)

    b = cycle_kit.business(s, "보드 회고")
    c = cycle_kit.started(s, b)
    for st in cycle.stages_of(s, c.id):
        if st.status == "planned":
            st.status = "skipped"
    st = _gate(s, c)
    cycle.decide(s, c.id, cycle.FinishCycle(after_stage_id=st.id), by="owner", lang="en")
    seen["retro"] = _board(s, b)

    assert {k: v.state for k, v in seen.items()} == {k: k for k in seen}
    assert set(seen) == set(CycleBoardOut.model_fields["state"].annotation.__args__)
    assert len({v.sentence for v in seen.values()}) == len(seen)
    assert seen["running"].strip[0].is_current and seen["running"].current.tasks_total == 0
    assert seen["gate"].waiting[0].kind == "gate" and seen["gate"].waiting[0].actions[0] == "start_next"
    assert seen["gate"].waiting[0].proposal_version == 1   # Start and Revise from the gate name the version it shows
    assert seen["proposal"].waiting[0].actions == ["start_cycle", "revise"]
    assert seen["stage_stopped"].waiting[0].actions == ["resume_stage", "stop_here"]
    s.rollback()


def test_the_gate_names_what_waits_for_it(s):
    """Amendment 8 of the review: open decisions and parked work are listed by title at the gate, not only counted."""
    b = cycle_kit.business(s, "기다리는 것")
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    [p] = cycle.stage_projects(s, st.id)
    from owner_task_kit import draft
    ask = requests.create(s, draft(title="Show HN 게시"), from_agent="marketing", project_id=p.id, origin=by_project_manager(s, p.id))
    _settle(s, st)
    cycle.tick(s, lang="en")
    cycle.store_summary(s, st.id, st.round, None, lang="en")
    late = projects.create(s, "g", name="Late work", business=s.get(type(b), b.id))
    gate_card = _board(s, b).waiting[0]
    titles = {(w.kind, w.title) for w in gate_card.waiting}
    assert ("decision", "Show HN 게시") in titles and ("job", "Run project") in titles
    assert s.scalar(select(Job.status).where(Job.payload["project_id"].as_integer() == late.id)) == "gated"
    assert ask.id in {w.id for w in gate_card.waiting}
    s.rollback()


def test_working_means_working(s):
    """Probe F5 of the review: tasks of a paused or failed project and a build waiting on the owner showed as work."""
    from justshipit.services import control
    b = cycle_kit.business(s, "일하는 중")
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    [p] = cycle.stage_projects(s, st.id)
    p.status = "RUNNING"
    interview = Task(project_id=p.id, key="a", title="Interview", owner="research", objective="o",
                     acceptance_criteria=["c"], status="RUNNING", spent_usd=0.4)
    s.add(interview)
    s.flush()
    # a card's spend is what the task's calls cost, from the ledger the stage card and Costs add up
    s.add(LLMCallRecord(call_id=f"wn-{interview.id}", task_id=interview.id, project_id=p.id, agent="research",
                        purpose="task", provider="claude", status="ok", cost_usd=0.4))
    paused = projects.create(s, "g", name="Paused", start=False, business=b)
    paused.status = "RUNNING"
    s.add(Task(project_id=paused.id, key="b", title="Paused task", owner="marketing", objective="o",
               acceptance_criteria=["c"], status="RUNNING"))
    s.flush()
    control.pause(s, paused.id, "owner paused it")
    failed = projects.create(s, "g", name="Failed", start=False, business=b)
    failed.status = "FAILED"
    s.add(Task(project_id=failed.id, key="c", title="Stale task", owner="finance", objective="o",
               acceptance_criteria=["c"], status="RUNNING"))
    spec = Spec(spec_key=f"WN-{p.id}", project_id=p.id, title="Needs an answer", registry_path="specs/wn",
                status="IN_DEVELOPMENT", handoff={})
    s.add(spec)
    s.flush()
    s.add(DevJob(spec_id=spec.id, status=DevJobStatus.CLARIFICATION_REQUIRED.value, attempt=1))
    s.flush()
    rows = [r for r in work_now.rows(s, set()) if r.business_id == b.id]
    assert [(r.agent, r.title) for r in rows] == [("research", "Interview")]
    board = _board(s, b)
    [card] = board.working
    [item] = card.items
    assert (card.agent, item.cycle_number, item.stage_title, item.spent_usd) == ("research", 1, st.title, 0.4)
    assert board.idle_agents == len(set(work_now.agents_config())) - 1
    s.rollback()


def test_one_agent_is_one_card_and_every_count_counts_agents(s):
    """Round 1 of the first-time test: the sentence and the "Working now" count said 4 while the sidebar said 3,
    because an agent on two tasks got two cards."""
    from datetime import UTC, datetime, timedelta
    b = cycle_kit.business(s, "한 명 한 카드")
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    [p] = cycle.stage_projects(s, st.id)
    p.status = "RUNNING"
    now = datetime.now(UTC)
    for key, title, owner, ago in (("a", "Interview", "research", 30), ("b", "Survey", "research", 5),
                                   ("c", "Landing copy", "marketing", 10)):
        s.add(Task(project_id=p.id, key=key, title=title, owner=owner, objective="o", acceptance_criteria=["c"],
                   status="RUNNING", started_at=now - timedelta(minutes=ago)))
    queue.worker_seen(s, "worker-board-shape")   # "working" is said only while a worker runs
    s.flush()
    board = _board(s, b)
    assert [(card.agent, [i.title for i in card.items]) for card in board.working] == [
        ("research", ["Survey", "Interview"]), ("marketing", ["Landing copy"])]
    assert board.sentence == "Cycle 1 · " + st.title + " · 2 agents working"
    assert board.idle_agents == len(set(work_now.agents_config())) - 2
    s.rollback()


def test_idle_agents_the_owner_stopped_are_counted_apart(s):
    """The dashboard said "11 other agents are idle" while one of them was stopped by the owner and waiting on a
    resume (agent-detail-005): the board says how many of the idle agents are stopped."""
    from justshipit.services import stops
    b = cycle_kit.business(s, "멈춘 에이전트")
    c = cycle_kit.started(s, b)
    cycle_kit.running(s, c)
    agent = sorted(work_now.agents_config())[0]
    stops.stop_agent(s, agent, by="owner")
    s.flush()
    board = _board(s, b)
    assert board.idle_stopped == 1
    assert board.idle_agents == len(set(work_now.agents_config()))
    s.rollback()


@pytest.fixture
def api(api_token):
    from justshipit.api.app import app
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def test_the_owner_decides_through_one_endpoint_and_an_old_card_is_refused(s, api):
    b = cycle_kit.business(s, "API 결정")
    c = cycle_kit.proposed(s, b)
    s.commit()
    board = api.get(f"/api/businesses/{b.id}/cycle-board")
    assert board.status_code == 200 and board.json()["state"] == "proposal"
    started = api.post(f"/api/cycles/{c.id}/decisions", json={"kind": "start_cycle", "version": 1})
    assert started.status_code == 200 and started.json()["state"] == "running"
    again = api.post(f"/api/cycles/{c.id}/decisions", json={"kind": "start_cycle", "version": 1})
    assert again.status_code == 200 and again.json()["strip"] == started.json()["strip"]
    old = api.post(f"/api/cycles/{c.id}/decisions", json={"kind": "revise", "note": "x", "base_version": 0})
    assert old.status_code == 409 and "moved on" in old.json()["detail"]
    assert api.post(f"/api/cycles/{c.id}/decisions", json={"kind": "launch"}).status_code == 422
    assert [(r["number"], r["state"], r["stages_total"]) for r in api.get(f"/api/businesses/{b.id}/cycles").json()] == [
        (1, "running", 4)]
    detail = api.get(f"/api/cycles/{c.id}").json()
    assert [p["version"] for p in detail["proposals"]] == [1] and len(detail["stages"]) == 4
    assert s.scalar(select(CycleProposal.status).where(CycleProposal.cycle_id == c.id)) == "ready"


def test_the_cli_decides_through_the_same_path_and_names_who_decided(s):
    from justshipit.cli import DELEGATED, app
    from typer.testing import CliRunner
    b = cycle_kit.business(s, "CLI 결정")
    c = cycle_kit.proposed(s, b)
    s.commit()
    r = CliRunner().invoke(app, ["cycle", "decide", str(c.id), '{"kind": "start_cycle", "version": 1}', "--by", DELEGATED])
    assert r.exit_code == 0, r.output
    s.expire_all()
    assert cycle_kit.running(s, c).decided_by == DELEGATED
    r = CliRunner().invoke(app, ["cycle", "decide", str(c.id), '{"kind": "start_cycle", "version": 9}'])
    assert r.exit_code == 1 and "moved on" in r.output
