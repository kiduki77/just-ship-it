"""Door rules from an approved brief: who decides which kind of action.

The CEO reads the brief's decision-rights and red-line lines once, in a queued job, and the rules are stored on the
business (`Business.door_policy`) for `policy.decide` to read. The model only proposes. Every rule is checked here
before it is stored: the action kind must exist, the line it cites must be a decision-rights line it was shown for a
`project_manager` rule, a `project_manager` rule reaches only `policy.PROJECT_MANAGER_DECIDABLE` kinds (anything else becomes the owner's), and one line
hands the CEO at most `MAX_PROJECT_MANAGER_RULES_PER_LINE` rules. A brief with no decision rights gets the stock rules instead.
"""
from __future__ import annotations

import logging
from collections import Counter
from functools import lru_cache

from justshipit.agents.executor import run_agent
from justshipit.contracts.door import DoorRuleOut, DoorRulesOut
from justshipit.db.models import BusinessBrief
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, msg
from justshipit.services import app_settings, policy, roadmap
from justshipit.services.roadmap import BRIEF_BY_KEY, PROVENANCE_KO, SETTLED
from justshipit.settings import settings, tool_policy

log = logging.getLogger(__name__)

RIGHTS, RED_LINES = "decision_rights", "red_lines"
DEFAULT_PROJECT_MANAGER_ACTIONS = ("internal.*", "research.*")
MAX_PROJECT_MANAGER_RULES_PER_LINE = 4


def _rule(action: str, decider: str, note: str, source: str, origin: str = "brief") -> dict:
    return {"action": action, "decider": decider, "note": note[:300], "source": source, "origin": origin}


def default_rules(lang: Lang) -> list[dict]:
    return [_rule(a, "project_manager", msg("door.note.default", lang), "default", "stock") for a in DEFAULT_PROJECT_MANAGER_ACTIONS]


def _expand(out: DoorRuleOut, line: dict, version: int, *, from_rights: bool, problems: list[str],
            lang: Lang) -> list[dict]:
    """The model's rule as a stored rule. A `project_manager` rule stays one only when it came from a decision-rights line and
    covers a kind the CEO may decide; any other `project_manager` rule is the owner's."""
    action = out.action.strip()
    source = f"brief:v{version}:{line['id']}"
    note = msg("door.note.from_plan", lang, reason=out.reason.strip(), line=line["text"][:80])
    if not policy.kinds_covered(action):
        problems.append(msg("door.dropped.unknown", lang, action=action))
        return []
    if out.decider == "owner":
        return [_rule(action, "owner", note, source)]
    if not from_rights:
        problems.append(msg("door.dropped.red_line", lang, action=action))
        return [_rule(action, "owner", note, source)]
    if not policy.project_manager_kinds_covered(action):
        return [_rule(action, "owner", msg("door.note.owner_decides", lang, note=note), source)]
    return [_rule(action, "project_manager", note, source)]


def accept(proposed: list[DoorRuleOut], lines: dict[str, tuple[str, dict]], version: int,
           lang: Lang) -> tuple[list[dict], list[str]]:
    """Rules the model proposed, minus the ones that cite a line it was not shown, and what was dropped or changed
    as problems in the owner's language. One rule per action, the owner's winning over the CEO's, and one line hands
    the CEO at most `MAX_PROJECT_MANAGER_RULES_PER_LINE` rules: a line that seems to cover more is not read that widely."""
    best: dict[str, dict] = {}
    problems: list[str] = []
    for out in proposed:
        if (found := lines.get(out.line_id)) is None:
            problems.append(msg("door.dropped.unknown_line", lang, action=out.action, line=out.line_id))
            continue
        section, line = found
        for rule in _expand(out, line, version, from_rights=section == RIGHTS, problems=problems, lang=lang):
            if rule["action"] not in best or rule["decider"] == "owner":
                best[rule["action"]] = rule
    taken: Counter[str] = Counter()
    kept = []
    for rule in best.values():
        if rule["decider"] == "project_manager":
            taken[rule["source"]] += 1
            if taken[rule["source"]] > MAX_PROJECT_MANAGER_RULES_PER_LINE:
                problems.append(msg("door.dropped.too_many", lang, action=rule["action"], n=MAX_PROJECT_MANAGER_RULES_PER_LINE))
                continue
        kept.append(rule)
    for problem in problems:
        log.warning("door rules: %s", problem)
    return kept, problems


@lru_cache
def _system() -> str:
    return (settings().prompts_dir / "door_rules.md").read_text(encoding="utf-8")


def _settled(brief: BusinessBrief, key: str) -> list[dict]:
    sec = next((x for x in brief.sections if x["key"] == key), None)
    return [x for x in sec["lines"] if x["provenance"] in SETTLED] if sec else []


def _kinds_listing() -> str:
    return "\n".join(f"- {kind}: {mode}{', the Project Manager may decide' if kind in policy.PROJECT_MANAGER_DECIDABLE else ''}"
                     for kind, mode in sorted(tool_policy().items()))


def _prompt(lines: dict[str, tuple[str, dict]]) -> str:
    shown = "\n".join(f"- [{line['id']}] ({BRIEF_BY_KEY[section].title}, {PROVENANCE_KO[line['provenance']]}) {line['text']}"
                      for section, line in lines.values())
    return "\n\n".join(["## Action kinds and their policy now", _kinds_listing(), "## Plan lines", shown,
                        "Turn the lines above into rules."])


def derive(brief_id: int) -> None:
    """Store the door rules of an approved brief. A newer approved brief replaces this one's rules, so a run for an
    older brief does nothing. The model call holds no transaction; a `MustWait` out of it leaves the job to run later."""
    lang = app_settings.language()
    with session_scope() as s:
        brief = s.get(BusinessBrief, brief_id)
        if brief is None or brief.approved_at is None or (roadmap.approved_brief(s, brief.business_id).id != brief.id):
            return
        bid, version = brief.business_id, brief.version
        lines = {x["id"]: (key, x) for key in (RIGHTS, RED_LINES) for x in _settled(brief, key)}
        has_rights = bool(_settled(brief, RIGHTS))
    rules, problems = accept(run_agent("project_manager", "plan", _prompt(lines), DoorRulesOut, project_id=None, business_id=bid,
                                       system_override=_system()).rules, lines, version, lang) if lines else ([], [])
    if not has_rights:
        rules += [r for r in default_rules(lang) if r["action"] not in {x["action"] for x in rules}]
    with session_scope() as s:
        brief = s.get(BusinessBrief, brief_id)
        if roadmap.approved_brief(s, bid).id == brief.id:
            roadmap.set_derived_door_policy(s, bid, rules, brief=brief, problems=problems, lang=lang)
