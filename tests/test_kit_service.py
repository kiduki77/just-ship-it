"""The First 10 kit's rules: who gets a message draft, what a text may hold, what makes a text stale, the guess mark, the
owner's place, and what forgetting a person takes. A text is written by `kit.run` with the marketing agent faked."""
import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from justshipit.db.models import KitText
from justshipit.i18n import LANGS, msg
from justshipit.services import kit, people, tracking
from bet_loop_kit import reset_world
from kit_world import GOOD, LINK, Marketing, business, good, live, person
from profile_kit import describe
from sqlalchemy import select

NOW = datetime.now(UTC)


def no_model(*a, **kw):
    raise AssertionError("a model was called")


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    reset_world()
    monkeypatch.setattr(kit, "run_agent", no_model)
    yield
    reset_world()


@pytest.fixture
def marketing(monkeypatch):
    m = Marketing()
    monkeypatch.setattr(kit, "run_agent", m)
    return m


@pytest.fixture
def b(s):
    return business(s)


def write(b, *, parts=("message", "profile", "invite"), people_ids=(), lang="en"):
    return kit.run(b.id, list(parts), list(people_ids), lang=lang)


def read(s, b, lang="en"):
    s.expire_all()
    return kit.read(s, b.id, lang=lang, now=NOW)


# ------------------------------------------------------------------ who gets a message draft

@pytest.mark.parametrize(("source", "stage", "draft"), [
    ("warm", "found", True), ("inbound", "found", True),
    ("warm", "contacted", True), ("inbound", "talked", True),
    ("public_problem", "found", False), ("similar_tool", "found", False), ("participation", "found", False),
    ("public_problem", "contacted", True), ("similar_tool", "talked", True), ("participation", "first_value", True),
])
def test_a_person_gets_a_draft_when_the_owner_knows_them_or_has_already_contacted_them(source, stage, draft):
    assert kit.wants_draft(source, stage) is draft


def test_todays_people_show_a_draft_or_the_reason_there_is_none(s, b, marketing):
    known = person(s, b, "kim", source="warm")
    stranger = person(s, b, "lee", source="public_problem")
    write(b, people_ids=[known.id])

    today = {t["person"]["id"]: t for t in read(s, b)["today"]}

    assert today[known.id]["draft"]["body"] == good("message", "p") and today[known.id]["no_draft_reason"] is None
    assert today[stranger.id]["draft"] is None
    assert today[stranger.id]["no_draft_reason"] == "public_first"
    assert "your own words" in today[stranger.id]["no_draft_text"]
    assert today[stranger.id]["person"]["where_found_url"], "the post to answer is on the row"


def test_a_person_who_gets_a_draft_and_has_none_yet_says_so(s, b):
    person(s, b, "kim", source="warm")

    (row,) = read(s, b)["today"]

    assert (row["draft"], row["no_draft_reason"], row["no_draft_text"]) == (None, "not_written", "No draft yet.")


def test_nobody_is_written_to_who_is_not_to_get_a_draft(s, b, marketing):
    stranger = person(s, b, "lee", source="public_problem")

    report = write(b, parts=("message",), people_ids=[stranger.id])

    assert report.written == [] and marketing.calls == 0 and live(s, b, "message") == {}


# ------------------------------------------------------------------ what a text may hold

def judged(s, b, part, slot, body, *, p=None, link=LINK):
    """The codes that refuse `body` as the text of this line, for a message to `p` when it is one."""
    facts = kit.Facts(b.id, b.name, "x", "", "", "", (), False, link, (kit._person_facts(p, "en"),) if p else (),
                      (), "en")
    want = kit.Want(part, slot, p.id if p else None)
    return [x.code for x in kit.judge(s, facts, want, body)]


@pytest.fixture
def kim(s, b):
    return person(s, b, "kim", source="warm")


def test_a_good_text_of_every_line_passes(s, b, kim):
    for part, slot in GOOD:
        assert judged(s, b, part, slot, good(part, slot), p=kim if part == "message" else None) == [], (part, slot)


@pytest.mark.parametrize("body", [
    "Mail me at kim.park@example.com and I will answer.",
    "Call me on 010-1234-5678 any time.",
    "My number is +1 415 555 0132.",
    "Reach me on telegram id: kimpark99.",
    "write to kim dot park at example dot com",
])
def test_a_text_that_holds_contact_details_is_refused_in_every_part(s, b, kim, body):
    assert "contact" in judged(s, b, "message", "p", body, p=kim)
    assert "contact" in judged(s, b, "profile", "short", body)
    assert "contact" in judged(s, b, "invite", "known", f"{body} {LINK}")


def test_the_owners_own_link_is_not_mistaken_for_a_phone_number(s, b):
    link = "https://logtool.example.com/join/010-1234-5678"
    assert judged(s, b, "invite", "known", f"Try it: {link}", link=link) == []


def test_a_link_other_than_the_owners_place_is_refused_and_so_is_any_link_where_none_is_allowed(s, b, kim):
    assert judged(s, b, "invite", "known", "Try it at https://elsewhere.example.org/x") == ["foreign_link"]
    assert judged(s, b, "invite", "known", f"Try {LINK} or https://elsewhere.example.org") == ["foreign_link"]
    assert "foreign_link" in judged(s, b, "profile", "line", f"A tool. {LINK}"), "the line carries no link"
    assert "foreign_link" in judged(s, b, "message", "p", f"{good('message', 'p')} {LINK}", p=kim), "not before they talked"
    assert "foreign_link" in judged(s, b, "invite", "known", f"Try it at {LINK}", link=None)
    assert "foreign_link" in judged(s, b, "invite", "known", "Try it at logtool.example.org")


def test_the_owners_place_is_required_in_an_invite_and_the_about_text_and_allowed_in_a_message_after_they_talked(s, b):
    assert judged(s, b, "invite", "known", "I built a tool. Would you try it?") == ["no_link"]
    assert judged(s, b, "profile", "about", "A tool for small teams.") == ["no_link"]
    assert judged(s, b, "profile", "about", "A tool for small teams.", link=None) == []
    talked = person(s, b, "lee", source="warm", stage="talked")
    assert judged(s, b, "message", "p", f"Thanks for the talk. The tool is here: {LINK}", p=talked) == []
    assert judged(s, b, "message", "p", "Thanks for the talk. Any thoughts so far?", p=talked) == []


def test_a_text_over_its_limit_is_refused(s, b, kim):
    assert judged(s, b, "profile", "line", "x" * 81) == ["too_long"]
    assert judged(s, b, "profile", "line", "x" * 80) == []
    assert judged(s, b, "message", "p", "y" * 601, p=kim) == ["too_long"]
    assert judged(s, b, "invite", "problem", "z" * 480 + f" {LINK}") == ["too_long"]


def test_a_message_for_several_people_or_naming_another_is_refused(s, b, kim):
    other = person(s, b, "lena", source="warm")
    assert judged(s, b, "message", "p", "Hi everyone, I am building a tool for server logs.", p=kim) == ["several_people"]
    assert judged(s, b, "message", "p", "Hi Kim and Lena, could we talk about server logs?", p=kim) == ["several_people"]
    assert judged(s, b, "message", "p", "Hi Lena, could we talk about server logs?", p=kim) == ["several_people"]
    assert judged(s, b, "message", "p", "Hi Kim, could we talk about server logs?", p=kim) == []
    assert judged(s, b, "message", "p", "여러분 안녕하세요, 서버 로그 이야기 좀 나눠요.", p=kim) == ["several_people"]
    assert other.id != kim.id


def test_a_message_that_claims_an_earlier_talk_is_refused_until_they_have_talked(s, b, kim):
    body = "As we discussed last week, here is the tool."
    assert "past_talk" in judged(s, b, "message", "p", body, p=kim)
    talked = person(s, b, "lee", source="warm", stage="talked")
    assert "past_talk" not in judged(s, b, "message", "p", body, p=talked)


def test_a_message_may_quote_only_what_the_person_wrote(s, b, kim):
    assert judged(s, b, "message", "p", 'You wrote "I lose hours grepping through 2GB of server logs". Could we talk?',
                  p=kim) == []
    assert judged(s, b, "message", "p", 'You said "I hate every log tool". Could we talk?', p=kim) == ["unsaid_quote"]


def test_the_cards_checks_apply_blanks_voice_votes_invented_experience_and_internal_notes(s, b, kim):
    assert "blanks" in judged(s, b, "message", "p", "Hi, it is [Your name]. Could we talk?", p=kim)
    assert "mixed_voice" in judged(s, b, "profile", "short", "I build it. I test it. We ship it. We run it.")
    assert "asks_votes" in judged(s, b, "invite", "known", f"Try it and please star the repo. {LINK}")
    assert "invented_experience" in judged(s, b, "profile", "short", "I tried three log tools and quit them all.")
    assert "internal_note" in judged(s, b, "invite", "known", f"Per EV-12 you may like it. {LINK}")
    assert "internal_note" in judged(s, b, "message", "p", "Hi, this is about T-41. Could we talk?", p=kim)


def test_an_empty_text_is_refused():
    assert [x.code for x in kit.judge(None, None, kit.Want("profile", "line"), "")] == ["empty"]


def test_every_refusal_has_the_words_in_both_languages():
    for code in kit.REFUSALS:
        for lang in LANGS:
            assert msg(f"kit.refused.{code}", lang)


# ------------------------------------------------------------------ what a written kit says

def test_a_written_kit_reads_back_with_counts_limits_and_what_each_text_was_built_from(s, b, marketing):
    kim = person(s, b, "kim", source="warm")

    write(b, people_ids=[kim.id])
    out = read(s, b)

    draft = out["today"][0]["draft"]
    assert draft["chars"] == len(draft["body"]) and draft["limit"] == 600 and draft["lang"] == "en"
    assert draft["basis_view"]["quotes"] == ["I lose hours grepping through 2GB of server logs"]
    assert (out["profile"]["line"]["limit"], out["profile"]["short"]["limit"], out["profile"]["about"]["limit"]) == (80, 160, 400)
    assert out["invite"]["known"]["limit"] == out["invite"]["problem"]["limit"] == 500
    assert out["link"] == {"url": LINK, "state": "ready"} and out["prepared"] and not out["writing"] and out["can_write"]
    assert LINK in out["invite"]["known"]["body"] and out["profile"]["about"]["basis_view"]["link"] == LINK
    assert all(t["stale"] is False for t in (draft, *out["profile"].values(), *out["invite"].values()))


def test_the_message_is_written_in_the_language_of_the_persons_words(s, b, marketing):
    ko = person(s, b, "min", source="warm", quote="서버 로그를 뒤지느라 몇 시간을 써요")
    en = person(s, b, "lee", source="warm")

    write(b, parts=("message",), people_ids=[ko.id, en.id], lang="en")

    rows = live(s, b, "message")
    assert (rows[("message", "p", ko.id)].lang, rows[("message", "p", en.id)].lang) == ("ko", "en")
    assert "Written in Korean" in next(x for x in marketing.prompts[0].split("### ") if x.startswith(f"message p PPL-{ko.id}"))


def test_the_milestone_is_the_numbers_tabs_count(s, b):
    out = read(s, b)

    assert out["milestone"] == {"n": 10, "have": 0, "estimated": 0}
    people.move(s, person(s, b, "kim", source="warm").id, "first_value", lang="en")
    s.commit()
    assert read(s, b)["milestone"]["have"] == 1


# ------------------------------------------------------------------ stale

def stale(out):
    return {"message": out["today"][0]["draft"]["stale"] if out["today"] else None,
            "line": out["profile"]["line"]["stale"], "about": out["profile"]["about"]["stale"],
            "known": out["invite"]["known"]["stale"]}


def test_a_message_is_stale_when_the_persons_stage_words_or_next_step_change(s, b, marketing):
    kim = person(s, b, "kim", source="warm")
    write(b, people_ids=[kim.id])
    assert stale(read(s, b))["message"] is False

    people.add_quote(s, kim.id, "I now also grep CI logs", lang="en")
    s.commit()
    assert stale(read(s, b))["message"] is True

    write(b, parts=("message",), people_ids=[kim.id])
    assert stale(read(s, b))["message"] is False
    people.set_next(s, kim.id, next_action="Ask about CI", next_action_at=NOW - timedelta(days=1), lang="en")
    s.commit()
    assert stale(read(s, b))["message"] is True

    write(b, parts=("message",), people_ids=[kim.id])
    people.move(s, kim.id, "contacted", lang="en")
    s.commit()
    out = read(s, b)
    assert out["today"][0]["draft"]["stale"] is True and out["today"][0]["draft"]["body"], "it stays readable"


def test_profile_and_invite_are_stale_when_the_customer_description_changes(s, b, marketing):
    write(b, parts=("profile", "invite"))
    assert [stale(read(s, b))[k] for k in ("line", "about", "known")] == [False, False, False]

    describe(s, b.id, buyer=("consumers",))

    assert [stale(read(s, b))[k] for k in ("line", "about", "known")] == [True, True, True]


def test_the_texts_that_carry_the_place_are_stale_when_the_place_changes_and_the_others_are_not(s, b, marketing):
    write(b, parts=("profile", "invite"))

    tracking.set_doors(s, b.id, [{"kind": "site", "url": "https://new.example.com/", "param": "ref"}])
    s.commit()

    assert [stale(read(s, b))[k] for k in ("line", "about", "known")] == [False, True, True]


# ------------------------------------------------------------------ the guess mark and the place

def test_a_text_written_from_a_guessed_description_is_marked_and_one_from_a_confirmed_one_is_not(s, monkeypatch):
    monkeypatch.setattr(kit, "run_agent", Marketing())
    guessed = business(s, buyer=(), reach=())
    describe(s, guessed.id, ("developers",), ("communities",), provenance="guess")
    sure = business(s, name="확실한 도구")

    write(guessed, parts=("profile",))
    write(sure, parts=("profile",))

    assert read(s, guessed)["profile"]["line"]["guess"] is True
    assert read(s, sure)["profile"]["line"]["guess"] is False


def test_a_business_with_no_place_gets_texts_with_no_link_and_the_kit_says_to_add_one(s, monkeypatch):
    m = Marketing()
    monkeypatch.setattr(kit, "run_agent", m)
    bare = business(s, place=False, name="자리 없는 도구")

    write(bare, parts=("profile", "invite"))
    out = read(s, bare)

    assert out["link"] == {"url": None, "state": "no_place"}
    assert "The only link you may write" not in m.prompts[0] and "Write no link" in m.prompts[0]
    for text in (out["profile"]["about"], out["invite"]["known"], out["invite"]["problem"]):
        assert text["body"] and "http" not in text["body"] and text["basis_view"]["link"] is None


def test_a_text_with_a_link_the_owner_does_not_have_is_stored_refused_not_kept(s, monkeypatch):
    monkeypatch.setattr(kit, "run_agent", Marketing({"invite known": "Try it: https://elsewhere.example.org"}))
    bare = business(s, place=False, name="자리 없는 도구")

    report = write(bare, parts=("invite",))

    row = live(s, bare, "invite")[("invite", "known", None)]
    assert row.body is None and row.refused == "foreign_link" and kit.Want("invite", "known") in report.refused


def test_the_owner_who_said_nothing_about_themselves_is_told_so(s, b):
    assert read(s, b)["owner_said"] is False


# ------------------------------------------------------------------ forgetting a person

def test_forgetting_a_person_takes_the_words_of_every_text_written_for_them(s, b, marketing):
    kim = person(s, b, "kim", source="warm")
    lena = person(s, b, "lena", source="warm")
    write(b, parts=("message",), people_ids=[kim.id, lena.id])
    write(b, parts=("message",), people_ids=[kim.id])
    s.expire_all()
    assert s.scalar(select(KitText).where(KitText.person_id == kim.id, KitText.replaced_at.is_not(None)))

    people.archive(s, kim.id, lang="en")
    s.commit()

    s.expire_all()
    rows = list(s.scalars(select(KitText).where(KitText.person_id == kim.id)))
    assert len(rows) == 2 and all(r.body is None and r.refused == "forgotten" and r.basis["quotes"] == [] for r in rows)
    kept = s.scalar(select(KitText).where(KitText.person_id == lena.id))
    assert kept.body and kept.basis["quotes"], "someone else's texts are left alone"


def test_a_person_forgotten_while_the_model_wrote_gets_nothing_stored(s, b, monkeypatch):
    kim = person(s, b, "kim", source="warm")

    def forget_midway(*a, **kw):
        people.archive(s, kim.id, lang="en")
        s.commit()
        return Marketing()(*a, **kw)
    monkeypatch.setattr(kit, "run_agent", forget_midway)

    write(b, parts=("message",), people_ids=[kim.id])

    assert live(s, b, "message") == {}


# ------------------------------------------------------------------ nothing is sent

SENDING = {"sending", "requests", "secrets", "credentials", "netguard", "metrics", "notify_state", "people_link",
           "gateway", "notify", "runtime"}


def test_the_kit_module_imports_no_connector_outbox_or_request_module():
    tree = ast.parse((Path(kit.__file__)).read_text(encoding="utf-8"))
    imported = {part for n in ast.walk(tree) if isinstance(n, ast.ImportFrom | ast.Import)
                for part in (n.module.split(".") if isinstance(n, ast.ImportFrom) and n.module else
                             [a.name.split(".")[-1] for a in n.names] if isinstance(n, ast.Import) else [])}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert imported & SENDING == set(), imported & SENDING
    assert "operations" not in imported
