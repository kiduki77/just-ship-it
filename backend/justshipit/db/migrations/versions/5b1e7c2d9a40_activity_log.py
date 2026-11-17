"""activity log, with history rebuilt from existing timestamps

Revision ID: 5b1e7c2d9a40
Revises: 030ff48236ca
Create Date: 2026-11-01 12:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "5b1e7c2d9a40"
down_revision = "030ff48236ca"
branch_labels = None
depends_on = None

# Past events are rebuilt from the timestamps the records already carry, so the console's activity
# feed starts with the company's history instead of an empty page. They are marked backfill=true.
_BACKFILL = [
    """INSERT INTO activity_events (at, actor, verb, entity, entity_id, project_id, summary, data)
       SELECT created_at, 'owner', 'created', 'project', id, id, 'P-' || id || ' ' || left(name, 120) || ': created',
              jsonb_build_object('ref', 'P-' || id, 'title', name, 'backfill', true) FROM projects""",
    """INSERT INTO activity_events (at, actor, verb, entity, entity_id, project_id, summary, data)
       SELECT created_at, CASE WHEN origin = 'owner' THEN 'owner' ELSE 'ceo' END, 'created', 'task', id, project_id,
              'T-' || id || ' ' || left(title, 120) || ': created',
              jsonb_build_object('ref', 'T-' || id, 'title', title, 'owner', owner, 'backfill', true) FROM tasks""",
    """INSERT INTO activity_events (at, actor, verb, entity, entity_id, project_id, summary, data)
       SELECT started_at, owner, 'status', 'task', id, project_id,
              'T-' || id || ' ' || left(title, 120) || ': status - → RUNNING',
              jsonb_build_object('ref', 'T-' || id, 'title', title, 'owner', owner, 'field', 'status',
                                 'from', null, 'to', 'RUNNING', 'backfill', true)
       FROM tasks WHERE started_at IS NOT NULL""",
    """INSERT INTO activity_events (at, actor, verb, entity, entity_id, project_id, summary, data)
       SELECT finished_at, 'system', 'status', 'task', id, project_id,
              'T-' || id || ' ' || left(title, 120) || ': status - → ' || status,
              jsonb_build_object('ref', 'T-' || id, 'title', title, 'owner', owner, 'field', 'status',
                                 'from', null, 'to', status, 'backfill', true)
       FROM tasks WHERE finished_at IS NOT NULL""",
    """INSERT INTO activity_events (at, actor, verb, entity, entity_id, project_id, summary, data)
       SELECT created_at, from_agent, 'created', 'request', id, project_id,
              'REQ-' || id || ' ' || left(title, 120) || ': created',
              jsonb_build_object('ref', 'REQ-' || id, 'title', title, 'type', type, 'blocking', blocking,
                                 'status', 'PENDING', 'backfill', true) FROM agent_requests""",
    """INSERT INTO activity_events (at, actor, verb, entity, entity_id, project_id, summary, data)
       SELECT resolved_at,
              COALESCE(response->>'responded_by', response->>'by',
                       CASE WHEN status IN ('AUTO_APPROVED', 'DENIED_BY_POLICY') THEN 'policy' ELSE 'owner' END),
              'status', 'request', id, project_id, 'REQ-' || id || ' ' || left(title, 120) || ': status PENDING → ' || status,
              jsonb_build_object('ref', 'REQ-' || id, 'title', title, 'type', type, 'blocking', blocking,
                                 'field', 'status', 'from', 'PENDING', 'to', status, 'backfill', true)
       FROM agent_requests WHERE resolved_at IS NOT NULL""",
    """INSERT INTO activity_events (at, actor, verb, entity, entity_id, project_id, summary, data)
       SELECT created_at, 'ceo', 'created', 'decision', id, project_id,
              'DEC-' || id || ' ' || left(title, 120) || ': created',
              jsonb_build_object('ref', 'DEC-' || id, 'title', title, 'backfill', true) FROM decisions""",
    """INSERT INTO activity_events (at, actor, verb, entity, entity_id, project_id, summary, data)
       SELECT created_at, 'ceo', 'created', 'meeting', id, project_id,
              'M-' || id || ' ' || left(agenda, 120) || ': created',
              jsonb_build_object('ref', 'M-' || id, 'title', left(agenda, 200), 'backfill', true) FROM meetings""",
    """INSERT INTO activity_events (at, actor, verb, entity, entity_id, project_id, summary, data)
       SELECT j.created_at, 'engineer', 'created', 'dev_job', j.id, sp.project_id,
              'J-' || j.id || ' ' || left(sp.title, 120) || ': created',
              jsonb_build_object('ref', 'J-' || j.id, 'title', sp.title, 'backfill', true)
       FROM dev_jobs j JOIN specs sp ON sp.id = j.spec_id""",
    """INSERT INTO activity_events (at, actor, verb, entity, entity_id, project_id, summary, data)
       SELECT created_at, 'system', 'created', 'artifact', id, project_id,
              'A-' || id || ' ' || left(title, 120) || ': created',
              jsonb_build_object('ref', 'A-' || id, 'title', title, 'kind', kind, 'backfill', true)
       FROM artifacts WHERE kind IN ('plan', 'report', 'minutes', 'prd')""",
]

def upgrade() -> None:
    op.create_table(
        "activity_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("actor", sa.String(length=60), nullable=False),
        sa.Column("verb", sa.String(length=32), nullable=False),
        sa.Column("entity", sa.String(length=24), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_activity_events_at"), "activity_events", ["at"], unique=False)
    op.create_index(op.f("ix_activity_events_project_id"), "activity_events", ["project_id"], unique=False)
    for sql in _BACKFILL:
        op.execute(sql)


def downgrade() -> None:
    op.drop_index(op.f("ix_activity_events_project_id"), table_name="activity_events")
    op.drop_index(op.f("ix_activity_events_at"), table_name="activity_events")
    op.drop_table("activity_events")
