"""Leftovers of the cycle build: the owner's to-do list shows a cycle that waits for them, and an agent's brief says
which stage its project works in."""
from datetime import UTC, datetime

import cycle_kit
import pytest
from justshipit.db.models import Job
from justshipit.orchestration import context
from justshipit.services import cycle, projects, today

pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def _cycle_items(s, b):
    return [i for i in today.collect(s, b.id, datetime.now(UTC), "en") if i.kind == "cycle"]


def test_a_proposal_that_waits_for_the_owner_is_one_to_do(s):
    b = cycle_kit.business(s, "today proposal")
    assert _cycle_items(s, b) == []
    cycle_kit.proposed(s, b)
    [item] = _cycle_items(s, b)
    assert (item.tier, item.action) == (today.Tier.BLOCKING, "open")
    assert today.label("cycle", "en") and item.title and item.reason
    assert item.view("en")["kind"] == "cycle"
    assert item.view("en")["link"] == f"/?b={b.id}"   # the dashboard, where the proposal card is


def test_a_stage_at_the_gate_is_one_to_do_and_a_running_stage_is_none(s):
    b = cycle_kit.business(s, "today gate")
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    assert _cycle_items(s, b) == []
    for p in cycle.stage_projects(s, st.id):
        p.status = "DONE"
    s.execute(Job.__table__.update().where(Job.stage_id == st.id, Job.kind.notin_(cycle._PROJECT_MANAGER_KINDS)).values(status="done"))
    cycle.tick(s, lang="en")
    cycle.store_summary(s, st.id, st.round, None, lang="en")
    [item] = _cycle_items(s, b)
    assert st.title in item.title and item.tier == today.Tier.BLOCKING
    assert item.view("en")["link"] == f"/?b={b.id}"   # the dashboard, where the gate card is
    cycle.decide(s, c.id, cycle.StopHere(after_stage_id=st.id), by="owner", lang="en")
    assert _cycle_items(s, b) == []


def test_the_to_do_follows_the_owners_language(s):
    b = cycle_kit.business(s, "today ko")
    cycle_kit.proposed(s, b)
    en = today.collect(s, b.id, datetime.now(UTC), "en")
    ko = today.collect(s, b.id, datetime.now(UTC), "ko")
    assert [i.title for i in en if i.kind == "cycle"] != [i.title for i in ko if i.kind == "cycle"]


def test_a_project_of_a_stage_reads_the_stage_in_its_brief(s):
    b = cycle_kit.business(s, "brief")
    c = cycle_kit.started(s, b)
    first = cycle_kit.running(s, c)
    [p1] = cycle.stage_projects(s, first.id)
    brief = context.project_brief(s, p1)
    assert "### This stage" in brief
    assert first.title in brief and first.doing in brief and first.owner_does in brief
    assert "Earlier stages" not in brief


def test_a_later_stage_brief_names_what_the_earlier_stages_found(s):
    b = cycle_kit.business(s, "brief later")
    c = cycle_kit.started(s, b)
    first = cycle_kit.running(s, c)
    for p in cycle.stage_projects(s, first.id):
        p.status = "DONE"
    s.execute(Job.__table__.update().where(Job.stage_id == first.id, Job.kind.notin_(cycle._PROJECT_MANAGER_KINDS)).values(status="done"))
    cycle.tick(s, lang="en")
    cycle.store_summary(s, first.id, first.round, None, lang="en")
    version = cycle.proposal(s, c.id, status=("ready",)).version
    cycle.decide(s, c.id, cycle.StartNext(after_stage_id=first.id, version=version), by="owner", lang="en")
    second = cycle_kit.running(s, c)
    [p2] = cycle.stage_projects(s, second.id)
    brief = context.project_brief(s, p2)
    assert second.title in brief and "Earlier stages" in brief
    assert first.title in brief and first.summary["headline"] in brief


def test_a_project_of_no_stage_has_no_stage_section(s):
    p = projects.create(s, "a goal", name="Loose", start=False)
    assert "### This stage" not in context.project_brief(s, p)


def test_a_stage_and_a_cycle_event_carry_what_the_dashboard_feed_names(s):
    """The feed words "Stage Validate started" and "Cycle 1 started" from the event's data, in the console's language, not
    from the sentence stored in the language of the day it happened."""
    from justshipit.db.models import ActivityEvent
    from sqlalchemy import select
    b = cycle_kit.business(s, "feed words")
    c = cycle_kit.started(s, b)
    st = cycle_kit.running(s, c)
    events = {e.verb: e for e in s.scalars(select(ActivityEvent).where(ActivityEvent.business_id == b.id))}
    assert events["cycle_started"].data["n"] == c.number
    assert events["stage_running"].data["title"] == st.title


def test_the_to_do_to_read_the_cycle_is_gone_once_the_first_stage_starts(s):
    b = cycle_kit.business(s, "today start")
    c = cycle_kit.proposed(s, b)
    assert len(_cycle_items(s, b)) == 1
    cycle.decide(s, c.id, cycle.StartCycle(version=cycle_kit.version(s, c.id)), by="owner", lang="en")
    assert _cycle_items(s, b) == []
    built = today.build(s, b.id, lang="en", everything=True, now=datetime.now(UTC))
    assert not [i for i in built["items"] if i["kind"] == "cycle"]


def test_the_list_refreshes_when_a_cycle_table_changes():
    """today.py reads these tables, so the console must reload the list when one changes (web/src/today.ts)."""
    import re
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "web" / "src" / "today.ts").read_text(encoding="utf-8")
    tables = re.search(r"TODAY_TABLES = \[(.*?)\]", source, re.S).group(1)
    for name in ("cycles", "cycle_stages", "cycle_proposals"):
        assert f'"{name}"' in tables
