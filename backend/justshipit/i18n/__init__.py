"""Owner-facing text in the owner's language: `msg(key, lang, **vars)` over per-area catalogs.

Every sentence the server writes for the owner lives in a catalog under `justshipit/i18n/messages/`, one module per area,
each entry holding every language side by side so a key is never added in one language only. The owner picks the
language in Settings (`app_settings.language()`); a caller reads it once at its boundary and passes `lang` down.

A template names its values in braces: "{title}". A Korean particle after a value is written `{title|을}`, and the
right form for the value's last sound is chosen (을/를, 이/가, 은/는, 과/와, 으로/로, 이에요/예요 ...). An English
message that counts something is a plural pair, `{"one": "...", "other": "..."}`, chosen by the value `n`.

Text already stored (a request title, an agent's summary) stays in the language it was written in. Only the words the
server puts around it follow the setting.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Mapping
from functools import cache
from typing import Literal, get_args

Lang = Literal["en", "ko"]
LANGS: tuple[Lang, ...] = get_args(Lang)
DEFAULT: Lang = "en"
NAMES: dict[Lang, str] = {"en": "English", "ko": "Korean"}

Message = str | Mapping[str, str]
Entry = Mapping[Lang, Message]

PLACEHOLDER = re.compile(r"\{(\w+)(?:\|([^{}]+))?\}")

# (with a final consonant, without one). 으로/로 also takes 로 after ㄹ.
_PARTICLES: tuple[tuple[str, str], ...] = (
    ("을", "를"), ("이", "가"), ("은", "는"), ("과", "와"), ("으로", "로"), ("이에요", "예요"), ("이었어요", "였어요"),
    ("아", "야"), ("이나", "나"), ("이랑", "랑"), ("이라고", "라고"), ("이라", "라"),
)
_PAIR = {form: pair for pair in _PARTICLES for form in pair}
# The final consonant of each digit read in Korean (영 일 이 삼 사 오 육 칠 팔 구): 0 none, 8 ㄹ, other a consonant.
_DIGIT_FINAL = {"0": 21, "1": 8, "2": 0, "3": 16, "4": 0, "5": 0, "6": 1, "7": 8, "8": 8, "9": 0}
_RIEUL = 8


def _final(word: str) -> int | None:
    """The final consonant index of the word's last sound (0 for none), or None when it cannot be told."""
    for ch in reversed(word):
        if "가" <= ch <= "힣":
            return (ord(ch) - 0xAC00) % 28
        if ch in _DIGIT_FINAL:
            return _DIGIT_FINAL[ch]
        if ch.isalpha():
            return None
    return None


def particle(word: str, form: str) -> str:
    """The particle `form` (either spelling of a pair, as in "을" or "를") that fits after `word`. When the word's
    sound cannot be told (it ends in a Latin letter), both are written: "을(를)"."""
    pair = _PAIR.get(form)
    if pair is None:
        return form
    final = _final(word)
    if final is None:
        return f"{pair[0]}({pair[1]})"
    if pair[0] == "으로":
        return pair[1] if final in (0, _RIEUL) else pair[0]
    return pair[0] if final else pair[1]


def placeholders(message: Message) -> set[str]:
    texts = message.values() if isinstance(message, Mapping) else (message,)
    return {m.group(1) for text in texts for m in PLACEHOLDER.finditer(text)}


def format_message(message: Message, vars: Mapping[str, object]) -> str:
    if isinstance(message, Mapping):
        message = message["one" if vars.get("n") == 1 else "other"]

    def fill(m: re.Match) -> str:
        value = str(vars[m.group(1)])
        return value + particle(value, m.group(2)) if m.group(2) else value

    return PLACEHOLDER.sub(fill, message)


@cache
def catalog() -> dict[str, Entry]:
    """Every area's messages, keyed `<area>.<name>`. An area module is any module in `justshipit.i18n.messages`; a key
    outside its area's prefix or a language left out fails here, at first use, rather than on the owner's screen."""
    from justshipit.i18n import messages

    merged: dict[str, Entry] = {}
    for info in pkgutil.iter_modules(messages.__path__):
        area = importlib.import_module(f"{messages.__name__}.{info.name}")
        for key, entry in area.MESSAGES.items():
            if not key.startswith(f"{info.name}."):
                raise ValueError(f"{key}: a key in messages/{info.name}.py starts with '{info.name}.'")
            if missing := [lang for lang in LANGS if lang not in entry]:
                raise ValueError(f"{key}: no {', '.join(missing)} text")
            merged[key] = entry
    return merged


def msg(key: str, lang: Lang, /, **vars: object) -> str:
    """The catalog's text for `key` in `lang`, with `vars` filled in. An unknown key or a missing value raises."""
    return format_message(catalog()[key][lang], vars)


def lang_of(value: object) -> Lang:
    """A stored or requested language code as a `Lang`, the default for anything else."""
    return value if value in LANGS else DEFAULT  # type: ignore[return-value]
