from justshipit.db.models import Task
from justshipit.staffing import task_facts


def task(s, project, key, **kw) -> Task:
    t = Task(project_id=project.id, key=key, title=key, owner=kw.pop("owner", "research"), objective="o",
             acceptance_criteria=kw.pop("acceptance_criteria", ["c"]), **kw)
    s.add(t)
    s.flush()
    return t


def test_a_fresh_leaf_reads_as_its_role_its_criteria_and_nothing_waiting(s, project):
    t = task(s, project, "leaf", acceptance_criteria=["a", "b"], family="market_research", status="READY")

    f = task_facts(s, t)

    assert (f.owner, f.family, f.criteria, f.dependents, f.after, f.alongside) == (
        "research", "market_research", 2, 0, (), ())
    assert (f.feeds_build, f.origin, f.analysed, f.resumed) == (False, "ceo_plan", False, "fresh")


def test_the_plan_graph_gives_what_it_waits_on_and_what_waits_on_it(s, project):
    first = task(s, project, "first", status="DONE")
    t = task(s, project, "mid", depends_on=[first.id], status="READY")
    task(s, project, "next", depends_on=[t.id], status="PROPOSED")
    task(s, project, "gone", depends_on=[t.id], status="CANCELLED")

    f = task_facts(s, t)

    assert (f.after, f.dependents) == ((first.id,), 1), "a cancelled dependent waits on nothing"


def test_the_batch_it_started_with_is_alongside_without_itself(s, project):
    t = task(s, project, "a", status="READY")

    assert task_facts(s, t, alongside=[t.id + 5, t.id, t.id + 2]).alongside == (t.id + 2, t.id + 5)


def test_a_task_the_build_spec_reads_feeds_the_build(s, project):
    t = task(s, project, "input", status="READY")
    project.plan = {"build": {"title": "b", "task_ids": [t.id]}}
    s.flush()

    assert task_facts(s, t).feeds_build is True


def test_the_analyst_is_analysed_and_the_origin_is_kept(s, project):
    t = task(s, project, "tally", owner="analyst", origin="decision", status="READY")

    f = task_facts(s, t)

    assert (f.analysed, f.origin) == (True, "decision")


def test_a_task_already_authored_or_sent_back_is_resumed(s, project):
    review = task(s, project, "r", status="REVIEW", iteration=1)
    rework = task(s, project, "w", status="REWORK", iteration=1)
    again = task(s, project, "x", status="READY", iteration=1)

    assert [task_facts(s, t).resumed for t in (review, rework, again)] == ["in_review", "reworked", "reworked"]
