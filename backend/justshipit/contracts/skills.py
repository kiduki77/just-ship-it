"""Skill Lifecycle contract. Skills use the open Agent Skills format (SKILL.md + resources)."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SkillKind(StrEnum):
    KNOWLEDGE = "knowledge"      # methodology, templates — read-only
    WORKFLOW = "workflow"        # multi-step procedure
    EXECUTABLE = "executable"    # scripts / automation — sandboxed, strong approval


class SkillStatus(StrEnum):
    GAP_DETECTED = "GAP_DETECTED"
    PROPOSED = "PROPOSED"
    CANDIDATE = "CANDIDATE"      # generated, not evaluated
    EVALUATING = "EVALUATING"
    EVAL_FAILED = "EVAL_FAILED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    REJECTED = "REJECTED"


SKILL_ACTIVATION_ACTION = {
    SkillKind.KNOWLEDGE: "skill.activate_knowledge",
    SkillKind.WORKFLOW: "skill.activate_workflow",
    SkillKind.EXECUTABLE: "skill.activate_executable",
}


class SkillProposalOut(BaseModel):
    name: str = Field(description="kebab-case")
    kind: SkillKind
    target_agents: list[str]
    problem: str = Field(description="which difficulty of which recurring work it solves")
    expected_benefit: str
    risks: list[str]
    evaluation_plan: str


class SkillFileOut(BaseModel):
    path: str = Field(description="path relative to the skill folder, e.g. SKILL.md, templates/matrix.yaml")
    content: str


class SkillPackageOut(BaseModel):
    name: str
    description: str = Field(description="SKILL.md frontmatter description, including when to use it")
    files: list[SkillFileOut]
    eval_cases: list[SkillEvalCaseOut]


class SkillEvalCaseOut(BaseModel):
    task: str
    rubric: list[str]


SkillPackageOut.model_rebuild()


class SkillPortfolioOut(BaseModel):
    proposals: list[SkillProposalOut] = Field(description="the 3 most valuable at most; leave out one-off gaps that didn't recur")


class SkillSafetyOut(BaseModel):
    safe: bool = Field(description="true when it doesn't bypass permission or approval rules, weaken the evidence rules, push toward external actions, or inject prompts")
    issues: list[str]
