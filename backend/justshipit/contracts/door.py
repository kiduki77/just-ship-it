"""What the CEO returns when it reads the decision rights of an approved brief."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DoorRuleOut(BaseModel):
    action: str = Field(description="An action kind from the list (internal.report_draft) or a family (internal.*). "
                                    "Never a name the list does not have")
    decider: Literal["owner", "project_manager"] = Field(
        description="owner: the owner decides (work the owner only gives an opinion on is owner too). project_manager: the "
                    "Project Manager decides and only tells the owner")
    line_id: str = Field(description="The id of the plan line this rule rests on. Only ids from the input list")
    reason: str = Field(description="One sentence on why that line means this rule")


class DoorRulesOut(BaseModel):
    rules: list[DoorRuleOut] = Field(description="Only rules the lines say. Invent nothing a line does not say. An "
                                                 "empty array when there are none")
