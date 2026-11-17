"""Agent Request Protocol: the only channel through which agents talk to the human owner."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

import re

from pydantic import BaseModel, Field, field_validator

from justshipit.contracts.goal import GoalTarget
from justshipit.contracts.owner_text import Body, Detail, OneLine
from justshipit.contracts.post_card import PostCardDraft
from justshipit.contracts.work_path import HoldKind, RequestContext
from justshipit.i18n import LANGS
from justshipit.i18n.match import card_words


class RequestType(StrEnum):
    QUESTION = "QUESTION"            # needs an answer before continuing
    APPROVAL = "APPROVAL"            # permission for a concrete action
    PROPOSAL = "PROPOSAL"            # strategic idea: adopt / defer / reject
    RESOURCE = "RESOURCE"            # budget, paid data, people
    SKILL = "SKILL"                  # create / activate a skill
    ESCALATION = "ESCALATION"        # agents cannot resolve (conflicting evidence, etc.)
    DEVELOPMENT = "DEVELOPMENT"      # software must be built
    CLARIFICATION = "CLARIFICATION"  # engineering -> product: spec is ambiguous


class RequestStatus(StrEnum):
    PENDING = "PENDING"
    AUTO_APPROVED = "AUTO_APPROVED"
    APPROVED = "APPROVED"
    ANSWERED = "ANSWERED"
    DEFERRED = "DEFERRED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    DENIED_BY_POLICY = "DENIED_BY_POLICY"


RESOLVED_STATUSES = {RequestStatus.AUTO_APPROVED, RequestStatus.APPROVED, RequestStatus.ANSWERED,
                     RequestStatus.DEFERRED, RequestStatus.REJECTED, RequestStatus.EXPIRED,
                     RequestStatus.CANCELLED, RequestStatus.DENIED_BY_POLICY}
POSITIVE_STATUSES = {RequestStatus.AUTO_APPROVED, RequestStatus.APPROVED, RequestStatus.ANSWERED}


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PolicyMode(StrEnum):
    AUTO = "auto"
    APPROVE = "approve"
    STRONG_APPROVE = "strong_approve"
    DENY = "deny"


POLICY_ORDER = (PolicyMode.AUTO, PolicyMode.APPROVE, PolicyMode.STRONG_APPROVE, PolicyMode.DENY)


class HumanDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    DEFER = "defer"
    ANSWER = "answer"


UNKNOWN_VALUES = {w for lang in LANGS for w in card_words.UNKNOWN[lang]}
_BADGE = re.compile(r"\s*[(\[](?:" + "|".join(re.escape(w) for lang in LANGS for w in card_words.BADGE[lang])
                    + r")[)\]]\s*$", re.IGNORECASE)


class CardMetric(BaseModel):
    label: OneLine(30) = Field(description="The number's name, up to 30 characters. E.g. spent this month")
    value: OneLine(40) = Field(description="The value with its unit, up to 40 characters. E.g. 12.4 USD. Leave the "
                                           "number out when its value is unknown")
    basis: Detail(160) = Field(description="What the number rests on: an evidence id (EV-12) or the arithmetic. Say "
                                           "so when it is an estimate")

    @property
    def known(self) -> bool:
        return self.value.strip().lower() not in UNKNOWN_VALUES


class PlanLine(BaseModel):
    text: Detail(300) = Field(description="The plan line as it reads if the owner picks this option, written like the "
                                          "line it replaces")
    target: GoalTarget | None = Field(description="On a goal_deadline line, its main number typed, which the dashboard "
                                                  "counts. The number must be one the text says. null otherwise")


class CardOption(BaseModel):
    label: OneLine(30) = Field(description="The option's name: a short verb phrase of up to 30 characters. E.g. "
                                           "Post it, Don't post")
    verdict: HumanDecision = Field(
        description="What the owner decides by picking this option: approve, reject, defer, or answer (an answer to "
                    "a question). The answers to a QUESTION request are answer")
    summary: Detail(240) = Field(description="One or two sentences on what happens if the owner takes this path")
    evidence_ids: list[str] = Field(description="The evidence ids (EV-12) behind this option. An empty array when none")
    risks: list[OneLine(120)] = Field(description="This option's risks, one line each. An empty array when none")
    recommended: bool = Field(description="true for the option the Project Manager recommends. Only one is true")
    line: PlanLine | None = Field(
        default=None, description="When the card amends a plan line: that line as it reads after this option. null for "
                                  "an option that leaves the line as it is, and on a card that amends no line")

    @field_validator("label", mode="after")
    @classmethod
    def _no_badge_words(cls, v: str) -> str:
        return _BADGE.sub("", v).strip() or v


class DecisionCard(BaseModel):
    """The card the owner reads to decide in a few seconds. Like a report to a boss: the conclusion, what the owner
    does and one line of why come first; the rest is read only when the owner opens it. What approving really does,
    and whether it can be undone, the server shows separately."""
    headline: OneLine(70) = Field(
        description="The conclusion in one line, up to 70 characters: what the owner decides, as a question, or the "
                    "conclusion. E.g. Start writing the spec?")
    owner_does: OneLine(90) = Field(
        description="What the owner does, in one line of up to 90 characters. E.g. Read the post, approve it and post "
                    "it yourself (about 20 min)")
    why: OneLine(120) = Field(description="The reason in one line, up to 120 characters. E.g. It's the pet-owner channel "
                                          "the plan names first")
    metrics: list[CardMetric] = Field(description="Only the numbers the decision needs. Leave out a number you do not "
                                                  "know. An empty array when none")
    options: list[CardOption] = Field(
        description="The options. When the choice is only whether to approve, write two: approve and reject. The owner "
                    "edits a post on the card itself, so never write an option like 'edit, then post'")
    recommendation: Detail(600) = Field(description="The longer explanation the owner opens: the recommendation and "
                                                    "what backs it, in three or four sentences")
    reversible: bool = Field(description="true when approving can be undone. For an action the server knows, the "
                                         "server's judgment wins")
    amends: str | None = Field(
        default=None,
        description="When an option changes a said or confirmed line of the approved plan (the main number, the "
                    "deadline): that line's id as the plan shows it (l-...). The owner's pick rewrites the line to the "
                    "option's `line`, and the dashboard follows it. null when no plan line changes")

    @field_validator("options", mode="after")
    @classmethod
    def _distinct_labels(cls, v: list[CardOption]) -> list[CardOption]:
        """The owner picks an option by its label, so no two read the same after clipping."""
        seen: set[str] = set()
        for o in v:
            label, n = o.label, 2
            while label in seen:
                label, n = f"{o.label} ({n})", n + 1
            o.label = label
            seen.add(label)
        return v

    @field_validator("metrics", mode="after")
    @classmethod
    def _known_numbers(cls, v: list[CardMetric]) -> list[CardMetric]:
        """A number the agent does not know is shown only with the reason it is unknown."""
        return [m for m in v if m.known or m.basis.strip()]


HANDOUT_CHARS = 6000


class Handout(BaseModel):
    """Text the owner uses by hand, put on the to-do that approving the request makes, whole and ready to copy."""
    kind: Literal["message", "steps"] = Field(
        description="message: the exact message the owner sends from their own account (KakaoTalk, a text, an email). "
                    "steps: the steps the owner follows (install an app, fill in a profile)")
    to: str | None = Field(description="For a message, the one person it goes to, as the owner knows them: a message "
                                       "for several people is one request each. null for steps")
    body: str = Field(
        max_length=HANDOUT_CHARS,
        description="The whole text, ready to paste or follow, never a pointer to a document or a task. A message "
                    "leaves no [bracket] blank for the owner to fill: the owner sends it from their own account, so it "
                    "signs off without a name. It keeps one register and one voice (I, never switching to we) from "
                    "start to end, and claims no earlier talk with the person ('as we discussed') the owner's words do "
                    "not record. Steps leave in [brackets] only what the owner alone knows")


_CONTEXT = (
    "Why you ask, what waits on the answer, what answering does and what happens if nobody answers (the request "
    "contract). The server records where the request came from and checks every field: a hold your role or the "
    "request's type may not take, a ref that is not a record of this project, an unanswered approval that does "
    "anything but close, a default that is not the recommended option, or a request for the owner's hands without "
    "owner_work is refused and never reaches the owner")


class AgentRequestDraft(BaseModel):
    """A request to the owner. The server decides policy and the expiry date, never the agent. A draft the server builds
    leaves `context` empty: the row its call site names in ORIGIN_RULES gives the reason, the hold and the expiry
    rule."""
    type: RequestType
    title: OneLine(80, names=True) = Field(description="The title on the board, up to 80 characters: a noun phrase "
                                                       "for what is being decided, with no aside in parentheses")
    summary: Body() = Field(description="What is asked, read when opened: three or four sentences, conclusion first")
    rationale: Body() = Field(description="What backs it and how it came about, read when opened")
    options: list[str] = Field(description="The options. May be an empty array for an approval")
    recommended_option: str | None
    action_kind: str | None = Field(description="The action key in tool_policy, e.g. external.survey_distribute")
    action_payload_json: str | None = Field(
        default=None,
        description="What the outside action needs, as a JSON string. E.g. an email {\"to\":[..],\"subject\":..,"
                    "\"body\":..}, an event {\"title\":..,\"start\":\"2026-11-03T10:00\",\"duration_min\":60,"
                    "\"attendees\":[..]}. null when none")
    estimated_cost_usd: float | None
    risk_level: RiskLevel
    context: RequestContext | None = Field(default=None, description=_CONTEXT)
    card: DecisionCard | None = Field(
        default=None, description="The decision card the owner reads. Always filled for a request; null only for a "
                                  "request the server made")
    post_card: PostCardDraft | None = Field(
        default=None,
        description="The post card, when raising something the owner posts by hand under external.social_post, "
                    "external.landing_publish or external.other. null for any other request")
    handout: Handout | None = Field(
        default=None,
        description="The message the owner sends, or the steps the owner follows, by hand, when approving this makes "
                    "a to-do (external.other, external.interview_outreach). null otherwise")
    replaces: str | None = Field(
        default=None,
        description="The id of an earlier request (REQ-12) this one revises or raises again. The server then changes "
                    "that card, or the owner to-do its approval made, instead of making a new card. null for new work")
    owner_action: bool = Field(
        default=True,
        description="true when the owner has something to decide or do. false when it only records or reports "
                    "something already settled (nothing for the owner to do): it goes to the activity log, not onto "
                    "a decision card")

    @property
    def holds_work(self) -> bool:
        """Whether the agent declared that work waits on the answer."""
        return self.context is not None and self.context.holds.kind != HoldKind.NOTHING


class AgentAskDraft(AgentRequestDraft):
    """A request an agent writes: the contract is required, so a draft without it fails when the output is parsed."""
    context: RequestContext = Field(description=_CONTEXT)


SCREEN_NOTE_CHARS = 1000  # the console's note box stops at the same length


class PostEdit(BaseModel):
    """The post as the owner rewrote it in the decision card."""
    title: str | None = Field(default=None, max_length=300)
    body: str = Field(min_length=1, max_length=20000)


class ScreenNote(BaseModel):
    """The owner's words on one planned screen of a spec card."""
    screen: str = Field(min_length=1, max_length=64)  # a screen id of the card's commit
    note: str = Field(min_length=1, max_length=SCREEN_NOTE_CHARS)

    @field_validator("note", mode="before")
    @classmethod
    def _trimmed(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class HumanResponse(BaseModel):
    decision: HumanDecision
    answer: str | None = None
    note: str | None = None
    chosen_option: str | None = None
    post_edit: PostEdit | None = None  # the owner's own version of a draft post, used for the owner task
    # Scope narrowing: the human may grant less than requested.
    max_amount_usd: float | None = None
    max_uses: int | None = None
    confirm_text: str | None = None  # required for strong_approve: must equal the request id
    revision: int | None = None  # the card revision the owner read; a decision on an older one is refused
    screen_notes: list[ScreenNote] = []  # a spec card, with a decline: sends the spec back to be drawn again
    responded_by: str = "owner"
    responded_at: datetime | None = None
