"""The people list, its cards and its refusals read as English for an English reader and as natural Korean for a
Korean one."""
import re

import pytest
from justshipit.i18n import catalog, msg
from justshipit.services import owner_tasks, people, people_tasks
from people_kit import add, make_business
from test_korean_style import smells_in

HANGUL = re.compile("[가-힣]")


def refusals(s, lang):
    """What the owner reads when the list refuses something, one of each kind."""
    b = make_business(s)
    p = add(s, b, stage="talked")
    out = []
    for act in (lambda: people.move(s, p.id, "found", lang=lang),
                lambda: people.move(s, p.id, "vip", lang=lang),
                lambda: people.add_quote(s, p.id, "call me on 010-1234-5678", lang=lang),
                lambda: people.create_by_hand(s, b.id, platform="github", handle="alice", display_name=None, url=None,
                                              quote=None, why=None, lang=lang),
                lambda: people.get(s, 10 ** 9, lang=lang),
                lambda: people_tasks.create_task(s, p.id, "referral", lang=lang)):
        with pytest.raises(owner_tasks.OwnerTaskError) as refused:
            act()
        out.append(str(refused.value))
    return out, b, p


def test_an_english_reader_gets_english_labels_cards_and_refusals(s):
    said, b, p = refusals(s, "en")
    row = people.listing(s, b.id, lang="en")
    card = people_tasks.create_task(s, p.id, "follow_up", lang="en").card
    texts = [*said, row["items"][0]["stage_ko"], row["items"][0]["source_ko"], *row["places"]["searched"],
             *row["places"]["by_hand"], card["person"]["stage_ko"], *card["questions"], *card["talking_points"],
             *card["checklist"]]
    assert [t for t in texts if HANGUL.search(t)] == []
    assert row["items"][0]["stage_ko"] == "Talked" and said[0] == "Can't move someone from Talked to Found."


def test_a_korean_reader_gets_natural_korean_in_the_polite_form(s):
    said, _, _ = refusals(s, "ko")
    assert [t for t in said if smells_in(t)] == []
    assert all(re.search(r"요(?:\([^)]*\))?\.?$", t) for t in said), said
    assert said[0] == "'대화함'에서 '찾음' 단계로는 옮길 수 없어요."


def test_the_stage_words_are_the_glossarys():
    assert [msg(f"people.stage.{st}", "en") for st in people.STAGES] == [
        "Found", "Contacted", "Talked", "Got value", "Came back", "Dropped"]
    assert [msg(f"people.stage.{st}", "ko") for st in people.STAGES] == ["찾음", "연락함", "대화함", "도움 됨", "재사용", "그만둠"]


def test_no_english_people_message_holds_hangul():
    english = {k: e["en"] for k, e in catalog().items() if k.startswith("people.")}
    assert {k: v for k, v in english.items() if HANGUL.search(str(v))} == {}
