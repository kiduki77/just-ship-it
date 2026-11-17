"""The staffing rule is a table: every row is walked, every reason has a sentence in both languages, and the checker
is the one the old reviewer function (`reviewer_for`, now deleted) chose."""
import itertools
from dataclasses import replace

import pytest
from justshipit.i18n import LANGS, catalog, msg, placeholders
from justshipit.i18n.glossary import role
from justshipit.staffing import (
    AMENDMENT_REASONS,
    HISTORY_REASONS,
    RULES,
    Duty,
    Flow,
    JobFacts,
    Limits,
    Shape,
    Slot,
    SubjectKind,
    fixed_path,
    load_limits,
    place,
    verifier_role,
)

LIMITS = Limits(always_check=False, solo_max_criteria=1, audit_rate=0.2, audit_budget_usd=2.0, min_samples=20,
                solo_max_unmet_rate=0.1, model_tolerance=0.05, explore_rate=0.1)
LEAF = JobFacts(owner="research", family="market_research", criteria=1, dependents=0, after=(), alongside=(),
                feeds_build=False, origin="ceo_plan", analysed=False, resumed="fresh")
BUILD = replace(LEAF, kind=SubjectKind.BUILD, owner="engineer", family="build", criteria=3)

# For each check rule, what turns it on in an otherwise small leaf task: facts, then limits.
WITNESS: dict[str, tuple[dict, dict]] = {
    "check.feeds_build": ({"feeds_build": True}, {}),
    "check.analysed": ({"analysed": True}, {}),
    "check.numbers": ({"owner": "finance"}, {}),
    "check.reviewer_role": ({"owner": "risk"}, {}),
    "check.resumed": ({"resumed": "in_review"}, {}),
    "check.decision": ({"origin": "decision"}, {}),
    "check.owner_added": ({"origin": "owner"}, {}),
    "check.has_dependents": ({"dependents": 2}, {}),
    "check.criteria": ({"criteria": 2}, {}),
    "check.config": ({}, {"always_check": True}),
}


def held(f: JobFacts, limits: Limits = LIMITS) -> list[str]:
    return [r.id for r in RULES if r.kind is f.kind and r.holds(f, limits)]


def witnessed(rule_id: str) -> tuple[JobFacts, Limits]:
    facts, limits = WITNESS[rule_id]
    return replace(LEAF, **facts), replace(LIMITS, **limits)


def test_the_witness_table_names_every_check_rule_and_each_witness_turns_its_rule_on():
    assert set(WITNESS) == {r.id for r in RULES if r.shape is Shape.CHECKED}

    for rule_id in WITNESS:
        assert rule_id in held(*witnessed(rule_id)), rule_id


@pytest.mark.parametrize("rule_id", sorted(WITNESS))
def test_a_check_rule_adds_a_checker_and_says_which_rule_did(rule_id):
    facts, limits = witnessed(rule_id)

    placed = place(facts, limits)

    assert placed.slots == fixed_path(facts.owner)
    assert rule_id in [r.rule for r in placed.why]
    assert "solo.small_leaf" not in [r.rule for r in placed.why]
    assert placed.audit is False


def test_a_small_leaf_task_with_nothing_forcing_a_check_runs_alone_and_may_be_audited():
    placed = place(LEAF, LIMITS)

    assert placed.slots == (Slot(Duty.AUTHOR, "research", None),)
    assert [(r.rule, r.facts) for r in placed.why] == [("solo.small_leaf", {"criteria": 1})]
    assert placed.audit is True


def test_every_rule_that_holds_is_recorded_not_only_the_first():
    placed = place(replace(LEAF, feeds_build=True, dependents=1, criteria=3), LIMITS)

    assert [r.rule for r in placed.why] == ["check.feeds_build", "check.has_dependents", "check.criteria"]


def test_the_criteria_reason_records_the_count_and_the_limit_it_was_read_against():
    placed = place(replace(LEAF, criteria=4), replace(LIMITS, solo_max_criteria=3))

    assert placed.why[0].facts == {"criteria": 4, "solo_max_criteria": 3}


def test_the_checker_of_risk_is_strategy_and_the_reason_names_both_roles():
    placed = place(replace(LEAF, owner="risk"), LIMITS)

    assert [s.role for s in placed.slots] == ["risk", "strategy"]
    assert placed.why[0].facts == {"owner": "risk", "checker": "strategy"}


def test_the_old_path_is_the_author_and_its_reviewer():
    assert fixed_path("research") == (Slot(Duty.AUTHOR, "research", None), Slot(Duty.VERIFIER, "risk", None))
    assert fixed_path("risk")[1].role == "strategy"


def test_a_build_gets_the_fixed_chain_and_only_the_fixed_build_reason():
    placed = place(BUILD, LIMITS)

    assert [(s.duty, s.role) for s in placed.slots] == [
        (Duty.AUTHOR, "engineer"), (Duty.VERIFIER, "eng_reviewer"), (Duty.VERIFIER, "product")]
    assert [r.rule for r in placed.why] == ["fixed.build"]
    assert placed.audit is False
    assert [r.rule for r in place(replace(BUILD, dependents=4, feeds_build=True), LIMITS).why] == ["fixed.build"]


def test_no_combination_of_facts_leaves_a_task_without_exactly_one_shape():
    limit_grid = [replace(LIMITS, always_check=a, solo_max_criteria=m) for a in (False, True) for m in (0, 1, 3)]
    grid = itertools.product(("research", "risk", "finance", "analyst", "someone-new"), (False, True), (False, True),
                             ("fresh", "in_review", "reworked"), ("ceo_plan", "owner", "decision"), (0, 2),
                             (0, 1, 2, 5), limit_grid)
    for owner, feeds, analysed, resumed, origin, dependents, criteria, limits in grid:
        f = replace(LEAF, owner=owner, feeds_build=feeds, analysed=analysed, resumed=resumed, origin=origin,
                    dependents=dependents, criteria=criteria)
        placed = place(f, limits)
        checks = [r for r in RULES if r.shape is Shape.CHECKED and r.holds(f, limits)]

        assert (len(placed.slots) == 1) == (not checks), f
        assert placed.slots[0] == Slot(Duty.AUTHOR, owner, None), f
        assert placed.audit == (not checks), f
        assert ("solo.small_leaf" in [r.rule for r in placed.why]) == (not checks), f
        assert [r.rule for r in placed.why] == held(f, limits), f


def test_the_flow_is_read_from_the_plans_relations():
    assert LEAF.flow is Flow.ALONE
    assert replace(LEAF, after=(11,)).flow is Flow.AFTER
    assert replace(LEAF, alongside=(12,)).flow is Flow.ALONGSIDE
    assert replace(LEAF, after=(11,), alongside=(12,)).flow is Flow.AFTER


# ------------------------------------------------------------------ the old reviewer choice

@pytest.mark.parametrize(("owner", "checker"), [("research", "risk"), ("risk", "strategy"), ("strategy", "risk"),
                                                ("finance", "risk"), ("someone-not-on-the-roster", "risk"), ("", "risk")])
def test_the_verifier_is_the_one_the_old_reviewer_function_chose_including_an_unknown_role(owner, checker):
    assert verifier_role(owner) == checker


# ------------------------------------------------------------------ the sentences

@pytest.mark.parametrize("rule_id", [r.id for r in RULES])
def test_every_rule_has_a_sentence_in_both_languages_that_its_own_facts_can_fill(rule_id):
    rule = next(r for r in RULES if r.id == rule_id)
    facts, limits = (BUILD, LIMITS) if rule.kind is SubjectKind.BUILD else (
        witnessed(rule_id) if rule_id in WITNESS else (LEAF, LIMITS))
    cited = rule.cite(facts, limits)
    key = f"staffing.why.{rule_id}"

    assert key in catalog()
    assert set(catalog()[key]) == set(LANGS)
    assert placeholders(catalog()[key]) <= set(cited), "the sentence names a fact the rule does not record"
    for lang in LANGS:
        said = msg(key, lang, **{k: role(v, lang) if k in ("owner", "checker") else v for k, v in cited.items()})
        assert said and "{" not in said


def test_no_sentence_is_kept_for_a_rule_that_does_not_exist():
    keys = {k for k in catalog() if k.startswith("staffing.why.")}

    assert keys == {f"staffing.why.{r}" for r in [*(r.id for r in RULES), *AMENDMENT_REASONS, *HISTORY_REASONS]}


@pytest.mark.parametrize("reason", AMENDMENT_REASONS)
def test_every_reason_a_slot_is_added_for_has_a_sentence_in_both_languages(reason):
    assert all(msg(f"staffing.why.{reason}", lang) for lang in LANGS)


# ------------------------------------------------------------------ the limits

def test_the_shipped_file_loads_as_the_limits_the_rule_reads():
    limits = load_limits()

    assert limits.always_check is False and limits.solo_max_criteria == 1
    assert limits.model_ladder == ("haiku", "sonnet", "opus"), "cheapest first"


GOOD = {"always_check": True, "solo_max_criteria": 1, "audit_rate": 0.2, "audit_budget_usd": 2.0, "min_samples": 20,
        "solo_max_unmet_rate": 0.1, "model_tolerance": 0.05, "explore_rate": 0.1, "model_ladder": ["haiku", "sonnet"]}


def test_a_complete_file_loads():
    assert Limits.from_config(GOOD).model_ladder == ("haiku", "sonnet")


@pytest.mark.parametrize("change", [{"audit_rate": 1.5}, {"explore_rate": -0.1}, {"min_samples": 0},
                                    {"solo_max_criteria": -1}, {"always_check": "yes"}, {"audit_budget_usd": -1},
                                    {"model_ladder": "opus"}, {"model_ladder": ["sonnet", "sonnet"]},
                                    {"model_ladder": ["sonnet", ""]}])
def test_a_setting_out_of_range_is_refused_when_the_file_is_read(change):
    with pytest.raises((ValueError, TypeError)):
        Limits.from_config({**GOOD, **change})


def test_a_setting_missing_from_the_file_is_named_when_the_file_is_read():
    cfg = {k: v for k, v in GOOD.items() if k != "audit_rate"}

    with pytest.raises(ValueError, match="audit_rate"):
        Limits.from_config(cfg)
