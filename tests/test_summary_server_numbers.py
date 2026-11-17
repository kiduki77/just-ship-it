"""The summary at a stage's gate: the numbers the owner reads are the server's, an id the CEO was not shown is dropped,
and a claim's confidence is capped by what its evidence supports in the ledger."""
import pytest
import cycle_kit
from justshipit.contracts.cycle import Claim, StageSummaryOut
from justshipit.db.models import Evidence, LLMCallRecord, Task
from justshipit.services import cycle, cycle_board, evidence


pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def _out(claims, **kw) -> StageSummaryOut:
    base = {"headline": "It went well.", "done": ["Talked to five people"], "not_done": [], "claims": claims,
            "needs_owner": [], "next_key": "plan", "next_reason": "Next in line.", "change_suggestions": []}
    return StageSummaryOut(**{**base, **kw})


def test_the_summary_keeps_the_servers_numbers_and_caps_each_claim_by_its_evidence(s):
    b = cycle_kit.business(s, "요약 숫자")
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    [p] = cycle.stage_projects(s, st.id)
    t = Task(project_id=p.id, key="t1", title="Interview five people", owner="research", objective="o",
             acceptance_criteria=["c"], status="DONE")
    s.add(t)
    s.flush()
    firm = Evidence(project_id=p.id, task_id=t.id, agent="research", claim="5 of 5 said yes", kind="verified_fact",
                    verification_status="supported")
    guess = Evidence(project_id=p.id, task_id=t.id, agent="research", claim="People will pay", kind="hypothesis")
    s.add_all([firm, guess])
    s.add(LLMCallRecord(call_id=f"sum-{st.id}", project_id=p.id, business_id=b.id, stage_id=st.id, agent="research",
                        purpose="work", provider="claude", status="ok", cost_usd=1.25))
    p.status = "DONE"
    s.execute(cycle.Job.__table__.update().where(cycle.Job.stage_id == st.id).values(status="done"))
    cycle.tick(s, lang="en")
    out = _out([Claim(claim="They want it", evidence_ids=[evidence.ev_id(firm.id)], confidence="high"),
                Claim(claim="They will pay", evidence_ids=[evidence.ev_id(guess.id), "EV-999999"], confidence="high"),
                Claim(claim="It is easy", evidence_ids=[f"T-{t.id}"], confidence="mid")])
    assert cycle.store_summary(s, st.id, st.round, out, lang="en")
    summary = st.summary
    assert (summary["cost_usd"], summary["tasks"]) == (1.25, {"done": 1, "failed": 0, "total": 1})
    assert [(x["confidence"], x["capped"]) for x in summary["claims"]] == [("high", False), ("low", True), ("low", True)]
    assert summary["claims"][1]["evidence_ids"] == [evidence.ev_id(guess.id)], "an id it was not shown is dropped"
    assert summary["next_key"] == "plan" and st.status == "awaiting_owner"
    assert cycle_board.board(s, b.id, lang="en").current.summary.by == "project_manager", "the Project Manager wrote it"
    assert cycle.store_summary(s, st.id, st.round, out, lang="en") is False, "the first answer wins"
    s.rollback()


def test_a_next_stage_the_cycle_does_not_have_is_not_suggested(s):
    b = cycle_kit.business(s, "없는 다음")
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    for p in cycle.stage_projects(s, st.id):
        p.status = "DONE"
    s.execute(cycle.Job.__table__.update().where(cycle.Job.stage_id == st.id).values(status="done"))
    cycle.tick(s, lang="en")
    cycle.store_summary(s, st.id, st.round, _out([], next_key="launch-to-the-moon"), lang="en")
    assert st.summary["next_key"] is None
    s.rollback()
