"""product plans: the owner approves a plan before a spec is written, and a spec records the plan version it follows

Operator: stop the worker, `alembic upgrade head`, start the worker. Hand-written; there is no autogenerate output to
review for dropped LangGraph checkpoint tables. Existing specs keep plan_id NULL: they were written before plans.

Revision ID: b4e9c2a7d815
Revises: 1e082314908b
Create Date: 2026-11-12 12:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b4e9c2a7d815"
down_revision = "1e082314908b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_key", sa.String(40), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("registry_path", sa.Text(), nullable=False),
        sa.Column("draft_commit_sha", sa.String(64), nullable=True),
        sa.Column("approved_commit_sha", sa.String(64), nullable=True),
        sa.Column("approval_request_id", sa.Integer(), sa.ForeignKey("agent_requests.id"), nullable=True),
        sa.Column("need", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("plan_key"),
    )
    op.create_index("ix_plans_project_id", "plans", ["project_id"])
    op.add_column("specs", sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plans.id"), nullable=True))
    op.add_column("specs", sa.Column("plan_commit_sha", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("specs", "plan_commit_sha")
    op.drop_column("specs", "plan_id")
    op.drop_index("ix_plans_project_id", table_name="plans")
    op.drop_table("plans")
