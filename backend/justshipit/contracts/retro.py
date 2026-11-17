"""What the CEO returns when a bet ends, and what the server stores as the bet's retro.

The model writes the judgement (`RetroDraft`, or `PeopleRetroDraft` while the business has fewer than 30 people at
first value or beyond). The server adds what it knows itself: the hypothesis from the bet and the numbers from the
metric points, so the retro never rests on a number the model typed. Every field is required and nullable ones say so,
like the other CEO contracts, so the schema stays strict.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from justshipit.services.metrics import Observed

Verdict = Literal["continue", "stop", "change", "routine"]
Confidence = Literal["high", "mid", "low"]
MAX_NEXT_BETS = 3


class Cause(BaseModel):
    cause: str = Field(description="One sentence on a possible reason the numbers moved (or didn't). A candidate, "
                                   "not a certainty")
    evidence_ids: list[str] = Field(description="Evidence ids that support this candidate, such as mp-12, OT-3 or "
                                                "PPL-5. Only ids from the input. Empty when there are none")
    confidence: Confidence = Field(description="Match it to the evidence in the input. low when there is no evidence id")


class NextBet(BaseModel):
    title: str = Field(description="The next experiment's name. Short")
    pitch: str = Field(description="What it does, in one or two sentences")
    audience: str | None = Field(description="Who the test is for. null when unknown")
    question: str = Field(description="The one thing this experiment finds out, in one sentence. The hypothesis")
    appetite_days: int = Field(description="Time limit in days", ge=1, le=60)
    budget_usd: float | None = Field(description="Usage limit in USD equivalent. null when not set")
    exit_signal: str = Field(description="The sign that it worked, as a number of people who got value (first_value) "
                                         "or came back (repeat)")
    circuit_breaker: str = Field(description="The sign to stop at")
    in_scope: bool = Field(description="true when it stays inside the plan. false when it changes the starting path, "
                                       "the red lines, the budget or the success conditions")
    brief_line_ids: list[str] = Field(description="Ids of the plan lines that put it in scope. Only ids from the input. "
                                                  "Empty when out of scope")
    reason: str = Field(description="Why this experiment comes next, given what this one showed. One or two sentences")


class RoutineSpec(BaseModel):
    every_days: int = Field(description="Repeat every this many days", ge=1, le=30)
    what: str = Field(description="The work to repeat, in one or two sentences")


class RetroDraft(BaseModel):
    causes: list[Cause] = Field(description="1 to 4 candidate causes")
    counter_evidence: list[str] = Field(description="Facts that weaken the candidate causes. Empty when there are none")
    verdict: Verdict = Field(description="continue: keep going the same way. stop: stop. change: change direction. "
                                         "routine: it worked and should keep going, as a routine rather than an "
                                         "experiment")
    verdict_reason: str = Field(description="Why, in one or two sentences")
    routine: RoutineSpec | None = Field(description="Only when verdict is routine. Otherwise null")
    drop_channels: list[str] = Field(description="Keys of channels to stop posting to, given these numbers. Empty when "
                                                 "none")
    next_bets: list[NextBet] = Field(description="1 to 3 candidates for the next experiment", min_length=1,
                                     max_length=MAX_NEXT_BETS)


class BetRetroOut(RetroDraft):
    """The stored retro in numbers mode: the model's judgement plus the hypothesis and observations the server fills."""
    mode: Literal["stats"] = "stats"
    hypothesis: str
    observed: Observed
    notes: list[str] = []


class UserQuote(BaseModel):
    person_ref: str = Field(description="The id of the person who said it, such as PPL-5. Only ids from the input")
    quote: str = Field(description="What that person said, copied from the input word for word. Never edited or "
                                   "shortened")


class PeopleRetroDraft(BaseModel):
    did: str = Field(description="What this experiment did, in one line")
    users_said: list[UserQuote] = Field(description="What users said. Only quotes from the input. Empty when there are "
                                                    "none")
    next: str = Field(description="What to do next, in one line")
    verdict: Verdict
    verdict_reason: str
    routine: RoutineSpec | None
    drop_channels: list[str]
    next_bets: list[NextBet] = Field(min_length=1, max_length=MAX_NEXT_BETS)


class PeopleRetroOut(PeopleRetroDraft):
    """The stored retro while there are fewer than 30 people at first value or beyond: three lines, no statistics."""
    mode: Literal["people_quotes"] = "people_quotes"
    notes: list[str] = []
