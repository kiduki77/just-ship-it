"""Task lifecycle, evidence ledger, and the structured units agents exchange."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from justshipit.contracts.owner_text import Body, Detail
from justshipit.contracts.requests import AgentAskDraft

# A task result is read by the CEO, the next task and the owner, mostly in part. Measured 2026-11-04: summaries ran to
# 1,542 characters and findings to 16,090. The summary is cut at its limit; the findings are not (the spec writer and
# the engineer read them whole), so their limit is the author's to keep.
SUMMARY_CHARS = 500
FINDINGS_CHARS = 6000


class TaskStatus(StrEnum):
    PROPOSED = "PROPOSED"
    READY = "READY"
    RUNNING = "RUNNING"
    REVIEW = "REVIEW"
    REWORK = "REWORK"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REPLACED = "REPLACED"  # failed, and a later task of the project redoes it (TaskDraft.redoes)


TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PROPOSED: {TaskStatus.READY, TaskStatus.CANCELLED},
    TaskStatus.READY: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {TaskStatus.REVIEW, TaskStatus.FAILED},
    TaskStatus.REVIEW: {TaskStatus.DONE, TaskStatus.REWORK, TaskStatus.APPROVAL_REQUIRED, TaskStatus.FAILED,
                        TaskStatus.CANCELLED},  # an owner directive ruled it out mid-run
    TaskStatus.REWORK: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.APPROVAL_REQUIRED: {TaskStatus.APPROVED, TaskStatus.REJECTED},
    TaskStatus.APPROVED: {TaskStatus.DONE},
    TaskStatus.REJECTED: {TaskStatus.DONE, TaskStatus.CANCELLED},
    TaskStatus.FAILED: {TaskStatus.READY, TaskStatus.REPLACED},  # explicit retry, or a follow-up that redoes it
    TaskStatus.DONE: set(),
    TaskStatus.CANCELLED: set(),
    TaskStatus.REPLACED: set(),
}

TERMINAL_TASK_STATUSES = {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REJECTED,
                          TaskStatus.REPLACED}


class EvidenceKind(StrEnum):
    # Only the server (after verification) or a human may assign VERIFIED_FACT.
    VERIFIED_FACT = "verified_fact"
    SOURCED_CLAIM = "sourced_claim"   # claim backed by a cited source, not yet independently verified
    HYPOTHESIS = "hypothesis"         # agent inference without direct source
    ASSUMPTION = "assumption"         # explicit modelling assumption (e.g. price point)
    CALCULATION = "calculation"       # deterministic output of the finance engine


# ---------- LLM-facing output models (strict-schema compatible: no free-form dicts) ----------

class EvidenceOut(BaseModel):
    claim: str = Field(description="The claim, in one sentence")
    kind: EvidenceKind = Field(description="sourced_claim only with a source_url; hypothesis when nothing backs it")
    source_url: str | None = Field(description="The source's URL, or null")
    source_title: str | None
    quote: str | None = Field(description="The key sentence copied word for word from the source, in the source's language, or null")
    published_at: str | None = Field(description="When the source was published or what date it covers (YYYY or YYYY-MM), or null "
                                                   "when unknown")
    confidence: float = Field(description="0 to 1. Your own confidence, not the chance it is right")


class TaskDraft(BaseModel):
    key: str = Field(description="A key unique in the plan, for example market_size")
    title: str
    owner: str = Field(description="The id of the agent who does it (research, strategy, marketing, finance, product, "
                                   "risk, analyst)")
    objective: str
    acceptance_criteria: list[str]
    depends_on: list[str] = Field(description="Keys of the tasks this one waits for (an existing task's id works too)")
    new_evidence: str | None = Field(default=None, description=(
        "Only when this redoes a finished task: the new evidence that calls for it (EV-n, new material, a changed "
        "condition). Otherwise null. Finished work can be redone once at most"))
    redoes: str | None = Field(default=None, description=(
        "Only when this task does again the work of a task of this project that failed: that task's id (T-12). "
        "Otherwise null. The failed task then shows as redone, not failed"))
    family: str | None = Field(default=None, description=(
        "Only when the instructions list task families: the one that fits best. Otherwise null"))


class FinancialModelOut(BaseModel):
    """Inputs for the deterministic finance engine; the model never computes totals itself."""
    currency: str
    scenarios: list[FinancialScenarioOut]


class FinancialScenarioOut(BaseModel):
    name: str = Field(description="conservative | base | optimistic")
    price_per_customer_month: float
    customers_month_1: float
    monthly_customer_growth_rate: float = Field(description="For example 0.05 = 5% a month")
    monthly_churn_rate: float
    variable_cost_per_customer_month: float
    fixed_cost_month: float
    acquisition_cost_per_customer: float
    months: int
    assumption_notes: list[str]


FinancialModelOut.model_rebuild()


class AgentResultOut(BaseModel):
    summary: Detail(SUMMARY_CHARS) = Field(description=(
        f"The key conclusion, within {SUMMARY_CHARS} Korean characters. Lead with the conclusion. Anything over the "
        "limit is cut"))
    findings_markdown: Body() = Field(description=(
        f"The full analysis (markdown), within {FINDINGS_CHARS:,} Korean characters. Give every claim an evidence "
        "number such as [E1]. The sources and their original text are in evidence, so don't copy them into the text"))
    evidence: list[EvidenceOut] = Field(description="In the order the text cites them: [E1], [E2], ...")
    assumptions: list[str]
    risks: list[str]
    open_questions: list[str] = Field(description="Include the questions web research can't settle, which need checking in the field")
    unmet_criteria: list[str] = Field(default_factory=list, description=(
        "The acceptance criteria this result does not meet, word for word. Always list one done only in part or not "
        "at all. An empty array when every one is met"))
    proposed_follow_up_tasks: list[TaskDraft]
    requests: list[AgentAskDraft] = Field(description=(
        "Only when a person's answer, approval or material is needed. They reach the owner only after the "
        "independent review passes"))
    capability_gaps: list[str] = Field(description="Work that keeps coming back or was hard with the tools at hand (skill "
                                                     "candidates)")
    financial_model: FinancialModelOut | None = Field(description="Only for a finance task, otherwise null")


class ReviewVerdict(StrEnum):
    PASS = "pass"
    REWORK = "rework"
    ESCALATE = "escalate"


class ClaimCheckOut(BaseModel):
    evidence_index: int = Field(description="The evidence_index the list gives the item: 0 for [E1], 1 for [E2]")
    status: str = Field(description="supported | weak | contradicted | unverifiable")
    note: Detail()
    counter_source_url: str | None


class PreviousUnmetOut(BaseModel):
    number: int = Field(description="The item's number in your previous review: 1 for [U1]")
    fixed: bool = Field(description="true when this result fixes it")


class ReviewOut(BaseModel):
    verdict: ReviewVerdict = Field(description="rework only when criteria_unmet is not empty")
    previous_unmet: list[PreviousUnmetOut] = Field(default_factory=list, description=(
        "Only when your previous review of this task is given: every item it listed, judged first"))
    criteria_met: list[str]
    criteria_unmet: list[str] = Field(description=(
        "What keeps the result from the task's goal and a rework can fix: each acceptance criterion it misses and "
        "what is missing, in plain words the owner reads. Cite evidence as [E3]. Never a remark about how the "
        "evidence list is numbered or formatted"))
    notes: list[Detail()] = Field(default_factory=list, description=(
        "What is unmet but does not block the goal or can't be fixed by a rework: a criterion the author's tools "
        "can't meet (mark it '(tool limit)'), a check that needs the field, a gap a later task can take. The owner "
        "reads them; they never send the work back"))
    claim_checks: list[ClaimCheckOut]
    counter_arguments: list[Detail()] = Field(description="The strongest arguments against the conclusion")
    rework_instructions: Detail() | None = Field(description="Concrete fixes when rework, otherwise null")
    contested: bool = Field(description="true when the evidence leaves real disagreement about the conclusion itself "
                                      "(the signal to call a meeting)")
