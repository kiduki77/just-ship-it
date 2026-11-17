"""a business can hold a product the owner already has: its clone under products/ and the CPO's current-state report

Revision ID: c4e8a2f6b913
Revises: b8e4d2f6a179
Create Date: 2026-11-04 18:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c4e8a2f6b913"
down_revision = "b8e4d2f6a179"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("product", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("businesses", "product")
