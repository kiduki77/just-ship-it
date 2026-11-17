"""Korean the owner reads should read as written in Korean. The ko catalogs fail on the most mechanical 번역체
patterns from docs/KOREAN-STYLE.md. This is a guard against the obvious, not a judge of style: a sentence can pass
and still read badly."""
import re
from dataclasses import dataclass

import pytest
from justshipit.i18n import catalog
from justshipit.i18n.prompting import output_rule
from i18n_kit import web_catalog


@dataclass(frozen=True)
class Smell:
    id: str
    pattern: re.Pattern[str]
    instead: str


SMELLS: tuple[Smell, ...] = (
    Smell("formal", re.compile(r"습니다|습니까|십시오"), "해요체: 저장했어요, 기다려요"),
    Smell("double_passive", re.compile(r"되어지|되어진|되어져"), "an active verb: 저장했어요"),
    Smell("uncontracted", re.compile(r"되었|하였"), "됐어요, 했어요"),
    Smell("in_terms_of", re.compile(r"에 있어서"), "~할 때, ~에서"),
    Smell("will_do", re.compile(r"하도록 하겠"), "~할게요"),
    Smell("successfully", re.compile(r"성공적으로"), "drop it: 저장했어요"),
    Smell("through", re.compile(r"[을를] 통해|통하여"), "~에서, ~로"),
    Smell("in_case_of", re.compile(r"의 경우"), "~하면, ~일 때"),
    Smell("is_possible", re.compile(r"것이 가능|것도 가능"), "~할 수 있어요"),
    Smell("by_agent", re.compile(r"에 의해|에 의한"), "the doer as the subject: CEO가 승인했어요"),
    Smell("have", re.compile(r"[을를] (?:가지고|갖고) 있"), "~이 있어요"),
    Smell("pronoun", re.compile(r"그것|그들"), "name the thing, or drop it"),
    Smell("you", re.compile(r"당신"), "no address, or 오너님"),
    # 음슴체 from a past tense ("봤음", "들었음") inside 해요체 copy. A noun label ("완료", "철회됨", "출처 있음") is fine.
    Smell("eumseum", re.compile("[" + "".join(chr(0xAC00 + i * 28 + 20) for i in range(399)).replace("있", "")
                                + "]음(?![가-힣])"),
          "a noun tag (직접 본 일, 들은 일) or 해요체"),
)

# "~에 대해" is often fine once. Many of them is translation; this share of messages may use it.
ABOUT = re.compile(r"에 대(?:해|한|하여)")
ABOUT_SHARE = 0.02

# key -> the smells it may keep, each with the reason it is right there.
ALLOW: dict[str, frozenset[str]] = {}


def _korean() -> dict[str, list[str]]:
    """Every ko message in the console's and the server's catalogs, by key, each plural form as its own text."""
    out: dict[str, list[str]] = {}
    entries = [*((k, e) for area in web_catalog().values() for k, e in area.items()), *catalog().items()]
    for key, entry in entries:
        ko = entry.get("ko")
        if ko is not None:
            out[key] = list(ko.values()) if isinstance(ko, dict) else [ko]
    return out


def smells_in(text: str) -> set[str]:
    return {s.id for s in SMELLS if s.pattern.search(text)}


@pytest.mark.parametrize(("text", "found"), [
    ("성공적으로 저장되었습니다", {"formal", "successfully", "uncontracted"}),
    ("대시보드를 통해 확인하도록 하겠습니다", {"through", "will_do", "formal"}),
    ("실패의 경우 그것을 다시 시도해요", {"in_case_of", "pronoun"}),
    ("CEO에 의해 승인되어진 결정", {"by_agent", "double_passive"}),
    ("권한을 갖고 있어요", {"have"}),
    ("나머지 2건은 업무와 작업 대기열에 있어요", set()),
    ("결정 3건이 기다려요. 예산을 늘리면 이어서 해요.", set()),
    ("개발이 끝나 브랜치를 전달했어요", set()),
    ("CEO가 접어 두자고 했어요", set()),
    ("근거 수준(봤음, 들었음, 짐작)", {"eumseum"}),
    ("어떻게 알았는지(직접 본 일, 들은 일, 짐작) · 철회됨 · 출처 있음", set()),
])
def test_the_lint_finds_the_patterns_and_leaves_plain_korean_alone(text, found):
    assert smells_in(text) == found


def test_no_korean_catalog_message_reads_like_a_translation():
    hits = {}
    for key, texts in _korean().items():
        found = set().union(*(smells_in(t) for t in texts)) - ALLOW.get(key, frozenset())
        if found:
            hits[key] = sorted(found)
    advice = {s.id: s.instead for s in SMELLS}
    assert hits == {}, "번역체 in the ko catalogs (docs/KOREAN-STYLE.md): " + "; ".join(
        f"{key} has {', '.join(f'{f} (write {advice[f]})' for f in found)}" for key, found in hits.items())


def test_few_korean_messages_lean_on_about():
    texts = _korean()
    about = sorted(key for key, ts in texts.items() if any(ABOUT.search(t) for t in ts))
    assert len(about) <= max(2, int(len(texts) * ABOUT_SHARE)), (
        f"{len(about)} ko messages say '~에 대해/에 대한' ({', '.join(about)}). Say what the thing is instead")


def test_the_korean_output_rule_asks_for_natural_korean_and_leaves_schema_values_alone():
    ko, en = output_rule("ko"), output_rule("en")
    style = ko.split("Style: ", 1)[1].split("\n", 1)[0]
    assert "해요체" in style and "번역투" in style and "결론을 먼저" in style and "저장했어요" in style
    assert len(style) <= 300, "every agent call carries this line; say more in docs/KOREAN-STYLE.md instead"
    assert "해요체" not in en
    for rule in (ko, en):
        assert "JSON keys and enum values" in rule


def test_every_allowance_is_still_needed():
    texts = _korean()
    stale = {key: sorted(ids - set().union(*(smells_in(t) for t in texts.get(key, []))))
             for key, ids in ALLOW.items()}
    assert {k: v for k, v in stale.items() if v} == {}, "delete these from ALLOW in tests/test_korean_style.py"
