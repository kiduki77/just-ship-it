"""A business's market: the language its customers speak.

The market is the language the owner wrote the business's name and one-liner in, not the console's language: a
Korean owner may read the console in English and still run a shop in 망원동. Any Hangul there makes it Korean. When
those have no letters, the brief's text decides, and English is the default. It picks the channels a weekly promotion
may use (`channels.for_market`) and the places the people finder searches (`people.where`).

Who a business serves is not decided here: that is the customer description (`services/profile`).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from justshipit.db.models import Business
from justshipit.i18n import DEFAULT, Lang

SECTIONS = ("one_liner", "customer_problem", "alternative_value", "path", "scope")


def market_of_text(*texts: str) -> Lang:
    """The language of the first text that has letters: Korean when it holds any Hangul. A Korean shop often has a
    Latin brand name ("Mango Coffee 망원점"); an English business seldom writes a Korean word."""
    for text in texts:
        if any(0xAC00 <= ord(ch) <= 0xD7A3 for ch in text):
            return "ko"
        if any(ch.isalpha() for ch in text):
            return "en"
    return DEFAULT


def _brief_text(s: Session, business: Business) -> str:
    # Imported here because roadmap reaches this module through control, requests and people.
    from justshipit.services import roadmap

    brief = roadmap.approved_brief(s, business.id)
    by_key = roadmap.lines_by_key(brief.sections) if brief else {}
    return "\n".join(x["text"] for key in SECTIONS for x in by_key.get(key, []) if x["provenance"] != "blank")


def market_of_business(s: Session, business_id: int) -> Lang:
    business = s.get(Business, business_id)
    if business is None:
        return DEFAULT
    return market_of_text(f"{business.name} {business.one_liner or ''}", _brief_text(s, business))
