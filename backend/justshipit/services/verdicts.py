"""What an option's words say the owner decides: the one reading the request parser, `respond` and the owner tasks share.

An agent types each option's verdict, and it can type `approve` on "지금은 보내지 않음". Approving that option would
send the email the option says not to send, so the words outrank the agent's type wherever an option declines.
"""
from __future__ import annotations

import re

from justshipit.contracts.requests import HumanDecision
from justshipit.i18n.match import plain
from justshipit.i18n.match.verdicts import WORDS

_LEAD = r"(?=$|[\s(:·,./-])"


def _any(kind: str) -> str:
    """Every language's fragments of one kind as one alternation: an option keeps the language it was written in."""
    return "|".join(f for words in WORDS.values() for f in words[kind])


REJECT_LEAD = re.compile(rf"^(?:{_any('reject_lead')}){_LEAD}", re.IGNORECASE)
_DEFER_LEAD = re.compile(rf"^(?:{_any('defer_lead')}){_LEAD}", re.IGNORECASE)
_DEFER = re.compile(_any("defer"), re.IGNORECASE)
_NEGATIVE = re.compile(f"{_any('negative')}|{_any('defer')}", re.IGNORECASE)
_POSITIVE = re.compile(_any("positive"), re.IGNORECASE)
# An option that says the action does not happen now ("지금은 올리지 않음", "not now"), or that leads with a reject or
# cancel word. A word that often names the thing acted on does not count inside the option.
_NOT_NOW = re.compile(rf"{_any('not_now')}|^\s*(?:{_any('not_now_lead')}){_LEAD}", re.IGNORECASE)


def infer_verdict(label: str, question: bool) -> HumanDecision | None:
    """The decision an option from before options were typed states in its words, or None when they do not say.
    A question's option is an answer, even "no" or "Redis 캐시 안 함", unless it leads with 반려 or 보류. Approval is
    never guessed: only an option that says yes and nothing against it is one. One that says both is a deferral when
    it says to wait ("승인 보류"), and otherwise unsaid ("취소 정책 승인")."""
    head = plain(label.strip())
    if REJECT_LEAD.search(head):
        return HumanDecision.REJECT
    if _DEFER_LEAD.search(head):
        return HumanDecision.DEFER
    if question:
        return HumanDecision.ANSWER
    yes, no = _POSITIVE.search(head), _NEGATIVE.search(head)
    if no and _DEFER.search(head):
        return HumanDecision.DEFER
    if yes and no:
        return None
    if no:
        return HumanDecision.REJECT
    return HumanDecision.APPROVE if yes else None


def declines(label: str | None) -> bool:
    """Whether the option says the action does not happen. Only the words that say so outright count, not every word
    `infer_verdict` reads against an option (제외, 중단, no): an option that acts on such a thing would turn into a 반려."""
    return bool(label) and bool(_NOT_NOW.search(plain(label)))
