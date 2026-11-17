"""owner-edited settings, one row per section

Revision ID: c9e4a7b1d352
Revises: b8d2f4a6c913
Create Date: 2026-11-03 12:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c9e4a7b1d352"
down_revision = "b8d2f4a6c913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("section", sa.String(24), primary_key=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", sa.String(60), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
