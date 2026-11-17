"""Words that make text from a web page read as an instruction to whoever reads it, per language
(`services/people_tasks.hostile_problem`). Each value is a tuple of regex fragments, `|`-joined across every language,
because a page keeps the language it was written in. An English fragment carries its own edges.
"""
from justshipit.i18n import Lang

WORDS: dict[Lang, tuple[str, ...]] = {
    "en": (r"(?<![a-z])ignore[^.]{0,30}(?:previous|prior|above|instructions?|rules?)(?![a-z])",
           r"(?<![a-z])(?:disregard|override)(?![a-z])", r"system\s*prompt", r"you\s+are\s+now",
           r"(?<![a-z])(?:api[\s_-]?keys?|secrets?|tokens?|passwords?|credentials?)(?![a-z])"),
    "ko": (r"이전[^.]{0,10}(?:지시|명령|지침)", "무시하", r"시스템\s*프롬프트", "비밀번호", "토큰", "시크릿", r"인증\s*키",
           r"api\s*키"),
}
