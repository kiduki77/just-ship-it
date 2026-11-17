"""bet window: a bet whose run finished stays LISTENING for market response until its days run out

Revision ID: b5e2d8f4a617
Revises: d7a3f5c1e982
Create Date: 2026-11-04 18:00:00
"""
from alembic import op

revision = "b5e2d8f4a617"
down_revision = "d7a3f5c1e982"
branch_labels = None
depends_on = None

OLD_STATUSES = ("PROPOSED", "PICKED", "RUNNING", "REVIEW", "DONE", "STOPPED", "DROPPED")
NEW_STATUSES = ("PROPOSED", "PICKED", "RUNNING", "LISTENING", "REVIEW", "DONE", "STOPPED", "DROPPED")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.drop_constraint("ck_bets_status", "bets")
    op.create_check_constraint("ck_bets_status", "bets", f"status IN ({_in(NEW_STATUSES)})")


def downgrade() -> None:
    # Before this revision a listening bet was a running one whose run had finished; the old sweep reviews it.
    op.execute("UPDATE bets SET status = 'RUNNING' WHERE status = 'LISTENING'")
    op.drop_constraint("ck_bets_status", "bets")
    op.create_check_constraint("ck_bets_status", "bets", f"status IN ({_in(OLD_STATUSES)})")
