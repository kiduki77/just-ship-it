"""owner ideas: what the owner thinks of while the company works, and what the CEO did with each

Revision ID: d7a3c9e1b254
Revises: c4e8a2d6f1b9
Create Date: 2026-11-03 22:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d7a3c9e1b254"
down_revision = "c4e8a2d6f1b9"
branch_labels = None
depends_on = None

STATUSES = ("NEW", "TRIAGING", "TRIAGED", "FAILED")
DISPOSITIONS = ("apply", "bet_candidate", "merge", "reshape", "brief_change", "park", "discard_proposed", "discard")
TEXT_MAX, LINKS_MAX = 4000, 5


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(repr(v) for v in values)


def upgrade() -> None:
    op.create_table(
        "owner_ideas",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("business_id", sa.Integer, sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("links", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(10), nullable=False, server_default="NEW"),
        sa.Column("disposition", sa.String(16)),
        sa.Column("reason", sa.Text),
        sa.Column("refs", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("owner_override", postgresql.JSONB),
        sa.Column("note", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("triaged_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(f"status IN ({_in(STATUSES)})", name="ck_owner_ideas_status"),
        sa.CheckConstraint(f"disposition IS NULL OR disposition IN ({_in(DISPOSITIONS)})",
                           name="ck_owner_ideas_disposition"),
        sa.CheckConstraint(f"char_length(text) BETWEEN 1 AND {TEXT_MAX}", name="ck_owner_ideas_text"),
        sa.CheckConstraint(f"jsonb_typeof(links) = 'array' AND jsonb_array_length(links) <= {LINKS_MAX}",
                           name="ck_owner_ideas_links"),
        sa.CheckConstraint("status <> 'TRIAGED' OR disposition IS NOT NULL",
                           name="ck_owner_ideas_triaged_has_disposition"),
    )
    op.create_index("ix_owner_ideas_business_id", "owner_ideas", ["business_id"])
    op.create_index("ix_owner_ideas_project_id", "owner_ideas", ["project_id"])
    op.create_index("ix_owner_ideas_status", "owner_ideas", ["status"])
    op.create_index("ix_owner_ideas_created_at", "owner_ideas", ["created_at"])


def downgrade() -> None:
    op.drop_table("owner_ideas")
