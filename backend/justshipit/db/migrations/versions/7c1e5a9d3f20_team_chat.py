"""team chat: a conversation per subject on the owner's screen (chat_threads), and agent_messages as its lines

Operator: stop the worker, `alembic upgrade head`, start the worker. Hand-written; there is no autogenerate output to
review for dropped LangGraph checkpoint tables. The new agent_messages columns are nullable or have defaults, so
existing rows stay as they are. The kind check is NOT VALID: it holds every new row and leaves rows written before it
unchecked. Every task with comments gets its task thread, its comments numbered in the order they were written and
read, since the owner wrote them all.

Revision ID: 7c1e5a9d3f20
Revises: b4e9c2a7d815
Create Date: 2026-11-13 12:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "7c1e5a9d3f20"
down_revision = "b4e9c2a7d815"
branch_labels = None
depends_on = None

_TAG_KINDS = ("general", "business", "project", "task", "request", "owner_task", "plan", "spec", "dev_job", "page")
_KINDS = ("comment", "text", "task", "handoff", "escalation", "decision", "status")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.create_table(
        "chat_threads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=True),
        sa.Column("tag_kind", sa.String(16), nullable=False),
        sa.Column("tag_ref", sa.String(40), nullable=False),
        sa.Column("default_agent", sa.String(40), nullable=False),
        sa.Column("owner_read_seq", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_seq", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("business_id", "tag_kind", "tag_ref", name="uq_chat_threads_subject",
                            postgresql_nulls_not_distinct=True),
        sa.CheckConstraint(f"tag_kind IN ({_in(_TAG_KINDS)})", name="ck_chat_threads_tag_kind"),
    )
    op.add_column("agent_messages", sa.Column("thread_id", sa.Integer(), sa.ForeignKey("chat_threads.id"), nullable=True))
    op.add_column("agent_messages", sa.Column("seq", sa.Integer(), nullable=True))
    op.add_column("agent_messages", sa.Column("business_id", sa.Integer(), nullable=True))
    op.add_column("agent_messages", sa.Column("data", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"),
                                              nullable=False))
    op.add_column("agent_messages", sa.Column("scrubbed", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"),
                                              nullable=False))
    op.add_column("agent_messages", sa.Column("call_id", sa.String(40), nullable=True))
    op.add_column("agent_messages", sa.Column("in_reply_to", sa.Integer(), sa.ForeignKey("agent_messages.id"),
                                              nullable=True))
    op.add_column("agent_messages", sa.Column("client_id", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_agent_messages_thread_seq", "agent_messages", ["thread_id", "seq"])
    op.create_unique_constraint("uq_agent_messages_client", "agent_messages", ["thread_id", "client_id"])
    op.execute(f"ALTER TABLE agent_messages ADD CONSTRAINT ck_agent_messages_kind CHECK (kind IN ({_in(_KINDS)})) "
               "NOT VALID")
    op.execute("""
        INSERT INTO chat_threads (business_id, tag_kind, tag_ref, default_agent, owner_read_seq, last_seq, created_at,
                                  last_message_at)
        SELECT p.business_id, 'task', t.id::text, t.owner, count(*), count(*), min(m.created_at), max(m.created_at)
        FROM agent_messages m JOIN tasks t ON t.id = m.ref_id JOIN projects p ON p.id = t.project_id
        WHERE m.ref_type = 'task'
        GROUP BY p.business_id, t.id, t.owner
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        UPDATE agent_messages m SET thread_id = c.id, business_id = c.business_id, seq = n.seq
        FROM (SELECT id, row_number() OVER (PARTITION BY ref_id ORDER BY id) AS seq
              FROM agent_messages WHERE ref_type = 'task') n,
             tasks t JOIN projects p ON p.id = t.project_id
             JOIN chat_threads c ON c.tag_kind = 'task' AND c.tag_ref = t.id::text
                                AND c.business_id IS NOT DISTINCT FROM p.business_id
        WHERE n.id = m.id AND t.id = m.ref_id
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE agent_messages DROP CONSTRAINT ck_agent_messages_kind")
    op.drop_constraint("uq_agent_messages_client", "agent_messages", type_="unique")
    op.drop_constraint("uq_agent_messages_thread_seq", "agent_messages", type_="unique")
    for column in ("client_id", "in_reply_to", "call_id", "scrubbed", "data", "business_id", "seq", "thread_id"):
        op.drop_column("agent_messages", column)
    op.drop_table("chat_threads")
