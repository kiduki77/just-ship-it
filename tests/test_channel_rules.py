"""A place whose rules were not read gets no post written (roadmap 0.7, docs/design/channel-rules.md): the registry says
which places are checked, the weekly promotion and the agents' requests write nothing for the others, a stored card says
what is true of its place now, and the owner's list of places says the same."""
import re
from datetime import date
from types import SimpleNamespace

import pytest
from justshipit.api.app import app
from justshipit.contracts.post_card import PostCardDraft
from justshipit.db.models import ActivityEvent, OwnerTask
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.i18n.said import say
from justshipit.services import channels, owner_tasks, promotion, requests, tracking
from justshipit.services.profile import core
from bet_loop_kit import NOW, make_business, reset_world
from fastapi.testclient import TestClient
from lang_kit import owner_speaks
from owner_task_kit import as_stored_before_the_rules_check, draft, open_task, post_card, what
from ask_kit import by_project_manager
from sqlalchemy import select, text
from test_promotion import ScriptedMarketing, latest_week_event, tasks, week

# Every key the registry held before 0.7. A key lives in cards, `heard_via` values, channel drops and tags, so none may go.
OLD_KEYS = (
    "hacker_news", "show_hn", "reddit", "reddit_opensource", "reddit_sideproject", "reddit_selfhosted", "reddit_claudeai",
    "product_hunt", "devto", "hashnode", "x", "discord_global", "dev_newsletter", "awesome_list", "github_release",
    "geeknews", "disquiet", "okky", "careerly", "facebook_dev_groups", "discord_ko", "daangn_community", "daangn_jobs",
    "naver_cafe", "kakao_openchat", "instagram_local", "nextdoor", "facebook_groups", "reddit_local", "other")
CHECKED = {"hacker_news", "show_hn", "product_hunt", "devto", "hashnode", "github_release", "linkedin", "lobsters",
           "geeknews"}
UNREAD = {"reddit_opensource", "reddit_sideproject", "reddit_selfhosted", "reddit_claudeai"}
UNCHECKED = msg("promotion.skip.rules_unchecked", "en")


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    with session_scope() as s:
        s.execute(text("DELETE FROM channel_drops"))
    yield
    reset_world()


@pytest.fixture
def business(s):
    return make_business(s, repo="owner/repo")


@pytest.fixture
def marketing(monkeypatch):
    m = ScriptedMarketing()
    monkeypatch.setattr(promotion, "run_agent", m)
    return m


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000", headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


# ------------------------------------------------------------------ the registry

def test_every_key_the_registry_held_before_still_resolves():
    assert len(OLD_KEYS) == 30 and set(OLD_KEYS) <= set(channels.keys())


def test_a_place_is_checked_exactly_when_its_rules_were_read_and_never_neither():
    for key, c in channels.registry().items():
        assert channels.state(c) == ("checked" if c.basis == "verified" else "unchecked"), key
        assert channels.writable(c) is (c.basis == "verified"), key
        if channels.state(c) == "checked":
            assert isinstance(c.checked_on, date) and c.unchecked_why is None and c.source_url, key
        else:
            assert c.checked_on is None and c.unchecked_why in ("not_read", "per_unit", "no_source"), key
            assert c.source_url is None, key
    assert {k for k, c in channels.registry().items() if channels.writable(c)} == CHECKED


def test_each_unchecked_place_says_why_and_a_background_page_is_not_a_source():
    reg = channels.registry()
    assert {reg[k].unchecked_why for k in (*UNREAD, "reddit", "x", "reddit_local")} == {"not_read"}
    assert {reg[k].unchecked_why for k in ("discord_global", "awesome_list", "dev_newsletter", "other")} == {"per_unit"}
    assert reg["discord_global"].background_url == "https://discord.com/guidelines"
    assert reg["awesome_list"].background_url.startswith("https://github.com/sindresorhus/awesome/")
    assert {k for k, c in reg.items() if c.background_url} == {"discord_global", "awesome_list"}
    assert reg["geeknews"].checked_on == date(2026, 11, 3)


def test_the_places_that_were_read_say_only_what_was_read():
    reg = channels.registry()
    assert "automate posting" in reg["hacker_news"].rules["en"] and "AI-edited" in reg["hacker_news"].rules["en"]
    assert "landing pages" not in reg["show_hn"].rules["en"] and "guidelines" in reg["show_hn"].rules["en"]
    ph = reg["product_hunt"].rules["en"]
    assert all(x in ph for x in ("Skip link shorteners", "40 characters", "60", "500", "two months"))
    assert reg["product_hunt"].repost.min_interval_days == 180 and reg["product_hunt"].repost.basis == "none"
    assert "company's choice" in ph
    assert "70 days" in reg["lobsters"].rules["en"] and reg["lobsters"].link == "plain"
    assert reg["lobsters"].link_basis == "verified"
    assert "take responsibility" in reg["hashnode"].rules["en"] and reg["hashnode"].mode == "draft"


def test_devto_is_outline_and_written_by_the_owner_and_the_new_places_follow_their_neighbours():
    dev = channels.get("devto")
    assert (dev.mode, dev.self_written, dev.basis, dev.source_url) == ("outline", True, "verified", "https://dev.to/terms")
    for key in ("linkedin", "lobsters"):
        c = channels.get(key)
        assert (c.mode, c.self_written, c.region, c.repost.basis, c.repost.source_url) == ("outline", True, "global", "none", None)
        assert c.fits and c.requires == () and c.tag and c.domains
    assert {channels.get("linkedin").tag, channels.get("lobsters").tag} == {"li", "lb"}


# ------------------------------------------------------------------ the load-time checks

def _load(tmp_path, monkeypatch, old: str, new: str):
    text_ = (channels.settings().config_dir / "channels.yaml").read_text(encoding="utf-8")
    assert old in text_
    (tmp_path / "channels.yaml").write_text(text_.replace(old, new, 1), encoding="utf-8")
    monkeypatch.setattr(channels, "settings", lambda: SimpleNamespace(config_dir=tmp_path))
    channels.registry.cache_clear()
    try:
        channels.registry()
    finally:
        channels.registry.cache_clear()


HN_READ = "    checked_on: 2026-11-16\n    domains: [news.ycombinator.com]\n    post_link: &hn_post"
X_WHY = "    unchecked_why: not_read\n    domains: [x.com, twitter.com]"


@pytest.mark.parametrize(("old", "new", "match"), [
    (HN_READ, "    domains: [news.ycombinator.com]\n    post_link: &hn_post", "hacker_news needs checked_on"),
    (HN_READ, "    checked_on: not a date\n    domains: [news.ycombinator.com]\n    post_link: &hn_post",
     "hacker_news needs checked_on"),
    (HN_READ, HN_READ.replace("checked_on", "unchecked_why: per_unit\n    checked_on"),
     "hacker_news needs unchecked_why exactly"),
    (X_WHY, "    domains: [x.com, twitter.com]", "x needs unchecked_why"),
    (X_WHY, "    checked_on: 2026-11-16\n" + X_WHY, "x needs checked_on"),
    (X_WHY, "    unchecked_why: forgotten\n    domains: [x.com, twitter.com]", "unchecked_why 'forgotten'"),
    ("    source_url: https://news.ycombinator.com/newsguidelines.html\n", "    source_url: null\n",
     "hacker_news needs a source_url"),
])
def test_a_registry_that_reads_its_rules_two_ways_or_none_stops_at_load(tmp_path, monkeypatch, old, new, match):
    with pytest.raises(ValueError, match=match):
        _load(tmp_path, monkeypatch, old, new)


def test_a_background_page_on_an_unchecked_place_loads_and_on_a_checked_one_does_not(tmp_path, monkeypatch):
    _load(tmp_path, monkeypatch, X_WHY, X_WHY.replace("unchecked_why", "background_url: https://x.example/\n    unchecked_why"))
    with pytest.raises(ValueError, match="hacker_news has a background_url"):
        _load(tmp_path, monkeypatch, HN_READ, "    background_url: https://hn.example/\n" + HN_READ)


def test_a_source_on_a_place_that_was_not_read_stops_the_registry_at_load(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="x needs a source_url"):
        _load(tmp_path, monkeypatch, X_WHY, "    source_url: https://x.example/rules\n" + X_WHY)


# ------------------------------------------------------------------ the weekly promotion

MATERIAL = [promotion.Material("release:new", "release", "v2", "", None, NOW)]


def test_an_unchecked_place_is_skipped_for_its_rules_before_any_other_reason(s, business):
    with session_scope() as x:
        promotion.drop_channel(x, business.id, "reddit_opensource", None, "첫 가치 0명")
    with session_scope() as x:
        picked, skipped = promotion.pick(x, business.id, MATERIAL, 99, NOW, lang="en")
    assert "reddit_opensource" not in [c.channel.key for c in picked]
    assert skipped["reddit_opensource"] == UNCHECKED, "dropped too, and the rules are still the reason"
    assert all(channels.writable(c.channel) for c in picked)
    assert {"x", "discord_global", "dev_newsletter", "awesome_list", *UNREAD} <= set(skipped)
    assert all(skipped[k] == UNCHECKED for k in ("x", "discord_global", "dev_newsletter", "awesome_list"))


def test_the_prompt_the_marketing_agent_reads_holds_no_unchecked_place(s, business, marketing):
    week(business)
    prompt = marketing.prompts[0]
    keys = [line.split()[1] for line in prompt.splitlines() if line.startswith("### ")]
    assert keys and all(channels.writable(channels.get(k)) for k in keys)
    assert not any(k in prompt for k in ("reddit_opensource", "discord_global", "dev_newsletter", "awesome_list"))


def test_the_weeks_record_names_the_places_left_out_for_unchecked_rules(s, business, marketing):
    report = week(business)
    event = latest_week_event(s, business)
    left = set(event.data["rules_unchecked"])
    assert {"reddit_opensource", "x", "awesome_list"} <= left and not left & CHECKED
    assert left <= set(report.skipped) and all(report.skipped[k] == msg("promotion.skip.rules_unchecked", "ko") for k in left)
    assert "규칙을 읽지 못해 뺀 채널:" in event.summary and "Reddit r/opensource" in event.summary


def test_a_week_with_no_place_the_company_may_write_for_says_so_and_is_not_an_error(s, marketing):
    shop = make_business(s, name="동네 가게", buyer=("consumers",), reach=("local",))
    report = week(shop)
    assert report.tasks == [] and marketing.calls == 0
    assert report.note == msg("promotion.note.no_writable", "ko")
    event = latest_week_event(s, shop)
    assert report.note in event.summary and "daangn_community" in event.data["rules_unchecked"]
    assert f"{report.note}. " in event.summary, "the note and the left-out sentence are two sentences"
    assert msg("promotion.note.no_writable", "en").startswith("No place that fits this business")


def test_a_card_for_an_unchecked_place_is_refused_and_never_asked_for_again(s, business, marketing, monkeypatch):
    """The agent writes a card for every channel it is shown. Even if a closed place were shown, no card is kept, and the
    refusal is not sent back for a rewrite, because no rewrite can open the place."""
    real = promotion.pick
    fit = core.Fit(basis={})

    def pick_a_closed_place(*a, **kw):
        picked, skipped = real(*a, **kw)
        return [promotion.Candidate(channels.get("reddit_opensource"), MATERIAL, fit), *picked[:1]], skipped
    monkeypatch.setattr(promotion, "pick", pick_a_closed_place)

    report = week(business, lang="en")

    assert marketing.calls == 1, "a refusal no rewrite can lift is not sent back to the agent"
    assert "rules_unchecked" in promotion._NOT_THE_WRITING
    assert [t.card["channel"] for t in tasks(s, business)] == ["hacker_news"]
    assert report.skipped["reddit_opensource"] == msg("promotion.skip.refused", "en", rule="rules_unchecked")


# ------------------------------------------------------------------ build_post_card

GATE = (lambda _channel: [])


def _draft(key, **kw) -> PostCardDraft:
    mode = channels.get(key).mode
    w = what() if mode == "outline" else what(title="제목", body="본문", facts=[], expected_questions=[], checklist=["확인"])
    return post_card(key, what=w, **kw)


@pytest.mark.parametrize("key", sorted(set(channels.keys()) - CHECKED - {"other"}))
def test_a_card_for_a_place_whose_rules_were_not_read_is_refused_with_its_name_in_both_languages(key):
    for lang in ("en", "ko"):
        with pytest.raises(owner_tasks.CardRefused) as err:
            owner_tasks.build_post_card(_draft(key), GATE, lang)
        assert err.value.rule == "rules_unchecked"
        assert channels.get(key).names[lang] in err.value.words(lang)
        assert say(channels.unchecked_said(channels.get(key)), lang) in err.value.words(lang)
    assert not re.search("[가-힣]", err.value.words("en").replace(channels.get(key).names["en"], ""))


def test_the_refusal_comes_before_every_other_check():
    bad = post_card("reddit_opensource", where_url="https://example.com/x", what=what(facts=["star를 눌러 주세요"]))
    with pytest.raises(owner_tasks.CardRefused) as err:
        owner_tasks.build_post_card(bad, GATE, "en")
    assert err.value.rule == "rules_unchecked"


def test_a_card_for_a_checked_place_still_carries_its_rules_and_basis():
    card, _ = owner_tasks.build_post_card(_draft("lobsters"), GATE, "en")
    assert (card["rules_basis"], card["rules_source"]) == ("verified", "https://lobste.rs/about")
    assert "own_place" not in card


# ------------------------------------------------------------------ what an agent asks for

def test_an_agents_request_for_an_unchecked_place_is_refused_in_the_owners_language_and_names_the_place(s, project):
    for lang, place in (("en", "Reddit r/opensource"), ("ko", "Reddit r/opensource")):
        with owner_speaks(lang):
            req = requests.create(s, draft(post_card=_draft("reddit_opensource")), from_agent="marketing",
                                  project_id=project.id, origin=by_project_manager(s, project.id))
            s.commit()
        note = req.response["note"]
        assert req.status == "DENIED_BY_POLICY" and place in note and req.response["refused"] == "post_card"
        assert msg("post.rules.not_read", lang) in note
        assert bool(re.search("[가-힣]", note.replace(place, ""))) is (lang == "ko")
        fired = [e for e in session_events(req.id) if e.verb == "post_card_refused"]
        assert [e.data["rule"] for e in fired] == ["rules_unchecked"]
        assert "post_card" not in req.action_params


def session_events(request_id):
    with session_scope() as x:
        found = list(x.scalars(select(ActivityEvent).where(ActivityEvent.entity == "request",
                                                           ActivityEvent.entity_id == request_id)))
        x.expunge_all()
        return found


def test_the_agents_are_told_which_places_no_card_can_be_made_for():
    from justshipit.agents.executor import system_prompt
    text_ = system_prompt("marketing")
    usable, closed = text_.split("규칙을 읽지 못한 채널이다.")
    assert "`lobsters`: Lobsters (outline)" in usable and "`linkedin`: LinkedIn (outline)" in usable
    assert "`reddit_opensource`" in closed and "`discord_global`" in closed and "`geeknews`" not in closed
    assert "규칙을 읽지 못한 채널이면 점검표에" not in text_


# ------------------------------------------------------------------ the owner's own place

OWN = "https://my-shop.example/"


def _other(title="랜딩 공개", **kw) -> PostCardDraft:
    return post_card("other", where_url=kw.pop("where_url", None),
                     what=what(title=title, body="랜딩 초안", facts=[], expected_questions=[]), **kw)


def test_a_card_for_the_owners_own_site_is_written_and_says_it_is_the_owners_own():
    card, _ = owner_tasks.build_post_card(_other(where_url=OWN + "launch"), GATE, "en", own_places=(OWN,))
    assert card["channel"] == "other" and card["own_place"] is True and card["what"]["title"] == "랜딩 공개"


def test_a_landing_page_is_the_owners_own_place_whatever_its_address():
    card, _ = owner_tasks.build_post_card(_other(where_url="https://someone.vercel.app/"), GATE, "en", own_page=True)
    assert card["own_place"] is True


def test_other_with_no_address_that_names_no_outside_place_is_the_owners_own_and_sends_no_outside_rules():
    card, _ = owner_tasks.build_post_card(_other(), GATE, "en")
    assert card["channel"] == "other" and card["own_place"] is True and card["rules_summary"] == ""


def test_other_cannot_carry_an_outside_address_around_the_rules_check():
    with pytest.raises(owner_tasks.CardRefused) as err:
        owner_tasks.build_post_card(_other(where_url="https://forum.example/new"), GATE, "en", own_places=(OWN,))
    assert err.value.rule == "rules_unchecked" and "saved places" in err.value.words("en")
    with pytest.raises(owner_tasks.CardRefused) as err:
        owner_tasks.build_post_card(_other(where_url="https://forum.example/new"), GATE, "en", own_page=False)
    assert err.value.rule == "rules_unchecked"


def test_an_agents_landing_page_and_an_own_site_post_still_reach_the_owner(s, project):
    tracking.set_doors(s, project.business_id, [{"kind": "site", "url": OWN}])
    s.commit()
    page = requests.create(s, draft(action_kind="external.landing_publish", post_card=_other(where_url="https://x.vercel.app/")),
                           from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    own = requests.create(s, draft(title="가게 소식", post_card=_other("가게 소식 글", where_url=OWN + "news")), from_agent="marketing",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    outside = requests.create(s, draft(title="포럼 글", post_card=_other("포럼 글", where_url="https://forum.example/new")),
                              from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    assert page.status == own.status == "PENDING" and outside.status == "DENIED_BY_POLICY"
    assert own.action_params["post_card"]["own_place"] is True


# ------------------------------------------------------------------ a stored card, read now

def _stored(key, **kw):
    return {"channel": key, "channel_name": "x", "rules_summary": "old text", "mode": "outline", "where_url": None,
            "what": {"facts": ["a"]}, "extra_key": {"kept": 1}, **kw}


def test_a_stored_card_for_a_place_that_is_unchecked_says_so_and_an_open_one_says_it_was_written_before():
    for lang in ("en", "ko"):
        read = owner_tasks.with_named_channel(_stored("reddit_opensource"), lang)
        assert read["rules_state"] == "unchecked" and read["rules_checked_on"] is None
        assert read["rules_unchecked_text"] == msg("post.rules.not_read", lang)
        assert read["rules_written_before"] == msg("post.rules.written_before", lang)
        assert read["rules_summary"] == channels.get("reddit_opensource").rules[lang]
        assert read["extra_key"] == {"kept": 1} and read["what"] == {"facts": ["a"]}, "unknown keys pass through"
    done = owner_tasks.with_named_channel(_stored("reddit_opensource"), "en", open_card=False)
    assert done["rules_state"] == "unchecked" and "rules_written_before" not in done


def test_a_stored_card_for_a_checked_place_says_when_its_rules_were_read():
    read = owner_tasks.with_named_channel(_stored("hacker_news"), "en")
    assert (read["rules_state"], read["rules_checked_on"], read["rules_unchecked_text"]) == ("checked", "2026-11-16", None)
    assert "rules_written_before" not in read and read["rules_source"] == channels.get("hacker_news").source_url


def test_a_stored_card_on_the_catch_all_or_the_owners_own_place_is_not_written_before_anything():
    other = owner_tasks.with_named_channel(_stored("other", where_url="https://forum.example/new"), "en")
    assert other["rules_state"] == "unchecked" and "rules_written_before" not in other
    bare = owner_tasks.with_named_channel(_stored("other"), "en")
    assert bare["rules_state"] == "own" and bare["rules_summary"] == "", "stored before the marker: no address, no place named"
    own = owner_tasks.with_named_channel(_stored("other", own_place=True), "en")
    assert (own["rules_state"], own["rules_unchecked_text"]) == ("own", None) and own["own_place"] is True


def test_a_card_filed_under_the_catch_all_that_names_a_place_reads_as_that_place():
    read = owner_tasks.with_named_channel(_stored("reddit"), "en", "Reddit r/opensource 게시")
    assert read["channel"] == "reddit_opensource" and read["rules_state"] == "unchecked" and "rules_written_before" in read
    lobsters = owner_tasks.with_named_channel(_stored("other", where_url="https://lobste.rs/s/abc"), "en")
    assert lobsters["channel"] == "lobsters" and lobsters["rules_state"] == "checked"


def test_the_to_do_a_card_belongs_to_reads_open_or_done(s, project):
    task = as_stored_before_the_rules_check(open_task(s, project), "reddit_opensource")
    open_view = owner_tasks.view(task, "en")["card"]
    assert open_view["rules_state"] == "unchecked" and open_view["rules_written_before"]
    owner_tasks.done(s, task.id, link="https://www.reddit.com/r/opensource/comments/abc/post/")
    s.commit()
    done_view = owner_tasks.view(s.get(OwnerTask, task.id), "en")["card"]
    assert done_view["rules_state"] == "unchecked" and "rules_written_before" not in done_view


# ------------------------------------------------------------------ the owner's list of places

def test_the_list_of_places_for_a_business_is_read_only_and_puts_the_checked_ones_first(s, business, api):
    got = api.get(f"/api/businesses/{business.id}/channels")
    assert got.status_code == 200
    body = got.json()
    rows = body["places"]
    assert {"key", "name", "region", "state", "checked_on", "source_url", "background_url", "rules", "unchecked_why",
            "unchecked_text", "does", "fits", "repost_days", "requires_unmet", "dropped"} <= set(rows[0])
    states = [r["state"] for r in rows]
    shown = [r["state"] for r in rows if not r["generic"]]
    assert states == sorted(states, key=lambda x: x != "checked") and body["counts"] == {
        "checked": shown.count("checked"), "unchecked": shown.count("unchecked")}
    by_key = {r["key"]: r for r in rows}
    assert {k for k, r in by_key.items() if r["state"] == "checked"} == CHECKED
    hn, rd = by_key["hacker_news"], by_key["reddit_opensource"]
    assert (hn["state"], hn["checked_on"], hn["does"], hn["unchecked_why"], hn["unchecked_text"]) == (
        "checked", "2026-11-16", "points", None, None)
    assert hn["source_url"] == channels.get("hacker_news").source_url and hn["rules"]
    assert (rd["state"], rd["does"], rd["unchecked_why"], rd["checked_on"]) == ("unchecked", "nothing", "not_read", None)
    assert rd["unchecked_text"] and rd["source_url"] is None
    assert by_key["hashnode"]["does"] == "draft" and by_key["discord_global"]["background_url"]
    assert by_key["show_hn"]["requires_unmet"] and by_key["show_hn"]["requires_unmet"][0]["item"]
    assert api.post(f"/api/businesses/{business.id}/channels").status_code == 405


def test_the_list_speaks_the_owners_language_and_a_missing_business_is_refused(s, business, api):
    with owner_speaks("ko"):
        ko = {r["key"]: r for r in api.get(f"/api/businesses/{business.id}/channels").json()["places"]}
    assert ko["reddit_opensource"]["unchecked_text"] == msg("post.rules.not_read", "ko")
    assert ko["hacker_news"]["name"] == channels.get("hacker_news").names["ko"]
    assert api.get("/api/businesses/999999/channels").status_code == 409


def test_a_place_the_retro_dropped_and_one_that_fits_are_marked(s, business, api):
    with session_scope() as x:
        promotion.drop_channel(x, business.id, "hacker_news", None, "첫 가치 0명")
    by_key = {r["key"]: r for r in api.get(f"/api/businesses/{business.id}/channels").json()["places"]}
    assert by_key["hacker_news"]["dropped"] is True and by_key["hacker_news"]["fits"] is True
    assert by_key["linkedin"]["fits"] is False, "LinkedIn reaches professionals and companies, not developers"


# ------------------------------------------------------------------ review fixes

def _refused(title, where_url=None, **kw) -> owner_tasks.CardRefused:
    with pytest.raises(owner_tasks.CardRefused) as err:
        owner_tasks.build_post_card(_other(title, where_url=where_url), GATE, "en", **kw)
    return err.value


@pytest.mark.parametrize("title,place", [
    ("Launch post for Reddit", "Reddit"), ("Twitter thread for the launch", "X (Twitter)"),
    ("Post for the Discord server", "Discord"), ("레딧에 올릴 소개 글", "Reddit"), ("Post this on r/selfhosted", "r/selfhosted"),
    ("서브레딧 소개 글", "Reddit"), ("트위터 스레드", "X (Twitter)"), ("트윗 한 줄", "X (Twitter)"), ("디스코드 공지", "Discord"),
    ("a tweet about the release", "X (Twitter)"), ("Instagram reel script", "Instagram")])
def test_other_with_no_address_that_names_a_place_whose_rules_were_not_read_is_refused(title, place):
    refused = _refused(title)
    assert refused.rule == "rules_unchecked" and place in refused.words("en")


@pytest.mark.parametrize("title,key", [("Show HN: my CLI", "show_hn"), ("dev.to article: how I built it", "devto"),
                                       ("Hashnode post about the build", "hashnode"), ("Lobsters story", "lobsters")])
def test_other_that_names_a_checked_place_is_told_to_file_under_that_place(title, key):
    refused = _refused(title)
    assert refused.rule == "wrong_channel" and f"`{key}`" in refused.words("en")
    assert channels.get(key).names["en"] in refused.words("en")
    assert f"`{key}`" in refused.words("ko") and not re.search("[가-힣]", refused.words("en"))


def test_the_owners_own_pages_are_not_refused_for_a_word_in_their_title():
    for title in ("당근 주스 신메뉴 랜딩 페이지", "오픈채팅으로 예약하세요", "Join our Facebook group"):
        card, _ = owner_tasks.build_post_card(_other(title), GATE, "en", own_page=True)
        assert card["own_place"] is True
        card, _ = owner_tasks.build_post_card(_other(title, where_url=OWN + "x"), GATE, "en", own_places=(OWN,))
        assert card["own_place"] is True


def test_a_card_not_the_owners_own_with_a_place_word_in_its_title_is_refused_and_says_to_reword():
    refused = _refused("당근 주스 신메뉴 안내")
    assert refused.rule == "rules_unchecked" and "당근" in refused.words("ko")


@pytest.mark.parametrize("saved,address,own", [
    ("https://bsky.app/profile/me", "https://bsky.app/profile/me", True),
    ("https://bsky.app/profile/me", "https://bsky.app/profile/me/post/1?x=1", True),
    ("https://bsky.app/profile/me", "https://bsky.app/profile/someone", False),
    ("https://bsky.app/profile/me", "https://bsky.app/profile/mevil", False),
    ("https://bsky.app/profile/me", "https://bsky.app/profile/me/../other", False),
    ("https://bsky.app/profile/me", "https://bsky.app/profile/me/%2e%2e/other", False),
    ("https://bsky.app/profile/me", "https://bsky.app/", False),
    ("https://www.indiehackers.com/", "https://indiehackers.com/post/x", True),
    ("https://indiehackers.com", "https://www.indiehackers.com/post/x", True),
    ("https://my-shop.example/", "https://blog.my-shop.example/a", False),
    ("https://my-shop.example/", "https://my-shop.example.evil.com/a", False),
    ("https://my-shop.example/", "https://MY-SHOP.example/a", True)])
def test_an_address_is_the_owners_own_when_it_is_under_a_saved_place_not_anywhere_on_its_host(saved, address, own):
    assert channels.under_places(address, (saved,)) is own


def test_a_saved_page_on_a_shared_site_does_not_make_the_whole_site_the_owners():
    refused = _refused("글", where_url="https://bsky.app/profile/someone", own_places=("https://bsky.app/profile/me",))
    assert refused.rule == "rules_unchecked" and "saved places" in refused.words("en")
    assert "repository" not in refused.words("en") and "저장소" not in refused.words("ko")


@pytest.mark.parametrize("address,ok", [
    (None, True), (OWN + "p", True), ("https://x.pages.dev/", True), ("https://a.b.vercel.app/", True),
    ("https://x.netlify.app/", True), ("https://me.github.io/site", True), ("https://x.workers.dev/", True),
    ("https://pages.dev/", False), ("https://evilpages.dev/", False), ("https://pages.dev.evil.com/", False),
    ("https://www.indiehackers.com/post/x", False), ("https://forum.example/new", False)])
def test_a_landing_page_request_is_the_owners_own_only_at_no_address_a_saved_place_or_a_static_host(address, ok):
    card = _other("랜딩 공개", where_url=address)
    if ok:
        assert owner_tasks.build_post_card(card, GATE, "en", own_places=(OWN,), own_page=True)[0]["own_place"] is True
        return
    with pytest.raises(owner_tasks.CardRefused) as err:
        owner_tasks.build_post_card(card, GATE, "en", own_places=(OWN,), own_page=True)
    assert err.value.rule == "rules_unchecked" and "save" in err.value.words("en") and "저장" in err.value.words("ko")


def test_a_request_without_a_card_that_names_an_unchecked_place_in_its_title_is_refused():
    other = draft(action_kind="external.other", title="Post this on r/selfhosted", post_card=None)
    with pytest.raises(owner_tasks.CardRefused) as err:
        owner_tasks.screen(other, GATE, "en")
    assert err.value.rule == "rules_unchecked"
    plain = draft(action_kind="external.other", title="Sign the printing contract", post_card=None)
    assert owner_tasks.screen(plain, GATE, "en") == ({}, [])
    checked = draft(action_kind="external.other", title="Look at Hacker News", post_card=None)
    assert owner_tasks.screen(checked, GATE, "en") == ({}, []), "only a place whose rules were not read is refused here"


def test_an_open_card_for_a_place_that_now_wants_its_own_words_says_the_company_wrote_it_before_that_was_known():
    stored = _stored("devto", mode="draft", what={"title": "T", "body": "B"})
    for lang in ("en", "ko"):
        read = owner_tasks.with_named_channel(stored, lang)
        assert read["rules_state"] == "checked"
        assert read["rules_written_before"] == msg("post.rules.written_before_self", lang)
        assert read["rules_written_before"] != msg("post.rules.written_before", lang)
    closed = owner_tasks.with_named_channel(stored, "en", open_card=False)
    assert "rules_written_before" not in closed
    outline = owner_tasks.with_named_channel(_stored("devto"), "en")
    assert "rules_written_before" not in outline


def test_an_own_card_carries_no_outside_rules_summary_and_a_landing_title_does_not_turn_it_into_a_place():
    card, _ = owner_tasks.build_post_card(_other("당근 주스 랜딩"), GATE, "ko", own_page=True)
    assert card["rules_summary"] == "" and card["rules_source"] is None
    read = owner_tasks.with_named_channel({**card, "mode": "draft"}, "ko", "당근 주스 랜딩")
    assert read["channel"] == "other" and read["rules_state"] == "own" and read["rules_summary"] == ""


def test_the_empty_week_names_the_places_left_out_for_their_rules(s):
    shop = make_business(s, name="동네 가게", buyer=("consumers",), reach=("local",))
    week(shop)
    event = latest_week_event(s, shop)
    assert channels.get("daangn_community").names["ko"] in event.summary


def test_the_counts_cover_only_the_places_the_list_shows(s, business, api):
    body = api.get(f"/api/businesses/{business.id}/channels").json()
    shown = [r for r in body["places"] if not r["generic"]]
    assert body["counts"] == {"checked": sum(r["state"] == "checked" for r in shown),
                              "unchecked": sum(r["state"] == "unchecked" for r in shown)}
    assert sum(body["counts"].values()) < len(body["places"])


def test_the_registry_says_no_more_than_the_pages_it_cites():
    rules = {k: channels.get(k).rules for k in ("product_hunt", "lobsters", "hashnode", "discord_ko", "devto")}
    assert "personal accounts" not in rules["product_hunt"]["en"] and "개인 계정" not in rules["product_hunt"]["ko"]
    assert all(a in rules["product_hunt"]["en"] for a in ("help.producthunt.com", "/articles/"))
    assert "rule of thumb" in rules["lobsters"]["en"] and "경험칙" in rules["lobsters"]["ko"]
    assert "Terms and Code of Conduct" not in rules["hashnode"]["en"] and "이용약관과 행동 강령" not in rules["hashnode"]["ko"]
    assert "separate channel for promotion" not in rules["discord_ko"]["en"] and "홍보용 채널" not in rules["discord_ko"]["ko"]
    assert "program" in rules["devto"]["en"] and "프로그램" in rules["devto"]["ko"]


def test_the_request_the_console_reads_names_its_business(s, project):
    req = requests.create(s, draft(post_card=_draft("lobsters")), from_agent="marketing", project_id=project.id,
                          origin=by_project_manager(s, project.id))
    assert requests.read_model(s, req, "en", None)["business_id"] == project.business_id
