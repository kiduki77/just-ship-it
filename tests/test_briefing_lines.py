"""The daily briefing carries the idea box's discard proposals and the people the owner should reach today."""
import pytest
from justshipit.db.models import Business, OwnerIdea, Person
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.operations import jobs, notify  # noqa: F401 - importing jobs registers the lines
from justshipit.services import ideas, people
from people_kit import add, make_business
from sqlalchemy import delete


@pytest.fixture
def business():
    with session_scope() as s:
        b = make_business(s)
        bid = b.id
    yield bid
    with session_scope() as s:
        s.execute(delete(OwnerIdea).where(OwnerIdea.business_id == bid))
        s.execute(delete(Person).where(Person.business_id == bid))


def test_both_lines_are_registered_by_the_job_table():
    assert ideas.pending_discard_line in notify.BRIEFING_LINES and people.today_line in notify.BRIEFING_LINES


def test_ideas_pending_a_discard_confirmation_are_in_the_briefing_until_the_owner_answers(business):
    with session_scope() as s:
        s.add_all([OwnerIdea(business_id=business, text="a", status="TRIAGED", disposition="discard_proposed"),
                   OwnerIdea(business_id=business, text="b", status="TRIAGED", disposition="discard_proposed"),
                   OwnerIdea(business_id=business, text="c", status="TRIAGED", disposition="park")])
        s.flush()
        before = ideas.pending_discard_line(s, "ko")
        english = ideas.pending_discard_line(s, "en")
    assert before == msg("ideas.briefing.discard_waiting", "ko", n=2) and "2 ideas" in english

    with session_scope() as s:
        for idea in s.query(OwnerIdea).filter_by(business_id=business, disposition="discard_proposed"):
            idea.disposition = "discard"
        s.flush()
        assert ideas.pending_discard_line(s, "ko") is None


def test_the_people_to_reach_today_are_counted_without_their_names(business):
    with session_scope() as s:
        assert people.today_line(s, "ko") is None
        add(s, s.get(Business, business), "alice")
        add(s, s.get(Business, business), "bob")
        s.flush()
        line = people.today_line(s, "ko")
        text = notify.briefing_text(s, lang="en")
    assert line == "오늘 연락할 사람 2명"
    assert text is not None and "2 people to reach out to today" in text and "alice" not in text.lower()
