"""a business's look: the style pack and main colour its new products are built from

Operator: stop the worker, `alembic upgrade head`, start the worker. The autogenerate output was this one column and
nothing else (no LangGraph checkpoint table dropped). Every business starts with none, which builds in the default
look; downgrade forgets the owner's picks.

Revision ID: 1e082314908b
Revises: f2b6d9e4a173
Create Date: 2026-11-12 11:27:49
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "1e082314908b"
down_revision = "f2b6d9e4a173"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("look", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("businesses", "look")
