"""Where a new ask about something already on a card went (`services/matters.py`): the reason the activity log gives
when the server folds it into a card, an owner to-do or a note instead of raising a new card."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "matter.waits_on_draft": {
        "en": "Kept as a candidate for the next version. It goes onto the spec's card once the product agent has "
              "written the spec",
        "ko": "다음 버전 후보로 올려 둘게요. 제품기획 에이전트가 명세를 쓰면 그 카드에 함께 올라와요"},
    "matter.waits_on_redraft": {
        "en": "Kept as a candidate for the next version. It goes onto the spec's card once the product agent has "
              "rewritten the spec from your notes",
        "ko": "다음 버전 후보로 올려 둘게요. 메모대로 명세를 다시 쓰면 그 카드에 함께 올라와요"},
    # These never name the spec's key: the owner reads no key, and the one the console drops took its particle along.
    "matter.spec_in_progress": {"en": "The product agent is writing a spec", "ko": "제품기획 에이전트가 쓰고 있는 명세가 있어요"},
    "matter.waits_on_questions": {
        "en": "Kept for the spec's card as a candidate for the next version. The card goes up once you answer the "
              "spec's questions",
        "ko": "명세 카드에 다음 버전 후보로 올려 둘게요. 명세 질문에 답하면 카드가 올라와요"},
    "matter.next_version": {"en": "Noted on the spec's card as a candidate for the next version",
                            "ko": "명세 카드에 다음 버전 후보로 적어 뒀어요"},
    "matter.reask_deferred": {"en": "Raised the held spec card again", "ko": "보류한 명세 카드를 다시 올렸어요"},
    "matter.reask_withdrawn": {"en": "Raised the withdrawn spec card again", "ko": "철회한 명세 카드를 다시 올렸어요"},
    "matter.reask_plan": {"en": "Raised the held product plan card again", "ko": "보류한 기획안 카드를 다시 올렸어요"},
    "matter.waits_on_plan": {"en": "Added to the product plan being written", "ko": "쓰고 있는 기획안에 함께 붙였어요"},
    "matter.building": {"en": "Building from the approved spec", "ko": "승인한 명세로 개발하고 있어요"},
    "matter.built": {"en": "The build of the approved spec is done and waits for the merge decision",
                     "ko": "승인한 명세의 개발이 끝나서 머지 결정을 기다려요"},
    "matter.approved_idle": {"en": "There's an approved spec, with no build running now",
                             "ko": "승인한 명세가 있어요. 지금 돌고 있는 개발 작업은 없어요"},
    "matter.added_to": {"en": "Added to the card '{title}'", "ko": "'{title}' 카드에 함께 붙였어요"},
    "matter.rejected": {"en": "You declined this", "ko": "오너님이 반려한 일이에요"},
    "matter.to_task": {"en": "Added the revision to your to-do OT-{id}", "ko": "내 할 일 OT-{id}에 고친 내용을 붙였어요"},
    "matter.approved": {"en": "You already approved this", "ko": "오너님이 이미 승인한 일이에요"},
}
