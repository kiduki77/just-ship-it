"""per-claim lease token, so a worker that lost its lease cannot renew or finish the job

Revision ID: d4f8b2a6e713
Revises: b7e3a1c9d052
Create Date: 2026-11-02 20:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "d4f8b2a6e713"
down_revision = "b7e3a1c9d052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("lease_token", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "lease_token")
