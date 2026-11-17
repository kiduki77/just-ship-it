"""What the research agent returns when it looks for people and threads. The server treats every field as untrusted:
it caps the lists, checks each link and quote against the page, and drops what it cannot confirm."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CandidateOut(BaseModel):
    platform: str = Field(description="One platform key from the prompt's 'Where to search'. The server drops any other key")
    handle: str = Field(description="The public handle or nickname the person used on that post, exactly as shown. "
                                    "Never make up a name")
    display_name: str = Field(description="The display name shown on the post. The same as handle when there is none")
    where_found_url: str = Field(description="The address of the post, comment or question where the person described "
                                             "the problem. Only an address you opened yourself")
    quote: str = Field(description="One or two sentences the person wrote there, verbatim. Do not summarize, translate "
                                   "or rewrite")
    why: str = Field(description="One sentence on why this person is in the business's target customers")
    from_alternative: bool = Field(description="true if found in a post or discussion about a competing or alternative "
                                               "service, false if the post describes this business's problem directly")
    questions: list[str] = Field(description="2 to 3 questions for the owner to ask, about past behavior (the last time "
                                             "it happened, what they did then). No opinions or hypotheticals")
    talking_points: list[str] = Field(description="0 to 3 things the owner could bring up. Short points. No promotional "
                                                  "sentences and no asks")


class FindPeopleOut(BaseModel):
    candidates: list[CandidateOut] = Field(description="At most 10 people. Leave out anyone you could not confirm. An "
                                                       "empty array when there are none")
    searched: str = Field(description="Two or three sentences on where and how you searched")


class ThreadOut(BaseModel):
    platform: str = Field(description="One platform key from the prompt's 'Where to search'. The server drops any other key")
    where_found_url: str = Field(description="The address of the question. Only an address you opened yourself")
    asker_handle: str = Field(description="The public handle of the person who asked")
    quote: str = Field(description="One or two sentences of the question, verbatim")
    asks: str = Field(description="One sentence on what they are asking")
    talking_points: list[str] = Field(description="2 to 3 facts or points the owner could offer as help. Short. Do not "
                                                  "write a reply to post")


class FindThreadsOut(BaseModel):
    threads: list[ThreadOut] = Field(description="3 to 5 questions the owner can really answer. An empty array when "
                                                 "there are none")
    searched: str = Field(description="Two or three sentences on where and how you searched")
