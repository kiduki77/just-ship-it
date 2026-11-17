"""The vocabulary of the work path (docs/design/work-path.md): where a decision card came from, what it holds up, what
answering does and what happens if nobody answers. The records (`request_links`, `review_rounds`, the columns of
`agent_requests`) store these values, and the CHECK constraints are rendered from the tables here, so a value or a
pair the database accepts is one this module names."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from justshipit.contracts.owner_text import OneLine


class HoldScope(StrEnum):
    NONE = "none"          # nothing waits, the card is still in the owner's list
    TASKS = "tasks"        # the tasks in its `holds` links
    PLANNING = "planning"  # the CEO's next plan step
    RUN = "run"            # the whole project run, what `blocking` always meant


HOLD_RANK = {HoldScope.NONE: 0, HoldScope.TASKS: 1, HoldScope.PLANNING: 2, HoldScope.RUN: 3}


def wider(a: HoldScope | str, b: HoldScope | str) -> HoldScope:
    """The scope two cards merged into one must keep: a merge never holds less than either card did."""
    return max(HoldScope(a), HoldScope(b), key=HOLD_RANK.__getitem__)


class Rel(StrEnum):
    RAISED_BY = "raised_by"      # what led to the card, one row per asker
    HOLDS = "holds"              # a task that waits on the card
    ANSWER_USE = "answer_use"    # what the agent declared answering does
    FOLLOWED = "followed"        # what actually ran once the card was decided


class TargetKind(StrEnum):
    TASK = "task"
    ROUND = "round"              # a `review_rounds` row
    DECISION = "decision"
    CHAT_MESSAGE = "chat_message"
    DEV_JOB = "dev_job"
    SPEC = "spec"
    PLAN = "plan"
    BET = "bet"
    IDEA = "idea"
    SKILL = "skill"
    JOB = "job"
    REQUEST = "request"          # `re_request_of`: the card this one asks again
    PROJECT = "project"          # `replans` and `followed`: the project's plan
    CALL = "call"                # an `llm_calls` row, the one `ship work-path backfill --from-calls` matched


class HoldPhase(StrEnum):
    START = "start"              # the task may not begin
    FINISH = "finish"            # its result waits, today's APPROVAL_REQUIRED


class LinkSource(StrEnum):
    AGENT = "agent"
    SERVER = "server"
    BACKFILL = "backfill"


class Known(StrEnum):
    RECORDED = "recorded"        # written when the card was filed
    BACKFILLED = "backfilled"    # read from what the old row recorded
    MATCHED = "matched"          # found by comparing the card to the call that wrote it


class ReviewKind(StrEnum):
    PLAN = "plan"
    REVIEW = "review"
    REALIGN = "realign"
    DECISION = "decision"


class AskReason(StrEnum):
    GOAL_UNCLEAR = "GOAL_UNCLEAR"
    INPUT_MISSING = "INPUT_MISSING"
    STEP_NOT_RUN = "STEP_NOT_RUN"
    RESULT_NEEDS_CALL = "RESULT_NEEDS_CALL"
    EVIDENCE_CONFLICTS = "EVIDENCE_CONFLICTS"
    NEEDS_PERMISSION = "NEEDS_PERMISSION"
    SPEND = "SPEND"
    BUILD_NEEDED = "BUILD_NEEDED"
    SPEC_UNCLEAR = "SPEC_UNCLEAR"
    FOLLOWS_DECISION = "FOLLOWS_DECISION"


class ExpiryPolicy(StrEnum):
    CLOSE = "close"
    DEFAULT = "default"
    KEEP_WAITING = "keep_waiting"


ALLOWED_PAIRS: dict[Rel, frozenset[TargetKind]] = {
    Rel.RAISED_BY: frozenset({
        TargetKind.TASK, TargetKind.ROUND, TargetKind.DECISION, TargetKind.CHAT_MESSAGE, TargetKind.DEV_JOB,
        TargetKind.SPEC, TargetKind.PLAN, TargetKind.BET, TargetKind.IDEA, TargetKind.SKILL, TargetKind.JOB,
        TargetKind.REQUEST, TargetKind.CALL}),
    Rel.HOLDS: frozenset({TargetKind.TASK, TargetKind.DEV_JOB, TargetKind.SPEC, TargetKind.PLAN}),
    Rel.ANSWER_USE: frozenset({TargetKind.TASK, TargetKind.PROJECT}),
    Rel.FOLLOWED: frozenset({TargetKind.TASK, TargetKind.JOB, TargetKind.PROJECT, TargetKind.REQUEST}),
}


def values(enum: type[StrEnum]) -> str:
    """The members of an enum as a SQL list, for a CHECK `col IN (...)`."""
    return ", ".join(f"'{m.value}'" for m in enum)


def pair_check(rel: str = "rel", target: str = "target_kind") -> str:
    """The `(rel, target_kind)` CHECK expression of `request_links`, rendered from ALLOWED_PAIRS."""
    return " OR ".join(
        f"({rel} = '{r.value}' AND {target} IN ({', '.join(f"'{k.value}'" for k in sorted(kinds))}))"
        for r, kinds in ALLOWED_PAIRS.items())


# ----------------------------------------------------------------------------- the request contract (design 2)

class HoldKind(StrEnum):
    NOTHING = "nothing"
    THIS_TASK = "this_task"          # your own task's result waits for the answer
    MY_DEPENDENTS = "my_dependents"  # the tasks planned to start after yours wait
    PLANNING = "planning"            # the CEO's next plan step waits; work already started goes on
    TASKS = "tasks"                  # the CEO names the planned tasks that wait
    RUN = "run"                      # the whole project waits


HOLD_SCOPE: dict[HoldKind, HoldScope] = {
    HoldKind.NOTHING: HoldScope.NONE, HoldKind.THIS_TASK: HoldScope.TASKS, HoldKind.MY_DEPENDENTS: HoldScope.TASKS,
    HoldKind.TASKS: HoldScope.TASKS, HoldKind.PLANNING: HoldScope.PLANNING, HoldKind.RUN: HoldScope.RUN}


class AnswerEffect(StrEnum):
    INFORMS = "informs"   # the agents read the answer
    REPLANS = "replans"   # the CEO re-plans the tasks that have not started
    FEEDS = "feeds"       # the answer goes into the named tasks' prompts


class FollowedEffect(StrEnum):
    """What deciding a card set going, as its `followed` links record it: what the answer ran (the declared answer uses
    and the server's own work), and what the expiry rule did when nobody answered. Each names its target kind."""
    INFORMS = "informs"        # project: the answer is where every agent of the project reads it
    REPLANS = "replans"        # job: the CEO's re-plan of the work that has not started
    FEEDS = "feeds"            # task: the answer is in that task's own prompt
    QUEUED = "queued"          # job: the server's work for the decision (`requests._effect` and the flows it routes)
    RESUMES = "resumes"        # job: the waiting run's resume
    DEFAULTED = "defaulted"    # request (the card): nobody answered, the team went with the recommended option
    CLOSED = "closed"          # request (the card): nobody answered, it closed
    CANCELLED = "cancelled"    # task: it could not start while the card was open, and was cancelled when it closed
    REJECTED = "rejected"      # task: its result waited on the card, and was rejected when it closed


class WhyHuman(StrEnum):
    ACCOUNT_BOUND = "account_bound"  # it needs the owner's own account, device or identity
    TERMS_BOUND = "terms_bound"      # a site's terms forbid a program doing it
    DECISION = "decision"            # it is a judgment only the owner may make


class RefKind(StrEnum):
    EVIDENCE = "evidence"
    TASK = "task"
    REQUEST = "request"


_REF = re.compile(r"(EV|T|REQ)-(\d{1,9})", re.IGNORECASE)
_REF_KIND = {"EV": RefKind.EVIDENCE, "T": RefKind.TASK, "REQ": RefKind.REQUEST}


@dataclass(frozen=True)
class Ref:
    """A record a card points at, as the agent wrote it: `EV-14`, `T-41` or `REQ-6` carry the id, anything else is a
    task key the server looks up in the card's own project."""
    kind: RefKind
    id: int | None = None
    key: str | None = None

    @classmethod
    def parse(cls, text: str) -> Ref:
        text = text.strip()
        if m := _REF.fullmatch(text):
            return cls(_REF_KIND[m.group(1).upper()], int(m.group(2)))
        return cls(RefKind.TASK, key=text)


class HoldDecl(BaseModel):
    kind: HoldKind = Field(description=(
        "What waits for the owner's answer. nothing: no work waits, the card is only in the owner's list. this_task: "
        "your own task's result waits (a task agent only). my_dependents: the tasks planned to start after yours wait "
        "(a task agent only; the server finds them). planning: the Project Manager's next plan step waits, work already started "
        "goes on. tasks: the Project Manager names the planned tasks that wait, in task_keys. run: the whole project waits, only "
        "for a Project Manager QUESTION whose reason is GOAL_UNCLEAR. A hold your role or the request's type may not take is "
        "refused"))
    task_keys: list[str] = Field(description=(
        "For kind tasks, the keys of the tasks that wait, as the plan shows them. Each must be a task of this project "
        "that is not finished and that you do not depend on. An empty array for any other kind"))


class AnswerUse(BaseModel):
    effect: AnswerEffect = Field(description=(
        "What answering does. informs: the agents read the answer. replans: the Project Manager re-plans the tasks that have not "
        "started. feeds: the answer goes into the prompts of the tasks in task_keys"))
    option: str | None = Field(description=(
        "The option this applies to, exactly as the request's options name it. null when it applies to any answer"))
    task_keys: list[str] = Field(description="For feeds, the keys of the tasks the answer goes into. An empty array "
                                             "otherwise")


class ExpiryDecl(BaseModel):
    policy: ExpiryPolicy = Field(description=(
        "What happens when nobody answers before the card expires. close: the card closes and the work it holds is "
        "cancelled, the Project Manager re-plans without it. default: the team goes on with `option`, recorded as the default and "
        "never as the owner's answer; only for a question, and only with its recommended option. keep_waiting: it "
        "waits for the owner with no end; only when it holds nothing. An approval, anything that spends money and "
        "anything sent outside only close"))
    option: str | None = Field(description="For default, the recommended option, exactly as written. null otherwise")


OWNER_WORK_MINUTES = 600


class OwnerWork(BaseModel):
    """Why a card asks the owner to do something by hand. A card asking the owner to do work a program could do is not
    filed: what a program can do is the company's work."""
    minutes: int = Field(ge=1, le=OWNER_WORK_MINUTES, description=(
        f"How long it takes the owner, in minutes, from 1 to {OWNER_WORK_MINUTES}"))
    why_human: WhyHuman = Field(description=(
        "Why only the owner can do it. account_bound: it needs the owner's own account, device or identity. "
        "terms_bound: a site's terms forbid a program doing it. decision: it is a judgment only the owner may make"))
    if_skipped: OneLine(120) = Field(description="What is lost if the owner skips it, in one line of up to 120 "
                                                 "characters")


MAX_REFS = 5
MAX_ANSWER_USES = 4


class RequestContext(BaseModel):
    """Why the card is asked, what waits on it, what answering does and what happens if nobody answers. The server
    records where it came from; never say that here."""
    reason: AskReason = Field(description=(
        "Why the owner is asked. GOAL_UNCLEAR: the goal can be read more than one way. INPUT_MISSING: work needs "
        "something only the owner has. STEP_NOT_RUN: a step this needs was planned but never ran. RESULT_NEEDS_CALL: a "
        "result is in and the next step is the owner's call. EVIDENCE_CONFLICTS: the evidence points both ways. "
        "NEEDS_PERMISSION: an action needs the owner's yes. SPEND: it spends money. BUILD_NEEDED: software must be "
        "built. SPEC_UNCLEAR: the spec can be read more than one way. FOLLOWS_DECISION: it follows from a decision "
        "already made"))
    refs: list[str] = Field(max_length=MAX_REFS, description=(
        f"Up to {MAX_REFS} records that show why it is asked: evidence (EV-14), tasks (T-41 or a task key), earlier "
        "requests (REQ-6). Each must be a record of this project. Empty only when the reason is GOAL_UNCLEAR"))
    note: OneLine(120) = Field(description="The reason in your own words, one line of up to 120 characters. Plain "
                                           "text, no links")
    holds: HoldDecl
    on_answer: list[AnswerUse] = Field(max_length=MAX_ANSWER_USES, description=(
        f"What answering does, up to {MAX_ANSWER_USES} items, one per option when options do different things. An "
        "empty array when answering changes no work"))
    if_unanswered: ExpiryDecl
    owner_work: OwnerWork | None = Field(description=(
        "When the card asks the owner to do something by hand (a post, a message, a payment, a check, steps to "
        "follow), why only the owner can do it. A card that asks for the owner's hands without it is not filed. null "
        "when the owner only decides"))

    @field_validator("refs", mode="after")
    @classmethod
    def _refs_are_plain(cls, v: list[str]) -> list[str]:
        refs = [r.strip() for r in v]
        if any(not r or len(r) > 64 or any(c.isspace() for c in r) for r in refs):
            raise ValueError("a ref is EV-n, T-n, REQ-n or a task key")
        return refs

    def parsed_refs(self) -> list[Ref]:
        return [Ref.parse(r) for r in self.refs]
