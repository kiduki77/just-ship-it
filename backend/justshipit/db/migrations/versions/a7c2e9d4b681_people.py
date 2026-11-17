"""people: who the owner may meet, and the owner task kinds that go with them

Revision ID: a7c2e9d4b681
Revises: d7a3c9e1b254
Create Date: 2026-11-03 23:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7c2e9d4b681"
down_revision = "d7a3c9e1b254"
branch_labels = None
depends_on = None

STAGES = ("found", "contacted", "talked", "first_value", "repeat", "dropped")
SOURCES = ("warm", "public_problem", "similar_tool", "participation", "inbound")
OLD_KINDS = ("post", "publish", "account", "spend", "other")
NEW_KINDS = (*OLD_KINDS, "contact", "onboard", "follow_up", "referral", "participate")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(repr(v) for v in values)


def upgrade() -> None:
    op.create_table(
        "people",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("business_id", sa.Integer, sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("handle", sa.String(200), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("platform", sa.String(24), nullable=False),
        sa.Column("profile_url", sa.String(500)),
        sa.Column("where_found_url", sa.String(500), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("why", sa.Text, nullable=False, server_default=""),
        sa.Column("stage", sa.String(12), nullable=False, server_default="found"),
        sa.Column("quotes", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("prep", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("next_action", sa.String(300)),
        sa.Column("next_action_at", sa.DateTime(timezone=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("business_id", "platform", "handle", name="uq_people_handle"),
        sa.UniqueConstraint("business_id", "where_found_url", name="uq_people_found_at"),
        sa.CheckConstraint(f"stage IN ({_in(STAGES)})", name="ck_people_stage"),
        sa.CheckConstraint(f"source IN ({_in(SOURCES)})", name="ck_people_source"),
    )
    op.create_index("ix_people_business_id", "people", ["business_id"])
    op.drop_constraint("ck_owner_tasks_kind", "owner_tasks")
    op.create_check_constraint("ck_owner_tasks_kind", "owner_tasks", f"kind IN ({_in(NEW_KINDS)})")
    op.create_index("ix_owner_tasks_person", "owner_tasks", [sa.text("(card->>'person_id')")])


def downgrade() -> None:
    op.drop_index("ix_owner_tasks_person", "owner_tasks")
    op.drop_table("people")
    op.execute(f"DELETE FROM owner_tasks WHERE kind NOT IN ({_in(OLD_KINDS)})")
    op.drop_constraint("ck_owner_tasks_kind", "owner_tasks")
    op.create_check_constraint("ck_owner_tasks_kind", "owner_tasks", f"kind IN ({_in(OLD_KINDS)})")
