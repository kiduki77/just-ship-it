"""What a role produced in one pass, validated where it enters the ledger and trusted after that. The whole text is
kept, because the 30-day `var/calls` transcripts are not (design section 5). A build's steps already live in
`DevJob.steps` and `DevJob.result`, so a build turn keeps only what those do not."""
from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from justshipit.contracts.handoff import AcceptanceOut, CodeReviewOut
from justshipit.contracts.work import ReviewOut
from justshipit.staffing.crew import Duty


class Turn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    duty: ClassVar[Duty]
    role: str
    call_id: str
    # The fix round (0 for the first run and for every task) and the loop counter. Neither is unique: a build repeats
    # both, which is why the call id is the key.
    round: int = 0
    iteration: int = 0


class AuthorTurn(Turn):
    duty: ClassVar[Duty] = Duty.AUTHOR
    fixing: str = ""  # the rework or send-back text this pass answered
    summary: str
    findings_markdown: str
    assumptions: list[str]
    risks: list[str]
    open_questions: list[str]
    unmet_criteria: list[str]
    evidence_ids: list[int]


class VerifierTurn(Turn):
    duty: ClassVar[Duty] = Duty.VERIFIER
    review: ReviewOut
    independence: str


class BuildTurn(Turn):
    duty: ClassVar[Duty] = Duty.AUTHOR
    # Which of the engineer's calls this is: the plan, the build (a fix round's change too), the engineer's own review
    # pass, or the fix of what the reviewer found. Two of them share a round and an iteration.
    stage: Literal["plan", "build", "review", "fix"]
    summary: str
    decisions: list[str]
    commit: str | None = None


class BuildReviewTurn(Turn):
    duty: ClassVar[Duty] = Duty.VERIFIER
    review: CodeReviewOut


class AcceptanceTurn(Turn):
    duty: ClassVar[Duty] = Duty.VERIFIER
    acceptance: AcceptanceOut
