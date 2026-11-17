"""What the CEO returns when it reads the owner's words and plan for the customer description."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from justshipit.contracts.owner_text import OneLine
from justshipit.services.profile import vocab

# Every live code of every line, so the schema offers the model nothing else. The server checks that a code is on
# the line the model put it under and drops it otherwise.
ProfileCode = Literal[tuple(code for line in vocab.LINES for code in vocab.live_codes(line))]  # type: ignore[valid-type]
_LANGUAGE = "in the language the Output language rule names"


class ProfileValueOut(BaseModel):
    line: Literal["buyer", "reach", "has"] = Field(
        description="buyer: who pays for it. reach: how the owner will meet those people. has: what the owner already "
                    "has to reach them with")
    code: ProfileCode | None = Field(
        description="One code from the vocabulary of this line that the material supports. null when the value is a "
                    "sentence instead")
    sentence: OneLine(120) | None = Field(  # type: ignore[valid-type]
        description=f"One short sentence, {_LANGUAGE}, only for what no code of the line says. null when code is set")
    owner_quote: str = Field(
        description="The owner's words behind this value, copied character for character from the owner's answer or "
                    "from a plan line marked said (at least 6 characters without spaces, at least 2 meaningful words, "
                    "at least one word that this value is about). The server turns the value into a guess when the "
                    "quote is not there or is not about the value. For a guess: an empty string")


class ProfileOut(BaseModel):
    values: list[ProfileValueOut] = Field(
        description="The values the material supports. A line the material does not settle is left out, so it stays "
                    "unknown. Do not fill a line the material says nothing about")
