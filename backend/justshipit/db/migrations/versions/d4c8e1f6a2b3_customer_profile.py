"""customer description: customer_profile_values (a business's who buys, how they are reached and what the owner
already has, one row per value) and customer_profile_answers (the owner's words about it)

Operator: stop the worker, `alembic upgrade head`, start the worker. Hand-written; there is no autogenerate output to
review for dropped LangGraph checkpoint tables. It creates two empty tables and writes no data: a business with no live
rows has never been asked, and reads as unknown until the backfill or the owner fills it.

Revision ID: d4c8e1f6a2b3
Revises: 7c1e5a9d3f20
Create Date: 2026-11-13 15:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "d4c8e1f6a2b3"
down_revision = "7c1e5a9d3f20"
branch_labels = None
depends_on = None

_LINES = ("buyer", "reach", "has")
_PROVENANCES = ("said", "confirmed", "guess", "unknown")
_ASKED_IN = ("kickoff", "business")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.create_table(
        "customer_profile_values",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("line", sa.String(8), nullable=False),
        sa.Column("code", sa.String(40), nullable=True),
        sa.Column("sentence", sa.String(400), nullable=True),
        sa.Column("provenance", sa.String(9), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("brief_id", sa.Integer(), sa.ForeignKey("business_briefs.id"), nullable=True),
        sa.Column("written_by", sa.String(40), nullable=False),
        sa.Column("cause", sa.String(24), nullable=False),
        sa.Column("call_id", sa.String(40), nullable=True),
        sa.Column("written_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"line IN ({_in(_LINES)})", name="ck_customer_profile_values_line"),
        sa.CheckConstraint(f"provenance IN ({_in(_PROVENANCES)})", name="ck_customer_profile_values_provenance"),
        sa.CheckConstraint("code IS NULL OR sentence IS NULL", name="ck_customer_profile_values_code_or_sentence"),
        sa.CheckConstraint("(provenance = 'unknown') = (code IS NULL AND sentence IS NULL)",
                           name="ck_customer_profile_values_unknown_is_empty"),
    )
    op.create_index("ix_customer_profile_values_business_id", "customer_profile_values", ["business_id"])
    op.create_index("ix_customer_profile_values_live_code", "customer_profile_values",
                    ["business_id", "line", "code"], unique=True,
                    postgresql_where=sa.text("replaced_at IS NULL AND code IS NOT NULL"))
    op.create_index("ix_customer_profile_values_live_codeless", "customer_profile_values",
                    ["business_id", "line"], unique=True,
                    postgresql_where=sa.text("replaced_at IS NULL AND code IS NULL"))
    op.create_table(
        "customer_profile_answers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("text", sa.String(2000), nullable=True),
        sa.Column("asked_in", sa.String(8), nullable=False),
        sa.Column("kickoff_session_id", sa.Integer(), sa.ForeignKey("kickoff_sessions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"asked_in IN ({_in(_ASKED_IN)})", name="ck_customer_profile_answers_asked_in"),
    )
    op.create_index("ix_customer_profile_answers_business_id", "customer_profile_answers", ["business_id"])


def downgrade() -> None:
    op.drop_table("customer_profile_answers")
    op.drop_table("customer_profile_values")
