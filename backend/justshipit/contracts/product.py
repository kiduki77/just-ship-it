"""A product the owner already has: the repo the company works on (`ExistingProduct`, kept on the business) and the
CPO's current-state report on it (`CurrentStateOut`), which the kickoff, the plan and every agent read."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from justshipit.contracts.owner_text import Detail, OneLine


class FeatureOut(BaseModel):
    name: OneLine(60, names=True) = Field(description="The feature as a user would name it, within 60 Korean characters")
    state: Literal["works", "partial", "missing"] = Field(
        description="works: the code does it end to end. partial: started or only part of it. missing: the README or "
                    "a screen promises it but no code does it")
    evidence: str = Field(description="The file you opened (one in files_read) and the function or line that shows it")


class FindingOut(BaseModel):
    text: Detail(240) = Field(description="One finding, within 240 Korean characters, in the owner's words")
    evidence: str = Field(description="The file you opened (one in files_read) and the function or line it rests on")


class TestsOut(BaseModel):
    state: Literal["strong", "some", "few", "none"] = Field(
        description="How well the tests cover the product, judged from reading them: strong, some, few or none")
    summary: Detail(300) = Field(description="How many there are, what they cover, how they run (command, CI), and "
                                             "what is not covered. You read the tests; you did not run them")
    evidence: str = Field(description="The test folder, CI file or script it rests on")


class ClaimOut(BaseModel):
    claim: OneLine(120, names=True) = Field(description="What the README says the product does, within 120 Korean "
                                                        "characters")
    verdict: Literal["holds", "partly", "not_found"] = Field(
        description="holds: the code does it. partly: only part of it. not_found: you found no code that does it")
    reality: Detail(240) = Field(description="What the code actually does, within 240 Korean characters")
    evidence: str = Field(description="The file you opened (one in files_read) that it rests on")


class CurrentStateOut(BaseModel):
    """The CPO's report on an imported product, read summary first: the conclusion, what it means for the owner,
    then the detail the console folds away."""
    summary: OneLine(160, names=True) = Field(
        description="The conclusion in one or two sentences, within 160 Korean characters: what the product is and "
                    "how far along it is. No file paths here")
    for_owner: Detail(400) = Field(
        description="What this means for the owner now, within 400 Korean characters: what is ready to put in "
                    "front of users, and the one or two things that stand in the way")
    what_it_does: Detail(400) = Field(description="What the product does for its user, within 400 Korean characters")
    features: list[FeatureOut] = Field(description="The main features, at most 12, most important first")
    stack: list[str] = Field(description="The tech stack: languages, frameworks, databases, how it runs. At most 10")
    tests: TestsOut
    risks: list[FindingOut] = Field(description="Quality problems and risks, worst first, at most 6")
    unfinished: list[FindingOut] = Field(description="What looks unfinished: TODOs, stubs, screens or commands that "
                                                     "are planned but not built. At most 6")
    readme_vs_reality: list[ClaimOut] = Field(description="The README's main claims checked against the code, at "
                                                          "most 8")
    files_read: list[str] = Field(description="Every file you opened, in the order you read them")

    @model_validator(mode="after")
    def _rests_on_what_was_read(self) -> CurrentStateOut:
        """Every item cites a file the CPO opened (or the README, whose top the repository map carries). An item that
        cites only a file name it saw in the map is a guess, and the company would cite it as a finding."""
        read = [f.strip().lower() for f in self.files_read if len(f.strip()) >= 3]

        def rests(evidence: str) -> bool:
            e = evidence.lower()
            return e.startswith("readme") or any(f in e for f in read)
        self.features = [x for x in self.features if rests(x.evidence)]
        self.risks = [x for x in self.risks if rests(x.evidence)]
        self.unfinished = [x for x in self.unfinished if rests(x.evidence)]
        self.readme_vs_reality = [x for x in self.readme_vs_reality if rests(x.evidence)]
        return self


ReportState = Literal["reading", "ready", "failed"]


class ExistingProduct(BaseModel):
    """What a business holds when the owner brought a product: the clone the company works on (products/<repo>),
    where it came from, and the CPO's report once written. Its presence is what tells every stage the product
    exists."""
    repo: str = Field(description="The folder under products/ the company works on")
    source: str = Field(description="Where the owner's copy is: a local path as given, or a URL")
    base_branch: str
    commit: str = Field(description="The commit the company started from")
    report: CurrentStateOut | None = None
    report_cost_usd: float | None = None
    report_failed: bool = False

    @property
    def report_state(self) -> ReportState:
        return "ready" if self.report else "failed" if self.report_failed else "reading"
