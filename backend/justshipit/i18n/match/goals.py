"""The words of a goal line in the plan, per language (`services/completion.py`): "12명이 두 번 이상 쓰고, 그중 6명과
대화", "12 users use it twice, and talk to 6 of them".

`CLAUSE` splits a line into goals. `UNITS` are the counters a number may carry; `WHO` are the ones that count people,
the only thing the people list holds. `MEASURES` names what a clause counts, first match wins, and a clause about
money counts nothing the list holds (`None`). `OF_THEM` marks a talk goal that counts within the goal before it.
Korean fragments have no edges, since a particle sits against the word; an English one is a whole word, and "use"
also matches "users", as "사용" matches "사용자".
"""
from justshipit.i18n import Lang


def _en(*fragments: str) -> str:
    return "|".join(rf"(?<![a-z])(?:{f})(?![a-z])" for f in fragments)


CLAUSE: dict[Lang, str] = {"ko": r"\s그리고\s", "en": r"\s(?:and|then)\s"}

WHO: dict[Lang, tuple[str, ...]] = {
    "ko": ("명", "곳", "개사", "팀", "가구", "군데", "매장", "업체", "분"),
    "en": ("people", "person", "users", "user", "customers", "customer", "teams", "team", "companies", "company",
           "businesses", "business", "shops", "shop", "stores", "store", "households", "household", "developers",
           "developer", "clients", "client", "founders", "founder", "members", "member"),
}
UNITS: dict[Lang, tuple[str, ...]] = {
    "ko": (*WHO["ko"], "만 원", "만원", "원", "개", "건", "회", "번", "통", "시간", "%", "퍼센트"),
    "en": (*WHO["en"], "times", "posts", "post", "hours", "hour", "signups", "signup", "stars", "star", "%", "percent",
           "dollars"),
}

MEASURES: dict[Lang, tuple[tuple[str | None, str], ...]] = {
    "ko": (
        (None, r"유료|결제|매출|구독료|전환|계약|구매"),
        ("repeat", (r"두\s*번\s*이상|2\s*번\s*이상|반복|재사용|재이용|재방문|재주문|다시\s*(?:쓰|써|사용|이용|맡|찾)"
                    r"|계속\s*(?:쓰|써|사용|이용)")),
        ("talked", r"대화|이야기|인터뷰|통화|만나|만남|미팅|면담"),
        ("contacted", r"연락|접촉|컨택"),
        ("first_value", r"첫\s*가치|사용|이용|써\s*보|써보|쓰기\s*시작|도입|체험|맡기|맡긴|무료|도움"),
    ),
    "en": (
        (None, _en(r"paid", r"pay(?:s|ing)?", r"revenue", r"subscri\w*", r"conver(?:t|sion)\w*", r"contracts?", r"purchas\w*",
                   r"buy(?:s|ing)?", r"sales?", r"MRR", r"ARR")),
        ("repeat", _en(r"twice", r"(?:two|2|three|3)\s+(?:or\s+more\s+)?times", r"again", r"repeat\w*",
                       r"(?:came|come|comes|coming)\s+back", r"return\w*", r"reuse\w*", r"weekly",
                       r"(?:keep|kept|keeps)\s+using")),
        ("talked", _en(r"talk\w*", r"spoke", r"speak\w*", r"interview\w*", r"calls?", r"called", r"met", r"meet\w*",
                       r"conversations?", r"chat(?:s|ted|ting)?")),
        ("contacted", _en(r"contact\w*", r"reach(?:ed)?\s+out", r"reached", r"emailed")),
        ("first_value", _en(r"got\s+value", r"use[sd]?", r"using", r"users?", r"tr(?:y|ied|ies)", r"sign(?:ed|s)?\s+up",
                            r"onboard\w*", r"adopt\w*", r"free")),
    ),
}

OF_THEM: dict[Lang, str] = {"ko": r"그\s*중", "en": _en(r"of\s+them", r"among\s+them")}
