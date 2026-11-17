import json

import pytest

from justshipit.contracts.handoff import CodeReviewOut, SpecDraftOut
from justshipit.contracts.planning import ProjectManagerPlanOut, DecisionOut, FinalReportOut
from justshipit.contracts.schema import strict_json_schema
from justshipit.contracts.work import (
    AgentResultOut, EvidenceKind, EvidenceOut, FinancialModelOut, FinancialScenarioOut, ReviewOut, TaskDraft,
    TaskStatus,
)
from justshipit.services import evidence, finance, queue, tasks


pytestmark = pytest.mark.usefixtures("made_up_kinds")


def td(key, owner="research", deps=()):
    return TaskDraft(key=key, title=key, owner=owner, objective="o", acceptance_criteria=["c"], depends_on=list(deps))


@pytest.mark.parametrize("model", [AgentResultOut, ReviewOut, ProjectManagerPlanOut, DecisionOut, FinalReportOut,
                                   SpecDraftOut, CodeReviewOut])
def test_strict_schema_is_provider_compatible(model):
    schema = strict_json_schema(model)
    assert "$ref" not in json.dumps(schema)

    def check(node):
        if isinstance(node, dict):
            assert "default" not in node, "an enum value may be called default; a key may not"
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                check(v)
        elif isinstance(node, list):
            for v in node:
                check(v)
    check(schema)


def test_dag_resolution_cycles_and_owner_fallback(s, project):
    created, warnings = tasks.create_from_drafts(s, project, [
        td("market"), td("competitors"), td("finance", "finance", ["market", "competitors"]),
        td("a", deps=["b"]), td("b", deps=["a"]), td("x", "cfo_typo", ["missing"]),
    ], origin="ceo_plan", plan_round=1)
    by_key = {t.key: t for t in created}
    assert set(by_key["finance"].depends_on) == {by_key["market"].id, by_key["competitors"].id}
    assert by_key["x"].owner == "research"
    assert any("cycle" in w for w in warnings) and any("unknown dependency" in w for w in warnings)
    assert not (by_key["a"].id in by_key["b"].depends_on and by_key["b"].id in by_key["a"].depends_on)

    ready = {t.key for t in tasks.promote_ready(s, project.id)}
    assert {"market", "competitors"} <= ready and "finance" not in ready
    for k in ("market", "competitors"):
        t = by_key[k]
        for st in (TaskStatus.RUNNING, TaskStatus.REVIEW, TaskStatus.DONE):
            tasks.transition(t, st)
    assert "finance" in {t.key for t in tasks.promote_ready(s, project.id)}
    with pytest.raises(tasks.InvalidTransition):
        tasks.transition(by_key["market"], TaskStatus.RUNNING)


def test_duplicate_keys_get_suffixed(s, project):
    tasks.create_from_drafts(s, project, [td("market")], origin="ceo_plan", plan_round=1)
    created, _ = tasks.create_from_drafts(s, project, [td("market")], origin="follow_up", plan_round=2)
    assert created[0].key == "market_2"


def test_evidence_rules_agents_cannot_self_verify(s, project):
    ids = evidence.record(s, project.id, None, "research", [
        EvidenceOut(claim="a", kind=EvidenceKind.VERIFIED_FACT, source_url="https://example.com", source_title=None,
                    quote=None, published_at=None, confidence=2.0),
        EvidenceOut(claim="b", kind=EvidenceKind.SOURCED_CLAIM, source_url=None, source_title=None, quote=None,
                    published_at=None, confidence=0.5),
    ])
    from justshipit.db.models import Evidence
    a, b = (s.get(Evidence, i) for i in ids)
    assert a.kind == "sourced_claim" and a.confidence == 1.0
    assert b.kind == "hypothesis"


def test_finance_engine_is_deterministic():
    m = FinancialModelOut(currency="KRW", scenarios=[FinancialScenarioOut(
        name="base", price_per_customer_month=100_000, customers_month_1=10, monthly_customer_growth_rate=0.1,
        monthly_churn_rate=0.02, variable_cost_per_customer_month=20_000, fixed_cost_month=1_500_000,
        acquisition_cost_per_customer=300_000, months=24, assumption_notes=["가설"])])
    results, table, claims = finance.run_model(m)
    r = results[0]
    assert r.ltv == 4_000_000 and r.ltv_to_cac == 13.33 and r.payback_months == 3.8
    assert r.monthly[0]["revenue"] == 1_000_000
    assert finance.run_model(m)[0][0].cumulative_cash_end == r.cumulative_cash_end
    assert "base" in table and claims


def test_queue_idempotency_and_retry(s):
    j = queue.enqueue(s, "noop", {"x": 1}, idempotency_key="k-1", max_attempts=2)
    assert queue.enqueue(s, "noop", {"x": 1}, idempotency_key="k-1") is None
    s.flush()
    claimed = queue.claim(s, "w1", ["noop"])
    assert claimed.id == j.id and claimed.status == "running"
    queue.finish(s, j.id, claimed.lease_token, error="boom")
    assert claimed.status == "queued" and claimed.attempts == 1
