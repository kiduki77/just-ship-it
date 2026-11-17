"""The post package an agent proposes for a place the owner posts by hand. The company never posts.

Every field is required and nullable ones say so, as in the decision card, so the model's schema stays strict. What
the owner finally reads is built by the server (`services/owner_tasks.build_post_card`): the channel's rules and mode
come from the registry, never from the model, and an `outline` channel loses any title or body the model wrote.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from justshipit.contracts.owner_text import Detail
from justshipit.i18n.match.copy_rules import PLACEHOLDER
from justshipit.services import channels

ChannelKey = Literal[tuple(channels.keys())]  # type: ignore[valid-type]


class ExpectedQuestion(BaseModel):
    q: str = Field(description="A question the post is likely to get in the comments")
    talking_points: list[str] = Field(description="Facts and points for answering it. Points, not sentences")


class PostWhen(BaseModel):
    window: str = Field(description="A good time to post, e.g. Tue to Thu 9 to 11 am US Eastern, which is 10 pm to "
                                    "midnight in Korea")
    reason: str = Field(description="Why that time. Say it is a guess when nothing backs it")


class PostWhat(BaseModel):
    title: str | None = Field(description="The draft title, for a draft channel only. null for an outline channel")
    body: str | None = Field(description="The draft body, for a draft channel only. null for an outline channel. The "
                                         "owner pastes it as it is, so never invent what the owner went through (leave "
                                         f"that spot as {' or '.join(PLACEHOLDER.values())}), and put notes such as "
                                         "evidence ids, task ids or 'draft, not final' in the checklist")
    facts: list[str] = Field(description="Outline channel: the facts the post must hold (numbers, features, limits). "
                                         "Points, not sentences")
    expected_questions: list[ExpectedQuestion] = Field(description="Outline channel: likely questions and what to "
                                                                   "answer")
    checklist: list[str] = Field(description="What the owner checks before posting (rules, links, timing)")


class PostCardDraft(BaseModel):
    """The post card the owner reads to post by hand. The server fills in the rules and the mode; do not write them."""
    channel: ChannelKey = Field(description="Where to post. other when the list does not have it")
    where_url: str | None = Field(description="The address to post at (a subreddit, a submit page, a repository). "
                                              "null when unknown")
    when: PostWhen
    why: Detail(300) = Field(description="One or two sentences on which experiment's hypothesis or which plan line "
                                         "this post serves. No line ids")
    what: PostWhat
