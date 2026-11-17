"""staffing records: crews (how each task or build was staffed, and why), crew_turns (what each role produced) and
tasks.family (the task family the Project Manager named, kept until the task's crew is made)

Operator: this revision refuses a database that already has tasks or builds. Every reader of the records assumes a
subject that did work has a crew, and the owner decided the old data may be erased (docs/design/staffing-structure.md,
section 11). Reset first: stop the worker, then reset the Postgres volume and `var/` yourself (nothing here deletes
data), start Postgres, and run `alembic upgrade head`. Stop the worker before it runs and read the autogenerate output
of any later revision for dropped LangGraph checkpoint tables. Hand-written. Writes no data.

Revision ID: b8e2c6a4d1f7
Revises: a9d4f7b2c613
Create Date: 2026-11-13 18:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "b8e2c6a4d1f7"
down_revision = "a9d4f7b2c613"
branch_labels = None
depends_on = None

_KINDS = ("task", "dev_job")
_DUTIES = ("author", "verifier")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    conn = op.get_bind()
    held = [t for t in ("tasks", "dev_jobs") if conn.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {t})")).scalar()]
    if held:
        raise RuntimeError(
            f"Not upgrading: {' and '.join(held)} already hold rows, and the staffing records need a database that "
            "starts empty (a task or build with no crew would break the crews screen and recall). Nothing was "
            "changed. If the old data may go, stop the worker, reset the Postgres volume and var/ yourself, start "
            "Postgres again, and rerun `alembic upgrade head`.")
    op.create_table(
        "crews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject_kind", sa.String(8), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("family", sa.String(40), nullable=False),
        sa.Column("slots", JSONB(), nullable=False),
        sa.Column("facts", JSONB(), nullable=False),
        sa.Column("why", JSONB(), nullable=False),
        sa.Column("amendments", JSONB(), nullable=False),
        sa.Column("rules_version", sa.String(8), nullable=False),
        sa.Column("assembled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("subject_kind", "subject_id", name="uq_crews_subject"),
        sa.CheckConstraint(f"subject_kind IN ({_in(_KINDS)})", name="ck_crews_subject_kind"),
    )
    op.create_table(
        "crew_turns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("crew_id", sa.Integer(), sa.ForeignKey("crews.id"), nullable=False),
        sa.Column("call_id", sa.String(40), nullable=False),
        sa.Column("duty", sa.String(8), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("shadow", sa.Boolean(), nullable=False),
        sa.Column("record", JSONB(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("crew_id", "call_id", name="uq_crew_turns_call"),
        sa.CheckConstraint(f"duty IN ({_in(_DUTIES)})", name="ck_crew_turns_duty"),
    )
    op.create_index("ix_crew_turns_crew_id", "crew_turns", ["crew_id"])
    op.add_column("tasks", sa.Column("family", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "family")
    op.drop_table("crew_turns")
    op.drop_table("crews")
