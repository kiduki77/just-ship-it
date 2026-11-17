"""The words of the asks no channel allows (begging for votes, stars or reviews, or paying for them), per language
(`services/channels.py`). Every card is read in every language at once: a post can mix them.

`ASKS` holds each ask's patterns, keyed by the ask's id; `{particle}` and `{words}` in a Korean pattern stand for the
particles after a noun and the few words between the noun and its verb. `THING` and `REWARD` are the things asked
for and the rewards offered, joined across languages into the rewards ask. `NEGATED_TAIL` is what, said right after
an ask, takes it back ("부탁하지 않기", "is not allowed"), and `CONTRAST` the words that undo a negation before it.
"""
from justshipit.i18n import Lang

_PARTICLE = r"(?:을|를|은|는|도|만|좀|이라도)?"
_STAR_KO = r"(?:star|스타(?!트|일|벅|디|크|킹)|(?<![가-힣])별(?=(?:을|를|은|는|도|만|좀|이라도)?(?![가-힣])))"
_VOTE_VERB_KO = r"(?:해\s*주|해주|눌러|누르|찍어|부탁|주세요|주시|바랍)"

ASKS: dict[Lang, dict[str, tuple[str, ...]]] = {
    "ko": {
        "votes": (r"업보트", rf"(?:투표|추천|좋아요){_PARTICLE}{{words}}{_VOTE_VERB_KO}"),
        "stars": (rf"{_STAR_KO}{_PARTICLE}{{words}}(?:눌러|누르|찍어|박아|꾹|클릭|부탁|주세요|주시|달아|남겨)",),
        "reviews": (rf"(?:리뷰|후기){_PARTICLE}{{words}}(?:남겨|남기|써\s*주|써주|작성해|부탁|달아)",
                    rf"(?:별점|평점){_PARTICLE}{{words}}(?:주세요|주시|부탁|남겨|달아|매겨)"),
    },
    "en": {
        "votes": (r"\bup-?vot", r"\bvot(?:e|ing)\s+(?:for|on)\s+(?:us|this|my|our|it)\b",
                  r"\bvote\s+(?:us|it|this)\s+up\b", r"\byour\s+votes?\b", r"\bby\s+voting\b",
                  r"\b(?:smash|hit|click|tap|press)\s+(?:that\s+|the\s+|this\s+)?"
                  r"(?:like|upvote|vote|subscribe|follow|arrow|up\s*arrow)\b",
                  r"\bsupport\s+(?:us|me|our\s+launch)\s+on\b", r"\byour\s+support\s+on\b",
                  r"\bask\s+(?:your\s+)?(?:friends|colleagues|followers|team)\b",
                  r"\b(?:help|get)\s+(?:us|me|this)\b[^\n.!?]{{0,30}}{rank}", r"\bpush\s+(?:us|it)\s+to\s+{rank}"),
        "stars": (r"\bstar\s+(?:us|the\s+repo|this\s+repo|our\s+repo|the\s+project|it\b|them\b|on\s+github)",
                  r"\b(?:give|drop|leave|smash|hit|add)\b[^\n.!?]{{0,15}}\bstars?\b", r"⭐"),
        "reviews": (r"\b(?:leave|write|give|post|drop|add)\b[^\n.!?]{{0,15}}\b(?:review|rating)s?\b",
                    r"\b(?:rate|review)\s+(?:us|me|our|my|it|this)\b", r"\b(?:five|5)[- ]star\b"),
    },
}
THING: dict[Lang, tuple[str, ...]] = {
    "ko": ("리뷰", "후기", "별점", "평점", r"스타(?!트)", "업보트", "투표", "추천", "댓글"),
    "en": ("star", "upvote", "vote", "review", "rating", r"\brate\b", "comment"),
}
REWARD: dict[Lang, tuple[str, ...]] = {
    "ko": ("보상", "대가", "쿠폰", "기프티콘", "경품", "사은품", "추첨", "증정", "적립", "선물", "상품권", "드립니다", "드려요",
           "드릴게요"),
    "en": ("reward", "in exchange", r"gift ?card", r"amazon ?card", "voucher", "giveaway", "coupon", "incentive", "prize",
           r"we'?ll (?:give|send|gift|pay)", r"we will (?:give|send)", "get a free",
           r"\$\s?\d+[^\n.!?]{0,12}(?:card|coupon|credit|voucher|gift)"),
}
NEGATED_TAIL: dict[Lang, str] = {
    "ko": r"^[^,.;!?\n]{0,14}?(?:하지\s*(?:않|마)|말\s*것|말아|말고|금지|않(?:는|기|습|아))",
    "en": r"^\W*(?:(?:is|are|was)\s+)?(?:not\s+(?:allowed|permitted)|forbidden|prohibited|banned|against\s+the\s+rules)",
}
CONTRAST: dict[Lang, tuple[str, ...]] = {
    "ko": ("그러나", "하지만", "대신"),
    "en": (r"\b(?:but|however|yet|though|although|except|instead|just|then|unless|otherwise|forget|hesitate)\b",),
}
