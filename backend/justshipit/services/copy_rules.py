"""What public copy (a post's title and body the owner pastes as is) may never carry.

Two rules, each a deterministic check, because both failures read as fine to a model:

- No invented experience. A draft is in the owner's voice, so a sentence that says what the owner tried, used, quit or
  went through puts words in the owner's mouth. In one business the owner's interview notes about someone else became
  a sentence saying the owner had tried a pet-sitter app and quit. The owner writes their own experience; a draft
  leaves the placeholder (`PLACEHOLDER`) there.
- No internal notes. Evidence ids, brief line ids, task and request ids, the company's own words for its plan, draft
  markers and "utm:" are the company's notes to itself or to the owner, and they leaked into a job post.

Korean drops the subject, so the experience check reads the whole body: a body written in the first person may not
hold a past-experience verb anywhere outside a bracketed placeholder. The words are in `i18n/match/copy_rules.py`.
"""
from __future__ import annotations

import re

from justshipit.i18n import LANGS
from justshipit.i18n.match import copy_rules as words
from justshipit.i18n.said import Said, said

PLACEHOLDER = words.PLACEHOLDER

_BRACKETED = re.compile(r"\[[^\]]*\]")
_FIRST_PERSON = re.compile("|".join(w for lang in LANGS for w in words.FIRST_PERSON[lang]))
_EXPERIENCE = re.compile("|".join(w for lang in LANGS for w in words.EXPERIENCE[lang]), re.IGNORECASE)
_SENTENCE = re.compile(r"[^.!?。！？\n]+[.!?。！？]?")

# Each pattern and the kind of note it gives away (`post.internal.<kind>`).
_INTERNAL = (
    (re.compile(r"\[E\d+\]|(?<![A-Za-z])EV-\d+", re.IGNORECASE), "evidence_id"),
    (re.compile(r"(?<![A-Za-z0-9-])l-[0-9a-f]{6}(?![0-9a-f])"), "plan_line_id"),
    (re.compile(r"(?<![A-Za-z])(?:T|REQ|OT|DEC|J|P|B)-\d+"), "company_id"),
    *((re.compile(pattern, re.IGNORECASE), kind) for lang in LANGS for pattern, kind in words.INTERNAL[lang]),
    (re.compile(r"utm\s*:", re.IGNORECASE), "link_note"),
    (re.compile(r"(?<![A-Za-z])TODO(?![A-Za-z])"), "todo_note"),
)


def _excerpt(text: str, at: int) -> str:
    start = max(0, at - 12)
    return text[start:start + 40].strip()


def invented_experience(text: str) -> str | None:
    """The sentence of a first-person draft that states an experience, or None."""
    bare = _BRACKETED.sub(" ", text)
    if not _FIRST_PERSON.search(bare):
        return None
    for sentence in _SENTENCE.findall(bare):
        if _EXPERIENCE.search(sentence):
            return sentence.strip()[:60]
    return None


def internal_note(text: str) -> Said | None:
    """Why the text reads as a note the company left for itself, with the words that gave it away, or None."""
    for pattern, kind in _INTERNAL:
        if m := pattern.search(text):
            return said("post.internal.found", what=said(f"post.internal.{kind}"), excerpt=_excerpt(text, m.start()))
    return None


def public_copy_problem(*texts: str | None) -> tuple[Said, str] | None:
    """Why this public copy may not go on a card, as (the reason, the rule that fired), or None."""
    for text in filter(None, texts):
        if found := invented_experience(text):
            return said("post.copy.invented", found=found, placeholder=said("post.placeholder")), "invented_experience"
        if found := internal_note(text):
            return said("post.copy.internal", found=found), "internal_note"
    return None
