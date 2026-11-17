"""The customer description as data, and the rules that read it. Pure: no session, no clock, no catalog.

A business's live rows (`store.live_rows`) become a `Profile` of three `LineView`s. A line with no live rows was never
asked and reads as unknown. `fit` is the one match rule every reader uses: a place or channel declares the codes it
suits per line, and the description either fits, misses, or cannot say yet.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from justshipit.services.profile.vocab import LINES, Line, Provenance

# The plan sections the description is read from. A change to one of their lines may change what the description should
# say, so it makes the rows written against an older plan stale.
WATCHED_SECTIONS = ("one_liner", "customer_problem", "path", "known_people", "owner_time", "budget_usage",
                    "background")

# The codes a place declares it suits, per line. A line left out means any.
Fits = Mapping[Line, Sequence[str]]

_STRENGTH: dict[Provenance, int] = {"unknown": -1, "guess": 0, "said": 1, "confirmed": 2}


@dataclass(frozen=True)
class Value:
    """A live row: one code, or the line's sentence (code None), or its unknown marker."""
    row_id: int
    line: Line
    code: str | None
    sentence: str | None
    provenance: Provenance
    source: str
    quote: str | None
    brief_id: int | None
    written_by: str
    written_at: datetime


@dataclass(frozen=True)
class LineView:
    line: Line
    values: tuple[Value, ...] = ()

    @property
    def known(self) -> bool:
        return any(v.provenance != "unknown" for v in self.values)

    @property
    def asked(self) -> bool:
        """Whether the line has any live row, the unknown marker included."""
        return bool(self.values)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(v.code for v in self.values if v.code is not None and v.provenance != "unknown")

    @property
    def sentence(self) -> str | None:
        return next((v.sentence for v in self.values if v.sentence is not None and v.provenance != "unknown"), None)

    @property
    def weakest(self) -> Provenance:
        """The provenance the row shows: unknown when nothing is known, else the lowest of guess, said, confirmed."""
        known = [v.provenance for v in self.values if v.provenance != "unknown"]
        return min(known, key=_STRENGTH.__getitem__) if known else "unknown"


@dataclass(frozen=True)
class Profile:
    business_id: int
    lines: Mapping[Line, LineView]

    def has(self, line: Line, code: str) -> bool:
        """True for any live value that is not unknown, a guess included."""
        return code in self.lines[line].codes

    def confirmable(self, line: Line) -> bool:
        """Whether the line holds a value the owner has not stood behind yet: a guess, or a `said` value the CEO read
        from a quote. **Right** turns each into the owner's confirmed value."""
        return any(v.provenance == "guess" or (v.provenance == "said" and v.written_by == "project_manager")
                   for v in self.lines[line].values)

    def guessed(self, line: Line) -> bool:
        """Whether any known value on the line is only the CEO's guess, so the owner can still say it is right."""
        return any(v.provenance == "guess" for v in self.lines[line].values)


def assemble(business_id: int, rows: Iterable[Value]) -> Profile:
    by_line: dict[Line, list[Value]] = {line: [] for line in LINES}
    for row in sorted(rows, key=lambda v: v.row_id):
        by_line[row.line].append(row)
    return Profile(business_id, {line: LineView(line, tuple(values)) for line, values in by_line.items()})


@dataclass(frozen=True)
class Fit:
    """`basis` is the codes that matched, per declared line. `guessed` is the declared lines whose matching values are
    all guesses, which a reader marks for the owner to check."""
    basis: Mapping[Line, tuple[str, ...]]
    guessed: tuple[Line, ...] = ()


@dataclass(frozen=True)
class Unsure:
    """No declared line misses, but these declared lines are unknown, so the match waits."""
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class Miss:
    pass


def _matching(fits: Fits, prof: Profile) -> dict[Line, list[Value]]:
    return {line: [v for v in prof.lines[line].values if v.provenance != "unknown" and v.code in codes]
            for line, codes in fits.items()}


def fit(fits: Fits, prof: Profile) -> Fit | Unsure | Miss:
    """A line left out of `fits` means any. The description fits when every declared line shares a code with it, misses
    when a declared line is known and shares none, and otherwise is unsure about the declared lines it does not know."""
    matching = _matching(fits, prof)
    unsure = tuple(line for line in fits if not prof.lines[line].known)
    if any(not matching[line] for line in fits if line not in unsure):
        return Miss()
    if unsure:
        return Unsure(unsure)
    return Fit({line: tuple(v.code for v in values if v.code) for line, values in matching.items()},
               tuple(line for line, values in matching.items() if all(v.provenance == "guess" for v in values)))


def _owners(v: Value) -> bool:
    """A value the owner stands behind: one they confirmed, or words they typed themselves. A `said` value the CEO
    wrote rests on its reading of a quote, so it does not count."""
    return v.provenance == "confirmed" or (v.provenance == "said" and v.written_by == "owner")


def firm(fits: Fits, prof: Profile) -> bool:
    """Whether every declared line's matching values are the owner's own (`_owners`): what a launch that happens once
    waits for."""
    return all(values and all(_owners(v) for v in values) for values in _matching(fits, prof).values())


def launch_ready(fits: Fits, prof: Profile) -> bool:
    """Whether a launch that happens once may go: the owner has confirmed who buys, whatever the place declares, and
    `firm` holds for what it does declare. A guess about the buyer wastes a launch, so a place that names only how
    people are reached still waits on it."""
    buyer = prof.lines["buyer"]
    return (buyer.known and all(_owners(v) for v in buyer.values if v.provenance != "unknown")
            and firm(fits, prof))


def stale(prof: Profile, plan_lines_by_brief: Mapping[int, tuple[str, ...]], current_brief_id: int | None
          ) -> frozenset[Line]:
    """The lines written against a plan whose watched lines (`WATCHED_SECTIONS`) differ from the current plan's. A row
    with no brief is never stale, and nothing is stale while there is no current plan. A row's plan the caller did not
    send counts as changed, since nothing says it did not."""
    if current_brief_id is None or current_brief_id not in plan_lines_by_brief:
        return frozenset()
    now = plan_lines_by_brief[current_brief_id]
    return frozenset(line for line, view in prof.lines.items()
                     if any(v.brief_id is not None and plan_lines_by_brief.get(v.brief_id) != now
                            for v in view.values))
