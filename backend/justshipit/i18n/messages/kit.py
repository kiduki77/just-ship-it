"""The First 10 kit: who to reach today, a message for each, and wording for the owner's profile and invites, all written
for the owner to copy (`services/kit.py`, `api/kit.py`). The console's own words for the section are in its catalog."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    # why a text was not kept (`KitText.refused`), as the owner reads it beside "Not written"
    "kit.refused.no_draft": {"en": "The company did not write this one.", "ko": "회사가 이 문구를 쓰지 못했어요."},
    "kit.refused.empty": {"en": "The text came back empty.", "ko": "문구가 비어 있었어요."},
    "kit.refused.too_long": {"en": "It was longer than the limit, so it was not kept.",
                             "ko": "글자 수 제한을 넘어서 남기지 않았어요."},
    "kit.refused.contact": {"en": "It held an email address, a phone number or a messenger ID. The kit never keeps those.",
                            "ko": "이메일, 전화번호, 메신저 ID가 들어 있어서 남기지 않았어요. 키트에는 연락처를 담지 않아요."},
    "kit.refused.foreign_link": {"en": "It held a link that is not yours.",
                                 "ko": "오너님의 것이 아닌 링크가 들어 있어서 남기지 않았어요."},
    "kit.refused.no_link": {"en": "It left out your link.", "ko": "오너님의 링크가 빠져 있어서 남기지 않았어요."},
    "kit.refused.blanks": {"en": "It left blanks for you to fill in.", "ko": "채워 넣어야 할 빈칸이 남아 있어서 남기지 않았어요."},
    "kit.refused.mixed_register": {"en": "It mixed polite and formal endings.",
                                   "ko": "해요체와 합니다체가 섞여 있어서 남기지 않았어요."},
    "kit.refused.mixed_voice": {"en": "It spoke as \"I\" and as \"we\" in one text.",
                                "ko": "한 글에서 \"저\"와 \"저희\"가 섞여 있어서 남기지 않았어요."},
    "kit.refused.past_talk": {"en": "It said you and this person had talked before.",
                              "ko": "이 사람과 전에 이야기한 것처럼 써서 남기지 않았어요."},
    "kit.refused.invented_experience": {"en": "It said something you went through that you never told the company.",
                                        "ko": "오너님이 말한 적 없는 경험을 겪었다고 써서 남기지 않았어요."},
    "kit.refused.internal_note": {"en": "It held a note the company wrote for itself, such as an ID.",
                                  "ko": "회사 안에서만 쓰는 번호나 메모가 들어 있어서 남기지 않았어요."},
    "kit.refused.asks_votes": {"en": "It asked for votes, stars or reviews.",
                               "ko": "추천, 별점, 후기를 부탁하는 말이 들어 있어서 남기지 않았어요."},
    "kit.refused.several_people": {"en": "It was written for more than one person. A message is for one person.",
                                   "ko": "여러 사람에게 쓴 글이라서 남기지 않았어요. 메시지는 한 사람에게 한 개씩 써요."},
    "kit.refused.asks_spread": {"en": "It asked this person to pass it on to several people.",
                                "ko": "여러 사람에게 퍼뜨려 달라는 말이 들어 있어서 남기지 않았어요."},
    "kit.refused.unsaid_quote": {"en": "It quoted words this person never wrote.",
                                 "ko": "이 사람이 쓴 적 없는 말을 따옴표로 옮겨서 남기지 않았어요."},
    "kit.refused.forgotten": {"en": "You removed this person from your list, so their text is gone.",
                              "ko": "명단에서 지운 사람이라 문구도 지웠어요."},

    # what the model is told when it wrote something the owner never lived through (`kit.refused.invented_experience` is
    # what the owner reads): the post card's way out, a placeholder to fill in, has no place in a text the owner sends as is
    "kit.reason.invented": {
        "en": "A sentence reads as if the owner lived it ('{found}'). The owner told you no experience of their own, so "
              "remove that sentence and write nothing about the owner's experience. Do not leave a placeholder.",
        "ko": "오너가 겪은 일처럼 쓴 문장이 있어요('{found}'). 오너는 자기 경험을 말한 적이 없으니 그 문장을 빼고 "
              "오너의 경험은 아무것도 쓰지 마세요. 대신 넣을 표시도 남기지 마세요."},

    # a person today with no message draft
    "kit.no_draft.public_first": {"en": "Answer their post in your own words first. A message comes after that.",
                                  "ko": "먼저 그 사람의 글에 직접 답을 달아 주세요. 메시지는 그다음이에요."},
    "kit.no_draft.not_written": {"en": "No draft yet.", "ko": "아직 초안이 없어요."},

    # why a person is on today's list
    "kit.today.overdue": {"en": "The next step you set with them is due.", "ko": "그 사람과 정해 둔 다음 일을 할 때가 됐어요."},
    "kit.today.new": {"en": "You have not reached out yet.", "ko": "아직 연락하지 않았어요."},

    # refusals
    "kit.error.not_found": {"en": "Business B-{id} does not exist.", "ko": "B-{id} 사업이 없어요."},
    "kit.error.person_not_found": {"en": "PPL-{id} is not on this business's list.",
                                   "ko": "PPL-{id|은} 이 사업의 명단에 없어요."},
    "kit.error.person_gone": {"en": "PPL-{id} is dropped or removed, so there is no one to write to.",
                              "ko": "PPL-{id|은} 접었거나 지운 사람이라 메시지를 쓸 수 없어요."},
    "kit.error.public_first": {"en": "PPL-{id} was found in a public post. Answer it in your own words first; the "
                                     "message comes after you have reached out.",
                               "ko": "PPL-{id|은} 공개 글에서 찾은 사람이에요. 먼저 그 글에 직접 답을 달아 주세요. "
                                     "메시지는 연락한 뒤에 써요."},
    "kit.error.no_plan": {"en": "The kit is written from the plan. Approve a plan first.",
                          "ko": "키트는 계획을 보고 써요. 먼저 계획을 승인해 주세요."},
    "kit.error.busy": {"en": "The kit is being written now. Wait for it to finish.",
                       "ko": "지금 키트를 쓰는 중이에요. 끝날 때까지 기다려 주세요."},
    "kit.error.nothing": {"en": "There is nothing to write: no one to reach out to today.",
                          "ko": "쓸 것이 없어요. 오늘 연락할 사람이 없어요."},
    "kit.error.bad_part": {"en": "Unknown part: {part}. Choose from {options}.",
                           "ko": "알 수 없는 종류예요: {part}. {options} 중에서 골라 주세요."},
    "kit.error.person_needs_message": {"en": "A person goes only with the message part.",
                                       "ko": "사람은 메시지와 함께만 고를 수 있어요."},
    "kit.field.parts": {"en": "parts", "ko": "종류"},
    "kit.field.person_id": {"en": "person", "ko": "사람"},

    # how the last write went, under "Writing..."
    "kit.job.failed": {"en": "The last write did not finish, so nothing was changed. Try again.",
                       "ko": "지난번 쓰기가 끝나지 못해서 바뀐 것은 없어요. 다시 해 주세요."},
    "kit.job.cancelled": {"en": "The last write was cancelled, so nothing was changed.",
                          "ko": "지난번 쓰기를 취소해서 바뀐 것은 없어요."},
    "kit.job.waiting": {"en": "Waiting for the {reason} until {until}. The kit is written after that.",
                        "ko": "{reason} 때문에 {until}까지 기다리는 중이에요. 그 뒤에 키트를 써요."},

    # the record
    "kit.activity.prepared": {"en": "First 10 kit for B-{id}: {written} written, {refused} not written",
                              "ko": "B-{id} 첫 10명 키트: {written}개를 썼고 {refused}개는 쓰지 못했어요"},
}
