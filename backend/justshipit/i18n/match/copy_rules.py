"""The words the public-copy rules read, per language (`services/copy_rules.py`). A draft post is in the language the
owner posts in, so every rule reads every language at once.

`FIRST_PERSON` says a body is in the owner's voice, `EXPERIENCE` that a sentence states something the owner lived
through, and `INTERNAL` maps a pattern to the kind of note the company left for itself (`post.internal.<kind>`).
Korean drops the subject and the particles sit against the word, so a Korean fragment has no edges.
"""
from justshipit.i18n import LANGS, Lang, msg

PLACEHOLDER: dict[Lang, str] = {lang: msg("post.placeholder", lang) for lang in LANGS}
# A label that opens an outline item and makes it read as a headline ("title: ...").
TITLE_WORDS: tuple[str, ...] = ("title", "제목")

FIRST_PERSON: dict[Lang, tuple[str, ...]] = {
    "ko": ("저는", "제가", "저도", "저희", "나는", "내가", "나도", r"우리\s*집"),
    "en": (r"(?<![A-Za-z])(?:I|I'm|I've|my|me)(?![A-Za-z])",),
}
EXPERIENCE: dict[Lang, tuple[str, ...]] = {
    "ko": (r"(?:써|해|가|맡겨|이용해|사용해|시켜|불러|맡아|겪어|받아)\s?봤", "그만뒀", "그만두었", "끊었", "겪었", "당했",
           "해지했"),
    "en": (r"(?<![A-Za-z])(?:I|we)\s+(?:tried|used|quit|stopped|cancell?ed|switched)(?![A-Za-z])",
           r"(?<![A-Za-z])I've\s+(?:tried|used|had)(?![A-Za-z])"),
}
INTERNAL: dict[Lang, tuple[tuple[str, str], ...]] = {
    "ko": ((r"지시서|근거 원장", "company_word"),
           (r"\((?:시범\s*)?안\)|확정\s*아님|확정이?\s*아닙니다", "draft_note")),
    "en": ((r"(?<![A-Za-z])(?:work\s+brief|evidence\s+ledger)(?![A-Za-z])", "company_word"),
           (r"\((?:pilot\s+)?draft\)|(?<![A-Za-z])not\s+final(?![A-Za-z])", "draft_note")),
}
