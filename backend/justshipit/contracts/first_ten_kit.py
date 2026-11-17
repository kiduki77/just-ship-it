"""What the marketing agent returns for the First 10 kit: the texts the server asked for, each named by its part, its slot
and, for a message, the person it is for. The server checks every text before it keeps one (`services/kit.py`)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class KitDraft(BaseModel):
    part: Literal["message", "profile", "invite"] = Field(description="Which part of the kit the text belongs to")
    slot: str = Field(description="`p` for a message; `line`, `short` or `about` for profile wording; `known` or "
                                  "`problem` for invite wording")
    person: str | None = Field(description="For a message, the person's ref as the input gives it (PPL-12). null for "
                                           "profile and invite wording")
    body: str = Field(description="The text, ready to paste: one person's message, or the wording as it is. Never "
                                  "longer than the limit the input names for it")


class KitOut(BaseModel):
    texts: list[KitDraft] = Field(description="One text for each line of the input's list, and none for a line it does "
                                              "not list")
