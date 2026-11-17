"""business roadmap: businesses, briefs, cycles, bets; every project becomes a bet

Revision ID: 8c41f0d2e7a9
Revises: 5b1e7c2d9a40
Create Date: 2026-11-01 16:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "8c41f0d2e7a9"
down_revision = "5b1e7c2d9a40"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

_BET_STATUS = {"CREATED": "PICKED", "RUNNING": "RUNNING", "WAITING_HUMAN": "RUNNING", "PAUSED": "RUNNING",
               "FAILED": "RUNNING", "DONE": "DONE", "ARCHIVED": "STOPPED"}
_TERMINAL = ("DONE", "STOPPED", "DROPPED")

projects = sa.table(
    "projects", sa.column("id", sa.Integer), sa.column("slug", sa.String), sa.column("name", sa.String),
    sa.column("goal", sa.Text), sa.column("status", sa.String), sa.column("budget_usd", sa.Float),
    sa.column("context", JSONB), sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)), sa.column("business_id", sa.Integer),
    sa.column("bet_id", sa.Integer))
businesses = sa.table(
    "businesses", sa.column("id", sa.Integer), sa.column("slug", sa.String), sa.column("name", sa.String),
    sa.column("one_liner", sa.Text), sa.column("status", sa.String), sa.column("cycle_days", sa.Integer),
    sa.column("rungs", JSONB), sa.column("tracks", JSONB), sa.column("models", JSONB), sa.column("door_policy", JSONB),
    sa.column("checkpoints", JSONB), sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)))
bets = sa.table(
    "bets", sa.column("id", sa.Integer), sa.column("business_id", sa.Integer), sa.column("title", sa.String),
    sa.column("pitch", sa.Text), sa.column("budget_usd", sa.Float), sa.column("run_mode", sa.String),
    sa.column("origin", sa.String), sa.column("status", sa.String),
    sa.column("picked_at", sa.DateTime(timezone=True)), sa.column("started_at", sa.DateTime(timezone=True)),
    sa.column("finished_at", sa.DateTime(timezone=True)), sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)))
activity_events = sa.table(
    "activity_events", sa.column("at", sa.DateTime(timezone=True)), sa.column("actor", sa.String),
    sa.column("verb", sa.String), sa.column("entity", sa.String), sa.column("entity_id", sa.Integer),
    sa.column("project_id", sa.Integer), sa.column("business_id", sa.Integer), sa.column("summary", sa.Text),
    sa.column("data", JSONB))


def backfill(conn) -> None:
    """Give every project a business and a bet. A reassessment child joins its parent's business; everything
    else becomes a business of its own. Only rows still without a link are touched, so a rerun does nothing."""
    rows = conn.execute(sa.select(projects).order_by(projects.c.id)).mappings().all()
    by_id = {r["id"]: dict(r) for r in rows}
    for p in by_id.values():
        if p["business_id"] is not None:
            continue
        parent = by_id.get((p["context"] or {}).get("parent_project_id"))
        if parent is not None and parent["business_id"] is not None:
            bid = parent["business_id"]
        else:
            bid = conn.scalar(sa.select(businesses.c.id).where(businesses.c.slug == p["slug"]))
            if bid is None:
                bid = conn.execute(sa.insert(businesses).values(
                    slug=p["slug"], name=p["name"], one_liner=(p["goal"] or p["name"]).splitlines()[0][:1000],
                    status="ARCHIVED" if p["status"] == "ARCHIVED" else "ACTIVE", cycle_days=14,
                    rungs=[], tracks=[], models=[], door_policy=[], checkpoints=[],
                    created_at=p["created_at"], updated_at=p["updated_at"]).returning(businesses.c.id)).scalar_one()
        conn.execute(sa.update(projects).where(projects.c.id == p["id"]).values(business_id=bid))
        p["business_id"] = bid
    for p in by_id.values():
        if p["bet_id"] is not None:
            continue
        status = _BET_STATUS.get(p["status"], "RUNNING")
        is_child = (p["context"] or {}).get("parent_project_id") in by_id
        bet_id = conn.execute(sa.insert(bets).values(
            business_id=p["business_id"], title=p["name"][:300], pitch=p["goal"], budget_usd=p["budget_usd"],
            run_mode="ai", origin="ceo" if is_child else "owner", status=status, picked_at=p["created_at"],
            started_at=p["created_at"] if status != "PICKED" else None,
            finished_at=p["updated_at"] if status in _TERMINAL else None,
            created_at=p["created_at"], updated_at=p["updated_at"]).returning(bets.c.id)).scalar_one()
        conn.execute(sa.update(projects).where(projects.c.id == p["id"]).values(bet_id=bet_id))
        p["bet_id"] = bet_id
    conn.execute(sa.text("""
        INSERT INTO activity_events (at, actor, verb, entity, entity_id, project_id, business_id, summary, data)
        SELECT b.created_at, 'owner', 'created', 'business', b.id, NULL, b.id,
               'B-' || b.id || ' ' || left(b.name, 120) || ': created',
               jsonb_build_object('ref', 'B-' || b.id, 'title', b.name, 'backfill', true)
        FROM businesses b
        WHERE NOT EXISTS (SELECT 1 FROM activity_events e WHERE e.entity = 'business' AND e.entity_id = b.id
                          AND e.verb = 'created')"""))
    conn.execute(sa.text("""
        INSERT INTO activity_events (at, actor, verb, entity, entity_id, project_id, business_id, summary, data)
        SELECT t.created_at, CASE WHEN t.origin = 'ceo' THEN 'ceo' ELSE 'owner' END, 'created', 'bet', t.id,
               p.id, t.business_id, 'BET-' || t.id || ' ' || left(t.title, 120) || ': created',
               jsonb_build_object('ref', 'BET-' || t.id, 'title', t.title, 'status', t.status, 'backfill', true)
        FROM bets t LEFT JOIN projects p ON p.bet_id = t.id
        WHERE NOT EXISTS (SELECT 1 FROM activity_events e WHERE e.entity = 'bet' AND e.entity_id = t.id
                          AND e.verb = 'created')"""))
    conn.execute(sa.text("""
        UPDATE activity_events e SET business_id = p.business_id FROM projects p
        WHERE e.project_id = p.id AND e.business_id IS NULL AND p.business_id IS NOT NULL"""))
    conn.execute(sa.text("""
        UPDATE activity_events
        SET business_id = coalesce(business_id, (data->>'business_id')::int), data = data - 'business_id'
        WHERE data ? 'business_id'"""))


def upgrade() -> None:
    op.create_table(
        "businesses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("one_liner", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("cycle_days", sa.Integer(), nullable=False),
        sa.Column("rungs", JSONB, nullable=False),
        sa.Column("tracks", JSONB, nullable=False),
        sa.Column("models", JSONB, nullable=False),
        sa.Column("door_policy", JSONB, nullable=False),
        sa.Column("checkpoints", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "business_briefs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("sections", JSONB, nullable=False),
        sa.Column("assumptions", JSONB, nullable=False),
        sa.Column("author", sa.String(length=40), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "version"),
    )
    op.create_index("ix_business_briefs_business_id", "business_briefs", ["business_id"])
    op.create_index("ix_business_briefs_one_draft", "business_briefs", ["business_id"], unique=True,
                    postgresql_where=sa.text("approved_at IS NULL"))
    op.create_table(
        "cycles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("ends_on > starts_on", name="ck_cycles_span"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "number"),
    )
    op.create_index("ix_cycles_business_id", "cycles", ["business_id"])
    op.create_index("ix_cycles_one_open", "cycles", ["business_id"], unique=True,
                    postgresql_where=sa.text("closed_at IS NULL"))
    op.create_table(
        "bets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("cycle_id", sa.Integer(), nullable=True),
        sa.Column("track_id", sa.String(length=24), nullable=True),
        sa.Column("rung_id", sa.String(length=24), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("pitch", sa.Text(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=True),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("appetite_days", sa.Integer(), nullable=True),
        sa.Column("budget_usd", sa.Float(), nullable=True),
        sa.Column("exit_signal", sa.Text(), nullable=True),
        sa.Column("circuit_breaker", sa.Text(), nullable=True),
        sa.Column("run_mode", sa.String(length=8), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("hill", sa.String(length=16), nullable=True),
        sa.Column("health", sa.String(length=16), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("picked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('PROPOSED', 'PICKED', 'RUNNING', 'DONE', 'STOPPED', 'DROPPED')",
                           name="ck_bets_status"),
        sa.CheckConstraint("hill IS NULL OR hill IN ('uphill', 'top', 'downhill')", name="ck_bets_hill"),
        sa.CheckConstraint("health IS NULL OR health IN ('on_track', 'at_risk', 'blocked')", name="ck_bets_health"),
        sa.CheckConstraint("run_mode IN ('ai', 'owner')", name="ck_bets_run_mode"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["cycle_id"], ["cycles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "business_id", name="uq_bets_id_business"),
    )
    op.create_index("ix_bets_business_id", "bets", ["business_id"])
    op.create_index("ix_bets_status", "bets", ["status"])

    op.add_column("projects", sa.Column("business_id", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("bet_id", sa.Integer(), nullable=True))
    op.create_index("ix_projects_business_id", "projects", ["business_id"])
    op.create_unique_constraint("uq_projects_bet_id", "projects", ["bet_id"])
    op.create_foreign_key("fk_projects_business_id_businesses", "projects", "businesses", ["business_id"], ["id"])
    op.create_foreign_key("fk_projects_bet_in_business", "projects", "bets", ["bet_id", "business_id"],
                          ["id", "business_id"])
    op.add_column("activity_events", sa.Column("business_id", sa.Integer(), nullable=True))
    op.create_index("ix_activity_events_business_id", "activity_events", ["business_id"])

    backfill(op.get_bind())


def downgrade() -> None:
    op.execute("""
        UPDATE activity_events SET data = data || jsonb_build_object('business_id', business_id)
        WHERE business_id IS NOT NULL""")
    op.drop_index("ix_activity_events_business_id", table_name="activity_events")
    op.drop_column("activity_events", "business_id")
    op.drop_constraint("fk_projects_bet_in_business", "projects", type_="foreignkey")
    op.drop_constraint("fk_projects_business_id_businesses", "projects", type_="foreignkey")
    op.drop_constraint("uq_projects_bet_id", "projects", type_="unique")
    op.drop_index("ix_projects_business_id", table_name="projects")
    op.drop_column("projects", "bet_id")
    op.drop_column("projects", "business_id")
    op.drop_index("ix_bets_status", table_name="bets")
    op.drop_index("ix_bets_business_id", table_name="bets")
    op.drop_table("bets")
    op.drop_index("ix_cycles_one_open", table_name="cycles")
    op.drop_index("ix_cycles_business_id", table_name="cycles")
    op.drop_table("cycles")
    op.drop_index("ix_business_briefs_one_draft", table_name="business_briefs")
    op.drop_index("ix_business_briefs_business_id", table_name="business_briefs")
    op.drop_table("business_briefs")
    op.drop_table("businesses")
