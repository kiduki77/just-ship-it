"""The staffing rule: who works a job and why (docs/design/staffing-structure.md, sections 5, 6 and 7).

Pure. No database, no model call, no clock. `place` reads the facts of a job and the limits and answers with the people
who do it and the reasons, one `Reason` for every rule that held. The rules are the `RULES` table below, not branches,
so a test can walk every row and the catalog can name every reason (`staffing.why.<rule id>`).

Version 2 also reads `History`, the statistics of the job's family, computed outside. History never touches a job a
forcing rule holds for, and below `Limits.min_samples` it changes nothing, so the placement is version 1's exactly.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, replace
from enum import StrEnum
from fractions import Fraction
from typing import Literal

# Bumped when a rule changes, so a stored decision says which rules made it.
RULES_VERSION = "2"


class SubjectKind(StrEnum):
    TASK = "task"
    BUILD = "dev_job"


class Duty(StrEnum):
    AUTHOR = "author"
    VERIFIER = "verifier"


class Flow(StrEnum):
    ALONE = "alone"
    AFTER = "after"
    ALONGSIDE = "alongside"


class Shape(StrEnum):
    CHECKED = "checked"
    SOLO = "solo"
    CHAIN = "chain"


@dataclass(frozen=True)
class Slot:
    duty: Duty
    role: str
    model: str | None  # None runs the role's configured model; version 2 names another when history chooses one


@dataclass(frozen=True)
class JobFacts:
    """What the rule read, stored in `crews.facts` so an old decision can be explained after the rule changes. A
    build has `owner="engineer"` and `family="build"`."""
    owner: str
    family: str
    criteria: int
    dependents: int
    after: tuple[int, ...]
    alongside: tuple[int, ...]
    feeds_build: bool
    origin: str
    analysed: bool
    resumed: Literal["fresh", "in_review", "reworked"]
    kind: SubjectKind = SubjectKind.TASK

    @property
    def flow(self) -> Flow:
        return Flow.AFTER if self.after else Flow.ALONGSIDE if self.alongside else Flow.ALONE


@dataclass(frozen=True)
class Limits:
    """`config/staffing.yaml`'s `staffing` block. Version 1 of the rule reads the first two; the rest are read by the
    audit job and by version 2."""
    always_check: bool
    solo_max_criteria: int
    audit_rate: float
    audit_budget_usd: float
    min_samples: int
    solo_max_unmet_rate: float
    model_tolerance: float
    explore_rate: float
    # The models history may move a role to, cheapest first. Empty: history never changes a model.
    model_ladder: tuple[str, ...] = ()

    @classmethod
    def from_config(cls, cfg: Mapping[str, object]) -> Limits:
        if missing := sorted({f.name for f in fields(cls)} - set(cfg)):
            raise ValueError(f"staffing: config/staffing.yaml has no {', '.join(missing)}")
        limits = cls(always_check=_flag(cfg["always_check"]), solo_max_criteria=int(cfg["solo_max_criteria"]),
                     audit_rate=float(cfg["audit_rate"]), audit_budget_usd=float(cfg["audit_budget_usd"]),
                     min_samples=int(cfg["min_samples"]), solo_max_unmet_rate=float(cfg["solo_max_unmet_rate"]),
                     model_tolerance=float(cfg["model_tolerance"]), explore_rate=float(cfg["explore_rate"]),
                     model_ladder=tuple(_names(cfg["model_ladder"])))
        for name in ("audit_rate", "solo_max_unmet_rate", "model_tolerance", "explore_rate"):
            if not 0 <= getattr(limits, name) <= 1:
                raise ValueError(f"staffing.{name} must be between 0 and 1, got {getattr(limits, name)}")
        if limits.solo_max_criteria < 0 or limits.min_samples < 1 or limits.audit_budget_usd < 0:
            raise ValueError("staffing: solo_max_criteria and audit_budget_usd are not negative, min_samples is 1 or more")
        ladder = limits.model_ladder
        if not all(isinstance(m, str) and m for m in ladder) or len(set(ladder)) != len(ladder):
            raise ValueError(f"staffing.model_ladder lists distinct model names, got {list(ladder)}")
        return limits


def _flag(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"staffing.always_check must be true or false, got {value!r}")
    return value


def _names(value: object) -> list:
    if not isinstance(value, list):
        raise TypeError(f"staffing.model_ladder must be a list of model names, got {value!r}")
    return value


@dataclass(frozen=True)
class Tally:
    samples: int = 0
    unmet: int = 0   # of the samples, how many a check found an unmet criterion in

    def __add__(self, other: Tally) -> Tally:
        return Tally(self.samples + other.samples, self.unmet + other.unmet)


@dataclass(frozen=True)
class Stat:
    """What one role's tasks of one family came to on one model (`staffing.placement_stats`). `alone` counts the tasks
    a shadow check judged first (they ran alone), `checked` the tasks their own checker judged first."""
    family: str
    role: str
    model: str
    alone: Tally
    checked: Tally
    calls: int     # the tasks' own calls, shadow checks apart: cost is counted in calls, not dollars
    reworks: int   # author passes after the first

    @property
    def judged(self) -> Tally:
        return self.alone + self.checked


@dataclass(frozen=True)
class History:
    """What version 2 reads besides the facts. `draw` is the explore sample's draw, taken outside `place` because a
    draw is not pure: the job samples a cheaper model when it is under `Limits.explore_rate`."""
    stats: tuple[Stat, ...]
    configured: Mapping[str, str | None]   # each role's model on the roster
    draw: float = 1.0


@dataclass(frozen=True)
class Reason:
    rule: str  # also the catalog key: staffing.why.<rule>
    facts: dict[str, int | float | str]


@dataclass(frozen=True)
class Placement:
    slots: tuple[Slot, ...]
    why: tuple[Reason, ...]
    # True when the job ran alone, so a shadow check may follow. Whether this one is sampled (`Limits.audit_rate`) is
    # drawn where the check is queued, because a draw is not pure.
    audit: bool


@dataclass(frozen=True)
class Rule:
    id: str
    kind: SubjectKind
    shape: Shape
    holds: Callable[[JobFacts, Limits], bool]
    cite: Callable[[JobFacts, Limits], dict[str, int | str]]
    # A forcing check is one history never overrides: where one holds, version 2 places as version 1 does.
    forcing: bool = False


def verifier_role(owner: str) -> str:
    return "strategy" if owner == "risk" else "risk"


def fixed_path(owner: str) -> tuple[Slot, ...]:
    """What the path before staffing would have used, recorded for comparison only."""
    return Slot(Duty.AUTHOR, owner, None), Slot(Duty.VERIFIER, verifier_role(owner), None)


def _cites(*names: str) -> Callable[[JobFacts, Limits], dict[str, int | str]]:
    def read(f: JobFacts, limits: Limits) -> dict[str, int | str]:
        return {n: getattr(f, n) if hasattr(f, n) else getattr(limits, n) for n in names}
    return read


_NO_FACTS = _cites()


def _check(rule_id: str, holds: Callable[[JobFacts, Limits], bool],
           cite: Callable[[JobFacts, Limits], dict[str, int | str]] = _NO_FACTS, *, forcing: bool = True) -> Rule:
    return Rule(f"check.{rule_id}", SubjectKind.TASK, Shape.CHECKED, holds, cite, forcing)


_CHECKS: tuple[Rule, ...] = (
    _check("feeds_build", lambda f, _: f.feeds_build),
    _check("analysed", lambda f, _: f.analysed),
    _check("numbers", lambda f, _: f.owner == "finance"),
    _check("reviewer_role", lambda f, _: f.owner == "risk",
           lambda f, _: {"owner": f.owner, "checker": verifier_role(f.owner)}),
    _check("resumed", lambda f, _: f.resumed != "fresh"),
    _check("decision", lambda f, _: f.origin == "decision"),
    _check("owner_added", lambda f, _: f.origin == "owner"),
    # The two optional checks: a guess about difficulty that a family's shadow checks may show it does not need.
    _check("has_dependents", lambda f, _: f.dependents > 0, forcing=False),
    _check("criteria", lambda f, limits: f.criteria > limits.solo_max_criteria,
           _cites("criteria", "solo_max_criteria"), forcing=False),
    _check("config", lambda _, limits: limits.always_check),
)

RULES: tuple[Rule, ...] = (
    *_CHECKS,
    Rule("solo.small_leaf", SubjectKind.TASK, Shape.SOLO,
         lambda f, limits: not any(r.holds(f, limits) for r in _CHECKS), _cites("criteria")),
    Rule("fixed.build", SubjectKind.BUILD, Shape.CHAIN, lambda f, _: True, _NO_FACTS),
)

_BUILD_CHAIN = (Slot(Duty.AUTHOR, "engineer", None), Slot(Duty.VERIFIER, "eng_reviewer", None),
                Slot(Duty.VERIFIER, "product", None))

_SLOTS: Mapping[Shape, Callable[[JobFacts], tuple[Slot, ...]]] = {
    Shape.CHECKED: lambda f: fixed_path(f.owner),
    Shape.SOLO: lambda f: (Slot(Duty.AUTHOR, f.owner, None),),
    Shape.CHAIN: lambda _: _BUILD_CHAIN,
}
# Rules of one kind never hold in two shapes at once (`solo.small_leaf` holds only when no check does), so this order
# only picks the one that is there.
_SHAPE_ORDER = (Shape.CHAIN, Shape.CHECKED, Shape.SOLO)


# What history decided, each with its sentence `staffing.why.<id>` and the statistics it read in its facts.
HISTORY_SOLO = "history.solo"          # the family's shadow checks rarely find unmet work: the optional checks go
HISTORY_CHECKED = "history.checked"    # they find it too often: the family is checked
HISTORY_MODEL = "history.model"        # a cheaper model does about as well on the family
HISTORY_EXPLORE = "history.explore"    # this job, drawn at random, gives the cheaper model a sample
HISTORY_REASONS = (HISTORY_SOLO, HISTORY_CHECKED, HISTORY_MODEL, HISTORY_EXPLORE)


def place(f: JobFacts, limits: Limits, history: History | None = None) -> Placement:
    held = [r for r in RULES if r.kind is f.kind and r.holds(f, limits)]
    shape = next(s for s in _SHAPE_ORDER if any(r.shape is s for r in held))
    placed = Placement(slots=_SLOTS[shape](f), why=tuple(Reason(r.id, r.cite(f, limits)) for r in held),
                       audit=shape is Shape.SOLO)
    if history is None or f.kind is not SubjectKind.TASK or any(r.forcing for r in held):
        return placed
    stats = tuple(x for x in history.stats if x.family == f.family)
    return _by_model(f, _by_shadow_checks(f, placed, stats, limits), stats, history, limits)


def _by_shadow_checks(f: JobFacts, placed: Placement, stats: tuple[Stat, ...], limits: Limits) -> Placement:
    alone = sum((x.alone for x in stats), Tally())
    if alone.samples < limits.min_samples:
        return placed
    cited = {"samples": alone.samples, "unmet": alone.unmet, "unmet_pct": _pct(Fraction(alone.unmet, alone.samples)),
             "solo_max_unmet_rate": limits.solo_max_unmet_rate, "limit_pct": _pct(_exact(limits.solo_max_unmet_rate)),
             "min_samples": limits.min_samples}
    if Fraction(alone.unmet, alone.samples) >= _exact(limits.solo_max_unmet_rate):
        kept = tuple(r for r in placed.why if r.rule != "solo.small_leaf")
        return Placement(fixed_path(f.owner), (*kept, Reason(HISTORY_CHECKED, cited)), audit=False)
    if placed.audit:
        return placed
    waived = " ".join(r.rule for r in placed.why)
    return Placement((Slot(Duty.AUTHOR, f.owner, None),), (Reason(HISTORY_SOLO, {**cited, "waived": waived}),),
                     audit=True)


def _by_model(f: JobFacts, placed: Placement, stats: tuple[Stat, ...], history: History,
              limits: Limits) -> Placement:
    """Only ever lowers the author's model, and only between models that both have `min_samples` judged tasks of the
    family. Until the cheaper one has them, a drawn share of jobs runs on it to give it samples."""
    ladder, configured = limits.model_ladder, history.configured.get(f.owner)
    if configured not in ladder or not (cheaper := ladder[:ladder.index(configured)]):
        return placed
    on = {x.model: x for x in stats if x.role == f.owner}
    judged = {m: on[m].judged if m in on else Tally() for m in ladder}
    ready = [m for m in (*cheaper, configured) if judged[m].samples >= limits.min_samples]   # cheapest first
    if configured not in ready:
        return placed
    best = max(_met(judged[m]) for m in ready)
    pick = next(m for m in ready if _met(judged[m]) >= best - _exact(limits.model_tolerance))
    mine = {"configured": configured, "configured_samples": judged[configured].samples,
            "configured_unmet": judged[configured].unmet, "configured_pct": _pct(_met(judged[configured])),
            "min_samples": limits.min_samples}
    if pick != configured:
        why = Reason(HISTORY_MODEL, {"model": pick, "samples": judged[pick].samples, "unmet": judged[pick].unmet,
                                     "met_pct": _pct(_met(judged[pick])), "best_pct": _pct(best),
                                     "model_tolerance": limits.model_tolerance,
                                     "tolerance_pct": _pct(_exact(limits.model_tolerance)),
                                     "calls": on[pick].calls, "reworks": on[pick].reworks, **mine})
    elif cheaper[-1] not in ready and history.draw < limits.explore_rate:
        pick = cheaper[-1]
        why = Reason(HISTORY_EXPLORE, {"model": pick, "samples": judged[pick].samples, "draw": history.draw,
                                       "explore_rate": limits.explore_rate,
                                       "explore_pct": _pct(_exact(limits.explore_rate)), **mine})
    else:
        return placed
    author, *rest = placed.slots
    return Placement((replace(author, model=pick), *rest), (*placed.why, why), placed.audit)


def _exact(rate: float) -> Fraction:
    """A setting as the decimal the owner wrote, so a rate exactly at it compares as equal."""
    return Fraction(str(rate))


def _met(t: Tally) -> Fraction:
    return Fraction(t.samples - t.unmet, t.samples)


def _pct(rate: Fraction) -> int:
    return round(rate * 100)
