"""The adapter between the bet loop and F11's people list: it checks the contract it relies on, handles a refusal of one
person without swallowing real errors, and asks for public threads only when a worker handles them."""
import logging
import sys
from types import ModuleType, SimpleNamespace

import pytest
from justshipit import services
from justshipit.db.models import Job
from justshipit.db.session import session_scope
from justshipit.services import people_link, queue
from justshipit.workers import runner
from bet_loop_kit import clear_jobs, jobs_of, make_business, reset_world
from sqlalchemy.exc import IntegrityError


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    people_link._f11.cache_clear()
    clear_jobs("find_threads")
    yield
    people_link._f11.cache_clear()
    reset_world()


class PeopleError(ValueError):
    pass


def good_people() -> SimpleNamespace:
    return SimpleNamespace(PeopleError=PeopleError, north_star=lambda s, business_id: {"first_value": 0},
                           add_inbound=lambda s, business_id, platform, handle, url, quote=None, *, lang, display_name=None: object(),
                           ref=lambda person_id: f"PERSON-{person_id}")


def good_models() -> SimpleNamespace:
    person = type("Person", (), {c: None for c in ("id", "business_id", "archived_at", "stage", "quotes")})
    task = type("OwnerTask", (), {c: None for c in ("business_id", "status", "kind", "card")})
    return SimpleNamespace(Person=person, OwnerTask=task)


def test_a_module_that_matches_the_contract_has_no_problems():
    assert people_link.contract_problems(good_people(), good_models()) == []


@pytest.mark.parametrize("change,expected", [
    (lambda p, m: setattr(p, "north_star", lambda s: {}), "north_star"),
    (lambda p, m: setattr(p, "add_inbound", lambda s, business_id: None), "add_inbound"),
    (lambda p, m: setattr(p, "ref", "not callable"), "people.ref is missing"),
    (lambda p, m: delattr(p, "PeopleError"), "PeopleError is missing"),
    (lambda p, m: delattr(m.Person, "quotes"), "Person.quotes is missing"),
    (lambda p, m: delattr(m, "OwnerTask"), "OwnerTask.card is missing"),
])
def test_each_way_f11_can_differ_from_what_the_adapter_calls_is_named(change, expected):
    people, models = good_people(), good_models()
    change(people, models)

    assert any(expected in problem for problem in people_link.contract_problems(people, models))


def test_a_people_module_that_does_not_match_leaves_the_loop_with_nobody_and_a_warning(monkeypatch, caplog):
    fake = ModuleType("justshipit.services.people")
    fake.__dict__.update(vars(good_people()))
    fake.north_star = lambda s: {}
    monkeypatch.setitem(sys.modules, "justshipit.services.people", fake)
    monkeypatch.setattr(services, "people", fake)

    with caplog.at_level(logging.WARNING, logger=people_link.log.name):
        provider = people_link.provider()

    assert isinstance(provider, people_link.NoPeople) and provider.available is False
    assert "does not match" in caplog.text and "people.north_star" in caplog.text


def test_with_no_people_module_the_loop_runs_with_nobody_and_says_nothing(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "justshipit.services.people", None)
    monkeypatch.delattr(services, "people")
    with caplog.at_level(logging.WARNING, logger=people_link.log.name):
        provider = people_link.provider()

    assert isinstance(provider, people_link.NoPeople) and "does not match" not in caplog.text


def adapter(add_inbound) -> people_link.F11People:
    fake = good_people()
    fake.add_inbound = add_inbound
    return people_link.F11People(fake)


def test_a_person_the_list_refuses_is_logged_and_skipped(s, caplog):
    def refuse(*a, **kw):
        raise PeopleError("이메일 주소 같은 값은 받지 않습니다")

    with caplog.at_level(logging.WARNING, logger=people_link.log.name):
        got = adapter(refuse).add_inbound(s, 1, "github", "a@b.com", "https://x", lang="en")

    assert got is False and "refused github/a@b.com" in caplog.text and "이메일" in caplog.text


def test_a_database_error_on_one_person_is_logged_with_its_trace_and_skipped(s, caplog):
    def store_fails(*a, **kw):
        raise IntegrityError("insert", {}, Exception("duplicate"))

    with caplog.at_level(logging.ERROR, logger=people_link.log.name):
        got = adapter(store_fails).add_inbound(s, 1, "github", "dup", "https://x", lang="en")

    assert got is False and "could not store github/dup" in caplog.text and "Traceback" in caplog.text


def test_a_bug_in_the_list_is_not_swallowed(s):
    def broken(*a, **kw):
        raise AttributeError("a typo in people.py")

    with pytest.raises(AttributeError):
        adapter(broken).add_inbound(s, 1, "github", "x", "https://x", lang="en")


def test_a_person_the_list_accepts_is_true_and_one_it_forgot_is_false(s):
    assert adapter(lambda *a, **kw: object()).add_inbound(s, 1, "github", "x", "https://x", lang="en") is True
    assert adapter(lambda *a, **kw: None).add_inbound(s, 1, "github", "x", "https://x", lang="en") is False


def test_threads_are_requested_only_when_a_worker_handles_them(s, monkeypatch, caplog):
    business = make_business(s)
    mine = adapter(lambda *a, **kw: object())
    monkeypatch.setattr(runner, "handles", lambda kind: False)

    with caplog.at_level(logging.WARNING, logger=people_link.log.name):
        asked = mine.request_participation(s, business.id)
    s.flush()

    assert asked is False and not jobs_of(s, "find_threads", business_id=business.id)
    assert "no worker handler for find_threads" in caplog.text

    monkeypatch.undo()
    assert runner.handles("find_threads"), "F11's worker handles it on this branch"
    assert mine.request_participation(s, business.id) is True
    s.flush()
    [job] = jobs_of(s, "find_threads", business_id=business.id)
    assert job.max_attempts == 2


def test_two_promotion_jobs_of_one_business_in_one_week_never_run_together():
    clear_jobs("promotion_week")
    with session_scope() as x:
        a = queue.enqueue(x, "promotion_week", {"business_id": 901}, idempotency_key="promo-a", by_owner=True)
        b = queue.enqueue(x, "promotion_week", {"business_id": 901}, idempotency_key="promo-b", by_owner=True)
        other = queue.enqueue(x, "promotion_week", {"business_id": 902}, idempotency_key="promo-c", by_owner=True)
        a_id, other_id = a.id, other.id
        assert a.lock_key == b.lock_key != other.lock_key and a.lock_key.startswith("promotion:901:")

    with session_scope() as x:
        first = queue.claim(x, "w1", ["promotion_week"])
        second = queue.claim(x, "w2", ["promotion_week"])
        third = queue.claim(x, "w3", ["promotion_week"])
        claimed = (first.id, second.id, third)

    assert claimed == (a_id, other_id, None), "the second job of business 901 waits for the first"
    clear_jobs("promotion_week")
    with session_scope() as x:
        x.query(Job).filter(Job.idempotency_key.in_(["promo-a", "promo-b", "promo-c"])).update({"status": "done"})
