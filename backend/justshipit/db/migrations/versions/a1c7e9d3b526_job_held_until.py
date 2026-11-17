"""a reclaimed job's lock key stays held until its old holder cannot still be running

Revision ID: a1c7e9d3b526
Revises: f6b1d4e2a935
Create Date: 2026-11-02 23:30:00
"""
import sqlalchemy as sa
from alembic import op

revision = "a1c7e9d3b526"
down_revision = "f6b1d4e2a935"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("held_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "held_until")
