"""Version 2 of the staffing rule reads history (docs/design/staffing-structure.md section 7, unit 13). The rule stays a
pure function, so these are table tests: below `min_samples` it is version 1 exactly, a forcing check is never
overridden, history may only waive an optional check, return a family to checked or lower the author's model, and
every such decision records the statistics it read."""
import itertools
from dataclasses import replace

import pytest
from justshipit.i18n import LANGS, catalog, msg, placeholders
from justshipit.staffing import (
    HISTORY_REASONS,
    RULES,
    History,
    JobFacts,
    Limits,
    Shape,
    Stat,
    SubjectKind,
    Tally,
    fixed_path,
    place,
)

LIMITS = Limits(always_check=False, solo_max_criteria=1, audit_rate=0.2, audit_budget_usd=2.0, min_samples=20,
                solo_max_unmet_rate=0.1, model_tolerance=0.05, explore_rate=0.1,
                model_ladder=("haiku", "sonnet", "opus"))
LEAF = JobFacts(owner="research", family="market_research", criteria=1, dependents=0, after=(), alongside=(),
                feeds_build=False, origin="ceo_plan", analysed=False, resumed="fresh")
HARD = replace(LEAF, criteria=3)                       # checked by the optional criteria check only
ROSTER = {"research": "sonnet", "strategy": "sonnet", "finance": "sonnet", "analyst": "sonnet", "risk": "opus",
          "marketing": "sonnet"}


def stat(model="sonnet", *, alone=(0, 0), checked=(0, 0), role="research", family="market_research", calls=0,
         reworks=0) -> Stat:
    return Stat(family, role, model, Tally(*alone), Tally(*checked), calls, reworks)


def history(*stats: Stat, draw=1.0, roster=ROSTER) -> History:
    return History(tuple(stats), roster, draw)


def rules(placed) -> list[str]:
    return [r.rule for r in placed.why]


def reason(placed, rule_id):
    (found,) = [r for r in placed.why if r.rule == rule_id]
    return found


# ------------------------------------------------------------------ inert below min_samples

UNDER = [
    history(),
    history(stat(alone=(19, 0))),
    history(stat(alone=(19, 19))),
    history(stat(alone=(10, 0)), stat(role="marketing", alone=(9, 0))),
    history(stat(checked=(19, 0)), stat("haiku", checked=(19, 0))),
    history(stat(alone=(10, 0), checked=(9, 0)), stat("haiku", checked=(500, 0))),
    history(stat(alone=(50, 0), family="regulation_check"), stat(alone=(50, 50), family="regulation_check")),
    history(stat(checked=(50, 0), family="regulation_check"), stat("haiku", checked=(50, 0), family="regulation_check")),
]


@pytest.mark.parametrize("hist", UNDER)
def test_a_family_under_min_samples_is_placed_exactly_as_version_one(hist):
    grid = itertools.product((LEAF, HARD, replace(LEAF, dependents=2), replace(LEAF, owner="risk"),
                              replace(LEAF, feeds_build=True)), (0.0, 0.05, 0.5))
    for facts, draw in grid:
        assert place(facts, LIMITS, replace(hist, draw=draw)) == place(facts, LIMITS), (facts, draw)


def test_an_empty_database_changes_nothing_for_any_draw():
    for draw in (0.0, 0.01, 0.099, 0.5):
        assert place(LEAF, LIMITS, history(draw=draw)) == place(LEAF, LIMITS)
        assert place(HARD, LIMITS, history(draw=draw)) == place(HARD, LIMITS)


# ------------------------------------------------------------------ alone or checked, from the shadow checks

@pytest.mark.parametrize(("facts", "alone", "shape", "why", "waived"), [
    (HARD, (20, 1), Shape.SOLO, ["history.solo"], "check.criteria"),
    (replace(HARD, dependents=2), (20, 1), Shape.SOLO, ["history.solo"], "check.has_dependents check.criteria"),
    (HARD, (40, 3), Shape.SOLO, ["history.solo"], "check.criteria"),
    (LEAF, (20, 1), Shape.SOLO, ["solo.small_leaf"], None),
    (LEAF, (20, 2), Shape.CHECKED, ["history.checked"], None),
    (HARD, (20, 2), Shape.CHECKED, ["check.criteria", "history.checked"], None),
    (LEAF, (20, 20), Shape.CHECKED, ["history.checked"], None),
    (HARD, (19, 0), Shape.CHECKED, ["check.criteria"], None),
])
def test_the_shadow_unmet_rate_decides_alone_or_checked_once_the_family_has_min_samples(facts, alone, shape, why,
                                                                                         waived):
    placed = place(facts, LIMITS, history(stat(alone=alone)))

    assert rules(placed) == why
    if shape is Shape.SOLO:
        assert [s.role for s in placed.slots] == [facts.owner] and placed.audit is True
    else:
        assert placed.slots == fixed_path(facts.owner) and placed.audit is False
    if waived is not None:
        assert reason(placed, "history.solo").facts["waived"] == waived


def test_a_rate_exactly_at_the_limit_returns_the_family_to_checked_with_the_reason():
    """2 of 20 is the 0.1 limit itself, which a float subtraction would put a hair under."""
    placed = place(LEAF, LIMITS, history(stat(alone=(20, 2))))

    assert rules(placed) == ["history.checked"]
    assert reason(placed, "history.checked").facts == {
        "samples": 20, "unmet": 2, "unmet_pct": 10, "solo_max_unmet_rate": 0.1, "limit_pct": 10, "min_samples": 20}


def test_the_shadow_checks_of_every_role_in_the_family_count_and_no_other_familys():
    split = history(stat(alone=(12, 0)), stat(role="marketing", alone=(8, 0)),
                    stat(alone=(100, 100), family="regulation_check"))

    placed = place(HARD, LIMITS, split)

    assert rules(placed) == ["history.solo"]
    assert reason(placed, "history.solo").facts["samples"] == 20


def test_a_build_is_never_placed_by_history():
    build = replace(LEAF, kind=SubjectKind.BUILD, owner="engineer", family="build")

    assert place(build, LIMITS, history(stat(alone=(99, 99), family="build"), draw=0.0)) == place(build, LIMITS)


# ------------------------------------------------------------------ forcing checks

FORCING_WITNESS = {
    "check.feeds_build": {"feeds_build": True},
    "check.analysed": {"analysed": True, "owner": "analyst"},
    "check.numbers": {"owner": "finance"},
    "check.reviewer_role": {"owner": "risk"},
    "check.resumed": {"resumed": "reworked"},
    "check.decision": {"origin": "decision"},
    "check.owner_added": {"origin": "owner"},
}


def aggressive(f: JobFacts) -> list[History]:
    """Every way history could pull at a job: alone is safe, alone is unsafe, a cheaper model is as good, and a draw
    that would explore."""
    on = {"role": f.owner, "family": f.family}
    return [
        history(stat(alone=(100, 0), **on)),
        history(stat(alone=(100, 100), **on)),
        history(*(stat(m, checked=(100, 0), **on) for m in ("haiku", "sonnet", "opus"))),
        history(stat("sonnet", checked=(100, 0), **on), stat("opus", checked=(100, 0), **on), draw=0.0),
        history(stat("sonnet", alone=(100, 0), checked=(100, 0), **on), draw=0.0),
    ]


def test_the_forcing_checks_are_the_ones_the_design_names():
    assert {r.id for r in RULES if r.forcing} == {*FORCING_WITNESS, "check.config"}
    assert {r.id for r in RULES if r.shape is Shape.CHECKED and not r.forcing} == {"check.criteria",
                                                                                  "check.has_dependents"}


@pytest.mark.parametrize("rule_id", sorted(FORCING_WITNESS) + ["check.config"])
def test_no_history_overrides_a_forcing_check(rule_id):
    limits = replace(LIMITS, always_check=True) if rule_id == "check.config" else LIMITS
    for base in (LEAF, HARD):
        facts = replace(base, **FORCING_WITNESS.get(rule_id, {}))
        assert rule_id in rules(place(facts, limits))
        for hist in aggressive(facts):
            assert place(facts, limits, hist) == place(facts, limits), (facts, hist)


# ------------------------------------------------------------------ the author's model

@pytest.mark.parametrize(("stats", "chosen"), [
    ((stat(checked=(20, 2)), stat("haiku", checked=(20, 3))), "haiku"),            # 85% is 90% less 5 points
    ((stat(checked=(20, 2)), stat("haiku", checked=(20, 4))), None),               # 80% is too far behind
    ((stat(checked=(20, 2)), stat("haiku", checked=(20, 0))), "haiku"),            # cheaper and better
    ((stat(checked=(20, 2)), stat("haiku", alone=(10, 0), checked=(10, 1))), "haiku"),  # shadow checks judge too
    ((stat(checked=(20, 2)), stat("haiku", checked=(19, 0))), None),               # haiku has too few samples
    ((stat(checked=(19, 0)), stat("haiku", checked=(50, 0))), None),               # sonnet has too few samples
    ((stat(checked=(40, 20)), stat("opus", checked=(40, 0))), None),               # never raised to a dearer model
    ((stat(checked=(20, 2)), stat("haiku", checked=(20, 0), role="strategy")), None),  # another role's record
])
def test_the_cheaper_model_is_chosen_only_within_tolerance_and_with_min_samples_on_both(stats, chosen):
    placed = place(LEAF, LIMITS, history(*stats))

    assert placed.slots[0].model == chosen
    assert ("history.model" in rules(placed)) == (chosen is not None)
    assert [s.role for s in placed.slots] == ["research"], "the model never changes who works"


def test_a_role_on_the_dearest_model_moves_to_the_cheapest_one_within_tolerance():
    hist = history(stat("opus", checked=(20, 2)), stat("sonnet", checked=(20, 2)), stat("haiku", checked=(20, 3)),
                   roster={"research": "opus"})

    placed = place(LEAF, LIMITS, hist)

    assert placed.slots[0].model == "haiku"
    assert reason(placed, "history.model").facts == {
        "model": "haiku", "samples": 20, "unmet": 3, "met_pct": 85, "best_pct": 90, "model_tolerance": 0.05,
        "tolerance_pct": 5, "calls": 0, "reworks": 0, "configured": "opus", "configured_samples": 20,
        "configured_unmet": 2, "configured_pct": 90, "min_samples": 20}


@pytest.mark.parametrize("roster", [{"research": "haiku"}, {"research": "gpt-x"}, {}])
def test_a_role_with_no_cheaper_model_on_the_ladder_keeps_its_own(roster):
    hist = history(stat("haiku", checked=(50, 0)), stat("sonnet", checked=(50, 0)), draw=0.0, roster=roster)

    assert place(LEAF, LIMITS, hist).slots[0].model is None


def test_an_empty_ladder_turns_model_choice_off():
    hist = history(stat(checked=(20, 2)), stat("haiku", checked=(20, 0)), draw=0.0)

    assert place(LEAF, replace(LIMITS, model_ladder=()), hist) == place(LEAF, LIMITS)


# ------------------------------------------------------------------ exploring the cheaper model

READY = stat(checked=(20, 0))   # sonnet, the roster's model for research, has its samples


@pytest.mark.parametrize(("stats", "draw", "limits", "explored"), [
    ((READY, stat("haiku", checked=(5, 0))), 0.05, LIMITS, True),
    ((READY,), 0.0, LIMITS, True),
    ((READY, stat("haiku", checked=(5, 0))), 0.1, LIMITS, False),        # the draw must be under the rate
    ((stat(checked=(19, 0)),), 0.0, LIMITS, False),                      # nothing to compare with yet: inert
    ((READY, stat("haiku", checked=(20, 10))), 0.0, LIMITS, False),      # haiku has its samples and lost
    ((READY,), 0.0, replace(LIMITS, explore_rate=0.0), False),
])
def test_the_cheaper_model_is_explored_only_on_a_drawn_share_while_it_lacks_samples(stats, draw, limits, explored):
    placed = place(LEAF, limits, history(*stats, draw=draw))

    assert (placed.slots[0].model == "haiku") == explored
    assert ("history.explore" in rules(placed)) == explored


def test_the_explored_share_is_the_explore_rate():
    draws = [i / 1000 for i in range(1000)]

    explored = sum(place(LEAF, LIMITS, history(READY, draw=d)).slots[0].model == "haiku" for d in draws)

    assert explored == 100


def test_an_explore_records_the_draw_and_what_the_cheaper_model_still_lacks():
    placed = place(HARD, LIMITS, history(READY, stat("haiku", checked=(5, 1)), draw=0.03))

    assert rules(placed) == ["check.criteria", "history.explore"], "the check stays; only the model moves"
    assert reason(placed, "history.explore").facts == {
        "model": "haiku", "samples": 5, "draw": 0.03, "explore_rate": 0.1, "explore_pct": 10, "configured": "sonnet",
        "configured_samples": 20, "configured_unmet": 0, "configured_pct": 100, "min_samples": 20}


# ------------------------------------------------------------------ the sentences

PRODUCES = {
    "history.solo": (HARD, history(stat(alone=(20, 1)))),
    "history.checked": (LEAF, history(stat(alone=(20, 5)))),
    "history.model": (LEAF, history(stat(checked=(20, 2)), stat("haiku", checked=(20, 2)))),
    "history.explore": (LEAF, history(READY, draw=0.0)),
}


def test_every_history_reason_has_a_case_that_produces_it():
    assert set(PRODUCES) == set(HISTORY_REASONS)


@pytest.mark.parametrize("rule_id", HISTORY_REASONS)
def test_every_history_decision_records_its_statistics_and_says_them_in_both_languages(rule_id):
    facts, hist = PRODUCES[rule_id]
    cited = reason(place(facts, LIMITS, hist), rule_id).facts
    key = f"staffing.why.{rule_id}"

    assert {"samples", "min_samples"} <= set(cited), "the decision names the samples it read and the bar"
    assert set(catalog()[key]) == set(LANGS)
    assert placeholders(catalog()[key]) <= set(cited), "the sentence names a statistic the decision does not record"
    for lang in LANGS:
        said = msg(key, lang, **cited)
        assert said and "{" not in said
        assert str(cited["samples"]) in said
