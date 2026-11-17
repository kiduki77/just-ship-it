"""A spec's user stories: prioritized, each testable alone, with Given/When/Then scenarios and the day nothing has
happened yet. The structure follows GitHub Spec Kit's spec template (MIT, Copyright GitHub, Inc.): P1 first, one
independent test per story, scenarios numbered under it.

The server numbers them, so the ids the engineer's script prints and the acceptance check reads are the server's:
stories US1, US2... in priority order, scenarios US1.1, US1.2... with the empty case last. `stories.yaml` beside the
spec holds them; the P1 scenarios are run by the branch's scripts/core-flow.sh at the acceptance check, and before the
merge card the stories are compared with what was built and what passed (`gaps`).
"""
from __future__ import annotations

import re

import yaml

from justshipit.contracts.handoff import AcceptanceOut, UserStoryOut
from justshipit.contracts.owner_text import one_line
from justshipit.i18n import Lang, msg
from justshipit.services import registry

FILE = "stories.yaml"
_ORDER = {"P1": 0, "P2": 1, "P3": 2}
# The line scripts/core-flow.sh prints for each P1 scenario it ran.
MARK = re.compile(r"^\[scenario (US\d+\.\d+)\] (pass|fail)\b", re.MULTILINE)


def ordered(stories: list[UserStoryOut]) -> list[tuple[str, UserStoryOut]]:
    """The stories with the ids the server gives them: US1, US2... by priority."""
    return [(f"US{n}", x) for n, x in enumerate(sorted(stories, key=lambda x: _ORDER[x.priority]), 1)]


def number(stories: list[UserStoryOut]) -> list[dict]:
    out = []
    for sid, x in ordered(stories):
        scenarios = [*({**sc.model_dump(), "empty": False} for sc in x.scenarios), {**x.empty_case.model_dump(), "empty": True}]
        out.append({"id": sid, "priority": x.priority, "title": x.title, "story": x.story,
                    "why_priority": x.why_priority, "independent_test": x.independent_test,
                    "scenarios": [{"id": f"{sid}.{i}", **sc} for i, sc in enumerate(scenarios, 1)]})
    return out


def dump(stories: list[dict]) -> str:
    return yaml.safe_dump({"stories": stories}, allow_unicode=True, sort_keys=False)


def at(registry_path: str, commit: str) -> list[dict]:
    """The stories as the spec's commit holds them; none for a spec from before stories."""
    try:
        data = yaml.safe_load(registry.read_at(f"{registry_path}/{FILE}", commit))
    except RuntimeError:
        return []
    return (data.get("stories") or []) if isinstance(data, dict) else []


def markdown(stories: list[dict], owner_data: str) -> str:
    """The prd's stories section, which the owner reads in the spec and the engineer builds from."""
    if not stories:
        return ""
    out = ["## User stories", ""]
    if owner_data.strip():
        out += ["### The owner's data", owner_data.strip(), ""]
    for x in stories:
        out += [f"### {x['id']} {x['title']} ({x['priority']})", x["story"], "",
                f"**Why {x['priority']}**: {x['why_priority']}", "",
                f"**Independent test**: {x['independent_test']}", "", "**Acceptance scenarios**:"]
        out += [f"{sc['id']}. {'(nothing has happened yet) ' if sc['empty'] else ''}**Given** {sc['given']}, "
                f"**When** {sc['when']}, **Then** {sc['then']}" for sc in x["scenarios"]]
        out.append("")
    return "\n".join(out)


def card(stories: list[dict]) -> list[dict]:
    """What the spec card shows of each story: its priority and its plain sentence."""
    return [{"id": x["id"], "priority": x["priority"], "says": x["story"]} for x in stories]


def p1_scenarios(stories: list[dict]) -> list[dict]:
    return [sc for x in stories if x["priority"] == "P1" for sc in x["scenarios"]]


def ran(log: str) -> dict[str, str]:
    """What the core-flow script said of each scenario: pass or fail. A scenario that failed on any input failed, even
    when a later input passed."""
    out: dict[str, str] = {}
    for m in MARK.finditer(log):
        out[m.group(1)] = "fail" if out.get(m.group(1)) == "fail" else m.group(2)
    return out


def p1_built(stories: list[dict], acceptance: AcceptanceOut) -> bool:
    """Whether the acceptance check found every P1 story built whole in the code."""
    checked = {c.story_id: c.built for c in acceptance.stories}
    return all(checked.get(x["id"]) == "yes" for x in stories if x["priority"] == "P1")


def _words(sc: dict) -> dict:
    """A scenario as the owner reads it: its own words, each part cut short. Its id is the script's, not his."""
    return {k: one_line(sc[k], _PART) for k in ("given", "when", "then")}


_PART = 90  # characters of each of a scenario's given, when and then on a card


def criteria(stories: list[dict], flow: dict, lang: Lang) -> list[dict]:
    """Each P1 scenario as an acceptance criterion the product agent cannot pass alone: met only when the branch's own
    script ran it and said pass. A script that never ran is one criterion for them all, not each scenario failed."""
    said = flow.get("scenarios") or {}
    scenarios = p1_scenarios(stories)
    if scenarios and not flow.get("ran"):
        return [{"criterion": msg("build.scenario.all", lang, n=len(scenarios)), "met": False,
                 "evidence": msg("build.scenario.not_run", lang)}]
    out = []
    for sc in scenarios:
        why = {"pass": "passed", "fail": "failed"}.get(said.get(sc["id"]), "missing")
        out.append({"criterion": criterion(sc, lang), "met": why == "passed",
                    "evidence": msg(f"build.scenario.{why}", lang)})
    return out


def criterion(sc: dict, lang: Lang) -> str:
    """A P1 scenario's criterion as `criteria` words it, which the merge card matches back to its scenario."""
    return msg("build.scenario.criterion", lang, **_words(sc))


def gaps(stories: list[dict], acceptance: AcceptanceOut, flow: dict) -> list[dict]:
    """Where the spec's stories, what was built and what the acceptance check saw disagree, for the merge card's first
    lines: a P1 scenario that did not pass, a story the code does not build (or the check never said), and what the
    branch adds beyond the spec."""
    said = flow.get("scenarios") or {}
    checked = {c.story_id: c for c in acceptance.stories}
    out: list[dict] = []
    for x in stories:
        story = {"story": x["id"], "priority": x["priority"], "title": x["title"]}
        if x["priority"] == "P1" and x["scenarios"] and not flow.get("ran"):
            out.append({**story, "kind": "not_run", "detail": ""})
        elif x["priority"] == "P1":
            out += [{**story, "kind": "scenario", "detail": "", "scenario": _words(sc)}
                    for sc in x["scenarios"] if said.get(sc["id"]) != "pass"]
        c = checked.get(x["id"])
        if c is None:
            out.append({**story, "kind": "unchecked", "detail": ""})
        elif c.built != "yes":
            out.append({**story, "kind": f"built_{c.built}", "detail": c.evidence})
    out += [{"story": None, "priority": None, "title": "", "kind": "beyond", "detail": b} for b in acceptance.beyond_spec]
    return out


def gap_lines(found: list[dict], lang: Lang) -> list[str]:
    """The gaps as the merge card's first lines: each story by its title, each scenario in its own words."""
    return [msg(f"build.gap.{g['kind']}", lang, priority=g["priority"], title=g["title"], detail=g["detail"],
                **g.get("scenario", {})) for g in found]
