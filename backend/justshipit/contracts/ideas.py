"""What the CEO returns when it reads one idea the owner dropped in."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from justshipit.contracts.requests import DecisionCard

# `discard` is the owner's word alone: the model can only propose it, so the schema never offers it.
Disposition = Literal["apply", "bet_candidate", "merge", "reshape", "brief_change", "park", "discard_proposed"]


class ReshapedItem(BaseModel):
    kind: Literal["apply", "bet_candidate"] = Field(
        description="apply: a task in a running experiment. bet_candidate: a new experiment candidate")
    title: str = Field(description="The smaller piece's title, short")
    detail: str = Field(description="For apply the task's goal, for bet_candidate the experiment's pitch. One or two "
                                    "sentences")
    bet_id: int | None = Field(description="For apply, the number of the running experiment that takes the task (3 for "
                                           "BET-3). Otherwise null")


class IdeaRefs(BaseModel):
    brief_lines: list[str] = Field(description="The ids of the plan lines this disposition rests on, only ids from the "
                                               "input. An empty list when there are none")
    red_line_conflicts: list[str] = Field(
        description="The ids of the off-limits lines this idea breaks or touches, only ids from the input. An empty list "
                    "when there are none")
    bet_id: int | None = Field(description="For apply, the number of the running experiment that takes the task. "
                                           "Otherwise null")
    task_title: str | None = Field(description="For apply, the title of the task to create. Otherwise null")
    task_detail: str | None = Field(description="For apply, the task's goal in one or two sentences. Otherwise null")
    task_criteria: list[str] = Field(description="For apply, 1-3 acceptance criteria. Otherwise an empty list")
    task_owner: str | None = Field(description="For apply, the id of the agent who owns the task, from the input list. "
                                               "Otherwise null")
    merge_into_bet_id: int | None = Field(description="For merge, the number of the experiment to merge into. Otherwise "
                                                      "null")
    bet_title: str | None = Field(description="For bet_candidate, the new experiment's title. Otherwise null")
    bet_pitch: str | None = Field(description="For bet_candidate, the experiment's pitch: who it is for and what it "
                                              "finds out. Otherwise null")
    reshaped: list[ReshapedItem] = Field(description="For reshape, 1-3 items. Otherwise an empty list")


class IdeaTriageOut(BaseModel):
    disposition: Disposition = Field(
        description="apply: add it to current work now. bet_candidate: a new experiment candidate. merge: fold it into "
                    "an experiment that exists. reshape: split it into smaller pieces. brief_change: propose a plan "
                    "change. park: keep it for later. discard_proposed: suggest setting it aside (only the owner "
                    "discards)")
    reason: str = Field(description="Why this disposition, in one or two sentences in the output language. The owner "
                                    "reads it")
    refs: IdeaRefs
    card: DecisionCard | None = Field(description="For brief_change, the decision card the owner reads. Otherwise null")
