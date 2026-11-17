"""The rule every system prompt ends with: which language the owner reads, and the owner's words for things.

`agents.executor` appends `output_rule(lang)` to every call's system prompt, whichever prompt file or override the
caller used, so the Project Manager's kickoff replies, decision cards, reports, drafts and the idea panel all follow
the language the owner chose in Settings. It goes last because it overrides any language a prompt file names.
"""
from __future__ import annotations

from justshipit.i18n import NAMES, Lang
from justshipit.i18n.glossary import TERMS

# English says the same thing in more characters than Korean. A length a prompt or schema gives in Korean characters
# ("70자 이내") allows this many times as many in English; `contracts.owner_text` clips by the same factor.
EN_LENGTH_FACTOR = 1.8

_STYLE: dict[Lang, str] = {
    "en": "Plain, everyday English a first-time founder understands. Short sentences. No jargon such as TAM, MVP, KPI "
          "or OKR.",
    # docs/KOREAN-STYLE.md, kept short because every call pays for it. The one example teaches more than the list.
    "ko": "오너가 읽는 글은 해요체로, 한국 사람이 처음부터 한국어로 쓴 것처럼 쓴다. 결론을 먼저, 짧은 문장으로, 누가 "
          "하는지 드러나는 능동형으로 쓴다('성공적으로 저장되었습니다'가 아니라 '저장했어요'). 번역투를 쓰지 않는다: "
          "~에 대해, ~을 통해, ~의 경우, ~하는 것이 가능, ~되어지다, ~에 의해, 그것·그들, 당신. 뻔한 주어는 빼고, "
          "오너를 꼭 불러야 할 때만 '오너님'이라고 쓴다. TAM, MVP, KPI, OKR 같은 말은 쓰지 않는다.",
}


# The rule is for the owner's reading. What the company builds has its own users and its own language.
_PRODUCT = ("Leave the product being built to its own language: its UI, its user-facing copy, its README and its code "
            "follow the language its spec or the product names, not this rule.\n")
_ENGINEERING = ("In this engineering run the rule covers only the fields the owner reads: your summary, report, "
                "questions and decision cards. Everything you write into the repository follows the spec.\n")


# What every agent of a business whose customers are reached in person nearby reads with its plan (read by
# `orchestration.context.project_brief` when the description's `reach` has `local`): the owner runs a shop, so the people
# it serves are customers, and ad and startup jargon means nothing to them.
LOCAL_WORDS = (
    "This is a local business people visit in person. In everything the owner reads, call the people it serves "
    "customers (손님 in Korean), not users or external users, and say someone came back (다시 왔어요) rather than "
    "reuse or retention. Use the owner's own trade's plain words. Leave out ad and startup jargon (CPC, CPM, CAC, "
    "conversion rate, funnel, MVP, B2C) unless you say it plainly, and never write internal names such as "
    "experiment ids (BET-7) or role titles (CPO).")


def output_rule(lang: Lang, *, engineering: bool = False) -> str:
    name = NAMES[lang]
    terms = "\n".join(f"- {t.words[lang]}: {t.means[lang]}"
                      + (f" (never call it {', '.join(repr(w) for w in t.retired[lang])})" if t.retired.get(lang) else "")
                      for t in TERMS)
    lengths = ("" if lang == "ko" else
               f"\nA length limit written in Korean characters (such as '70자 이내') allows about {EN_LENGTH_FACTOR:g} "
               "times as many characters in English.")
    return (
        f"## Output language (this rule wins over any language named above)\n"
        f"The owner reads {name}. Write every text the owner reads in {name}: replies, questions and answer chips, "
        "plan lines, decision cards, options, reports, summaries, to-dos, warnings and drafts. Do this even when the "
        "owner, a document or an example above is in another language.\n"
        "Keep these as written: quotes from sources and from the owner, names, links, code, identifiers, file paths "
        "and commit messages. A draft meant for a community in another language is written in that community's "
        "language. JSON keys and enum values (such as a verdict of approve or reject) stay exactly as the schema "
        "gives them; only the text in string values follows this rule.\n"
        "In text the owner reads, never use a shorthand made up inside the work (D14, P0, C4, v2, section 6, index "
        "5): write the date, the day or the thing itself.\n"
        f"{_PRODUCT}{_ENGINEERING if engineering else ''}"
        f"Style: {_STYLE[lang]}{lengths}\n"
        f"Use the owner's words for the company's things:\n{terms}"
    )
