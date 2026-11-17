"""the business a model call worked for, so a business's spend counts its kickoff, idea panel and people search

Revision ID: d4a8e2c6b917
Revises: b8d4f2a6c913
Create Date: 2026-11-06 12:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "d4a8e2c6b917"
down_revision = "b8d4f2a6c913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_calls", sa.Column("business_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_llm_calls_business_id", "llm_calls", "businesses", ["business_id"], ["id"])
    op.create_index("ix_llm_calls_business_id", "llm_calls", ["business_id"])


def downgrade() -> None:
    op.drop_index("ix_llm_calls_business_id", "llm_calls")
    op.drop_constraint("fk_llm_calls_business_id", "llm_calls", type_="foreignkey")
    op.drop_column("llm_calls", "business_id")
