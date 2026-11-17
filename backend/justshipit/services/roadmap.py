"""Roadmap: a business, its stages and tracks, its experiments (bets) and cycles.

A business is what the owner is trying to do. Its rungs (stages), tracks, business models, door policy and
checkpoints are lists the CEO drafts and the owner edits, so no screen fixes a stage template. A bet (the owner's
"experiment") is one time-boxed try; its run is the Project whose `bet_id` points at it, and that link has one writer.
Every change to these records goes through this module. Refusals are `ControlError`, which the console shows as is,
so each takes the owner's language (`lang`) from the route or job that calls it.

A function some tests still call without a language defaults to `UNMIGRATED`, the Korean they always got. Every
caller in the server passes `lang`; once the tests do too, that default goes.
"""
from __future__ import annotations

import copy
import math
import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, BeforeValidator, Field, ValidationError, model_validator
from sqlalchemy import Column, func, select
from sqlalchemy.orm import Session

from justshipit.agents import roles
from justshipit.contracts.goal import GoalTarget
from justshipit.db.models import (
    BET_HEALTH,
    HILL_POSITIONS,
    KICKOFF_LIVE,
    RUN_MODES,
    Bet,
    Business,
    BusinessBrief,
    Cycle,
    CycleStage,
    Job,
    KickoffSession,
    MetricPoint,
    OwnerTask,
    Project,
)
from justshipit.i18n import LANGS, Lang, catalog, msg
from justshipit.i18n.match.durations import KO_LETTERS, MONTH, NUMBER_WORDS, UNITS, WORD_UNITS
from justshipit.i18n.said import say
from justshipit.services import activity, app_settings, control, documents, metrics, people_link, policy, queue
from justshipit.services.control import ControlError
from justshipit.services.fence import neutralize
from justshipit.settings import limits


class BetStatus(StrEnum):
    PROPOSED = "PROPOSED"
    PICKED = "PICKED"
    RUNNING = "RUNNING"
    LISTENING = "LISTENING"
    REVIEW = "REVIEW"
    DONE = "DONE"
    STOPPED = "STOPPED"
    DROPPED = "DROPPED"


BET_TRANSITIONS: dict[BetStatus, set[BetStatus]] = {
    BetStatus.PROPOSED: {BetStatus.PICKED, BetStatus.DROPPED},
    BetStatus.PICKED: {BetStatus.RUNNING, BetStatus.PROPOSED, BetStatus.DROPPED},
    BetStatus.RUNNING: {BetStatus.LISTENING, BetStatus.REVIEW, BetStatus.DONE, BetStatus.STOPPED},
    BetStatus.LISTENING: {BetStatus.REVIEW},
    BetStatus.REVIEW: {BetStatus.RUNNING, BetStatus.DONE, BetStatus.STOPPED},
    BetStatus.DONE: set(),
    BetStatus.STOPPED: set(),
    BetStatus.DROPPED: set(),
}
TERMINAL_BET_STATUSES = {BetStatus.DONE, BetStatus.STOPPED, BetStatus.DROPPED}
LIVE_BET_STATUSES = (BetStatus.RUNNING.value, BetStatus.LISTENING.value)
_STAMP = {BetStatus.PICKED: "picked_at", BetStatus.RUNNING: "started_at", BetStatus.REVIEW: "finished_at",
          BetStatus.DONE: "finished_at", BetStatus.STOPPED: "finished_at", BetStatus.DROPPED: "finished_at"}

UNMIGRATED: Lang = "ko"


def _now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ list items

ItemId = Annotated[str, Field(max_length=24)]


class Rung(BaseModel):
    id: ItemId = ""
    name: str
    exit_signal: str = ""
    reached_at: str | None = None


class Track(BaseModel):
    id: ItemId = ""
    name: str
    description: str = ""


class BusinessModelItem(BaseModel):
    id: ItemId = ""
    name: str
    kind: str = ""
    status: Literal["candidate", "testing", "dropped"] = "candidate"
    break_even: str | None = None
    first_proof: str | None = None


class DoorRule(BaseModel):
    """Who decides an action kind (`internal.*` names a family). A `project_manager` rule is for the kinds in
    `policy.PROJECT_MANAGER_DECIDABLE` only, which `policy.door_rule_problem` enforces on write. `origin` and `source` are the
    server's: `origin` is `brief` for a rule taken from the brief (`source` is `brief:v<version>:<line id>`), `stock`
    for the stock rules (`source` is `default`), and `owner` for one the owner wrote or edited, which a new brief
    never replaces."""
    id: ItemId = ""
    action: str
    decider: Annotated[Literal["owner", "project_manager"], BeforeValidator(roles.current)]
    note: str | None = None
    source: str | None = None
    origin: Literal["owner", "brief", "stock"] = "owner"


def origin_of(rule: Any) -> str:
    """Who wrote a stored rule. A row from before `origin` existed is read from its `source`; anything else is the
    owner's, so a hand-stored row is never replaced."""
    if not isinstance(rule, dict):
        return "owner"
    if rule.get("origin") in ("owner", "brief", "stock"):
        return rule["origin"]
    source = rule.get("source") or ""
    return "brief" if source.startswith("brief:") else "stock" if source == "default" else "owner"


def _owner_door_edit(stored: list[Any], items: list[Any]) -> list[Any]:
    """The owner's door rules with `origin` and `source` set by the server, whatever the client sent. A rule sent
    back as it was keeps its origin; one the owner added or changed is the owner's from now on."""
    prior = {r.get("id"): r for r in stored if isinstance(r, dict)}
    out = []
    for raw in items:
        if not isinstance(raw, dict):
            out.append(raw)
            continue
        old = prior.get(raw.get("id"))
        if old and all((raw.get(k) or None) == (old.get(k) or None) for k in ("action", "decider", "note")):
            out.append({**raw, "origin": origin_of(old), "source": old.get("source")})
        else:
            out.append({**raw, "origin": "owner", "source": None})
    return out


def _is_door_rule(raw: Any) -> bool:
    try:
        DoorRule.model_validate(raw)
    except ValidationError:
        return False
    return True


class Checkpoint(BaseModel):
    id: ItemId = ""
    on: date
    title: str
    kind: Literal["date", "rung_exit"] = "date"
    rung_id: str | None = None
    done_at: str | None = None


LISTS: dict[str, tuple[type[BaseModel], str]] = {
    "rungs": (Rung, "r"), "tracks": (Track, "t"), "models": (BusinessModelItem, "m"),
    "door_policy": (DoorRule, "d"), "checkpoints": (Checkpoint, "c"),
}


Provenance = Literal["said", "confirmed", "guess", "unknown", "blank"]


def provenance_label(provenance: str, lang: Lang) -> str:
    return msg(f"roadmap.provenance.{provenance}", lang)


# The Korean labels the agent prompts in other files still read; owner text uses `provenance_label`.
PROVENANCE_KO: dict[str, str] = {p: provenance_label(p, "ko") for p in get_args(Provenance)}
SETTLED = ("said", "confirmed")


class BriefLine(BaseModel):
    """`confirmed` is a line the CEO proposed and the owner agreed to. `source` names where it came from:
    `kickoff:<session>:<turn>` for the kickoff conversation (turn 0 is what the owner typed to start it,
    `material` the text the owner pasted, `request:<id>` for the owner's answer to a card that amended it). `quote` is
    the owner's own sentence a `said` line rests on, and `note` says why a line the CEO called `said` was kept as a
    guess. `person` is the one person a `known_people` line names, as a name; a line without it names nobody to
    contact. `target` is a goal-and-deadline line's number, typed (`checked_target`)."""
    id: str = ""
    text: str = ""
    provenance: Provenance
    source: str | None = None
    quote: str | None = None
    note: str | None = None
    person: str | None = None
    target: GoalTarget | None = None

    @model_validator(mode="after")
    def _blank_is_empty(self):
        if self.provenance == "blank" and self.text.strip():
            raise ValueError("a blank line has no text")
        if self.provenance != "blank" and not self.text.strip():
            raise ValueError("a line with no text is blank")
        return self


class BriefSection(BaseModel):
    key: str
    title: str
    lines: list[BriefLine] = []


SpecText = Literal["title", "holds", "drives", "default"]


@dataclass(frozen=True)
class BriefSpec:
    """`gate`: approval needs a said or confirmed line here. `quiet`: never listed as missing. Its words (the title,
    what it holds, what it drives, and the default the company applies when a required section stays empty) are in
    the `roadmap.section.<key>.*` messages, read with `text`. A title may name the plan's goal (`goal_count`)."""
    key: str
    required: bool
    gate: bool = False
    quiet: bool = False

    def text(self, part: SpecText, lang: Lang, goal: str | None = None) -> str:
        """The section's words in `lang`; "" for a default it has none of. `goal` is the plan's goal as a count;
        without one a title that names the goal says "your goal"."""
        key = f"roadmap.section.{self.key}.{part}"
        return msg(key, lang, goal=goal or msg("roadmap.section.your_goal", lang)) if key in catalog() else ""

    # The Korean words the kickoff and agent prompts in other files still read; owner text uses `text`.
    title = property(lambda self: self.text("title", "ko"))
    holds = property(lambda self: self.text("holds", "ko"))
    drives = property(lambda self: self.text("drives", "ko"))
    default = property(lambda self: self.text("default", "ko"))


# The sections of 08-first-100.md 3.3 after the expert panel (09 section 6, item 2), in the order a plan reads. Seven
# are required. Organization, reporting and deliverables are settings defaults now, and decision rights is a quiet
# optional section: the stock door rules apply unless the owner states some.
BRIEF_SECTIONS: tuple[BriefSpec, ...] = (
    BriefSpec("one_liner", True),
    BriefSpec("customer_problem", True, gate=True),
    BriefSpec("alternative_value", True),
    BriefSpec("path", True),
    BriefSpec("goal_deadline", True, gate=True),
    BriefSpec("owner_time", True),
    BriefSpec("red_lines", True),
    BriefSpec("known_people", False),
    BriefSpec("reverse_100", False),
    BriefSpec("top_risks", False),
    BriefSpec("scope", False),
    BriefSpec("budget_usage", False),
    BriefSpec("pricing_hypothesis", False),
    BriefSpec("background", False),
    BriefSpec("decision_rights", False, quiet=True),
    BriefSpec("owner_words", False, quiet=True),
)
BRIEF_BY_KEY: dict[str, BriefSpec] = {spec.key: spec for spec in BRIEF_SECTIONS}
OLD_KEY_TO_NEW: dict[str, str] = {"success_stop": "goal_deadline"}
# The heading the kickoff and agent prompts in other files give a section an older plan had; the console gets `legacy`.
LEGACY_TITLE = msg("roadmap.section.legacy", "ko")


def empty_sections() -> list[dict]:
    return [{"key": spec.key, "title": spec.title, "lines": []} for spec in BRIEF_SECTIONS]


def normalize_sections(sections: list[dict]) -> list[dict]:
    """A brief's sections as the registry reads them: every registry section in order, then the sections of
    older briefs that have no place in the registry (their own key and title, empty ones dropped). A key the
    registry replaced one-to-one moves to its new section, so the success lines of an old brief sit under the goal and
    deadline. A registry section's title is the Korean the prompts read, naming the plan's goal where it has one;
    `section_views` gives the console its own."""
    found: dict[str, dict] = {}
    for sec in sections:
        key = OLD_KEY_TO_NEW.get(sec["key"], sec["key"])
        found.setdefault(key, {"key": key, "title": sec["title"], "lines": []})["lines"].extend(sec["lines"])
    goal = goal_count(found.get("goal_deadline", {}).get("lines", []))
    ordered = [{"lines": [], **found.pop(spec.key, {}), "key": spec.key, "title": spec.text("title", "ko", goal)}
               for spec in BRIEF_SECTIONS]
    return ordered + [sec for sec in found.values() if sec["lines"]]


def goal_count(goal_lines: list[dict], lang: Lang | None = None) -> str | None:
    """The plan's goal as a count in its own unit ("5 stores", "10곳"): of the goals its said and confirmed goal lines
    state with a number, the one the overview shows. None when no such line states a number, or, with `lang`, when the
    plan counts it in another language, since the owner's titles are in `lang` ("30명" under an English title)."""
    from justshipit.services import completion  # completion reads the plan through this module

    numbered = tuple(g for g in completion.plan_goals(goal_lines) if g.number is not None)
    if not numbered:
        return None
    g = completion.headline(numbered)
    count = f"{g.number:,}{' ' if re.match(r'[A-Za-z]', g.unit) else ''}{g.unit}"
    return None if lang and bool(re.search(f"[{KO_LETTERS}]", count)) != (lang == "ko") else count


def is_legacy(sec: dict) -> bool:
    return sec["key"] not in BRIEF_BY_KEY


def section_state(lines: list[dict]) -> Literal["settled", "guess", "unknown", "empty"]:
    """What a section holds: an owner-stated or confirmed line settles it, an explicit "모름" also closes it
    (the owner answered, the answer is a research bet), a guess alone is still weak."""
    seen = {x["provenance"] for x in lines}
    if seen & set(SETTLED):
        return "settled"
    if "guess" in seen:
        return "guess"
    if "unknown" in seen:
        return "unknown"
    return "empty"


def settles(spec: BriefSpec, state: str) -> bool:
    """Whether a section in `state` is closed for the conversation: said or confirmed, or answered with 모르겠어요.
    A gate section answered with 모르겠어요 stays open, because approval needs the owner's own line there."""
    return state == "settled" or (state == "unknown" and not spec.gate)


def lines_by_key(sections: list[dict]) -> dict[str, list[dict]]:
    return {sec["key"]: sec["lines"] for sec in normalize_sections(sections)}


def checked_target(section: str, text: str, target: GoalTarget | dict | None) -> dict | None:
    """A goal line's typed number as stored: only on a goal-and-deadline line, and only a number the line's own words
    state, with a unit. Anything else is dropped and the line's words are read instead (`completion.goals`)."""
    if target is None or section != "goal_deadline":
        return None
    try:
        t = GoalTarget.model_validate(target)
    except ValidationError:
        return None
    said = {int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text)}
    unit = t.unit.strip()
    if t.number <= 0 or t.number not in said or not 0 < len(unit) <= 30:
        return None
    return {**t.model_dump(mode="json"), "unit": unit}


def _alternation(words) -> str:
    return "|".join(sorted(map(re.escape, words), key=len, reverse=True))


_UNIT_DAYS = {u: d for units in UNITS.values() for u, d in units.items()}
# A span of time ("6주", "30일", "석 달", "6 weeks", "two months"). A day after a month ("11월 30일") is a date, not a
# span. A number word counts only the units `WORD_UNITS` gives its language.
DURATION = re.compile(
    rf"(?<![\d.,])(?<!{MONTH})(?<!{MONTH}\s)(?P<n>\d+)[\s-]*(?P<unit>{_alternation(_UNIT_DAYS)})(?![A-Za-z])"
    rf"|(?<![{KO_LETTERS}])(?P<ko>{_alternation(NUMBER_WORDS['ko'])})\s*(?P<ko_unit>{_alternation(WORD_UNITS['ko'])})"
    rf"|(?<![a-z])(?P<en>{_alternation(NUMBER_WORDS['en'])})[\s-]+"
    rf"(?P<en_unit>{_alternation(WORD_UNITS['en'])})(?![a-z])",
    re.IGNORECASE)
MAX_DEADLINE_DAYS = 365


def _span_days(m: re.Match[str]) -> int:
    if m["n"]:
        return int(m["n"]) * _UNIT_DAYS[m["unit"].lower()]
    lang, number, unit = ("ko", m["ko"], m["ko_unit"]) if m["ko"] else ("en", m["en"], m["en_unit"])
    return NUMBER_WORDS[lang][number.lower()] * _UNIT_DAYS[unit.lower()]


def deadline_days(sections: list[dict]) -> int | None:
    """The nearest deadline the owner set in 목표와 기한, in days ("6주 안에 3곳, 8주 안에 1곳" is 42), or None when no
    said or confirmed line there names a span of time."""
    spans = [_span_days(m) for line in lines_by_key(sections).get("goal_deadline", []) if line["provenance"] in SETTLED
             for m in DURATION.finditer(line["text"])]
    return min(MAX_DEADLINE_DAYS, min(spans)) if spans and min(spans) > 0 else None


def unsettled_gates(sections: list[dict]) -> list[str]:
    """Keys of the gate sections with no said or confirmed line. A 모름 answered twice does not settle these."""
    by_key = lines_by_key(sections)
    return [spec.key for spec in BRIEF_SECTIONS if spec.gate and section_state(by_key.get(spec.key, [])) != "settled"]


def approval_blockers(sections: list[dict], *, lang: Lang = UNMIGRATED) -> list[str]:
    """Titles of the gate sections that block approval."""
    return [BRIEF_BY_KEY[k].text("title", lang) for k in unsettled_gates(sections)]


PROMPT_MAX_LINES = 6
PROMPT_MAX_CHARS = 300


def prompt_lines(lines: list[dict]) -> list[str]:
    """A section's lines as a prompt shows them: the newest few, each cut short, and a marker for the rest."""
    shown = lines[-PROMPT_MAX_LINES:]
    out = [f"- ({len(lines) - len(shown)} older lines not shown)"] if len(lines) > len(shown) else []
    for x in shown:
        text = x["text"] if len(x["text"]) <= PROMPT_MAX_CHARS else x["text"][:PROMPT_MAX_CHARS] + "…"
        out.append(f"- [{x['id']}] ({PROVENANCE_KO[x['provenance']]}) {text}")
    return out


def empty_defaults(sections: list[dict], *, lang: Lang = UNMIGRATED) -> list[dict]:
    """Required sections the owner has not settled and the default the company applies in their place, shown before
    the owner approves. A gate section is never listed: it blocks approval instead."""
    by_key = lines_by_key(sections)
    out = []
    for spec in BRIEF_SECTIONS:
        state = section_state(by_key.get(spec.key, []))
        if spec.required and not spec.gate and state != "settled":
            default = msg("roadmap.section.guess_default", lang) if state == "guess" else spec.text("default", lang)
            out.append({"key": spec.key, "title": spec.text("title", lang), "state": state, "default": default})
    return out


def section_views(sections: list[dict], *, lang: Lang = UNMIGRATED) -> list[dict]:
    """The sections as a screen reads them: the registry's facts about each (what it holds, what it drives, the default
    the company applies when it stays empty) beside its lines and the state those lines add up to. A section an older
    plan had keeps its stored title."""
    out = []
    normalized = normalize_sections(sections)
    goal = goal_count(next(sec["lines"] for sec in normalized if sec["key"] == "goal_deadline"), lang)
    for sec in normalized:
        spec = BRIEF_BY_KEY.get(sec["key"])
        words = {part: spec.text(part, lang) if spec else "" for part in ("holds", "drives", "default")}
        out.append({"key": sec["key"], "title": spec.text("title", lang, goal) if spec else sec["title"],
                    "legacy": spec is None,
                    "required": bool(spec and spec.required), "quiet": bool(spec and spec.quiet),
                    "gate": bool(spec and spec.gate), **words, "state": section_state(sec["lines"]),
                    "lines": [{**x, "provenance_label": provenance_label(x["provenance"], lang)} for x in sec["lines"]]})
    return out


def brief_view(s: Session, bid: int, *, lang: Lang) -> dict:
    """The brief the company works from, for the business page: the approved one in its sections, with the defaults it
    applies where a required section stayed unsettled, and whether a newer draft waits for the owner."""
    _business(s, bid, lang, lock=False)
    brief, draft = approved_brief(s, bid), draft_brief(s, bid)
    return {"brief": {"id": brief.id, "version": brief.version, "approved_at": brief.approved_at,
                      "sections": documents.annotate(s, section_views(brief.sections, lang=lang)),
                      "empty_defaults": empty_defaults(brief.sections, lang=lang)}
            if brief else None,
            "draft_waiting": bool(draft and any(sec["lines"] for sec in draft.sections))}


class Assumption(BaseModel):
    id: str = ""
    text: str
    importance: Literal["high", "mid", "low"] = "mid"
    evidence: str | None = None
    status: Literal["open", "testing", "confirmed", "refuted"] = "open"
    bet_id: int | None = None


def _validated(model: type[BaseModel], items: list[Any], prefix: str, what: str, lang: Lang) -> list[dict]:
    """The items as `model` stores them, each with an id. `what` names the list (`roadmap.list.<what>`) in a refusal."""
    out, seen = [], set()
    label = msg(f"roadmap.list.{what}", lang)
    for i, raw in enumerate(items):
        try:
            item = model.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors()[0]
            raise ControlError(msg("roadmap.refuse.item", lang, what=label, n=i + 1, detail=first["msg"])) from exc
        data = item.model_dump(mode="json")
        if "id" in data:
            data["id"] = data["id"] or f"{prefix}-{secrets.token_hex(3)}"
            if data["id"] in seen:
                raise ControlError(msg("roadmap.refuse.duplicate_id", lang, what=label, id=data["id"]))
            seen.add(data["id"])
        out.append(data)
    return out


def _ids(items: list[dict]) -> set[str]:
    return {i["id"] for i in items}


# ------------------------------------------------------------------ businesses

def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "business"


def unique_slug(s: Session, column: Column, base: str) -> str:
    limit = column.type.length
    final, n = base[:limit], 2
    while s.scalar(select(column).where(column == final)):
        suffix = f"-{n}"
        final, n = base[:limit - len(suffix)] + suffix, n + 1
    return final


def _business(s: Session, bid: int, lang: Lang, *, lock: bool = True) -> Business:
    q = select(Business).where(Business.id == bid)
    b = s.scalar(q.with_for_update() if lock else q)
    if b is None:
        raise ControlError(msg("roadmap.refuse.no_business", lang, id=bid))
    return b


def create_business(s: Session, *, name: str, one_liner: str, slug: str | None = None,
                    lang: Lang = UNMIGRATED) -> Business:
    name = name.strip()
    if not name:
        raise ControlError(msg("roadmap.refuse.no_name", lang))
    b = Business(slug=unique_slug(s, Business.slug, slug or _slugify(name)), name=name[:200],
                 one_liner=one_liner.strip()[:1000])
    s.add(b)
    s.flush()
    return b


def _check_door_rules(rules: list[dict], lang: Lang) -> None:
    """What the owner wrote must be a rule the server will honour. A rule from a brief is not the owner's to fix."""
    for i, rule in enumerate(rules):
        if origin_of(rule) == "owner" and (problem := policy.door_rule_problem(rule)):
            raise ControlError(msg("roadmap.refuse.item", lang, what=msg("roadmap.list.door_policy", lang), n=i + 1,
                                   detail=say(problem, lang)))


def _withdraw_derived_project_manager_rules(s: Session, b: Business, brief: BusinessBrief, lang: Lang) -> None:
    """A new brief may take back what the last one handed the CEO. Until its rules are derived the owner decides:
    the rules the brief wrote (and the stock ones) that let the CEO decide go, the owner's own stay."""
    kept = [r for r in b.door_policy
            if origin_of(r) == "owner"
            or not (isinstance(r, dict) and roles.current(r.get("decider") or "") == roles.PROJECT_MANAGER)]
    if (gone := len(b.door_policy) - len(kept)) > 0:
        b.door_policy = kept
        activity.record(s, verb="door_rules_withdrawn", entity="business", entity_id=b.id, project_id=None,
                        business_id=b.id, actor="system", data={"brief_id": brief.id, "rules": gone},
                        summary=msg("roadmap.activity.rules_withdrawn", lang, id=b.id, name=b.name[:120],
                                    version=brief.version, n=gone))


def set_derived_door_policy(s: Session, bid: int, derived: list[dict], *, brief: BusinessBrief,
                            problems: Sequence[str] = (), lang: Lang = UNMIGRATED) -> Business:
    """Replace the rules taken from a brief with a new set. The owner's rules stay as they are and win over a
    derived rule for the same action. An owner rule the current policy would not accept is kept but not honoured,
    and recorded as a problem; it never stops the derivation."""
    b = _business(s, bid, lang)
    mine = [r for r in b.door_policy if origin_of(r) == "owner"]
    owned = {r.get("action") for r in mine if isinstance(r, dict)}
    problems = list(problems)
    for r in mine:
        if problem := policy.door_rule_problem(r):
            problems.append(msg("roadmap.problem.owner_rule", lang, problem=say(problem, lang),
                                action=r.get("action") if isinstance(r, dict) else repr(r)))
    fit = []
    for r in derived:
        if r["action"] in owned:
            continue
        if problem := policy.door_rule_problem(r):
            problems.append(msg("roadmap.problem.plan_rule", lang, action=r["action"], problem=say(problem, lang)))
        else:
            fit.append(r)
    new = [*_validated(DoorRule, [*(r for r in mine if _is_door_rule(r)), *fit], "d", "door_policy", lang),
           *(r for r in mine if not _is_door_rule(r))]
    if new != b.door_policy:
        b.door_policy = new
        activity.record(s, verb="door_rules_derived", entity="business", entity_id=b.id, project_id=None,
                        business_id=b.id, actor="project_manager", data={"brief_id": brief.id, "rules": len(new)},
                        summary=msg("roadmap.activity.rules_derived", lang, id=b.id, name=b.name[:120],
                                    version=brief.version, n=len(new)))
    if problems:
        activity.record(s, verb="door_rules_problem", entity="business", entity_id=b.id, project_id=None,
                        business_id=b.id, actor="system", data={"brief_id": brief.id, "problems": problems},
                        summary=msg("roadmap.activity.rules_problem", lang, id=b.id, name=b.name[:120],
                                    n=len(problems)))
    return b


def update_lists(s: Session, bid: int, *, lang: Lang, **lists: list[Any] | None) -> Business:
    """Replace one or more of the business's lists. New items get ids; an item a live bet points at cannot go."""
    b = _business(s, bid, lang)
    changed = []
    for name, items in lists.items():
        if items is None:
            continue
        if name not in LISTS:
            raise ControlError(msg("roadmap.refuse.not_a_list", lang, name=name))
        model, prefix = LISTS[name]
        if name == "door_policy":
            items = _owner_door_edit(b.door_policy, items)
        new = _validated(model, items, prefix, name, lang)
        if name == "door_policy":
            _check_door_rules(new, lang)
        gone = _ids(getattr(b, name)) - _ids(new) if name in ("rungs", "tracks") else set()
        if gone:
            col = Bet.rung_id if name == "rungs" else Bet.track_id
            held = s.scalars(select(Bet).where(Bet.business_id == bid, col.in_(gone),
                                               Bet.status.notin_([x.value for x in TERMINAL_BET_STATUSES]))).all()
            if held:
                raise ControlError(msg("roadmap.refuse.held", lang,
                                       bets=", ".join(f"BET-{x.id} {x.title[:40]}" for x in held)))
        if new != getattr(b, name):
            setattr(b, name, new)
            changed.append(name)
    rungs = _ids(b.rungs)
    for c in b.checkpoints:
        if c["rung_id"] and c["rung_id"] not in rungs:
            raise ControlError(msg("roadmap.refuse.checkpoint_rung", lang, title=c["title"]))
    if changed:
        activity.record(s, verb="edited", entity="business", entity_id=b.id, project_id=None, business_id=b.id,
                        summary=msg("roadmap.activity.lists_edited", lang, id=b.id, name=b.name[:120],
                                    lists=", ".join(msg(f"roadmap.list.{x}", lang) for x in changed)),
                        data={"lists": changed})
    return b


# ------------------------------------------------------------------ briefs

def _kickoff_live(s: Session, bid: int) -> bool:
    return s.scalar(select(KickoffSession.id).where(KickoffSession.business_id == bid,
                                                   KickoffSession.status.in_(KICKOFF_LIVE))) is not None


def refuse_while_kickoff_live(s: Session, bid: int, *, lang: Lang = UNMIGRATED) -> None:
    if _kickoff_live(s, bid):
        raise ControlError(msg("roadmap.refuse.kickoff_live", lang, id=bid))


def refuse_abandoned_draft(s: Session, bid: int, *, lang: Lang = UNMIGRATED) -> None:
    """The business's latest conversation was given up, so its draft is half filled until the owner re-opens it."""
    last = s.scalar(select(KickoffSession.status).where(KickoffSession.business_id == bid)
                    .order_by(KickoffSession.id.desc()).limit(1))
    if last == "ABANDONED":
        raise ControlError(msg("roadmap.refuse.abandoned", lang, id=bid))


def new_brief_version(s: Session, bid: int, *, sections: list[Any], assumptions: list[Any], author: str,
                      approved: bool = False, lang: Lang = UNMIGRATED) -> BusinessBrief:
    b = _business(s, bid, lang)
    secs = _validated(BriefSection, sections, "s", "sections", lang)
    if approved:
        refuse_while_kickoff_live(s, bid, lang=lang)
        refuse_abandoned_draft(s, bid, lang=lang)
        _refuse_unsettled_gates(bid, secs, lang)
    seen = set()
    for sec in secs:
        for line in sec["lines"]:
            line["id"] = line["id"] or f"l-{secrets.token_hex(3)}"
            if line["id"] in seen:
                raise ControlError(msg("roadmap.refuse.duplicate_line", lang, id=line["id"]))
            seen.add(line["id"])
    assump = _validated(Assumption, assumptions, "a", "assumptions", lang)
    if not approved and s.scalar(select(BusinessBrief.id).where(BusinessBrief.business_id == bid,
                                                               BusinessBrief.approved_at.is_(None))):
        raise ControlError(msg("roadmap.refuse.draft_exists", lang, id=bid))
    version = (s.scalar(select(func.max(BusinessBrief.version)).where(BusinessBrief.business_id == bid)) or 0) + 1
    brief = BusinessBrief(business_id=b.id, version=version, sections=secs, assumptions=assump, author=author,
                          approved_at=_now() if approved else None)
    s.add(brief)
    s.flush()
    if approved:
        _on_approved(s, brief, lang)
    return brief


def _refuse_unsettled_gates(bid: int, sections: list[Any], lang: Lang) -> None:
    if blockers := approval_blockers(sections, lang=lang):
        raise ControlError(msg("roadmap.refuse.gates", lang, id=bid, sections=", ".join(blockers)))


def _on_approved(s: Session, brief: BusinessBrief, lang: Lang) -> None:
    """What an approved brief sets going, however it became approved. The owner's deadline sets how long a cycle runs."""
    activity.record(s, verb="approved", entity="brief", entity_id=brief.id, project_id=None,
                    business_id=brief.business_id,
                    summary=msg("roadmap.activity.brief_approved", lang, id=brief.business_id, version=brief.version))
    business = _business(s, brief.business_id, lang)
    if days := deadline_days(brief.sections):
        business.cycle_days = days
    _refresh_project_goals(s, business, brief)
    _withdraw_derived_project_manager_rules(s, business, brief, lang)
    queue.enqueue(s, "derive_door_rules", {"brief_id": brief.id}, idempotency_key=f"door_rules:{brief.id}",
                  max_attempts=2)
    from justshipit.services.profile import infer  # infer reads the roadmap
    infer.on_approved(s, brief)


def _refresh_project_goals(s: Session, business: Business, brief: BusinessBrief) -> None:
    """A project started from the plan holds its goal as a copy of the plan's lines (`goal_from_brief`). A newly
    approved version rewrites each copy of the one before it, so no project keeps a goal the owner has changed."""
    before = s.scalar(select(BusinessBrief).where(BusinessBrief.business_id == business.id, BusinessBrief.id != brief.id,
                                                 BusinessBrief.approved_at.is_not(None))
                      .order_by(BusinessBrief.approved_at.desc(), BusinessBrief.version.desc()).limit(1))
    if before is None:
        return
    copies = {goal_from_brief(before, business, lang=lang): lang for lang in LANGS}
    for p in s.scalars(select(Project).where(Project.business_id == business.id, Project.status != "ARCHIVED")):
        if (lang := copies.get(p.goal)) is not None:
            p.goal = goal_from_brief(brief, business, lang=lang)
            continue
        # A stage's project holds the stage's lines, then the plan's (cycle.goal_for_stage).
        for old, lang in copies.items():
            if p.goal.endswith(f"\n\n{old}"):
                p.goal = p.goal[: -len(old)] + goal_from_brief(brief, business, lang=lang)
                break


def amend_line(s: Session, bid: int, line_id: str, *, text: str, target: GoalTarget | dict | None, source: str,
               quote: str, lang: Lang) -> BusinessBrief | None:
    """The owner's answer written into the plan: the next approved version, with the said or confirmed line `line_id`
    reading `text` as the owner's (`said`, `source` naming the answer, `quote` the option picked). The same answer
    again changes nothing. None when the approved plan has no such line."""
    if any(x["id"] == line_id and x.get("source") == source
           for b in s.scalars(select(BusinessBrief).where(BusinessBrief.business_id == bid))
           for sec in b.sections for x in sec["lines"]):
        return approved_brief(s, bid)
    brief = approved_brief(s, bid)
    sections = copy.deepcopy(brief.sections) if brief else []
    for sec in sections:
        for i, line in enumerate(sec["lines"]):
            if line["id"] == line_id and line["provenance"] in SETTLED:
                sec["lines"][i] = {**line, "text": text, "provenance": "said", "source": source, "quote": quote,
                                   "note": None,
                                   "target": checked_target(OLD_KEY_TO_NEW.get(sec["key"], sec["key"]), text, target)}
                return new_brief_version(s, bid, sections=sections, assumptions=brief.assumptions, author="owner",
                                         approved=True, lang=lang)
    return None


def approve_brief(s: Session, brief_id: int, *, lang: Lang) -> BusinessBrief:
    brief = s.scalar(select(BusinessBrief).where(BusinessBrief.id == brief_id).with_for_update())
    if brief is None:
        raise ControlError(msg("roadmap.refuse.no_brief", lang, id=brief_id))
    if brief.approved_at is None:
        refuse_while_kickoff_live(s, brief.business_id, lang=lang)
        refuse_abandoned_draft(s, brief.business_id, lang=lang)
        _refuse_unsettled_gates(brief.business_id, brief.sections, lang)
        brief.approved_at = _now()
        _on_approved(s, brief, lang)
    return brief


def current_brief(s: Session, bid: int) -> BusinessBrief | None:
    """The approved brief, or the open draft when nothing is approved yet."""
    return s.scalar(select(BusinessBrief).where(BusinessBrief.business_id == bid)
                    .order_by(BusinessBrief.approved_at.desc().nulls_last(), BusinessBrief.version.desc()).limit(1))


def approved_brief(s: Session, bid: int) -> BusinessBrief | None:
    """The brief the owner approved last. A version number only says when a draft was started, so a later
    approval of an older draft wins over an earlier approval of a newer one."""
    return s.scalar(select(BusinessBrief).where(BusinessBrief.business_id == bid, BusinessBrief.approved_at.is_not(None))
                    .order_by(BusinessBrief.approved_at.desc(), BusinessBrief.version.desc()).limit(1))


def goal_from_brief(brief: BusinessBrief, business: Business, *, lang: Lang = UNMIGRATED) -> str:
    """The first project's goal: the brief's one-liner, goal and scope in the owner's words. A line the owner could
    not answer (unknown) is left out."""
    by_key = lines_by_key(brief.sections)

    def lines(key: str) -> list[str]:
        return [neutralize(x["text"]) for x in by_key.get(key, []) if x["provenance"] in ("said", "confirmed", "guess")]
    parts = ["\n".join(lines("one_liner")) or business.one_liner]
    for key in ("goal_deadline", "scope"):
        if texts := lines(key):
            parts.append(f"{BRIEF_BY_KEY[key].text('title', lang)}: " + " / ".join(texts))
    return "\n\n".join([*parts, msg("roadmap.goal.plan", lang, version=brief.version)])


def draft_brief(s: Session, bid: int, *, lock: bool = False) -> BusinessBrief | None:
    q = select(BusinessBrief).where(BusinessBrief.business_id == bid, BusinessBrief.approved_at.is_(None))
    return s.scalar(q.with_for_update() if lock else q)


# ------------------------------------------------------------------ bets

_BET_PITCH_FIELDS = ("title", "pitch", "audience", "question", "appetite_days", "budget_usd", "exit_signal",
                     "circuit_breaker", "track_id", "rung_id", "run_mode")
_BET_PROGRESS_FIELDS = ("hill", "health", "outcome")
_BET_CHOICES = {"run_mode": RUN_MODES, "hill": HILL_POSITIONS, "health": BET_HEALTH}


def _bet(s: Session, bet_id: int, lang: Lang) -> Bet:
    bet = s.scalar(select(Bet).where(Bet.id == bet_id).with_for_update())
    if bet is None:
        raise ControlError(msg("roadmap.refuse.no_bet", lang, id=bet_id))
    return bet


def _check_refs(b: Business, lang: Lang, *, track_id: str | None = None, rung_id: str | None = None) -> None:
    if track_id and track_id not in _ids(b.tracks):
        raise ControlError(msg("roadmap.refuse.no_track", lang, id=b.id, track=track_id))
    if rung_id and rung_id not in _ids(b.rungs):
        raise ControlError(msg("roadmap.refuse.no_rung", lang, id=b.id, rung=rung_id))


def _checked_bet_value(name: str, value: Any, lang: Lang) -> Any:
    if name in _BET_CHOICES and value not in _BET_CHOICES[name]:
        raise ControlError(msg("roadmap.refuse.choice", lang, name=name, choices=", ".join(_BET_CHOICES[name])))
    if not isinstance(value, str):
        return value
    value = value.strip()
    if name in ("title", "pitch") and not value:
        raise ControlError(msg("roadmap.refuse.no_title", lang))
    return value[:300] if name == "title" else value


def create_bet(s: Session, bid: int, *, title: str, pitch: str, origin: str = "owner", lang: Lang = UNMIGRATED,
               **fields: Any) -> Bet:
    b = _business(s, bid, lang)
    if b.status == "ARCHIVED":
        raise ControlError(msg("roadmap.refuse.archived", lang, id=bid))
    unknown = set(fields) - set(_BET_PITCH_FIELDS)
    if unknown:
        raise ControlError(msg("roadmap.refuse.bet_fields", lang, fields=", ".join(sorted(unknown))))
    given = {**fields, "title": title, "pitch": pitch}
    values = {k: _checked_bet_value(k, v, lang) for k, v in given.items() if v is not None}
    _check_refs(b, lang, track_id=values.get("track_id"), rung_id=values.get("rung_id"))
    bet = Bet(business_id=b.id, origin=origin, status=BetStatus.PROPOSED.value, **values)
    s.add(bet)
    s.flush()
    return bet


def update_bet(s: Session, bet_id: int, *, lang: Lang = UNMIGRATED, **fields: Any) -> Bet:
    """The pitch and its limits change only before the bet runs; hill, health and outcome follow the run."""
    bet = _bet(s, bet_id, lang)
    status = BetStatus(bet.status)
    changed = {}
    for name, value in fields.items():
        if name not in _BET_PITCH_FIELDS + _BET_PROGRESS_FIELDS:
            raise ControlError(msg("roadmap.refuse.bet_fields", lang, fields=name))
        if value is None:
            continue
        value = _checked_bet_value(name, value, lang)
        if value == getattr(bet, name):
            continue
        if name in _BET_PITCH_FIELDS and status not in (BetStatus.PROPOSED, BetStatus.PICKED):
            raise ControlError(msg("roadmap.refuse.started", lang, id=bet.id))
        if name in _BET_PROGRESS_FIELDS and status in TERMINAL_BET_STATUSES and name != "outcome":
            raise ControlError(msg("roadmap.refuse.finished", lang, id=bet.id))
        if name in ("track_id", "rung_id"):
            _check_refs(_business(s, bet.business_id, lang), lang, **{name: value})
        changed[name] = [getattr(bet, name), value]
        setattr(bet, name, value)
    edited = {k: v for k, v in changed.items() if k not in activity.TRACKED["bets"]}
    if edited:
        activity.record(s, verb="edited", entity="bet", entity_id=bet.id, project_id=None, business_id=bet.business_id,
                        summary=msg("roadmap.activity.bet_edited", lang, id=bet.id, title=bet.title[:120],
                                    fields=", ".join(edited)),
                        data={k: v for k, v in edited.items() if k in _BET_PROGRESS_FIELDS or k == "title"})
    return bet


def bet_goal(bet: Bet, business: Business, brief: BusinessBrief | None, lang: Lang) -> str:
    """What the bet's run is told, and what the owner reads as its project's goal: the pitch, then the hypothesis and
    the limits it was promised under, so the run knows what it must find out and when to stop."""
    parts = [bet.pitch.strip() or bet.title]
    for field in ("question", "audience", "exit_signal", "circuit_breaker"):
        if value := getattr(bet, field):
            parts.append(msg(f"roadmap.goal.{field}", lang, text=value.strip()))
    limit = [*([msg("roadmap.goal.days", lang, n=bet.appetite_days)] if bet.appetite_days else []),
             *([msg("roadmap.goal.budget", lang, usd=f"{bet.budget_usd:g}")] if bet.budget_usd else [])]
    if limit:
        parts.append(msg("roadmap.goal.limits", lang, limits=", ".join(limit)))
    parts.append(" ".join([msg("roadmap.goal.business", lang, id=business.id, name=business.name),
                           *([msg("roadmap.goal.plan", lang, version=brief.version)] if brief else [])]))
    return "\n\n".join(parts)


def is_legacy_bet(bet: Bet) -> bool:
    """A running bet the loop did not start: one that was already running when the loop shipped. The loop neither
    sweeps it nor counts it, and the owner ends it."""
    return bet.status == BetStatus.RUNNING.value and bet.loop_started_at is None


def running_count(s: Session, business_id: int, *, lock: bool = False, lang: Lang = UNMIGRATED) -> int:
    """Bets of the business the loop is running or listening on: a bet holds its slot for its whole window, not only
    while its run works. `lock` takes the business row first, so a caller that is about to start a bet counts and
    starts under one lock, and two starts cannot both see a free slot."""
    if lock:
        _business(s, business_id, lang)
    return s.scalar(select(func.count()).select_from(Bet).where(
        Bet.business_id == business_id, Bet.status.in_(LIVE_BET_STATUSES), Bet.loop_started_at.is_not(None))) or 0


def promised_days(s: Session, bid: int) -> int | None:
    """The deadline the business's approved brief states, in days, or None."""
    brief = approved_brief(s, bid)
    return deadline_days(brief.sections) if brief else None


def cycle_days(s: Session, b: Business) -> int:
    """How long the business's cycle runs: the approved brief's deadline, read each time so a business approved before
    deadlines were read from briefs follows its brief too, else the stored length."""
    return promised_days(s, b.id) or b.cycle_days


def window_end(s: Session, bet: Bet) -> datetime | None:
    """When a started bet's market window closes, counted from the start: its own days (`appetite_days`); for a bet
    promised none, the deadline the approved brief states, else `bets.window_days`. The brief is read each time, so a
    bet started before its brief set the window runs to the brief's deadline without its row being rewritten."""
    if bet.started_at is None:
        return None
    days = bet.appetite_days or promised_days(s, bet.business_id) or app_settings.effective().bets.window_days
    return bet.started_at + timedelta(days=days)


def window_view(s: Session, bet: Bet, now: datetime) -> dict[str, Any] | None:
    """A live loop bet's window as the console shows it: when it closes, the days left, and what came in since the
    bet started. The counts are the business's, since posts, tasks and numbers carry no bet."""
    end = window_end(s, bet)
    if bet.status not in LIVE_BET_STATUSES or bet.loop_started_at is None or end is None:
        return None

    def count(model, *where) -> int:
        return s.scalar(select(func.count()).select_from(model).where(model.business_id == bet.business_id, *where)) or 0

    done = (OwnerTask.status == "DONE", OwnerTask.done_at >= bet.started_at)
    people = people_link.provider()
    return {"ends_at": end, "days_left": max(0, math.ceil((end - now).total_seconds() / 86400)),
            "so_far": {"posts": count(OwnerTask, OwnerTask.kind == "post", *done), "owner_tasks": count(OwnerTask, *done),
                       "numbers": count(MetricPoint, MetricPoint.value.is_not(None), MetricPoint.observed_at >= bet.started_at,
                                     MetricPoint.metric.not_in(metrics.HIDDEN_METRICS)),
                       "contacted": people.north_star(s, bet.business_id)["contacted"] if people.available else None}}


def _take_slot(s: Session, bet: Bet, lang: Lang) -> Business:
    business = _business(s, bet.business_id, lang)
    if business.status == "ARCHIVED":
        raise ControlError(msg("roadmap.refuse.archived", lang, id=business.id))
    cap, taken = app_settings.effective().bets.max_running, running_count(s, business.id, lock=True, lang=lang)
    if taken >= cap:
        raise ControlError(msg("roadmap.refuse.slots", lang, bet=bet.id, business=business.id, n=taken, cap=cap))
    return business


def _start_run(s: Session, bet: Bet, lang: Lang, *, by_owner: bool = False) -> None:
    """The move to RUNNING starts the bet's run: the project linked to the bet, with the pitch, hypothesis and limits
    in its goal and the bet's budget as its budget, so the budget check that stops every run stops this one. A bet
    that already has a project (one adopted from a project) reuses it. An `owner` bet runs by hand and starts nothing.
    At most `bets.max_running` bets of a business run at once. The bet is marked as started by the loop, which is
    what makes the sweep and the retro act on it. `by_owner`: the owner's own move started it, so the run is not parked."""
    business = _take_slot(s, bet, lang)
    bet.loop_started_at = _now()
    if bet.run_mode != "ai":
        return
    run = s.scalar(select(Project).where(Project.bet_id == bet.id))
    if run is None:
        run = Project(slug=unique_slug(s, Project.slug, f"bet-{bet.id}"), name=bet.title[:200],
                      goal=bet_goal(bet, business, approved_brief(s, business.id), lang), status="CREATED",
                      budget_usd=bet.budget_usd or float(limits()["project_budget_usd"]), context={},
                      business_id=business.id, bet_id=bet.id)
        s.add(run)
        s.flush()
    if run.status == "CREATED":
        queue.enqueue(s, "run_project", {"project_id": run.id}, idempotency_key=f"run:{run.id}", max_attempts=1,
                      by_owner=by_owner)


def _resume_review(s: Session, bet: Bet, lang: Lang) -> None:
    """REVIEW back to RUNNING, for a run the owner retried before any retro was stored. It needs a free slot, since
    REVIEW holds none, and it starts nothing: the run is already there."""
    if bet.retro is not None:
        raise ControlError(msg("roadmap.refuse.has_retro", lang, id=bet.id))
    _take_slot(s, bet, lang)
    bet.end_cause, bet.finished_at = None, None


def move_bet(s: Session, bet_id: int, to: BetStatus | str, *, reason: str | None = None,
             cycle_id: int | None = None, end_cause: str | None = None, lang: Lang = UNMIGRATED,
             by_owner: bool = False) -> Bet:
    """Move a bet along its lifecycle. A move to the status it already has does nothing, so a repeated
    click is safe. Any move outside BET_TRANSITIONS is refused.

    RUNNING starts the run. LISTENING means the run finished and the bet waits out its window for market response.
    REVIEW means the bet is over (its window closed, its run failed or hit a limit, or the owner ended it; `end_cause`
    says which, and the owner's own move is `owner`) and queues the one retro that settles the bet into DONE or
    STOPPED. REVIEW stamps `finished_at`, which closes the window the retro reads. `by_owner`: the owner's button made
    the move, so the run it starts and the retro it asks for are not parked for a stage (the sweep's moves are)."""
    bet = _bet(s, bet_id, lang)
    cur = BetStatus(bet.status)
    try:
        new = BetStatus(to)
    except ValueError:
        raise ControlError(msg("roadmap.refuse.not_a_status", lang, to=to)) from None
    if new == cur:
        return bet
    if new not in BET_TRANSITIONS[cur]:
        raise ControlError(msg("roadmap.refuse.move", lang, id=bet.id, was=cur.value, to=new.value))
    if cycle_id is not None:
        cycle = s.get(Cycle, cycle_id)
        if cycle is None or cycle.business_id != bet.business_id or cycle.closed_at is not None:
            raise ControlError(msg("roadmap.refuse.not_open_cycle", lang, id=cycle_id))
    if new == BetStatus.RUNNING:
        if cur == BetStatus.REVIEW:
            _resume_review(s, bet, lang)
        else:
            _start_run(s, bet, lang, by_owner=by_owner)
    if cycle_id is not None:
        bet.cycle_id = cycle_id
    if new == BetStatus.PROPOSED:
        bet.picked_at, bet.cycle_id = None, None
    elif cur == BetStatus.REVIEW and new == BetStatus.RUNNING:
        bet.started_at = bet.started_at or _now()
    elif new in _STAMP and (new not in (BetStatus.DONE, BetStatus.STOPPED) or bet.finished_at is None):
        setattr(bet, _STAMP[new], _now())
    if reason:
        bet.status_reason = reason.strip()[:1000]
    if new in (BetStatus.DONE, BetStatus.STOPPED, BetStatus.REVIEW):
        run = s.scalar(select(Project).where(Project.bet_id == bet.id))
        if run is not None and run.status in ("CREATED", "RUNNING", "WAITING_HUMAN"):
            control.pause(s, run.id, reason or msg("roadmap.bet.paused", lang), lang=lang)
    if new == BetStatus.REVIEW:
        bet.end_cause = end_cause or "owner"
        queue_retro(s, bet.id, owner=by_owner)
    bet.status = new.value
    return bet


RETRO_ROUNDS = 3


def _retro_jobs(bet_id: int):
    return select(Job).where(Job.kind == "bet_retro", Job.payload["bet_id"].as_integer() == bet_id)


def retro_rounds(s: Session, bet_id: int) -> int:
    """How many times a retro has been queued for the bet."""
    return s.scalar(select(func.count()).select_from(_retro_jobs(bet_id).subquery())) or 0


def retro_live(s: Session, bet_id: int) -> bool:
    return s.scalar(_retro_jobs(bet_id).where(queue.in_flight()).limit(1)) is not None


def retro_stuck(s: Session, bet: Bet) -> bool:
    """A bet in review with no retro, no retro job alive and every automatic round spent: only the owner can start another."""
    return (bet.status == BetStatus.REVIEW.value and bet.retro is None and not retro_live(s, bet.id)
            and retro_rounds(s, bet.id) >= RETRO_ROUNDS)


def retro_budget_stop(s: Session, bet: Bet, *, lang: Lang) -> dict | None:
    """The budget that refused the bet's last retro round while it still has no room, so another round would be refused
    too: the cap, the run whose budget the owner raises, and the owner's sentence. None otherwise."""
    from justshipit.services import budget
    last = s.scalar(_retro_jobs(bet.id).order_by(Job.id.desc()).limit(1))
    cap = budget.refused_by(last.last_error) if last is not None and last.status == "failed" else None
    if bet.retro is not None or cap is None:
        return None
    pid = s.scalar(select(Project.id).where(Project.bet_id == bet.id))
    if not budget.still_short(s, cap, pid):
        return None
    return {"cap": cap, "project_id": pid if cap == "project" else None,
            "says": budget.stop_line(s, cap, pid, msg("roadmap.retro.budget_stop", lang), lang=lang)}


def queue_retro(s: Session, bet_id: int, *, owner: bool = False) -> Job | None:
    """The next round of the bet's retro, under a key of its own so an earlier round that ended without a retro does not
    block it. Nothing is queued while a round is alive, and the sweep stops after `RETRO_ROUNDS`; the owner's own
    request (`owner`) goes past that and runs whatever stage is open."""
    n = retro_rounds(s, bet_id)
    if retro_live(s, bet_id) or (n >= RETRO_ROUNDS and not owner):
        return None
    return queue.enqueue(s, "bet_retro", {"bet_id": bet_id}, idempotency_key=f"bet_retro:{bet_id}:{n + 1}",
                         max_attempts=3, by_owner=owner)


def adopt_project(s: Session, p: Project, *, running: bool, business: Business | None = None,
                  stage: CycleStage | None = None) -> Bet | None:
    """Give a project the business and bet it runs for. A project started for an existing business joins it;
    a reassessment child joins its parent's business as a new bet; anything else starts a business of its
    own. The business's first bet runs until the deadline of its approved brief. Calling it again changes nothing.
    A stage's project, and a child of one, gets no bet: a bet carries an experiment's slot, window and retro, and the
    stage is what the owner started (its run joins the stage, services/queue.py)."""
    if p.bet_id is not None:
        return s.get(Bet, p.bet_id)
    parent_id = (p.context or {}).get("parent_project_id")
    parent = s.get(Project, parent_id) if parent_id else None
    if business is None and parent is not None and parent.business_id is not None:
        business = s.get(Business, parent.business_id)
    if stage is not None or (parent is not None and parent.stage_id is not None):
        p.business_id = (business or s.get(Business, stage.business_id)).id
        return None
    if business is None:
        business = create_business(s, name=p.name.strip() or p.slug,
                                   one_liner=p.goal.splitlines()[0] if p.goal else p.name, slug=p.slug)
    first = s.scalar(select(Bet.id).where(Bet.business_id == business.id).limit(1)) is None
    brief = approved_brief(s, business.id) if first else None
    now = _now()
    bet = Bet(business_id=business.id, title=p.name[:300], pitch=p.goal, budget_usd=p.budget_usd,
              appetite_days=deadline_days(brief.sections) if brief else None,
              origin="project_manager" if parent is not None else "owner",
              status=(BetStatus.RUNNING if running else BetStatus.PICKED).value,
              picked_at=now, started_at=now if running else None, loop_started_at=now if running else None)
    s.add(bet)
    s.flush()
    p.business_id, p.bet_id = business.id, bet.id
    return bet


# ------------------------------------------------------------------ read models

def _row(o) -> dict[str, Any]:
    return {c.name: getattr(o, c.name) for c in o.__table__.columns}


def hidden_business(b: Business, include_archived: bool, include_test: bool) -> bool:
    return (b.status == "ARCHIVED" and not include_archived) or (b.name.startswith("[TEST]") and not include_test)


def current_rung(rungs: list[dict]) -> dict | None:
    return next((r for r in rungs if not r.get("reached_at")), None)


def business_list(s: Session, *, include_archived: bool = False, include_test: bool = False) -> list[dict]:
    counts: dict[int, dict[str, int]] = {}
    for bid, st, n in s.execute(select(Bet.business_id, Bet.status, func.count()).group_by(Bet.business_id, Bet.status)):
        counts.setdefault(bid, {})[st] = n
    briefs = dict(s.execute(select(BusinessBrief.business_id, func.max(BusinessBrief.version))
                            .where(BusinessBrief.approved_at.is_not(None)).group_by(BusinessBrief.business_id)).all())
    runs: dict[int, list[int]] = {}
    for pid, bid in s.execute(select(Project.id, Project.business_id).where(Project.business_id.is_not(None))):
        runs.setdefault(bid, []).append(pid)
    cycles = dict(s.execute(select(Cycle.business_id, Cycle.id).where(Cycle.closed_at.is_(None))).all())
    out = []
    for b in s.scalars(select(Business).order_by(Business.id.desc())):
        if hidden_business(b, include_archived, include_test):
            continue
        by = counts.get(b.id, {})
        out.append({**_row(b), "ref": f"B-{b.id}", "is_test": b.name.startswith("[TEST]"), "bets_by_status": by,
                    "bets_total": sum(by.values()), "bets_running": sum(by.get(st, 0) for st in LIVE_BET_STATUSES),
                    "current_rung": current_rung(b.rungs), "brief_version": briefs.get(b.id),
                    "project_ids": sorted(runs.get(b.id, [])), "cycle_id": cycles.get(b.id)})
    return out


def _attention(bet: Bet, run: Project | None, today: date) -> list[str]:
    out = []
    if is_legacy_bet(bet):
        return ["owner_cleanup"]
    if bet.status == BetStatus.RUNNING.value:
        if run is not None and run.status in ("DONE", "ARCHIVED"):
            out.append("run_finished")
        if run is not None and run.status == "FAILED":
            out.append("run_failed")
        if bet.appetite_days and bet.started_at and (today - bet.started_at.date()).days > bet.appetite_days:
            out.append("over_appetite")
        if bet.budget_usd and run is not None and run.spent_usd > bet.budget_usd:
            out.append("over_budget")
    return out


def roadmap(s: Session, bid: int, *, lang: Lang) -> dict:
    b = _business(s, bid, lang, lock=False)
    now = _now()
    today = now.date()
    bets = s.scalars(select(Bet).where(Bet.business_id == bid).order_by(Bet.id)).all()
    runs = {p.bet_id: p for p in s.scalars(select(Project).where(Project.business_id == bid, Project.bet_id.is_not(None)))}
    cycles = s.scalars(select(Cycle).where(Cycle.business_id == bid).order_by(Cycle.number)).all()
    open_cycle_row = next((c for c in cycles if c.closed_at is None), None)
    brief = current_brief(s, bid)
    cur = current_rung(b.rungs)
    rungs = []
    for r in b.rungs:
        state = "reached" if r.get("reached_at") else ("current" if r is cur else "ahead")
        rungs.append({**r, "state": state})
    from justshipit.engineering import status as dev_status
    bet_rows = []
    dev = dev_status.by_project(s, [p.id for p in runs.values()])
    for bet in bets:
        run = runs.get(bet.id)
        refused = retro_budget_stop(s, bet, lang=lang) if bet.status == BetStatus.REVIEW.value else None
        retro = ["retro_budget"] if refused else ["retro_stuck"] if retro_stuck(s, bet) else []
        bet_rows.append({**_row(bet), "ref": f"BET-{bet.id}", "legacy": is_legacy_bet(bet), "budget_stop": refused,
                         "run": {"project_id": run.id, "ref": f"P-{run.id}", "name": run.name, "status": run.status,
                                 "phase": run.phase, "spent_usd": run.spent_usd,
                                 "stage": dev_status.project_stage(run.status, dev.get(run.id, dev_status.NO_DEV))}
                         if run else None,
                         "window": window_view(s, bet, now),
                         "attention": _attention(bet, run, today) + retro})
    lanes = {"now": [x.id for x in bets if x.status in (*LIVE_BET_STATUSES, BetStatus.REVIEW.value)], "next": [x.id for x in bets if x.status == "PICKED"],
             "later": [x.id for x in bets if x.status == "PROPOSED"],
             "done": [x.id for x in bets if x.status in ("DONE", "STOPPED", "DROPPED")]}
    return {
        "business": {**_row(b), "ref": f"B-{b.id}", "cycle_days": cycle_days(s, b)},
        "brief": _row(brief) if brief else None,
        "rungs": rungs, "tracks": b.tracks, "models": b.models, "door_policy": b.door_policy,
        "checkpoints": [{**c, "days_until": (date.fromisoformat(c["on"]) - today).days} for c in b.checkpoints],
        "cycle": {**_row(open_cycle_row), "days_left": (open_cycle_row.ends_on - today).days} if open_cycle_row else None,
        "cycles": [_row(c) for c in cycles],
        "bets": bet_rows,
        "lanes": lanes,
        "limits": {"max_running": app_settings.effective().bets.max_running, "running": running_count(s, bid, lang=lang),
                   "max_routines": app_settings.effective().bets.max_routines},
    }
