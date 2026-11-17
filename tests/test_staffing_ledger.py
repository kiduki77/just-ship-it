"""The crew ledger: one crew per subject and one turn per ledger call, however many times a retry, a checkpoint replay
or a second caller writes them."""
from dataclasses import replace

import pytest
from justshipit.contracts.handoff import AcceptanceOut, CodeFindingOut, CodeReviewOut, CriterionResultOut
from justshipit.contracts.work import ReviewOut, ReviewVerdict
from justshipit.db.models import CREW_DUTIES, CREW_SUBJECT_KINDS, Crew, CrewTurn
from justshipit.db.session import session_scope
from justshipit.staffing import (
    AcceptanceTurn,
    AuthorTurn,
    BuildReviewTurn,
    BuildTurn,
    Duty,
    JobFacts,
    Limits,
    Reason,
    Slot,
    SubjectKind,
    VerifierTurn,
    amend,
    ensure,
    record_turn,
)
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

LIMITS = Limits(always_check=False, solo_max_criteria=1, audit_rate=0.2, audit_budget_usd=2.0, min_samples=20,
                solo_max_unmet_rate=0.1, model_tolerance=0.05, explore_rate=0.1)
LEAF = JobFacts(owner="research", family="market_research", criteria=1, dependents=0, after=(3,), alongside=(),
                feeds_build=False, origin="ceo_plan", analysed=False, resumed="fresh")
BUILD = replace(LEAF, kind=SubjectKind.BUILD, owner="engineer", family="build", criteria=4, after=())
SUBJECT = 910_001  # no task or build has this id: a crew only names its subject


@pytest.fixture(autouse=True)
def clean():
    def wipe():
        with session_scope() as s:
            s.execute(delete(CrewTurn))
            s.execute(delete(Crew))
    wipe()
    yield
    wipe()


def author(call_id="c1", **kw):
    return AuthorTurn(role="research", call_id=call_id, iteration=1, summary="s", findings_markdown="f", assumptions=[],
                      risks=[], open_questions=[], unmet_criteria=[], evidence_ids=[7], **kw)


def review():
    return ReviewOut(verdict=ReviewVerdict.PASS, criteria_met=["a"], criteria_unmet=[], claim_checks=[],
                     counter_arguments=[], rework_instructions=None, contested=False)


# ------------------------------------------------------------------ ensure

def test_ensure_twice_leaves_one_crew_and_the_first_decision(s):
    first = ensure(s, SUBJECT, LEAF, LIMITS)
    again = ensure(s, SUBJECT, replace(LEAF, criteria=9, feeds_build=True), LIMITS)

    assert first.id == again.id
    assert s.scalar(select(func.count()).select_from(Crew)) == 1
    assert [x["rule"] for x in again.why] == ["solo.small_leaf"], "the rule is not rerun on a later call"


def test_ensure_from_two_transactions_leaves_one_crew():
    with session_scope() as one:
        ids = [ensure(one, SUBJECT, LEAF, LIMITS).id]
    with session_scope() as two:
        ids.append(ensure(two, SUBJECT, LEAF, LIMITS).id)

    assert ids[0] == ids[1]


def test_a_crew_keeps_the_placement_the_facts_and_the_version_that_made_it(s):
    crew = ensure(s, SUBJECT, replace(LEAF, feeds_build=True), LIMITS)

    s.expire_all()
    crew = s.get(Crew, crew.id)
    assert (crew.subject_kind, crew.subject_id, crew.family, crew.rules_version) == ("task", SUBJECT, "market_research", "2")
    assert crew.slots == [{"duty": "author", "role": "research", "model": None},
                          {"duty": "verifier", "role": "risk", "model": None}]
    assert crew.why == [{"rule": "check.feeds_build", "facts": {}}]
    assert crew.facts["feeds_build"] is True and crew.facts["after"] == [3] and crew.facts["kind"] == "task"
    assert crew.amendments == []


def test_a_task_and_a_build_with_the_same_id_have_a_crew_each(s):
    task, build = ensure(s, SUBJECT, LEAF, LIMITS), ensure(s, SUBJECT, BUILD, LIMITS)

    assert task.id != build.id
    assert [x["role"] for x in build.slots] == ["engineer", "eng_reviewer", "product"]
    assert build.why == [{"rule": "fixed.build", "facts": {}}]


def test_the_column_checks_match_the_enums_the_rule_uses():
    assert CREW_SUBJECT_KINDS == tuple(k.value for k in SubjectKind)
    assert CREW_DUTIES == tuple(d.value for d in Duty)


def test_the_database_refuses_an_unknown_subject_kind_and_duty(s):
    crew = ensure(s, SUBJECT, LEAF, LIMITS)

    with pytest.raises(IntegrityError), s.begin_nested():
        s.execute(text("UPDATE crews SET subject_kind = 'idea' WHERE id = :i"), {"i": crew.id})
    with pytest.raises(IntegrityError), s.begin_nested():
        s.execute(text("INSERT INTO crew_turns (crew_id, call_id, duty, role, round, iteration, shadow, record) "
                       "VALUES (:i, 'x', 'boss', 'r', 0, 0, false, '{}')"), {"i": crew.id})


# ------------------------------------------------------------------ amend

def test_amend_only_adds_a_slot_and_leaves_the_decision_as_made(s):
    crew = ensure(s, SUBJECT, LEAF, LIMITS)
    checker = Slot(Duty.VERIFIER, "risk", None)
    why = Reason("check.owner_added", {})

    amend(s, crew, checker, why)

    s.expire_all()
    crew = s.get(Crew, crew.id)
    assert crew.slots == [{"duty": "author", "role": "research", "model": None}]
    assert [(a["slot"]["role"], a["why"]["rule"]) for a in crew.amendments] == [("risk", "check.owner_added")]
    assert crew.amendments[0]["at"]


def test_amending_the_same_slot_again_changes_nothing(s):
    crew = ensure(s, SUBJECT, LEAF, LIMITS)
    checker = Slot(Duty.VERIFIER, "risk", None)

    amend(s, crew, checker, Reason("check.owner_added", {}))
    amend(s, crew, checker, Reason("check.owner_added", {}))
    amend(s, crew, Slot(Duty.AUTHOR, "research", None), Reason("check.config", {}))

    assert len(crew.amendments) == 1, "a role that already holds the duty is not added twice"


# ------------------------------------------------------------------ record_turn

def test_one_call_recorded_twice_leaves_one_turn(s):
    crew = ensure(s, SUBJECT, LEAF, LIMITS)

    first = record_turn(s, crew, author("call-a"))
    again = record_turn(s, crew, author("call-a"))

    assert first.id == again.id
    assert s.scalar(select(func.count()).select_from(CrewTurn)) == 1


def test_two_calls_of_one_iteration_are_two_turns_and_a_build_may_repeat_its_numbers(s):
    crew = ensure(s, SUBJECT, BUILD, LIMITS)
    eng = dict(role="engineer", round=1, iteration=0, summary="s", decisions=["d"])

    record_turn(s, crew, BuildTurn(call_id="build-1", stage="review", commit="abc", **eng))
    record_turn(s, crew, BuildTurn(call_id="fix-1", stage="fix", **eng))

    rows = s.scalars(select(CrewTurn).order_by(CrewTurn.id)).all()
    assert [(r.call_id, r.round, r.iteration, r.duty, r.role) for r in rows] == [
        ("build-1", 1, 0, "author", "engineer"), ("fix-1", 1, 0, "author", "engineer")]


def test_a_turn_keeps_its_text_outside_the_columns_that_describe_it(s):
    crew = ensure(s, SUBJECT, LEAF, LIMITS)

    row = record_turn(s, crew, author("call-a", fixing="cite your sources"))

    assert (row.crew_id, row.duty, row.role, row.round, row.iteration, row.shadow) == (
        crew.id, "author", "research", 0, 1, False)
    assert row.record == {"fixing": "cite your sources", "summary": "s", "findings_markdown": "f", "assumptions": [],
                          "risks": [], "open_questions": [], "unmet_criteria": [], "evidence_ids": [7]}


def test_every_turn_type_is_kept_whole(s):
    task, build = ensure(s, SUBJECT, LEAF, LIMITS), ensure(s, SUBJECT, BUILD, LIMITS)
    finding = CodeFindingOut(severity="major", file="a.py", location=None, issue="i", suggestion="s")
    code = CodeReviewOut(verdict="fix_required", summary="s", spec_coverage=["REQ-1: built"], findings=[finding])
    accepted = AcceptanceOut(accepted=True, criteria_results=[CriterionResultOut(criterion="c", met=True, evidence="e")],
                             notes="n")

    verdict = record_turn(s, task, VerifierTurn(role="risk", call_id="v1", iteration=1, review=review(),
                                                independence="review by risk"))
    code_turn = record_turn(s, build, BuildReviewTurn(role="eng_reviewer", call_id="r1", round=2, review=code))
    accept = record_turn(s, build, AcceptanceTurn(role="product", call_id="a1", round=2, acceptance=accepted))

    assert verdict.duty == "verifier" and verdict.record["review"]["verdict"] == "pass"
    assert code_turn.record["review"]["findings"][0]["issue"] == "i", "the whole findings list is kept"
    assert accept.record["acceptance"]["criteria_results"][0]["met"] is True and accept.round == 2


def test_a_shadow_check_is_marked_and_does_not_collide_with_the_authors_call(s):
    crew = ensure(s, SUBJECT, LEAF, LIMITS)
    record_turn(s, crew, author("call-a"))

    shadow = record_turn(s, crew, VerifierTurn(role="risk", call_id="call-b", iteration=1, review=review(),
                                               independence="x"), shadow=True)

    assert shadow.shadow is True
    assert s.scalar(select(func.count()).select_from(CrewTurn)) == 2


def test_a_turn_refuses_what_it_does_not_declare():
    with pytest.raises(ValueError):
        author(unexpected="x")
