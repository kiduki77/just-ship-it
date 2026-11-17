"""The audit a business owner reads: per business, its description and what the readers pick from it. On the test
database."""
from bet_loop_kit import line
from justshipit.services import roadmap
from justshipit.services.profile import audit, store
from justshipit.services.profile.store import NewValue

SHOP = "망원동 일대 맞벌이 반려견 보호자와 동네 산책 도우미를 연결하는 동네 기반 산책 대행 서비스"
SHOP_CUSTOMER = "망원동 일대 맞벌이 반려견 보호자"
SHOP_PATH = "당근 동네생활과 동네 단톡방에서 시작"
CLI = "A command line tool that documents what a developer ran in the terminal"


def approved(s, name, one_liner, customer, path):
    """An approved plan: the gate sections the owner must have settled."""
    b = roadmap.create_business(s, name=name, one_liner=one_liner)
    sections = [{"key": key, "title": key, "lines": [line(f"l-{key}", text)]}
                for key, text in (("one_liner", one_liner), ("customer_problem", customer), ("path", path),
                                  ("goal_deadline", "30 customers in six weeks"))]
    roadmap.new_brief_version(s, b.id, sections=sections, assumptions=[], author="owner", approved=True)
    s.commit()
    return b


def describe(s, bid, line_, provenance, *codes):
    owner = provenance == "confirmed"
    store.set_line(s, bid, line_, [NewValue(c, None, provenance, "console") for c in codes],
                   cause="owner_change" if owner else "backfill", written_by="owner" if owner else "project_manager",
                   brief_id=None, if_live=None, lang="en")
    s.commit()


def row(rows, business):
    return next(r for r in rows if r.business_id == business.id)


def test_a_shop_with_a_confirmed_description_picks_the_neighborhood_places(s):
    shop = approved(s, "Mango Walks", SHOP, SHOP_CUSTOMER, SHOP_PATH)
    describe(s, shop.id, "buyer", "confirmed", "consumers")
    describe(s, shop.id, "reach", "confirmed", "local")
    r = row(audit.compare(s, [shop.id]), shop)
    assert r.market == "ko"
    assert r.description == {"buyer": (("consumers", "confirmed"),), "reach": (("local", "confirmed"),), "has": ()}
    assert r.channels == ("daangn_community", "naver_cafe", "kakao_openchat", "instagram_local")
    assert (r.searched, r.by_hand) == (("daangn", "naver_cafe", "naver_blog"), ("kakao_openchat", "instagram"))
    assert r.unsure == () and r.waiting_launches == ()


def test_a_business_with_no_plan_and_no_description_picks_nothing_and_says_what_it_waits_on(s):
    empty = roadmap.create_business(s, name="Empty", one_liner="A thing")
    s.commit()
    r = row(audit.compare(s, [empty.id]), empty)
    assert r.market == "en"
    assert r.description == {"buyer": (), "reach": (), "has": ()}
    assert r.unsure == ("buyer", "reach")
    assert r.channels == () and (r.searched, r.by_hand) == ((), ())


def test_a_one_time_launch_waits_while_who_buys_is_a_guess(s):
    cli = approved(s, "Terminal docs", CLI, "Developers who cannot show what they ran", "Post it where developers read")
    describe(s, cli.id, "buyer", "guess", "developers")
    describe(s, cli.id, "reach", "confirmed", "communities")
    r = row(audit.compare(s, [cli.id]), cli)
    assert "hacker_news" in r.channels
    assert set(r.waiting_launches) == {"show_hn", "product_hunt"} and not set(r.waiting_launches) & set(r.channels)
    describe(s, cli.id, "buyer", "confirmed", "developers")
    r = row(audit.compare(s, [cli.id]), cli)
    assert r.waiting_launches == () and {"show_hn", "product_hunt"} <= set(r.channels)


def test_an_archived_business_is_left_out_and_ids_narrow_the_rows(s):
    kept = roadmap.create_business(s, name="Kept", one_liner="A thing")
    gone = roadmap.create_business(s, name="Gone", one_liner="A thing")
    gone.status = "ARCHIVED"
    s.commit()
    assert {kept.id} <= {r.business_id for r in audit.compare(s)} and gone.id not in {r.business_id for r in audit.compare(s)}
    assert [r.business_id for r in audit.compare(s, [kept.id, gone.id])] == [kept.id]


def test_the_report_names_each_business_its_values_and_what_the_readers_pick(s):
    shop = approved(s, "Mango Walks", SHOP, SHOP_CUSTOMER, SHOP_PATH)
    describe(s, shop.id, "buyer", "guess", "consumers")
    empty = roadmap.create_business(s, name="Empty", one_liner="A thing")
    s.commit()
    text = audit.render(audit.compare(s, [shop.id, empty.id]))
    assert f"B-{shop.id} Mango Walks (market ko)" in text and f"B-{empty.id} Empty (market en)" in text
    assert "buyer: consumers (guess)" in text and "reach: unknown" in text
    assert "waiting on: reach" in text and "waiting on: buyer, reach" in text
    assert "channels: none" in text and "searched: none" in text and "by hand: none" in text


def test_the_report_lists_the_picks_and_leaves_out_a_line_nobody_is_waiting_on(s):
    shop = approved(s, "Mango Walks", SHOP, SHOP_CUSTOMER, SHOP_PATH)
    describe(s, shop.id, "buyer", "confirmed", "consumers")
    describe(s, shop.id, "reach", "confirmed", "local")
    text = audit.render(audit.compare(s, [shop.id]))
    assert "channels: daangn_community, naver_cafe, kakao_openchat, instagram_local" in text
    assert "searched: daangn, naver_cafe, naver_blog" in text
    assert "by hand: kakao_openchat, instagram" in text
    assert "waiting on" not in text and "one-time launches" not in text


def test_the_report_names_the_launches_that_wait_for_a_firm_buyer(s):
    cli = approved(s, "Terminal docs", CLI, "Developers who cannot show what they ran", "Post it where developers read")
    describe(s, cli.id, "buyer", "guess", "developers")
    describe(s, cli.id, "reach", "confirmed", "communities")
    text = audit.render(audit.compare(s, [cli.id]))
    assert "one-time launches waiting for a said or confirmed buyer: show_hn, product_hunt" in text


def test_no_business_is_said_plainly():
    assert audit.render([]) == "No business to compare."
