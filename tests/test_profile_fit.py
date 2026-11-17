"""The channel and place tags reproduce the picks the word-counting classifier made. Each traced business (a developer
tool, a shop, a consumer app, and an owner who is reached only through introductions) is described with confirmed values,
and the weekly channels and people-finder places its old kind picked, frozen below from the code before it was deleted,
are compared with what `fits` picks, per market. A difference is listed with its reason, so an unintended one fails.
The old enum maps dev -> buyer developers, local -> reach local, general -> neither. Pure data: no database."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from justshipit.services import channels, people
from justshipit.services.profile import core
from justshipit.services.profile.core import Fit, Miss, Unsure, Value, assemble

_ids = iter(range(1, 10_000))


def described(buyer=(), reach=(), provenance="confirmed", *, unknown=()):
    rows = [Value(next(_ids), line, code, None, provenance, "console", None, None, "owner",
                  datetime(2026, 11, 13, tzinfo=UTC))
            for line, codes in (("buyer", buyer), ("reach", reach)) for code in codes]
    rows += [Value(next(_ids), line, None, None, "unknown", "console", None, None, "owner",
                   datetime(2026, 11, 13, tzinfo=UTC)) for line in unknown]
    return assemble(1, rows)


# The weekly channels of each old kind in each market, in registry order (global first), as the classifier's per-channel
# kinds gave them, and the places its two people-finder tables listed (searched, by hand).
_DEV = ("hacker_news", "show_hn", "reddit_opensource", "reddit_sideproject", "reddit_selfhosted", "reddit_claudeai",
        "product_hunt", "devto", "hashnode", "x", "discord_global", "dev_newsletter", "awesome_list", "github_release")
OLD_CHANNELS = {
    ("ko", "dev"): (*_DEV, "geeknews", "disquiet", "okky", "careerly", "facebook_dev_groups", "discord_ko"),
    ("ko", "local"): ("daangn_community", "naver_cafe", "kakao_openchat", "instagram_local"),
    ("ko", "general"): ("product_hunt", "x", "disquiet", "naver_cafe", "kakao_openchat"),
    ("en", "dev"): _DEV,
    ("en", "local"): ("nextdoor", "facebook_groups", "reddit_local"),
    ("en", "general"): ("product_hunt", "x", "facebook_groups"),
}
_DEV_SEARCHED = ("github", "reddit", "hn", "stackoverflow", "forum")
OLD_PLACES = {  # (searched, by hand)
    ("ko", "dev"): (_DEV_SEARCHED, ("discord", "x")),
    ("ko", "local"): (("daangn", "naver_cafe", "naver_blog"), ("kakao_openchat", "instagram")),
    ("ko", "general"): (("naver_cafe", "naver_blog", "reddit"), ("kakao_openchat", "instagram", "x")),
    ("en", "dev"): (_DEV_SEARCHED, ("discord", "x")),
    ("en", "local"): (("reddit",), ("nextdoor", "facebook_groups", "instagram")),
    ("en", "general"): (("reddit",), ("facebook_groups", "instagram", "x")),
}

# name: (the kind the old classifier gave such a business, its description)
CASES = {
    "developer tool": ("dev", described(["developers"], ["communities"])),
    "shop": ("local", described(["consumers"], ["local"])),
    "consumer app": ("general", described(["consumers"], ["communities"])),
    "introductions only": ("general", described(["consumers"], ["introductions"])),
}
MARKETS = ("ko", "en")

_NO_PLACE = "no known place reaches people through introductions (design 3.2)"
# (case, market): channel keys the old kind picked that the description does not, and the reverse. Nothing else differs.
_NEW_PLACE = "a place added after the classifier was deleted (0.7)"
CHANNELS_INTENDED = {
    ("developer tool", "ko"): ({}, {"lobsters": _NEW_PLACE}),
    ("developer tool", "en"): ({}, {"lobsters": _NEW_PLACE}),
    ("introductions only", "ko"): ({"product_hunt": _NO_PLACE, "disquiet": _NO_PLACE, "x": _NO_PLACE,
                                    "naver_cafe": _NO_PLACE, "kakao_openchat": _NO_PLACE}, {}),
    ("introductions only", "en"): ({"product_hunt": _NO_PLACE, "x": _NO_PLACE, "facebook_groups": _NO_PLACE}, {}),
}
# (case, market): (searched, by hand) the old tables listed that `where` does not, and the reverse.
PLACES_INTENDED = {
    ("introductions only", "ko"): ({"naver_cafe": _NO_PLACE, "naver_blog": _NO_PLACE, "reddit": _NO_PLACE,
                                    "kakao_openchat": _NO_PLACE, "instagram": _NO_PLACE, "x": _NO_PLACE}, {}),
    ("introductions only", "en"): ({"reddit": _NO_PLACE, "facebook_groups": _NO_PLACE, "instagram": _NO_PLACE,
                                    "x": _NO_PLACE}, {}),
}


def weekly(market):
    return [c for c in channels.for_market(market) if not c.generic and c.weekly]


def picks(c, prof):
    return isinstance(core.fit(c.fits, prof), Fit)


@pytest.mark.parametrize("market", MARKETS)
@pytest.mark.parametrize("case", CASES)
def test_the_tags_pick_the_weekly_channels_the_old_kind_picked_except_the_named_differences(case, market):
    kind, prof = CASES[case]
    old = set(OLD_CHANNELS[market, kind])
    new = {c.key for c in weekly(market) if picks(c, prof)}
    removed, added = CHANNELS_INTENDED.get((case, market), ({}, {}))
    assert (old - new, new - old) == (set(removed), set(added))


KO_DEV_ONLY = {"hacker_news", "show_hn", "reddit_opensource", "reddit_sideproject", "reddit_selfhosted",
               "reddit_claudeai", "discord_global", "geeknews", "okky", "careerly", "facebook_dev_groups", "discord_ko",
               "devto", "hashnode", "dev_newsletter", "awesome_list", "github_release", "lobsters"}


def test_the_korean_traces_of_the_design_hold():
    ko = {case: {c.key for c in weekly("ko") if picks(c, prof)} for case, (_, prof) in CASES.items()}
    assert ko["developer tool"] == KO_DEV_ONLY | {"product_hunt", "disquiet", "x"}
    assert ko["shop"] == {"daangn_community", "naver_cafe", "kakao_openchat", "instagram_local"}
    assert ko["consumer app"] == {"product_hunt", "disquiet", "x", "naver_cafe", "kakao_openchat"}
    assert ko["introductions only"] == set()


def test_a_neighborhood_place_is_not_in_a_consumer_apps_picks():
    """The design tagged daangn_community, reddit_local and nextdoor `reach [local, communities]`, and by the match rule
    that fits an app reached through online communities, which the old `general` never got. They say `reach [local]`."""
    app = CASES["consumer app"][1]
    for key in ("daangn_community", "reddit_local", "nextdoor"):
        assert isinstance(core.fit(channels.get(key).fits, app), Miss), key
    assert isinstance(core.fit({"buyer": ["consumers"], "reach": ["local", "communities"]}, app), Fit)


def test_the_one_time_launch_channels_are_the_ones_with_requires():
    assert {c.key for c in channels.registry().values() if c.requires} == {"show_hn", "product_hunt", "geeknews"}


def old_where(kind, market):
    return OLD_PLACES[market, kind]


@pytest.mark.parametrize("market", MARKETS)
@pytest.mark.parametrize("case", CASES)
def test_where_lists_the_places_the_old_tables_listed_in_their_order_except_the_named_differences(case, market):
    kind, prof = CASES[case]
    old, new = old_where(kind, market), people.where(prof, market)
    removed, added = PLACES_INTENDED.get((case, market), ({}, {}))
    assert ({k for part in old for k in part} - {k for part in new for k in part},
            {k for part in new for k in part} - {k for part in old for k in part}) == (set(removed), set(added))
    if (case, market) not in PLACES_INTENDED:
        assert new == old


def test_the_introductions_only_owner_gets_nothing_searched_and_nothing_by_hand():
    assert people.where(CASES["introductions only"][1], "ko") == ((), ())
    assert people.where(CASES["introductions only"][1], "en") == ((), ())


def test_reddit_is_a_topic_community_in_korea_and_a_city_subreddit_in_english():
    shop = CASES["shop"][1]
    assert "reddit" not in people.where(shop, "ko")[0]
    assert "reddit" in people.where(shop, "en")[0]
    assert "reddit" in people.where(CASES["consumer app"][1], "ko")[0]


def test_a_place_is_not_offered_in_a_market_it_has_no_role_in():
    shop = CASES["shop"][1]
    assert "nextdoor" not in sum(people.where(shop, "ko"), ())
    assert "daangn" not in sum(people.where(shop, "en"), ())


def test_where_never_offers_the_places_added_by_hand_or_the_catch_all():
    for _, prof in CASES.values():
        for market in MARKETS:
            assert not {"email", "direct", "other"} & set(sum(people.where(prof, market), ()))


# Outside the traced cases the tags answer from the codes, not from the old kind. Each line says what changes.
def test_a_shop_that_sells_to_businesses_is_not_in_the_neighborhood_boards_picks():
    """The old `local` kind sent any business with local words to the neighborhood places. The design tags them for
    consumers, since neighbors are consumers, so a shop whose buyers are small businesses keeps the groups and cafes."""
    prof = described(["small_business"], ["local"])
    assert {c.key for c in weekly("ko") if picks(c, prof)} == {"naver_cafe", "kakao_openchat"}
    assert people.where(prof, "ko") == (("naver_cafe", "naver_blog"), ("kakao_openchat", "instagram"))


def test_a_developer_tool_reached_by_search_gets_the_publishing_sites_and_no_community_channel():
    """The old `dev` kind sent every developer product to the communities. A description that says its buyers are
    found by search reaches the sites where posts are found by search, and no forum, subreddit or chat."""
    prof = described(["developers"], ["search"])
    assert {c.key for c in weekly("en") if picks(c, prof)} == {"devto", "hashnode", "dev_newsletter", "awesome_list",
                                                              "github_release"}
    assert people.where(prof, "en") == (("github", "hn", "stackoverflow", "forum"), ())


@pytest.mark.parametrize("market", MARKETS)
def test_a_declared_line_nobody_knows_is_not_picked_and_a_line_left_out_is_not_asked(market):
    buyer_unknown = described(reach=["communities"], unknown=["buyer"])
    never_asked = described(reach=["communities"])
    for prof in (buyer_unknown, never_asked):
        by_key = {c.key: core.fit(c.fits, prof) for c in weekly(market)}
        assert by_key["hacker_news"] == Unsure(("buyer",))
        assert isinstance(by_key["product_hunt"], Fit) and isinstance(by_key["x"], Fit)
        assert not picks(channels.get("hacker_news"), prof)
        searched, by_hand = people.where(prof, market)
        assert "github" not in searched and "x" in by_hand and "reddit" in searched


def test_a_miss_on_a_known_line_wins_over_an_unknown_one():
    prof = described(reach=["local"], unknown=["buyer"])
    assert isinstance(core.fit(channels.get("product_hunt").fits, prof), Miss)
    assert isinstance(core.fit(channels.get("naver_cafe").fits, prof), Unsure)
    assert isinstance(core.fit({"buyer": ["developers"], "reach": ["communities"]}, prof), Miss)


def test_a_guess_fits_and_marks_the_line_for_the_owner_to_check():
    prof = described(["developers"], ["communities"], "guess")
    result = core.fit(channels.get("hacker_news").fits, prof)
    assert result == Fit({"buyer": ("developers",), "reach": ("communities",)}, ("buyer", "reach"))
    assert "hacker_news" in {c.key for c in weekly("en") if picks(c, prof)}


def test_the_generic_channels_name_no_place_and_are_never_picked_weekly():
    for key in ("reddit", "other"):
        assert channels.get(key).fits == {} and channels.get(key).generic
    assert channels.get("daangn_jobs").fits == {"reach": ("local",)} and not channels.get("daangn_jobs").weekly


def write_channels(tmp_path, monkeypatch, replace):
    text = (channels.settings().config_dir / "channels.yaml").read_text(encoding="utf-8")
    assert replace[0] in text
    (tmp_path / "channels.yaml").write_text(text.replace(*replace, 1), encoding="utf-8")
    monkeypatch.setattr(channels, "settings", lambda: SimpleNamespace(config_dir=tmp_path))
    channels.registry.cache_clear()


def test_the_shipped_registry_loads_its_tags():
    assert channels.registry()["hacker_news"].fits == {"buyer": ("developers",), "reach": ("communities",)}


@pytest.mark.parametrize(("tag", "why"), [
    ("{buyer: [developer]}", "not live codes"),
    ("{reach: [developers]}", "not live codes"),
    ("{audience: [dev]}", "not a line"),
    ("{buyer: []}", "no code"),
])
def test_a_bad_tag_stops_the_registry_at_load_and_names_the_channel(tmp_path, monkeypatch, tag, why):
    write_channels(tmp_path, monkeypatch, ("fits: {buyer: [developers], reach: [communities]}", f"fits: {tag}"))
    try:
        with pytest.raises(ValueError, match=rf"hacker_news fits .*{why}"):
            channels.registry()
    finally:
        channels.registry.cache_clear()


def test_a_retired_code_may_not_be_tagged(monkeypatch):
    from justshipit.services.profile import vocab

    monkeypatch.setattr(vocab, "RETIRED", frozenset({"short_video"}))
    assert "short_video" in channels.fits_problem({"reach": ["short_video"]})
    assert channels.fits_problem({"reach": ["local"]}) is None


def test_a_place_with_a_bad_tag_fails_at_import_and_names_the_place():
    bad = {"x": people.Place("x", fits={"reach": ("carrier_pigeon",)})}
    with pytest.raises(ValueError, match="x fits .*carrier_pigeon"):
        people._check_fits(bad)
    with pytest.raises(ValueError, match="x fits .*nobody"):
        people._check_fits({"x": people.Place("x", fits_in={"en": {"buyer": ("nobody",)}})})
