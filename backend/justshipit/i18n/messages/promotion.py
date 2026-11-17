"""The weekly promotion, the launch gate and the growth routes (`services/promotion.py`, `services/launch_gate.py`,
`api/growth.py`)."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "promotion.gate.demo": {"en": "A recorded demo", "ko": "녹화한 데모"},
    "promotion.gate.outside_install_3": {"en": "3 outsiders installed it on a fresh machine, timed",
                                         "ko": "외부인 3명이 새 PC에 설치 성공(걸린 시간 기록)"},
    "promotion.gate.readme_checked": {"en": "README first screen checked", "ko": "README 첫 화면 점검"},
    "promotion.gate.first_value": {"en": {"one": "{n} person who got value (People list)",
                                          "other": "{n} people who got value (People list)"},
                                   "ko": "도움 됨 단계까지 온 사람 {n}명(명단 기준)"},
    "promotion.gate.not_owner": {"en": "That item isn't yours to confirm. You can confirm: {items}",
                                 "ko": "오너님이 확인하는 항목이 아니에요. 확인할 수 있는 항목: {items}"},

    "promotion.skip.rules_unchecked": {"en": "Its rules were not read, so the company writes nothing for it",
                                       "ko": "이 채널의 규칙을 읽지 못해서 회사가 글을 쓰지 않아요"},
    "promotion.skip.dropped": {"en": "An experiment review stopped using this channel",
                               "ko": "실험 회고에서 그만 쓰기로 한 채널이에요"},
    "promotion.skip.gate": {"en": "Still to do first: {items}", "ko": "먼저 끝낼 일이 남았어요: {items}"},
    "promotion.skip.waiting": {"en": "A post for this channel is already in your to-dos",
                               "ko": "이 채널에 올릴 글이 이미 내 할 일에 있어요"},
    "promotion.skip.interval": {"en": {"one": "It hasn't been {n} day since the last post here",
                                       "other": "It hasn't been {n} days since the last post here"},
                                "ko": "올린 지 아직 {n}일이 안 지났어요"},
    "promotion.skip.no_material": {"en": "Nothing new to say here yet", "ko": "이 채널에 아직 안 쓴 새 소식이 없어요"},
    "promotion.skip.cap": {"en": "This week's post limit is reached", "ko": "이번 주 올릴 글 상한에 닿았어요"},
    "promotion.skip.no_draft": {"en": "The marketing agent wrote no post for this channel",
                                "ko": "마케팅 에이전트가 이 채널 글을 쓰지 않았어요"},
    "promotion.skip.refused": {"en": "The post failed the checks ({rule})", "ko": "글이 검사를 통과하지 못했어요({rule})"},
    # `lines` is the profile.line.* label of the line nobody knows yet, lower-cased in English.
    "promotion.skip.profile_unknown": {"en": "Needs to know: {lines}", "ko": "{lines|을} 알아야 해요"},
    # Every launch channel declares how its people are reached, and core.launch_ready also needs who buys.
    "promotion.skip.launch_guess": {"en": "A one-time launch waits until you confirm who buys and how they're reached",
                                    "ko": "한 번뿐인 출시라 누가 사는지와 어떻게 닿는지 확인한 뒤에 올려요"},

    "promotion.note.no_brief": {"en": "The business is gone or archived, or has no approved plan",
                                "ko": "사업이 없거나 보관했거나, 승인한 계획이 없어요"},
    "promotion.note.capped": {"en": {"one": "This week already has {n} post, the limit of {cap}",
                                     "other": "This week already has {n} posts, the limit of {cap}"},
                              "ko": "이번 주에 올릴 글 {n}개를 만들어 상한({cap}개)에 닿았어요"},
    "promotion.note.no_material": {"en": "Nothing new happened, so there are no posts this week",
                                   "ko": "새 소식이 없어 이번 주에는 올릴 글을 만들지 않았어요"},
    "promotion.note.none_passed": {"en": "No post passed the checks, even after a rewrite, so there are no posts this week",
                                   "ko": "다시 써도 검사를 통과한 글이 없어 이번 주에는 올릴 글을 만들지 않았어요"},
    "promotion.note.held": {"en": {"one": "{n} post was ready, but no stage you started lists post to-dos, so it was not added",
                                   "other": "{n} posts were ready, but no stage you started lists post to-dos, so they were not added"},
                            "ko": "올릴 글 {n}개를 준비했지만 오너님이 시작한 단계에 글 올리기 할 일이 없어 내 할 일에 넣지 않았어요"},
    "promotion.note.no_channel": {"en": "No channel can take another post yet, so there are no posts this week",
                                  "ko": "다시 올릴 수 있는 채널이 없어 이번 주에는 올릴 글을 만들지 않았어요"},

    "promotion.note.no_writable": {
        "en": "No place that fits this business has rules the company has read, so there are no posts this week. You "
              "can still post by hand after reading a place's rules yourself",
        "ko": "이 사업에 맞는 채널 가운데 회사가 규칙을 읽은 곳이 없어서 이번 주에는 올릴 글을 만들지 않았어요. 채널의 규칙을 "
              "직접 읽은 뒤 오너님이 직접 올릴 수는 있어요"},
    "promotion.activity.unchecked": {"en": "Left out because their rules were not read: {places}.",
                                     "ko": "규칙을 읽지 못해 뺀 채널: {places}."},
    "promotion.activity.week": {"en": "B-{id} {week} weekly promotion: {note}", "ko": "B-{id} {week} 주간 홍보: {note}"},
    "promotion.activity.made": {"en": {"one": "B-{id} {week} weekly promotion: prepared {n} post",
                                       "other": "B-{id} {week} weekly promotion: prepared {n} posts"},
                                "ko": "B-{id} {week} 주간 홍보: 올릴 글 {n}개를 준비했어요"},
    "promotion.activity.gate_confirmed": {"en": "B-{id} launch check confirmed: {item}",
                                          "ko": "B-{id} 출시 전 확인: {item}"},
    # The to-do's badge already says Post, and some channel names read badly after a verb ("GitHub release").
    "promotion.task.title": {"en": "{channel}: {headline}", "ko": "{channel}에 올리기: {headline}"},

    "promotion.drop.no_posts": {"en": "Nothing was posted there yet, so there is nothing to judge",
                                "ko": "올린 글이 없어 판단할 수 없어요"},
    "promotion.drop.too_soon": {"en": "Posts with new material: {fresh}, weeks since the first: {weeks}. "
                                      "Judge after 3 posts or 4 weeks",
                                "ko": "새 소식으로 올린 글 {fresh}개, 첫 글 뒤 {weeks}주예요. 3개를 올리거나 4주가 "
                                      "지나야 판단해요"},
    "promotion.drop.no_value_numbers": {"en": "No Got value numbers yet. Stars and visits alone are no reason to stop",
                                        "ko": "도움 됨 숫자가 없어 판단할 수 없어요. 별과 방문만 보고 그만두지는 않아요"},
    "promotion.drop.ok": {"en": "Posts with new material: {fresh}, weeks since the first: {weeks}. Judged on the Got "
                                "value numbers",
                          "ko": "새 소식으로 올린 글 {fresh}개, 첫 글 뒤 {weeks}주. 도움 됨 숫자로 판단했어요"},

    "promotion.not_found": {"en": "B-{id} not found", "ko": "B-{id|를} 찾지 못했어요"},
    "promotion.bet_not_found": {"en": "BET-{id} not found", "ko": "BET-{id|를} 찾지 못했어요"},
}
