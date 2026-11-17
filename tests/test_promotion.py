"""The weekly promotion and the channel rules it obeys: the repost interval, new material, the launch gate, one
exclusive channel a day, the weekly cap, global before Korean, and nothing made when there is nothing new to say."""
from datetime import date, timedelta

import pytest
from justshipit.contracts.post_card import ExpectedQuestion, PostCardDraft, PostWhat, PostWhen
from justshipit.contracts.promotion import PromotionOut
from justshipit.db.models import ActivityEvent, OwnerTask
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import channels, launch_gate, owner_tasks, promotion
from justshipit.services.control import ControlError
from justshipit.services.metrics import Reply
from justshipit.services.usage import MustWait
from bet_loop_kit import NOW, FakeHttp, github, install_people, make_business, reset_world, set_bets
from i18n_kit import HANGUL
from sqlalchemy import select, text
from test_korean_style import smells_in

GLOBAL_FIRST = [k for k, c in channels.registry().items() if c.region == "global" and k != "other"]


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


class ScriptedMarketing:
    """The marketing agent: writes a valid card for every channel the prompt names, and records the prompt."""

    def __init__(self):
        self.prompts: list[str] = []
        self.calls = 0

    def __call__(self, agent, purpose, prompt, output_model, *, project_id, system_override=None, **kw):
        assert (agent, purpose, project_id, output_model) == ("marketing", "scheduled", None, PromotionOut)
        assert system_override and "post card" in system_override
        self.calls += 1
        self.prompts.append(prompt)
        keys = [line.split()[1] for line in prompt.splitlines() if line.startswith("### ")]
        return PromotionOut(cards=[card(k) for k in keys], notes=[])


def card(key: str) -> PostCardDraft:
    channel = channels.get(key)
    domains = channel.domains
    what = (PostWhat(title=None, body=None, facts=["새 릴리스 0.13.7이 나왔다", "설치는 한 줄이다"],
                     expected_questions=[ExpectedQuestion(q="기존 도구와 무엇이 다른가요?", talking_points=["속도"])],
                     checklist=["가입 없이 써 볼 수 있는지 확인"])
            if channel.mode == "outline" else
            PostWhat(title=f"{key} 제목", body="본문 초안", facts=[], expected_questions=[], checklist=["링크 확인"]))
    return PostCardDraft(channel=key, where_url=f"https://{domains[0]}/submit" if domains else None,
                         when=PostWhen(window="화~목 오전", reason="추정"), why="도전 BET-1의 가설을 확인한다", what=what)


@pytest.fixture
def marketing(monkeypatch):
    m = ScriptedMarketing()
    monkeypatch.setattr(promotion, "run_agent", m)
    return m


def week(business, **kw):
    return promotion.run_week(business.id, lang=kw.pop("lang", "ko"), fetch=kw.pop("fetch", github()),
                              now=kw.pop("now", NOW))


def tasks(s, business):
    s.expire_all()
    return list(s.scalars(select(OwnerTask).where(OwnerTask.business_id == business.id, OwnerTask.kind == "post")
                          .order_by(OwnerTask.id)))


def _post_link(channel) -> str | None:
    """A link to a post itself: the shape the channel's post_link asks for, or any page on its domain when it has none."""
    if not channel.domains:
        return None
    host = channel.domains[0]
    shapes = {"news.ycombinator.com": "https://news.ycombinator.com/item?id=4100", "reddit.com": "https://www.reddit.com/r/test/comments/abc123/a_post/",
              "x.com": "https://x.com/owner/status/4100"}
    return next((url for domain, url in shapes.items() if host.endswith(domain)), f"https://{host}/x")


def posted(s, business, channel, days_ago, keys=("release:old",)):
    """A post the owner made `days_ago` days before NOW."""
    task = owner_tasks.create_post_task(s, business.id, {"channel": channel, "week": "2026-W01", "material_keys": list(keys)},
                                        "이전 게시")
    owner_tasks.done(s, task.id, link=_post_link(channels.get(channel)))
    s.execute(text("UPDATE owner_tasks SET done_at = :t, created_at = :t WHERE id = :i"),
              {"t": NOW - timedelta(days=days_ago), "i": task.id})
    s.commit()
    return task


# ---------------------------------------------------------------- the registry

def test_every_channel_says_its_repost_rule_region_gate_and_who_writes(s):
    for key, c in channels.registry().items():
        assert c.repost.min_interval_days > 0 and c.repost.needs_new_material, key
        assert (c.repost.source_url is None) == (c.repost.basis == "none"), key
        assert c.region in ("global", "en", "ko") and c.self_written == (c.mode == "outline"), key
        assert all(launch_gate.gate_known(i) for i in c.requires), key
    launch_gate.checked_registry()


def test_hacker_news_and_show_hn_carry_the_verified_repost_text_and_show_hn_is_gated():
    hn, show = channels.get("hacker_news"), channels.get("show_hn")
    assert hn.repost.basis == show.repost.basis == "verified" and hn.self_written and show.self_written
    for lang in ("en", "ko"):
        assert "bury reposts as duplicates" in hn.rules[lang] and "A major overhaul is probably ok" in show.rules[lang]
    assert show.repost.min_interval_days >= 365 and hn.requires == ()
    assert set(show.requires) == {"demo", "outside_install_3", "readme_checked", "first_value_20"}
    assert set(channels.get("product_hunt").requires) == set(show.requires)


def test_geeknews_rules_come_from_its_guidelines_page_and_it_is_korean_and_gated():
    gn = channels.get("geeknews")
    assert gn.basis == gn.repost.basis == "verified" and gn.source_url == "https://news.hada.io/guidelines"
    assert gn.region == "ko" and gn.requires and gn.self_written
    for fragment in ("Show", "다시 올릴 수 없고", "upvote", "downvote", "찍어 낸 글", "90일"):
        assert fragment in gn.rules["ko"]
    for fragment in ("Show", "can't be posted again", "upvote", "Mass-produced", "90 days"):
        assert fragment in gn.rules["en"]


def test_korean_communities_have_no_rule_text_and_are_supplementary():
    for key in ("disquiet", "okky", "careerly", "facebook_dev_groups", "discord_ko"):
        c = channels.get(key)
        assert (c.region, c.basis, c.source_url, c.repost.basis, c.requires) == ("ko", "none", None, "none", ()), key


def test_global_channels_include_subreddit_units_dev_blogs_x_and_discord():
    for key, unit in (("reddit_opensource", "r/opensource"), ("reddit_selfhosted", "r/selfhosted"),
                      ("reddit_sideproject", "r/SideProject"), ("reddit_claudeai", "r/ClaudeAI")):
        assert (channels.get(key).unit, channels.get(key).region, channels.get(key).same_day_exclusive) == (unit, "global", True)
    assert all(channels.get(k).region == "global" for k in ("devto", "hashnode", "x", "discord_global"))


def test_the_weekly_routine_skips_the_subreddits_whose_rules_were_not_read_and_never_picks_the_generic_entries(s, business):
    material = [promotion.Material("release:new", "release", "v2", "", None, NOW)]
    with session_scope() as x:
        picked, skipped = promotion.pick(x, business.id, material, 99, NOW, lang="ko")

    keys = [c.channel.key for c in picked]
    for unread in ("reddit_opensource", "reddit_sideproject", "reddit_selfhosted", "reddit_claudeai"):
        assert unread not in keys and skipped[unread] == msg("promotion.skip.rules_unchecked", "ko"), unread
    assert "reddit" not in keys and "other" not in keys and "reddit" not in skipped and "other" not in skipped
    assert all(channels.writable(c.channel) for c in picked)


def test_the_github_row_no_longer_says_github_forbids_asking():
    rules = channels.get("github_release").rules
    assert "자동으로 늘리는 일" in rules["ko"] and "찾지 못했지만" in rules["ko"]
    assert "automatically" in rules["en"] and "found no sentence banning" in rules["en"]


# ---------------------------------------------------------------- the launch gate

def test_a_gated_channel_gets_no_card_until_every_item_is_met(s, business):
    people = install_people(first_value=19)
    show = channels.get("show_hn")
    for item in ("demo", "outside_install_3"):
        with session_scope() as x:
            promotion.confirm_gate_item(x, business.id, item, "확인", lang="ko")

    with session_scope() as x:
        assert launch_gate.unmet(x, business.id, show) == ["readme_checked", "first_value_20"]
    with session_scope() as x:
        promotion.confirm_gate_item(x, business.id, "readme_checked", lang="ko")
    with session_scope() as x:
        assert launch_gate.unmet(x, business.id, show) == ["first_value_20"], "nineteen people is not twenty"
    people.counts["first_value"] = 20
    with session_scope() as x:
        assert launch_gate.unmet(x, business.id, show) == []


def test_the_company_cannot_confirm_a_gate_item_that_follows_the_people_list(s, business):
    with pytest.raises(ControlError, match="오너님이 확인하는 항목이 아니에요"), session_scope() as x:
        promotion.confirm_gate_item(x, business.id, "first_value_20", lang="ko")
    with pytest.raises(ControlError, match="isn't yours to confirm"), session_scope() as x:
        promotion.confirm_gate_item(x, business.id, "made_up", lang="en")


def test_a_withdrawn_item_closes_the_gate_again(s, business):
    for item in launch_gate.OWNER_GATE_ITEMS:
        with session_scope() as x:
            promotion.confirm_gate_item(x, business.id, item, lang="ko")
    install_people(first_value=25)
    with session_scope() as x:
        assert launch_gate.unmet(x, business.id, channels.get("show_hn")) == []
        promotion.withdraw_gate_item(x, business.id, "demo")
    with session_scope() as x:
        assert launch_gate.unmet(x, business.id, channels.get("show_hn")) == ["demo"]


def test_a_gated_channel_is_skipped_with_the_missing_items_named(s, business):
    material = [promotion.Material("release:1", "release", "v1", "", None, NOW)]
    with session_scope() as x:
        picked, skipped = promotion.pick(x, business.id, material, 99, NOW, lang="ko")
    keys = [c.channel.key for c in picked]
    assert "show_hn" not in keys and "product_hunt" not in keys and "geeknews" not in keys
    assert "녹화한 데모" in skipped["show_hn"] and "도움 됨 단계까지 온 사람 20명" in skipped["show_hn"]


def test_once_the_gate_is_met_show_hn_is_picked(s, business):
    install_people(first_value=20)
    for item in launch_gate.OWNER_GATE_ITEMS:
        with session_scope() as x:
            promotion.confirm_gate_item(x, business.id, item, lang="ko")
    material = [promotion.Material("release:1", "release", "v1", "", None, NOW)]
    with session_scope() as x:
        picked, _ = promotion.pick(x, business.id, material, 99, NOW, lang="ko")
    assert "show_hn" in [c.channel.key for c in picked]


# ---------------------------------------------------------------- repost interval and new material

def test_a_channel_posted_to_recently_waits_out_its_interval_even_with_new_material(s, business):
    posted(s, business, "hacker_news", days_ago=10)
    material = [promotion.Material("release:new", "release", "v2", "", None, NOW)]
    with session_scope() as x:
        picked, skipped = promotion.pick(x, business.id, material, 99, NOW, lang="ko")
    assert "hacker_news" not in [c.channel.key for c in picked] and "30일이 안 지났어요" in skipped["hacker_news"]

    posted(s, business, "lobsters", days_ago=10)
    with session_scope() as x:
        picked, skipped = promotion.pick(x, business.id, material, 99, NOW, lang="ko")
    assert "lobsters" not in [c.channel.key for c in picked] and "30일" in skipped["lobsters"]
    posted(s, business, "github_release", days_ago=40)
    with session_scope() as x:
        picked, _ = promotion.pick(x, business.id, material, 99, NOW, lang="ko")
    assert "github_release" in [c.channel.key for c in picked]


def test_after_the_interval_the_same_material_is_not_posted_again_but_new_material_is(s, business):
    posted(s, business, "hacker_news", days_ago=60, keys=("release:v1",))
    with session_scope() as x:
        picked, skipped = promotion.pick(x, business.id, [promotion.Material("release:v1", "release", "v1", "", None, NOW)],
                                         99, NOW, lang="ko")
    assert "hacker_news" not in [c.channel.key for c in picked] and "새 소식이 없어요" in skipped["hacker_news"]

    fresh = [promotion.Material("release:v1", "release", "v1", "", None, NOW), promotion.Material("release:v2", "release", "v2", "", None, NOW)]
    with session_scope() as x:
        picked, _ = promotion.pick(x, business.id, fresh, 99, NOW, lang="ko")
    cand = next(c for c in picked if c.channel.key == "hacker_news")
    assert [m.key for m in cand.fresh] == ["release:v2"], "only what no earlier post there used"


def test_a_card_still_waiting_for_the_owner_blocks_another_for_the_same_channel(s, business, marketing):
    week(business)
    with session_scope() as x:
        _, skipped = promotion.pick(x, business.id, [promotion.Material("release:z", "release", "z", "", None, NOW)], 99, NOW,
                                    lang="ko")
    assert "이미 내 할 일에 있어요" in skipped["hacker_news"]


def test_a_channel_the_retro_dropped_is_never_picked(s, business):
    with session_scope() as x:
        promotion.drop_channel(x, business.id, "hacker_news", None, "첫 가치 0명")
    with session_scope() as x:
        picked, skipped = promotion.pick(x, business.id, [promotion.Material("release:z", "release", "z", "", None, NOW)], 99, NOW,
                                         lang="ko")
    assert "hacker_news" not in [c.channel.key for c in picked] and "그만 쓰기로 한" in skipped["hacker_news"]


# ---------------------------------------------------------------- the weekly job

def test_with_new_material_the_week_makes_cards_for_global_channels_first_up_to_the_cap(s, business, marketing):
    report = week(business)

    made = tasks(s, business)
    assert [t.card["channel"] for t in made] == ["hacker_news", "devto"], "the cap is 2 and global channels go first"
    assert report.tasks == [t.id for t in made] and marketing.calls == 1
    assert all(t.status == "OPEN" and t.kind == "post" and t.request_id is None for t in made)
    hn = made[0].card
    assert hn["mode"] == "outline" and "body" not in hn["what"] and hn["what"]["facts"], "HN gets facts, never a sentence"
    assert hn["week"] == "2026-W45" and "release:0.13.7" in hn["material_keys"]
    assert made[0].due_at.date() != made[1].due_at.date(), "two exclusive channels never share a day"
    assert made[0].title.startswith("Hacker News") and "새 릴리스" in made[0].title
    assert "0.13.7" in marketing.prompts[0] and "hacker_news" in marketing.prompts[0]


def _open_the_launch_gate(business):
    install_people(first_value=20)
    for item in launch_gate.OWNER_GATE_ITEMS:
        with session_scope() as x:
            promotion.confirm_gate_item(x, business.id, item, lang="ko")


def test_korean_channels_come_after_every_global_one_and_only_a_checked_one_is_picked(s, business):
    install_people()
    material = [promotion.Material("release:z", "release", "z", "", None, NOW)]

    with session_scope() as x:
        picked, skipped = promotion.pick(x, business.id, material, 99, NOW, lang="ko")
    assert "geeknews" not in [c.channel.key for c in picked] and "geeknews" in skipped
    assert {"okky", "disquiet", "careerly"} <= set(skipped) and "ko" not in [c.channel.region for c in picked]

    _open_the_launch_gate(business)
    with session_scope() as x:
        picked, skipped = promotion.pick(x, business.id, material, 99, NOW, lang="ko")
    regions = [c.channel.region for c in picked]
    assert regions == sorted(regions, key=lambda r: r != "global") and regions[-1] == "ko"
    assert [c.channel.key for c in picked][-1] == "geeknews"
    assert all(skipped[k] == msg("promotion.skip.rules_unchecked", "ko") for k in ("okky", "disquiet", "careerly"))


def test_when_the_cap_is_tight_no_korean_channel_takes_a_place_a_global_one_could_have(s, business):
    _open_the_launch_gate(business)
    material = [promotion.Material("release:z", "release", "z", "", None, NOW)]
    with session_scope() as x:
        picked, skipped = promotion.pick(x, business.id, material, 3, NOW, lang="ko")
    assert [c.channel.region for c in picked] == ["global"] * 3 and "상한" in skipped["geeknews"]


def test_the_weekly_cap_counts_cards_already_made_this_week(s, business, marketing):
    set_bets(weekly_post_cap=2)
    week(business)
    again = week(business)

    assert len(tasks(s, business)) == 2 and again.tasks == [] and "상한" in again.note
    assert marketing.calls == 1, "a capped week asks the model for nothing"


def test_a_cap_of_one_makes_one_card_and_a_cap_of_zero_none(s, business, marketing):
    set_bets(weekly_post_cap=1)
    week(business)
    assert len(tasks(s, business)) == 1
    with session_scope() as x:
        x.execute(text("DELETE FROM owner_tasks WHERE business_id = :b"), {"b": business.id})
    set_bets(weekly_post_cap=0)
    week(business)
    assert tasks(s, business) == []


def test_next_week_the_cap_is_fresh_but_the_channels_with_open_cards_are_not_asked_again(s, business, marketing):
    set_bets(weekly_post_cap=2)
    week(business)
    week(business, now=NOW + timedelta(days=7))

    channels_made = [t.card["channel"] for t in tasks(s, business)]
    assert channels_made.count("hacker_news") == 1 and channels_made.count("devto") == 1
    assert len(channels_made) == 4 and "2026-W46" in [t.card["week"] for t in tasks(s, business)]


def test_with_no_material_nothing_is_made_the_model_is_not_called_and_the_briefing_is_told(s, business, marketing):
    nothing = FakeHttp({"https://api.github.com/repos/owner/repo/releases": Reply(200, [], {})})

    report = week(business, fetch=nothing)

    assert tasks(s, business) == [] and marketing.calls == 0
    assert "새 소식이 없어" in report.note
    event = s.scalars(select(ActivityEvent).where(ActivityEvent.verb == "promotion_week", ActivityEvent.business_id == business.id)
                      .order_by(ActivityEvent.id.desc())).first()
    assert event and "새 소식" in event.summary


def test_a_finished_bet_and_a_milestone_are_material_too(s, business, marketing):
    from justshipit.db.models import Bet
    from bet_loop_kit import make_bet
    install_people(first_value=12)
    bet = make_bet(s, business, status="DONE")
    bet.finished_at = NOW - timedelta(days=3)
    bet.retro = {"mode": "people_quotes", "did": "서브레딧 두 곳에 올렸다", "users_said": [{"person_ref": "PERSON-1", "quote": "설치가 쉬웠다"}]}
    s.commit()
    quiet = FakeHttp({})

    week(business, fetch=quiet)

    prompt = marketing.prompts[0]
    assert f"[bet:{bet.id}]" in prompt and "설치가 쉬웠다" in prompt and "[milestone:first_value:10]" in prompt
    assert s.get(Bet, bet.id).status == "DONE"


def test_a_card_that_fails_the_checks_twice_is_skipped_and_the_rest_are_kept(s, business, monkeypatch):
    class Greedy(ScriptedMarketing):
        def __call__(self, *a, **kw):
            out = super().__call__(*a, **kw)
            for c in out.cards:
                if c.channel == out.cards[0].channel:
                    c.what.facts = ["star를 눌러 주세요"]
            return out
    monkeypatch.setattr(promotion, "run_agent", Greedy())
    set_bets(weekly_post_cap=2)

    report = week(business)

    kept = [t.card["channel"] for t in tasks(s, business)]
    assert kept == ["devto"] and "forbidden_ask" in report.skipped["hacker_news"]


class SecondTry(ScriptedMarketing):
    """Fails on the first call the way a real run did, and writes clean cards when asked again."""

    def __init__(self, spoil):
        super().__init__()
        self.spoil = spoil

    def __call__(self, *a, **kw):
        out = super().__call__(*a, **kw)
        return self.spoil(out) if self.calls == 1 else out


def test_a_refused_card_is_written_again_with_the_reason_and_the_second_one_is_kept(s, business, monkeypatch):
    def spoil(out):
        out.cards[0].what.facts = ["star를 눌러 주세요"]
        return out
    m = SecondTry(spoil)
    monkeypatch.setattr(promotion, "run_agent", m)
    set_bets(weekly_post_cap=2)

    report = week(business)

    assert [t.card["channel"] for t in tasks(s, business)] == ["hacker_news", "devto"]
    assert "hacker_news" not in report.skipped and m.calls == 2
    again = m.prompts[1].split("## Write these again")[1]
    assert "### hacker_news" in again and "devto" not in again and "star" in again


def test_a_channel_the_agent_wrote_nothing_for_is_asked_once_more(s, business, monkeypatch):
    def spoil(out):
        out.cards = out.cards[1:]
        return out
    m = SecondTry(spoil)
    monkeypatch.setattr(promotion, "run_agent", m)
    set_bets(weekly_post_cap=2)

    week(business)

    assert [t.card["channel"] for t in tasks(s, business)] == ["hacker_news", "devto"] and m.calls == 2
    assert "You wrote no card" in m.prompts[1]


def test_a_week_whose_every_card_fails_twice_is_still_on_the_record_with_why(s, business, monkeypatch):
    class Long(ScriptedMarketing):
        def __call__(self, *a, **kw):
            out = super().__call__(*a, **kw)
            for c in out.cards:
                c.what.facts = ["가" * 121]
                c.what.title = c.what.body = None
            return out
    m = Long()
    monkeypatch.setattr(promotion, "run_agent", m)
    set_bets(weekly_post_cap=1)

    report = week(business)

    assert tasks(s, business) == [] and m.calls == 2
    assert "outline_too_long" in report.skipped["hacker_news"] and "통과한 글이 없어" in report.note
    event = latest_week_event(s, business)
    assert "통과한 글이 없어" in event.summary and "outline_too_long" in event.data["skipped"]["hacker_news"]


def test_a_week_whose_cards_the_stage_gate_holds_says_so_and_not_that_the_checks_failed(s, business, marketing, monkeypatch):
    monkeypatch.setattr(owner_tasks, "allowed", lambda *a, **kw: False)
    set_bets(weekly_post_cap=2)

    report = week(business)

    assert tasks(s, business) == [] and marketing.calls == 1
    assert "2개를 준비했지만" in report.note and "2개를 준비했지만" in latest_week_event(s, business).summary


def test_a_usage_wait_in_the_marketing_call_makes_nothing(s, business, monkeypatch):
    def wait(*a, **kw):
        raise MustWait(NOW + timedelta(hours=5), "usage_cap_day", "하루 상한")
    monkeypatch.setattr(promotion, "run_agent", wait)

    with pytest.raises(MustWait):
        week(business)

    assert tasks(s, business) == []


def test_the_week_asks_the_people_list_for_public_threads_to_join(s, business, marketing):
    people = install_people()
    week(business)
    assert people.participation == 1


def test_a_business_with_no_approved_brief_is_left_alone(s, marketing):
    from justshipit.services import roadmap
    bare = roadmap.create_business(s, name="지시서 없음", one_liner="x")
    s.commit()
    report = promotion.run_week(bare.id, lang="ko", fetch=github(), now=NOW)
    assert report.tasks == [] and marketing.calls == 0


# ---------------------------------------------------------------- one exclusive channel a day

def ch(key):
    return channels.get(key)


def test_exclusive_channels_get_different_days_and_an_exclusive_day_holds_nothing_else():
    start = date(2026, 11, 4)
    days = promotion.assign_days([ch("hacker_news"), ch("reddit"), ch("devto"), ch("x")], {}, start)
    assert days[0] == start and days[1] == start + timedelta(days=1)
    assert days[2] == days[3] == start + timedelta(days=2), "two channels that are not exclusive may share a day"


def test_a_day_already_planned_for_an_exclusive_post_is_taken():
    start = date(2026, 11, 4)
    assert promotion.assign_days([ch("devto")], {start: {True}}, start) == [start + timedelta(days=1)]
    assert promotion.assign_days([ch("hacker_news")], {start: {False}}, start) == [start + timedelta(days=1)]
    assert promotion.assign_days([ch("devto")], {start: {False}}, start) == [start]


# ---------------------------------------------------------------- what a retro may drop

def test_a_channel_is_not_droppable_on_one_post_or_without_first_value_numbers(s, business):
    posted(s, business, "reddit", days_ago=2)
    with session_scope() as x:
        ok, why = promotion.droppable(x, business.id, "reddit", NOW, lang="ko")
    assert not ok and "3개를 올리거나 4주가" in why

    for _ in range(2):
        posted(s, business, "reddit", days_ago=1)
    with session_scope() as x:
        ok, why = promotion.droppable(x, business.id, "reddit", NOW, lang="ko")
    assert not ok and "도움 됨 숫자가 없어" in why

    from justshipit.services import metrics
    with session_scope() as x:
        metrics.record(x, business.id, "people_first_value", 0.0, NOW, source="people")
    with session_scope() as x:
        ok, why = promotion.droppable(x, business.id, "reddit", NOW, lang="ko")
    assert ok and "3개" in why


def test_four_weeks_after_the_first_post_is_enough_with_first_value_numbers(s, business):
    posted(s, business, "reddit", days_ago=30, keys=())
    from justshipit.services import metrics
    with session_scope() as x:
        metrics.record(x, business.id, "people_first_value", 2.0, NOW, source="people")
    with session_scope() as x:
        assert promotion.droppable(x, business.id, "reddit", NOW, lang="ko")[0]


# ---------------------------------------------------------------- the owner's language

class EnglishMarketing(ScriptedMarketing):
    def __call__(self, *a, **kw):
        out = super().__call__(*a, **kw)
        for c in out.cards:
            c.what.facts = ["Release 0.13.7 is out"] if c.what.facts else []
            c.what.title = "Release 0.13.7" if c.what.title else None
        return out


def latest_week_event(s, business):
    s.expire_all()
    return s.scalars(select(ActivityEvent).where(ActivityEvent.verb == "promotion_week", ActivityEvent.business_id == business.id)
                     .order_by(ActivityEvent.id.desc())).first()


def test_in_english_the_week_names_each_place_in_english_and_the_owner_reads_no_korean(s, business, monkeypatch):
    monkeypatch.setattr(promotion, "run_agent", EnglishMarketing())
    install_people(first_value=3)
    set_bets(weekly_post_cap=2)

    report = week(business, lang="en")

    made = tasks(s, business)
    assert [t.title for t in made] == ["Hacker News: Release 0.13.7 is out",
                                       "dev.to: Release 0.13.7 is out"]
    assert report.skipped["show_hn"].startswith("Still to do first: A recorded demo, ")
    assert report.skipped["okky"] == msg("promotion.skip.rules_unchecked", "en")
    said = [*(t.title for t in made), *report.skipped.values(), latest_week_event(s, business).summary,
            *(t.card[k] for t in made for k in ("channel_name", "rules_summary"))]
    assert not [x for x in said if HANGUL.search(x)]


def test_in_korean_the_week_keeps_the_korean_names_and_reads_as_plain_haeyo(s, business, marketing):
    install_people(first_value=3)
    set_bets(weekly_post_cap=2)

    report = week(business, lang="ko")

    assert [t.title.split(":")[0] for t in tasks(s, business)] == ["Hacker News 글·댓글에 올리기", "dev.to에 올리기"]
    said = [*report.skipped.values(), latest_week_event(s, business).summary]
    assert said and all("요" in x and not smells_in(x) for x in said), said


def test_the_gate_and_the_retro_rules_speak_the_owners_language(s, business):
    posted(s, business, "reddit", days_ago=2)
    with session_scope() as x:
        en = [launch_gate.gate_label(i, lang="en") for i in (*launch_gate.OWNER_GATE_ITEMS, "first_value_20")]
        en.append(promotion.droppable(x, business.id, "reddit", NOW, lang="en")[1])
        ko = [launch_gate.gate_label(i, lang="ko") for i in (*launch_gate.OWNER_GATE_ITEMS, "first_value_20")]
    assert en[3] == "20 people who got value (People list)" and en[4].startswith("Posts with new material: 1")
    assert not [x for x in en if HANGUL.search(x)]
    assert ko[3] == "도움 됨 단계까지 온 사람 20명(명단 기준)" and not [x for x in ko if smells_in(x)]


def test_confirming_a_gate_item_is_logged_in_the_owners_language(s, business):
    with session_scope() as x:
        promotion.confirm_gate_item(x, business.id, "demo", lang="en")
    s.expire_all()
    event = s.scalars(select(ActivityEvent).where(ActivityEvent.verb == "launch_fact_confirmed",
                                                  ActivityEvent.business_id == business.id)).one()
    assert event.summary == f"B-{business.id} launch check confirmed: A recorded demo"
