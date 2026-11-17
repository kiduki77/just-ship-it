"""A person forgotten while the kit's job stores its texts, in either order, leaves none of their words in a kit row. The two
transactions really interleave: one is held open while the other runs on its own connection."""
import threading
from datetime import UTC, datetime

import pytest
from justshipit.db.models import KitText, Person
from justshipit.db.session import session_scope
from justshipit.services import kit, people
from bet_loop_kit import reset_world
from kit_world import Marketing, business, good, person
from sqlalchemy import select

WAIT = 2.0


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    yield
    reset_world()


def kim_rows(s, kim):
    s.expire_all()
    return list(s.scalars(select(KitText).where(KitText.person_id == kim.id)))


def test_a_person_forgotten_in_a_transaction_still_open_while_the_job_stores_is_waited_for_and_gets_nothing_stored(s, monkeypatch):
    monkeypatch.setattr(kit, "run_agent", Marketing())
    b = business(s)
    kim = person(s, b, "kim", source="warm")
    done: dict = {}

    with session_scope() as owner:
        people.archive(owner, kim.id, lang="en")
        owner.flush()  # the owner's forgetting is mid-transaction and holds the person's row
        job = threading.Thread(target=lambda: done.setdefault("report", kit.run(b.id, ["message"], [kim.id], lang="en")))
        job.start()
        job.join(timeout=WAIT)
        waiting = job.is_alive()
    job.join(timeout=20)

    assert waiting, "the job's store waits for the person's row instead of writing past the forgetting"
    assert s.get(Person, kim.id, populate_existing=True).archived_at is not None
    assert kim_rows(s, kim) == [] and done["report"].written == [], "a forgotten person gets no row at all"


def test_a_person_forgotten_while_the_job_has_stored_their_text_is_scrubbed_once_it_commits(s, monkeypatch):
    monkeypatch.setattr(kit, "run_agent", Marketing())
    b = business(s)
    kim = person(s, b, "kim", source="warm")
    now = datetime.now(UTC)
    done: dict = {}

    with session_scope() as store:
        facts = kit._gather(store, b.id, ["message"], [kim.id], "en")
        report = kit._store(store, facts, {kit.Want("message", "p", kim.id): kit.Result(good("message", "p"), None, None)}, None, now)
        store.flush()  # the text is stored in a transaction not yet committed
        forget = threading.Thread(target=lambda: done.setdefault("p", _archive(kim.id)))
        forget.start()
        forget.join(timeout=WAIT)
        waiting = forget.is_alive()
    forget.join(timeout=20)

    assert report.written and waiting, "forgetting waits for the store, which holds the person's row"
    rows = kim_rows(s, kim)
    assert len(rows) == 1 and rows[0].body is None and rows[0].refused == "forgotten" and rows[0].basis["quotes"] == []


def _archive(person_id: int):
    with session_scope() as other:
        return people.archive(other, person_id, lang="en").id
