"""an external action's job holds its own lock key, so a reclaimed send waits out its old holder

Revision ID: b8d2f4a6c913
Revises: a1c7e9d3b526
Create Date: 2026-11-03 01:00:00
"""
from alembic import op

revision = "b8d2f4a6c913"
down_revision = "a1c7e9d3b526"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE jobs SET lock_key = 'action:' || (payload->>'request_id') "
               "WHERE kind = 'execute_action' AND lock_key IS NULL")


def downgrade() -> None:
    op.execute("UPDATE jobs SET lock_key = NULL WHERE kind = 'execute_action'")
