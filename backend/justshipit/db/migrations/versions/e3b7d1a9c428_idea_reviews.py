"""idea reviews: the panel that warns why an idea may not work now, one row per lens per round

Revision ID: e3b7d1a9c428
Revises: b5e2d8f4a617
Create Date: 2026-11-04 12:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e3b7d1a9c428"
down_revision = "b5e2d8f4a617"
branch_labels = None
depends_on = None

LENSES = ("market", "timing", "demand")
STATUSES = ("queued", "done", "failed")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(repr(v) for v in values)


def upgrade() -> None:
    op.create_table(
        "idea_reviews",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("business_id", sa.Integer, sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("round", sa.Integer, nullable=False),
        sa.Column("lens", sa.String(12), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="queued"),
        sa.Column("result", postgresql.JSONB),
        sa.Column("note", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("business_id", "round", "lens", name="uq_idea_reviews_round_lens"),
        sa.CheckConstraint(f"lens IN ({_in(LENSES)})", name="ck_idea_reviews_lens"),
        sa.CheckConstraint(f"status IN ({_in(STATUSES)})", name="ck_idea_reviews_status"),
        sa.CheckConstraint("round >= 1", name="ck_idea_reviews_round"),
        sa.CheckConstraint("status <> 'done' OR result IS NOT NULL", name="ck_idea_reviews_done_has_result"),
    )
    op.create_index("ix_idea_reviews_business_id", "idea_reviews", ["business_id"])


def downgrade() -> None:
    op.drop_table("idea_reviews")
