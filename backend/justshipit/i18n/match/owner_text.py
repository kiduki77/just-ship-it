"""The words the owner-text scrub reads around a code it drops, per language (`contracts/owner_text.py`).

Korean sticks a particle to a code ("OT-14에", "REQ-5를"), so `PARTICLES` go with the code. `CITE` is the word that
heads a bracket of codes ("(지시서 l-972536)", "(plan l-972536)"), `REFERENCE` what a dropped code was said to do to
its record ("(REQ-26 대체)", "(REQ-26 follow-up)"), `EVIDENCE_SUFFIX` what follows an evidence id ("EV-3류"), and
`KIND_PREFIX` the kind tag the server once wrote at the head of a title ("의사결정 승인: "). Each is a regex fragment.

`GLOSSARY` is the other way round: a field or code name an agent may write, in the words of the text it sits in.
"""
from justshipit.i18n import Lang

PARTICLES: dict[Lang, tuple[str, ...]] = {
    "ko": ("에서", "에게", "으로", "로", "에", "의", "와", "과", "를", "을", "은", "는", "이", "가"),
    "en": (),
}
CITE: dict[Lang, tuple[str, ...]] = {
    "ko": ("지시서", "근거", "지시", "출처", "같은 일:"),
    "en": ("plan", "see", "source", "evidence", "same as"),
}
REFERENCE: dict[Lang, tuple[str, ...]] = {
    "ko": ("대체", "반영", "재제출", "후속", "관련", "참고"),
    "en": ("follow-up", "related"),
}
EVIDENCE_SUFFIX: dict[Lang, tuple[str, ...]] = {
    "ko": ("류", "등"),
    "en": (),
}
KIND_PREFIX: dict[Lang, tuple[str, ...]] = {
    "ko": (r"(?:의사결정|결정)\s*승인",),
    "en": (),
}
GLOSSARY: dict[Lang, dict[str, str]] = {
    "en": {
        "open_questions": "open questions", "capability_gaps": "skill gaps", "action_tasks": "tasks to do",
        "validation_plan": "test plan", "development_needs": "what to build", "evidence_ids": "evidence",
        "post_card": "post card", "brief_lines": "plan lines", "red_line_conflicts": "clashes with the limits",
        "executive_summary": "summary", "CONDITIONAL-GO": "go, with conditions", "CONDITIONAL_GO": "go, with conditions",
        "NO-GO": "stop", "NO_GO": "stop",
    },
    "ko": {
        "open_questions": "미해결 질문", "capability_gaps": "역량 공백", "action_tasks": "실행 업무",
        "validation_plan": "검증 계획", "development_needs": "개발 필요 항목", "evidence_ids": "근거",
        "post_card": "게시 카드", "brief_lines": "계획의 줄", "red_line_conflicts": "금지선 충돌",
        "executive_summary": "요약", "CONDITIONAL-GO": "조건부 진행", "CONDITIONAL_GO": "조건부 진행",
        "NO-GO": "중단", "NO_GO": "중단",
    },
}
