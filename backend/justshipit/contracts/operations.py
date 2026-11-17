"""Contracts for recurring operations: change watch, KPI review, portfolio review."""
from __future__ import annotations

from pydantic import BaseModel, Field

from justshipit.contracts.requests import AgentAskDraft
from justshipit.contracts.work import EvidenceOut


class KpiDraftOut(BaseModel):
    name: str = Field(description="a measurable metric's name, e.g. organizations interviewed")
    unit: str
    target: float
    direction: str = Field(description="up (higher is better) | down")
    rationale: str


class MaterialChangeOut(BaseModel):
    related_evidence_id: str | None = Field(description="the EV-id of the existing evidence it affects, or null")
    change: str
    impact: str = Field(description="what it does to the decisions and hypotheses")
    severity: str = Field(description="low | medium | high")


class WatchOut(BaseModel):
    summary: str
    material_changes: list[MaterialChangeOut]
    new_evidence: list[EvidenceOut]
    recommend_reassessment: bool = Field(description="true when a change is big enough to review the existing decisions")


class KpiReviewOut(BaseModel):
    assessment: str
    on_track: bool
    diagnosis: list[str] = Field(description="hypotheses for the gap against the target (stated as hypotheses)")
    requests: list[AgentAskDraft] = Field(description="proposals such as strategy or priority changes (mostly PROPOSAL)")


class PortfolioReviewOut(BaseModel):
    summary_markdown: str = Field(description="the company's weekly summary")
    priorities: list[str]
    stalled_items: list[str] = Field(description="approvals waiting long and work that is stuck")
    requests: list[AgentAskDraft]
