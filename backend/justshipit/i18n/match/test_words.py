"""The words that make a journey step read like a test, per language (`engineering/specs.journeys_problem`).

A journey is a person using the product, so a step that repeats itself to compare two runs, or checks what the product
leaves alone, belongs in a story's scenarios, which the build's script runs. Each value is a tuple of regex fragments,
`|`-joined across every language into one pattern. A Korean fragment has no edges, since a particle sits right against
the word. An English fragment is a whole word (`_en`): "again" is not inside "against".
"""
from justshipit.i18n import Lang


def _en(*fragments: str) -> tuple[str, ...]:
    """Each fragment as a whole word: a letter or a hyphen beside it makes it part of another word."""
    return tuple(rf"(?<![a-z-])(?:{f})(?![a-z-])" for f in fragments)


WORDS: dict[Lang, tuple[str, ...]] = {
    "ko": (r"두\s*번", r"다시\s*돌려", "똑같", "덮어쓰", r"그대로인지"),
    "en": _en("twice", "again", r"compar(?:e|es|ed|ing)", r"same\s+results?", r"overwrit(?:e|es|ten|ing)",
              "unchanged", r"regressions?"),
}
