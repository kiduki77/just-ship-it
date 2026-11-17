"""The idea review panel (`services/idea_review.py`): its lenses, its refusals and its notes."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "idea_review.lens.market": {"en": "Market and competition", "ko": "시장·경쟁"},
    "idea_review.lens.timing": {"en": "Timing and feasibility", "ko": "시점·실현 가능성"},
    "idea_review.lens.demand": {"en": "Demand and reach", "ko": "수요·도달"},

    "idea_review.error.not_found": {"en": "Business B-{id} not found.", "ko": "B-{id} 사업을 찾지 못했어요."},
    "idea_review.error.archived": {"en": "B-{id} is archived, so the critics won't review it again.",
                                   "ko": "B-{id|는} 보관한 사업이라 다시 평가하지 않아요."},
    "idea_review.error.busy": {"en": "The critics are still reviewing. You can ask again once they're done.",
                               "ko": "평가단이 아직 평가하고 있어요. 끝나면 다시 맡길 수 있어요."},
    "idea_review.activity.rereview": {"en": "B-{id}: asked the idea critics for review {round}",
                                      "ko": "B-{id}: 아이디어 평가단에 {round}회차 평가를 맡겼어요"},
    "idea_review.note.usage_wait": {"en": "The {reason} was reached, so the critics pick this up at {until}.",
                                    "ko": "{reason}에 닿아서 {until}에 이어서 평가해요."},
    "idea_review.note.failed": {"en": "The critics couldn't review this angle. Press “Review again” to run all three.",
                                "ko": "평가단이 이 관점을 평가하지 못했어요. '다시 평가'를 누르면 세 관점을 다시 봐요."},
    "idea_review.note.cancelled": {"en": "You cancelled this review before it started. Press “Review again” to run all three.",
                                   "ko": "시작하기 전에 평가를 취소했어요. '다시 평가'를 누르면 세 관점을 다시 봐요."},
}
