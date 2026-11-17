"""worker heartbeats, so the console can tell when no worker is alive

Revision ID: b7e3a1c9d052
Revises: 9d2a6f4c1b83
Create Date: 2026-11-02 18:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "b7e3a1c9d052"
down_revision = "9d2a6f4c1b83"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("workers")
