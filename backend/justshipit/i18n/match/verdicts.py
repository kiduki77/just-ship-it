"""The words in an option's label that say what the owner decides, per language (`services/verdicts.py`).

Each value is a tuple of regex fragments, `|`-joined across every language into one pattern. A Korean particle sits
right against the word it follows, so a Korean fragment has no edges. An English fragment is a whole word or phrase
(`_en`): "hold" is not inside "stakeholder", "approve" not inside "disapprove". Labels reach these with their
apostrophes made plain ("Don’t" is "Don't").
"""
from justshipit.i18n import Lang


def _en(*fragments: str) -> tuple[str, ...]:
    """Each fragment as a whole word: a letter or a hyphen beside it makes it part of another word ("stop-loss")."""
    return tuple(rf"(?<![a-z-])(?:{f})(?![a-z-])" for f in fragments)


# A verb an option says not to do ("not sending", "don't merge"), with its common endings.
_EN_ACT = (r"(?:post|send|publish|start|buy|pay|contact|apply|register|ask|merge|deploy|launch|ship|release|email"
           r"|proceed|run|sign|spend|build|do|go)(?:s|ed|ing|ping|ning)?|(?:us|mak|writ)(?:e|es|ing)")
# Said at the head of an option, these say to stop or wait. Inside one they often name the thing acted on ("the
# hold music", "never miss a deal"), so `not_now` counts them only there. A filler may come first ("Let's wait").
_EN_WAIT_LEAD = (r"(?:(?:let'?s|please|just|ok(?:ay)?|no)[\s,]+)?"
                 r"(?:stop|hold|pause|wait|delay|never|leave\s+it|refuse|reject|decline|cancel)")

WORDS: dict[Lang, dict[str, tuple[str, ...]]] = {
    "ko": {
        # a label that leads with one of these is that decision, whatever follows
        "reject_lead": ("반려", "거부", "거절"),
        "defer_lead": ("보류", "연기", "나중에"),
        # a word that asks to wait: with a no, the option is a deferral
        "defer": ("보류", "연기", "나중", "대기"),
        "negative": (r"하지\s*않", "않음", "않는", "않기", "않아요", r"안\s*(?:함|하|해|됨)", "말고", "마세요", "금지",
                     "불가", "불허", "미승인", "거부", "거절", "반려", "철회", "중단", "중지", "취소", "제외", "재검토"),
        "positive": ("승인", "진행", "채택", "허용", "동의", "시작", "신청", "적용", "확정", "머지", "활성화", "올림",
                     "올리기", "올려", "돌리기", "만들기"),
        # an option that says the action does not happen now. A word that often names the thing acted on (제외, 중단,
        # 취소 inside "광고 중단 공지 메일 발송") does not count unless it leads.
        "not_now": ((r"(?:하|올리|게시하|보내|진행하|시작하|쓰|사|결제하|만들|연락하|신청하|등록하|맡기|공개하|묻|물어보)"
                     r"지\s*(?:않|말)"),
                    "않음", r"안\s*(?:함|올림|보냄|씀|삼|묻)", "보류", "연기", "나중에", "생략", "건너뛰", "미루", "그만"),
        "not_now_lead": ("반려", "거부", "거절", "취소"),
    },
    "en": {
        "reject_lead": _en("reject(?:ed)?", "declin(?:e|ed)", "refuse(?:d)?", "disapprov(?:e|ed)"),
        "defer_lead": _en("defer(?:red)?", "later", r"hold(?:\s+off)?", r"on\s+hold", "postpon(?:e|ed)",
                          "paus(?:e|ed)", "wait", "delay(?:ed)?", r"not\s+(?:now|yet)"),
        "defer": _en("later", "hold", "defer(?:s|red|ring|ral)?", "postpon(?:e|es|ed|ing)", "paus(?:e|es|ed|ing)",
                     "wait(?:s|ed|ing)?", "delay(?:s|ed|ing)?", r"not\s+(?:now|yet)"),
        "negative": _en("no", "not", r"do\s+not", "don'?t", "won'?t", "never", "cancel(?:s|led|ed|ling|ing)?",
                        "stop(?:s|ped|ping)?", "skip(?:s|ped|ping)?", "reject(?:s|ed|ing)?", "declin(?:e|es|ed|ing)",
                        "refus(?:e|es|ed|ing)", "disapprov(?:e|es|ed|ing|al)", "unacceptable"),
        "positive": _en("approv(?:e|es|ed|ing|al)", "proceed(?:s|ed|ing)?", "accept(?:s|ed|ing)?", "yes",
                        r"go\s+ahead"),
        "not_now": _en(r"not\s+(?:now|yet|today|this\s+(?:time|week|month))", r"do\s+not", "don'?t", "won'?t",
                       rf"(?:(?:will|would|should)\s+not|wouldn'?t|shouldn'?t)\s+(?:{_EN_ACT})",
                       rf"not\s+(?:{_EN_ACT})", "skip(?:s|ped|ping)?", r"hold\s+off", r"on\s+hold", "later",
                       "defer(?:s|red|ring)?", "postpon(?:e|es|ed|ing)", r"put\s+(?:it\s+|this\s+|that\s+)?off",
                       r"keep(?:\s+\w+){0,3}?\s+(?:as\s+)?(?:a\s+)?drafts?",
                       r"leave\s+it\s+(?:as\s+is|for\s+now|alone)"),
        "not_now_lead": _en(_EN_WAIT_LEAD),
    },
}

# The console's words for a decision (`decide.word.*` in web/src/i18n/areas/decide.ts). An option or an answer that is
# one of them alone says nothing the decision buttons don't. tests/test_i18n_merge_review.py keeps the two in step.
BARE: dict[Lang, tuple[str, ...]] = {
    "ko": ("승인", "보류", "연기", "반려", "거절", "답변"),
    "en": ("approve", "hold", "defer", "decline", "reject", "answer"),
}
