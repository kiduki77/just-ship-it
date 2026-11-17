"""Words a spec writer may give a screen's state instead of the five the card draws (`wireflow.state_word`):
"save-error", "불러오는 중". Each maps to the state it says; a state that says none of them is the screen in normal use.
"""
from justshipit.i18n import Lang

SAYS: dict[Lang, dict[str, tuple[str, ...]]] = {
    "en": {"error": ("error", "fail", "invalid"), "empty": ("empty",), "loading": ("loading",),
           "warning": ("warning", "warn"), "success": ("success", "done")},
    "ko": {"error": ("오류", "에러", "실패"), "empty": ("빈 ", "비어"), "loading": ("로딩", "불러오", "읽는"),
           "warning": ("경고", "주의"), "success": ("완료", "성공")},
}
