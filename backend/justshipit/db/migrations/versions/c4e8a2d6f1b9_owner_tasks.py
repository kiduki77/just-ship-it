"""owner tasks: what only the owner can do, made when a request with no connector is approved

Revision ID: c4e8a2d6f1b9
Revises: b3d9a1c7e5f2
Create Date: 2026-11-03 21:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c4e8a2d6f1b9"
down_revision = "b3d9a1c7e5f2"
branch_labels = None
depends_on = None

KINDS = ("post", "publish", "account", "spend", "other")
STATUSES = ("OPEN", "DONE", "SKIPPED")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(repr(v) for v in values)


def upgrade() -> None:
    op.create_table(
        "owner_tasks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("business_id", sa.Integer, sa.ForeignKey("businesses.id")),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("request_id", sa.Integer, sa.ForeignKey("agent_requests.id"), unique=True),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("card", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(8), nullable=False, server_default="OPEN"),
        sa.Column("result", postgresql.JSONB),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(f"kind IN ({_in(KINDS)})", name="ck_owner_tasks_kind"),
        sa.CheckConstraint(f"status IN ({_in(STATUSES)})", name="ck_owner_tasks_status"),
        sa.CheckConstraint("(status = 'OPEN') = (done_at IS NULL)", name="ck_owner_tasks_done_at"),
    )
    op.create_index("ix_owner_tasks_business_id", "owner_tasks", ["business_id"])
    op.create_index("ix_owner_tasks_project_id", "owner_tasks", ["project_id"])
    op.create_index("ix_owner_tasks_status", "owner_tasks", ["status"])


def downgrade() -> None:
    op.drop_table("owner_tasks")
