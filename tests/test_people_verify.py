"""The server proves a person exists and the quote is theirs: only the handle counts, it must stand just before the
quote as its nearest byline, a builder of the project is dropped, and only exact platform hosts are opened."""
import json

import pytest
from justshipit.services import people_finder
from justshipit.services.people_finder import _host_problem, confirm
from people_kit import ISSUE_QUOTE, ISSUE_URL, Web, candidate, issue_page, make_business, page
from test_people_finder import find, people_of, url


def verdict(text, handle="alice", *, platform="github", target=ISSUE_URL, quote=ISSUE_QUOTE, api=None):
    web = Web({target: page(text)})
    return confirm(platform, target, quote, handle, web, api or people_finder._no_api)


# ------------------------------------------------------------------ the author is the handle, only the handle

def test_a_display_name_that_is_a_word_inside_the_quote_cannot_stand_in_for_the_handle(s, monkeypatch):
    b = make_business(s)
    web = Web({ISSUE_URL: page("zed opened this issue " + ISSUE_QUOTE)})
    report, _ = find(monkeypatch, b, [candidate("totally-fake-user", display_name="logs")], web)
    assert report.dropped["author_not_found"] == 1 and people_of(b.id) == []


def test_a_handle_that_only_appears_inside_the_quote_is_not_its_author():
    quote = "alice told me the server logs are hopeless and I lose hours finding the root cause of any crash"
    assert verdict("zed opened this issue on Nov 2 " + quote, quote=quote) == "author_not_found"


def test_the_handle_after_the_quote_is_not_a_byline():
    assert verdict(f"zed opened this issue {ISSUE_QUOTE} alice commented on Nov 3") == "author_not_found"


def test_an_earlier_commenter_is_not_the_author_of_the_next_comment():
    assert verdict(f"alice commented: me too. zed commented: {ISSUE_QUOTE}") == "author_not_found"
    assert verdict(f"zed commented: me too. alice commented: {ISSUE_QUOTE}") is None


def test_a_mention_of_the_handle_is_not_a_byline():
    assert verdict(f"zed commented on Nov 2 @alice thanks, same here. {ISSUE_QUOTE}") == "author_not_found"
    assert verdict(f"zed commented on Nov 2 see alice-dev/logs for that. {ISSUE_QUOTE}") == "author_not_found"


def test_a_handle_that_is_only_part_of_a_longer_name_does_not_match():
    assert verdict(f"alice2 opened this issue {ISSUE_QUOTE}") == "author_not_found"
    assert verdict(f"malice opened this issue {ISSUE_QUOTE}") == "author_not_found"


def test_the_handle_must_stand_within_a_couple_of_hundred_characters():
    assert verdict(f"alice opened this issue {'filler ' * 60}{ISSUE_QUOTE}") == "author_not_found"
    assert verdict(f"alice opened this issue {'filler ' * 10}{ISSUE_QUOTE}") is None


def test_a_reddit_byline_with_the_u_prefix_counts():
    target = "https://www.reddit.com/r/devops/comments/abc/x"
    assert verdict(f"u/alice • 3h ago {ISSUE_QUOTE}", platform="reddit", target=target) is None
    assert verdict(f"u/bob • 3h ago {ISSUE_QUOTE}", platform="reddit", target=target) == "author_not_found"


# ------------------------------------------------------------------ someone who builds the project is not its user

def test_the_owner_of_the_repository_is_dropped_as_a_builder():
    assert verdict(f"acme opened this issue {ISSUE_QUOTE}", handle="acme") == "builder"


def test_a_maintainer_badge_in_the_page_marks_a_builder():
    assert verdict(f"alice commented on Nov 2 Member {ISSUE_QUOTE}") == "builder"
    assert verdict(f"alice commented on Nov 2 Collaborator {ISSUE_QUOTE}") == "builder"


def api_for(items, *, issue=None):
    base = "https://api.github.com/repos/acme/logtool/issues/41"
    return Web({base: ("url_ok", "HTTP 200", json.dumps(issue)) if issue else ("url_unreachable", "HTTP 404", None),
                f"{base}/comments?per_page=100": ("url_ok", "HTTP 200", json.dumps(items))})


def comment(login, association, body=ISSUE_QUOTE):
    return {"user": {"login": login}, "author_association": association, "body": body}


@pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR"])
def test_the_api_names_a_builder_whatever_the_page_text_shows(association):
    assert verdict("alice commented " + ISSUE_QUOTE, api=api_for([comment("alice", association)])) == "builder"


def test_the_api_names_the_real_author_even_when_the_page_text_would_pass_another_name():
    got = verdict(f"zed commented: me too. alice commented: {ISSUE_QUOTE}", handle="alice",
                  api=api_for([comment("zed", "NONE")]))
    assert got == "author_not_found"


def test_a_plain_user_confirmed_by_the_api_passes():
    assert verdict("an unrelated page text " + ISSUE_QUOTE, api=api_for([comment("Alice", "NONE")])) is None


def test_the_issue_body_is_read_from_the_issue_not_only_its_comments():
    issue = {"user": {"login": "alice"}, "author_association": "NONE", "body": ISSUE_QUOTE}
    assert verdict("page " + ISSUE_QUOTE, api=api_for([], issue=issue)) is None


def test_an_unreachable_api_falls_back_to_the_page_text():
    assert verdict(f"alice opened this issue {ISSUE_QUOTE}") is None
    assert verdict(f"zed opened this issue {ISSUE_QUOTE}") == "author_not_found"


def test_the_submitter_of_a_show_hn_is_dropped_but_a_commenter_on_it_is_not():
    target = "https://news.ycombinator.com/item?id=9"
    thread = ("Show HN: Logtool, find crashes in logs (logtool.dev) 12 points by alice 3 hours ago | hide | "
              "2 comments bob 2 hours ago | parent | next [-] " + ISSUE_QUOTE)
    assert verdict(thread, handle="alice", platform="hn", target=target) == "builder"
    assert verdict(thread, handle="bob", platform="hn", target=target) is None


def test_a_builder_never_becomes_a_person_on_the_list(s, monkeypatch):
    b = make_business(s)
    web = Web({url("acme"): issue_page("acme"), url("alice"): issue_page("alice")})
    report, _ = find(monkeypatch, b, [candidate("acme", url("acme")), candidate("alice", url("alice"))], web)
    assert report.dropped["builder"] == 1 and [p.handle for p in people_of(b.id)] == ["alice"]


# ------------------------------------------------------------------ hosts

@pytest.mark.parametrize("platform, link", [
    ("github", "https://evil.github.com/x"), ("github", "https://github.com.evil.com/x"),
    ("github", "https://notgithub.com/x"), ("github", "https://github.com:8443/x"),
    ("hn", "https://news.ycombinator.com.evil.io/item?id=1"), ("reddit", "https://evilreddit.com/r/x"),
    ("stackoverflow", "https://evil.stackexchange.com/q/1"), ("stackoverflow", "https://stackoverflow.com.evil.com/q/1"),
    ("forum", "https://localtest.me/x"), ("forum", "https://127.0.0.1.nip.io/x"), ("forum", "http://[::1]/x"),
    ("forum", "https://metadata.google.internal/x"), ("forum", "https://example.com/thread"),
    ("forum", "https://evil.dev.to/x"), ("forum", "https://dev.to.evil.com/x"),
])
def test_a_host_that_is_not_exactly_a_listed_one_is_wrong_site(platform, link):
    assert _host_problem(platform, link) == "wrong_site"


@pytest.mark.parametrize("platform, link", [
    ("github", "https://github.com/acme/logtool/issues/1"), ("github", "https://www.github.com/a/b/issues/2"),
    ("reddit", "https://old.reddit.com/r/devops/comments/x"), ("hn", "https://news.ycombinator.com/item?id=1"),
    ("stackoverflow", "https://serverfault.com/questions/1"), ("forum", "https://dev.to/x/y"),
    ("forum", "https://lobste.rs/s/abc/title"), ("forum", "https://discuss.python.org/t/x/1"),
])
def test_the_listed_hosts_pass(platform, link):
    assert _host_problem(platform, link) is None


def test_a_forum_host_the_config_does_not_list_is_dropped_and_never_opened(s, monkeypatch):
    b = make_business(s)
    other = "https://forum.unlisted.example/t/1"
    web = Web({other: page("alice posted " + ISSUE_QUOTE)})
    report, _ = find(monkeypatch, b, [candidate("alice", other, platform="forum")], web)
    assert report.dropped["wrong_site"] == 1 and web.opened == []


def test_the_config_decides_which_forums_are_open(s, monkeypatch):
    b = make_business(s)
    link = "https://forum.listed.example/t/1"
    monkeypatch.setattr(people_finder, "company_config", lambda: {"people": {"forum_hosts": ["forum.listed.example"]}})
    web = Web({link: page("alice posted " + ISSUE_QUOTE)})
    report, _ = find(monkeypatch, b, [candidate("alice", link, platform="forum")], web)
    assert report.added == 1


def test_find_people_reads_the_platform_api_only_when_it_fetches_for_real():
    assert people_finder._default_api(Web({})) is people_finder._no_api
    assert people_finder._default_api(people_finder.fetch_page) is people_finder.fetch_json
