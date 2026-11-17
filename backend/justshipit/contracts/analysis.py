"""Data-analysis loop contracts: the analyst writes a script, the server fetches inputs and runs it offline."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class AnalysisAction(StrEnum):
    RUN = "run"        # fetch `inputs`, run `script`, show me the result
    FINISH = "finish"  # the last successful run is final; write up the result


class AnalysisInputOut(BaseModel):
    url: str = Field(description="An http(s) URL that downloads the file itself, local:<filename> for a file the owner "
                                 "uploaded, or ledger:<filename> for this project's evidence list or a task result")
    filename: str = Field(description="The file name to save it as under inputs/ (for example ltc_2024.xlsx)")


class AnalysisStepOut(BaseModel):
    reasoning: str = Field(description="What this step checks or calculates, and why (briefly)")
    action: AnalysisAction
    inputs: list[AnalysisInputOut] = Field(description="Files to fetch before this run. Don't list a file already "
                                                       "fetched")
    script: str = Field(description="The whole analysis.py to run when action=run. It reads inputs/ and always writes "
                                    "out/results.json. An empty string for finish")
