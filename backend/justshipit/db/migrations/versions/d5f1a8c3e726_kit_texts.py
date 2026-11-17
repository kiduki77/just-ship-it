"""kit_texts: the texts the First 10 kit wrote for the owner to copy (docs/design/first-ten-kit.md, 2)

Operator: stop the worker before this runs, and read the autogenerate output of any later revision for dropped
LangGraph checkpoint tables. Hand-written. It creates one table and touches nothing else, the checkpoint tables
included. Writes no data: a business starts with no kit texts. The downgrade drops the table and the texts in it.

Revision ID: d5f1a8c3e726
Revises: c4a8e1d7b295
Create Date: 2026-11-16 14:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "d5f1a8c3e726"
down_revision = "c4a8e1d7b295"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kit_texts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("business_id", sa.Integer, sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("part", sa.String(10), nullable=False),
        sa.Column("slot", sa.String(20), nullable=False),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id")),
        sa.Column("lang", sa.String(5), nullable=False),
        sa.Column("body", sa.Text),
        sa.Column("refused", sa.String(40)),
        sa.Column("basis", JSONB, nullable=False),
        sa.Column("job_id", sa.Integer),
        sa.Column("call_id", sa.String(40)),
        sa.Column("made_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("replaced_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("part IN ('message', 'profile', 'invite')", name="ck_kit_texts_part"),
        sa.CheckConstraint("(part = 'message') = (person_id IS NOT NULL)", name="ck_kit_texts_person"),
        sa.CheckConstraint("(body IS NULL) <> (refused IS NULL)", name="ck_kit_texts_body_or_refused"),
    )
    op.create_index("uq_kit_texts_live", "kit_texts",
                    ["business_id", "part", "slot", sa.text("coalesce(person_id, 0)")], unique=True,
                    postgresql_where=sa.text("replaced_at IS NULL"))
    op.create_index("ix_kit_texts_person", "kit_texts", ["person_id"])


def downgrade() -> None:
    op.drop_index("ix_kit_texts_person", table_name="kit_texts")
    op.drop_index("uq_kit_texts_live", table_name="kit_texts")
    op.drop_table("kit_texts")
