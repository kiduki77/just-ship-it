"""kickoff conversation: one live session per business, its turns, one row per (session, n)

Revision ID: b3d9a1c7e5f2
Revises: a7c2e5b9d041
Create Date: 2026-11-03 18:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b3d9a1c7e5f2"
down_revision = "a7c2e5b9d041"
branch_labels = None
depends_on = None

STATUSES = ("OPEN", "WAITING_OWNER", "PAUSED", "DONE", "ABANDONED")
LIVE = ("OPEN", "WAITING_OWNER", "PAUSED")
KINDS = ("question", "playback", "proposal")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(repr(v) for v in values)


def upgrade() -> None:
    op.create_table(
        "kickoff_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("business_id", sa.Integer, sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("turns_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_turns", sa.Integer, nullable=False, server_default="20"),
        sa.Column("material", sa.Text),
        sa.Column("note", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("end_reason", sa.String(40)),
        sa.CheckConstraint(f"status IN ({_in(STATUSES)})", name="ck_kickoff_sessions_status"),
        sa.CheckConstraint("turns_used >= 0 AND turns_used <= max_turns", name="ck_kickoff_sessions_turns"),
    )
    op.create_index("ix_kickoff_sessions_business_id", "kickoff_sessions", ["business_id"])
    op.create_index("ix_kickoff_one_live", "kickoff_sessions", ["business_id"], unique=True,
                    postgresql_where=sa.text(f"status IN ({_in(LIVE)})"))
    op.create_table(
        "kickoff_turns",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.Integer, sa.ForeignKey("kickoff_sessions.id"), nullable=False),
        sa.Column("n", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("ceo_text", sa.Text, nullable=False),
        sa.Column("chips", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("section", sa.String(24)),
        sa.Column("closing", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("promote", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("patch_notes", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("owner_text", sa.Text),
        sa.Column("owner_choice", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("session_id", "n", name="uq_kickoff_turns_session_n"),
        sa.CheckConstraint(f"kind IN ({_in(KINDS)})", name="ck_kickoff_turns_kind"),
    )
    op.create_index("ix_kickoff_turns_session_id", "kickoff_turns", ["session_id"])


def downgrade() -> None:
    op.drop_table("kickoff_turns")
    op.drop_table("kickoff_sessions")
