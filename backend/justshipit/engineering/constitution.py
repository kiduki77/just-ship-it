"""A product's constitution: the few rules every version keeps, after GitHub Spec Kit's constitution (MIT, Copyright
GitHub, Inc.). For a product the owner brought, keep its look, stack and scope; for every product, the constraints the
owner stated (a budget, no sign-up, a language), each citing where he said it. The first spec that finds none proposes
it; a later spec proposes a change only through an owner decision (engineering/questions.py). Either binds only once
the owner approves that spec, which shows the rules on its card: a rejected spec leaves the product's rules as they
were. The spec writer, the engineer, the code reviewer and the acceptance check read them: each spec carries its copy
at its commit, `constitution.yaml` with `constitution.md` rendered from it.

The registry keeps the product's rules, as approved, one file per product repository: `constitutions/<repo>.yaml`.
"""
from __future__ import annotations

import logging
from datetime import date

import yaml

from justshipit.contracts.handoff import PrincipleChangeOut, PrincipleOut
from justshipit.engineering.owner_trace import OwnerSource
from justshipit.i18n import Lang, msg
from justshipit.services import registry

log = logging.getLogger(__name__)

FILE = "constitution.md"    # in a spec
RULES = "constitution.yaml"  # in a spec: the rules it was written under, and sets once approved
_KEEP = ("look", "stack", "scope")  # what a product the owner brought keeps


def path(repo: str) -> str:
    return f"constitutions/{repo}.yaml"


def read(repo: str) -> dict | None:
    rel = path(repo)
    return yaml.safe_load(registry.read(rel)) if (registry.root() / rel).exists() else None


def read_at(repo: str, commit: str) -> dict | None:
    try:
        return yaml.safe_load(registry.read_at(path(repo), commit))
    except RuntimeError:
        return None


def in_spec(base: str, commit: str) -> dict | None:
    """The rules a spec was written under, at its commit; None for a spec from before specs carried them."""
    try:
        return yaml.safe_load(registry.read_at(f"{base}/{RULES}", commit))
    except RuntimeError:
        return None


def dump(c: dict) -> str:
    return yaml.safe_dump(c, allow_unicode=True, sort_keys=False)


def first(repo: str, principles: list[PrincipleOut], sources: list[OwnerSource], brought: bool, lang: Lang) -> dict:
    """The product's first constitution: what a brought product keeps, then each rule the spec writer took from the
    owner's own words. A rule that cites nothing the owner said, or a decision he has not approved, is not the owner's
    constraint and is left out."""
    known = {x.ref: x for x in sources if x.binding}
    rules = [{"rule": msg(f"build.constitution.keep_{k}", lang), "why": msg(f"build.constitution.keep_{k}_why", lang),
              "from": msg("build.constitution.from_product", lang)} for k in _KEEP] if brought else []
    for p in principles:
        if p.source not in known:
            log.warning("constitution rule %r cites %r, none of the owner sources", p.rule, p.source)
            continue
        rules.append({"rule": p.rule, "why": p.why, "from": known[p.source].origin})
    today = date.today().isoformat()
    return {"product": repo, "version": 1, "ratified": today, "amended": today,
            "principles": [{"id": f"C{n}", **r} for n, r in enumerate(rules, 1)]}


def amended(c: dict, change: dict, lang: Lang) -> dict:
    """The constitution with one change the owner approved: the rule it names rewritten, or a new rule added."""
    rules = [dict(r) for r in c["principles"]]
    new = {"rule": change["rule"], "why": change["why"], "from": msg("build.constitution.from_decision", lang)}
    hit = next((r for r in rules if r["id"] == change.get("principle_id")), None)
    if hit is not None:
        hit.update(new)
    else:
        rules.append({"id": f"C{len(rules) + 1}", **new})
    return {**c, "version": c["version"] + 1, "amended": date.today().isoformat(), "principles": rules}


def approved(repo: str, proposed: dict | None, made: list[dict], lang: Lang) -> dict | None:
    """The product's rules once the owner approves a spec, or None when they stay as they are. A product with none
    takes the rules the spec proposed; one with rules takes the changes the owner chose on the spec's questions, onto
    the rules as they are now, so a spec approved after another keeps the other's changes."""
    now = read(repo)
    if now is None:
        return proposed or None
    for change in made:
        now = amended(now, change, lang)
    return now if made else None


def card(c: dict | None) -> list[dict]:
    """What the spec card shows of the rules: each one and where the owner said it."""
    return [{"id": r["id"], "rule": r["rule"], "from": r["from"]} for r in (c or {}).get("principles", [])]


def change_of(x: PrincipleChangeOut, c: dict | None) -> dict | None:
    """A proposed change as the server keeps it, with the rule it replaces; None for one that names no rule there is."""
    old = next((r for r in (c or {}).get("principles", []) if r["id"] == x.principle_id), None)
    if x.principle_id is not None and old is None:
        log.warning("constitution change names %r, which the constitution does not have", x.principle_id)
        return None
    return {"principle_id": x.principle_id, "rule": x.rule, "why": x.why, "old": old["rule"] if old else None}


def markdown(c: dict, lang: Lang) -> str:
    out = [f"# {msg('build.constitution.title', lang, product=c['product'])}", "",
           msg("build.constitution.lead", lang), ""]
    for r in c["principles"]:
        out += [f"## {r['id']}. {r['rule']}", f"{r['why']} ({r['from']})", ""]
    out += [f"## {msg('build.constitution.governance', lang)}", msg("build.constitution.governance_text", lang), "",
            msg("build.constitution.version", lang, version=c["version"], ratified=c["ratified"], amended=c["amended"])]
    return "\n".join(out) + "\n"


def prompt_section(c: dict | None) -> str:
    if c is None:
        return ("## This product's constitution\nIt has none yet. Put in constitution the owner's own constraints that "
                "every version must keep, each citing where the owner said it (a rule that cites no owner words is "
                "dropped); the server adds what a product the owner brought keeps. They bind once the owner approves "
                "this spec.")
    rules = "\n".join(f"- {r['id']}: {r['rule']} ({r['why']})" for r in c["principles"]) or "- (no rules)"
    return ("## This product's constitution (every version keeps these rules)\n" + rules + "\nWrite a spec that keeps "
            "every rule. When this spec cannot be built without changing one, propose the change in "
            "constitution_changes; the owner decides it before the spec is approved. Leave constitution empty.")
