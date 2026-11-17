"""A goal line's number, typed: what the dashboard counts the plan's goal against without reading the line's words.
The kickoff CEO writes it on a goal-and-deadline line, and a card that changes that line writes it on each option's
new line. `roadmap.checked_target` keeps it only when the line says its number."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Measure = Literal["contacted", "talked", "first_value", "repeat", "repeat_talked"]


class GoalTarget(BaseModel):
    number: int = Field(description="The number success is judged by, as the line writes it. For a range (150-250) "
                                    "the lower end")
    unit: str = Field(description="What the line counts, in its own word: signups, cafés, users")
    measure: Measure | None = Field(
        description="The step of the people list that counts it: contacted (reached), talked (spoken with), "
                    "first_value (used it and got what they came for), repeat (came back and used it again), "
                    "repeat_talked (came back, and the owner spoke with them). null when the people list cannot "
                    "count it: signups, sales, revenue, stars, time")
