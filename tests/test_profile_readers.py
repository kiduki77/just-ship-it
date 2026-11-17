"""Every reader reads the customer description and nothing else: promotion, the people finder and its places, the
numbers page, the contact card and the agents' words. The word-counting classifier they replaced is gone, and these
tests keep what it did for the traced cases (frozen below) so the switch changed no Korean channel."""
import re
from pathlib import Path

import pytest
from justshipit.contracts.post_card import PostCardDraft, PostWhat
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import channels, launch_gate, metrics, owner_tasks, people, people_tasks, profile, promotion
from justshipit.services.profile import audit
from bet_loop_kit import NOW, install_people, make_business, reset_world
from people_kit import add
from people_kit import make_business as make_people_business
from profile_kit import describe, reject
from sqlalchemy import text
from test_profile_fit import OLD_CHANNELS, OLD_PLACES
from test_promotion import ScriptedMarketing, tasks, week

ROOT = Path(__file__).resolve().parents[1]
MATERIAL = [promotion.Material("release:1", "release", "v1", "", None, NOW)]

# The classifier's weekly channels and places per old kind are frozen in test_profile_fit (OLD_CHANNELS, OLD_PLACES). The
# one-time launches among them, which a channel's gate held back and still does:
_NEW_SINCE = {"dev": {"lobsters"}, "local": set(), "general": set()}  # places added after the classifier went
OLD_LAUNCHES = {("ko", "dev"): {"show_hn", "product_hunt", "geeknews"}, ("ko", "local"): set(),
                ("ko", "general"): {"product_hunt"}}
# The three traced Korean businesses: what the owner confirmed for each, and the old kind it stands for.
TRACED = {
    "developer tool": ("dev", ["developers"], ["communities"]),
    "shop": ("local", ["consumers"], ["local"]),
    "consumer app": ("general", ["consumers"], ["communities"]),
}


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    with session_scope() as s:
        s.execute(text("DELETE FROM channel_drops"))
    yield
    reset_world()


@pytest.fixture
def marketing(monkeypatch):
    m = ScriptedMarketing()
    monkeypatch.setattr(promotion, "run_agent", m)
    return m


def meet_every_gate(business):
    install_people(first_value=25)
    for item in launch_gate.OWNER_GATE_ITEMS:
        with session_scope() as x:
            promotion.confirm_gate_item(x, business.id, item, lang="ko")


UNCHECKED = msg("promotion.skip.rules_unchecked", "en")


def unchecked(skipped):
    """The channels that fit the description and were left out only because their rules were not read."""
    return [k for k, v in skipped.items() if v == UNCHECKED]


def considered(s, business, *, cap=99):
    """The channels the week looked at: the ones it picked and the ones it skipped for a reason of their own. A place
    left out because its rules were not read is not one of those: it is in `skipped`, and `unchecked` names it."""
    picked, skipped = promotion.pick(s, business.id, MATERIAL, cap, NOW, lang="en")
    return ([c.channel.key for c in picked],
            {k: v for k, v in skipped.items() if not k.startswith("profile:") and v != UNCHECKED}, skipped)


# ------------------------------------------------------------------ (a) the classifier is gone

DELETED = [r"\baudience\.of_business\b", r"\baudience\.of_text\b", r"\bwords_for\b", r"\bchannels\.Audience\b",
           r"\bAUDIENCES\b", r"\bpeople\.SEARCHED\b", r"\bpeople\.BY_HAND\b", r"\bSEARCHED\[", r"\bBY_HAND\[",
           r"i18n\.match\.audience", r"i18n\.match import[^\n]*\baudience\b",
           r"services\.audience import[^\n]*\b(?:of_business|of_text|words_for)\b",
           r"services\.channels import[^\n]*\bAudience\b", r"\b(?:ch|c|channel|place|registry\[[^\]]+\])\.audience\b",
           r"\bchannels\.get\([^)]*\)\.audience\b", r"^\s*audience:\s*\["]


def test_no_module_uses_a_name_the_classifier_left_behind():
    sources = [*ROOT.joinpath("backend").rglob("*.py"), *ROOT.joinpath("tests").rglob("*.py"),
               *ROOT.joinpath("config").rglob("*.yaml")]
    found = [f"{path.relative_to(ROOT)}: {m.group()}" for path in sources if path != Path(__file__).resolve()
             for m in re.finditer("|".join(DELETED), path.read_text(encoding="utf-8"), re.MULTILINE)]
    assert found == []


@pytest.mark.parametrize("text", ["audience.of_business(s, 1)", "words_for(s, 1)", "channels.Audience",
                                  "people.SEARCHED[market][kind]", "ch.audience", "    audience: [dev]"])
def test_the_scan_finds_each_deleted_name(text):
    assert re.search("|".join(DELETED), text, re.MULTILINE)


def test_the_classifiers_modules_and_tables_no_longer_exist():
    from justshipit.services import audience

    assert not (ROOT / "backend/justshipit/i18n/match/audience.py").exists()
    assert not hasattr(audience, "of_business") and not hasattr(audience, "of_text") and not hasattr(audience, "words_for")
    assert not hasattr(channels, "Audience") and not hasattr(channels, "AUDIENCES")
    assert "audience" not in channels.Channel.model_fields
    assert not hasattr(people, "SEARCHED") and not hasattr(people, "BY_HAND")


# ------------------------------------------------------------------ (b) a guess marks the card

def test_a_card_picked_from_a_guess_says_which_lines_were_guessed_and_the_console_view_notices_a_change(
        s, marketing):
    b = make_business(s, repo="owner/repo", buyer=(), reach=())
    describe(s, b.id, ["developers"], ["communities"], provenance="guess")
    week(b)
    made = tasks(s, b)
    assert made
    for task in made:
        assert task.card["fit"] == {"guessed": ["buyer", "reach"],
                                    "codes": {"buyer": ["developers"], "reach": ["communities"]}}
    view = owner_tasks.view(made[0], "en")["fit_view"]
    assert view == {"guessed": [{"line": "buyer", "line_label": "Who buys", "labels": ["Developers"]},
                                {"line": "reach", "line_label": "How they're reached",
                                 "labels": ["Online communities where they gather"]}], "changed": False}
    assert owner_tasks.view(made[0], "ko")["fit_view"]["guessed"][0]["labels"] == ["개발자"]

    reject(s, b.id, "buyer")
    s.expire_all()
    assert owner_tasks.view(tasks(s, b)[0], "en")["fit_view"]["changed"] is True
    describe(s, b.id, ["developers"], provenance="confirmed")
    again = owner_tasks.view(tasks(s, b)[0], "en")["fit_view"]
    assert again["changed"] is False, "the same value is live again"
    assert [g["line"] for g in again["guessed"]] == ["reach"], "a guess the owner confirmed needs no check"


def test_a_card_picked_from_confirmed_values_carries_the_codes_and_no_guessed_line(s, marketing):
    b = make_business(s, repo="owner/repo")
    week(b)
    card = tasks(s, b)[0].card
    assert card["fit"] == {"guessed": [], "codes": {"buyer": ["developers"], "reach": ["communities"]}}
    assert owner_tasks.view(tasks(s, b)[0], "en")["fit_view"] == {"guessed": [], "changed": False}


def test_a_card_the_company_did_not_pick_has_no_fit_view(s):
    b = make_business(s)
    task = owner_tasks.create_post_task(s, b.id, {"channel": "hacker_news"}, "A post")
    assert owner_tasks.view(task, "en")["fit_view"] is None


def test_the_model_cannot_write_the_fit_of_a_card(s, marketing, monkeypatch):
    """The draft has no `fit` field, and the server writes `card["fit"]` over whatever the built card held."""
    assert "fit" not in PostCardDraft.model_fields and "fit" not in PostWhat.model_fields
    b = make_business(s, repo="owner/repo")
    real = owner_tasks.build_post_card

    def forged(draft, gate, lang):
        card, stripped = real(draft, gate, lang)
        return {**card, "fit": {"guessed": ["buyer"], "codes": {}}}, stripped

    monkeypatch.setattr(owner_tasks, "build_post_card", forged)
    week(b)
    assert all(t.card["fit"]["guessed"] == [] for t in tasks(s, b))


# ------------------------------------------------------------------ (c) a one-time launch needs a firm buyer

@pytest.mark.parametrize("key", ["show_hn", "product_hunt", "geeknews"])
def test_a_one_time_launch_skips_on_a_guessed_buyer_and_is_considered_once_it_is_confirmed(s, key):
    b = make_business(s, buyer=(), reach=())
    meet_every_gate(b)
    describe(s, b.id, ["developers"], ["communities"], provenance="guess")
    picked, own, _ = considered(s, b)
    assert key not in picked and own[key] == msg("promotion.skip.launch_guess", "en")
    assert own[key] == "A one-time launch waits until you confirm who buys and how they're reached"
    assert "hacker_news" in picked, "an ordinary post keeps going on a guess"

    describe(s, b.id, ["developers"], ["communities"], provenance="confirmed")
    picked, own, _ = considered(s, b)
    assert key in picked and key not in own


def test_a_one_time_launch_on_a_guess_gives_its_gate_reason_first_when_the_gate_is_not_met(s):
    b = make_business(s, buyer=(), reach=())
    describe(s, b.id, ["developers"], ["communities"], provenance="guess")
    _, own, _ = considered(s, b)
    assert own["show_hn"].startswith("Still to do first")


def test_a_launch_that_declares_only_how_people_are_reached_still_waits_on_a_guessed_buyer(s):
    b = make_business(s, buyer=(), reach=())
    meet_every_gate(b)
    describe(s, b.id, ["consumers"], ["communities"], provenance="guess")
    picked, own, skipped = considered(s, b)
    assert "product_hunt" not in picked and own["product_hunt"].startswith("A one-time launch waits")
    assert {"x", "disquiet"} <= set(unchecked(skipped))


# ------------------------------------------------------------------ (d) the Korean channels are unchanged

@pytest.mark.parametrize("case", TRACED)
def test_a_korean_business_with_a_confirmed_description_gets_the_channels_its_old_kind_got(s, case):
    kind, buyer, reach = TRACED[case]
    b = make_business(s, name=f"한국 {case}", buyer=buyer, reach=reach)
    old = OLD_CHANNELS["ko", kind]

    picked, own, skipped = considered(s, b)
    assert sorted(set(picked + list(own) + unchecked(skipped)) - _NEW_SINCE[kind]) == sorted(old), "the same channels were looked at"
    assert set(own) == OLD_LAUNCHES["ko", kind], "and the launches were held back by their gate, as before"
    assert all(v.startswith("Still to do first") for v in own.values())
    assert skipped == {**own, **{k: UNCHECKED for k in unchecked(skipped)}}

    meet_every_gate(b)
    picked, own, skipped = considered(s, b)
    assert [k for k in picked if k not in _NEW_SINCE[kind]] == [k for k in old if k in picked] and sorted(set(picked + unchecked(skipped)) - _NEW_SINCE[kind]) == sorted(old) and own == {}


@pytest.mark.parametrize("case", TRACED)
def test_the_korean_places_are_the_ones_the_old_tables_listed(s, case):
    kind, buyer, reach = TRACED[case]
    b = make_business(s, name=f"한국 {case}", buyer=buyer, reach=reach)
    assert people.where(profile.of(s, b.id), "ko") == OLD_PLACES["ko", kind]
    shown = people.listing(s, b.id, lang="ko")["places"]
    searched, by_hand = OLD_PLACES["ko", kind]
    assert shown["searched"] == [people.PLACES[k].names["ko"] for k in searched]
    assert shown["by_hand"] == [people.PLACES[k].names["ko"] for k in by_hand]


@pytest.mark.parametrize("case", TRACED)
def test_the_audit_shows_the_same_korean_picks(s, case):
    kind, buyer, reach = TRACED[case]
    b = make_business(s, name=f"한국 {case}", buyer=buyer, reach=reach)
    row = audit.compare(s, [b.id])[0]
    assert set(row.channels) | set(row.waiting_launches) == set(OLD_CHANNELS["ko", kind]) | _NEW_SINCE[kind]
    assert row.waiting_launches == ()
    assert (row.searched, row.by_hand) == OLD_PLACES["ko", kind]


# ------------------------------------------------------------------ (e) an unknown line is asked for, once

def test_a_declared_line_nobody_knows_gives_one_reason_per_line_and_no_pick(s):
    b = make_business(s, buyer=(), reach=())
    picked, own, skipped = considered(s, b)
    assert picked == [] and own == {}
    assert {k: v for k, v in skipped.items() if k.startswith("profile:")} == {
        "profile:buyer": "Needs to know: who buys", "profile:reach": "Needs to know: how they're reached"}
    assert set(skipped) - set(unchecked(skipped)) == {"profile:buyer", "profile:reach"}, "a place never waits on a line it cannot use"


def test_a_buyer_nobody_knows_holds_back_only_the_places_that_declare_it(s):
    b = make_business(s, buyer=(), reach=("communities",))
    picked, own, skipped = considered(s, b)
    assert "hacker_news" not in picked and {"x", "disquiet"} <= set(unchecked(skipped))
    assert skipped["profile:buyer"] == "Needs to know: who buys"
    assert [k for k in skipped if k.startswith("profile:")] == ["profile:buyer"]
    assert "hacker_news" not in own, "a place that waits is not also skipped for a reason of its own"


def test_the_unknown_line_is_worded_in_korean_and_the_owner_reads_no_english(s):
    b = make_business(s, buyer=(), reach=())
    _, skipped = promotion.pick(s, b.id, MATERIAL, 99, NOW, lang="ko")
    assert skipped["profile:buyer"] == "누가 사나를 알아야 해요"


def test_an_owner_rejecting_a_line_makes_the_week_ask_instead_of_picking_on_the_old_value(s):
    b = make_business(s)
    assert "hacker_news" in considered(s, b)[0]
    reject(s, b.id, "buyer")
    picked, _, skipped = considered(s, b)
    assert "hacker_news" not in picked and "profile:buyer" in skipped


def test_the_week_tells_the_marketing_agent_who_the_customers_are_and_how_sure_that_is(s, marketing):
    b = make_business(s, repo="owner/repo", buyer=(), reach=())
    describe(s, b.id, ["developers"], ["communities"], provenance="guess")
    week(b)
    assert "누가 사나: 개발자 (짐작)" in marketing.prompts[0]


# ------------------------------------------------------------------ the other readers

def test_a_business_whose_buyers_are_developers_has_the_star_row_and_says_when_that_is_a_guess(s):
    install_people()
    b = make_business(s, buyer=(), reach=())
    assert [x["stage"] for x in metrics.stage_trends(s, b.id, lang="en")] == ["first_value", "repeat"]
    describe(s, b.id, ["developers"], provenance="guess")
    stars = metrics.stage_trends(s, b.id, lang="en")[0]
    assert stars["stage"] == "interest" and stars["guess"] is True and "GitHub" in stars["why_unknown"]
    describe(s, b.id, ["developers"], provenance="confirmed")
    assert metrics.stage_trends(s, b.id, lang="en")[0]["guess"] is False
    assert all("guess" not in x for x in metrics.stage_trends(s, b.id, lang="en")[1:])


def test_a_shop_has_no_github_star_row_whatever_its_repo_is(s):
    install_people()
    b = make_business(s, repo="owner/repo", buyer=("consumers",), reach=("local",))
    assert [x["stage"] for x in metrics.stage_trends(s, b.id, lang="en")] == ["first_value", "repeat"]


def test_the_contact_card_asks_about_the_shop_when_the_description_says_in_person(s):
    b = make_people_business(s, buyer=("consumers",), reach=("local",))
    card = people_tasks.person_card(s, add(s, b, "regular", source="warm", platform="direct", url="brief:v1:l-1"),
                                    "contact", lang="ko")
    assert card["questions"][0] == msg("people.ask.contact_local.1", "ko")
    online = make_people_business(s, name="온라인 가게", buyer=("consumers",), reach=("communities",))
    card = people_tasks.person_card(s, add(s, online, "regular", source="warm", platform="direct", url="brief:v1:l-1"),
                                    "contact", lang="ko")
    assert card["questions"][0] != msg("people.ask.contact_local.1", "ko")


def test_the_people_tab_has_nothing_to_search_until_the_description_says_who_and_how(s):
    b = make_business(s, buyer=(), reach=())
    shown = people.listing(s, b.id, lang="en")["places"]
    assert shown["searched"] == [] and shown["by_hand"] == [] and "github" in shown["platforms"]
    describe(s, b.id, ["consumers"], ["introductions"])
    shown = people.listing(s, b.id, lang="en")["places"]
    assert shown["searched"] == [] and shown["by_hand"] == [], "no known place reaches people through introductions"


def test_a_value_written_now_is_the_next_readers_input_with_no_cache_between(s):
    b = make_business(s, buyer=(), reach=())
    assert considered(s, b)[0] == []
    describe(s, b.id, ["consumers"], ["local"])
    picked, _, skipped = considered(s, b)
    assert picked == [] and sorted(unchecked(skipped)) == sorted(OLD_CHANNELS["ko", "local"])


# ------------------------------------------------------------------ the market still picks the region

DOG_WALKS = "Matches busy dog owners with neighbors who walk dogs, a door-to-door service for one neighborhood"
LOCAL_MATERIAL = [promotion.Material("bet:1", "bet", "첫 도전", "끝남", None, NOW)]


def english(s, name, one_liner, buyer, reach):
    b = make_business(s, name=name, buyer=buyer, reach=reach)
    b.one_liner = one_liner
    s.commit()
    return b


def looked_at(s, business, material):
    """The channels the week looked at for the business: the ones it picked and the ones that fit and were left out only
    because their rules were not read."""
    picked, skipped = promotion.pick(s, business.id, material, 99, NOW, lang="en")
    return {c.channel.key for c in picked} | {k for k, v in skipped.items() if v == UNCHECKED}


def keys_of(s, business, material=LOCAL_MATERIAL):
    return looked_at(s, business, material)


def test_the_job_board_is_never_a_weekly_promotion_channel(s):
    b = make_business(s, buyer=("consumers",), reach=("local",))
    picked, skipped = promotion.pick(s, b.id, LOCAL_MATERIAL, 99, NOW, lang="en")
    assert "daangn_jobs" not in {c.channel.key for c in picked} | set(skipped)


def test_a_local_description_is_sent_to_local_channels_and_a_developer_one_to_developer_channels(s):
    local = keys_of(s, make_business(s, name="동네 가게", buyer=("consumers",), reach=("local",)))
    dev = keys_of(s, make_business(s))
    assert {"daangn_community", "naver_cafe"} <= local
    assert not local & {"hacker_news", "reddit_opensource", "devto", "geeknews", "okky"}
    assert "hacker_news" in dev and not dev & {"daangn_community", "daangn_jobs", "naver_cafe", "kakao_openchat"}
    assert not local & {"nextdoor", "facebook_groups", "reddit_local"}


def test_an_english_local_business_gets_english_local_channels_and_a_korean_one_keeps_daangn(s):
    material = [promotion.Material("bet:1", "bet", "First experiment", "done", None, NOW)]
    en = english(s, "Walkies", DOG_WALKS, ("consumers",), ("local",))
    ko = make_business(s, name="동네 가게", buyer=("consumers",), reach=("local",))
    en_looked, ko_looked = looked_at(s, en, material), looked_at(s, ko, material)
    assert {"nextdoor", "facebook_groups", "reddit_local"} <= en_looked
    assert {channels.get(k).region for k in en_looked} <= {"global", "en"}
    assert {"daangn_community", "naver_cafe", "kakao_openchat", "instagram_local"} <= ko_looked
    assert {channels.get(k).region for k in ko_looked} <= {"global", "ko"}


def test_an_english_developer_tool_gets_no_korean_community(s):
    b = english(s, "Termdoc", "An open-source CLI that documents the commands developers run in the terminal",
                ("developers",), ("communities",))
    picked = keys_of(s, b, [promotion.Material("release:1", "release", "v1", "", None, NOW)])
    assert "hacker_news" in picked and not picked & {"okky", "disquiet", "careerly", "discord_ko", "geeknews"}


def test_words_in_the_plan_do_not_decide_who_the_customers_are(s):
    """The plan says open-source CLI for developers, and the owner says the buyers are shoppers reached nearby: the
    description wins, which is the point of reading it instead of counting words."""
    b = make_business(s, buyer=("consumers",), reach=("local",))
    picked = keys_of(s, b)
    assert "daangn_community" in picked and "hacker_news" not in picked
