"""A spec's user journeys: how real people will use this version, written by the spec writer beside the stories and
approved with the spec. A journey is not a test. The tests are the stories' scenarios, which the build's own script
runs; a journey is what the owner follows on the merge card, with what the worker captured when it ran each step on
the build (engineering/walkthrough.py).

The server numbers them, as it numbers stories: journeys JN1, JN2... with P1 first, steps JN1.1, JN1.2... The JN
prefix keeps a step from reading like build J-12. `journeys.yaml` beside the spec holds them with the spec's `about`,
and `missing`: why the draft has no usable journey (`none`, `too_many`, `steps`, `see`, `test`), which the spec card
tells the owner. A spec from before journeys has no file, so no `missing`, and nothing about it is blocked.
"""
from __future__ import annotations

import yaml

from justshipit.contracts.handoff import AboutOut, JourneyOut
from justshipit.services import registry

FILE = "journeys.yaml"
_ORDER = {"P1": 0, "P2": 1}


def number(about: AboutOut | None, journeys: list[JourneyOut]) -> dict:
    """{"about": {...} | None, "journeys": [{"id", "priority", "title", "persona", "why", "brings", "success",
    "steps": [{"id", "do", "why", "see"}]}]} with the server's ids, P1 first and otherwise in the writer's order."""
    ordered = sorted(journeys, key=lambda x: _ORDER[x.priority])
    return {"about": about.model_dump() if about else None,
            "journeys": [{"id": f"JN{n}", "priority": x.priority, "title": x.title, "persona": x.persona,
                          "why": x.why, "brings": x.brings, "success": x.success,
                          "steps": [{"id": f"JN{n}.{i}", **st.model_dump()} for i, st in enumerate(x.steps, 1)]}
                         for n, x in enumerate(ordered, 1)]}


def dump(doc: dict) -> str:
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def parse(text: str) -> dict:
    """The document a journeys.yaml holds, {"about", "journeys", "missing"}; empty for text that is not one."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        data = None
    if not isinstance(data, dict):
        return {"about": None, "journeys": [], "missing": None}
    about = data.get("about")
    journeys = data.get("journeys")
    missing = data.get("missing")
    return {"about": about if isinstance(about, dict) else None,
            "journeys": [x for x in journeys if isinstance(x, dict)] if isinstance(journeys, list) else [],
            "missing": missing if isinstance(missing, str) else None}


def at(registry_path: str, commit: str) -> dict:
    """The journeys as the spec's commit holds them; none for a spec from before journeys."""
    try:
        return parse(registry.read_at(f"{registry_path}/{FILE}", commit))
    except RuntimeError:
        return {"about": None, "journeys": [], "missing": None}


def card(doc: dict) -> list[dict]:
    """What the spec card shows of each journey: who it is for and what they have when it worked."""
    return [{"id": x["id"], "priority": x["priority"], "title": x["title"], "persona": x["persona"],
             "success": x["success"]} for x in doc.get("journeys") or []]


def steps(doc: dict) -> dict[str, dict]:
    """Every step of every journey by its id."""
    return {st["id"]: st for x in doc.get("journeys") or [] for st in x.get("steps") or [] if isinstance(st, dict)
            and isinstance(st.get("id"), str)}
