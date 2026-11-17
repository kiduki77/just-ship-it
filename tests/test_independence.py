"""A review is independent when a different role ran it as its own call. The models that ran never gate it."""
import pytest

from justshipit.agents.executor import AgentFailed
from justshipit import staffing
from justshipit.agents.independence import Served, assess
from justshipit.contracts.work import AgentResultOut, ClaimCheckOut, EvidenceKind, EvidenceOut, ReviewOut, ReviewVerdict
from justshipit.db.models import Evidence, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import company_graph
from justshipit.settings import agents_config

OPUS, SONNET = Served("claude", "claude-opus-4-5"), Served("claude", "claude-sonnet-4-5")


@pytest.mark.parametrize(("author_role", "author", "reviewer_role", "reviewer", "independent", "label"), [
    ("research", SONNET, "risk", OPUS, True,
     "review by risk (claude/claude-opus-4-5) of research (claude/claude-sonnet-4-5)"),
    ("risk", OPUS, "strategy", SONNET, True,
     "review by strategy (claude/claude-sonnet-4-5) of risk (claude/claude-opus-4-5)"),
    ("research", OPUS, "risk", OPUS, True,
     "review by risk (claude/claude-opus-4-5) of research (claude/claude-opus-4-5)"),
    ("research", Served("claude", None), "risk", None, True, "review by risk (unknown) of research (claude/unknown)"),
    ("risk", OPUS, "risk", OPUS, False,
     "not independent: review by the author's own role, risk (claude/claude-opus-4-5) of risk (claude/claude-opus-4-5)"),
])
def test_a_review_is_independent_when_a_different_role_ran_it_whatever_the_models(
        author_role, author, reviewer_role, reviewer, independent, label):
    verdict = assess(author_role, author, reviewer_role, reviewer)
    assert (verdict.independent, verdict.label) == (independent, label)


def test_the_roster_runs_on_claude_and_never_reviews_an_author_with_the_author():
    cfg = agents_config()
    assert {c["provider"] for c in cfg.values()} == {"claude"}
    reviewable = [a for a, c in cfg.items() if c.get("department") in ("business", "audit")]
    assert "risk" in reviewable and "research" in reviewable
    for author in reviewable:
        reviewer = staffing.verifier_role(author)
        assert reviewer in cfg and reviewer != author


def test_risk_reviews_everyone_and_risk_is_reviewed_by_strategy():
    assert staffing.verifier_role("research") == "risk"
    assert staffing.verifier_role("risk") == "strategy"
    assert staffing.verifier_role("someone-not-on-the-roster") == "risk"


def _result() -> AgentResultOut:
    return AgentResultOut(
        summary="s", findings_markdown="f [E1]",
        evidence=[EvidenceOut(claim="claim", kind=EvidenceKind.SOURCED_CLAIM, source_url="https://example.org/a",
                              source_title="t", quote="q", published_at="2025", confidence=0.9)],
        assumptions=[], risks=[], open_questions=[], proposed_follow_up_tasks=[], requests=[], capability_gaps=[],
        financial_model=None)


def _review() -> ReviewOut:
    return ReviewOut(verdict=ReviewVerdict.PASS, criteria_met=["c"], criteria_unmet=[],
                     claim_checks=[ClaimCheckOut(evidence_index=0, status="supported", note="ok",
                                                 counter_source_url=None)],
                     counter_arguments=[], rework_instructions=None, contested=False)


def _run_work(project, monkeypatch, served, reviewer_fails=False):
    monkeypatch.setattr(company_graph, "last_served", lambda: next(served))

    def fake_run_agent(agent, purpose, prompt, model, **kw):
        if model is ReviewOut and reviewer_fails:
            raise AgentFailed("review: quota")
        return _result() if model is AgentResultOut else _review()

    def reachable(ids):
        with session_scope() as s2:
            for i in ids:
                s2.get(Evidence, i).verification_status = "url_ok"

    monkeypatch.setattr(company_graph, "run_agent", fake_run_agent)
    monkeypatch.setattr(company_graph.evidence, "verify_sources", reachable)
    with session_scope() as s2:
        # Two criteria, so its crew has a checker (a one-criterion leaf would run alone with no review).
        t = Task(project_id=project.id, key="r", title="t", owner="research", objective="o",
                 acceptance_criteria=["c", "d"], status="READY", max_iterations=1)
        s2.add(t)
        s2.flush()
        tid = t.id
    company_graph.work({"project_id": project.id, "task_id": tid})
    with session_scope() as s2:
        t = s2.get(Task, tid)
        e = s2.get(Evidence, t.result["evidence_ids"][0])
        return t.review, (e.kind, e.verification_status)


PROMOTED, UNCHANGED = ("verified_fact", "supported"), ("sourced_claim", "url_ok")


@pytest.mark.parametrize(("reviewer_served", "label"), [
    (OPUS, "review by risk (claude/claude-opus-4-5) of research (claude/claude-sonnet-4-5)"),
    (SONNET, "review by risk (claude/claude-sonnet-4-5) of research (claude/claude-sonnet-4-5)"),
])
def test_a_separate_review_promotes_a_supported_claim_even_on_the_authors_model(
        s, project, monkeypatch, reviewer_served, label):
    s.commit()
    review, evidence = _run_work(project, monkeypatch, iter([SONNET, reviewer_served]))
    assert evidence == PROMOTED
    assert review["independence"] == label and review["served_by"] == str(reviewer_served)


def test_a_failed_review_promotes_nothing(s, project, monkeypatch):
    s.commit()
    review, evidence = _run_work(project, monkeypatch, iter([SONNET, OPUS]), reviewer_fails=True)
    assert evidence == UNCHANGED
    assert review["verdict"] == "unreviewed" and "independence" not in review


def test_a_review_by_the_authors_own_role_promotes_nothing(s, project, monkeypatch):
    s.commit()
    monkeypatch.setattr(staffing, "verifier", lambda crew: staffing.Slot(staffing.Duty.VERIFIER, "research", None))
    review, evidence = _run_work(project, monkeypatch, iter([SONNET, SONNET]))
    assert evidence == UNCHANGED
    assert review["independence"].startswith("not independent")
