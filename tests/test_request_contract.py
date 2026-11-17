"""The request contract an agent fills (contracts/work_path.RequestContext, docs/design/work-path.md 2.1): a draft that
leaves it out, names an unknown reason or points at more than five records fails when the agent's output is parsed,
every output model that carries drafts requires it, and the link pairs the database accepts are the ones the contract
names."""
import json
import re

import pytest
from ask_kit import contract
from justshipit.contracts.chat import ChatReplyOut
from justshipit.contracts.operations import KpiReviewOut, PortfolioReviewOut
from justshipit.contracts.planning import ProjectManagerPlanOut, ProjectManagerReviewOut
from justshipit.contracts.requests import AgentAskDraft, AgentRequestDraft, RequestType, RiskLevel
from justshipit.contracts.schema import strict_json_schema
from justshipit.contracts.work import AgentResultOut
from justshipit.contracts.work_path import (
    ALLOWED_PAIRS, HOLD_SCOPE, MAX_REFS, HoldKind, Ref, RefKind, Rel, TargetKind,
)
from pydantic import ValidationError
from sqlalchemy import text


def drafted(**over) -> dict:
    """An agent's request as its JSON output holds it."""
    base = AgentAskDraft(type=RequestType.QUESTION, title="Which audience first", summary="s", rationale="r",
                         options=["Pet owners", "Cafés"], recommended_option="Pet owners", action_kind=None,
                         estimated_cost_usd=None, risk_level=RiskLevel.LOW,
                         context=contract(refs=("EV-14", "T-41"))).model_dump(mode="json")
    return {**base, **over}


def test_an_agent_draft_without_its_context_fails_when_the_output_is_parsed():
    no_context = {k: v for k, v in drafted().items() if k != "context"}

    with pytest.raises(ValidationError, match="context"):
        AgentAskDraft.model_validate(no_context)
    with pytest.raises(ValidationError, match="context"):
        AgentAskDraft.model_validate({**no_context, "context": None})
    assert AgentRequestDraft.model_validate(no_context).context is None, "a draft the server builds needs none"


def test_an_unknown_reason_or_more_than_five_refs_fails_at_parse():
    good = drafted()
    assert AgentAskDraft.model_validate(good).context.parsed_refs() == [Ref(RefKind.EVIDENCE, 14),
                                                                        Ref(RefKind.TASK, 41)]

    with pytest.raises(ValidationError, match="reason"):
        AgentAskDraft.model_validate({**good, "context": {**good["context"], "reason": "BECAUSE"}})
    too_many = [f"EV-{n}" for n in range(MAX_REFS + 1)]
    with pytest.raises(ValidationError, match="refs"):
        AgentAskDraft.model_validate({**good, "context": {**good["context"], "refs": too_many}})
    with pytest.raises(ValidationError, match="hold"):
        AgentAskDraft.model_validate({**good, "context": {**good["context"], "holds": {"kind": "everything",
                                                                                      "task_keys": []}}})


def test_a_ref_is_an_id_the_server_reads_or_a_task_key():
    assert [Ref.parse(x) for x in ("EV-14", "t-41", "REQ-6", "price_check")] == [
        Ref(RefKind.EVIDENCE, 14), Ref(RefKind.TASK, 41), Ref(RefKind.REQUEST, 6), Ref(RefKind.TASK, key="price_check")]


@pytest.mark.parametrize("model, path", [
    (AgentResultOut, ("requests", "items")), (ProjectManagerPlanOut, ("requests", "items")), (ProjectManagerReviewOut, ("requests", "items")),
    (KpiReviewOut, ("requests", "items")), (PortfolioReviewOut, ("requests", "items")), (ChatReplyOut, ("decision",)),
])
def test_every_output_that_carries_drafts_requires_a_context_the_model_cannot_leave_null(model, path):
    node = strict_json_schema(model)["properties"][path[0]]
    for step in path[1:]:
        node = node[step]
    draft = next(x for x in node["anyOf"] if x.get("type") == "object") if "anyOf" in node else node

    assert "context" in draft["required"]
    context = draft["properties"]["context"]
    assert context.get("type") == "object" and "anyOf" not in context, "required and never null"
    assert set(context["required"]) == {"reason", "refs", "note", "holds", "on_answer", "if_unanswered", "owner_work"}
    assert "GOAL_UNCLEAR" in context["properties"]["reason"]["description"]


def test_every_hold_an_agent_declares_has_one_scope():
    assert set(HOLD_SCOPE) == set(HoldKind)


def test_a_parsed_output_keeps_the_context_the_agent_wrote():
    raw = json.dumps({"summary": "s", "findings_markdown": "f", "evidence": [], "assumptions": [], "risks": [],
                      "open_questions": [], "unmet_criteria": [], "proposed_follow_up_tasks": [],
                      "requests": [drafted()], "capability_gaps": [], "financial_model": None})
    out = AgentResultOut.model_validate_json(raw)

    assert out.requests[0].context.refs == ["EV-14", "T-41"]
    assert out.requests[0].holds_work is False


_PAIR = re.compile(r"\(\(rel\)::text = '(\w+)'::text\) AND \(\(target_kind\)::text = ANY \(\(ARRAY\[([^\]]*)\]")


def test_the_allowed_pairs_are_the_check_the_database_holds(s):
    found = s.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_request_links_pair'"))
    pairs = {Rel(rel): frozenset(TargetKind(k) for k in re.findall(r"'(\w+)'", kinds))
             for rel, kinds in _PAIR.findall(found)}

    assert pairs == ALLOWED_PAIRS
