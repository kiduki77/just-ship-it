"""The bet loop on this branch runs on F11's people list, not on the no-op: the adapter's contract holds against the real
`justshipit.services.people`, and each of its five calls gives the right answer on real rows."""
from datetime import UTC, datetime

import pytest
from justshipit.db import models
from justshipit.db.models import Job, OwnerTask
from justshipit.services import people, people_link
from bet_loop_kit import clear_jobs, jobs_of, reset_world
from people_kit import add, make_business


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    people_link._f11.cache_clear()
    clear_jobs("find_threads")
    yield
    people_link._f11.cache_clear()
    reset_world()
    clear_jobs("find_threads")


@pytest.fixture
def business(s):
    return make_business(s)


def spoke_with(s, business, person, kind="contact") -> None:
    s.add(OwnerTask(business_id=business.id, kind=kind, title="이야기 나누기", card={"person_id": person.id},
                    status="DONE", done_at=datetime.now(UTC)))
    s.flush()


def test_f11_matches_the_contract_the_adapter_calls():
    assert people_link.contract_problems(people, models) == []


def test_this_branch_selects_the_real_provider_not_the_no_op():
    provider = people_link.provider()

    assert isinstance(provider, people_link.F11People) and provider.available is True
    assert provider.people is people


def test_north_star_counts_a_stage_and_everyone_beyond_it(s, business):
    for handle, stage in (("a", "found"), ("b", "contacted"), ("c", "talked"), ("d", "first_value"), ("e", "repeat"),
                          ("f", "dropped")):
        add(s, business, handle, stage=stage)

    got = people_link.provider().north_star(s, business.id)

    assert got == {"contacted": 4, "talked": 3, "first_value": 2, "repeat": 1}


def test_add_inbound_puts_a_newcomer_on_the_list_once_and_says_no_to_a_forgotten_one(s, business):
    provider = people_link.provider()

    assert provider.add_inbound(s, business.id, "github", "newcomer", "https://github.com/acme/logtool/issues/90",
                                "The install failed on my machine for two days", lang="en") is True
    assert provider.add_inbound(s, business.id, "github", "newcomer", "https://github.com/acme/logtool/issues/90", lang="en") is True
    forgotten = add(s, business, "gone", url="https://github.com/acme/logtool/issues/91")
    people.archive(s, forgotten.id, lang="ko")
    assert provider.add_inbound(s, business.id, "github", "gone", "https://github.com/acme/logtool/issues/91", lang="en") is False

    rows = s.query(models.Person).filter_by(business_id=business.id, handle="newcomer").all()
    assert [(p.source, p.stage) for p in rows] == [("inbound", "found")]


def test_add_inbound_skips_a_handle_the_list_refuses_and_keeps_the_session_usable(s, business):
    provider = people_link.provider()

    assert provider.add_inbound(s, business.id, "github", "someone@example.com", "https://github.com/acme/x/issues/1", lang="en") is False
    assert provider.add_inbound(s, business.id, "github", "fine", "https://github.com/acme/x/issues/2", lang="en") is True


def test_quotes_come_only_from_people_at_first_value_or_beyond_newest_first_under_f11s_ref(s, business):
    early = add(s, business, "early", stage="talked", quote="I only looked at the readme for a minute")
    first = add(s, business, "first", stage="first_value", quote="Setup took three minutes and the doc was usable")
    again = add(s, business, "again", stage="repeat", quote="Used it twice this week on our deploy scripts")
    people.add_quote(s, again.id, "The generated README needed one fix only", "https://github.com/acme/logtool/issues/7", lang="ko")
    s.flush()

    got = people_link.provider().quotes(s, business.id, 10)

    assert [q.text for q in got] == ["The generated README needed one fix only",
                                     "Used it twice this week on our deploy scripts",
                                     "Setup took three minutes and the doc was usable"]
    assert {q.ref for q in got} == {people.ref(first.id), people.ref(again.id)} and people.ref(early.id) not in {q.ref for q in got}
    assert [q.stage for q in got] == ["repeat", "repeat", "first_value"]
    assert len(people_link.provider().quotes(s, business.id, 2)) == 2


def test_used_twice_counts_repeat_users_and_those_the_owner_spoke_with(s, business):
    one = add(s, business, "one", stage="repeat")
    add(s, business, "two", stage="repeat")
    other = add(s, business, "three", stage="first_value")
    spoke_with(s, business, one, "follow_up")
    spoke_with(s, business, other)

    got = people_link.provider().used_twice(s, business.id)

    assert (got.users, got.talked) == (2, 1)


def test_a_task_not_done_does_not_count_as_a_talk(s, business):
    one = add(s, business, "one", stage="repeat")
    s.add(OwnerTask(business_id=business.id, kind="contact", title="이야기 나누기", card={"person_id": one.id}, status="OPEN"))
    s.flush()

    assert people_link.provider().used_twice(s, business.id).talked == 0


def test_requesting_participation_queues_f11s_find_threads_once_a_day(s, business):
    provider = people_link.provider()

    assert provider.request_participation(s, business.id) is True
    assert provider.request_participation(s, business.id) is True
    s.flush()

    [job] = jobs_of(s, "find_threads", business_id=business.id)
    assert isinstance(job, Job) and job.max_attempts == 2


def test_the_queued_find_threads_is_a_job_f11s_worker_runs(s):
    from justshipit.workers import runner
    assert runner.handles("find_threads")
