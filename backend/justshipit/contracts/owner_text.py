"""Text the owner reads, checked where it enters the system.

The owner reads a card like a report to a superior: a one-line conclusion, what to do, one line of why. Agents leak
their working vocabulary into that text: brief line ids (`l-972536`), field names (`open_questions`), flags
(`contested=False`), registry paths, skill names and commit hashes. The prompts ask them not to; these functions make
it so. `one_line` is for the lines read first and also drops evidence ids, hashes, code names and markdown; `detail`
keeps evidence ids, which the console links, code names an engineer may ask about, and the text's lines. `body` is
for a request's own summary and rationale, which carry an engineer's question or a skill's file path to the owner and
back: it drops only brief line ids and skill names, so `retry=True` or `registry/skills/x/` reaches the owner as written.
"""
from __future__ import annotations

import math
import re
from functools import partial
from typing import Annotated

from pydantic import AfterValidator

from justshipit.i18n import LANGS, Lang
from justshipit.i18n.match import owner_text as words
from justshipit.i18n.prompting import EN_LENGTH_FACTOR

_A = r"A-Za-z0-9"  # ASCII only: Python's \b treats a Hangul syllable as a word character, so "l-d4bdf1의" has no \b


def _any(table: dict[Lang, tuple[str, ...]]) -> str:
    """One alternation over every language's fragments, in the table's order (longer forms are listed first)."""
    return "|".join(w for lang in LANGS for w in table[lang]) or "(?!)"


def _hangul(text: str) -> int:
    return sum(0xAC00 <= ord(ch) <= 0xD7A3 for ch in text)


def _lang_of(text: str) -> Lang:
    """The language a field name in this text is said in: Korean when the text has any Hangul, which English text
    does not."""
    return "ko" if _hangul(text) else "en"

# Dropped from every owner text. Code identifiers, paths and hashes stay in detail text: an engineer's question can
# be about `quote_parser.py`, and only the lines read first must be plain.
_NEVER = [
    re.compile(rf"(?<![{_A}-])l-[0-9a-f]{{6}}(?![{_A}])"),                          # brief line ids
    re.compile(rf"(?<![{_A}/])/?pstack:[A-Za-z0-9_-]+"),                            # skill names
]
_INTERNAL = _NEVER + [
    re.compile(rf"(?<![{_A}_])[a-z_]+=(?:True|False|None)(?![{_A}])"),              # flags
    re.compile(r"(?<![\w/.])registry/[A-Za-z0-9_./-]+"),                          # registry paths; var/ is the owner's
]
_CODE_NAME = re.compile(rf"(?<![{_A}_/.-])(?:[a-z][a-z0-9]*(?:_[a-z0-9]+)+|[A-Z]{{2,}}(?:[-_][A-Z]{{2,}})+)(?![{_A}_/.-])")
# Another record's code ("REQ-32", "T-30", a spec's story or scenario "US1.3", a spec key like "BUSINE2-0004"), with
# the ones run on after it ("REQ-17~20·REQ-25") and the particle or colon stuck to it ("OT-14에", "REQ-5:"). The console shows a record's own
# code beside its title and links the codes in a body, so a line read first has no use for one.
# A spec key starts with the project's slug, which can start with a digit ("3MAYAA-0001"); its number is 0-padded,
# so a digit-led one is told from a word with a year ("5G-2030", "4K-2160") by the 0.
_RECORD = r"(?:REQ|OT|BET|IDEA|DEC|T|J|M|P|B)-\d+|US\d{1,2}(?:\.\d+)*|[A-Z][A-Z0-9]{1,7}-\d{4}|\d(?=[A-Z0-9]*[A-Z])[A-Z0-9]{1,7}-0\d{3}"
_RECORDS = re.compile(rf"(?<![{_A}/-])(?:{_RECORD})(?:\s*~\s*(?:{_RECORD}|\d+)|\s*[·,/]\s*(?:{_RECORD}))*"
                      rf"(?![{_A}-])(?:{_any(words.PARTICLES)}|[:.](?=\s|$))?")
# Also dropped from the lines read first.
_LINE_ONLY = [
    _RECORDS,
    re.compile(rf"(?<![{_A}/])(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{{7,40}}(?![{_A}])"),  # commit hashes
    re.compile(r"(?<![\w/.])projects/[A-Za-z0-9_./-]+"),                                 # registry paths
    re.compile(rf"(?<![{_A}-])EV-\d+(?:{_any(words.EVIDENCE_SUFFIX)})?"),           # evidence ids
    re.compile(r"\[E\d+(?:\s*[,·]\s*E?\d+)*\]"),
    re.compile(r"\[[a-z][a-z_-]*\]"),                                               # a code tag: "[conditional]"
    re.compile(rf"(?<![{_A}_])[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+=[^\s,;)\]]+"),          # a setting: "AI_PROVIDER=stub"
]
_GONE = "\x00"  # where a token was dropped, so tidying touches only those places and leaves the rest as written
_LEFT = rf"[\s,·/…{_GONE}]*{_GONE}[\s,·/…{_GONE}]*"
_EMPTIED = [
    re.compile(rf"[ \t]*\(\s*(?:{_any(words.CITE)})?{_LEFT}\)", re.IGNORECASE),  # "(plan l-972536, l-c4668e…)"
    re.compile(rf"[ \t]*\[{_LEFT}\]"),
    re.compile(rf"(?:[ \t]*[,·][ \t]*{_GONE})+(?=[ \t]*[)\]])"),  # "(the owner's final, REQ-25 related)"
    re.compile(rf"(?<=[(\[]){_GONE}(?:[ \t]*[,·][ \t]*{_GONE})*[ \t]*"),  # "(T-30 as the master, …)"
    re.compile(rf"(?:[ \t]*[,·])?[ \t]*{_GONE}(?:[ \t]*[,·][ \t]*{_GONE})*"),  # "stub AI, PORT=1 and" keeps no comma
]
# What a dropped code was said to do to its record goes with it: "(REQ-26 follow-up)" is about REQ-26 alone.
_REFERENCE = re.compile(rf"{_GONE}[ \t]*(?:{_any(words.REFERENCE)})(?=\s*[)\]])", re.IGNORECASE)
_MARKDOWN = re.compile(r"\*\*|__|`|^\s*#+\s*|^\s*[-*]\s+", re.MULTILINE)
_SENTENCE_END = re.compile(r"[.!?。！？](?=\s)")
_URL = re.compile(r"https?://\S+")


def leaks(text: str, *, line: bool = False) -> list[str]:
    """The internal ids and names in a text the owner reads: brief line ids, skill names, flags, registry paths and
    code names, and with `line=True` what a line read first also drops (other records' codes, hashes, evidence ids,
    settings with their values).
    The scrub's own patterns, so a builder's tests hold its output to the contract rather than to a list of their own.
    """
    found = []
    for part in _URL.split(text or ""):
        for pattern in _INTERNAL + (_LINE_ONLY if line else []) + [_CODE_NAME]:
            found += [m.group(0) for m in pattern.finditer(part)
                      if pattern is not _CODE_NAME or "-" not in m.group(0)]
    return found


def _glossary(m: re.Match) -> str:
    return words.GLOSSARY[_lang_of(m.string)].get(m.group(0), m.group(0))


def _glossary_or_drop(m: re.Match) -> str:
    """A field or constant name goes; a dashed capital name like AI-OS or PR-FAQ is a word the owner uses and stays."""
    name = m.group(0)
    return words.GLOSSARY[_lang_of(m.string)].get(name, name if "-" in name else _GONE)


def _tidy(text: str) -> str:
    text = _REFERENCE.sub(_GONE, text)
    for pattern in _EMPTIED:
        text = pattern.sub("", text)
    return text


def scrub(text: str, *, line: bool = False, names: bool = False) -> str:
    """The text without internal ids and names. `line=True` is for a line read first: it also drops evidence ids,
    commit hashes and every code name, unless `names` keeps code names (a title can name a file). Links are left
    whole."""
    parts = _URL.split(text)
    links = _URL.findall(text)
    out = [_scrub_prose(part, line, names) for part in parts]
    return _tidy("".join(x for pair in zip(out, [*links, ""]) for x in pair))


def _scrub_prose(text: str, line: bool, names: bool) -> str:
    for pattern in _INTERNAL + (_LINE_ONLY if line else []):
        text = pattern.sub(_GONE, text)
    return _CODE_NAME.sub(_glossary_or_drop if line and not names else _glossary, text)


def fitted(text: str, limit: int) -> int:
    """A limit given in Korean characters, for this text. English says the same in more characters, so a text that is
    not mostly Hangul gets the limit stretched by the factor the output-language rule tells the agents."""
    letters = sum(ch.isalpha() for ch in text)
    return limit if _hangul(text) * 2 >= letters else math.ceil(limit * EN_LENGTH_FACTOR)


def clip(text: str, limit: int) -> str:
    """At most `limit` characters (in Korean; see `fitted`), cut at a sentence end or a space when one is near, and
    marked with an ellipsis."""
    limit = fitted(text, limit)
    if len(text) <= limit:
        return text
    head = text[:limit - 1]
    ends = [m.end() for m in _SENTENCE_END.finditer(head)]
    if ends and ends[-1] >= limit * 0.6:
        return head[:ends[-1]].rstrip()
    space = head.rfind(" ")
    cut = head[:space] if space >= limit * 0.6 else head
    return cut.rstrip(" ,·:;(") + "…"


def one_line(text: str, limit: int, names: bool = False) -> str:
    """A line read before anything else: one line, no markdown, no internal or evidence ids, at most `limit`.
    `names=True` keeps code names, mapped through the glossary when it knows them."""
    text = _MARKDOWN.sub("", text or "")
    return clip(re.sub(r"\s+", " ", scrub(text, line=True, names=names)).strip(), limit)


# What a request is, which its kind tag already says: the prefix makes "<kind>: Bean stock app: …" read as the decision.
_KIND_PREFIX = re.compile(rf"^\s*(?:{_any(words.KIND_PREFIX)})\s*[:：]\s*", re.IGNORECASE)


def title(text: str | None, limit: int = 200) -> str:
    """A record's title wherever the owner reads it (a card, a panel heading, a list, the feed): a line read first,
    which keeps code names, without the kind prefix the server wrote or other records' codes the agent wrote. Stored
    titles are not rewritten; every read model passes them through here."""
    return one_line(_KIND_PREFIX.sub("", text or ""), limit, names=True)


def detail(text: str, limit: int | None = None) -> str:
    """Text read on demand: internal ids dropped, evidence ids and lines kept."""
    text = scrub(text or "")
    return clip(text, limit) if limit else text


def body(text: str) -> str:
    """A request's own text, which an engineer's question or a file path rides in: only brief ids and skill names go."""
    text = text or ""
    for pattern in _NEVER:
        text = "".join(pattern.sub(_GONE, part) if i % 2 == 0 else part
                       for i, part in enumerate(re.split(f"({_URL.pattern})", text)))
    return _tidy(text)


def OneLine(limit: int, names: bool = False):  # noqa: N802 - a type alias with a parameter
    return Annotated[str, AfterValidator(partial(one_line, limit=limit, names=names))]


def Body():  # noqa: N802
    return Annotated[str, AfterValidator(body)]


def Detail(limit: int | None = None):  # noqa: N802
    return Annotated[str, AfterValidator(partial(detail, limit=limit))]
