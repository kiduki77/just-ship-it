"""A build's crew and its turns (docs/design/staffing-structure.md, sections 5 and 6, unit 6).

A build is staffed by the fixed chain (the engineer, `eng_reviewer`, the product role), so there is nothing to place
and its reason is `fixed.build`. The crew is made when the build starts working, and every later call that says
something the build's steps do not keep is a turn of it, keyed by the ledger call (`staffing.record_turn`).

The `staffing` package stays the one writer of both tables; this file only says what a build's facts are and where in
the build the calls are recorded.
"""
from __future__ import annotations

from justshipit import staffing
from justshipit.db.models import Crew

# A build has no dependents, criteria or earlier task of its own (it is one spec's build), so the facts the task rules
# read take neutral values. `origin` says what the subject is.
_FACTS = staffing.JobFacts(
    owner="engineer", family="build", criteria=0, dependents=0, after=(), alongside=(), feeds_build=False,
    origin="build", analysed=False, resumed="fresh", kind=staffing.SubjectKind.BUILD)


def crew(s, job_id: int) -> Crew:
    """The crew of build `job_id`. The first call makes it and every later call, a retry or a resume included, returns
    that row, so it can be asked for at the start and again wherever a turn is kept."""
    return staffing.ensure(s, job_id, _FACTS, staffing.load_limits())


def keep(s, job_id: int, turn: staffing.Turn) -> None:
    staffing.record_turn(s, crew(s, job_id), turn)
