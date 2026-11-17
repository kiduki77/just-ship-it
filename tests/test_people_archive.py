"""Archive reaches every place a person's data went, and a forgotten person is never found again."""
from justshipit.db.models import ActivityEvent, OwnerTask, Person
from justshipit.services import owner_tasks, people, people_tasks
from people_kit import ISSUE_QUOTE, make_business
from sqlalchemy import select
from test_people_privacy import person_task


def thread_of(asker="bob", url="https://stackoverflow.com/questions/77/x"):
    return people_tasks.Thread("stackoverflow", url, asker, "How do I find which request caused the crash in a 3GB log",
                               "로그에서 요청 찾는 법을 묻는다", ["요청 id로 묶어 본다"])


def events_of(s, task_ids):
    return list(s.scalars(select(ActivityEvent).where(ActivityEvent.entity == "owner_task",
                                                      ActivityEvent.entity_id.in_(task_ids))))


def test_answering_a_thread_stamps_the_asker_on_its_card_and_archive_then_clears_it(s):
    b = make_business(s)
    t = people_tasks.create_participate(s, b.id, thread_of(), lang="ko")
    s.flush()
    owner_tasks.done(s, t.id, note="bob에게 요청 id로 묶으라고 답함", outcome=people_tasks.Outcome())
    s.flush()
    bob = s.scalar(select(Person).where(Person.business_id == b.id, Person.handle == "bob"))
    assert t.card["person_id"] == bob.id

    people.archive(s, bob.id, lang="ko")
    s.flush()
    s.refresh(t)

    assert t.card == {"person_id": bob.id, "archived": True}
    assert t.title == people.archived_note("ko") and t.result == {"link": None, "numbers": {}, "note": None}
    assert all(e.data == {} and e.summary == people.archived_note("ko") for e in events_of(s, [t.id]))


def test_archive_clears_an_unanswered_thread_card_that_names_the_person_by_platform_and_handle(s):
    b = make_business(s)
    t = people_tasks.create_participate(s, b.id, thread_of(), lang="ko")
    other = people_tasks.create_participate(s, b.id, thread_of("carol", "https://stackoverflow.com/questions/78/y"), lang="ko")
    p, _ = people.insert(s, b.id, people.NewPerson("stackoverflow", "Bob", "https://stackoverflow.com/users/1/bob",
                                                   "inbound", quote=ISSUE_QUOTE), lang="ko")
    s.flush()

    people.archive(s, p.id, lang="ko")
    s.flush()
    s.refresh(t)
    s.refresh(other)

    assert t.card == {"person_id": p.id, "archived": True} and t.status == owner_tasks.SKIPPED
    assert t.card.get("thread") is None and t.result == {"link": None, "numbers": {}, "note": None}
    assert other.card["thread"]["asker"] == "carol" and other.status == owner_tasks.OPEN
    assert all(e.data == {} for e in events_of(s, [t.id]))


def test_a_person_who_was_forgotten_is_not_offered_again_as_the_asker_of_a_thread(s):
    b = make_business(s)
    p, _ = people.insert(s, b.id, people.NewPerson("stackoverflow", "bob", "https://stackoverflow.com/users/1/bob",
                                                   "inbound", quote=ISSUE_QUOTE), lang="ko")
    people.archive(s, p.id, lang="ko")
    s.flush()
    assert people.is_forgotten(s, b.id, "stackoverflow", "Bob")
    assert not people.is_forgotten(s, b.id, "stackoverflow", "carol")


def test_every_task_card_that_concerns_a_person_carries_the_person_id(s):
    _, p, t = person_task(s)
    assert t.card["person_id"] == p.id
    row = s.scalar(select(OwnerTask).where(OwnerTask.card["person_id"].astext == str(p.id)))
    assert row.id == t.id


def test_find_threads_drops_a_thread_whose_asker_asked_to_be_forgotten(s, monkeypatch):
    from justshipit.contracts.people import FindThreadsOut
    from justshipit.services import people_finder
    from people_kit import Researcher, Web, page, thread

    b = make_business(s)
    p, _ = people.insert(s, b.id, people.NewPerson("stackoverflow", "bob", "https://stackoverflow.com/users/1/bob",
                                                   "inbound", quote=ISSUE_QUOTE), lang="ko")
    people.archive(s, p.id, lang="ko")
    s.commit()
    t = thread()
    web = Web({t.where_found_url: page("bob asked: " + t.quote)})
    monkeypatch.setattr(people_finder, "run_agent", Researcher(FindThreadsOut(threads=[t], searched="x")))

    report = people_finder.find_threads(b.id, fetch=web, lang="ko")

    assert report.dropped["forgotten"] == 1 and report.added == 0
