"""The answer chips the kickoff screen adds by itself, per language (`services/kickoff.py`). A chip the CEO writes
that says the same is dropped, so the owner never sees it twice. And the words of the tag an older prompt had the CEO
append to a plan line after a middle dot ("· a guess", "· your own view", "· 직접 본 일"), which the server drops."""
from justshipit.i18n import Lang

SCREEN_CHIPS: dict[Lang, tuple[str, ...]] = {
    "ko": ("모르겠어요", "잘 모르겠어요", "나중에"),
    "en": ("I don't know", "Don't know", "I'm not sure", "Not sure", "Later", "Maybe later"),
}

# The whole tag after the last middle dot, as the CEO wrote it on live plans. A tail that only contains one of these
# words is the line's own content ("· stop if none have seen value", "· 사장님들은 매주 다시 셈") and stays.
LINE_TAGS: dict[Lang, tuple[str, ...]] = {
    "ko": (r"(?:짐작|추측)(?: \(.*\))?", r"직접 \S+ (?:일|사람)", r"들은 (?:일|말|이야기)", r"계획서", r"바라는 목표",
           r"오너님 생각"),
    "en": (r"a guess(?: from .+)?", r"(?:seen |heard )?first-?hand", r"heard", r"from (?:you|your notes)",
           r"your (?:own )?(?:rule|goal|schedule|view|target|estimate|plan)", r"a general sense(?:,.*)?"),
}
