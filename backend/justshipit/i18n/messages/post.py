"""Post cards for places the owner posts by hand (`services/owner_tasks.py`, `services/channels.py`,
`services/copy_rules.py`): why the server refuses a card, a link or a draft, and the fixed line every card's
checklist ends with."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "post.fixed_check": {"en": "Check that nothing asks for votes, stars or reviews, or offers a reward for them",
                         "ko": "투표·별·리뷰를 부탁하거나 보상을 거는 문구가 없는지 확인"},
    "post.placeholder": {"en": "[My experience: write it yourself]", "ko": "[내 경험: 직접 쓰기]"},

    "post.refuse.unknown_channel": {"en": "Unknown channel: {channel}", "ko": "모르는 채널이에요: {channel}"},
    "post.refuse.launch_gate": {"en": "You can't post on {channel} yet. First finish: {missing}",
                                "ko": "{channel}에는 아직 올릴 수 없어요. 먼저 끝낼 일이 남았어요: {missing}"},
    "post.refuse.rules_unchecked": {
        "en": "The company writes nothing for {channel}, because its rules were not read: {why} Pick a place whose rules "
              "were checked, or post there yourself after reading its rules.",
        "ko": "{channel}에는 회사가 글을 쓰지 않아요. 이 채널의 규칙을 읽지 못했어요. {why} 규칙을 확인한 채널을 고르거나, "
              "규칙을 직접 읽은 뒤 오너님이 직접 올려 주세요."},
    "post.refuse.own_place": {
        "en": "{channel} is for the owner's own pages, at an address under one of your saved places (Your places). This "
              "address is not, and its rules were not read. Save the address as your own place first, use the place's own "
              "channel, or leave the address out.",
        "ko": "{channel}: 저장해 둔 내 장소 안의 주소에 올릴 때만 쓰는 채널이에요. 이 주소는 그렇지 않고, 그 규칙은 읽지 못했어요. "
              "먼저 이 주소를 내 장소로 저장하거나, 그 사이트의 채널을 고르거나, 주소를 빼 주세요."},
    "post.refuse.landing_place": {
        "en": "A landing page goes to the owner's own place. This address is not under one of your saved places (Your "
              "places) or on a static-hosting address ({hosts}). Save the address as your own place first, then ask "
              "again, or leave the address out.",
        "ko": "랜딩 페이지는 오너님의 내 장소에 올려요. 이 주소는 저장해 둔 내 장소 안에 있지 않고 정적 호스팅 주소({hosts})도 아니에요. "
              "먼저 이 주소를 내 장소로 저장한 뒤 다시 요청하거나, 주소를 빼 주세요."},
    "post.refuse.names_place": {
        "en": "This card is about {channel}, so it can't go under the catch-all channel, which would skip that place's "
              "mode and checks. File it under `{place_key}` instead.",
        "ko": "이 카드는 {channel}에 올리는 글이라 그 밖의 채널에 둘 수 없어요. 그러면 그 채널의 방식과 점검을 건너뛰게 돼요. "
              "채널을 `{place_key}`로 골라 다시 요청해 주세요."},
    "post.rules.not_read": {"en": "A page with its rules exists, but it could not be read.",
                            "ko": "규칙을 적은 페이지가 있지만 읽지 못했어요."},
    "post.rules.per_unit": {"en": "Each server, list, newsletter or subreddit has its own rules, so there is no single text to read.",
                            "ko": "서버, 목록, 뉴스레터, 서브레딧마다 규칙이 따로 있어서 하나로 읽을 수 있는 규칙이 없어요."},
    "post.rules.no_source": {"en": "No page with its rules was found.", "ko": "규칙을 적은 페이지를 찾지 못했어요."},
    # On an open card made before the place's rules were checked (`owner_tasks.with_named_channel`).
    "post.rules.written_before": {
        "en": "Written before the rules check. Read the place's rules yourself before you post.",
        "ko": "규칙을 확인하기 전에 쓴 카드예요. 올리기 전에 그 채널의 규칙을 직접 읽어 주세요."},
    # An open card for a place that now asks you to write the post yourself, written by the company before that was known.
    "post.rules.written_before_self": {
        "en": "This place's rules ask you to write the post yourself. The company wrote this text before that was known, "
              "so write your own.",
        "ko": "이 채널의 규칙은 글을 직접 쓰라고 해요. 이 글은 그 사실을 알기 전에 회사가 쓴 거예요. 오너님이 직접 써 주세요."},
    "post.refuse.where_url": {"en": "The address to post at can't be used. {problem}",
                              "ko": "올릴 곳 주소를 쓸 수 없어요. {problem}"},
    "post.refuse.outline_empty": {
        "en": "{channel} takes no sentences, so the card needs at least one fact, expected question or checklist item.",
        "ko": "{channel} 채널에는 문장을 쓰지 않아요. 꼭 넣을 사실, 받을 질문, 점검표 중 하나는 있어야 해요."},
    "post.refuse.outline_item": {
        "en": "{channel} takes no sentences, so each {where} item must be a short point: {reason} ('{item}…')",
        "ko": "{channel} 채널에는 문장을 쓰지 않아요. {where} 항목은 짧은 요점이어야 해요: {reason} ('{item}…')"},
    "post.refuse.draft_empty": {"en": "A {channel} post card needs a draft title and body.",
                                "ko": "{channel} 게시 카드에는 제목과 본문 초안이 있어야 해요."},
    "post.refuse.draft": {"en": "The {channel} draft can't be used. {problem}", "ko": "{channel} 초안을 쓸 수 없어요. {problem}"},
    "post.refuse.card_missing": {
        "en": "A post you make yourself needs a post card (post_card). Ask again with where, what, when and why to post.",
        "ko": "오너님이 직접 올리는 일에는 게시 카드(post_card)가 있어야 해요. 어디에, 무엇을, 언제, 왜 올릴지 적어서 "
              "다시 요청해 주세요."},
    "post.item.fact": {"en": "fact", "ko": "사실"},
    "post.item.checklist": {"en": "checklist", "ko": "점검표"},
    "post.item.question": {"en": "question", "ko": "질문"},
    "post.item.talking_point": {"en": "talking point", "ko": "답할 요점"},
    "post.point.too_long": {"en": "longer than {n} characters", "ko": "{n}자를 넘어요"},
    "post.point.many_sentences": {"en": "more than one sentence", "ko": "한 문장을 넘어요"},
    "post.point.headline": {"en": "written like a headline", "ko": "제목처럼 썼어요"},

    "post.copy.invented": {
        "en": "A sentence reads as if you lived it ('{found}'). You write your own experience, so leave that spot as "
              "{placeholder}.",
        "ko": "오너님이 겪은 일처럼 쓴 문장이 있어요('{found}'). 경험은 오너님이 직접 쓰니, 그 자리에는 "
              "{placeholder} 표시를 남겨 주세요."},
    "post.copy.internal": {
        "en": "The post holds a note the company left for itself: {found}. A post says only what its reader needs.",
        "ko": "올릴 글에 회사 안에서 쓰는 메모가 섞여 있어요: {found}. 올릴 글에는 읽는 사람에게 하는 말만 써요."},
    "post.internal.found": {"en": "{what} ('{excerpt}')", "ko": "{what}('{excerpt}')"},
    "post.internal.evidence_id": {"en": "an evidence id", "ko": "근거 번호"},
    "post.internal.plan_line_id": {"en": "a plan line id", "ko": "계획 줄 id"},
    "post.internal.company_id": {"en": "a company id", "ko": "회사 안의 번호"},
    "post.internal.company_word": {"en": "the company's own word", "ko": "회사 안에서 쓰는 말"},
    "post.internal.draft_note": {"en": "a draft note", "ko": "초안 메모"},
    "post.internal.link_note": {"en": "a link note", "ko": "링크 메모"},
    "post.internal.todo_note": {"en": "a to-do note", "ko": "할 일 메모"},

    "post.ask.votes": {"en": "asking for votes or upvotes", "ko": "투표·업보트 부탁"},
    "post.ask.stars": {"en": "asking for stars", "ko": "별 부탁"},
    "post.ask.reviews": {"en": "asking for reviews or ratings", "ko": "리뷰·별점 부탁"},
    "post.ask.rewards": {"en": "offering a reward for votes, stars or reviews", "ko": "투표·별·리뷰의 대가 제안"},
    "post.ask.found": {"en": "{ask} ('{clause}')", "ko": "{ask} ('{clause}')"},
    "post.ask.refused": {
        "en": "No channel allows asking for votes, stars or reviews, or paying for them: {found}",
        "ko": "투표·별·리뷰를 부탁하거나 그 대가를 주는 일은 어느 채널에서도 하지 않아요: {found}"},

    "post.url.malformed": {"en": "The address isn't a valid link.", "ko": "주소 형식이 올바르지 않아요."},
    "post.url.scheme": {"en": "A link starts with http:// or https://.", "ko": "링크는 http:// 또는 https://로 시작해야 해요."},
    "post.url.characters": {"en": "An address can't hold a space, @, \\ or %.", "ko": "주소에는 공백, @, \\, %를 쓸 수 없어요."},
    "post.url.domain": {"en": "The address's domain name isn't valid.", "ko": "주소의 도메인 이름이 올바르지 않아요."},
    "post.url.other_site": {"en": "That isn't a {channel} address. Paste a {domains} address.",
                            "ko": "{channel} 주소가 아니에요. {domains} 주소를 붙여 주세요."},
    "post.url.wrong_channel": {"en": "That's a {channel} address. Pick {channel} as the channel.",
                               "ko": "{channel} 주소예요. 채널 칸에서 {channel} 채널을 골라 주세요."},
    "post.url.not_post": {"en": "That isn't the post's own page. Paste the address of the post itself, not a list or "
                                "submit page: a {channel} post looks like {example}.",
                          "ko": "올린 글의 주소가 아니에요. 목록이나 제출 페이지가 아니라 올린 글을 연 주소를 붙여 주세요. "
                                "{channel} 글 주소는 {example} 모양이에요."},
    "post.url.too_long": {"en": "The address to post at can be up to {n} characters.",
                          "ko": "올릴 곳 주소는 {n}자까지 쓸 수 있어요."},
    "post.url.prefilled": {"en": "The address fills in the post ({params}). You write the post yourself.",
                           "ko": "주소에 미리 채운 글({params})이 들어 있어요. 글은 오너님이 직접 써요."},
}
