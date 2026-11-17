"""a grant remembers its connector's mode at approval, so a later switch to live needs a new approval

Revision ID: d1f5b8c2e463
Revises: c9e4a7b1d352
Create Date: 2026-11-03 15:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "d1f5b8c2e463"
down_revision = "c9e4a7b1d352"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("grants", sa.Column("connector_mode", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("grants", "connector_mode")
