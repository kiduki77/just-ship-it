"""The owner's time: how many minutes a day the owner said they can give, and the days the owner's list fills with it.

The plan's `owner_time` line is the owner's own words ("가게 끝나고 하루 30분 정도 시간을 쓸 수 있어요"). A week's time
counts as a seventh a day. Words that name no amount ("주말에만 돼요") give no limit, and the list stays one day.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from justshipit.i18n import LANGS, Lang
from justshipit.i18n.match import owner_time as words
from justshipit.services import roadmap

SECTION = "owner_time"


def _amount(text: str, lang: Lang) -> float | None:
    """Minutes in an amount such as "1시간 30분", "한 시간 반", "an hour and a half", "45 minutes"."""
    n = words.NUMBER_WORDS[lang]
    minutes = 0.0
    if h := re.search(words.HOURS[lang], text, re.IGNORECASE):
        minutes += 60 * (float(n[h[1].lower()]) if h[1].lower() in n else float(h[1]))
    if m := re.search(words.MINUTES[lang], text, re.IGNORECASE):
        minutes += float(m[1])
    if re.search(words.HALF[lang], text, re.IGNORECASE):
        minutes += 30
    return minutes or None


def minutes_per_day(text: str) -> int | None:
    """The minutes a day the owner's words give, or None when they name no amount for a day or a week."""
    for table, days in ((words.PER_DAY, 1), (words.PER_WEEK, 7)):
        for lang in LANGS:
            for pattern in table[lang]:
                if (found := re.search(pattern, text, re.IGNORECASE)) and (amount := _amount(found["amount"], lang)):
                    return round(amount / days)
    return None


def of_business(s: Session, business_id: int) -> int | None:
    """The minutes a day the approved plan says the owner has, from the first `owner_time` line that names an amount."""
    brief = roadmap.approved_brief(s, business_id)
    lines = roadmap.lines_by_key(brief.sections).get(SECTION, []) if brief else []
    return next((m for x in lines if x["provenance"] != "blank" and (m := minutes_per_day(x["text"]))), None)


def plan_days(minutes: list[int], due_now: list[bool], per_day: int | None) -> list[int]:
    """The day each item of the owner's list falls on (0 is today), in the list's order, so no day holds more than
    `per_day` minutes. What stops the company or is due by today stays today whatever it takes; an item longer than a
    day takes a day of its own. No limit keeps everything today."""
    if not per_day:
        return [0] * len(minutes)
    out, day, used = [], 0, 0
    for need, now in zip(minutes, due_now, strict=True):
        if now:
            out.append(0)
            used += need if day == 0 else 0
            continue
        if used and used + need > per_day:
            day, used = day + 1, 0
        out.append(day)
        used += need
    return out
