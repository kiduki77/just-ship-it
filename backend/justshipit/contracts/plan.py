"""The product plan the owner approves before any spec is written: what the product agent writes, and where a plan is
in its life. A spec is written from one approved version of it (`Spec.plan_commit_sha`)."""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class PlanStatus(StrEnum):
    DRAFT = "DRAFT"          # being written, or written again from the owner's notes
    IN_REVIEW = "IN_REVIEW"  # its card waits for the owner (or was put on hold)
    APPROVED = "APPROVED"    # the spec is written from approved_commit_sha
    REJECTED = "REJECTED"


Basis = Literal["verified", "sourced", "assumed"]


class PlanLineOut(BaseModel):
    text: str = Field(description="One claim in one plain sentence the owner reads")
    basis: Basis = Field(description="verified: a checked fact on the evidence list backs it (cite its EV id); "
                                     "sourced: a task result or a cited source backs it, unchecked; assumed: your "
                                     "inference or a guess")
    refs: list[str] = Field(description="The evidence ids (EV-12) and task ids (T-3) it rests on. An empty array for "
                                        "an assumption")


class SuccessOut(PlanLineOut):
    check: str = Field(description="How to check it once the product is out: what to count or look at, and where")


class PlanRevision(BaseModel):
    """The owner's note on an approved plan: what to change before its spec is approved."""
    note: str


class PlanDraftOut(BaseModel):
    title: str = Field(description="The product's name and what it does, in a few words")
    goal: list[PlanLineOut] = Field(description="The goal and the problem it solves, in 1 to 3 lines")
    users: list[PlanLineOut] = Field(description="Who uses it first, and in what moment")
    scope: list[PlanLineOut] = Field(description="What the first version does: the smallest version that tests the "
                                                 "core hypothesis")
    not_doing: list[PlanLineOut] = Field(description="What this version leaves out on purpose")
    success: list[SuccessOut] = Field(description="How we know it worked: things one can count or see after launch")
    evidence: list[PlanLineOut] = Field(description="The facts the plan leans on most")
    risks: list[PlanLineOut] = Field(description="What could make the plan wrong, and the questions still open")
