"""The words the First 10 kit's text checks read (`services/kit.py`).

`ADDRESSED_TO_MANY` is a greeting that speaks to a group ("Hi everyone", "여러분"): a message is for one person, so a
text that opens that way is refused whoever else it names. "talk to friends of yours" is not a greeting, so only `to`
with a word that means everybody counts.

`MESSENGERS` names an app whose id is a contact and `ID_WORDS` the word for that id. The shared contact check
(`people.contact_info_problem`) needs a `:` or `=` between the two; the kit also refuses "Ping me on Telegram @owner_jk"
and "카톡 아이디 ownerjk", where the owner's own word order has no separator.

`PASS_ON` asks the person to pass the text on to several people ("share it with your friends", "친구들에게 공유"). An
invite's normal "Know someone who needs this?" asks about one person and is not here.

`IN_THEIR_POST` and `WITH_THE_OWNER` tell a sentence about what the person wrote ("글에서 이야기했던 송장 문제") from one
about a talk the two of them had ("우리가 전에 이야기했던"). `COMMON_WORDS` are words an ordinary text holds that a
person's handle or one-word name could also be, so a bare match of one is no sign the text names that person.
"""
from justshipit.i18n import Lang

ADDRESSED_TO_MANY: dict[Lang, tuple[str, ...]] = {
    "en": (r"(?<![A-Za-z])(?:hi|hello|hey|dear)\s+(?:all|everyone|everybody|folks|team|friends|both)(?![A-Za-z])",
           r"(?<![A-Za-z])to\s+(?:all|everyone|everybody|folks|both)(?![A-Za-z])",
           r"(?<![A-Za-z])(?:you\s+all|you\s+both|y'all)(?![A-Za-z])"),
    "ko": (r"여러분", r"(?:모두|다들)\s*(?:님|분들|안녕)", r"두\s*분"),
}

MESSENGERS: dict[Lang, tuple[str, ...]] = {
    "en": (r"kakao(?:talk)?", "telegram", "wechat", "whatsapp", "discord", "instagram", "insta", "skype", "viber",
           "snapchat", "signal"),
    "ko": ("카톡", "카카오톡?", "텔레그램", "위챗", "왓츠앱", "디스코드", "인스타(?:그램)?", "스카이프", "시그널"),
}
ID_WORDS: dict[Lang, tuple[str, ...]] = {
    "en": ("id", "handle", "username", "account"),
    "ko": ("아이디", "계정", "핸들", "닉네임"),
}

PASS_ON: dict[Lang, tuple[str, ...]] = {
    "en": (r"(?<![A-Za-z])(?:share|forward|pass|send|spread|recommend|tell|show|post)\s+(?:(?:it|this|that|these|the\s+"
           r"(?:link|word|tool|product))\s+)?(?:(?:with|to|on\s+to|along\s+to)\s+)?(?:all\s+)?(?:of\s+)?your\s+(?:friends|"
           r"colleagues|coworkers|co-workers|teammates|team|followers|contacts|network|community|family)(?![A-Za-z])",
           r"(?<![A-Za-z])(?:share|forward|pass|spread|tell)\b[^.!?\n]{0,25}?\b(?:everyone|everybody|people)\s+you\s+"
           r"know(?![A-Za-z])",
           r"(?<![A-Za-z])(?:pass|spread)\s+(?:it|this|the\s+word)\s+(?:on|around|along)(?![A-Za-z])"),
    "ko": (r"(?:친구|지인|동료|팀원|가족|사람)(?:들|분들)(?:에게|한테|께)?\s*(?:도\s*)?(?:공유|전달|소개|알려|추천)",
           r"주변\s*(?:분들|사람들)(?:에게|한테|께)?\s*(?:도\s*)?(?:공유|전달|소개|알려|추천)"),
}

IN_THEIR_POST: dict[Lang, tuple[str, ...]] = {
    "en": (),
    "ko": (r"(?:글|게시글|게시물|포스트|포스팅|댓글|후기|답글|스레드|질문)\s*(?:에서|에|속)", r"(?:쓰신|올리신|적으신|남기신)"),
}
WITH_THE_OWNER: dict[Lang, tuple[str, ...]] = {
    "en": (),
    "ko": (r"우리|저희|저와|저랑|함께|같이|통화|미팅|만나|뵀|뵙",),
}

COMMON_WORDS = frozenset("""
able about after again also back been best bill blue both bright build call care code coder cafe chat cloud data dawn
demo done each easy even ever fast find fire first fish flow food four free from frank full game gift give goes gold good
grace grant great green hand hard have help here home hope hunter idea into just keep kind know last late life light like
line list live long look love made main make many mark mind more most move much must name need news next nice note
only open over page paid part past plan play plus post pure quick rain read real rich rise river rose safe same save
shop show side sign slow smart snow some soon star start stay stone store storm summer sunny take talk team test than
that them then they this thus time tool true turn upon used user very want warm wave well what when will wind wish with
word work year your zero
""".split())
