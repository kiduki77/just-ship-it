"""What one lens of the idea review panel returns: warnings about why the idea may not work now.

The panel advises and never decides. There is no verdict and no score, only warnings, each with a way past it that
the owner can take alone: a step that needs hiring, investors, partners, a team or a paid agency is dropped here
(`owner_alone`), whatever the prompt said. Text fields are written in the language the output-language rule names."""
from __future__ import annotations

import re
from typing import Literal, get_args

from pydantic import BaseModel, Field, field_validator

from justshipit.i18n.match.owner_alone import WORDS

Level = Literal["참고", "주의", "경고"]
NOTE, CAUTION, WARNING = get_args(Level)

NEEDS_OTHERS = re.compile("|".join(f for words in WORDS.values() for f in words), re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")


def owner_alone(text: str) -> str:
    """`text` without the sentences that need someone the owner would hire, raise money from, partner with or pay as
    an agency. Empty when every sentence does."""
    kept = [x for x in _SENTENCE_END.split(text.strip()) if x and not NEEDS_OTHERS.search(x)]
    return " ".join(kept)


class Source(BaseModel):
    label: str = Field(description="One line on what backs the warning: for a page found by web search, what the page "
                                   "is. With nothing found, an empty string: the console marks a source with no url as "
                                   "a guess")
    url: str | None = Field(description="The address of that page as the web search returned it, or null when nothing "
                                        "was found. Never write an address from memory")
    quote: str = Field(description="With a url, the sentence on that page that backs the warning, copied exactly as the "
                                   "page has it, in the page's own language (about 25 characters). Do not translate or "
                                   "summarize it. The server opens the page and drops the source when the sentence is "
                                   "not there. An empty string when url is null")


class IdeaWarning(BaseModel):
    title: str = Field(description="The warning in one line, in the output language. Example: 'Plenty of similar apps "
                                   "already exist'")
    why: str = Field(description="Why it is a worry, in 2-3 sentences in the output language, with concrete names, "
                                 "numbers and facts")
    level: Level = Field(description=f"{NOTE}: good to know. {CAUTION}: belongs in the plan. {WARNING}: without getting "
                                     "past it, reaching first users is unlikely")
    evidence: list[Source] = Field(description="0-3 sources for this warning")
    to_overcome: str = Field(description="1-2 sentences in the output language: something the owner can try alone within "
                                         "two weeks to get past this worry, such as asking people, a small test or a "
                                         "narrower scope. The server deletes any sentence that needs hiring, investment, "
                                         "partners, a team, an agency or outsourcing")

    @field_validator("to_overcome")
    @classmethod
    def _owner_alone(cls, v: str) -> str:
        return owner_alone(v)


class Similar(BaseModel):
    name: str = Field(description="The name of a similar service or app found by web search, as its page writes it")
    url: str = Field(description="That service's page, only an address the web search returned. The server opens it and "
                                 "checks the name is there")
    how_it_differs: str = Field(description="One sentence in the output language on what it shares with the owner's idea "
                                            "and what differs")


class CritiqueOut(BaseModel):
    headline: str = Field(description="The biggest worry from this lens in one sentence, in the output language")
    warnings: list[IdeaWarning] = Field(description="1-4 warnings, the most important first", min_length=1)
    similar: list[Similar] = Field(description="0-6 similar services, only for the market lens. An empty list for any "
                                               "other lens")
