"""D6: public copy the owner pastes holds no invented experience and no internal note (persona round 2026-11-04)."""
import pytest
from owner_task_kit import post_card, what

from justshipit.services import copy_rules, owner_tasks
from justshipit.services.owner_tasks import CardRefused

# What the marketing agent wrote for 동네산책: the owner's interview notes about a 과장님, as the owner's own past.
TESTIMONIAL = ("안녕하세요, 망원동에 사는 ○○○입니다. 저는 평일엔 아침 7시에 10분 산책이 전부이고 낮엔 강아지가 10시간쯤 "
               "혼자 있어요. 펫시터 앱도 써 봤는데 매번 다른 분이 오는 게 마음에 걸려서 그만뒀습니다. 그래서 같은 동네에 "
               "사는 한 분이 꾸준히 맡는 방식이 가능할지 알아보고 있어요.")
# The 당근알바 job post: a note to the owner about the form, an evidence id and a "not final" remark.
JOB_POST = ("망원동 이웃 보호자와 산책 도우미를 이어 주는 서비스를 시험 중입니다. 급여 입력란에는 숫자가 필요해 (시범 안) "
            "시급 10,700원을 적어 둡니다(2027년 최저임금 수준, 확정 아님) [E15].")


def _draft_card(body: str, title: str = "망원동 강아지 산책 도우미 대기자"):
    return post_card("other", where_url=None, what=what(title=title, body=body, facts=[], expected_questions=[]))


def test_a_draft_that_turns_notes_into_the_owner_s_own_experience_is_refused():
    with pytest.raises(CardRefused) as exc:
        owner_tasks.build_post_card(_draft_card(TESTIMONIAL), None, "en")
    assert exc.value.rule == "invented_experience" and "써 봤는데" in str(exc.value)


def test_a_draft_with_a_note_left_for_the_owner_is_refused():
    with pytest.raises(CardRefused) as exc:
        owner_tasks.build_post_card(_draft_card(JOB_POST), None, "en")
    assert exc.value.rule == "internal_note"


@pytest.mark.parametrize("text", ["지시서 l-6f79ae의 목표", "근거 EV-252 참고", "T-26에서 정한 시간", "링크: (utm: danggeun/g-w1)"])
def test_ids_and_link_notes_are_internal(text):
    assert copy_rules.internal_note(text)


def test_a_placeholder_for_the_owner_s_experience_and_present_facts_pass():
    body = ("안녕하세요, 망원동에 사는 ○○○입니다. 저는 평일 낮에 강아지가 혼자 있어요. "
            f"{copy_rules.PLACEHOLDER} 그래서 같은 동네 한 분이 꾸준히 맡는 방식을 알아보고 있어요. 카페 오너분들도 환영해요.")
    card, _ = owner_tasks.build_post_card(_draft_card(body), None, "en")
    assert card["what"]["body"] == body


def test_a_third_person_account_is_not_an_invented_experience():
    assert copy_rules.invented_experience("이웃 보호자들은 펫시터 앱을 써 봤지만 매번 다른 분이 와서 그만뒀다고 말합니다.") is None
