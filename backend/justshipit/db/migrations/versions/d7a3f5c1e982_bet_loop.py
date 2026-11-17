"""bet loop: a bet's review state and retro, metric points, owner-confirmed launch facts, dropped channels, a business's GitHub repo

Revision ID: d7a3f5c1e982
Revises: a7c2e9d4b681
Create Date: 2026-11-04 09:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d7a3f5c1e982"
down_revision = "a7c2e9d4b681"
branch_labels = None
depends_on = None

OLD_STATUSES = ("PROPOSED", "PICKED", "RUNNING", "DONE", "STOPPED", "DROPPED")
NEW_STATUSES = ("PROPOSED", "PICKED", "RUNNING", "REVIEW", "DONE", "STOPPED", "DROPPED")
VERDICTS = ("continue", "stop", "change", "routine")
END_CAUSES = ("run_done", "run_failed", "time_limit", "budget_limit", "owner", "legacy")
STAGES = ("interest", "first_value", "repeat")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(repr(v) for v in values)


def settle_legacy_bets() -> None:
    """Every bet that exists now was not started by the loop (`loop_started_at` stays null), so the loop never retros it.
    A running bet whose project already ended is closed here without a retro or a job; one whose project still lives
    stays RUNNING for the owner to end, and the loop does not count it toward the running cap."""
    op.execute(
        "UPDATE bets b SET status = CASE p.status WHEN 'DONE' THEN 'DONE' ELSE 'STOPPED' END, end_cause = 'legacy', "
        "retro = '{\"mode\": \"legacy\"}'::jsonb, finished_at = COALESCE(b.finished_at, now()), "
        "status_reason = '회고 기능 이전에 끝난 실행이라 회고 없이 닫았습니다' "
        "FROM projects p WHERE p.bet_id = b.id AND b.status = 'RUNNING' AND p.status IN ('DONE', 'FAILED', 'ARCHIVED')")


def upgrade() -> None:
    op.drop_constraint("ck_bets_status", "bets")
    op.create_check_constraint("ck_bets_status", "bets", f"status IN ({_in(NEW_STATUSES)})")
    op.add_column("bets", sa.Column("verdict", sa.String(10)))
    op.add_column("bets", sa.Column("retro", postgresql.JSONB))
    op.add_column("bets", sa.Column("end_cause", sa.String(12)))
    op.add_column("bets", sa.Column("source_bet_id", sa.Integer))
    op.add_column("bets", sa.Column("loop_started_at", sa.DateTime(timezone=True)))
    op.create_check_constraint("ck_bets_verdict", "bets", f"verdict IS NULL OR verdict IN ({_in(VERDICTS)})")
    op.create_check_constraint("ck_bets_end_cause", "bets", f"end_cause IS NULL OR end_cause IN ({_in(END_CAUSES)})")
    op.add_column("businesses", sa.Column("github_repo", sa.String(200)))
    settle_legacy_bets()

    op.create_table(
        "metric_points",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("business_id", sa.Integer, sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("metric", sa.String(48), nullable=False),
        sa.Column("stage", sa.String(12), nullable=False),
        sa.Column("value", sa.Float),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("ref", sa.String(300), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("business_id", "source", "metric", "ref", "observed_at", name="uq_metric_point"),
        sa.CheckConstraint(f"stage IN ({_in(STAGES)})", name="ck_metric_stage"),
    )
    op.create_index("ix_metric_points_business_id", "metric_points", ["business_id"])
    op.create_index("ix_metric_points_lookup", "metric_points", ["business_id", "metric", "observed_at"])

    op.create_table(
        "launch_facts",
        sa.Column("business_id", sa.Integer, sa.ForeignKey("businesses.id"), primary_key=True),
        sa.Column("item", sa.String(32), primary_key=True),
        sa.Column("note", sa.Text),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "channel_drops",
        sa.Column("business_id", sa.Integer, sa.ForeignKey("businesses.id"), primary_key=True),
        sa.Column("channel", sa.String(80), primary_key=True),
        sa.Column("bet_id", sa.Integer),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("dropped_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("channel_drops")
    op.drop_table("launch_facts")
    op.drop_table("metric_points")
    op.drop_column("businesses", "github_repo")
    op.drop_constraint("ck_bets_end_cause", "bets")
    op.drop_constraint("ck_bets_verdict", "bets")
    op.drop_column("bets", "loop_started_at")
    op.drop_column("bets", "source_bet_id")
    op.drop_column("bets", "end_cause")
    op.drop_column("bets", "retro")
    op.drop_column("bets", "verdict")
    op.execute("UPDATE bets SET status = 'STOPPED' WHERE status = 'REVIEW'")
    op.drop_constraint("ck_bets_status", "bets")
    op.create_check_constraint("ck_bets_status", "bets", f"status IN ({_in(OLD_STATUSES)})")
