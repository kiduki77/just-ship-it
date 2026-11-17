"""Fixes after the review of the First 10 kit's text checks: what the kit's own judge must refuse (an address with a rare
ending, a messenger id with no colon, a placeholder, someone else's name, a request to pass the text on) and what it must
no longer refuse (an address next to a Korean particle, the person's own handle, a talk in their own post, the product's
name in quotation marks). Each test failed before the fix."""
import pytest
from justshipit.i18n import LANGS, msg
from justshipit.services import kit
from bet_loop_kit import reset_world
from kit_world import LINK, Marketing, business, live, person


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    reset_world()
    yield
    reset_world()


@pytest.fixture
def b(s):
    return business(s, name="InvoiceMate")


@pytest.fixture
def kim(s, b):
    return person(s, b, "kim", source="warm")


def detail(s, b, part, slot, body, *, p=None, link=LINK):
    facts = kit.Facts(b.id, b.name, "x", "", "", "", (), False, link, (kit._person_facts(p, "en"),) if p else (), (), "en")
    return kit.judge(s, facts, kit.Want(part, slot, p.id if p else None), body)


def judged(s, b, part, slot, body, **kw):
    return [x.code for x in detail(s, b, part, slot, body, **kw)]


# ------------------------------------------------------------------ links and messenger ids

@pytest.mark.parametrize("other", ["bit.ly/3xYz", "discord.gg/abc123", "mybrand.shop/try", "t.me/ownerjk", "BIT.LY/3xYz",
                                   "go.mybrand.studio"])
def test_an_address_of_any_ending_other_than_the_owners_place_is_refused(s, b, other):
    assert "foreign_link" in judged(s, b, "invite", "known", f"Would you try it? {LINK} or {other}")


@pytest.mark.parametrize("own", [LINK, LINK + ".", f"({LINK})", "logtool.example.com/start", f"{LINK}에서 바로 써 볼 수 있어요",
                                 f"링크({LINK})에서 바로", f"{LINK}—it takes a minute", f"{LINK}–it takes a minute"])
def test_the_owners_own_place_passes_whatever_stands_next_to_it(s, b, own):
    assert judged(s, b, "invite", "known", f"Would you try it? {own}") == []


def test_a_sentence_run_into_the_next_is_not_an_address_and_neither_is_a_file_name(s, b, kim):
    assert judged(s, b, "message", "p", "Hi, I read your note about server logs. I am building a tool for exactly "
                                        "that.Then I would like your view. Could we talk for ten minutes?", p=kim) == []
    assert judged(s, b, "message", "p", "Hi, I read your note about server logs. My tool runs on node.js and reads them. "
                                        "Could we talk for ten minutes?", p=kim) == []


@pytest.mark.parametrize("body", [
    "Ping me on Telegram @owner_jk anytime.",
    "Message me on WhatsApp owner_jk99 anytime.",
    "카톡 아이디 ownerjk 로 물어봐 주세요.",
    "Kakao ID is ownerjk anytime.",
    "텔레그램 @owner_jk 로 연락 주세요.",
])
def test_a_messenger_id_is_refused_without_a_colon_between_the_app_and_the_id(s, b, body):
    assert "contact" in judged(s, b, "invite", "known", f"Would you try it? {LINK} {body}")


def test_naming_an_app_is_not_a_messenger_id(s, b, kim):
    for body in ("Hi, I read your note about server logs. Do you use Telegram or Slack for your team? Could we talk?",
                 "Hi, I read your note about server logs. 카톡이나 슬랙으로 연락하시나요? Could we talk?"):
        assert judged(s, b, "message", "p", body, p=kim) == [], body


def test_the_shared_contact_check_is_left_as_it_was(s):
    from justshipit.services import people
    assert people.contact_info_problem("Ping me on Telegram @owner_jk anytime.") is None, "only the kit's judge reads it"


# ------------------------------------------------------------------ the placeholder

def test_the_rewrite_round_never_asks_for_a_placeholder_and_a_placeholder_is_never_stored(s, b, monkeypatch):
    marketing = Marketing({"profile short": ["I tried three log tools and quit them all.",
                                             "A tool for cafes. [My experience: write it yourself]"],
                           "profile about": "A tool for cafes. [내 경험: 직접 쓰기] " + LINK})
    monkeypatch.setattr(kit, "run_agent", marketing)

    report = kit.run(b.id, ["profile"], [], lang="en")

    again = marketing.prompts[1].split("## Write these again")[1]
    assert "remove that sentence and write nothing about the owner's experience" in again
    assert "write it yourself" not in again.lower() and "[" not in again.split("### profile short")[1].split("###")[0]
    rows = live(s, b, "profile")
    assert (rows[("profile", "short", None)].body, rows[("profile", "short", None)].refused) == (None, "blanks")
    assert rows[("profile", "about", None)].refused == "blanks"
    assert {w.key for w in report.refused} == {"profile short", "profile about"}


def test_the_kits_own_reason_for_an_invented_experience_reads_in_both_languages_and_asks_for_no_placeholder(s, b):
    (problem,) = detail(s, b, "profile", "short", "I tried three log tools and quit them all.")
    assert problem.code == "invented_experience" and "remove that sentence" in problem.detail
    assert "[" not in problem.detail and "placeholder" in problem.detail.lower()
    for lang in LANGS:
        assert "{found}" not in msg("kit.reason.invented", lang, found="x") and msg("kit.reason.invented", lang, found="x")


@pytest.mark.parametrize("body", ["A tool. [My experience: write it yourself]", "[내 경험: 직접 쓰기] 도구예요", "See [the docs](x)",
                                  "A tool for [cafes]"])
def test_a_bracket_of_two_characters_or_more_is_refused_in_any_text(s, b, kim, body):
    assert "blanks" in judged(s, b, "profile", "short", body)
    assert "blanks" in judged(s, b, "message", "p", body, p=kim)


def test_a_single_character_in_brackets_is_left_alone(s, b):
    assert judged(s, b, "profile", "short", "A tool for cafes [1] that reads logs.") == []


# ------------------------------------------------------------------ the person's own handle

def test_a_message_that_greets_the_person_by_their_handle_is_not_read_as_an_email_or_an_address(s, b):
    jane = person(s, b, "jane.doe", source="warm")
    for body in ("Hey @jane.doe, I read your note about losing hours in server logs. Could we talk for ten minutes?",
                 "안녕하세요 @jane.doe님, 서버 로그 때문에 고생하신다는 글을 봤어요. 잠깐 이야기 나눌 수 있을까요?",
                 "Hey jane.doe, I read your note about losing hours in server logs. Could we talk for ten minutes?"):
        assert judged(s, b, "message", "p", body, p=jane) == [], body


def test_a_real_email_next_to_the_handle_is_still_refused(s, b):
    jane = person(s, b, "jane.doe", source="warm")
    assert "contact" in judged(s, b, "message", "p", "Hey jane.doe@example.com, could we talk about server logs?", p=jane)
    assert "contact" in judged(s, b, "message", "p", "Hey @jane.doe, write to kim.park@example.com. Could we talk?", p=jane)


# ------------------------------------------------------------------ a talk, the product's name, a group

def test_a_talk_in_the_persons_own_post_is_not_an_earlier_talk_with_the_owner(s, b, kim):
    post = "안녕하세요. 글에서 이야기했던 송장 문제가 궁금해서 연락드려요. 잠깐 시간 괜찮으세요?"
    assert "past_talk" not in judged(s, b, "message", "p", post, p=kim)
    assert "past_talk" in judged(s, b, "message", "p", "안녕하세요. 지난번에 이야기했던 송장 문제 때문에 연락드려요.", p=kim)
    assert "past_talk" in judged(s, b, "message", "p", "안녕하세요. 우리가 글에서 이야기했던 송장 문제 때문에 연락드려요.", p=kim)
    assert "past_talk" in judged(s, b, "message", "p", "Hi, as we discussed, here is the tool.", p=kim)


def test_the_products_name_may_stand_in_quotation_marks_and_another_quotation_may_not(s, b, kim):
    ok = 'Hi, I read your note about server logs. I am building "InvoiceMate" for that. Could we talk?'
    assert judged(s, b, "message", "p", ok, p=kim) == []
    assert judged(s, b, "message", "p", ok.replace('"InvoiceMate"', "‘InvoiceMate’"), p=kim) == []
    assert judged(s, b, "message", "p", ok.replace('"InvoiceMate"', '"InvoiceMagic"'), p=kim) == ["unsaid_quote"]


@pytest.mark.parametrize("quote", ['"I hate every log tool"', "“I hate every log tool”", "‘I hate every log tool’",
                                   "'I hate every log tool'", "「정말 매일 야근해요」", "『정말 매일 야근해요』", "‘정말 매일 야근해요’"])
def test_an_invented_quotation_is_refused_whichever_quotation_marks_it_wears(s, b, kim, quote):
    assert judged(s, b, "message", "p", f"Hi, you said {quote}. Could we talk about server logs?", p=kim) == ["unsaid_quote"]


def test_what_the_person_wrote_may_be_quoted_in_any_marks_and_an_apostrophe_is_no_quotation(s, b, kim):
    said = "I lose hours grepping through 2GB of server logs"
    for q in (f"“{said}”", f"‘{said}’", f"'{said}'"):
        assert judged(s, b, "message", "p", f"Hi, you wrote {q}. Could we talk?", p=kim) == [], q
    assert judged(s, b, "message", "p", "Hi, I don't know how you handle logs today, and it isn't my place to guess. Could we talk?",
                  p=kim) == []


def test_talking_to_friends_of_theirs_is_not_a_message_to_a_group_and_a_greeting_to_one_is(s, b, kim):
    assert "several_people" not in judged(s, b, "message", "p", "Thanks for replying. Would you mind if I talk to friends "
                                                                "of yours who run cafes too?", p=kim)
    assert judged(s, b, "message", "p", "Hi friends, I am building a tool for server logs.", p=kim) == ["several_people"]
    assert judged(s, b, "message", "p", "I would like to talk to everyone about server logs.", p=kim) == ["several_people"]


# ------------------------------------------------------------------ naming someone else

def test_a_message_names_someone_else_by_an_at_handle_a_full_name_or_a_name_that_is_no_ordinary_word(s, b, kim):
    person(s, b, "other_person", source="warm")
    will = person(s, b, "will", source="warm")
    mia = person(s, b, "mia99", source="warm")
    mia.display_name = "Mia Park"
    s.commit()
    refused = [("Could we talk? Tell @other_person about it too.", "'@other_person'"),
               ("Could we talk? Tell @will I said hi.", "'@will'"),
               ("Could we talk? Mia Park told me about you.", "'Mia Park'"),
               ("Could we talk? Mia99 told me about you.", "'Mia99'")]
    for text, word in refused:
        (problem,) = detail(s, b, "message", "p", f"Hi, I read your note about server logs. {text}", p=kim)
        assert problem.code == "several_people" and word in problem.detail, (text, problem.detail)
    for text in ("I will gladly talk about it.", "I want to park the idea for now.", "Mia is a fine name."):
        assert judged(s, b, "message", "p", f"Hi, I read your note about server logs. {text} Could we talk?", p=kim) == [], text
    assert will.id != mia.id


def test_a_korean_full_name_with_a_particle_is_a_name_and_a_greeting_to_a_group_names_the_greeting(s, b, kim):
    lee = person(s, b, "lee", source="warm")
    lee.display_name = "이서연"
    s.commit()
    (problem,) = detail(s, b, "message", "p", "안녕하세요, 이서연님께도 말씀드렸는데 잠깐 이야기 나눌 수 있을까요?", p=kim)
    assert problem.code == "several_people" and "이서연" in problem.detail
    (problem,) = detail(s, b, "message", "p", "Hi everyone, I am building a tool for server logs.", p=kim)
    assert problem.code == "several_people" and "Hi everyone" in problem.detail


# ------------------------------------------------------------------ passing the text on

@pytest.mark.parametrize("ask", ["Share it with your friends.", "Please tell your colleagues about it.", "Pass it on to everyone you know.",
                                 "친구들에게 공유해 주세요.", "주변 분들께 소개해 주세요."])
def test_a_message_that_asks_the_person_to_pass_it_on_to_several_people_is_refused(s, b, kim, ask):
    assert "asks_spread" in judged(s, b, "message", "p", f"Hi, I read your note about server logs. {ask} Could we talk?", p=kim)


def test_an_invite_that_asks_the_reader_to_pass_it_on_to_several_people_is_refused_too(s, b):
    assert "asks_spread" in judged(s, b, "invite", "known", f"Try it and share it with your friends! {LINK}")
    assert "asks_spread" in judged(s, b, "invite", "problem", f"I built a tool for server logs. {LINK} Tell your colleagues.")


def test_the_invites_normal_line_about_knowing_someone_is_left_alone(s, b, kim):
    assert judged(s, b, "invite", "known", f"I built a tool for server logs. Know someone who needs this? {LINK}") == []
    assert "asks_spread" not in judged(s, b, "message", "p", "Hi, I read your note about server logs. Know someone who "
                                                              "needs this? Could we talk?", p=kim)
