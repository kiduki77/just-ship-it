"""The customer description's vocabulary: every code, line and cause has words in both languages, and the lines and
provenances are the ones the tables and the plan already use. A buyer or reach code that no channel or place is tagged
with would steer nothing, so it fails here too."""
from typing import get_args

import pytest
from justshipit.db.models import PROFILE_LINES, PROFILE_PROVENANCES
from justshipit.i18n import LANGS, catalog, msg
from justshipit.services import channels, people, roadmap
from justshipit.services.profile import store, vocab

ALL_CODES = [(line, code) for line, codes in vocab.CODES.items() for code in codes]


def test_the_vocabulary_is_the_nineteen_codes_of_the_design():
    assert {line: len(codes) for line, codes in vocab.CODES.items()} == {"buyer": 5, "reach": 6, "has": 8}
    assert all(len(set(codes)) == len(codes) for codes in vocab.CODES.values())


@pytest.mark.parametrize(("line", "code"), ALL_CODES)
def test_every_code_has_a_label_in_both_languages(line, code):
    entry = catalog()[f"profile.code.{line}.{code}"]
    assert all(entry[lang].strip() for lang in LANGS)


def test_the_catalog_has_no_code_label_the_vocabulary_lacks():
    labelled = {k.removeprefix("profile.code.") for k in catalog() if k.startswith("profile.code.")}
    assert labelled == {f"{line}.{code}" for line, code in ALL_CODES}


@pytest.mark.parametrize("line", vocab.LINES)
def test_every_line_has_a_label_and_a_sentence_that_takes_a_list(line):
    for lang in LANGS:
        assert msg(f"profile.line.{line}", lang).strip()
        assert "X" in msg(f"profile.compose.{line}", lang, list="X")


@pytest.mark.parametrize("cause", store.CAUSES)
def test_every_cause_has_activity_words(cause):
    for lang in LANGS:
        assert msg(f"profile.activity.{cause}", lang, line=msg("profile.line.buyer", lang)).strip()


def test_the_lines_and_provenances_are_the_ones_the_table_and_the_plan_use():
    assert vocab.LINES == PROFILE_LINES
    assert set(get_args(vocab.Provenance)) == set(PROFILE_PROVENANCES)
    assert set(get_args(vocab.Provenance)) <= set(get_args(roadmap.Provenance))


def test_a_retired_code_leaves_the_picker_but_is_still_a_code(monkeypatch):
    monkeypatch.setattr(vocab, "RETIRED", frozenset({"developers"}))
    assert "developers" not in vocab.live_codes("buyer")
    assert vocab.is_code("buyer", "developers")
    assert not vocab.is_code("reach", "developers")


def test_every_buyer_and_reach_code_is_tagged_on_a_channel_or_a_place():
    tagged = {(line, code) for fits in (*(c.fits for c in channels.registry().values()),
                                       *(f for p in people.PLACES.values() for f in (p.fits, *p.fits_in.values())))
              for line, codes in fits.items() for code in codes}
    steering = [(line, code) for line in ("buyer", "reach") for code in vocab.live_codes(line)]
    assert [pair for pair in steering if pair not in tagged] == []
