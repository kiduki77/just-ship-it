"""Words that matching reads in every language, one module per rule. Matching always reads all languages at once:
stored text stays in the language it was written in, so a rule that read only the current one would miss it."""

_APOSTROPHES = str.maketrans(dict.fromkeys("\u2018\u2019\u02bc", "'"))


def plain(text: str) -> str:
    """The text with each curly or modifier apostrophe made plain, as the English words here are written: "Don’t"
    comes with any of them."""
    return text.translate(_APOSTROPHES)
