"""The spelled-out pieces of a contact detail, per language (`services/people.contact_info_problem`).

Each value is a tuple of regex fragments, `|`-joined across every language, because a quote or a note keeps the
language it was written in. `at` and `dot` stand for `@` and `.` in an address ("john [at] gmail [dot] com", "john
골뱅이 gmail 점 com"); `dot_word` is a dot spelled as a bare word after `at`; `messenger` names an app whose id is a
contact; `id` is the word for that id; `spoken_phone` is a phone number read aloud digit by digit.
"""
from justshipit.i18n import Lang

WORDS: dict[Lang, dict[str, tuple[str, ...]]] = {
    "en": {
        "at": (r"\[at\]", r"\(at\)", r"\{at\}"),
        "dot": (r"\[dot\]", r"\(dot\)", r"\{dot\}"),
        "dot_word": (r"\bdot\b",),
        "messenger": (r"kakao(?:talk)?", "telegram", "wechat", "whatsapp"),
        "id": ("id",),
        "spoken_phone": (),
    },
    "ko": {
        "at": ("골뱅이", "앳"),
        "dot": (r"\s점\s",),
        "dot_word": ("점",),
        "messenger": ("카톡", "카카오톡?", "텔레그램", "위챗", "왓츠앱"),
        "id": ("아이디",),
        # 공일공, 영일영: a Korean mobile number starts 010 read aloud.
        "spoken_phone": (r"[공영]\s*일\s*[공영일육칠팔구0-9][\s\-공영일이삼사오육륙칠팔구]{6,}",),
    },
}
