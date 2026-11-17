"""The pure rules over a customer description: how rows become lines, what a line shows, and the one match rule."""
from datetime import UTC, datetime

import pytest
from justshipit.services.profile import core
from justshipit.services.profile.core import (
    Fit,
    Miss,
    Unsure,
    Value,
    assemble,
    firm,
    fit,
    launch_ready,
    stale,
)

_ids = iter(range(1, 10_000))


def v(line, code=None, provenance="guess", *, sentence=None, brief_id=None, by="owner"):
    if provenance == "unknown":
        code = sentence = None
    return Value(next(_ids), line, code, sentence, provenance, "console", None, brief_id, by,
                 datetime(2026, 11, 13, tzinfo=UTC))


def profile(*rows):
    return assemble(1, rows)


def test_a_line_with_no_rows_was_never_asked_and_reads_as_unknown():
    line = profile().lines["buyer"]
    assert (line.asked, line.known, line.weakest, line.codes, line.sentence) == (False, False, "unknown", (), None)


def test_the_unknown_marker_is_asked_but_not_known():
    line = profile(v("buyer", provenance="unknown")).lines["buyer"]
    assert (line.asked, line.known, line.weakest) == (True, False, "unknown")


def test_a_line_shows_its_weakest_provenance():
    rows = [v("buyer", "consumers", "confirmed"), v("buyer", "developers", "said")]
    assert profile(*rows).lines["buyer"].weakest == "said"
    assert profile(*rows, v("buyer", "companies", "guess")).lines["buyer"].weakest == "guess"
    assert profile(v("buyer", "consumers", "confirmed")).lines["buyer"].weakest == "confirmed"


def test_a_line_reads_its_codes_and_its_sentence():
    line = profile(v("reach", "local"), v("reach", sentence="after a call", provenance="said")).lines["reach"]
    assert line.codes == ("local",) and line.sentence == "after a call"


def test_has_is_true_for_a_guess_and_false_for_unknown_or_another_line():
    prof = profile(v("reach", "local", "guess"), v("buyer", provenance="unknown"))
    assert prof.has("reach", "local")
    assert not prof.has("buyer", "local") and not prof.has("reach", "search")


def test_guessed_is_true_while_any_known_value_is_a_guess():
    prof = profile(v("buyer", "consumers", "confirmed"), v("buyer", "developers", "guess"),
                   v("reach", "local", "said"), v("has", provenance="unknown"))
    assert prof.guessed("buyer") and not prof.guessed("reach") and not prof.guessed("has")


def test_an_empty_fits_fits_anything_with_nothing_to_show():
    assert fit({}, profile()) == Fit({}, ())


def test_a_declared_line_that_shares_a_code_fits_and_names_the_codes():
    prof = profile(v("buyer", "developers", "confirmed"), v("buyer", "companies", "confirmed"),
                   v("reach", "communities", "said"))
    got = fit({"buyer": ["developers", "consumers"], "reach": ["communities"]}, prof)
    assert got == Fit({"buyer": ("developers",), "reach": ("communities",)}, ())


def test_a_line_left_out_means_any_even_when_it_is_unknown():
    assert isinstance(fit({"reach": ["local"]}, profile(v("reach", "local"))), Fit)


def test_fit_names_the_declared_lines_whose_matching_values_are_all_guesses():
    prof = profile(v("buyer", "developers", "guess"), v("reach", "communities", "confirmed"),
                   v("reach", "search", "guess"))
    got = fit({"buyer": ["developers"], "reach": ["communities", "search"]}, prof)
    assert got.guessed == ("buyer",)


def test_a_guess_beside_a_firm_value_that_does_not_match_still_marks_the_line():
    prof = profile(v("buyer", "developers", "guess"), v("buyer", "consumers", "confirmed"))
    assert fit({"buyer": ["developers"]}, prof).guessed == ("buyer",)
    assert fit({"buyer": ["consumers"]}, prof).guessed == ()


def test_a_known_line_that_shares_nothing_is_a_miss():
    assert fit({"buyer": ["developers"]}, profile(v("buyer", "consumers"))) == Miss()


def test_a_known_line_with_only_a_sentence_shares_no_code():
    assert fit({"buyer": ["developers"]}, profile(v("buyer", sentence="my neighbours", provenance="said"))) == Miss()


def test_an_unknown_declared_line_makes_it_unsure_and_names_the_line():
    prof = profile(v("buyer", "developers"), v("reach", provenance="unknown"))
    assert fit({"buyer": ["developers"], "reach": ["local"]}, prof) == Unsure(("reach",))
    assert fit({"buyer": ["developers"], "has": ["time"]}, prof) == Unsure(("has",))


def test_a_miss_wins_over_an_unknown_line():
    assert fit({"buyer": ["developers"], "reach": ["local"]}, profile(v("buyer", "consumers"))) == Miss()


def test_an_unknown_marker_matches_nothing_even_beside_known_values():
    prof = profile(v("reach", provenance="unknown"))
    assert fit({"reach": ["local"]}, prof) == Unsure(("reach",))


def test_firm_needs_every_matching_value_said_or_confirmed():
    declared = {"buyer": ["developers"], "reach": ["communities"]}
    assert firm(declared, profile(v("buyer", "developers", "said"), v("reach", "communities", "confirmed")))
    assert not firm(declared, profile(v("buyer", "developers", "said"), v("reach", "communities", "guess")))
    assert not firm(declared, profile(v("buyer", "developers", "said")))
    assert not firm(declared, profile(v("buyer", "developers", "said"), v("reach", provenance="unknown")))


def test_firm_ignores_values_that_do_not_match_and_holds_for_no_declared_lines():
    prof = profile(v("buyer", "developers", "confirmed"), v("buyer", "consumers", "guess"))
    assert firm({"buyer": ["developers"]}, prof)
    assert firm({}, profile())


HUNT = {"reach": ["communities"]}


def test_a_launch_waits_for_a_said_or_confirmed_buyer_even_when_it_declares_only_reach():
    reach = v("reach", "communities", "confirmed")
    assert launch_ready(HUNT, profile(v("buyer", "consumers", "said"), reach))
    assert launch_ready(HUNT, profile(v("buyer", "developers", "confirmed"), reach))
    assert not launch_ready(HUNT, profile(v("buyer", "consumers", "guess"), reach))
    assert not launch_ready(HUNT, profile(reach))
    assert not launch_ready(HUNT, profile(v("buyer", provenance="unknown"), reach))


def test_a_launch_needs_every_known_buyer_value_firm_not_only_the_matching_one():
    reach = v("reach", "communities", "confirmed")
    assert not launch_ready(HUNT, profile(v("buyer", "developers", "confirmed"), v("buyer", "consumers", "guess"), reach))
    assert launch_ready(HUNT, profile(v("buyer", "developers", "confirmed"), v("buyer", "consumers", "said"), reach))


def test_a_launch_also_needs_what_it_declares_firm():
    buyer = v("buyer", "developers", "confirmed")
    declared = {"buyer": ["developers"], "reach": ["communities"]}
    assert launch_ready(declared, profile(buyer, v("reach", "communities", "said")))
    assert not launch_ready(declared, profile(buyer, v("reach", "communities", "guess")))
    assert not launch_ready(declared, profile(buyer, v("reach", "local", "confirmed")))
    assert not launch_ready(declared, profile(buyer))


def test_a_said_value_the_project_manager_read_from_a_quote_does_not_open_a_launch():
    reach = v("reach", "communities", "confirmed")
    assert not launch_ready(HUNT, profile(v("buyer", "developers", "said", by="project_manager"), reach))
    assert not launch_ready(HUNT, profile(v("buyer", "developers", "confirmed"), v("reach", "communities", "said", by="project_manager")))


def test_a_launch_that_declares_nothing_still_needs_a_firm_buyer():
    assert launch_ready({}, profile(v("buyer", "consumers", "confirmed")))
    assert not launch_ready({}, profile(v("buyer", "consumers", "guess")))
    assert not launch_ready({}, profile())


PLANS = {1: ("customer_problem:a",), 2: ("customer_problem:b",), 3: ("customer_problem:a",)}


def test_a_line_written_against_a_plan_that_has_since_changed_is_stale():
    prof = profile(v("buyer", "developers", brief_id=1), v("reach", "local", brief_id=2), v("has", "time"))
    assert stale(prof, PLANS, 2) == {"buyer"}


def test_a_plan_with_the_same_watched_lines_does_not_make_a_line_stale():
    assert stale(profile(v("buyer", "developers", brief_id=1)), PLANS, 3) == frozenset()


def test_a_row_with_no_plan_is_never_stale_and_nothing_is_stale_without_a_current_plan():
    assert stale(profile(v("buyer", "developers", "guess")), PLANS, 2) == frozenset()
    assert stale(profile(v("buyer", "developers", brief_id=1)), PLANS, None) == frozenset()


def test_the_watched_sections_are_all_sections_of_the_plan():
    from justshipit.services import roadmap
    assert set(core.WATCHED_SECTIONS) <= {spec.key for spec in roadmap.BRIEF_SECTIONS}


@pytest.mark.parametrize("line", ["buyer", "reach", "has"])
def test_assemble_gives_every_line_a_view(line):
    assert profile().lines[line].line == line
