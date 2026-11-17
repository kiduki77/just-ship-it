"""The program's done sentence for a business, in the words of the owner's plan.

The goals are the numbers the approved plan's goal-and-deadline lines state (a said or confirmed line only). A line
with a typed `target` (written by the kickoff, or by the owner's answer to a card that amended the line) is one goal,
its number, unit and step as typed. A line without one is read for its numbers, one per clause, each with the plan's
own unit ("3곳", "30명", "12 users"). When the clause counts people (명, 곳, users, teams...) and names a step of the
people list, the goal is counted from that list: people who used it (got value), used it again (came back), were
spoken with, or were contacted, and "그중 N명과 대화" or "talk to N of them" after a came-back goal counts the repeat
users the owner spoke with. A goal the list cannot count (paid conversions, reply time, revenue) stays in the sentence
for the owner to check, and the sentence is not done while one is open. A plan whose goal lines state no number the
company can read has its first goal line as the goal, with no number, for the owner to check. Only a business with no
approved goal line gets the company's default, 10 outside users who used it twice and 5 of them spoken with, and says
so. The deadline phrase of a goal line ("6주 안에", "in six weeks") is the experiment window's
(`roadmap.deadline_days`), not a goal. The words come from `i18n.match.goals`, every language at once, since a line
keeps the language it was written in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.orm import Session

from justshipit.contracts.goal import Measure
from justshipit.db.models import Business
from justshipit.i18n import Lang, msg
from justshipit.i18n.match.goals import CLAUSE, MEASURES, OF_THEM, UNITS, WHO
from justshipit.services import people_link, roadmap
from justshipit.services.control import ControlError

TargetsFrom = Literal["brief", "default"]


@dataclass(frozen=True)
class Goal:
    text: str
    number: int | None
    unit: str
    measure: Measure | None


def default_goals(lang: Lang) -> tuple[Goal, ...]:
    unit = msg("roadmap.done.default_unit", lang)
    return (Goal(msg("roadmap.done.default_repeat", lang), 10, unit, "repeat"),
            Goal(msg("roadmap.done.default_talked", lang), 5, unit, "repeat_talked"))


def _alternation(words) -> str:
    return "|".join(sorted(map(re.escape, words), key=len, reverse=True))


_CLAUSE = re.compile(r"[,，;/\n]|" + "|".join(CLAUSE.values()), re.IGNORECASE)
_QTY = (re.compile(rf"(\d[\d,]*)\s*({_alternation(UNITS['ko'])})"),
        re.compile(rf"(\d[\d,]*)\s*(?:[a-z-]+\s+){{0,2}}?({_alternation(UNITS['en'])})(?![a-z])", re.IGNORECASE))
_NUMBER = re.compile(r"\d[\d,]*")
_WHO = frozenset(u.lower() for units in WHO.values() for u in units)
_MEASURES: tuple[tuple[Measure | None, re.Pattern[str]], ...] = tuple(
    (ko[0], re.compile(f"{ko[1]}|{en[1]}", re.IGNORECASE)) for ko, en in zip(MEASURES["ko"], MEASURES["en"], strict=True))
_OF_THEM = re.compile("|".join(OF_THEM.values()), re.IGNORECASE)


def _goal(clause: str, previous: Goal | None) -> Goal | None:
    body = roadmap.DURATION.sub(" ", re.sub(r"\([^)]*\)", " ", clause))
    of_them = _OF_THEM.search(body) is not None
    found = [(int(n.replace(",", "")), re.sub(r"\s+", "", u)) for qty in _QTY for n, u in qty.findall(body)]
    if not found and of_them and previous is not None:
        found = [(int(n.replace(",", "")), previous.unit) for n in _NUMBER.findall(body)]
    if not found:
        return None
    number, unit = next((q for q in found if q[1].lower() in _WHO), found[0])
    measure = next((m for m, p in _MEASURES if p.search(body)), None) if unit.lower() in _WHO else None
    if measure == "talked" and of_them and previous is not None and previous.measure == "repeat":
        measure = "repeat_talked"
    return Goal(clause, number, unit, measure) if number > 0 else None


def goals(s: Session, business_id: int, *, lang: Lang) -> tuple[tuple[Goal, ...], TargetsFrom]:
    """The goals and where they came from: `brief` when the approved plan has a said or confirmed goal line. Goals read
    from the plan keep its words; only the default is in `lang`."""
    brief = roadmap.approved_brief(s, business_id)
    out = plan_goals(roadmap.lines_by_key(brief.sections).get("goal_deadline", []) if brief else [])
    return (out, "brief") if out else (default_goals(lang), "default")


def plan_goals(goal_lines: list[dict]) -> tuple[Goal, ...]:
    """The goals a plan's goal-and-deadline lines state, read from its said and confirmed lines only."""
    lines = [x for x in goal_lines if x["provenance"] in roadmap.SETTLED]
    out: list[Goal] = []
    for line in lines:
        if t := line.get("target"):
            out.append(Goal(line["text"], t["number"], t["unit"], t["measure"]))
            continue
        for clause in (c.strip() for c in _CLAUSE.split(line["text"])):
            if clause and (g := _goal(clause, out[-1] if out else None)):
                out.append(g)
    if not out and lines:
        out.append(Goal(lines[0]["text"], None, "", None))
    return tuple(out)


def headline(goal_list: tuple[Goal, ...]) -> Goal:
    """The goal the overview shows: the first one the people list counts, else the first."""
    return next((g for g in goal_list if g.measure), goal_list[0])


def counts(s: Session, business_id: int) -> dict[str, int]:
    """What the people list counts for each measure, or nothing when there is no list."""
    people = people_link.provider()
    if not people.available:
        return {}
    used = people.used_twice(s, business_id)
    return {**people.north_star(s, business_id), "repeat": used.users, "repeat_talked": used.talked}


def check(s: Session, business_id: int, *, lang: Lang) -> dict[str, Any]:
    if s.get(Business, business_id) is None:
        raise ControlError(msg("roadmap.refuse.no_business", lang, id=business_id))
    goal_list, source = goals(s, business_id, lang=lang)
    counted = counts(s, business_id)
    rows = [{"text": g.text, "number": g.number, "unit": g.unit, "measure": g.measure,
             "current": counted.get(g.measure) if g.measure else None} for g in goal_list]
    missing = [] if counted else [msg("roadmap.done.no_people_list", lang)]
    return {"done": all(r["current"] is not None and r["current"] >= r["number"] for r in rows),
            "sentence": ", ".join(g.text for g in goal_list), "targets_from": source, "goals": rows,
            "headline": goal_list.index(headline(goal_list)), "used_twice": counted.get("repeat", 0),
            "talked_of_them": counted.get("repeat_talked", 0), "missing": missing}
