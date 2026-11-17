"""tracking: the owner's places (businesses.doors) and how a person heard about the product (people.heard_via,
people.heard_words)

Operator: stop the worker before this runs, and read the autogenerate output of any later revision for dropped
LangGraph checkpoint tables. Hand-written. Writes no data: a business starts with no places, and a person with no
answer, which the counts read as "no answer yet" or by their source (docs/design/tracking.md, 2.4). The downgrade drops
the three columns and the answers in them.

Revision ID: c4a8e1d7b295
Revises: b8e2c6a4d1f7
Create Date: 2026-11-16 10:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c4a8e1d7b295"
down_revision = "b8e2c6a4d1f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("doors", JSONB, nullable=False, server_default="[]"))
    op.add_column("people", sa.Column("heard_via", sa.String(40)))
    op.add_column("people", sa.Column("heard_words", sa.String(120)))


def downgrade() -> None:
    op.drop_column("people", "heard_words")
    op.drop_column("people", "heard_via")
    op.drop_column("businesses", "doors")
