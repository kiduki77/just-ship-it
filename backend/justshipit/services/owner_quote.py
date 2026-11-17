"""Whether a quote of the owner's words can back a brief line. A quote is only evidence for a line it talks about:
it must be long enough, carry at least two content words, share a content word with the line, and, for a gate
section, every number the line states must be in the quote or right beside it in the owner's message. Matching reads text in NFC with zero-width and other format
characters removed, so an invisible character cannot make a quote differ or match.

A line follows the owner's language setting and a quote stays as the owner wrote it, so the two can be in different
scripts and share no word. Then the CEO's gloss, the quote rendered in the line's language, stands in for the quote's
words. The numbers still come from the owner's own words: a gloss cannot add one.

A line that states a reason needs the owner to have given it: each content word of the reason must be among the
owner's words around the quote (or in the gloss). The joining word may differ, since the owner writes "so" or "-라서"
where the line says "because" or "때문에". Otherwise the reason is the CEO's reading, and the line stays a guess."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from justshipit.i18n import LANGS
from justshipit.i18n.match.owner_quote import REASON_FOLLOWS, REASONS
from justshipit.i18n.match.owner_quote import STOPWORDS as _STOPWORDS
from justshipit.i18n.said import Said
from justshipit.i18n.said import said as _said  # `problem` names the owner's message `said`

MIN_CHARS = 6
MIN_TOKENS = 2
NUMBER_REACH = 120
_TOKEN = re.compile(r"[^\W_]{2,}")
_NUMBER = re.compile(r"\d(?:,?\d)*")
_HANGUL = re.compile(r"[\uac00-\ud7a3\u3131-\u318e]")
_LATIN = re.compile(r"[a-z]")
_CLAUSE_END = re.compile(r"[,.;:!?·]")
STOPWORDS = frozenset(w for lang in LANGS for w in _STOPWORDS[lang].split())
_REASON = re.compile("|".join([*map(re.escape, REASONS["ko"]),
                               *(rf"(?<![a-z]){re.escape(w)}(?![a-z])" for w in REASONS["en"])]), re.IGNORECASE)


def norm(text: str) -> str:
    """NFC, no format or control characters, whitespace squeezed to single spaces."""
    text = "".join(c for c in unicodedata.normalize("NFC", text)
                   if unicodedata.category(c) != "Cf" and (c.isspace() or unicodedata.category(c) != "Cc"))
    return " ".join(text.split())


def length(normalized: str) -> int:
    """Code points of NFC text, spaces left out."""
    return len("".join(normalized.split()))


def tokens(normalized: str) -> set[str]:
    """Content words: Hangul or alphanumeric runs of two or more characters that are not stop words."""
    return {t for t in (x.lower() for x in _TOKEN.findall(normalized)) if t not in STOPWORDS}


def numbers(normalized: str) -> set[int]:
    return {int(n.replace(",", "")) for n in _NUMBER.findall(normalized)}


def _related(a: str, b: str) -> bool:
    """Korean particles and endings stick to the word (시간 and 시간이, 문서화만 and 문서화하는), so two words match
    when one contains the other or they agree on all but the last character of the shorter (two at least)."""
    if a in b or b in a:
        return True
    shared = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return shared >= 2 and shared >= min(len(a), len(b)) - 1


def _shares_a_word(quote: set[str], line: set[str]) -> bool:
    return any(_related(a, b) for a in quote for b in line)


def script(words: set[str]) -> str | None:
    """The script most of the content words are written in, "hangul" or "latin". None when neither has more."""
    counts = Counter("hangul" if _HANGUL.search(w) else "latin" for w in words if _HANGUL.search(w) or _LATIN.search(w))
    return "hangul" if counts["hangul"] > counts["latin"] else "latin" if counts["latin"] > counts["hangul"] else None


def _reasons(line: str) -> list[set[str]]:
    """The content words of each reason `line` (normalized) states: the clause on the side of its marker the reason
    sits (`REASON_FOLLOWS`), or the other side when that one has none ("X is the reason")."""
    def words(clause: str) -> set[str]:
        return {w for w in tokens(clause) if not _REASON.search(w)}
    out = []
    for m in _REASON.finditer(line):
        before = words(_CLAUSE_END.split(line[:m.start()])[-1])
        after = words(_CLAUSE_END.split(line[m.end():])[0])
        out.append((after or before) if m.group(0).lower() in REASON_FOLLOWS else (before or after))
    return out


def around(quote: str, said: str) -> str:
    """The owner's words within `NUMBER_REACH` characters of `quote` in `said` (both normalized), quote included."""
    at = said.find(quote)
    if at < 0:
        return quote
    return said[max(0, at - NUMBER_REACH):at + len(quote) + NUMBER_REACH]


def problem(quote: str, line_text: str, *, gate: bool, said: str, gloss: str = "") -> Said | None:
    """Why `quote` (already normalized) cannot back `line_text`, or None when it can. `said` is the owner's message
    the quote was found in. A gate line's numbers may come from the quote's own sentence and its neighbours there
    (`around`): the owner writes "도착→1차 답장까지 걸린 시간. 목표는 중앙값 2시간 이내" and a quote of the target
    alone still backs a line that keeps "1차". `gloss` counts only when the quote and the line are in different
    scripts."""
    if length(quote) < MIN_CHARS:
        return _said("evidence.quote.too_short", min=str(MIN_CHARS))
    q = tokens(quote)
    if len(q) < MIN_TOKENS:
        return _said("evidence.quote.few_words", min=str(MIN_TOKENS))
    line = norm(line_text)
    words = tokens(line)
    glossed = bool(gloss) and script(q) != script(words) and _shares_a_word(tokens(norm(gloss)), words)
    if not (_shares_a_word(q, words) or glossed):
        return _said("evidence.quote.off_line")
    owners = tokens(around(quote, said)) | (tokens(norm(gloss)) if glossed else set())
    if any(not any(_related(w, o) for o in owners) for reason in _reasons(line) for w in reason):
        return _said("evidence.quote.adds_reason")
    if gate and (missing := numbers(line) - numbers(around(quote, said))):
        return _said("evidence.quote.numbers", numbers=", ".join(str(n) for n in sorted(missing)))
    return None
