"""What the owner reads first carries no internal ids or names, and stays one short line."""
import pytest
from justshipit.contracts.owner_text import body, clip, detail, one_line, title


@pytest.mark.parametrize("raw,shown", [
    ("지시서 l-d4bdf1 보호자 채널, 6주 30명 목표의 1순위 채널 가설", "지시서 보호자 채널, 6주 30명 목표의 1순위 채널 가설"),
    ("보호자 채널 (지시서 l-972536, l-c4668e…)이에요", "보호자 채널이에요"),
    ("open_questions의 자문 후보 7개", "미해결 질문의 자문 후보 7개"),
    ("EV-252류 근거가 약해요", "근거가 약해요"),
    ("검토 결과 rework (contested=False)", "검토 결과 rework"),
    ("판정은 CONDITIONAL-GO예요", "판정은 조건부 진행예요"),
    ("registry/projects/p/report.md에 있어요", "에 있어요"),
    ("pstack:architect로 검토했어요", "로 검토했어요"),
    ("커밋 a1b2c3d4e5f 기준이에요", "커밋 기준이에요"),
    ("가이드라인 본문 미확인, 2차 자료 [E9]", "가이드라인 본문 미확인, 2차 자료"),
    ("**가장 강한 반론은** 이것이에요", "가장 강한 반론은 이것이에요"),
    ("[conditional] 결론: 진행해요", "결론: 진행해요"),
    ("첫 줄\n둘째 줄", "첫 줄 둘째 줄"),
])
def test_a_line_read_first_drops_internal_ids_names_and_markdown(raw, shown):
    assert one_line(raw, 200) == shown


def test_detail_keeps_evidence_ids_code_names_and_lines_but_not_brief_ids():
    raw = "근거는 EV-12예요.\n`quote_parser.py`의 parse_mail이 비어요 (지시서 l-972536)"
    assert detail(raw) == "근거는 EV-12예요.\n`quote_parser.py`의 parse_mail이 비어요"


def test_a_link_is_left_whole():
    raw = "자세히는 https://github.com/a/b/commit/a1b2c3d4e5f6/open_questions 에 있어요"
    assert one_line(raw, 200) == raw


def test_a_long_line_is_cut_at_a_sentence_or_a_space_and_marked():
    assert clip("짧아요", 10) == "짧아요"
    assert clip("가" * 50 + ". " + "나" * 50, 70) == "가" * 50 + "."
    cut = clip("가나다 " * 30, 40)
    assert len(cut) <= 40 and cut.endswith("…") and not cut.endswith(" …")


def test_a_folder_the_owner_is_asked_to_use_stays_in_the_text():
    """The KOSIS ask tells the owner to put files in var/data; that path is the owner's instruction, not a leak."""
    assert one_line("파일을 var/data/quote-bot/에 넣어 주세요", 200) == "파일을 var/data/quote-bot/에 넣어 주세요"


def test_a_requests_own_text_keeps_an_engineers_question_and_a_skill_path():
    """Review: the engineer's question rides in the summary and comes back in clarifications.md; scrubbing flags,
    paths and field names there rewrote the question itself."""
    q = "Should we default retry=True, or keep max_retries=None? evidence_ids in the open_questions table?\n파일: registry/skills/x/"
    assert body(q + " (지시서 l-972536) pstack:architect") == q


def test_a_title_keeps_its_words_and_names():
    assert one_line("AI-OS PR-FAQ 초안: quote_parser 버그", 80, names=True) == "AI-OS PR-FAQ 초안: quote_parser 버그"
    assert one_line("AI-OS PR-FAQ 초안: quote_parser 버그", 80) == "AI-OS PR-FAQ 초안: 버그"


@pytest.mark.parametrize("raw,shown", [
    ("의사결정 승인: 원두재고 웹앱: 개발 착수 패키지 v1.4로 구현", "원두재고 웹앱: 개발 착수 패키지 v1.4로 구현"),
    ("명세 승인 및 개발 착수: BUSINE2-0004 수신 Worker와 동의서 게이트 (REQ-32)",
     "명세 승인 및 개발 착수: 수신 Worker와 동의서 게이트"),
    ("REQ-36 30일 자동 삭제", "30일 자동 삭제"),
    ("토요일 현장 전단 A5 앞면 전문 (REQ-26 대체)", "토요일 현장 전단 A5 앞면 전문"),
    ("참고본(오너 최종본, REQ-17~20·REQ-25·REQ-26 반영): 첫 글", "참고본(오너 최종본): 첫 글"),
    ("최종본 (T-30 정본 기준, 바로 개발)", "최종본 (정본 기준, 바로 개발)"),
    ("T-30 저장본", "저장본"),
    ("최종 종합 수정: REQ-24 승인 조건 3개 다시 맞추기", "최종 종합 수정: 승인 조건 3개 다시 맞추기"),
    ("OT-14에 붙일 장소별 QR", "붙일 장소별 QR"),
    ("전단 앞면 — REQ-28/OT-14 문구 전문 제출본 (REQ-26·REQ-28 대체)", "전단 앞면 — 문구 전문 제출본"),
    ("비치 허락 말(REQ-26 재제출)", "비치 허락 말"),
    ("BET-3 회고 (REQ-26, OT-4)", "회고"),
    ("REQ-5: 명세 작성", "명세 작성"),
    ("개발(J-3)이 예산이 모자라 멈췄어요", "개발이 예산이 모자라 멈췄어요"),
])
def test_an_owner_title_drops_boilerplate_and_the_codes_of_other_records(raw, shown):
    """The live 결정 panel read '의사결정 승인: 원두재고 웹앱: …' and REQ-50 '… 동의서 게이트 (REQ-32)'."""
    assert title(raw) == shown


def test_an_owner_title_keeps_names_numbers_and_dashed_words():
    raw = "AI-OS PR-FAQ 초안: quote_parser 버그, A5 3종, 2026-11 계획, GPT-4 비교"
    assert title(raw) == raw
