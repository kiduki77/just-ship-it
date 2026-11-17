"""the owner task kind for a check the owner makes by hand

Revision ID: b8d4f2a6c913
Revises: c2f7a9d4e815
Create Date: 2026-11-05 12:00:00
"""
from alembic import op

revision = "b8d4f2a6c913"
down_revision = "c2f7a9d4e815"
branch_labels = None
depends_on = None

OLD_KINDS = ("post", "publish", "account", "spend", "other", "contact", "onboard", "follow_up", "referral", "participate")
NEW_KINDS = (*OLD_KINDS, "check")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(repr(v) for v in values)


def upgrade() -> None:
    op.drop_constraint("ck_owner_tasks_kind", "owner_tasks")
    op.create_check_constraint("ck_owner_tasks_kind", "owner_tasks", f"kind IN ({_in(NEW_KINDS)})")


def downgrade() -> None:
    # A check the owner already did keeps its result as an "other" to-do rather than being deleted.
    op.execute("UPDATE owner_tasks SET kind = 'other' WHERE kind = 'check'")
    op.drop_constraint("ck_owner_tasks_kind", "owner_tasks")
    op.create_check_constraint("ck_owner_tasks_kind", "owner_tasks", f"kind IN ({_in(OLD_KINDS)})")
