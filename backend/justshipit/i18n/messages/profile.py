"""The customer description (`services/profile`): code labels, the sentence each line is composed into, activity words,
refusals, and the words of the block agents read. Labels live here only, so the vocabulary has one place to grow."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "profile.line.buyer": {"en": "Who buys", "ko": "누가 사나"},
    "profile.line.reach": {"en": "How they're reached", "ko": "어떻게 닿나"},
    "profile.line.has": {"en": "What you already have", "ko": "이미 가진 것"},

    # `{list}` is the code labels and the line's own sentence joined with "; ", and nothing in a sentence depends on
    # how it ends, so no Korean particle follows it.
    "profile.compose.buyer": {"en": "Who buys: {list}", "ko": "누가 사나: {list}"},
    "profile.compose.reach": {"en": "How they're reached: {list}", "ko": "어떻게 닿나: {list}"},
    "profile.compose.has": {"en": "What you already have: {list}", "ko": "이미 가진 것: {list}"},

    "profile.code.buyer.consumers": {"en": "People buying for themselves", "ko": "자기 돈으로 사는 개인"},
    "profile.code.buyer.small_business": {"en": "Small businesses and shops", "ko": "작은 가게와 회사"},
    "profile.code.buyer.professionals": {"en": "Professionals (doctors, lawyers, designers, tutors)",
                                         "ko": "전문직 (의사, 변호사, 디자이너, 강사)"},
    "profile.code.buyer.companies": {"en": "Teams inside larger companies", "ko": "회사의 팀과 담당자"},
    "profile.code.buyer.developers": {"en": "Developers", "ko": "개발자"},
    "profile.code.reach.search": {"en": "They search for it (web, maps, app stores)",
                                  "ko": "검색해서 찾아요 (웹, 지도, 앱 스토어)"},
    "profile.code.reach.communities": {"en": "Online communities where they gather", "ko": "모여 있는 온라인 커뮤니티"},
    "profile.code.reach.introductions": {"en": "Introductions and word of mouth", "ko": "소개와 입소문"},
    "profile.code.reach.local": {"en": "In person, nearby", "ko": "가까운 곳에서 직접 만나요"},
    "profile.code.reach.direct": {"en": "Messages, calls or email to people you choose",
                                  "ko": "고른 사람에게 보내는 메시지, 전화, 메일"},
    "profile.code.reach.short_video": {"en": "Short videos and creators", "ko": "짧은 영상과 크리에이터"},
    "profile.code.has.network": {"en": "People you know who could buy or introduce you",
                                 "ko": "사거나 소개해 줄 아는 사람"},
    "profile.code.has.customers": {"en": "Customers you already have", "ko": "이미 있는 고객"},
    "profile.code.has.audience": {"en": "Followers or subscribers", "ko": "팔로워나 구독자"},
    "profile.code.has.registration": {"en": "Registered to sell (a company or sole trader)", "ko": "사업자 등록"},
    "profile.code.has.place": {"en": "A shop or place customers can visit", "ko": "손님이 올 수 있는 가게나 장소"},
    "profile.code.has.budget": {"en": "Money for ads", "ko": "광고에 쓸 돈"},
    "profile.code.has.time": {"en": "Hours each week for this", "ko": "매주 쓸 수 있는 시간"},
    "profile.code.has.face": {"en": "Willing to show your face (video, events)", "ko": "얼굴을 드러낼 마음 (영상, 행사)"},

    # The activity row for each cause of a change. `{line}` is a profile.line label, always followed by a fixed word.
    "profile.activity.kickoff_answer": {
        "en": "The Project Manager updated the customer description from your answer: {line}",
        "ko": "프로젝트 매니저가 오너님의 답을 보고 고객 설명의 {line} 항목을 채웠어요"},
    "profile.activity.plan": {
        "en": "The Project Manager wrote the customer description from your plan: {line}",
        "ko": "프로젝트 매니저가 계획을 보고 고객 설명의 {line} 항목을 썼어요"},
    "profile.activity.plan_changed": {
        "en": "The Project Manager updated the customer description after your plan changed: {line}",
        "ko": "계획이 바뀌어서 프로젝트 매니저가 고객 설명의 {line} 항목을 고쳤어요"},
    "profile.activity.backfill": {
        "en": "The Project Manager filled in the customer description from your plan: {line}",
        "ko": "프로젝트 매니저가 계획을 보고 고객 설명의 {line} 항목을 짐작으로 채웠어요"},
    "profile.activity.owner_right": {
        "en": "You confirmed the customer description: {line}",
        "ko": "고객 설명에서 {line} 항목을 맞다고 확인했어요"},
    "profile.activity.owner_not_right": {
        "en": "You marked the customer description not right: {line}",
        "ko": "고객 설명에서 {line} 항목이 맞지 않다고 표시했어요"},
    "profile.activity.owner_change": {
        "en": "You changed the customer description: {line}",
        "ko": "고객 설명에서 {line} 항목을 고쳤어요"},
    "profile.activity.owner_undo": {
        "en": "You undid a change to the customer description: {line}",
        "ko": "고객 설명에서 {line} 항목을 이전 내용으로 되돌렸어요"},

    "profile.error.stale_write": {
        "en": "The customer description changed in the meantime. Check the new values and try again.",
        "ko": "그 사이 고객 설명이 바뀌었어요. 새 내용을 확인하고 다시 시도해 주세요."},
    "profile.error.owner_value_held": {
        "en": "You said or confirmed this yourself, so the company won't overwrite it. If it's wrong, change it "
              "yourself.",
        "ko": "오너님이 직접 말하거나 확인한 내용이라 회사가 덮어쓰지 않아요. 틀렸다면 직접 고쳐 주세요."},
    "profile.error.bad_value": {
        "en": "That can't be saved. Pick from the list, or write one short sentence.",
        "ko": "이 내용은 저장할 수 없어요. 목록에서 고르거나 짧은 문장 하나로 적어 주세요."},
    "profile.error.bad_code": {
        "en": "One of those choices isn't available. Pick from the list.",
        "ko": "고른 항목 중 쓸 수 없는 것이 있어요. 목록에서 골라 주세요."},
    "profile.error.not_undoable": {
        "en": "That change can't be undone from here.",
        "ko": "이 변경은 여기서 되돌릴 수 없어요."},
    "profile.error.answer_too_long": {
        "en": "That answer is too long. Keep it under {max} characters.",
        "ko": "답이 너무 길어요. {max}자 안으로 줄여 주세요."},

    "profile.error.no_business": {"en": "B-{id} not found.", "ko": "B-{id} 사업을 찾지 못했어요."},
    "profile.error.bad_line": {"en": "The customer description has no such line.",
                               "ko": "고객 설명에 그런 항목은 없어요."},
    "profile.error.bad_action": {"en": "That isn't something you can do here.", "ko": "여기서 할 수 없는 동작이에요."},
    "profile.error.no_answer": {"en": "Write your answer first.", "ko": "먼저 답을 적어 주세요."},
    "profile.error.nothing_picked": {"en": "Pick at least one choice, or write a sentence.",
                                     "ko": "항목을 하나 이상 고르거나 문장을 적어 주세요."},

    # What an agent reads (`profile.prompt_block`).
    "profile.prompt.label": {"en": "Customer description", "ko": "고객 설명"},
    "profile.prompt.unknown": {"en": "not known yet. Do not assume it.", "ko": "아직 몰라요. 짐작해서 쓰지 마세요."},
}
