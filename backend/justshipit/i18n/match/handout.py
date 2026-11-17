"""The words the author checks read (`services/owner_tasks.author_problems`): a card line that asks the owner to fix
the company's own text, a message whose sentences end in two registers, a message that leaves the owner a blank to fill,
a message that switches between speaking as one person and as a company, and a message that claims an earlier
talk with the person.

`OWNER_FIX` is a fix of one concrete value to another ("19:30을 20:00으로 고친 뒤", "change 19:30 to 20:00"): the
target holds a digit, so filling in what only the owner knows ("가게 주소로 바꿔요") is not one, and a past tense
("고쳤어요") reports a fix the company made. It counts only on a line that names the company's text (`COMPANY_TEXT`):
"가격을 9,900원으로 바꿔요" is the owner's own decision. `FORMAL` and `POLITE` are sentence endings; `FIXED` are set phrases a
message in either register uses ("안녕하세요", "감사합니다"), which say nothing about its register. English has none.
"""
from justshipit.i18n import Lang

OWNER_FIX: dict[Lang, tuple[str, ...]] = {
    "ko": (r"[0-9][^\s]*\s*(?:으로|로)\s*(?:고치|고쳐|바꾸|바꿔|맞추|맞춰|수정하|수정해"
           r"|(?:고친|바꾼|맞춘|수정한)\s*(?:뒤|다음|후))",),
    "en": (r"(?<![A-Za-z])(?:change|fix|correct|update|edit|replace)(?![A-Za-z])[^.;]{0,60}?(?<![A-Za-z])(?:to|with)\s+"
           r"\$?[0-9]",),
}
COMPANY_TEXT: dict[Lang, tuple[str, ...]] = {
    "ko": (r"문구", r"답장", r"문안", r"초안", r"본문", r"메시지", r"안내문", r"게시글", r"글에", r"글의"),
    "en": (r"(?<![A-Za-z])(?:text|copy|draft|reply|message|caption|wording)(?![A-Za-z])",),
}
FORMAL: dict[Lang, tuple[str, ...]] = {"ko": (r"(?:니다|니까)$",), "en": ()}
POLITE: dict[Lang, tuple[str, ...]] = {"ko": (r"요$",), "en": ()}
FIXED: dict[Lang, tuple[str, ...]] = {
    "ko": ("안녕하세요", "안녕하십니까", "감사합니다", "감사드립니다", "고맙습니다", "반갑습니다", "부탁드립니다",
           "죄송합니다", "축하드립니다", "수고하세요", "수고하십시오"),
    "en": (),
}
# A blank the owner would fill before sending (`owner_tasks.without_blanks`): the sender's own name and contact, and
# what the company did not know ("[hosting company]", "[region]"). The owner sends from their own account, so who it
# is from is on it already. A blank for the person it goes to is not one: "[Name]" in a greeting, or "[your shop email
# address]" in steps they follow. So a name, e-mail or phone blank counts only when it says whose it is.
OWNER_BLANK: dict[Lang, tuple[str, ...]] = {
    "en": (r"\[\s*(?:(?:your|my|owner'?s|sender'?s)\s+(?:full\s+|first\s+)?(?:name|e-?mail(?:\s+address)?|phone"
           r"(?:\s+number)?)|(?:(?:your|my)\s+)?(?:signature|company\s+name|business\s+name))\s*\]",
           r"\[\s*(?:hosting\s+(?:company|provider)|host|region|data\s+region)\s*\]"),
    "ko": (r"\[\s*(?:(?:내|제|오너|사장님?|본인|보내는\s*사람)\s*(?:이름|성함|연락처|이메일|메일\s*주소|전화\s*번호|휴대폰\s*번호)"
           r"|(?:(?:내|제)\s*)?(?:서명|회사\s*이름|가게\s*이름|상호))\s*\]",
           r"\[\s*(?:호스팅\s*(?:회사|업체)|서버\s*지역|지역)\s*\]"),
}
# Who speaks in a message: one person (I, me, my) or a company (we, our). "Let us know" is left out: it is the reader's
# side. A message that speaks each way twice or more switches voice. Korean has none: 저희 is how one speaks of one's
# own shop (저희 가게), so a count cannot tell a switch.
ONE_PERSON: dict[Lang, tuple[str, ...]] = {
    "en": (r"(?<![\w'’])(?:I|[Mm]e|[Mm]y|[Mm]ine|[Mm]yself)(?:['’](?:m|ll|ve|d))?(?![\w'’])",),
    "ko": (),
}
COMPANY_VOICE: dict[Lang, tuple[str, ...]] = {
    "en": (r"(?<![\w'’])(?:[Ww]e|[Oo]ur|[Oo]urs|[Oo]urselves)(?:['’](?:re|ll|ve|d))?(?![\w'’])",),
    "ko": (),
}
# A message that says the owner and the person talked about this before ("전에 얘기했던 창고 소개 건으로", "as we
# discussed"). The company cannot know that, so the author keeps it only when the owner's words say it happened. What
# the person said ("지난주에 말씀하신") is left out: the plan quotes it from the owner when it is there. So are a talk
# still to come ("이야기 나눈 뒤에"), "before" ("주문하기 전에 말씀해 주세요") and the message itself ("위에 말한 대로",
# "as I mentioned above").
PAST_TALK: dict[Lang, tuple[str, ...]] = {
    "ko": (r"(?:얘기|이야기|말)\s*(?:했던|했었던|나눴던|나누었던)",
           r"말씀\s*(?:드렸던|나눴던|나누었던)",
           r"(?<!기)(?<!기\s)(?:전에|지난번에?|예전에|저번에)\s*(?:얘기|이야기|말씀)\s*(?:했|드렸|나눴|나누었)"),
    "en": (r"(?<![A-Za-z])as\s+(?:we|I)\s+(?:discussed|talked\s+about|spoke\s+about|agreed)",
           r"(?<![A-Za-z])(?:following|follow)\s+up\s+on\s+(?:our|my)\s+(?:conversation|chat|call|talk|discussion"
           r"|meeting)",
           r"(?<![A-Za-z])per\s+our\s+(?:conversation|chat|call|talk|discussion)",
           r"(?<![A-Za-z])(?:when|since|last\s+time)\s+we\s+(?:last\s+)?(?:talked|spoke|chatted|met)",
           r"(?<![A-Za-z])we\s+(?:talked|spoke|chatted)\s+(?:about\s+(?:this|it)|earlier|before|last)"),
}
