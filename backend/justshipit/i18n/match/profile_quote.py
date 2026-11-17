"""Words for the customer description's quote rule (`services/profile/infer.py`), per language.

`NEGATIONS` mark a quote that denies what it names ("not for developers", "개발자용은 아니에요"), so it backs no value.
English entries are whole words; Korean ones match inside a word, since endings sit against it. `GENERIC` are words a
code label shares with almost any sentence about customers, so sharing one is no sign the quote names that value.
"""
from justshipit.i18n import Lang

NEGATIONS: dict[Lang, tuple[str, ...]] = {
    "en": ("not", "no", "never", "without", "isn't", "aren't", "don't", "doesn't", "won't", "nobody", "none"),
    "ko": ("아니", "않", "없", "말고"),
}
GENERIC: frozenset[str] = frozenset({
    "people", "customers", "customer", "buy", "buying", "they", "them", "their", "themselves", "you", "your",
    "사람", "고객",
})
