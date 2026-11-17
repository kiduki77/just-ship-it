"""work path records: review_rounds, request_links, and the work path columns of agent_requests

Operator: stop the worker, `alembic upgrade head`, start the worker. Hand-written; it touches no LangGraph checkpoint
table. `blocking` stops being a stored boolean and becomes `hold_scope <> 'none'`, so nothing can write it any more:
every pending card that blocked holds the whole run (`hold_scope = 'run'`). Every card is backfilled with what its row
records (`source = 'backfill'`, `known = 'backfilled'`) and nothing more: the CEO step that filed a project-only card,
its reason and its answer use stay unknown, and its expiry rule is read only where the card has no expiry date. Each
`Project.context["ceo_reviews"]` entry becomes a review_rounds row after the project's plan row. The ceo_reviews data
itself is left in place. The downgrade turns `blocking` back into a stored boolean with the same values.

Revision ID: a9d4f7b2c613
Revises: 7c1e5a9d3f20
Create Date: 2026-11-13 18:00:00
"""
import sqlalchemy as sa
from alembic import op
from justshipit.contracts import work_path as wp
from sqlalchemy.dialects import postgresql

revision = "a9d4f7b2c613"
down_revision = "d4c8e1f6a2b3"
branch_labels = None
depends_on = None


def _int(expr: str) -> str:
    """A JSON text value as an integer, or NULL when it is not a plain one (the cast never runs on anything else)."""
    return f"CASE WHEN {expr} ~ '^[0-9]{{1,9}}$' THEN ({expr})::int END"


_REVISION = f"COALESCE({_int('r.action_params->>' + repr('revision'))}, 1)"
_PARAM_TARGETS = (("decision_id", "decision"), ("spec_id", "spec"), ("plan_id", "plan"), ("dev_job_id", "dev_job"),
                  ("use_of_job", "dev_job"), ("bet_id", "bet"), ("idea_id", "idea"), ("skill_id", "skill"),
                  ("re_request_of", "request"))


def _raised_by_sources() -> str:
    """(request_id, target_kind, target_id) for every origin an old row records."""
    parts = ["SELECT r.id, 'task', r.task_id FROM agent_requests r WHERE r.task_id IS NOT NULL"]
    parts += [f"SELECT r.id, '{kind}', {_int('r.action_params->>' + repr(key))} FROM agent_requests r"
              for key, kind in _PARAM_TARGETS]
    parts.append("SELECT r.id, 'task', t.v::int FROM agent_requests r, jsonb_array_elements_text("
                 "CASE WHEN jsonb_typeof(r.action_params->'need'->'task_ids') = 'array' "
                 "THEN r.action_params->'need'->'task_ids' ELSE '[]'::jsonb END) AS t(v) WHERE t.v ~ '^[0-9]{1,9}$'")
    parts.append(f"SELECT r.id, 'chat_message', m.id FROM agent_requests r JOIN agent_messages m "
                 f"ON m.kind = 'decision' AND {_int('m.data->>' + repr('request_id'))} = r.id")
    return "\nUNION ALL\n".join(parts)


def upgrade() -> None:
    op.create_table(
        "review_rounds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("plan_round", sa.Integer(), nullable=False),
        sa.Column("assessment", sa.Text(), nullable=False),
        sa.Column("action", sa.String(24), nullable=True),
        sa.Column("unmet", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("refusals", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("call_id", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("project_id", "seq", name="uq_review_rounds_seq"),
        sa.CheckConstraint(f"kind IN ({wp.values(wp.ReviewKind)})", name="ck_review_rounds_kind"),
    )
    op.create_index("ix_review_rounds_project_id", "review_rounds", ["project_id"])
    op.create_table(
        "request_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("agent_requests.id"), nullable=False),
        sa.Column("rel", sa.String(16), nullable=False),
        sa.Column("target_kind", sa.String(16), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(8), nullable=True),
        sa.Column("option_key", sa.String(80), nullable=True),
        sa.Column("effect", sa.String(32), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("known", sa.String(16), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"rel IN ({wp.values(wp.Rel)})", name="ck_request_links_rel"),
        sa.CheckConstraint(f"target_kind IN ({wp.values(wp.TargetKind)})", name="ck_request_links_target_kind"),
        sa.CheckConstraint(f"source IN ({wp.values(wp.LinkSource)})", name="ck_request_links_source"),
        sa.CheckConstraint(f"known IN ({wp.values(wp.Known)})", name="ck_request_links_known"),
        sa.CheckConstraint(wp.pair_check(), name="ck_request_links_pair"),
        sa.CheckConstraint("phase IS NULL OR (rel = 'holds' AND target_kind = 'task')", name="ck_request_links_phase"),
    )
    op.create_index("ix_request_links_request_id", "request_links", ["request_id"])
    op.create_index("ix_request_links_target", "request_links", ["target_kind", "target_id", "rel"])
    op.create_index("uq_request_links_edge", "request_links",
                    ["request_id", "rel", "target_kind", "target_id", sa.text("coalesce(option_key, '')"), "revision"],
                    unique=True)

    op.add_column("agent_requests", sa.Column("context_version", sa.SmallInteger(), server_default="0", nullable=False))
    op.add_column("agent_requests", sa.Column("hold_scope", sa.String(10), server_default="none", nullable=False))
    op.add_column("agent_requests", sa.Column("ask_reason", sa.String(24), nullable=True))
    op.add_column("agent_requests", sa.Column("reason_refs", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"),
                                              nullable=False))
    op.add_column("agent_requests", sa.Column("reason_note", sa.String(240), nullable=True))
    op.add_column("agent_requests", sa.Column("expiry_policy", sa.String(16), nullable=True))
    op.add_column("agent_requests", sa.Column("default_option", sa.Text(), nullable=True))
    op.add_column("agent_requests", sa.Column("owner_work", postgresql.JSONB(), nullable=True))
    op.create_index("ix_agent_requests_task_id", "agent_requests", ["task_id"])

    op.execute("UPDATE agent_requests SET hold_scope = 'run' WHERE blocking")
    op.execute("UPDATE agent_requests SET expiry_policy = 'keep_waiting' WHERE expires_at IS NULL")
    op.execute(f"""
        INSERT INTO request_links (request_id, rel, target_kind, target_id, revision, source, known)
        SELECT r.id, 'raised_by', src.kind, src.target, {_REVISION}, 'backfill', 'backfilled'
        FROM ({_raised_by_sources()}) AS src(id, kind, target)
        JOIN agent_requests r ON r.id = src.id
        WHERE src.target IS NOT NULL
        ORDER BY src.id, src.kind, src.target
        ON CONFLICT DO NOTHING
    """)
    op.execute(f"""
        INSERT INTO request_links (request_id, rel, target_kind, target_id, phase, revision, source, known)
        SELECT r.id, 'holds', 'task', r.task_id, 'finish', {_REVISION}, 'backfill', 'backfilled'
        FROM agent_requests r JOIN tasks t ON t.id = r.task_id
        WHERE t.status = 'APPROVAL_REQUIRED' AND r.type = 'APPROVAL' AND r.status = 'PENDING' AND r.blocking
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO review_rounds (project_id, seq, kind, plan_round, assessment, call_id, created_at)
        SELECT id, 1, 'plan', 1, '', NULL, NULL FROM projects
        WHERE plan IS NOT NULL AND jsonb_typeof(plan) <> 'null'
    """)
    op.execute(f"""
        INSERT INTO review_rounds (project_id, seq, kind, plan_round, assessment, action, unmet, call_id, created_at)
        SELECT p.id,
               e.n + (SELECT count(*) FROM review_rounds x WHERE x.project_id = p.id AND x.kind = 'plan'),
               'review',
               COALESCE({_int("e.v->>'round'")}, 0),
               COALESCE(e.v->>'assessment', ''),
               e.v->>'action',
               CASE WHEN jsonb_typeof(e.v->'unmet') = 'array' THEN e.v->'unmet' ELSE '[]'::jsonb END,
               NULL, NULL
        FROM projects p,
             jsonb_array_elements(CASE WHEN jsonb_typeof(p.context->'ceo_reviews') = 'array'
                                       THEN p.context->'ceo_reviews' ELSE '[]'::jsonb END) WITH ORDINALITY AS e(v, n)
        WHERE jsonb_typeof(e.v) = 'object'
    """)

    op.drop_column("agent_requests", "blocking")
    op.execute("ALTER TABLE agent_requests ADD COLUMN blocking boolean GENERATED ALWAYS AS (hold_scope <> 'none') STORED")
    op.execute("ALTER TABLE agent_requests ALTER COLUMN blocking SET NOT NULL")
    op.create_check_constraint("ck_agent_requests_hold_scope", "agent_requests",
                               f"hold_scope IN ({wp.values(wp.HoldScope)})")
    op.create_check_constraint("ck_agent_requests_expiry_policy", "agent_requests",
                               f"expiry_policy IS NULL OR expiry_policy IN ({wp.values(wp.ExpiryPolicy)})")
    op.create_check_constraint("ck_agent_requests_context", "agent_requests",
                               "context_version = 0 OR (ask_reason IS NOT NULL AND expiry_policy IS NOT NULL "
                               "AND (expiry_policy <> 'default' OR default_option IS NOT NULL))")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_requests ADD COLUMN blocking_stored boolean")
    op.execute("UPDATE agent_requests SET blocking_stored = (hold_scope <> 'none')")
    op.execute("ALTER TABLE agent_requests ALTER COLUMN blocking_stored SET NOT NULL")
    op.drop_column("agent_requests", "blocking")
    op.alter_column("agent_requests", "blocking_stored", new_column_name="blocking")
    for check in ("context", "expiry_policy", "hold_scope"):
        op.drop_constraint(f"ck_agent_requests_{check}", "agent_requests", type_="check")
    op.drop_index("ix_agent_requests_task_id", "agent_requests")
    for column in ("owner_work", "default_option", "expiry_policy", "reason_note", "reason_refs", "ask_reason",
                   "hold_scope", "context_version"):
        op.drop_column("agent_requests", column)
    op.drop_table("request_links")
    op.drop_table("review_rounds")
