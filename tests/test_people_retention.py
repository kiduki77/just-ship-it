"""Data the list cannot reach by person is kept for a set time and then goes: raw model transcripts, people never
contacted, people dropped. The activity log keeps counts of a search, never the model's text."""
from datetime import datetime, timedelta, timezone

from justshipit.contracts.people import FindPeopleOut
from justshipit.db.models import ActivityEvent, Business, Person, Schedule
from justshipit.db.session import session_scope
from justshipit.operations import jobs, retention, scheduler
from justshipit.services import owner_tasks, people, people_finder, people_tasks, usage
from justshipit.services.usage import MustWait
from justshipit.settings import settings
from people_kit import Researcher, Web, add, candidate, issue_page, make_business
from sqlalchemy import select, update
from test_people_finder import url


def age(s, person, days):
    s.execute(update(Person).where(Person.id == person.id).values(updated_at=datetime.now(timezone.utc) - timedelta(days=days)))
    s.expire(person)


# ------------------------------------------------------------------ what the log keeps of a search

def test_the_activity_log_keeps_counts_of_a_search_and_never_the_model_s_text(s, monkeypatch):
    b = make_business(s)
    secret = "SEARCHED-TEXT-alice-wrote-in-a-forum"
    agent = Researcher(FindPeopleOut(candidates=[candidate("alice")], searched=secret))
    monkeypatch.setattr(people_finder, "run_agent", agent)
    people_finder.find_people(b.id, fetch=Web({url("alice"): issue_page("alice")}), lang="ko")
    with session_scope() as x:
        ran = x.scalar(select(ActivityEvent).where(ActivityEvent.verb == "people_find_ran", ActivityEvent.business_id == b.id))
        assert set(ran.data) == {"asked", "verified", "added", "duplicate", "dropped", "tasks"}
        assert secret not in ran.summary


# ------------------------------------------------------------------ raw model transcripts

def make_day(days_ago, name="call-1"):
    day = (datetime.now() - timedelta(days=days_ago)).strftime("%Y%m%d")
    d = settings().var_dir / "calls" / day / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "prompt.md").write_text("prompt", encoding="utf-8")
    return d.parent


def test_transcripts_older_than_the_window_are_deleted_and_newer_ones_kept():
    old, edge, fresh, today = make_day(45), make_day(31), make_day(29), make_day(0)
    odd = settings().var_dir / "calls" / "not-a-date"
    odd.mkdir(parents=True, exist_ok=True)
    assert retention.prune_calls() >= 2
    assert not old.exists() and not edge.exists()
    assert fresh.exists() and today.exists() and odd.exists(), "only dated folders are touched"


def test_the_window_is_a_setting(monkeypatch):
    monkeypatch.setattr(retention, "company_config", lambda: {"retention": {"call_transcript_days": 5}})
    six, four = make_day(6, "c6"), make_day(4, "c4")
    retention.prune_calls()
    assert not six.exists() and four.exists()


def test_the_default_window_is_thirty_days_for_transcripts_and_ninety_for_people():
    assert retention.DEFAULTS == {"call_transcript_days": 30, "people_found_days": 90, "people_dropped_days": 90}


# ------------------------------------------------------------------ people nobody contacted

def test_a_person_never_contacted_for_ninety_days_is_archived_and_erased(s):
    b = make_business(s)
    old, fresh, met = add(s, b, "old"), add(s, b, "fresh"), add(s, b, "met", stage="contacted")
    task = people_tasks.create_task(s, old.id, "contact", actor="system", lang="ko")
    s.flush()
    age(s, old, 91)
    age(s, fresh, 89)
    age(s, met, 400)
    s.flush()

    done = retention.archive_stale_people(s)

    s.flush()
    for p in (old, fresh, met, task):
        s.refresh(p)
    assert done == {b.id: 1}
    assert old.archived_at is not None and old.quotes == [] and old.display_name == old.handle
    assert task.status == owner_tasks.SKIPPED and task.card == {"person_id": old.id, "archived": True}
    assert fresh.archived_at is None and met.archived_at is None, "contacted people are the owner's to keep"
    assert people.is_forgotten(s, b.id, "github", "old"), "the stub keeps the handle as a do-not-find entry"


def test_a_dropped_person_is_archived_after_ninety_days(s):
    b = make_business(s)
    gone, recent = add(s, b, "gone", stage="dropped"), add(s, b, "recent", stage="dropped")
    age(s, gone, 95)
    age(s, recent, 10)
    s.flush()
    retention.archive_stale_people(s)
    s.flush()
    s.refresh(gone)
    s.refresh(recent)
    assert gone.archived_at is not None and recent.archived_at is None


def test_the_windows_are_settings(s, monkeypatch):
    monkeypatch.setattr(retention, "company_config", lambda: {"retention": {"people_found_days": 10}})
    b = make_business(s)
    p = add(s, b, "slow")
    age(s, p, 11)
    s.flush()
    retention.archive_stale_people(s)
    s.flush()
    s.refresh(p)
    assert p.archived_at is not None


def test_the_daily_job_is_registered_and_scheduled(s):
    assert "retention" in jobs.HANDLERS
    scheduler.sync(s)
    s.flush()
    sc = s.scalar(select(Schedule).where(Schedule.kind == "retention"))
    assert sc.interval_hours == 24


# ------------------------------------------------------------------ the schedule

def test_a_business_that_is_no_longer_active_stops_being_scheduled_and_its_jobs_do_nothing(s, monkeypatch):
    b = make_business(s)
    s.commit()
    scheduler.sync(s)
    s.flush()
    sched = list(s.scalars(select(Schedule).where(Schedule.kind.in_(people_finder.KINDS),
                                                  Schedule.payload["business_id"].astext == str(b.id))))
    assert len(sched) == 2 and all(sc.enabled for sc in sched)

    s.get(Business, b.id).status = "ARCHIVED"
    s.flush()
    scheduler.sync(s)
    s.flush()
    for sc in sched:
        s.refresh(sc)
    assert not any(sc.enabled for sc in sched)

    s.commit()
    asked = Researcher(FindPeopleOut(candidates=[], searched=""))
    monkeypatch.setattr(people_finder, "run_agent", asked)
    assert people_finder.find_people(b.id, fetch=Web({}), lang="ko").asked == 0
    assert people_finder.find_threads(b.id, fetch=Web({}), lang="ko").asked == 0
    assert asked.calls == []


def test_the_weekly_run_and_find_now_share_one_key_per_business_per_day(s):
    b = make_business(s)
    s.commit()
    first = people_finder.request(s, b.id, lang="ko")
    assert first["queued"] == {"find_people": True, "find_threads": True}
    s.commit()

    scheduler.sync(s)
    for sc in s.scalars(select(Schedule).where(Schedule.kind.in_(people_finder.KINDS))):
        sc.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    s.flush()
    fired = scheduler.tick(s)
    assert fired, "the schedule fired"

    from justshipit.db.models import Job
    ours = [j for j in s.scalars(select(Job).where(Job.kind.in_(people_finder.KINDS))) if j.payload.get("business_id") == b.id]
    assert sorted(j.kind for j in ours) == ["find_people", "find_threads"], "one job per kind, not two"
    assert people_finder.request(s, b.id, lang="ko")["queued"] == {"find_people": False, "find_threads": False}


# ------------------------------------------------------------------ the cap on threads and the budget

def thread_at(n):
    from people_kit import thread
    return thread(f"https://stackoverflow.com/questions/{n}/x")


def test_no_more_than_five_participate_tasks_stay_open_for_a_business(s, monkeypatch):
    from justshipit.contracts.people import FindThreadsOut
    from people_kit import page, thread
    b = make_business(s)
    quote = thread().quote
    ids = list(range(100, 108))
    web = Web({thread_at(i).where_found_url: page(f"bob asked {quote}") for i in ids})
    agent = Researcher(FindThreadsOut(threads=[thread_at(i) for i in ids[:3]], searched="x"))
    monkeypatch.setattr(people_finder, "run_agent", agent)
    for n in range(3):
        people_tasks.create_participate(s, b.id, people_tasks.Thread(
            "stackoverflow", f"https://stackoverflow.com/questions/{n}/seed", "carol", quote, "질문", ["요점"]), lang="ko")
    s.commit()

    report = people_finder.find_threads(b.id, fetch=web, lang="ko")

    assert (report.added, report.dropped["cap"]) == (2, 1)
    assert people_tasks.open_participate(s, b.id) == 5
    again = people_finder.find_threads(b.id, fetch=web, lang="ko")
    assert again.asked == 0 and len(agent.calls) == 1, "with five open there is no model call at all"
    assert "5 posts" in again.notes[0]


def test_a_research_run_reserves_several_calls_not_one():
    step = usage.STEPS["people_find"]
    assert step.purpose == "people" and step.usd(1) == 4 * usage.purpose("people").reserve_usd
    assert usage.worst_units()["people_find"] == 1


def test_the_search_waits_before_the_model_when_the_whole_run_does_not_fit(s, monkeypatch):
    from conftest import store_usage
    b = make_business(s)
    store_usage(day_usd_eq=usage.STEPS["people_find"].usd(1) - 0.1)
    never = Researcher(FindPeopleOut(candidates=[], searched=""))
    monkeypatch.setattr(people_finder, "run_agent", never)
    try:
        people_finder.find_people(b.id, fetch=Web({}), lang="ko")
        raise AssertionError("should have waited")
    except MustWait as wait:
        assert wait.reason == "usage_cap_day"
    finally:
        from justshipit.db.models import AppSetting
        with session_scope(lease_checked=False) as x:
            x.query(AppSetting).delete()
    assert never.calls == []
