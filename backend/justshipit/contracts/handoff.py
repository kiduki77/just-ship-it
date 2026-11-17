"""Spec-to-Code Handoff contract (handoff.yaml) and engineering job lifecycle."""
from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator

from justshipit.contracts.owner_text import OneLine


class SpecStatus(StrEnum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    SPEC_APPROVED = "SPEC_APPROVED"
    READY_FOR_DEV = "READY_FOR_DEV"
    IN_DEVELOPMENT = "IN_DEVELOPMENT"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    DELIVERED = "DELIVERED"        # PR (or local branch) produced
    ACCEPTED = "ACCEPTED"          # CPO confirmed acceptance criteria
    MERGED = "MERGED"              # owner strong-approved, branch merged
    REJECTED = "REJECTED"


class DevJobStatus(StrEnum):
    QUEUED = "QUEUED"
    INTAKE = "INTAKE"
    PLAN_REVIEW = "PLAN_REVIEW"
    IMPLEMENTING = "IMPLEMENTING"
    REVIEWING = "REVIEWING"
    QA = "QA"
    SHIPPING = "SHIPPING"
    DELIVERED = "DELIVERED"
    MERGED = "MERGED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    FAILED = "FAILED"
    DISCARDED = "DISCARDED"  # the owner threw the delivered result away for a new attempt


_J = DevJobStatus
# Where a dev job may go from each status. A run starts only from QUEUED or CLARIFICATION_REQUIRED. A delivered
# job has its branch, so it only moves on to MERGED, or to DISCARDED when the owner throws the result away: a
# failure after delivery is a note on the job, never FAILED, because FAILED and DISCARDED are what let a new
# attempt, a second branch for the same spec, start. A FAILED job that opened a PR goes to MERGED only when the
# forge says the owner merged it there; the worker's own merge runs on a DELIVERED job alone. A FAILED job whose run
# stopped while shipping goes back to SHIPPING when the owner asks to deliver it again (worker.deliver_again).
# A delivered job the owner turned down goes back to IMPLEMENTING for a fix round on its own branch, and the round
# ends DELIVERED either way: with the fixed branch, or, when it stops, with the branch as it was (engineering/revise.py).
DEV_JOB_NEXT: dict[DevJobStatus, frozenset[DevJobStatus]] = {
    _J.QUEUED: frozenset({_J.INTAKE, _J.FAILED}),
    _J.INTAKE: frozenset({_J.INTAKE, _J.CLARIFICATION_REQUIRED, _J.PLAN_REVIEW, _J.IMPLEMENTING, _J.FAILED}),
    _J.CLARIFICATION_REQUIRED: frozenset({_J.INTAKE, _J.QA, _J.FAILED}),
    _J.PLAN_REVIEW: frozenset({_J.PLAN_REVIEW, _J.IMPLEMENTING, _J.FAILED}),
    _J.IMPLEMENTING: frozenset({_J.IMPLEMENTING, _J.CLARIFICATION_REQUIRED, _J.QA, _J.FAILED, _J.DELIVERED}),
    _J.QA: frozenset({_J.QA, _J.REVIEWING, _J.FAILED, _J.DELIVERED}),
    _J.REVIEWING: frozenset({_J.REVIEWING, _J.QA, _J.SHIPPING, _J.FAILED, _J.DELIVERED}),
    _J.SHIPPING: frozenset({_J.DELIVERED, _J.FAILED}),
    _J.DELIVERED: frozenset({_J.DELIVERED, _J.MERGED, _J.DISCARDED, _J.IMPLEMENTING}),  # -> DELIVERED: a CPO verdict
    _J.MERGED: frozenset(),
    _J.FAILED: frozenset({_J.SHIPPING, _J.DISCARDED, _J.MERGED}),
    _J.DISCARDED: frozenset(),
}
# The action_params key of the question that asks the owner what a turned-down build should change: it names the job.
FIX_QUESTION_KEY = "fix_of_job"
# The action_params key of a question about a spec the owner answers before approving it (engineering/questions.py):
# the question's id in the spec (Q1, A1). The card also names the spec under spec_id.
SPEC_QUESTION_KEY = "spec_question"
# A spec with a job in one of these has its branch, so it gets no new attempt.
DEV_JOB_DELIVERED = frozenset({_J.DELIVERED, _J.MERGED})
# No run is working on a job in one of these.
DEV_JOB_SETTLED = DEV_JOB_DELIVERED | {_J.FAILED, _J.DISCARDED}


class HandoffSource(BaseModel):
    repository: str = "registry"
    path: str
    commit_sha: str = Field(description="Approved spec commit; the worker reads the spec at exactly this commit")


# A language as a BCP 47 tag (ko, en, pt-BR). It reaches a shell command line (the browser's --lang), so nothing else
# passes.
_LANGUAGE_TAG = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")


def language_tag(value: str | None) -> str | None:
    """`value` when it is a language tag, else None."""
    value = (value or "").strip()
    return value if _LANGUAGE_TAG.fullmatch(value) else None


# What git refuses in a branch name (git check-ref-format --branch): a leading dash, which a command line would read
# as an option, and the characters and sequences a revision is spelled with.
_BRANCH_BAD = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]|\.\.|@\{|//")


def branch_name(value: str) -> str | None:
    """`value` when git would take it as a branch name, else None. It reaches git as a bare revision."""
    ok = (isinstance(value, str) and 0 < len(value) <= 255 and value != "@" and not _BRANCH_BAD.search(value)
          and not value.startswith(("-", "/")) and not value.endswith(("/", "."))
          and not any(part.startswith(".") or part.endswith(".lock") for part in value.split("/")))
    return value if ok else None


# A Docker image reference (the grammar of distribution/reference): [registry[:port]/]path[:tag][@digest]. The host's
# daemon pulls it, so nothing that starts with a dash or holds a space or a shell character passes.
_HOST_PART = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_PATH_PART = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_IMAGE_REF = re.compile(rf"(?:{_HOST_PART}(?:\.{_HOST_PART})*(?::[0-9]+)?/)?{_PATH_PART}(?:/{_PATH_PART})*"
                        r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?"
                        r"(?:@[A-Za-z][A-Za-z0-9]*(?:[-_+.][A-Za-z][A-Za-z0-9]*)*:[0-9A-Fa-f]{32,})?")


def image_ref(value: str | None) -> str | None:
    """`value` when it is a Docker image reference, else None. Any registry passes: which ones to trust is the
    owner's call, not this check's."""
    value = value or ""
    return value if len(value) <= 255 and _IMAGE_REF.fullmatch(value) else None


class HandoffTarget(BaseModel):
    repository: str = Field(description="Directory name under products/")
    base_branch: str = "main"
    test_command: str | None = None
    test_image: str | None = Field(default=None, description="Docker image for sandboxed test runs, e.g. python:3.13-slim")
    # The language the product's users read (SpecDraftOut.product_language); None for specs from before it was asked.
    language: str | None = None

    @field_validator("language")
    @classmethod
    def _tag(cls, value: str | None) -> str | None:
        return language_tag(value)

    @field_validator("base_branch")
    @classmethod
    def _branch(cls, value: str) -> str:
        if branch_name(value) is None:
            raise ValueError("not a branch name git accepts")
        return value

    @field_validator("test_image")
    @classmethod
    def _image(cls, value: str | None) -> str | None:
        return image_ref(value)


class HandoffWorkflow(BaseModel):
    type: str = "feature"
    engine: str = "claude-code+pstack@pstack-claude"
    require_plan_review: bool = True
    require_tests: bool = True
    create_pull_request: bool = True
    # The spec has a design brief: the engineer builds to the company's UI standard, and the CPO's acceptance looks
    # at screens the worker captured. False for a product without a screen, and for specs from before briefs.
    screens: bool = False
    # The repository already held the product when the spec was written (the owner brought it, or an earlier version
    # was merged): the spec writer read that code, and the engineer changes it in place and keeps its look.
    existing_code: bool = False
    # The spec has screens.yaml (engineering/wireflow.py): each scripts/ui-states.txt line is named by a screen id
    # from it. False for specs from before planned screens, whose builds are never told to read the file.
    planned_screens: bool = False
    auto_merge: bool = False
    auto_deploy: bool = False


class HandoffApproval(BaseModel):
    spec_approved: bool = False
    implementation_authorized: bool = False
    deployment_requires_human: bool = True
    approval_request_id: str | None = None


class HandoffLimits(BaseModel):
    max_iterations: int = 3
    max_budget_usd: float = 10.0


class Handoff(BaseModel):
    spec_id: str
    project: str
    title: str
    version: int = 1
    source: HandoffSource
    target: HandoffTarget
    workflow: HandoffWorkflow = HandoffWorkflow()
    approval: HandoffApproval = HandoffApproval()
    limits: HandoffLimits = HandoffLimits()


# ---------- LLM-facing outputs ----------

class SpecDraftOut(BaseModel):
    title: str
    prd_markdown: str = Field(description="The problem, the target users, in and out of scope, success measures. What "
                                          "and why only, never how. The user stories go in user_stories, not here")
    user_stories: list[UserStoryOut] = Field(
        default=[], description="The user stories, most important first. Each is independently testable and "
                                "shippable: built alone, it still gives the user something whole. Exactly one P1 is "
                                "usual; P1 is the smallest version worth shipping")
    owner_data: str = Field(
        default="", description="The data a user brings to the product, shaped as the owner's own is: the file type, "
                                "the column names and value formats, a realistic number of rows, as the owner or the "
                                "research describe them. An empty string when the product takes no data")
    assumptions: list[str] = Field(
        default=[], description="Each informed guess you made where the owner did not say (a reasonable default: data "
                                "kept on the device, errors shown in plain words), one line each")
    clarifications: list[ClarifyOut] = Field(
        default=[], description="At most 3: only the decisions that change the scope or the user's experience a lot, "
                                "have several reasonable answers, and have no reasonable default. Mark each in the spec "
                                "text where it applies as [NEEDS CLARIFICATION Qn: the question], n from 1 in this "
                                "list's order. Guess everything else and put the guess in assumptions. Never ask what "
                                "the owner already decided or what an open decision already asks")
    constitution: list[PrincipleOut] = Field(
        default=[], description="Only when the prompt says the product has no constitution yet: the owner's own "
                                "constraints every version must keep (a budget, no sign-up, a language, a device, "
                                "something never to do), each citing where the owner said it. An empty list otherwise")
    constitution_changes: list[PrincipleChangeOut] = Field(
        default=[], description="When this spec cannot be built without changing a rule of the product's constitution, "
                                "the change you propose. The owner decides each one before the spec is approved. An "
                                "empty list when the spec keeps every rule")
    product_language: str | None = Field(
        default=None,
        description="The language the product's users read, as a BCP 47 tag (ko, en, ja). Take it from who the users "
                    "are and where (a café in Seoul: ko; a tool for US developers: en), not from the language this spec "
                    "is written in; when nothing says, the language of the owner's plan. The product's screens, copy, "
                    "README and messages are in it")
    requirements: list[RequirementOut]
    acceptance_criteria: list[str] = Field(description="Testable, in Given/When/Then form: features and data rules. "
                                                       "Screen states and the core task's step count go in "
                                                       "design_brief, and the server turns them into criteria")
    owner_trace: list[OwnerTraceOut] = Field(
        default=[], description="At least one entry per item in the owner's list: what this spec did with it")
    design_brief: DesignBriefOut | None = Field(
        default=None, description="Required when the product has a screen people use. null only when it has none "
                                  "(a CLI, a library)")
    design_notes_markdown: str
    suggested_target_repository: str = Field(description="The target repository's name under products/ (kebab-case)")
    suggested_test_command: str | None
    suggested_test_image: str | None = Field(description="A public Docker image to run the tests in, e.g. "
                                                         "python:3.13-slim, node:22")
    about: AboutOut | None = Field(default=None, description="What this version is, from the approved plan's lines")
    journeys: list[JourneyOut] = Field(default=[], description="1 to 3: how real people use this version. Not tests")

    @field_validator("product_language")
    @classmethod
    def _tag(cls, value: str | None) -> str | None:
        return language_tag(value)

    @field_validator("suggested_test_image")
    @classmethod
    def _image(cls, value: str | None) -> str | None:
        return image_ref(value)


class AboutOut(BaseModel):
    what: OneLine(140, names=True) = Field(description="What the product does, in one plain sentence")
    who: OneLine(140, names=True) = Field(description="Who it is for: a role and their situation")
    problem: OneLine(140, names=True) = Field(description="The problem it solves for them")


class JourneyStepOut(BaseModel):
    do: OneLine(100, names=True) = Field(description="What the person does, in their own words. Never a command, a "
                                                     "flag or a file format they would not know")
    why: OneLine(100, names=True) = Field(description="What the step is for, for them")
    see: OneLine(140, names=True) = Field(description="What they see when it worked, concrete enough to compare with "
                                                      "what the product really prints or shows")


class JourneyOut(BaseModel):
    title: OneLine(60, names=True) = Field(description="A few words")
    persona: OneLine(80, names=True) = Field(description="One person: a role and a moment, never a tester. E.g. "
                                                         "Mina, planning a habit-tracker app alone")
    why: OneLine(120, names=True) = Field(description="Why they came")
    brings: OneLine(120, names=True) = Field(description="What they have at the start, shaped like owner_data")
    success: OneLine(140, names=True) = Field(description="What they have when it worked")
    priority: Literal["P1", "P2"]
    steps: list[JourneyStepOut] = Field(description="2 to 8 steps, in order")


class ScenarioOut(BaseModel):
    given: str = Field(description="The state before, with data shaped like the owner's (owner_data)")
    when: str = Field(description="What the user does")
    then: str = Field(description="What the user sees or gets, concrete enough to check")
    guards: OneLine(80, names=True) = Field(
        default="", description="What this test catches, in one line the owner reads. E.g. the same list gives the "
                                "same spec every time")


class BlockKind(StrEnum):
    """The whole vocabulary of a planned screen's sketch. The schema's enum, the prompt's list (VOCAB in
    engineering/wireflow.py) and the console's renderer map all follow it."""
    NAV = "nav"
    TITLE = "title"
    TEXT = "text"
    NOTICE = "notice"
    ACTIONS = "actions"
    FIELD = "field"
    UPLOAD = "upload"
    LIST = "list"
    TABLE = "table"
    CHIPS = "chips"
    MEDIA = "media"


class BlockOut(BaseModel):
    kind: BlockKind
    text: str = ""
    items: list[str] = []


_KINDS = frozenset(k.value for k in BlockKind)
# What a sketch is clipped to. A frame's drawn height comes from these, never from what the writer sent.
SKETCH_BLOCKS, SKETCH_ITEMS, SKETCH_CHARS = 14, 8, 120


def _text(value: object) -> str:
    """A string, or an empty one. Never raises."""
    if not isinstance(value, str):
        return ""
    return value.encode("utf-8", "ignore").decode("utf-8")  # a lone surrogate cannot be written to the registry


def _fields(value: object) -> list[dict]:
    """The objects of a list the writer sent, as dicts; anything else is no object."""
    items = value if isinstance(value, list) else []
    return [x.model_dump() if isinstance(x, BaseModel) else x for x in items if isinstance(x, (dict, BaseModel))]


def _blocks(value: object) -> list[dict]:
    """Whatever the writer sent, as blocks. Never raises: an unknown kind is `text`, a non-string is dropped, and the
    text, the items and the list are clipped. A block left with no words is dropped. A sketch cannot fail a draft."""
    out = []
    for b in _fields(value):
        kind, items = b.get("kind"), b.get("items")
        block = {"kind": kind if isinstance(kind, str) and kind in _KINDS else BlockKind.TEXT.value,
                 "text": _text(b.get("text"))[:SKETCH_CHARS],
                 "items": [w[:SKETCH_CHARS] for x in (items if isinstance(items, list) else [])
                           if (w := _text(x))][:SKETCH_ITEMS]}
        if block["text"].strip() or block["items"]:
            out.append(block)
    return out[:SKETCH_BLOCKS]


def _steps(value: object) -> list[dict]:
    """Flow steps that name a screen; anything else is dropped. Never raises."""
    return [{"screen": screen, "do": _text(x.get("do"))} for x in _fields(value) if (screen := _text(x.get("screen")))]


Lenient = Annotated[str, BeforeValidator(_text)]


class FlowStepOut(BaseModel):
    screen: Lenient = Field(description="The key of a screen in design_brief.states")
    do: Lenient = Field(default="", description="What the user does there to reach the next screen, in a few words")


class UserStoryOut(BaseModel):
    title: str = Field(description="A few words")
    priority: Literal["P1", "P2", "P3"]
    story: str = Field(description="One plain sentence the owner reads on the spec card: who does what and gets what. "
                                   "No technical words")
    why_priority: str = Field(description="Why it has this priority, in one sentence")
    independent_test: str = Field(description="How this story alone is checked, and what it gives the user then")
    scenarios: list[ScenarioOut] = Field(description="Its acceptance scenarios on a normal day, Given/When/Then")
    empty_case: ScenarioOut = Field(description="The same story on the day nothing has happened yet: no data, every "
                                                "flag no, an empty list. The user still gets a result that says so")
    flow: Annotated[list[FlowStepOut], BeforeValidator(_steps)] = Field(
        default=[], description="For a P1 story, the screens its first scenario walks through, in order")
    empty_screen: Lenient = Field(default="", description="The key of the screen its empty_case ends on")


class ClarifyOptionOut(BaseModel):
    answer: str = Field(description="The answer as the owner picks it, at most 30 characters")
    means: str = Field(description="What it means for the product, in one sentence")


class ClarifyOut(BaseModel):
    question: str = Field(description="The question for the owner in plain words, at most 70 characters, no technical "
                                      "words. E.g. Who can open the app?")
    why_it_matters: str = Field(description="What changes in the product with the answer, in one sentence")
    options: list[ClarifyOptionOut] = Field(description="2 or 3 answers, the one you recommend first")
    why_recommended: str = Field(description="Why you recommend the first answer, in one line of at most 120 "
                                             "characters")


# The spec card shows each rule under the stories, so it is written in the register they are, not the requirements'.
_RULE_STYLE = ("the owner reads on the spec card, in the Output language rule's Style, like the stories: never the "
               "terse register of a requirement")


class PrincipleOut(BaseModel):
    rule: str = Field(description="What every version of the product must or must never do, in one sentence "
                                   f"{_RULE_STYLE}")
    why: str = Field(description="Why, in one sentence")
    source: str = Field(description="The id of what the owner said it in, exactly as 'What the owner decided' gives "
                                    "it (l-2d8eb1, DEC-3, REQ-40, D-1)")


class PrincipleChangeOut(BaseModel):
    principle_id: str | None = Field(description="The rule it changes as the constitution numbers it (C2), or null "
                                                 "for a new rule")
    rule: str = Field(description=f"The rule as it would read, in one sentence {_RULE_STYLE}")
    why: str = Field(description="Why this spec needs the change, in one sentence the owner reads")


class RequirementOut(BaseModel):
    id: str = Field(description="In the form REQ-1")
    statement: str = Field(description="What the product must do and why, in the user's terms. Never how: no "
                                       "framework, library, file or API name: those go in design_notes_markdown")
    priority: str = Field(description="must | should | could")
    rationale: str


class TraceDisposition(StrEnum):
    INCLUDED = "included"        # a requirement of this spec carries it
    DROPPED = "dropped"          # left out of this version, with the reason the owner reads on the spec card
    NOT_PRODUCT = "not_product"  # not something the product does (a budget, a schedule, the owner's own work)
    MISSED = "missed"            # server only: the spec did not say what it did with it


def _keys(value: object) -> list[str]:
    """The screen keys the writer cited; anything else is dropped. Never raises."""
    return [k for x in (value if isinstance(value, list) else []) if (k := _text(x).strip())]


class OwnerTraceOut(BaseModel):
    source: str = Field(description="The item's id exactly as the owner's list gives it, e.g. DEC-3, l-b166de, or "
                                    "a directive or answer id as the list writes it")
    says: str = Field(description="What the owner wanted, in one line, without changing its meaning. E.g. beans "
                                  "already ordered leave the banner until they arrive")
    disposition: TraceDisposition = Field(description="included | dropped | not_product. Never missed")
    requirement_ids: list[str] = Field(description="When included, the ids of this spec's requirements that build it "
                                                   "(REQ-1); otherwise an empty list")
    reason: str = Field(description="When dropped or not_product, one sentence of why, for the owner. When included, "
                                    "an empty string")
    screens: Annotated[list[str], BeforeValidator(_keys)] = Field(
        default=[], description="For a note on a planned screen: the keys of the screens you changed for it")


class ScreenStateOut(BaseModel):
    screen: str = Field(description="The screen's name, e.g. the first screen")
    state: str = Field(description="The state the screen is in: empty, loading, error, success, warning and the like")
    shows: str = Field(description="What the user sees in that state and can do right away. Give the screen's exact "
                                   "words, in the product's language")
    key: Lenient = Field(default="", description="A short lowercase ASCII name for this screen in this state, unique "
                                                 "in the list (report-empty)")
    sketch: Annotated[list[BlockOut], BeforeValidator(_blocks)] = Field(
        default=[], description="The screen in this state as blocks, top to bottom")


class DesignBriefOut(BaseModel):
    """What the engineer designs from: the business's users and their moment, not a component list. A state's sketch
    says what is on the screen, never how it looks."""
    users_and_context: str = Field(description="Who uses it, where, on what device and in what posture, under what "
                                               "time pressure. E.g. a one-person café owner checking on the counter "
                                               "laptop in under a minute before opening")
    primary_task: str = Field(description="The one core task the screen exists for")
    primary_task_steps: int = Field(description="The most clicks or entries from opening the app to finishing the core "
                                                "task")
    tone: str = Field(description="Three words for the feel, and the feel to avoid. E.g. calm, sure, quick / the "
                                  "stiffness of an admin system, playfulness")
    first_glance: str = Field(description="What the user must know first, ranked; no layout")
    states: list[ScreenStateOut] = Field(description="Every core screen's empty, error and success states, none left "
                                                     "out, and its loading state if it loads")


SpecDraftOut.model_rebuild()


class IntakeOut(BaseModel):
    clear_enough: bool
    blocking_questions: list[str] = Field(description="Only the questions that cannot be built without a business "
                                                      "assumption")
    assumptions_safe_to_make: list[str] = Field(description="What a technical default may settle")
    implementation_outline: str


class DevStepOut(BaseModel):
    status: str = Field(description="done | blocked | failed")
    summary: str
    files_changed: list[str]
    tests_run: list[str]
    decisions: list[str] = Field(description="The decisions taken without asking when a skill asked, and why")
    blockers: list[str]


class AcceptanceOut(BaseModel):
    accepted: bool
    criteria_results: list[CriterionResultOut]
    screens_seen: list[str] = Field(default=[], description="The file names of the screen captures you opened. An "
                                                            "empty list if you opened none")
    design_findings: list[str] = Field(
        default=[], description="Where the screens fall short of the design bar (design-brief.md, ui-standard.md), "
                                "one sentence each")
    unseen: list[str] = Field(
        default=[], description="The core screens or steps the acceptance criteria name that no capture shows, each "
                                "by its name in a few words, in the owner's words (the suggested plan, Today, "
                                "Settings). A captured screen is not unseen: open it. An empty list when the captures "
                                "show them all")
    sample_output: str | None = Field(
        default=None, description="When the captures show made-up data or a stand-in AI instead of a real run (read "
                                  "scripts/preview.sh: a stub model, fixtures, demo data), say which in one sentence. "
                                  "null when they show the product working for real")
    built: list[str] = Field(
        default=[], description="What the branch really does, one line each, for every feature the spec or the owner's "
                                "notes name, as the code shows it, with the file. Where it differs from the spec or a "
                                "note, say so in that line")
    use: UseOut | None = Field(default=None, description="How people can use the product once it is merged")
    core_flow: CoreFlowOut | None = Field(
        default=None, description="Your reading of the sample inputs the core daily flow ran on (the prompt's Core "
                                  "daily flow section). The worker turns it and the run into one criterion")
    stories: list[StoryCheckOut] = Field(
        default=[], description="One entry per user story in `.ship/spec/stories.yaml`: whether the branch's code "
                                "builds it. An empty list when the spec has no stories file")
    beyond_spec: list[str] = Field(
        default=[], description="What the branch adds that no user story or requirement asks for, one line each with "
                                "the file, in the owner's words. An empty list when it adds nothing beyond the spec")
    screens: list[ScreenCheckOut] = Field(
        default=[], description="One entry per planned screen that has a capture, by the id the prompt lists. An "
                                "empty list when the prompt lists no planned screens")
    journeys: list[StepVerdictOut] = Field(
        default=[], description="One entry per user journey step the prompt's 'User journeys, as run' section shows a "
                                "capture for, by its id. An empty list when that section shows none")
    notes: str


class StepVerdictOut(BaseModel):
    step_id: str = Field(description="The step's id exactly as the prompt gives it (JN1.2)")
    verdict: Literal["passed", "differs"] = Field(description="passed when the capture shows what the step's 'see' "
                                                              "says, differs when it does not")
    fault: Literal["product", "scenario", "environment"] | None = Field(
        default=None, description="null when passed. product: the build does not do it. scenario: the step asks for "
                                  "something the approved spec never promised. environment: the sandbox lacked "
                                  "something the product needs (name it in need)")
    need: str = Field(default="", description="For environment: the name from use.needs the step lacked, exactly as "
                                              "you wrote it there. Empty otherwise")
    looks_like: OneLine(120, names=True) = Field(description="What the capture shows, in one line the owner compares "
                                                             "with their own run")
    hint: OneLine(120, names=True) = Field(default="", description="What the owner should know when their run differs "
                                                                   "(If you see 'command not found', ...). Empty when "
                                                                   "nothing")


class StoryCheckOut(BaseModel):
    story_id: str = Field(description="The story's id as stories.yaml gives it (US1)")
    built: Literal["yes", "partly", "no"] = Field(description="yes when the code does the whole story, partly when "
                                                              "some of it, no when none")
    evidence: str = Field(description="The files that do it, or what is missing, in one sentence in the owner's "
                                      "language")


class ScreenCheckOut(BaseModel):
    screen_id: str = Field(description="The planned screen's id exactly as the prompt lists it")
    built: Literal["yes", "partly", "no", "unseen"] = Field(
        description="yes when the capture shows what the plan sketched, partly when some of it, no when none, unseen "
                    "when you could not tell")
    differs: str = Field(description="Where the capture differs from the planned screen, in one sentence in the "
                                     "owner's language; an empty string when it does not")


class CoreFlowOut(BaseModel):
    realistic: bool = Field(description="true only when the sample inputs in fixtures/core-flow/ are shaped like the "
                                        "owner's real data as the spec describes it: the same column names, value "
                                        "formats and file type the owner's own files have")
    quiet_day: bool = Field(description="true only when one sample input is the day nothing has happened yet (no "
                                        "events: every flag no, an empty list) and the run log shows the user got a "
                                        "result for it, not an error or a step they cannot get past")
    evidence: str = Field(description="What you read to judge it, in one or two sentences in the owner's language: the "
                                      "files and what they hold")


class ProductNeedOut(BaseModel):
    name: str = Field(description="A key, account or paid service the product itself needs, as its README or code "
                                  "names it (AI_API_KEY, a Resend account)")
    what: str = Field(description="What it is and where the owner gets it, in one sentence")
    required: bool = Field(description="true when the product does not start, or its core feature fails, without it")
    without: str = Field(description="What happens without it, in one sentence")


class HostOptionOut(BaseModel):
    host: str = Field(description="Where to host it (Cloudflare Pages, Render, Fly.io)")
    cost: str = Field(description="What it costs a month for this product, as far as you know (Free for a static "
                                  "site, About $7 a month). It is an estimate the owner checks")
    steps: list[str] = Field(description="The owner's steps in order, with the exact commands where there are any")


class UseOut(BaseModel):
    """How the merged product gets in front of people, read from the branch's own files (README, package.json,
    scripts/), never from the spec."""
    local_steps: list[str] = Field(description="The exact commands that run it on the owner's computer, from its "
                                               "folder, in order. An empty list when it cannot run there")
    local_address: str | None = Field(description="The address to open after the last step, or null when there is "
                                                  "none (a command-line tool)")
    needs: list[ProductNeedOut] = Field(description="Every key, account or paid service the product needs to run "
                                                    "for real. An empty list when it needs none")
    hosting: list[HostOptionOut] = Field(description="Up to two places to host it so other people can open it. An "
                                                     "empty list when it is not something people open")
    recommend: Literal["local", "host"] = Field(description="local when only the owner needs to open it for now, "
                                                        "host when the plan has other people opening it")
    why: str = Field(description="Why that one, in one sentence the owner reads")
    reach: str | None = Field(default=None, description="Who other than the owner must open the product, and how, as "
                                                        "the spec and the messages already prepared for people promise "
                                                        "it, in a few words (the 8 stores, each through its own upload "
                                                        "link). null when only the owner opens it")


class CriterionResultOut(BaseModel):
    criterion: str
    met: bool
    evidence: str
    about: Literal["spec", "rule", "asked"] = Field(
        default="spec", description="rule for an entry about a rule of `.ship/spec/constitution.md`, asked for a point "
                                    "the owner asked this round to change, spec for every other")


UseOut.model_rebuild()
AcceptanceOut.model_rebuild()
AcceptanceOut.model_rebuild()


class CodeFindingOut(BaseModel):
    severity: str = Field(description="blocking | major | minor")
    file: str
    location: str | None
    issue: str
    suggestion: str


class CodeReviewOut(BaseModel):
    verdict: str = Field(description="pass | fix_required")
    summary: str
    spec_coverage: list[str] = Field(description="Whether each requirement is built (REQ-n: built / partly / "
                                                 "missing)")
    findings: list[CodeFindingOut]


class ClarificationAnswerOut(BaseModel):
    answers: list[ClarificationItemOut]


class ClarificationItemOut(BaseModel):
    question: str
    answer: str | None = Field(description="The answer, when the spec's scope can answer it; otherwise null")
    needs_owner: bool = Field(description="true when it needs a business call the owner must make")


ClarificationAnswerOut.model_rebuild()
