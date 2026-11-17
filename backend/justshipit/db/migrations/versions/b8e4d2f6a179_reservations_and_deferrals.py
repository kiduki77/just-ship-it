"""each budget reservation is a row tied to the job lease it was taken under; a job counts its usage deferrals

Revision ID: b8e4d2f6a179
Revises: e3b7d1a9c428
Create Date: 2026-11-04 12:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "b8e4d2f6a179"
down_revision = "e3b7d1a9c428"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("amount_usd", sa.Float(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("lease_token", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("jobs", sa.Column("deferrals", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("jobs", "deferrals")
    op.drop_table("budget_reservations")
