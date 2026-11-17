"""Korean particles and English counts that read wrong once a value is filled in. These are the 번역체 the live check
found on real screens: "11분 후이에요", "CEO이(가) 일했어요", "now 0/10people". Each rule reads every catalog, the
console's and the server's, so a new message is checked the day it is written (docs/I18N.md, docs/KOREAN-STYLE.md)."""
import re

import pytest
from justshipit.i18n import catalog
from i18n_kit import web_catalog

# The particle forms `{name|을}` knows (web/src/i18n/format.ts, justshipit.i18n._PARTICLES), longest first.
FORMS = ("이었어요", "였어요", "이에요", "예요", "이라고", "라고", "으로", "이나", "이랑", "이라",
         "을", "를", "이", "가", "은", "는", "과", "와", "로", "랑", "나", "라", "아", "야")
# A particle written by hand right after a value: "{when}이에요", "{name}을(를)". The form must end the word, so
# "{n}가지" and "{spent}이전" are words of their own.
HAND_PARTICLE = re.compile(r"\{(\w+)\}(" + "|".join(FORMS) + r")(?![가-힣])")
PARTICLE = re.compile(r"\{(\w+)\|([^{}]+)\}")
# English "{current}/{target}{unit}" reads "0/10people". Write "{current} of {target} {unit}".
GLUED_UNIT = re.compile(r"\{\w+\}/\{\w+\}(?:\{\w+\}|[A-Za-z])")

# Values whose text always ends in Hangul or a digit, so `{value|을}` picks the right form. Every other value may
# end in a Latin letter (an agent's name "CEO", a channel "Reddit", a person, an English title written before a
# language switch), and `{value|을}` would print "을(를)". Put a fixed word after such a value instead:
# "{name} 에이전트예요", "{channel} 채널로". The key is a value name, or "message.key:value" for one message.
ENDS_IN_HANGUL_OR_DIGIT: dict[str, str] = {
    "id": "a number or a ref such as REQ-12",
    "ref": "a ref such as REQ-12 or OT-3",
    "req": "a ref such as REQ-12",
    "version": "a version number such as v3",
    "n": "a count",
    "numbers": "the numbers of a plan line, digits",
    "decide.inbox.week:theirs": "a count with its counter, such as 결정 3건",
    "home.status.both:d": "home.status.n_decisions in Korean, a count with its counter: 결정 3건",
    "home.status.both:m": "home.status.n_todos in Korean, a count with its counter: 내 할 일 2개",
    "common.activity.artifact:kind": "a document kind from label.artifact, in Korean",
    "common.activity.created:entity": "an entity word from label.entity, in Korean",
    "common.activity.created_bare:entity": "an entity word from label.entity, in Korean",
    "owner_task.error.bad_move:to": "a to-do status from the owner_task catalog, in Korean",
    "people.contact.refused:found": "a kind of contact (이메일, 전화번호) from the people catalog",
    "people.card.contact:found": "a kind of contact (이메일, 전화번호) from the people catalog",
    "promotion.skip.profile_unknown:lines": "a profile.line label (누가 사나, 어떻게 닿나, 이미 가진 것), which ends in Hangul",
    "kickoff.invalid.too_long:field": "a plan field's Korean name from the kickoff catalog",
    "kickoff.invalid.missing:field": "a plan field's Korean name from the kickoff catalog",
    "work.ev.fact:fact": "label.status.verified_fact, 검증된 사실",
    "staffing.why.check.reviewer_role:owner": "always risk, whose Korean name ends in Hangul",
    "staffing.why.check.reviewer_role:checker": "always strategy, whose Korean name ends in Hangul",
    "build.spec_kept:key": "a spec key, which ends in its four-digit number (LANDIN-0003)",
}


def _messages() -> list[tuple[str, str, str]]:
    """(key, language, text) for every message in every catalog, each plural form on its own."""
    entries = [*((k, e) for area in web_catalog().values() for k, e in area.items()), *catalog().items()]
    out = []
    for key, entry in entries:
        for lang in ("en", "ko"):
            m = entry.get(lang)
            for text in (m.values() if isinstance(m, dict) else [m] if m else []):
                out.append((key, lang, text))
    return out


def hand_particles(text: str) -> list[str]:
    return [m.group(0) for m in HAND_PARTICLE.finditer(text)]


def latin_particles(key: str, text: str) -> list[str]:
    return [m.group(0) for m in PARTICLE.finditer(text)
            if m.group(1) not in ENDS_IN_HANGUL_OR_DIGIT and f"{key}:{m.group(1)}" not in ENDS_IN_HANGUL_OR_DIGIT]


@pytest.mark.parametrize(("text", "found"), [
    ("다음은 {name}, {when}이에요.", ["{when}이에요"]),
    ("{link}이 있어요.", ["{link}이"]),
    ("{name}을(를) 저장했어요", ["{name}을"]),
    ("역량 공백 {n}가지가 모여 있어요", []),
    ("{spent}이전 워커가 일해요", []),
    ("결정 {n}건이 기다려요", []),
    ("{ref|을} 찾지 못했어요", []),
])
def test_the_lint_finds_a_particle_written_by_hand_after_a_value(text, found):
    assert hand_particles(text) == found


def test_the_lint_finds_a_particle_after_a_value_that_may_be_latin():
    assert latin_particles("home.agents.last", " 마지막으로 {name|이} {when}에 일했어요.") == ["{name|이}"]
    assert latin_particles("request.not_found", "{ref|을} 찾지 못했어요.") == []
    assert latin_particles("decide.inbox.week", "프로젝트 매니저가 {theirs|을} 정했어요.") == []


def test_the_lint_finds_a_count_glued_to_its_unit():
    assert GLUED_UNIT.search(" · now {current}/{target}{unit}")
    assert GLUED_UNIT.search("{done}/{total}people")
    assert not GLUED_UNIT.search("{current} of {target} {unit} so far")
    assert not GLUED_UNIT.search("(try {attempts}/{max})")


def test_no_korean_message_writes_a_particle_by_hand_after_a_value():
    hits = {k: hand_particles(t) for k, lang, t in _messages() if lang == "ko" and hand_particles(t)}
    assert hits == {}, ("write {value|이에요} for a particle after a value, or put a fixed word after it "
                        f"(docs/I18N.md): {hits}")


def test_no_korean_particle_follows_a_value_that_may_be_latin():
    hits = {k: latin_particles(k, t) for k, lang, t in _messages() if lang == "ko" and latin_particles(k, t)}
    assert hits == {}, ("this value may end in a Latin letter and print '을(를)'. Put a fixed word after it, or add it "
                        f"to ENDS_IN_HANGUL_OR_DIGIT with the reason it never does: {hits}")


def test_no_english_count_is_glued_to_its_unit():
    hits = sorted(k for k, lang, t in _messages() if lang == "en" and GLUED_UNIT.search(t))
    assert hits == [], f"write '{{current}} of {{target}} {{unit}}', not '{{current}}/{{target}}{{unit}}': {hits}"


def test_every_value_said_to_end_in_hangul_or_a_digit_is_still_used():
    used = {(k, m.group(1)) for k, lang, t in _messages() if lang == "ko" for m in PARTICLE.finditer(t)}
    names = {v for _, v in used} | {f"{k}:{v}" for k, v in used}
    assert sorted(set(ENDS_IN_HANGUL_OR_DIGIT) - names) == [], "delete these from ENDS_IN_HANGUL_OR_DIGIT"
