"""The eight counting rules of `tracking_count.standing`, on facts fed to it: nothing here reads a database or a clock."""
from datetime import date, timedelta

import pytest
from justshipit.services import tracking_count as tc
from justshipit.services.tracking_count import Facts, Person, Point, Post, Tag

D0 = date(2026, 11, 1)
TODAY = date(2026, 11, 16)
DOMAINS = {"hacker_news": ("news.ycombinator.com",), "show_hn": ("news.ycombinator.com",),
           "reddit": ("reddit.com",), "discord_global": ("discord.com",)}


def facts(**kw) -> Facts:
    base = dict(posts=[], people=[], moved={}, referrers=[], visits=[], signups=[], tallies=[], tags=[], domains=DOMAINS)
    return Facts(**{**base, **kw})


def person(i, *, stage="talked", source="public_problem", via=None, archived=False, updated=D0) -> Person:
    return Person(i, f"PPL-{i}", source, stage, via, archived, updated)


def post(n, channel, on: date | None) -> Post:
    return Post(f"OT-{n}", channel, on)


def ref(n, host, day, count) -> Point:
    return Point(f"mp-{n}", host, day, count)


def rows(st) -> dict[str, tc.Row]:
    return {r.key: r for r in st.rows}


# ------------------------------------------------------------------ rule 1 and 8: one person, one row, and the invariant

def test_the_got_value_of_every_row_adds_up_to_the_people_who_got_value_and_nobody_counts_twice():
    people = [person(1, stage="first_value", source="warm"), person(2, stage="repeat", via="hacker_news"),
              person(3, stage="first_value", via="friend"), person(4, stage="talked", via="friend"),
              person(5, stage="first_value", source="inbound"), person(6, stage="repeat", source="similar_tool"),
              person(7, stage="dropped", via="hacker_news"), person(8, stage="first_value", via="x", archived=True),
              person(9, stage="found", source="participation")]

    st = tc.standing(facts(people=people), TODAY)

    live_got = [p for p in people if p.stage in ("first_value", "repeat") and not p.archived]
    assert sum(r.got_value.n for r in st.rows) == len(live_got) == 5
    assert sum(r.people.n for r in st.rows) == len([p for p in people if not p.archived and p.stage != "dropped"]) == 7
    seen = [ref_ for r in st.rows for ref_ in r.people.refs]
    assert len(seen) == len(set(seen)), "a person is in one row"
    got = {k: r.got_value.n for k, r in rows(st).items()}
    assert got == {"known": 1, "hacker_news": 1, "friend": 1, "no_answer": 1, "reached_out": 1}


@pytest.mark.parametrize(("via", "source", "key"), [
    ("hacker_news", "warm", "hacker_news"), (None, "warm", "known"), (None, "public_problem", "reached_out"),
    (None, "similar_tool", "reached_out"), (None, "participation", "reached_out"), (None, "inbound", "no_answer"),
    ("friend", "inbound", "friend"),
])
def test_a_person_is_in_the_row_their_answer_names_or_their_source_implies(via, source, key):
    assert tc.row_key(via, source) == key


def test_a_dropped_or_archived_person_is_in_no_row_at_all():
    st = tc.standing(facts(people=[person(1, stage="dropped", via="friend"), person(2, archived=True, via="friend")]), TODAY)

    assert st.rows == () and st.answers == 0


# ------------------------------------------------------------------ rule 2 and 3: nothing is added across kinds

def test_a_tally_has_its_own_column_and_is_never_a_person_a_got_value_or_a_milestone_entry():
    tallies = [Point("mp-1", "friend", date(2026, 11, 14), 2), Point("mp-2", "friend", TODAY, 3)]

    st = tc.standing(facts(tallies=tallies), TODAY)

    row = rows(st)["friend"]
    assert (row.tallied.n, row.tallied.basis, row.tallied_today) == (5, "told", 3)
    assert (row.people.n, row.got_value.n) == (0, 0)
    assert [m.have for m in st.milestones] == [0, 0]
    assert st.answers == 5


def test_posts_visits_and_sign_ups_are_three_numbers_that_never_add_up_to_each_other():
    tags = [Tag("dg-1", "discord_global", "OT-1", True, True)]
    st = tc.standing(facts(posts=[post(1, "discord_global", D0)], tags=tags,
                           visits=[Point("mp-5", "dg-1", TODAY, 40)], signups=[Point("mp-6", "dg-1", TODAY, 4)],
                           people=[person(1, stage="first_value", via="discord_global")]), TODAY)

    row = rows(st)["discord_global"]
    assert (row.posts, row.visits.n, row.signups.n, row.people.n, row.got_value.n) == (1, 40, 4, 1, 1)
    assert (row.visits.basis, row.signups.basis) == ("typed", "typed")
    assert [(t.tag, t.visits, t.signups) for t in row.tags] == [("dg-1", 40, 4)]


def test_only_a_done_post_counts_and_a_post_waiting_for_the_owner_does_not():
    st = tc.standing(facts(posts=[post(1, "reddit", D0), post(2, "reddit", None)]), TODAY)

    assert rows(st)["reddit"].posts == 1


def test_a_typed_count_is_the_newest_one_per_tag_not_a_sum_of_days():
    tags = [Tag("dg-1", "discord_global", "OT-1", True, True)]
    visits = [Point("mp-1", "dg-1", date(2026, 11, 10), 10), Point("mp-2", "dg-1", date(2026, 11, 15), 30)]

    st = tc.standing(facts(posts=[post(1, "discord_global", D0)], tags=tags, visits=visits), TODAY)

    assert (rows(st)["discord_global"].visits.n, rows(st)["discord_global"].visits.refs) == (30, ("mp-2",))


def test_a_tag_nobody_made_is_ignored_and_an_open_or_untagged_posts_tag_is_not_offered_for_typing():
    tags = [Tag("dg-1", "discord_global", "OT-1", False, True), Tag("hn-2", "hacker_news", "OT-2", True, False)]

    st = tc.standing(facts(posts=[post(2, "hacker_news", D0)], tags=tags, visits=[Point("mp-1", "zz-9", TODAY, 5)]), TODAY)

    assert rows(st)["hacker_news"].tags == () and "discord_global" not in rows(st)


# ------------------------------------------------------------------ rule 4: GitHub windows overlap

def test_overlapping_snapshots_are_never_summed_the_newest_is_the_last_14_days():
    refs = [ref(i, "news.ycombinator.com", TODAY - timedelta(days=i), 30 - i) for i in range(5)]

    st = tc.standing(facts(posts=[post(1, "hacker_news", TODAY - timedelta(days=3))], referrers=refs), TODAY)

    (host,) = st.hosts
    assert (host.last_14d.n, host.last_14d.basis, host.last_14d.as_of) == (30, "read", TODAY)
    assert (host.at_least.n, host.at_least.refs) == (30, ("mp-0",)), "snapshots a day apart share their days"


def test_the_at_least_chain_counts_back_from_the_newest_in_steps_of_14_days_or_more():
    days = [D0, D0 + timedelta(days=7), D0 + timedelta(days=14), D0 + timedelta(days=28)]
    refs = [ref(i, "news.ycombinator.com", d, n) for i, (d, n) in enumerate(zip(days, (10, 20, 30, 40)))]
    posts = [post(i, "hacker_news", d) for i, d in enumerate((D0, D0 + timedelta(days=14), D0 + timedelta(days=28)))]

    st = tc.standing(facts(posts=posts, referrers=refs), D0 + timedelta(days=28))

    (host,) = st.hosts
    assert (host.at_least.n, host.at_least.refs) == (80, ("mp-3", "mp-2", "mp-0"))
    assert host.last_14d.n == 40


def test_a_missed_day_makes_the_gap_larger_and_never_the_number():
    two = [ref(1, "news.ycombinator.com", D0, 10), ref(2, "news.ycombinator.com", D0 + timedelta(days=15), 12)]

    a = tc.standing(facts(posts=[post(1, "hacker_news", D0), post(2, "hacker_news", D0 + timedelta(days=15))], referrers=two),
                    D0 + timedelta(days=15))
    b = tc.standing(facts(posts=[post(1, "hacker_news", D0), post(2, "hacker_news", D0 + timedelta(days=15))],
                          referrers=[two[1]]), D0 + timedelta(days=15))

    assert a.hosts[0].at_least.n == 22 and b.hosts[0].at_least.n == 12


# ------------------------------------------------------------------ rule 5: who a host is credited to

def test_two_channels_that_share_a_host_get_one_host_line_that_names_both_and_neither_row_gets_the_visits():
    posts = [post(1, "hacker_news", TODAY - timedelta(days=2)), post(2, "show_hn", TODAY - timedelta(days=5))]

    st = tc.standing(facts(posts=posts, referrers=[ref(1, "news.ycombinator.com", TODAY, 90)]), TODAY)

    (host,) = st.hosts
    assert host.channels == ("hacker_news", "show_hn") and host.last_14d.n == 90
    assert rows(st)["hacker_news"].visits is None and rows(st)["show_hn"].visits is None


def test_the_hosts_of_one_channel_and_its_subdomains_go_to_its_row_as_each_hosts_own_newest_snapshot():
    st = tc.standing(facts(posts=[post(1, "reddit", TODAY - timedelta(days=13))],
                           referrers=[ref(1, "reddit.com", TODAY, 21), ref(2, "old.reddit.com", TODAY, 4)]), TODAY)

    assert rows(st)["reddit"].visits.n == 25 and tc.ORGANIC not in rows(st)
    assert sorted(h.host for h in st.hosts) == ["old.reddit.com", "reddit.com"]


def test_a_post_older_than_the_window_does_not_earn_the_visits_they_go_to_found_you_on_their_own():
    st = tc.standing(facts(posts=[post(1, "reddit", TODAY - timedelta(days=14))], referrers=[ref(1, "reddit.com", TODAY, 21)]),
                     TODAY)

    assert st.hosts[0].channels == () and rows(st)[tc.ORGANIC].visits.n == 21
    assert rows(st)["reddit"].visits is None and rows(st)["reddit"].posts == 1


def test_a_host_no_channel_owns_is_organic_and_a_post_still_waiting_does_not_credit_anyone():
    st = tc.standing(facts(posts=[post(1, "reddit", None)],
                           referrers=[ref(1, "google", TODAY, 8), ref(2, "reddit.com", TODAY, 3)]), TODAY)

    assert {h.host: h.channels for h in st.hosts} == {"google": (), "reddit.com": ()}
    assert rows(st)[tc.ORGANIC].visits.n == 11


def test_each_snapshot_is_credited_by_its_own_day_so_an_old_one_does_not_join_a_newer_chain():
    refs = [ref(1, "reddit.com", D0, 5), ref(2, "reddit.com", D0 + timedelta(days=30), 7)]

    st = tc.standing(facts(posts=[post(1, "reddit", D0 + timedelta(days=29))], referrers=refs), D0 + timedelta(days=30))

    assert st.hosts[0].channels == ("reddit",) and st.hosts[0].at_least.n == 7


# ------------------------------------------------------------------ rule 6: one cell, one source

def test_typed_visits_win_the_cell_over_the_hosts_and_the_two_are_never_added():
    tags = [Tag("rd-1", "reddit", "OT-1", True, True)]

    st = tc.standing(facts(posts=[post(1, "reddit", TODAY - timedelta(days=1))], tags=tags,
                           visits=[Point("mp-9", "rd-1", TODAY, 12)], referrers=[ref(1, "reddit.com", TODAY, 50)]), TODAY)

    row = rows(st)["reddit"]
    assert (row.visits.n, row.visits.basis) == (12, "typed") and row.visits_at_least is None
    assert st.hosts[0].last_14d.n == 50, "the host line still shows what GitHub read"


def test_the_read_visits_of_a_row_say_when_and_carry_their_at_least_beside_them():
    st = tc.standing(facts(posts=[post(1, "reddit", TODAY)], referrers=[ref(1, "reddit.com", TODAY, 50)]), TODAY)

    row = rows(st)["reddit"]
    assert (row.visits.n, row.visits.basis, row.visits.as_of) == (50, "read", TODAY)
    assert (row.visits_at_least.n, row.visits_at_least.basis) == (50, "at_least")


def test_stale_referrers_are_flagged_after_two_days_and_a_business_with_none_is_not_stale():
    old = [ref(1, "reddit.com", TODAY - timedelta(days=3), 5)]

    assert tc.standing(facts(referrers=old), TODAY).referrers_stale is True
    assert tc.standing(facts(referrers=[ref(1, "reddit.com", TODAY - timedelta(days=2), 5)]), TODAY).referrers_stale is False
    none = tc.standing(facts(), TODAY)
    assert (none.referrers_as_of, none.referrers_stale) == (None, False)


# ------------------------------------------------------------------ rule 7: the first 10 and the first 100

def test_the_first_10_orders_by_the_day_they_got_value_breaks_ties_by_id_and_sums_to_have():
    people = [person(i, stage="first_value", via="friend" if i % 2 else None, source="warm") for i in range(1, 13)]
    moved = {i: D0 + timedelta(days=13 - i) for i in range(1, 13)}   # person 12 was first, person 1 last
    moved[11] = moved[12]                                            # a tie with 12: the lower id goes first

    st = tc.standing(facts(people=people, moved=moved), TODAY)

    ten, hundred = st.milestones
    assert (ten.n, ten.have, hundred.n, hundred.have) == (10, 10, 100, 12 - 0)
    assert sum(ten.by_key.values()) == ten.have and sum(hundred.by_key.values()) == hundred.have
    assert ten.reached_on == moved[3] and hundred.reached_on is None
    assert ten.by_key == {"friend": 5, "known": 5} and ten.estimated == 0


def test_a_person_moved_before_the_log_existed_falls_back_to_their_rows_last_change_and_is_counted_as_estimated():
    people = [person(1, stage="first_value", updated=date(2026, 10, 1)), person(2, stage="repeat", updated=date(2026, 9, 1)),
              person(3, stage="first_value")]

    st = tc.standing(facts(people=people, moved={3: date(2026, 10, 15)}), TODAY)

    assert st.milestones[0].estimated == 2 and st.milestones[0].have == 3 and st.milestones[0].reached_on is None


def test_an_answer_less_got_value_person_is_a_no_answer_key_and_the_keys_still_sum_to_have():
    people = [person(1, stage="first_value", source="inbound"), person(2, stage="first_value", via="friend")]

    st = tc.standing(facts(people=people), TODAY)

    assert st.milestones[0].by_key == {"no_answer": 1, "friend": 1}


# ------------------------------------------------------------------ the too-few line

@pytest.mark.parametrize(("answered", "tallied", "few"), [(0, 0, True), (20, 9, True), (20, 10, False), (30, 0, False)])
def test_fewer_than_30_answers_and_tallies_say_channels_cannot_be_told_apart_yet(answered, tallied, few):
    people = [person(i, via="friend") for i in range(answered)]
    tallies = [Point("mp-1", "friend", TODAY, tallied)] if tallied else []

    st = tc.standing(facts(people=people, tallies=tallies), TODAY)

    assert (st.answers, st.too_few) == (answered + tallied, few)


def test_rows_sort_by_got_value_then_posts_and_a_row_with_nothing_in_it_is_left_out():
    st = tc.standing(facts(posts=[post(1, "reddit", D0), post(2, "reddit", D0), post(3, "discord_global", D0)],
                           people=[person(1, stage="first_value", via="friend")]), TODAY)

    assert [r.key for r in st.rows] == ["friend", "reddit", "discord_global"]
