"""The Skill Factory (`capability/factory.py`, `api/capability.py`): the decisions it raises and why it won't start."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "factory.propose.title": {"en": "Make a skill candidate: {name}", "ko": "스킬 후보 만들기: {name}"},
    "factory.activate.title": {"en": "Turn on skill: {name} v{version} ({kind})", "ko": "스킬 켜기: {name} v{version} ({kind})"},
    "factory.activate.summary": {
        "en": "For: {targets}\nProblem: {problem}\n\nA/B rerun on {n} tasks: passed {pass_before}→{pass_after}, average unmet criteria {unmet_before}→{unmet_after}, share of supported evidence {supported_before}→{supported_after}\nFiles: registry/{path}/",
        "ko": "대상: {targets}\n문제: {problem}\n\n업무 {n}건 A/B 재실행: 통과 {pass_before}→{pass_after}, 평균 미충족 기준 {unmet_before}→{unmet_after}, 뒷받침된 근거 비율 {supported_before}→{supported_after}\n파일: registry/{path}/"},
    "factory.activate.yes": {"en": "Approve and turn on", "ko": "승인하고 켜기"},
    "factory.activate.no": {"en": "Decline", "ko": "반려"},
    "factory.already_queued": {"en": "A Skill Factory run is already in the queue.", "ko": "스킬 만들기 작업이 이미 대기열에 있어요."},
    "factory.no_skill": {"en": "Couldn't find skill #{id}.", "ko": "스킬 #{id|을} 찾지 못했어요."},
    "factory.not_on": {"en": "{name} v{version} isn't on, so there is nothing to turn off.",
                       "ko": "{name} v{version|은} 켜져 있지 않아서 끌 게 없어요."},
}
