"""files the owner attaches to a business, with their text read once on upload

Revision ID: c2f7a9d4e815
Revises: c4e8a2f6b913
Create Date: 2026-11-04 23:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c2f7a9d4e815"
down_revision = "c4e8a2f6b913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("kickoff_sessions.id"), nullable=True),
        sa.Column("after_turn", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.Column("parts", postgresql.JSONB(), nullable=False),
        sa.Column("cut", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('pdf', 'docx', 'pptx', 'md', 'txt')", name="ck_documents_kind"),
    )
    op.create_index("ix_documents_business_id", "documents", ["business_id"])
    op.create_index("ix_documents_sha256", "documents", ["sha256"])


def downgrade() -> None:
    op.drop_index("ix_documents_sha256", "documents")
    op.drop_index("ix_documents_business_id", "documents")
    op.drop_table("documents")
