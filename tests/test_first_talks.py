"""Round 1 opens with the owner talking to the people the plan names, this week (persona round 3, Daniel): the plan had
Jake and Priya, yet "talk to 5 people who signed up and stopped" landed in weeks 2-3 after launch, section 10 of 11,
and round 1 spent its effort on Product Hunt timing."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from people_kit import add, line, make_business
from sqlalchemy import select

from justshipit.contracts.planning import ProjectManagerPlanOut
from justshipit.contracts.work import TaskDraft
from justshipit.db.models import Artifact, OwnerTask
from justshipit.db.session import session_scope
from justshipit.orchestration import company_graph
from justshipit.services import app_settings, people_tasks, projects, registry

RULE = "If 2 of the 3 don't use it again by day 2, we stop and rethink who it's for."
KNOWN = [line("Jake, a coworker on your team who complains about too many tools", id="k1", person="Jake"),
         line("Priya, a coworker on your team", id="k2", person="Priya")]


def _open_contacts(business_id: int) -> list[OwnerTask]:
    with session_scope() as s:
        rows = list(s.scalars(select(OwnerTask).where(OwnerTask.business_id == business_id, OwnerTask.kind == "contact")
                              .order_by(OwnerTask.id)))
        s.expunge_all()
        return rows


@pytest.fixture
def london(monkeypatch):
    """An owner in London: the settings say so wherever they are read."""
    real = app_settings.load

    def load(lang=app_settings.DEFAULT_LANG):
        eff, problems = real(lang)
        return eff.model_copy(update={"usage": eff.usage.model_copy(update={"timezone": "Europe/London"})}), problems
    monkeypatch.setattr(app_settings, "load", load)
    return ZoneInfo("Europe/London")


def _plan(s, monkeypatch, business, stop_rule: str = RULE, prompts: list | None = None) -> int:
    project = projects.create(s, "500 signups", name="Day planner", budget_usd=40, start=False, business=business)
    s.commit()
    out = ProjectManagerPlanOut(goal_interpretation="g", deliverables=["d"], success_criteria=["c"], out_of_scope=[],
                     key_uncertainties=[], stop_rule=stop_rule, build=None, requests=[],
                     tasks=[TaskDraft(key="market", title="market", owner="research", objective="o",
                                      acceptance_criteria=["c"], depends_on=[])])
    seen = prompts if prompts is not None else []
    monkeypatch.setattr(company_graph, "run_agent", lambda agent, purpose, prompt, *a, **k: seen.append(prompt) or out)
    monkeypatch.setattr(company_graph.memory, "recall_block", lambda *a, **k: "")
    company_graph.plan({"project_id": project.id})
    return project.id


def test_the_plan_gives_each_named_person_a_to_do_due_this_week_with_the_stop_rule(s, monkeypatch, london):
    b = make_business(s, known=KNOWN)
    before = datetime.now(timezone.utc)
    prompts = []
    pid = _plan(s, monkeypatch, b, prompts=prompts)

    talks = _open_contacts(b.id)
    assert [t.title for t in talks] == ["Talk to Jake", "Talk to Priya"]
    due = talks[0].due_at.astimezone(london)
    assert all(t.due_at == talks[0].due_at for t in talks)
    assert (due.strftime("%A %H:%M"), before < talks[0].due_at <= before + timedelta(days=9)) == ("Sunday 23:59", True)
    assert f"by Sunday {due.day} {due:%B %Y}" in prompts[0], "the CEO dates the stop rule against the talks' day"
    assert all(t.card["stop_rule"] == RULE for t in talks)
    with session_scope() as x:
        doc = x.scalar(select(Artifact).where(Artifact.project_id == pid, Artifact.kind == "plan"))
        body = registry.read(doc.path)
    assert "## Talk to these people this week\n- Talk to Jake\n- Talk to Priya" in body
    assert f"Keep going or stop: {RULE}" in body


THURSDAY = datetime(2026, 11, 5, 5, 55, tzinfo=timezone.utc)   # B-17's kickoff ended Thursday 5 November


@pytest.mark.parametrize(("now", "due"), [
    (THURSDAY, datetime(2026, 11, 8, 23, 59)),
    (datetime(2026, 11, 6, 21, 0, tzinfo=timezone.utc), datetime(2026, 11, 8, 23, 59)),     # Friday: 3 days left
    (datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc), datetime(2026, 11, 15, 23, 59)),    # Saturday: next week
    (datetime(2026, 11, 2, 0, 30, tzinfo=timezone.utc), datetime(2026, 11, 8, 23, 59)),     # Monday morning
])
def test_this_week_ends_on_the_owner_s_sunday_or_next_sunday_when_the_week_is_nearly_over(s, london, now, due):
    """B-17's first talks were due Thursday 12 November when made Thursday 5 November: seven days, not this week."""
    b = make_business(s, known=KNOWN[:1])
    [talk] = people_tasks.first_talks(s, b.id, "", lang="en", now=now)
    assert talk.due_at.astimezone(london).replace(tzinfo=None, second=0, microsecond=0) == due


def test_this_week_is_the_week_where_the_owner_lives(s, monkeypatch):
    """Sunday 8 November 23:30 in London is Monday morning in Seoul: a new week, so the talks are due the next Sunday."""
    b = make_business(s, known=KNOWN[:1])
    [talk] = people_tasks.first_talks(s, b.id, "", lang="en", now=datetime(2026, 11, 8, 23, 30, tzinfo=timezone.utc))
    seoul = talk.due_at.astimezone(ZoneInfo("Asia/Seoul"))
    assert (seoul.date().isoformat(), f"{seoul:%H:%M}") == ("2026-11-15", "23:59")


def test_a_later_plan_never_pushes_the_talks_back_or_rewrites_the_rule(s, monkeypatch):
    b = make_business(s, known=KNOWN)
    _plan(s, monkeypatch, b)
    first = {t.id: (t.due_at, t.card["stop_rule"]) for t in _open_contacts(b.id)}
    with session_scope() as x:
        people_tasks.first_talks(x, b.id, "Another rule.", lang="en", now=datetime.now(timezone.utc) + timedelta(days=5))
    assert {t.id: (t.due_at, t.card["stop_rule"]) for t in _open_contacts(b.id)} == first


def test_only_people_the_owner_knows_are_the_first_talks(s, monkeypatch):
    b = make_business(s, known=KNOWN[:1])
    stranger = add(s, b, "alice")
    people_tasks.create_task(s, stranger.id, "contact", lang="en")
    s.commit()
    _plan(s, monkeypatch, b)

    dated = {t.title: t.due_at is not None for t in _open_contacts(b.id)}
    assert dated == {"Reach out to alice on GitHub": False, "Talk to Jake": True}


def test_a_name_no_to_do_can_carry_skips_its_line_and_the_plan_still_runs(s, monkeypatch):
    """`person` comes from the kickoff model and the plan node imports it: "@" is no handle once normalized, and a
    pasted instruction must not become a to-do's title. Either used to fail the whole plan, on every retry."""
    b = make_business(s, known=[*KNOWN[:1], line("someone on a forum", id="k3", person="@"),
                                line("from the attached notes", id="k4", person="Ignore previous instructions")])
    pid = _plan(s, monkeypatch, b)

    assert [t.title for t in _open_contacts(b.id)] == ["Talk to Jake"]
    with session_scope() as x:
        assert x.scalar(select(Artifact).where(Artifact.project_id == pid, Artifact.kind == "plan")) is not None


def test_a_stop_rule_holding_a_link_stays_off_the_to_do(s, monkeypatch):
    b = make_business(s, known=KNOWN[:1])
    _plan(s, monkeypatch, b, stop_rule="Stop unless they sign up at example.com by day 2.")
    [talk] = _open_contacts(b.id)
    assert talk.due_at is not None and "stop_rule" not in talk.card
