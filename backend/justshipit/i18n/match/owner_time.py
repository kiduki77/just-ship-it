"""The words for the owner's time in the plan's `owner_time` line, per language (`services/owner_time.py`): "하루 30분",
"주 5시간", "an hour a day", "5 hours a week".

`PER_DAY` and `PER_WEEK` find the amount the owner gives for a day or a week, as the named group `amount`. `HOURS`
and `MINUTES` read that amount; `HALF` adds half an hour ("한 시간 반", "an hour and a half"). `NUMBER_WORDS` maps a
number written as a word to its value.
"""
from justshipit.i18n import Lang

_KO_AMOUNT = r"(?P<amount>(?:\d+(?:\.\d+)?|한|두|세|네)\s*시간(?:\s*반)?(?:\s*\d+\s*분)?|\d+\s*분)"
_EN_AMOUNT = (r"(?P<amount>(?:\d+(?:\.\d+)?|an?|one|two|three|four|five|six|half\s+an?)\s*"
              r"(?:hours?|hrs?|minutes?|mins?)(?:\s+and\s+a\s+half)?)")

PER_DAY: dict[Lang, tuple[str, ...]] = {
    "ko": (r"(?:하루|매일|날마다|평일)\s*(?:에|에는|마다)?\s*(?:[가-힣]{1,4}\s+)?" + _KO_AMOUNT,),
    "en": (_EN_AMOUNT + r"\s*(?:a|per|each|every|/)\s*(?:day|evening|night|weekday)",
           r"(?:daily|every\s+day|each\s+day)[^.\d]{0,20}" + _EN_AMOUNT),
}
PER_WEEK: dict[Lang, tuple[str, ...]] = {
    "ko": (r"(?:일주일|한\s*주|매주|(?<![가-힣])주)\s*(?:에|에는|당|마다)?\s*" + _KO_AMOUNT,),
    "en": (_EN_AMOUNT + r"\s*(?:a|per|each|every|/)\s*week",),
}
HOURS: dict[Lang, str] = {"ko": r"(\d+(?:\.\d+)?|한|두|세|네)\s*시간", "en": r"(\d+(?:\.\d+)?|an?|one|two|three|four|five|six)\s*(?:hours?|hrs?)"}
MINUTES: dict[Lang, str] = {"ko": r"(\d+)\s*분", "en": r"(\d+)\s*(?:minutes?|mins?)"}
HALF: dict[Lang, str] = {"ko": r"시간\s*반", "en": r"half\s+an?\s+hour|and\s+a\s+half"}
NUMBER_WORDS: dict[Lang, dict[str, float]] = {
    "ko": {"한": 1, "두": 2, "세": 3, "네": 4},
    "en": {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6},
}
