"""usage windows sum the call ledger by time; a job waiting on a usage cap records why

Revision ID: a7c2e5b9d041
Revises: d1f5b8c2e463
Create Date: 2026-11-03 12:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "a7c2e5b9d041"
down_revision = "d1f5b8c2e463"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"])
    op.add_column("jobs", sa.Column("wait_reason", sa.String(24), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "wait_reason")
    op.drop_index("ix_llm_calls_created_at", table_name="llm_calls")
