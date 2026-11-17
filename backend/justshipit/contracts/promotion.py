"""What the marketing agent returns for the week's promotion: one post card for each place the server picked."""
from __future__ import annotations

from pydantic import BaseModel, Field

from justshipit.contracts.post_card import PostCardDraft


class PromotionOut(BaseModel):
    cards: list[PostCardDraft] = Field(description="One post card for each channel the input lists. None for a channel "
                                                   "it does not list")
    notes: list[str] = Field(description="Each channel you could not make a card for, with the reason. Empty if none")
