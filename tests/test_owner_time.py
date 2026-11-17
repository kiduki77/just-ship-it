"""The owner's day: the time the owner gave at kickoff sizes the Today list, so one day never asks for more.

Persona round 3: 박정숙 said "가게 끝나고 하루 30분 정도 시간을 쓸 수 있어요"; her dashboard said "오늘 할 일 6 · 약
1시간"."""
from datetime import UTC, datetime

import pytest
from justshipit.db.models import AgentRequest, OwnerTask
from justshipit.db.session import session_scope
from justshipit.services import owner_time, roadmap, today
from bet_loop_kit import reset_world
from people_kit import line
from sqlalchemy import update


@pytest.fixture(autouse=True)
def _world():
    """A company-wide card another test file left waiting is on every business's list, so it would take a day's
    minutes here."""
    reset_world()
    with session_scope() as s:
        s.execute(update(AgentRequest).where(AgentRequest.project_id.is_(None), AgentRequest.status == "PENDING")
                  .values(status="CANCELLED"))
    yield
    reset_world()


@pytest.mark.parametrize(("said", "minutes"), [
    ("가게 끝나고 하루 30분 정도 시간을 쓸 수 있어요", 30),
    ("평일 저녁 1시간쯤 돼요", 60),
    ("하루 한 시간 반", 90),
    ("매일 1시간 30분", 90),
    ("주 5시간 정도", 43),
    ("일주일에 3시간", 26),
    ("About 30 minutes a day after closing", 30),
    ("an hour a day", 60),
    ("1.5 hours per day", 90),
    ("5 hours a week", 43),
    ("주말에만 돼요", None),
    ("시간은 충분해요", None),
])
def test_the_owners_words_become_minutes_a_day(said, minutes):
    assert owner_time.minutes_per_day(said) == minutes


def test_one_day_never_holds_more_than_the_owners_time():
    assert owner_time.plan_days([10] * 6, [False] * 6, 30) == [0, 0, 0, 1, 1, 1]


def test_what_stops_the_company_is_today_whatever_the_time():
    assert owner_time.plan_days([3, 3, 10, 10, 10], [True, True, False, False, False], 20) == [0, 0, 0, 1, 1]


def test_a_to_do_longer_than_the_day_takes_a_day_of_its_own():
    assert owner_time.plan_days([10, 45, 10], [False] * 3, 30) == [0, 1, 2]


def test_with_no_time_given_everything_is_today():
    assert owner_time.plan_days([10] * 6, [False] * 6, None) == [0] * 6


def _shop(s, said: str | None):
    b = roadmap.create_business(s, name="정숙이네 반찬", one_liner="망원동에서 12년 한 반찬가게예요")
    sections = roadmap.empty_sections()
    for sec in sections:
        if sec["key"] == "owner_time" and said:
            sec["lines"] = [line(said)]
        if sec["key"] == "customer_problem":
            sec["lines"] = [line("새로 생긴 아파트 사람들은 가게를 몰라요")]
        if sec["key"] == "goal_deadline":
            sec["lines"] = [line("두 달 안에 단골 열 명")]
    roadmap.new_brief_version(s, b.id, sections=sections, assumptions=[], author="project_manager", approved=True)
    for i in range(6):
        s.add(OwnerTask(business_id=b.id, kind="contact", title=f"연락: 손님 {i}", card={}, status="OPEN"))
    s.commit()
    return b


def test_the_today_list_fits_the_half_hour_the_shop_owner_gave(s):
    b = _shop(s, "가게 끝나고 하루 30분 정도 시간을 쓸 수 있어요")
    built = today.build(s, b.id, lang="ko", everything=True, now=datetime.now(UTC))
    assert built["daily_min"] == 30
    days = [i["day"] for i in built["items"]]
    assert days == [0, 0, 0, 1, 1, 1]
    assert built["today_min"] == 30 and built["today_count"] == 3 and built["total"] == 6


def test_what_is_overdue_stays_today_however_long_the_list(s):
    """Four overdue to-dos in a half-hour day are still today's: none reads "for tomorrow" beside its overdue mark."""
    b = _shop(s, "가게 끝나고 하루 30분 정도 시간을 쓸 수 있어요")
    late = datetime(2020, 1, 1, tzinfo=UTC)
    for task in s.query(OwnerTask).filter_by(business_id=b.id).limit(4):
        task.due_at = late
    s.commit()
    built = today.build(s, b.id, lang="ko", everything=True, now=datetime.now(UTC))
    assert [i["day"] for i in built["items"]] == [0, 0, 0, 0, 1, 1]
    assert built["today_count"] == 4


def test_a_business_whose_owner_gave_no_time_keeps_one_list(s):
    b = _shop(s, None)
    built = today.build(s, b.id, lang="ko", everything=True, now=datetime.now(UTC))
    assert built["daily_min"] is None and {i["day"] for i in built["items"]} == {0}
    assert built["today_min"] == built["total_min"] == 60


def test_first_talks_with_steps_count_one_sitting_not_the_whole_talk(s):
    """Round-9 review: a to-do about a person has two steps (reach out, then talk). Counting both, 20 minutes, put one
    first talk a day on a half-hour list; Today counts the next step, so reaching out to all six fits today."""
    b = _shop(s, "가게 끝나고 하루 30분 정도 시간을 쓸 수 있어요")
    for i, task in enumerate(s.query(OwnerTask).filter_by(business_id=b.id)):
        task.card = {"person": {"id": i + 1, "name": f"손님 {i}"}}
    s.commit()
    built = today.build(s, b.id, lang="ko", everything=True, now=datetime.now(UTC))
    assert [i["day"] for i in built["items"]] == [0, 0, 0, 0, 0, 0]
    assert built["today_min"] == 30
