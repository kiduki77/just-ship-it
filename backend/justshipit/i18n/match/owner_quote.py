"""Words that carry no meaning of their own, per language, which a quote of the owner's words does not count as
content, and the words that state a reason (`services/owner_quote.py`)."""
from justshipit.i18n import Lang

# A line that gives a reason ("is the reason to switch", "가격 때문에") claims more than a quote that gives none.
# English entries are whole words; Korean ones match inside a word, since particles sit against it.
REASONS: dict[Lang, tuple[str, ...]] = {
    "ko": ("때문", "이유", "덕분", "왜냐", "탓에"),
    "en": ("because", "reason", "reasons", "why", "due to", "so that", "thanks to"),
}
# The markers the reason follows ("because it doesn't scale", "왜냐하면 ..."). Before the other Korean ones it comes
# first ("비싸기 때문에").
REASON_FOLLOWS = frozenset({*REASONS["en"], "왜냐"})

STOPWORDS: dict[Lang, str] = {
    "ko": """
그리고 그래서 하지만 그런데 그러나 그러면 그럼 그래도 또는 그리고는 합니다 해요 했어요 하고 해서 있어요 있습니다 없어요 없습니다
같아요 같습니다 이에요 예요 입니다 이라고 라고요 정말 진짜 그냥 너무 아주 매우 많이 조금 약간 아마 이거 그거 저거 이것 그것 저것
이런 그런 저런 이렇게 그렇게 저렇게 제가 저는 내가 나는 우리 우리가 저희 어떤 어느 무엇 같은 하는 되는 있는 없는 하면 하지
""",
    "en": """
the and for with that this are was were but not you your have has had from they them then than into over just very some
what when where which will would can could should about also been being its is to of in on at as an it be or we my me do if
so no by up doesn don isn aren wasn weren didn hasn haven hadn couldn shouldn wouldn re ve ll
""",
}
