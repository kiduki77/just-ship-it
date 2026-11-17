"""the owner's stops: the whole company or one agent, and the agent a held job waits on

Operator: stop the worker, `alembic upgrade head`, start the worker. Hand-written; there is no autogenerate output to
review for dropped LangGraph checkpoint tables. Nothing is stopped after the upgrade. Downgrade drops the stops in force,
so whatever they held runs again; the activity log keeps the record of them.

Revision ID: f2b6d9e4a173
Revises: e6c3a9f1b204
Create Date: 2026-11-06 21:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "f2b6d9e4a173"
down_revision = "e6c3a9f1b204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stops",
        sa.Column("target", sa.String(48), primary_key=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("stopped_by", sa.String(80), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
    )
    op.add_column("jobs", sa.Column("waits_on", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "waits_on")
    op.drop_table("stops")
