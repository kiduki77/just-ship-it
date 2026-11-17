"""The channel registry and the asks no card may carry."""
import re

import pytest
from justshipit.services import channels


def test_hacker_news_and_show_hn_never_get_sentences_and_every_outline_channel_is_written_by_the_owner():
    modes = {k: c.mode for k, c in channels.registry().items()}
    assert modes["hacker_news"] == modes["show_hn"] == "outline"
    assert {k for k, m in modes.items() if m == "outline"} == {k for k, c in channels.registry().items() if c.self_written}
    assert {"hacker_news", "show_hn", "geeknews", "disquiet", "okky", "careerly", "facebook_dev_groups"} <= set(modes)
    assert {"hacker_news", "show_hn", "reddit", "product_hunt", "x", "geeknews", "dev_newsletter", "awesome_list",
            "github_release", "other"} <= set(modes)


def test_before_100_users_reddit_each_subreddit_product_hunt_and_x_give_points_and_never_sentences():
    reddit_units = [k for k, c in channels.registry().items() if k.startswith("reddit")]
    assert {"reddit", "reddit_opensource", "reddit_sideproject", "reddit_selfhosted", "reddit_claudeai"} <= set(reddit_units)
    for key in (*reddit_units, "product_hunt", "x"):
        assert (channels.get(key).mode, channels.get(key).self_written) == ("outline", True), key


def test_only_the_entries_that_name_no_place_are_generic():
    assert {k for k, c in channels.registry().items() if c.generic} == {"reddit", "other"}


@pytest.mark.parametrize("url,key,name", [
    ("https://www.daangn.com/kr/community/abc", "daangn_community", "당근 동네생활"),
    ("https://cafe.naver.com/mangwonmom/123", "naver_cafe", "네이버 카페"),
    ("https://open.kakao.com/o/abc", "kakao_openchat", "카카오 오픈채팅"),
    ("https://www.instagram.com/mangwon_news/", "instagram_local", "인스타그램 동네 계정"),
])
def test_a_local_place_has_its_own_name_and_is_not_filed_under_the_catch_all(url, key, name):
    assert channels.get(key).names["ko"] == name and channels.link_problem(channels.get(key), url) is None
    assert channels.link_problem(channels.get("other"), url).vars["channel"]["ko"] == name


@pytest.mark.parametrize("text,key", [
    ("Post the intro on Karrot", "daangn_community"),
    ("Ask on Nextdoor first", "nextdoor"),
    ("당근 동네생활에 첫 글", "daangn_community"),
    ("넥스트도어 동네 소개", "nextdoor"),
    ("Share in the KakaoTalk open chat", "kakao_openchat"),
])
def test_a_place_is_named_in_any_language(text, key):
    assert channels.named(text).key == key


def test_the_english_rules_of_every_channel_carry_no_hangul():
    for key, c in channels.registry().items():
        assert not re.search("[가-힣]", c.rules["en"]), key


def test_every_channel_says_what_its_rules_rest_on():
    for key, c in channels.registry().items():
        assert all(c.names[lang] and c.rules[lang] for lang in ("en", "ko")), key
        assert (c.source_url is None) == (c.basis == "none"), key
    assert channels.get("reddit").basis == "none"
    assert "원문은 읽지 못했" in channels.get("reddit").rules["ko"]
    assert "could not read" in channels.get("reddit").rules["en"]
    assert {channels.get(k).basis for k in ("hacker_news", "product_hunt", "devto", "hashnode")} == {"verified"}


@pytest.mark.parametrize("channel,url,ok", [
    ("hacker_news", "https://news.ycombinator.com/item?id=1", True),
    ("hacker_news", "https://example.com/item?id=1", False),
    ("hacker_news", "https://evilnews.ycombinator.com.example.com/x", False),
    ("reddit", "https://www.reddit.com/r/opensource/comments/abc", True),
    ("reddit", "https://old.reddit.com/r/x", True),
    ("reddit", "https://notreddit.com/r/x", False),
    ("x", "https://twitter.com/me/status/1", True),
    ("dev_newsletter", "https://tldr.tech/anything", True),
    ("other", "http://anything.example", True),
    ("hacker_news", "ftp://news.ycombinator.com/x", False),
    ("hacker_news", "news.ycombinator.com/item?id=1", False),
    ("hacker_news", "javascript:alert(1)", False),
])
def test_a_link_must_be_http_and_on_the_channels_own_domain_where_one_is_known(channel, url, ok):
    assert (channels.link_problem(channels.get(channel), url) is None) is ok


@pytest.mark.parametrize("text", [
    "많은 분이 업보트해 주시면 좋겠습니다",
    "Please upvote us on launch day",
    "GitHub에서 star를 눌러 주세요",
    "스타를 부탁드립니다",
    "If you like it, give us a star!",
    "star the repo if it helps",
    "⭐ 눌러 주세요",
    "리뷰를 남겨 주세요",
    "후기를 부탁드립니다",
    "Leave us a review on the store",
    "Rate us 5 stars",
    "친구들에게 투표를 부탁해 보세요",
    "Ask your friends to vote for us",
    "리뷰를 남기면 커피 쿠폰을 드립니다",
    "star를 눌러 주시는 분께 선물을 드려요",
    "Get a free month in exchange for an upvote",
    "We'll give a gift card for every review",
])
def test_asking_for_votes_stars_or_reviews_or_paying_for_them_is_found(text):
    assert channels.forbidden_hits(text), text
    assert channels.forbidden_reason([text]).key == "post.ask.refused"


@pytest.mark.parametrize("text", [
    "오픈소스 도구를 공개했습니다. 목표는 GitHub star 100개입니다",
    "스타트업 창업자를 위한 도구입니다",
    "무료로 써 볼 수 있고 가입이 필요 없습니다",
    "투표를 부탁하지 않기",
    "Don't ask for upvotes",
    "리뷰 요청은 하지 않는다. 별점 부탁도 금지",
    "Never solicit stars or reviews",
    "다섯 가지 기능을 소개합니다",
    "Reviewing the code takes 10 minutes",
])
def test_stating_a_goal_or_telling_the_owner_not_to_do_it_is_not_an_ask(text):
    assert channels.forbidden_hits(text) == [], text
    assert channels.forbidden_reason([text]) is None


def test_the_reason_names_every_kind_of_ask_once():
    why = channels.forbidden_reason(["업보트 부탁", "리뷰를 남겨 주세요", "또 업보트"])
    assert [found.vars["ask"].key for found in why.vars["found"]] == ["post.ask.votes", "post.ask.reviews"]


@pytest.mark.parametrize("text", [
    "Don't forget to upvote us!",
    "Never miss a chance: upvote this post",
    "If you like it, please upvote",
    "추천 눌러 주세요",
    "좋아요 부탁드립니다",
    "별 하나 눌러 주세요",
    "깃허브 스타 하나만 부탁드려요",
    "스타 하나 부탁해요",
    "ｕｐｖｏｔｅ please",
    "up\u200bvote please",
    "upv0te pls",
    "Would love your support on PH today 🙏 — hit the arrow",
    "Leave us 5 stars on the App Store",
    "Please leave a quick review",
    "review us and get 10% off",
    "Rate it on G2 and we'll send a $10 Amazon card",
    "Give the repo a ⭐",
    "Give the repo a star",
    "Upvotes appreciated",
    "투표 꼭 해주세요",
    "후기 남겨주시면 커피 쿠폰 드립니다",
    "댓글 달아주시면 추첨해서 기프티콘 드립니다",
    "Smash that like button",
    "We'd appreciate your vote",
    "support us on Product Hunt",
    "Don't hesitate to star the repo",
    "No pressure, but upvote if you like it",
    "Help us reach #1 on Product Hunt by voting",
    "u p v o t e now",
    "h1t the arrow",
    "Don't ask for upvotes but please upvote",
    "Don't ask for upvotes. Just kidding, upvote!",
    "We never ask for stars, but do us a favor and star us",
    "추천 한 번만 눌러 주시면 감사하겠습니다",
    "리뷰 하나만 남겨 주세요",
    "별점 5점 부탁드립니다",
    "Never forget to leave a review",
    "uрvote please",
    "별을 눌러 주세요",
    "별을 눌러주세요",
    "깃허브 별을 꾹 눌러 주세요",
    "스타를 눌러 주세요",
    "별 눌러줘",
    "별 하나만 박아 주세요",
    "별 클릭 부탁드려요",
    "star 눌러 주세요",
    "저장소에 별은 꼭 찍어 주세요",
])
def test_the_asks_an_independent_review_slipped_past_the_first_check_are_found(text):
    assert channels.forbidden_hits(text), text


@pytest.mark.parametrize("text", [
    "Do not solicit upvotes",
    "We never ask people to upvote",
    "Avoid asking for stars or reviews",
    "별을 눌러 달라고 하지 않는다",
    "투표해 달라고 하지 않기",
    "후기를 부탁하지 않습니다",
    "광고비 $20로 star 30개를 목표로 한다",
    "예산 $50 중 리뷰 수 확인에 쓴다",
    "v1.2 released with 3 fixes",
    "목표 5 stars 달성 여부를 추적",
    "Show HN은 star 30개가 목표 지표입니다",
    "별도로 정리해 주세요",
    "별표 목록을 눌러 보세요",
    "별개의 문제라서 따로 본다",
])
def test_a_negation_exempts_only_the_ask_it_governs_and_numbers_stay_numbers(text):
    assert channels.forbidden_hits(text) == [], text


def test_a_bare_negation_elsewhere_in_the_clause_does_not_exempt_the_ask():
    assert channels.forbidden_hits("It is not hard: just upvote")
    assert channels.forbidden_hits("Never mind the rules, upvote us")
    assert not channels.forbidden_hits("Never ask for upvotes")


@pytest.mark.parametrize("url", [
    "https://evil.com\\@news.ycombinator.com/item?id=1",
    "https://news.ycombinator.com@evil.com/",
    "https://user:pw@news.ycombinator.com/",
    "https://news%2eycombinator.com/item?id=1",
    "https://news.ycombinator.com /x",
    "https://news.ycombinator.com.evil.com/",
    "https://xn--news-ycombinator.com/",
    "https://evilreddit.com/r/x",
])
def test_an_address_that_hides_its_real_host_is_never_taken_for_the_channels_own(url):
    for key in ("hacker_news", "reddit"):
        assert channels.link_problem(channels.get(key), url), url


def test_the_host_is_read_the_way_a_browser_reads_it():
    hn = channels.get("hacker_news")
    assert channels.link_problem(hn, "https://NEWS.ycombinator.com./item?id=1") is None
    assert channels.link_problem(hn, "https://ｎｅｗｓ.ycombinator.com/item?id=1") is None


def test_the_other_channel_never_takes_an_address_that_belongs_to_another_channel():
    other = channels.get("other")
    for url in ("https://news.ycombinator.com/submit", "https://NEWS.ycombinator.com./submit",
                "https://old.reddit.com/r/x", "https://www.producthunt.com/posts/x", "https://github.com/me/repo"):
        assert channels.link_problem(other, url).key == "post.url.wrong_channel", url
    assert channels.link_problem(other, "https://my-blog.example/post") is None
    assert channels.link_problem(None, "https://news.ycombinator.com/item?id=1") is None


@pytest.mark.parametrize("query", ["text=hello", "title=Show+HN", "body=x", "t=x", "TITLE=x", "a=1&text="])
def test_a_where_url_that_arrives_with_the_text_filled_in_is_refused(query):
    problem = channels.where_url_problem(channels.get("reddit"), f"https://www.reddit.com/r/x/submit?{query}")
    assert problem and problem.key == "post.url.prefilled"


def test_a_where_url_is_short_and_otherwise_checked_like_any_link():
    reddit = channels.get("reddit")
    assert channels.where_url_problem(reddit, "https://www.reddit.com/r/opensource/submit") is None
    assert channels.where_url_problem(reddit, "https://www.reddit.com/" + "a" * 600).vars == {"n": "500"}
    assert channels.where_url_problem(reddit, "https://evil.example/r/x")


def test_a_channel_that_knows_its_post_address_gives_an_example_and_patterns_the_console_can_run():
    """The post form checks the same patterns in the browser, so they keep to syntax JavaScript reads the same way."""
    shaped = [c for c in channels.registry().values() if c.post_link]
    assert {"hacker_news", "show_hn", "reddit", "x"} <= {c.key for c in shaped}
    for c in shaped:
        assert c.post_example, c.key
        for p in c.post_link:
            re.compile(p)
            assert not re.search(r"\(\?[PiLmsux<]|\[AZz]", p), (c.key, p)
