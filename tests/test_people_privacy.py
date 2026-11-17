"""The list holds no contact details and archive reaches every place a person's data went. Each case here is a way
a review got past the first version."""
import pytest
from justshipit.db.models import Person
from justshipit.services import owner_tasks, people, people_tasks
from people_kit import ISSUE_QUOTE, make_business
from sqlalchemy import select


# ------------------------------------------------------------------ the detector

@pytest.mark.parametrize("text", [
    "john＠example.com", "john at gmail dot com", "john AT gmail DOT com", "john at gmail.com",
    "john (at) gmail (dot) com", "john [at] gmail [dot] com", "john 골뱅이 naver 점 com", "홍길동@naver.com",
    "010.1234.5678", "010 1234 5678", "01012345678", "010-12345678", "+82 10 1234 5678", "+82-10-1234-5678",
    "82-10-1234-5678", "0101234567 8", "010 1234 56 78", "공일공 일이삼사 오육칠팔", "공일공-일이삼사-오육칠팔",
    "０１０－１２３４－５６７８", "010–1234–5678", "02-1234-5678", "031 123 4567", "0212345678",
    "카톡 아이디: abcd123", "kakao: abcd123", "텔레그램: @abcd123", "WhatsApp = alice_dev",
])
def test_the_detector_sees_a_contact_detail_however_it_is_spelled(text):
    assert people.contact_info_problem(f"연락은 {text} 로 주세요") is not None


@pytest.mark.parametrize("text", [
    "we stopped at step.sh then retried", "looked at the logs. Then it failed", "meet at 5. Then 6 more",
    "ask @alice-dev about it", "version 3.12.1 broke it", "line: 42 of the file", "signal: SIGKILL arrived",
    "build 1234567890 failed", "it took 12 34 56 minutes", "2024년 11월 3일에 3시간 걸렸다", "5xx rate 0.4 percent",
    "I looked at gmail for a while", "kakao pay was slow", "공일공 규칙", "이 일이 이사 오는 일",
])
def test_the_detector_leaves_ordinary_prose_alone(text):
    assert people.contact_info_problem(text) is None


def test_the_labels_say_what_was_found_and_a_refusal_does_not_repeat_it():
    assert people.contact_info_problem("john at gmail dot com") == "email"
    assert people.contact_info_problem("010 1234 5678") == "phone"
    assert people.contact_info_problem("kakao: abcd123") == "messenger"
    with pytest.raises(people.PeopleError) as refused:
        people.no_contact("note", "call 010 1234 5678", lang="ko")
    assert "5678" not in str(refused.value) and "저장하지 않았어요" in str(refused.value)


def test_a_brief_line_that_still_reads_as_a_contact_after_redaction_is_replaced_whole():
    assert people.redact("민수 (010 1234 5678)", lang="ko") == "민수 ([연락처 생략])"
    assert people.redact("민수 공일공 일이삼사 오육칠팔", lang="ko") == "[연락처 생략]"
    assert people.redact("지은 (jieun at naver dot com)", lang="ko") == "지은 ([연락처 생략])"


# ------------------------------------------------------------------ what a finished or skipped task may hold

def person_task(s, handle="carol", n=2):
    b = make_business(s)
    p, _ = people.insert(s, b.id, people.NewPerson("github", handle, f"https://github.com/a/b/issues/{n}",
                                                   "public_problem", quote=ISSUE_QUOTE), lang="ko")
    t = people_tasks.create_task(s, p.id, "contact", actor="system", lang="ko")
    s.flush()
    return b, p, t


@pytest.mark.parametrize("note", ["carol@example.com 으로 답함", "전화 010-9999-8888", "카톡 아이디: carol99"])
def test_a_person_task_cannot_be_finished_with_a_contact_detail_in_its_note(korean_owner, s, note):
    _, _, t = person_task(s)
    with pytest.raises(owner_tasks.OwnerTaskError, match="저장하지 않았어요") as refused:
        owner_tasks.done(s, t.id, note=note, outcome=people_tasks.Outcome(stage="contacted"))
    assert note not in str(refused.value)
    assert t.status == owner_tasks.OPEN and t.result is None


def test_a_person_task_cannot_be_skipped_with_a_contact_detail_in_its_note(korean_owner, s):
    _, _, t = person_task(s)
    with pytest.raises(owner_tasks.OwnerTaskError, match="저장하지 않았어요"):
        owner_tasks.skip(s, t.id, note="나중에 010-9999-8888 로")
    assert t.status == owner_tasks.OPEN


def test_the_link_and_the_names_of_the_numbers_are_checked_too(korean_owner, s):
    _, _, t = person_task(s)
    outcome = people_tasks.Outcome(stage="contacted")
    with pytest.raises(owner_tasks.OwnerTaskError, match="저장하지 않았어요"):
        owner_tasks.done(s, t.id, link="https://github.com/a/b/issues/9?reply=carol@example.com", outcome=outcome)
    with pytest.raises(owner_tasks.OwnerTaskError, match="저장하지 않았어요"):
        owner_tasks.done(s, t.id, numbers={"010-1234-5678": 1}, outcome=outcome)
    assert t.status == owner_tasks.OPEN


def test_a_participate_task_cannot_hold_a_contact_detail_either(korean_owner, s):
    b = make_business(s)
    thread = people_tasks.Thread("stackoverflow", "https://stackoverflow.com/questions/77/x", "bob",
                                 "How do I find which request caused the crash in a 3GB log", "로그에서 요청 찾는 법",
                                 ["요청 id로 묶어 본다"])
    t = people_tasks.create_participate(s, b.id, thread, lang="ko")
    s.flush()
    with pytest.raises(owner_tasks.OwnerTaskError, match="저장하지 않았어요"):
        owner_tasks.done(s, t.id, note="bob's phone 010-1234-5678 per DM", outcome=people_tasks.Outcome())
    assert t.status == owner_tasks.OPEN


def test_a_task_that_is_not_about_a_person_keeps_the_owner_s_own_note(s):
    b = make_business(s)
    t = owner_tasks.OwnerTask(business_id=b.id, kind="other", title="문의", card={}, status=owner_tasks.OPEN)
    s.add(t)
    s.flush()
    owner_tasks.done(s, t.id, note="답장은 support@vendor.com 으로 옴")
    assert t.status == owner_tasks.DONE


def test_a_hand_entry_cannot_hide_a_phone_number_in_its_link(s):
    b = make_business(s)
    with pytest.raises(people.PeopleError, match="저장하지 않았어요"):
        people.create_by_hand(s, b.id, platform="direct", handle="dave", display_name=None,
                              url="owner:010-1234-5678", quote=None, why=None, lang="ko")
    with pytest.raises(people.PeopleError, match="저장하지 않았어요"):
        people.create_by_hand(s, b.id, platform="direct", handle="010-1234-5678", display_name=None, url=None,
                              quote=None, why=None, lang="ko")
    assert s.scalar(select(Person.id).where(Person.business_id == b.id)) is None
