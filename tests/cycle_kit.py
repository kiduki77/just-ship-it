"""Cycles and stages for tests, written as rows: a business inside a stage the owner started, without the CEO's
proposal. Tests that check how the cycle itself moves go through services/cycle.py instead."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from justshipit.contracts.cycle import CycleProposalOut, CycleRevisionOut, StageDraft
from justshipit.db.models import Business, Cycle, CycleStage, Project
from justshipit.services import cycle, gate, projects
from sqlalchemy import func, select

EVERYTHING = object()


def cycle_of(s, business: Business, *, state: str = "running") -> Cycle:
    """The business's open cycle, made when it has none."""
    c = s.scalar(select(Cycle).where(Cycle.business_id == business.id, Cycle.closed_at.is_(None)))
    if c is None:
        n = (s.scalar(select(func.max(Cycle.number)).where(Cycle.business_id == business.id)) or 0) + 1
        c = Cycle(business_id=business.id, number=n, starts_on=date.today(), ends_on=date.today() + timedelta(days=14),
                  state=state, mode="new", started_at=datetime.now(UTC))
        s.add(c)
        s.flush()
    return c


def stage(s, business: Business, *, status: str = "running", kinds=EVERYTHING, owner_work=EVERYTHING,
          key: str | None = None, title: str = "Validate") -> CycleStage:
    """A stage of the business's open cycle. By default it is running and lists every recurring kind and every kind of
    to-do, the world the tests written before the gate assume."""
    c = cycle_of(s, business)
    n = s.scalar(select(func.count()).select_from(CycleStage).where(CycleStage.cycle_id == c.id)) or 0
    st = CycleStage(cycle_id=c.id, business_id=business.id, key=key or f"s{n + 1}", position=n, title=title,
                    doing="Talk to five people and test the idea.", owner_does="Nothing", decision_points=[],
                    estimate_basis="guess", ends_when="run_settled",
                    allows_kinds=list(gate.RECURRING_KINDS if kinds is EVERYTHING else kinds),
                    allows_owner_work=list(gate.OWNER_WORK_KINDS if owner_work is EVERYTHING else owner_work),
                    adopted=False, status=status, round=1,
                    started_at=datetime.now(UTC) if status != "planned" else None)
    s.add(st)
    s.flush()
    return st


def draft(key: str, *, kinds=(), owner_work=(), ends_when="run_settled", listen_days=None, title=None,
          maps=()) -> StageDraft:
    return StageDraft(key=key, title=title or key.title(), doing=f"The company does {key}.", owner_does="Nothing",
                      decision_points=[], est_minutes=None, est_usd=None, estimate_basis="guess", ends_when=ends_when,
                      listen_days=listen_days, allows_kinds=list(kinds), allows_owner_work=list(owner_work),
                      experiment=None, maps_existing=[{"kind": k, "id": i} for k, i in maps])


def keys(*names: str) -> list[StageDraft]:
    return [draft(n) for n in names]


DEFAULT = ("define", "plan", "validate", "promote")


def proposal_out(stages=None, *, past=(), current=None) -> CycleProposalOut:
    return CycleProposalOut(intro="A first cycle to test the idea.", stages=stages or keys(*DEFAULT),
                            past_stage_keys=list(past), current_stage_key=current)


def revision_out(stages, why=()) -> CycleRevisionOut:
    return CycleRevisionOut(reply="Changed as you asked.", intro="A first cycle to test the idea.", stages=stages,
                            why=[{"stage_key": k, "why": w} for k, w in why])


def business(s, name: str = "사이클 사업") -> Business:
    """A business with an approved plan and no cycle yet: what Composer's Done finds."""
    from people_kit import make_business
    return make_business(s, name=name, in_stage=False)


def proposed(s, b: Business, stages=None, *, lang="en", **kw) -> Cycle:
    """Start pressed on the kickoff (start_business), and the CEO's first proposal stored."""
    c = projects.start_business(s, b.id, lang=lang)
    p = cycle.proposal(s, c.id)
    cycle.store_proposal(s, c.id, p.version, proposal_out(stages, **kw), dict(p.basis or {}), lang=lang)
    return c


def started(s, b: Business, stages=None, *, lang="en", **kw) -> Cycle:
    c = proposed(s, b, stages, lang=lang, **kw)
    cycle.decide(s, c.id, cycle.StartCycle(version=cycle.proposal(s, c.id).version), by="owner", lang=lang)
    return c


def version(s, cycle_id: int) -> int:
    """The proposal version a card shows: what StartCycle and StartNext carry back."""
    return cycle.proposal(s, cycle_id, status=("ready",)).version


def running(s, c: Cycle) -> CycleStage | None:
    return next((st for st in cycle.stages_of(s, c.id) if st.status == "running"), None)


def first_project(s, business_id: int, *, lang="ko") -> Project:
    """Composer's Done and the owner's Start on the CEO's first proposal: the project of the first stage."""
    c = cycle.open_cycle_of(s, business_id) or projects.start_business(s, business_id, lang=lang)
    if cycle.proposal(s, c.id, status=("ready",)) is None:
        p = cycle.proposal(s, c.id)
        cycle.store_proposal(s, c.id, p.version, proposal_out(), dict(p.basis or {}), lang=lang)
    if c.state == "draft":
        cycle.decide(s, c.id, cycle.StartCycle(version=cycle.proposal(s, c.id, status=("ready",)).version),
                     by="owner", lang=lang)
    return s.scalar(select(Project).where(Project.stage_id == running(s, c).id))
