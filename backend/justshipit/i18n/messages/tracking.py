"""Which channel brought people: the owner's own link on a post card, how a person heard about the product, and the counts
per channel (`services/tracking.py`, `services/tracking_count.py`, `api/tracking.py`). Words for the console's section
are in its own catalog; these are the words the server sends and the ones a stored record must not freeze in one
language."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    # the answer to "how did you hear about us?"
    "tracking.heard.label": {"en": "How did they hear about you?", "ko": "어디서 알게 됐대요?"},
    "tracking.heard.question": {"en": "How did you hear about us?", "ko": "저희를 어디서 알게 되셨어요?"},
    "tracking.heard.word.friend": {"en": "Someone told them", "ko": "누가 알려 줬어요"},
    "tracking.heard.word.search": {"en": "Search", "ko": "검색"},
    "tracking.heard.word.other": {"en": "Other", "ko": "기타"},
    "tracking.event.heard": {"en": "{ref} {handle}: heard from {to}", "ko": "{ref} {handle}: 알게 된 곳 {to}"},

    # rows that are not a channel
    "tracking.row.known": {"en": "People you know", "ko": "아는 사람"},
    "tracking.row.reached_out": {"en": "You reached out", "ko": "먼저 연락함"},
    "tracking.row.no_answer": {"en": "No answer yet", "ko": "아직 답 없음"},
    "tracking.row.organic": {"en": "Found you on their own", "ko": "스스로 찾아옴"},

    # what each figure rests on
    "tracking.basis.read": {"en": "read", "ko": "읽음"},
    "tracking.basis.told": {"en": "told", "ko": "들음"},
    "tracking.basis.typed": {"en": "you typed", "ko": "직접 입력"},
    "tracking.basis.at_least": {"en": "at least", "ko": "최소"},

    # the block that gives the owner a question for their own sign-up form
    "tracking.signup.title": {"en": "Ask on your sign-up form", "ko": "가입 화면에서 물어보기"},
    "tracking.signup.options": {"en": "A post (where?) / A friend or colleague / Search / Other",
                                "ko": "글을 보고(어디서?) / 지인 소개 / 검색 / 기타"},
    "tracking.signup.note": {"en": "Ask it once, make it optional, and keep a free-text Other.",
                             "ko": "한 번만 묻고, 건너뛸 수 있게 하고, 직접 쓰는 기타 칸을 두세요."},
    "tracking.tally.help": {"en": "People who are not on your list and said they came from here",
                            "ko": "명단에 없고 여기서 왔다고 말한 사람"},
    "tracking.say": {"en": "Tell us you saw this on {place}", "ko": "{place}에서 보고 왔다고 말해 주세요"},
    "tracking.too_few": {"en": "Too few people to tell channels apart yet. These are counts, not a ranking.",
                         "ko": "아직 사람이 적어서 채널끼리 비교하기 일러요. 순위가 아니라 개수예요."},
    "tracking.offer.homepage": {"en": "Your repository lists {host}. Use it?", "ko": "저장소에 적힌 사이트({host})를 쓸까요?"},

    "tracking.door.site": {"en": "Your site", "ko": "내 사이트"},
    "tracking.door.store": {"en": "Your store page", "ko": "내 가게 페이지"},
    "tracking.door.map": {"en": "Your map listing", "ko": "내 지도 등록"},
    "tracking.door.package": {"en": "Your package page", "ko": "내 패키지 페이지"},
    "tracking.door.repo": {"en": "Your repository", "ko": "내 저장소"},

    # the name of each field of a request body, for the refusal that names one that does not parse
    "tracking.field.key": {"en": "place", "ko": "장소"},
    "tracking.field.count": {"en": "count", "ko": "개수"},
    "tracking.field.day": {"en": "day", "ko": "날짜"},
    "tracking.field.doors": {"en": "places", "ko": "장소 목록"},
    "tracking.field.kind": {"en": "kind", "ko": "종류"},
    "tracking.field.url": {"en": "address", "ko": "주소"},
    "tracking.field.param": {"en": "tag name", "ko": "태그 이름"},
    "tracking.field.tag": {"en": "tag", "ko": "태그"},
    "tracking.field.visits": {"en": "visits", "ko": "방문"},
    "tracking.field.signups": {"en": "sign-ups", "ko": "가입"},

    # refusals
    "tracking.error.no_business": {"en": "There is no business B-{id}", "ko": "B-{id} 사업이 없어요"},
    "tracking.error.door_kind": {"en": "A place is one of: {options}", "ko": "장소는 {options} 중 하나예요"},
    "tracking.error.door_repo": {"en": "Your repository comes from the GitHub address of the business, so it is not added here",
                                 "ko": "저장소는 사업에 연결한 GitHub 주소에서 가져와요. 여기서는 추가하지 않아요"},
    "tracking.error.door_param": {"en": "A tag goes in one of: {options}, or nowhere", "ko": "태그는 {options} 중 하나에 달거나, 달지 않아요"},
    "tracking.error.door_many": {"en": "At most {n} places", "ko": "장소는 {n}곳까지예요"},
    "tracking.error.door_url": {"en": "That address can't be used: {problem}", "ko": "이 주소는 쓸 수 없어요. {problem}"},
    "tracking.error.door_long": {"en": "An address can be {n} characters at most", "ko": "주소는 {n}자까지예요"},
    "tracking.error.door_contact": {"en": "An address with an email address or a phone number in it is refused",
                                    "ko": "이메일 주소나 전화번호가 들어 있는 주소는 받지 않아요"},
    "tracking.error.heard_unknown": {"en": "That is not a place people hear about you from. Use one of: {options}",
                                     "ko": "알게 된 곳으로 받을 수 없는 값이에요. {options} 중에서 골라 주세요"},
    "tracking.error.heard_words_long": {"en": "Their words can be {n} characters at most", "ko": "말한 내용은 {n}자까지예요"},
    "tracking.error.count": {"en": "A count is a whole number from 0 to {n}", "ko": "개수는 0에서 {n} 사이의 정수예요"},
    "tracking.error.tally_day": {"en": "That count is for a day that has passed. Reload the page and count again",
                                 "ko": "지난 날짜의 개수예요. 화면을 새로 고친 뒤 다시 세어 주세요"},
    "tracking.error.tag_unknown": {"en": "That tag does not belong to one of this business's posts",
                                   "ko": "이 사업의 글에 붙은 태그가 아니에요"},
    "tracking.error.tag_counts": {"en": "Give the visits, the sign-ups or both", "ko": "방문, 가입 중 하나나 둘 다 알려 주세요"},
}
