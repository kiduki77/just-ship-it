"""The words for a span of time in a goal line, per language (`roadmap.DURATION`): "6주", "석 달", "six weeks".

`UNITS` maps a unit to its days. `NUMBER_WORDS` maps a number written as a word to its value, and `WORD_UNITS` names
the units a number word counts: a Korean numeral only counts weeks and months, since "한일" or "네 일" are words.
"""
from justshipit.i18n import Lang

UNITS: dict[Lang, dict[str, int]] = {
    "ko": {"주일": 7, "주": 7, "일": 1, "개월": 30, "달": 30},
    "en": {"weeks": 7, "week": 7, "days": 1, "day": 1, "months": 30, "month": 30},
}
NUMBER_WORDS: dict[Lang, dict[str, int]] = {
    "ko": {"한": 1, "두": 2, "세": 3, "석": 3, "넉": 4, "네": 4, "다섯": 5, "여섯": 6},
    "en": {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "eleven": 11, "twelve": 12, "fourteen": 14, "twenty": 20, "thirty": 30},
}
WORD_UNITS: dict[Lang, tuple[str, ...]] = {
    "ko": ("주일", "주", "개월", "달"),
    "en": tuple(UNITS["en"]),
}
# A day right after a month is a date ("11월 30일"), not a span.
MONTH = "월"
# A Korean number word inside a longer word ("세금", "한국") is not a number.
KO_LETTERS = "가-힣"
