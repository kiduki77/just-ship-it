"""a spec records the DEVELOPMENT request it was written for, at most one spec per request

Revision ID: f6b1d4e2a935
Revises: e5a9c3d1f824
Create Date: 2026-11-02 23:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "f6b1d4e2a935"
down_revision = "e5a9c3d1f824"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("specs", sa.Column("request_id", sa.Integer(), sa.ForeignKey("agent_requests.id"), nullable=True))
    op.create_unique_constraint("uq_specs_request_id", "specs", ["request_id"])


def downgrade() -> None:
    op.drop_constraint("uq_specs_request_id", "specs", type_="unique")
    op.drop_column("specs", "request_id")
