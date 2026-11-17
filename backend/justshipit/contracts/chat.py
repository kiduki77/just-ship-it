"""What an agent writes when it answers the owner in team chat (services/chat_reply.py). The reply is words; the intent
is a request the server checks (services/chat_tags.allowed), never an action the model takes."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from justshipit.contracts.requests import AgentAskDraft


class WorkAsk(BaseModel):
    title: str = Field(description="The task card's title: a short noun phrase for the change, in the owner's language")
    instructions: str = Field(description="What to change, concrete enough for the engineer who builds it")


class ChatReplyOut(BaseModel):
    reply: str = Field(description="Your answer to the owner: two to four warm, plain sentences, in the owner's "
                                   "language")
    intent: Literal["answer", "work", "escalate", "handoff", "decision"] = Field(
        description="What your reply asks the server to do: answer only, work (a fix on this build), escalate (to the "
                    "Project Manager), handoff (Project Manager only, to another role), or decision (a card "
                    "the owner decides)")
    work: WorkAsk | None = Field(description="The change to make, for intent work. null otherwise")
    to: str | None = Field(description="The role key to escalate or hand off to (project_manager, marketing, product, ...). null "
                                       "otherwise")
    why: str | None = Field(description="One line on why you escalate or hand off. null otherwise")
    decision: AgentAskDraft | None = Field(description="The decision card, for intent decision. null otherwise")


class RecallReplyOut(BaseModel):
    """What a recalled role writes (services/chat_recall.py). It has no intent: a recalled role explains, never acts."""
    reply: str = Field(description="Your answer to the owner, from the records only: two to five plain sentences in the "
                                   "owner's language, naming the record ids you rely on in square brackets")
    cites: list[str] = Field(description="The id of every record your reply relies on, as the records name them "
                                         "(T-3, turn-12, E-4). Empty only when no record answers the question")
    no_record: bool = Field(description="true when the records do not answer the question, or answer only part of it")
