"""What the owner is good at, and how the company fits its work to it.

The owner picks their strengths once (Settings > About you, first asked on the first new business). Where they are
strong the company asks deeper, shows the real detail, leaves choices open and hands them the work; where they are
not, it does more itself, explains plainly and recommends one way. Every agent call reads this table
(`prompt_section`, appended by `agents.executor`), and each screen that shows detail reads `fit`: the decision card
(`services.requests`), the task page (by the agent that owns the task) and the final report (by section,
`report_fit`).

`None` means the owner was never asked, and nothing changes. An empty list is the owner's answer "none of these yet",
so every area is one they are new to.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from justshipit.i18n import LANGS, msg

Strength = Literal["software", "marketing", "design", "operations"]
AREAS: tuple[Strength, ...] = get_args(Strength)
Strengths = list[Strength] | None


@dataclass(frozen=True)
class Area:
    name: str
    strong: str
    weak: str


# Agent-facing, English like the rest of what agents read. A line says what to ask, what to show and what work to
# give the owner; examples are the evidence from persona round 3 (Alex, 박정숙, Maya).
TABLE: dict[Strength, Area] = {
    "software": Area(
        "building software",
        strong="ask what they have built and where they like to host (when the product already exists, its code "
               "report answers the stack: ask only what the owner knows beyond it); write the technical part of the "
               "spec and of your summaries in real technical terms (the console shows the branch and changed files "
               "itself); offer them a build task or the changes to review.",
        weak="pick the stack and hosting yourself and say why in one line; never ask them a technical question; say "
             "what the software does for people, and say branch, merge or deploy in plain words when you must."),
    "marketing": Area(
        "marketing and sales",
        strong="ask which channels they have run, what a customer cost them and which words worked; show channel "
               "numbers and copy variants; give them the outreach and the copy to write or rewrite.",
        weak="choose the channels and draft every message yourself; their part is step-by-step to-dos (where to go, "
             "what to paste, what to record), each with a one-line why."),
    "design": Area(
        "design",
        strong="ask which products' look they have used and liked, and what they changed in a design they made; show "
               "the screens and the layout choices with their trade-offs; give them screens to sketch or review.",
        weak="choose a clean, standard look yourself and show the screens; ask them only whether a screen works."),
    "operations": Area(
        "running a business and knowing the trade",
        strong="ask how their trade works: suppliers, prices, margins, seasons and rules; show costs and margins; "
               "give them the calls with suppliers, partners and the people they already know.",
        weak="look up how the trade works yourself and explain it in a sentence; propose prices and suppliers with "
             "one line of why."),
}

_HOW = ("Fit every question, plan, card, report and to-do to this:\n"
        "- In a strong area, go deeper: ask specific questions, show the real detail (never internal ids), leave real "
        "choices open with their trade-offs, and give the owner the work they are good at.\n"
        "- In an area they are new to, the company does the work: ask fewer questions and offer a recommended default "
        "with each (in the kickoff, the default is the first example answer), write the conclusion and the next step in plain words with the detail after, recommend one option "
        "with a one-line why, and make the owner's part step-by-step to-dos.")


def known(strengths: Strengths) -> list[Strength]:
    """The owner's strengths in the table's order, unknown codes dropped."""
    return [a for a in AREAS if a in (strengths or [])]


def prompt_section(strengths: Strengths) -> str:
    """The section every agent call reads, or "" when the owner was never asked."""
    if strengths is None:
        return ""
    strong = known(strengths)
    weak = [a for a in AREAS if a not in strong]
    said = (f"The owner said they are strong in {', '.join(TABLE[a].name for a in strong)}." if strong
            else "The owner said they are new to all of these areas.")
    lines = [f"## What the owner is good at\n{said} {_HOW}"]
    lines += [f"- Strong, {TABLE[a].name}: {TABLE[a].strong}" for a in strong]
    lines += [f"- New to {TABLE[a].name}: {TABLE[a].weak}" for a in weak]
    return "\n".join(lines)


# The areas a decision card belongs to, by what approving does. A merge shows code and screens; a spec, what will be
# built and how it looks.
_BY_ACTION: dict[str, tuple[Strength, ...]] = {
    "dev.merge": ("software", "design"),
    "dev.spec_approve": ("software", "design"),
    "dev.pull_request_create": ("software",),
    "dev.deploy_production": ("software",),
    "skill.activate_executable": ("software",),
    "external.social_post": ("marketing",),
    "external.email_send": ("marketing",),
    "external.survey_distribute": ("marketing",),
    "external.ads_run": ("marketing",),
    "external.interview_outreach": ("marketing",),
    "external.landing_publish": ("marketing", "design"),
    "crm.contact_upsert": ("marketing",),
    "budget.spend": ("operations",),
    "budget.raise": ("operations",),
    "data.purchase": ("operations",),
    "external.contract_sign": ("operations",),
    "external.legal_consult": ("operations",),
    "external.official_inquiry": ("operations",),
}

# A card whose action names no area belongs to the area of the agent that raised it, and a task to the agent that owns
# it: market research is about customers and channels, the risk review about the rules of the trade.
_BY_AGENT: dict[str, tuple[Strength, ...]] = {
    "product": ("software",), "engineer": ("software",), "eng_reviewer": ("software",),
    "marketing": ("marketing",), "research": ("marketing",),
    "finance": ("operations",), "risk": ("operations",),
}


def card_areas(action_kind: str | None, from_agent: str | None) -> tuple[Strength, ...]:
    if action_kind in _BY_ACTION:
        return _BY_ACTION[action_kind]
    if (action_kind or "").startswith("dev."):
        return ("software",)
    return _BY_AGENT.get(from_agent or "", ())


def fit(areas: tuple[Strength, ...], strengths: Strengths) -> dict[str, list[Strength]]:
    """How a card about `areas` reads for this owner: `strong` areas show their detail open, `weak` ones lead with
    the conclusion and fold the detail. Both are empty when the owner was never asked or the card has no area, and
    the card reads as it always did."""
    if strengths is None:
        return {"strong": [], "weak": []}
    mine = known(strengths)
    return {"strong": [a for a in areas if a in mine], "weak": [a for a in areas if a not in mine]}


# The final report's own sections, by the heading the server writes in either language: the plan to check with
# customers is outreach, and what the merged build does is software. The CEO tags the sections it writes
# (FinalReportOut.section_areas).
_REPORT_SECTIONS: dict[str, tuple[Strength, ...]] = {"work.doc.report.validation": ("marketing",),
                                                     "build.report.heading": ("software",)}


def report_fit(tagged: dict[str, str], strengths: Strengths) -> dict[str, dict[str, list[Strength]]]:
    """`fit` for each section of the final report that belongs to an area, by its heading; a section left out reads as
    it always did. `tagged` is the CEO's heading-to-area tags the project keeps (`report_areas`)."""
    areas = {msg(key, lang): a for key, a in _REPORT_SECTIONS.items() for lang in LANGS}
    areas |= {heading: (area,) for heading, area in tagged.items() if area in AREAS}
    return {heading: fit(a, strengths) for heading, a in areas.items()}
