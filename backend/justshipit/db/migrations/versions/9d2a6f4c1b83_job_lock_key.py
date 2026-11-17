"""job lock keys, so one project run or dev job runs at a time across worker processes

Revision ID: 9d2a6f4c1b83
Revises: 8c41f0d2e7a9
Create Date: 2026-11-02 12:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "9d2a6f4c1b83"
down_revision = "8c41f0d2e7a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("lock_key", sa.String(length=40), nullable=True))
    op.create_index("ix_jobs_running_lock", "jobs", ["lock_key"], postgresql_where=sa.text("status = 'running'"))
    op.execute("""UPDATE jobs SET lock_key = 'dev_job:' || (payload->>'dev_job_id')
                  WHERE status IN ('queued', 'running') AND payload ? 'dev_job_id'""")
    op.execute("""UPDATE jobs SET lock_key = 'project:' || (payload->>'project_id')
                  WHERE status IN ('queued', 'running') AND lock_key IS NULL AND payload ? 'project_id'
                    AND kind <> 'realign_project'""")


def downgrade() -> None:
    op.drop_index("ix_jobs_running_lock", table_name="jobs")
    op.drop_column("jobs", "lock_key")
