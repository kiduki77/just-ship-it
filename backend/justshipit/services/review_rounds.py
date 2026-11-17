"""The CEO steps that can file cards, in order (`review_rounds`): what `Project.context["ceo_reviews"]` used to hold."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from justshipit.contracts.work_path import ReviewKind
from justshipit.db.models import ReviewRound


def add(s: Session, project_id: int, kind: ReviewKind, *, plan_round: int, assessment: str = "",
        action: str | None = None, unmet: list[str] | None = None, call_id: str | None = None) -> ReviewRound:
    """The next step of the project, numbered in the order the CEO took them."""
    seq = (s.scalar(select(func.max(ReviewRound.seq)).where(ReviewRound.project_id == project_id)) or 0) + 1
    row = ReviewRound(project_id=project_id, seq=seq, kind=kind.value, plan_round=plan_round, assessment=assessment,
                      action=action, unmet=unmet or [], call_id=call_id)
    s.add(row)
    s.flush()
    return row


def plan_step(s: Session, project_id: int) -> int | None:
    """The id of the project's plan step, which named its build."""
    return s.scalar(select(ReviewRound.id).where(ReviewRound.project_id == project_id,
                                                 ReviewRound.kind == ReviewKind.PLAN.value)
                    .order_by(ReviewRound.seq).limit(1))


def reviews(s: Session, project_id: int) -> list[ReviewRound]:
    return list(s.scalars(select(ReviewRound).where(ReviewRound.project_id == project_id,
                                                    ReviewRound.kind == ReviewKind.REVIEW.value)
                          .order_by(ReviewRound.seq)))
