"""owner can close a failed-job alert; failures from before this upgrade start closed

Revision ID: e5a9c3d1f824
Revises: d4f8b2a6e713
Create Date: 2026-11-02 22:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "e5a9c3d1f824"
down_revision = "d4f8b2a6e713"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("alert_dismissed_at", sa.DateTime(timezone=True), nullable=True))
    # Failed-job alerts are new in this release: the failures already in the queue are history (the run log
    # still lists them), not a wall of alerts on the first page load.
    op.execute("UPDATE jobs SET alert_dismissed_at = now() WHERE status = 'failed'")


def downgrade() -> None:
    op.drop_column("jobs", "alert_dismissed_at")
