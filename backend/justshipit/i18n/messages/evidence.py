"""What the evidence ledger says to the owner (`services/evidence.py`): how far a claim was checked, the ledger counted
by that, and why a quote of the owner's words cannot back a plan line (`services/owner_quote.py`)."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "evidence.grade.firm": {"en": "Checked", "ko": "확인된 근거"},
    "evidence.grade.sourced": {"en": "Source only", "ko": "출처만 있는 근거"},
    "evidence.grade.assumed": {"en": "Assumed or inferred", "ko": "가정·추론"},
    "evidence.mix.empty": {"en": "The evidence list is empty.", "ko": "근거 목록이 비어 있어요."},
    "evidence.mix.line": {"en": {"one": "{n} piece of evidence: {parts}.", "other": "{n} pieces of evidence: {parts}."},
                          "ko": "근거 {n}건 중 {parts}."},
    "evidence.mix.part": {"en": "{grade} {count} ({share}%)", "ko": "{grade} {count}건({share}%)"},
    "evidence.none": {"en": "(no evidence)", "ko": "(근거 없음)"},
    "evidence.quote.too_short": {"en": "The quote is too short (at least {min} characters, not counting spaces)",
                                 "ko": "인용이 너무 짧아요(띄어쓰기를 빼고 {min}자 이상)"},
    "evidence.quote.few_words": {"en": "The quote has fewer than {min} words that carry meaning (words like 'and' or "
                                       "'really' don't count)",
                                 "ko": "인용에 뜻이 있는 낱말이 {min}개가 안 돼요(그리고, 정말 같은 말은 세지 않아요)"},
    "evidence.quote.off_line": {"en": "The quote shares no word with this line, so it doesn't back it",
                                "ko": "인용에 이 줄과 겹치는 낱말이 없어 이 줄의 근거가 아니에요"},
    "evidence.quote.adds_reason": {"en": "The line gives a reason the quote doesn't, so the reason is the Project Manager's reading",
                                   "ko": "인용에 없는 이유를 이 줄이 말하고 있어서, 그 이유는 프로젝트 매니저의 짐작이에요"},
    "evidence.quote.numbers": {"en": "The line's numbers {numbers} aren't in the quote or the owner's words around it",
                               "ko": "이 줄의 숫자 {numbers|이} 인용과 그 앞뒤 오너님 말에 없어요"},
}
