"""The dashboard says what a business has spent so far, over all its projects (persona round 3: 이준호 saw only each
task's cost, $0.62 to $2.16, and asked for the total), and the calls made for it outside any project too: the kickoff
chat, the idea panel and finding people (round 7: the total left them out, so it read low)."""
import ast
from pathlib import Path

import pytest
from justshipit.agents import executor
from justshipit.api import console
from justshipit.contracts.planning import ProjectManagerPlanOut
from justshipit.db.models import Business, Job, KickoffSession, LLMCallRecord
from justshipit.db.session import session_scope
from justshipit.runtime.base import LLMResult
from justshipit.services import kickoff, projects, roadmap
from sqlalchemy import delete

pytestmark = pytest.mark.usefixtures("owner_asked", "strengths_answered")


BACKEND = Path(__file__).resolve().parents[1] / "backend" / "justshipit"
KICKOFF_TURN = {"kind": "question", "section": "path", "text": "q", "chips": [], "brief_patch": [], "done": False,
                "done_reason": ""}
PLAN = {"goal_interpretation": "g", "deliverables": [], "success_criteria": [], "out_of_scope": [],
        "key_uncertainties": [], "stop_rule": "", "build": None, "tasks": [], "requests": []}


@pytest.fixture
def billed(monkeypatch):
    """Every model call costs what the test says, and answers a kickoff turn or a plan."""
    cost = {"usd": 0.0}

    def run(call):
        out = KICKOFF_TURN if "running the kickoff chat" in call.system_prompt else PLAN
        return LLMResult(call_id=call.call_id, provider=call.provider, model="m", output=out, input_tokens=1,
                         output_tokens=1, cached_tokens=0, cost_usd=cost["usd"], duration_ms=1, session_id=None)
    monkeypatch.setattr(executor, "run_with_retry", run)
    yield cost
    with session_scope() as s:
        s.execute(delete(Job).where(Job.kind.in_(("kickoff_turn", "idea_review"))))


def test_the_overview_sums_every_project_of_the_business_archived_ones_too(s):
    b = roadmap.create_business(s, name="반품 예측", one_liner="쇼핑몰 반품 예측")
    other = roadmap.create_business(s, name="다른 사업", one_liner="다른 일")
    spent = [(b, "RUNNING", 3.5), (b, "ARCHIVED", 2.25), (other, "RUNNING", 9.0)]
    for i, (biz, status, usd) in enumerate(spent):
        p = projects.create(s, "목표", name=f"P{i}", start=False, business=biz)
        p.status, p.spent_usd = status, usd
    s.commit()
    spend = console.overview(business_id=b.id)["spend"]
    assert (spend["projects_usd"], spend["projects"], spend["total_usd"]) == (5.75, 2, 5.75)
    assert spend["outside_known"] is True, "no kickoff turn was taken, so nothing outside a project is missing"


def test_a_kickoff_turn_counts_toward_its_business_and_a_project_call_counts_once(billed):
    with session_scope() as s:
        opened = kickoff.open_session(s, name="반찬 예약", one_liner="동네 반찬 주간 예약", lang="ko")
        sid, bid = opened["session"]["id"], opened["session"]["business_id"]
        other = roadmap.create_business(s, name="옆 사업", one_liner="다른 일").id
    billed["usd"] = 0.4
    kickoff.run_turn(sid, 1)
    assert console.overview(business_id=bid)["spend"]["total_usd"] == 0.4, "the kickoff turn is the business's"
    assert console.overview(business_id=other)["spend"]["total_usd"] == 0.0

    with session_scope() as s:
        pid = projects.create(s, "목표", name="첫 프로젝트", start=False, business=s.get(Business, bid)).id
    billed["usd"] = 1.0
    executor.run_agent("project_manager", "plan", "plan it", ProjectManagerPlanOut, project_id=pid)
    spend = console.overview(business_id=bid)["spend"]
    assert (spend["projects_usd"], spend["outside_usd"], spend["total_usd"]) == (1.0, 0.4, 1.4)
    assert spend["outside_known"] is True


def test_a_kickoff_held_before_the_ledger_kept_its_business_says_the_total_misses_it(s):
    """Calls made before the ledger kept their business are in no total, so the owner is told this total is low."""
    opened = kickoff.open_session(s, name="예전 사업", one_liner="예전에 시작한 사업", lang="ko")
    sess = s.get(KickoffSession, opened["session"]["id"])
    sess.turns_used = 3
    s.add(LLMCallRecord(call_id="old-kickoff-call", agent="project_manager", purpose="kickoff", provider="claude", status="ok",
                        cost_usd=0.5))
    s.execute(delete(Job).where(Job.kind.in_(("kickoff_turn", "idea_review"))))
    s.commit()
    spend = console.overview(business_id=sess.business_id)["spend"]
    assert (spend["total_usd"], spend["outside_known"]) == (0.0, False)



def test_a_kickoff_that_ran_across_the_deploy_says_the_total_misses_its_early_turns(s):
    """The ledger keeps the business of the turns after the deploy and of none before it: some kickoff calls are found,
    yet fewer than the turns it took, so the total still misses the early ones."""
    opened = kickoff.open_session(s, name="걸친 사업", one_liner="배포 전후로 킥오프한 사업", lang="ko")
    sess = s.get(KickoffSession, opened["session"]["id"])
    sess.turns_used = 3
    s.add_all([LLMCallRecord(call_id="early-kickoff-call", agent="project_manager", purpose="kickoff", provider="claude",
                             status="ok", cost_usd=0.5),
               LLMCallRecord(call_id="late-kickoff-call", agent="project_manager", purpose="kickoff", provider="claude",
                             status="ok", cost_usd=0.5, business_id=sess.business_id)])
    s.execute(delete(Job).where(Job.kind.in_(("kickoff_turn", "idea_review"))))
    s.commit()
    spend = console.overview(business_id=sess.business_id)["spend"]
    assert (spend["total_usd"], spend["outside_known"]) == (0.5, False)

# Calls that work for the whole company, not one business: the skill factory and the portfolio review.
COMPANY_WIDE = {"capability/factory.py", "operations/jobs.py"}


def test_every_model_call_outside_a_project_names_its_business():
    """A call with no project and no business counts toward no business's spend, so only company-wide work may
    make one."""
    unnamed = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel in COMPANY_WIDE or rel == "agents/executor.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "run_agent"):
                continue
            kw = {k.arg: k.value for k in node.keywords}
            no_project = isinstance(kw.get("project_id"), ast.Constant) and kw["project_id"].value is None
            if no_project and "business_id" not in kw:
                unnamed.append(f"{rel}:{node.lineno}")
    assert unnamed == []
