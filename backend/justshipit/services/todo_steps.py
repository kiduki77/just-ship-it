"""A to-do's steps: the separate actions of a to-do that holds more than one, or runs longer than one sitting, in the
order the owner does them, so the owner finishes one sitting at a time and ticks each step off.

Steps are read from the card, not stored, so every to-do already made has them and a revised card's steps follow its
new text. A to-do about a person is reaching out (or sending the company's message) and then the talk itself; onboarding
is finding a time and then the session. Any other to-do's steps are the numbered lines the company wrote: the steps
handout, else what to do, else the background ("1. …" lines, or "1) … 2) …" inside a paragraph). A to-do with one
short action has none.

The owner's ticks are the only state, kept on the card as the keys of the steps ticked (`steps_done`). A step's key
is its role for a person's steps and a hash of its words for a numbered line (with its count for a repeated line), so
a tick never lands on a different step after the card changes.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from justshipit.contracts import owner_text
from justshipit.i18n import LANGS, Lang, msg
from justshipit.i18n.match import steps as step_words

SITTING_MIN = 20
STEP_MIN = {"send": 5, "reach": 5, "talk": 15, "session": 25}
DONE_KEY = "steps_done"

# A step's number at a line's start or after a space, then "." or ")" and a space: "1. ", "2) ". "1.5 hours" is not one.
_MARK = re.compile(r"(?:^|(?<=\s))(\d{1,2})[.)]\s+")
_LEAD = re.compile("(?:" + "|".join(p for lang in LANGS for p in step_words.LEAD[lang]) + r")\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Step:
    key: str
    text: str
    minutes: int


def _starts_line(text: str, m: re.Match[str]) -> bool:
    return not text[text.rfind("\n", 0, m.start()) + 1:m.start()].strip()


def numbered(text: str, *, lead_needed: bool = False) -> list[str]:
    """The text's numbered steps in order, from the first run that counts 1, 2, 3, or [] when it has fewer than two.
    A step is its line and the indented lines under it, so a paragraph or a closing line between or under the steps
    stays out. With `lead_needed`, a run inside a paragraph counts only after a word that opens steps ("Steps:"), as
    "Decide where to post: 1) the group chat, 2) the cafe" lists options, not steps."""
    run: list[re.Match[str]] = []
    for m in _MARK.finditer(text):
        if int(m[1]) == len(run) + 1:
            run.append(m)
    if len(run) < 2:
        return []
    if lead_needed and not all(_starts_line(text, m) for m in run) and not _LEAD.search(text[:run[0].start()]):
        return []
    ends = [m.start() for m in run[1:]] + [len(text)]
    out = []
    for m, end in zip(run, ends, strict=True):
        first, *rest = text[m.end():end].split("\n")
        kept = [first]
        for line in rest:
            if not (line.strip() and line[:1].isspace()):
                break
            kept.append(line)
        out.append(" ".join(" ".join(kept).split()))
    return out if all(out) else []


def _key(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def _person(card: dict, kind: str, lang: Lang) -> list[Step]:
    handout = card.get("handout") or {}
    message = handout.get("kind") == "message" and bool(handout.get("body"))
    if kind == "onboard":
        return [Step("reach", msg("owner_task.step.onboard_reach", lang), STEP_MIN["reach"]),
                Step("session", msg("owner_task.step.onboard_session", lang), STEP_MIN["session"])]
    if kind != "contact" and not message:
        return []
    n = len(card.get("questions") or [])
    first = Step("send", msg("owner_task.step.send", lang), STEP_MIN["send"]) if message \
        else Step("reach", msg("owner_task.step.reach", lang), STEP_MIN["reach"])
    talk = msg("owner_task.step.talk", lang, n=n) if n else msg("owner_task.step.talk_plain", lang)
    return [first, Step("talk", talk, STEP_MIN["talk"])]


def _written(card: dict) -> tuple[str | None, list[str]]:
    """The card field the numbered steps were read from, and the steps."""
    handout = card.get("handout") or {}
    sources = {"handout": handout.get("body") if handout.get("kind") == "steps" else None, "what": card.get("what"),
               "background": card.get("background")}
    return next(((field, found) for field, text in sources.items()
                 if isinstance(text, str) and (found := numbered(text, lead_needed=field != "handout"))), (None, []))


def source(card: dict | None) -> str | None:
    """Where the to-do's steps come from: "person", or the field of the numbered lines ("handout", "what",
    "background"), so the console can fold the text the steps repeat. None for a to-do with no steps."""
    card = card or {}
    if card.get("person"):
        return "person"
    return _written(card)[0]


def of(card: dict | None, kind: str, estimate_min: int, lang: Lang) -> list[Step]:
    """The to-do's steps in order, or [] for a to-do that is one short action. `estimate_min` is the kind's guess
    (today.ESTIMATE_MIN), shared out over numbered steps."""
    card = card or {}
    if card.get("person"):
        return _person(card, kind, lang)
    lines = _written(card)[1]
    each = max(1, round(estimate_min / len(lines))) if lines else 0
    return [Step(_key(line) + (f"-{n}" if (n := lines[:i].count(line)) else ""), line, each)
            for i, line in enumerate(lines)]


def for_task(task, lang: Lang) -> list[Step]:
    """`of` for a stored to-do, its numbered steps sharing the kind's guess at its minutes."""
    from justshipit.services.today import ESTIMATE_MIN
    return of(task.card, task.kind, ESTIMATE_MIN[task.kind], lang)


def done_keys(card: dict | None) -> set[str]:
    return set((card or {}).get(DONE_KEY) or [])


def view(steps: list[Step], card: dict | None) -> list[dict]:
    ticked = done_keys(card)
    return [{"key": x.key, "text": owner_text.detail(x.text), "minutes": x.minutes, "done": x.key in ticked}
            for x in steps]


def next_min(steps: list[Step], card: dict | None) -> int:
    """The minutes of the next step not ticked yet, at least one: Today plans one sitting of a to-do, so reaching out
    to six people fits a half-hour day while their talks wait for the days they're booked. All ticked leaves saying
    it is done."""
    ticked = done_keys(card)
    return max(1, next((x.minutes for x in steps if x.key not in ticked), 0))


def tick(card: dict, steps: list[Step], key: str, done: bool) -> dict:
    """The card with step `key` ticked or not. Raises KeyError for a step the to-do does not have."""
    if key not in {x.key for x in steps}:
        raise KeyError(key)
    ticked = done_keys(card) - {key} | ({key} if done else set())
    return {**card, DONE_KEY: [x.key for x in steps if x.key in ticked]}
