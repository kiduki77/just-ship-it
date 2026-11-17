"""The words that mark a step past an idea-panel warning as one the owner cannot take alone, per language
(`contracts/idea_review.py`): hiring, raising money, partners, a team, an agency or a contractor.

Each value is a tuple of regex fragments, `|`-joined across every language into one pattern. A Korean fragment has no
edges, since a particle sits right against the word. An English fragment is a whole word or phrase (`_en`), so "hire"
is not inside "hireling" and "agency" is not inside "urgency". Investing one's own time ("투자해 세 분을 만나", "invest
five hours") is the owner's alone and does not match.
"""
from justshipit.i18n import Lang


def _en(*fragments: str) -> tuple[str, ...]:
    """Each fragment as a whole word: a letter or a hyphen beside it makes it part of another word."""
    return tuple(rf"(?<![a-z-])(?:{f})(?![a-z-])" for f in fragments)


WORDS: dict[Lang, tuple[str, ...]] = {
    "ko": ("채용", "고용", r"(?:직원|사람|인력|팀원|개발자|디자이너)[을를] (?:뽑|구하|채용|고용|두)",
           r"팀을 (?:꾸|만들|구성|짜)", r"공동\s?창업", "투자자", "투자를 받", "투자받", "투자 유치", "펀딩",
           "파트너사", "파트너십", "파트너를", "제휴", "대행사", "에이전시", "외주", "컨설팅 업체"),
    "en": _en(r"hir(?:e|es|ed|ing)", r"recruit\w*", r"employ(?:s|ed|ing)?\s+(?:a|an|some|staff|people|someone)",
              r"staff\s+up", r"investors?", r"investment\s+(?:round|from)", r"fundrais\w*",
              r"rais(?:e|es|ed|ing)\s+(?:money|funds|funding|capital|a\s+(?:seed\s+)?round)", r"seed\s+round",
              r"venture\s+capital\w*", r"vcs?", r"co-?founders?", r"partner(?:s|ed|ing|ships?)?",
              r"team\s+up", r"(?:build|assemble|form|put\s+together|get)\s+(?:a|your|the)\s+team",
              r"agenc(?:y|ies)", r"outsourc\w*", r"freelancers?", r"contractors?", r"consultan(?:cy|cies|ts?)"),
}
