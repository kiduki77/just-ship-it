"""CEO planning, meeting and decision contracts."""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from justshipit.contracts.operations import KpiDraftOut
from justshipit.contracts.owner_text import Detail, OneLine
from justshipit.contracts.requests import AgentAskDraft
from justshipit.contracts.work import TaskDraft

# The final report is a report to a superior: measured reports were 36-87k characters, 85-91% of it the evidence table
# appended to the body. The ledger goes to its own file. The body's limit is the CEO's to keep and is not cut, since the
# spec writer reads the report; an overlong body is logged (`company_graph.report`).
REPORT_BODY_CHARS = 8000
MAX_CONCLUSIONS = 6
MAX_BUILD_INPUTS = 3


class ProjectManagerPlanOut(BaseModel):
    goal_interpretation: str = Field(description="How the Project Manager understands the owner's goal")
    deliverables: list[str] = Field(description="What this project must produce")
    success_criteria: list[str]
    out_of_scope: list[str]
    key_uncertainties: list[str]
    stop_rule: OneLine(160) = Field(default="", description=(
        "Whether to keep going after the owner's talks with the people the plan names this week, as one rule with a "
        "number and a day, said to the owner. The day is the talks' due day from 'Dates', or counted from a step the "
        "plan names, never a calendar date you work out. E.g. If 2 of the 3 don't use it again within 2 days of "
        "trying it, we stop and rethink who it's for. An empty string when the plan names no one to talk to"))
    talk_asks: list[str] = Field(default_factory=list, description=(
        "What the owner asks the people the plan names in this week's talks, when the plan says what to ask them "
        "(join the group chat and pre-order next week's dishes; send one day's sample file): at most 3 questions, "
        "one short sentence each, in the owner's words to that person. Their to-dos show these instead of the "
        "standard interview questions. An empty array when the talks are to hear how they deal with the problem "
        "today, or the plan names no one"))
    build: BuildPlanOut | None = Field(default=None, description=(
        "When the owner's first deliverable is working software (a web app, an app, a web service, a bot), that "
        "software. The server sets the build's share of the budget aside from the start and asks to build it within "
        "round 1, once the needs_task_keys tasks are done. When the product already exists ('The product as it is "
        "today'), the changes to its current code the goal needs, never a first version. null when research, "
        "validation or launching what exists is the first deliverable"))
    tasks: list[TaskDraft]
    requests: list[AgentAskDraft] = Field(description="Only when the direction is unclear and the owner must answer before work goes on")


class BuildPlanOut(BaseModel):
    title: OneLine(80) = Field(description="The software to build, within 80 Korean characters. For example: MVP web app for bean run-out "
                                                "and reorder dates")
    problem: Detail(400) = Field(description="The problem it solves for the owner's customers, within 400 Korean characters")
    why_software: Detail(300) = Field(description="Why it should be software, within 300 Korean characters")
    needs_task_keys: list[str] = Field(description=(
        f"Keys of this plan's tasks that must finish before the spec is written, at most {MAX_BUILD_INPUTS}. An empty "
        "array when none (the build is asked for at once). Leave out research that would not change what gets built"))


ProjectManagerPlanOut.model_rebuild()


class ProjectManagerReviewAction(StrEnum):
    COMPLETE = "complete"
    FOLLOW_UP = "follow_up"
    MEETING = "meeting"


class ProjectManagerReviewOut(BaseModel):
    assessment: Detail() = Field(description="The results so far, judged against the success criteria")
    unmet_success_criteria: list[Detail()]
    action: ProjectManagerReviewAction
    meeting_agenda: str | None = Field(description="The question at issue when action=meeting, otherwise null")
    meeting_participants: list[str]
    follow_up_tasks: list[TaskDraft] = Field(description="Only when action=follow_up")
    requests: list[AgentAskDraft]


class PositionOut(BaseModel):
    stance: str = Field(description="support | oppose | conditional | undecided")
    statement: Detail()
    evidence_ids: list[str] = Field(description="Ids from the evidence list, for example EV-12")
    concerns: list[Detail()]


class DecisionKind(StrEnum):
    ADOPT = "adopt"
    DEFER = "defer"
    REJECT = "reject"
    CONDITIONAL = "conditional"


class DecisionOut(BaseModel):
    title: Detail()
    decision: DecisionKind
    rationale: Detail()
    # Without these the console cannot show how well a decision is supported (P-5's decisions cited EV ids
    # only in the rationale and linked none).
    supporting_evidence_ids: list[str] = Field(
        description="Evidence list ids that support the decision (for example \"EV-12\"). Put here every EV the "
                    "rationale cites as grounds for the decision")
    dissenting_evidence_ids: list[str] = Field(
        description="Evidence list ids that go against the decision. Never leave it empty when such evidence exists")
    dissent_summary: Detail()
    open_questions: list[Detail()]
    action_tasks: list[TaskDraft]
    approval_required: bool = Field(description="true for a major change of the business's direction or an action outside the company")
    approval_reason: Detail() | None


class SectionAreaOut(BaseModel):
    heading: str = Field(description="One '## ' heading of report_markdown, exactly as written there, without the '## '")
    area: Literal["software", "marketing", "design", "operations", "none"] = Field(
        description="What the section is about: software (what to build and how it runs), marketing (customers, "
                    "channels, messages and sales), design (screens and how it looks), operations (costs, prices, "
                    "suppliers and the rules of the trade), none for anything else")


class FinalReportOut(BaseModel):
    """The report opens like a report to a superior: the conclusion and what the owner does first, the rest on
    demand. The console shows `recommendation` and `executive_summary`, opens the sections in the areas the owner is
    strong in (`section_areas`, services.strengths) and folds the rest."""
    title: OneLine(80)
    executive_summary: Detail(700) = Field(
        description="The summary, within 700 Korean characters and five lines: the conclusion, what the owner should "
                    "do, and why, in that order. No evidence ids and no internal terms")
    recommendation: OneLine(120) = Field(description="A one-line conclusion and its grounds, opening with go, stop, "
                                                     "or go with conditions")
    conclusions: list[ConclusionOut] = Field(default_factory=list, description=(
        f"The report's key conclusions, at most {MAX_CONCLUSIONS}, each with the evidence list ids that back it. The "
        "server marks each one, by that evidence's verification status, as resting on checked evidence or on "
        "assumptions and inference"))
    report_markdown: str = Field(description=(
        f"The report's body, within {REPORT_BODY_CHARS:,} Korean characters, in sections under '## ' headings. Cite "
        "facts as [EV-n] and mark hypotheses as such. The server attaches the evidence list and each task's detail as "
        "files of their own, so don't copy them into the body"))
    section_areas: list[SectionAreaOut] = Field(default_factory=list, description=(
        "The area of each '## ' section of report_markdown, one entry per section"))
    validation_plan: list[str] = Field(description="How to check it in the field and with customers (interviews, experiments and so on)")
    development_needs: list[DevelopmentNeedOut]
    kpis: list[KpiDraftOut] = Field(description="2 to 5 numbers that track the validation plan's progress and the business's results")

    @field_validator("conclusions", mode="after")
    @classmethod
    def _few(cls, v: list[ConclusionOut]) -> list[ConclusionOut]:
        return v[:MAX_CONCLUSIONS]


class ConclusionOut(BaseModel):
    statement: OneLine(160) = Field(description="The conclusion in one sentence, within 160 Korean characters")
    evidence_ids: list[str] = Field(description="Evidence list ids that back this conclusion (for example \"EV-12\"). "
                                                   "An empty array when none")


class DevelopmentNeedOut(BaseModel):
    title: str
    problem: str
    why_software: str
    expected_value: str
    priority: str = Field(description="P0 | P1 | P2")


FinalReportOut.model_rebuild()


class RequestMergeOut(BaseModel):
    keep_id: int = Field(description="The number of the request to keep (n in REQ-n)")
    merge_ids: list[int] = Field(description="Numbers of the duplicate requests to merge into the kept one")
    merged_title: OneLine(80, names=True) = Field(description="The merged request's new title, so the owner decides it in one go")
    reason: Detail()


class RequestWithdrawOut(BaseModel):
    id: int
    reason: Detail()


class TaskRevisionOut(BaseModel):
    task_id: int = Field(description="The number of the task to revise (n in T-n). Only a task that hasn't started")
    title: Detail()
    objective: Detail()
    acceptance_criteria: list[str]


class DirectiveRuleOut(BaseModel):
    label: str = Field(description="The work the owner said not to do, in one phrase. For example: market size count")
    keywords: list[str] = Field(description=(
        "1 to 3 words that the title of every task or request doing that work holds, in the language the titles are "
        "written in. For example: [\"market size\"], [\"spreadsheet\", \"recheck\"]. The server creates no task or "
        "request whose title holds all of them, and cancels the open ones. A word in Latin letters needs three "
        "letters or more and matches only as a whole word, plural included: \"ads\" matches \"Facebook ads\" but not "
        "\"leads\". A Korean word needs two letters or more and matches inside a word too, except a rule's only word "
        "when it is three letters or fewer, which must start a word in the title, so write the word the titles use. "
        "Never use one broad word alone, such as 'research' or 'check'. The server doesn't apply a rule with a word "
        "too short for its script, or one that matches more than 30% of the titles of the business's tasks and "
        "waiting requests"))


class RealignOut(BaseModel):
    """CEO's response to new owner guidance while a project is running."""
    directive_rules: list[DirectiveRuleOut] = Field(default_factory=list, description=(
        "One for each piece of work the owner's new instruction says not to do. An empty array when it rules out "
        "nothing"))
    assessment: Detail() = Field(description="How the owner's instruction changes the direction, and what in the "
                                             "current plan or requests no longer fits")
    goal_interpretation: Detail() = Field(description="The goal as read with the owner's instruction (the current one "
                                                      "when it didn't change)")
    out_of_scope_additions: list[str] = Field(description="What the owner's instruction puts out of scope")
    cancel_task_ids: list[int] = Field(description="Numbers of tasks that haven't started and no longer fit the "
                                                   "owner's instruction, to cancel")
    revise_tasks: list[TaskRevisionOut] = Field(description="Tasks that haven't started and only need a new direction")
    new_tasks: list[TaskDraft] = Field(description="Only the tasks the new direction needs")
    withdraw_requests: list[RequestWithdrawOut] = Field(description="Waiting requests that go against the owner's "
                                                                    "instruction or are no longer useful")
    spec_clarification: Detail() | None = Field(default=None, description=(
        "Only while the build has a spec (see 'The build') and the owner's answer or instruction changes what it "
        "builds: what the engineers must now know from the owner's words, in plain words. The server adds it to the "
        "spec's clarifications. Otherwise null"))


class InboxCleanupOut(BaseModel):
    merges: list[RequestMergeOut] = Field(description="Only groups of requests that ask for the same decision in effect")
    withdrawals: list[RequestWithdrawOut] = Field(description="Only requests the final decision made unneeded")
