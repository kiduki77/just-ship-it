"""Words an agent writes on a decision card that the server reads, per language (`contracts/requests.py`).

`UNKNOWN` is a metric value that says the number is not known, so the card leaves the metric out. `BADGE` is a word
an agent tacks onto an option's label in brackets ("올려요 (추천)", "Post it (recommended)"); the card already marks
the recommended option, so the word is cut from the label.
"""
from justshipit.i18n import Lang

UNKNOWN: dict[Lang, tuple[str, ...]] = {
    "ko": ("모름", "알 수 없음", "미정"),
    "en": ("unknown", "n/a", "tbd", "?", "-", ""),
}
BADGE: dict[Lang, tuple[str, ...]] = {
    "ko": ("추천", "권장", "CEO 추천", "프로젝트 매니저 추천"),
    "en": ("recommended", "suggested", "ceo's pick", "ceo pick", "project manager's pick", "project manager pick"),
}
