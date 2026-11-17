"""The request contract a test card carries (contracts/work_path.RequestContext) and the origin it is filed under. By
default the contract holds nothing, closes when nobody answers and says why only the owner can do the work, so it is
valid for any request type from any filer."""
from uuid import uuid4

from justshipit.contracts.origins import OriginKind, RoundOrigin, ServerOrigin, TaskOrigin
from justshipit.contracts.work_path import (
    AnswerUse, AskReason, ExpiryDecl, ExpiryPolicy, HoldDecl, HoldKind, OwnerWork, RequestContext, ReviewKind,
    WhyHuman,
)
from justshipit.db.models import Task
from justshipit.services import review_rounds


def contract(holds: HoldKind = HoldKind.NOTHING, *, reason: AskReason = AskReason.GOAL_UNCLEAR,
             refs: tuple[str, ...] = (), keys: tuple[str, ...] = (), expiry: ExpiryPolicy = ExpiryPolicy.CLOSE,
             default: str | None = None, on_answer: tuple[AnswerUse, ...] = (), owner_work: bool = True,
             note: str = "Why the owner is asked") -> RequestContext:
    return RequestContext(
        reason=reason, refs=list(refs), note=note, holds=HoldDecl(kind=holds, task_keys=list(keys)),
        on_answer=list(on_answer), if_unanswered=ExpiryDecl(policy=expiry, option=default),
        owner_work=OwnerWork(minutes=10, why_human=WhyHuman.ACCOUNT_BOUND, if_skipped="The post never goes up")
        if owner_work else None)


def by_project_manager(s, project_id: int | None) -> RoundOrigin | ServerOrigin:
    """The CEO's review step on the card's project, or its weekly review for a company-wide card."""
    if project_id is None:
        return ServerOrigin(OriginKind.PORTFOLIO, None)
    return RoundOrigin(review_rounds.add(s, project_id, ReviewKind.REVIEW, plan_round=1).id)


def by_task(s, project_id: int, *, status: str = "REVIEW", depends_on: tuple[int, ...] = ()) -> TaskOrigin:
    """A task of the project whose author files the card, as its review settles."""
    key = f"ask_{uuid4().hex[:8]}"
    t = Task(project_id=project_id, key=key, title=key, owner="research", objective="o", acceptance_criteria=["c"],
             status=status, depends_on=list(depends_on))
    s.add(t)
    s.flush()
    return TaskOrigin(t.id)
