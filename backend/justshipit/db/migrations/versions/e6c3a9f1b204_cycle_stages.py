"""owner-started cycle stages, and the gate every job passes: a job says who approved it, and stage work starts only
while its stage runs

Operator: stop the worker, `alembic upgrade head`, start the worker. Hand-written; there is no autogenerate output to
review for dropped LangGraph checkpoint tables.

Existing businesses keep what is in flight. A business with an approved plan gets a draft cycle whose first proposal
the CEO writes after deploy (cycle.tick asks for it). One with live work (a project not finished, an experiment running
or listening, a job queued or running) also gets an `adopted` stage that is already running: those projects and jobs
are stamped with it and run on, and it lists exactly the recurring kinds that have a job in flight now. A schedule
with nothing in flight does not run again until a stage the owner starts lists it. Archived and [TEST] businesses get
nothing: they are never gated.

Downgrade drops the stage history (cycle_stages, cycle_proposals and the stamps). Parked jobs go back to the queue first,
and the cycles this revision opened are closed, so the code before it finds no open cycle it did not make.

Revision ID: e6c3a9f1b204
Revises: d4a8e2c6b917
Create Date: 2026-11-06 18:00:00
"""
import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "e6c3a9f1b204"
down_revision = "d4a8e2c6b917"
branch_labels = None
depends_on = None

CYCLE_STATES = ("draft", "running", "retro", "closed", "stopped")
CYCLE_MODES = ("new", "adopt")
STAGE_STATUSES = ("planned", "running", "closing", "awaiting_owner", "done", "skipped", "stopped")
HOLDING_STAGE = ("running", "closing", "awaiting_owner")
ENDS_WHEN = ("run_settled", "window_closed", "owner_marks")
PROPOSAL_STATUSES = ("drafting", "ready", "superseded", "failed")
JOB_GATES = ("none", "direct", "stage")
# gate.POLICY at this revision. Stage work and recurring kinds in flight are what an adopted stage carries on; FREE
# kinds may not spend, so their rows say so.
STAGE_WORK = ("run_project", "resume_project", "retry_project", "reassess_project", "write_spec", "spec_approved",
              "spec_questions", "dev_job", "dev_resume", "dev_deliver", "dev_fix", "dev_fix_answered", "acceptance",
              "dev_merged", "bet_retro")
RECURRING = ("find_people", "find_threads", "promotion_week", "github_collect", "kpi_review", "evidence_watch",
             "routine_run", "portfolio_review", "skill_factory")
FREE = ("notify", "daily_briefing", "backup", "retention", "bet_sweep", "execute_action", "post_reactions")
LIVE_PROJECT = ("CREATED", "RUNNING", "WAITING_HUMAN", "PAUSED")
LIVE_BET = ("RUNNING", "LISTENING")
ADOPTED = {"en": ("Work already in progress",
                  "The work that was running before cycles began. It goes on as it was; the next stage waits for you."),
           "ko": ("이미 진행 중인 일",
                  "사이클을 시작하기 전부터 하던 일이에요. 하던 대로 이어 가고, 다음 단계는 오너님을 기다려요.")}


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.add_column("cycles", sa.Column("state", sa.String(10), nullable=False, server_default="closed"))
    op.add_column("cycles", sa.Column("mode", sa.String(6), nullable=False, server_default="new"))
    op.add_column("cycles", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("cycles", sa.Column("stop_reason", sa.Text()))
    op.add_column("cycles", sa.Column("retro", JSONB()))
    op.create_check_constraint("ck_cycles_state", "cycles", f"state IN ({_in(CYCLE_STATES)})")
    op.create_check_constraint("ck_cycles_mode", "cycles", f"mode IN ({_in(CYCLE_MODES)})")

    op.create_table(
        "cycle_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cycle_id", sa.Integer(), sa.ForeignKey("cycles.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("owner_note", sa.Text()),
        sa.Column("intro", sa.Text()),
        sa.Column("reply", sa.Text()),
        sa.Column("stages", JSONB(), nullable=False),
        sa.Column("changes", JSONB(), nullable=False),
        sa.Column("basis", JSONB(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("cycle_id", "version"),
        sa.CheckConstraint(f"status IN ({_in(PROPOSAL_STATUSES)})", name="ck_cycle_proposals_status"),
    )
    op.create_index("ix_cycle_proposals_cycle_id", "cycle_proposals", ["cycle_id"])
    op.create_index("ix_cycle_proposals_one_drafting", "cycle_proposals", ["cycle_id"], unique=True,
                    postgresql_where=sa.text("status = 'drafting'"))

    op.create_table(
        "cycle_stages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cycle_id", sa.Integer(), sa.ForeignKey("cycles.id"), nullable=False),
        sa.Column("business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("proposal_id", sa.Integer(), sa.ForeignKey("cycle_proposals.id")),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("doing", sa.Text(), nullable=False),
        sa.Column("owner_does", sa.Text(), nullable=False),
        sa.Column("decision_points", JSONB(), nullable=False),
        sa.Column("est_minutes", sa.Integer()),
        sa.Column("est_usd", sa.Float()),
        sa.Column("estimate_basis", sa.String(8), nullable=False),
        sa.Column("ends_when", sa.String(14), nullable=False),
        sa.Column("allows_kinds", JSONB(), nullable=False),
        sa.Column("allows_owner_work", JSONB(), nullable=False),
        sa.Column("listen_days", sa.Integer()),
        sa.Column("listen_until", sa.DateTime(timezone=True)),
        sa.Column("bet_id", sa.Integer()),
        sa.Column("adopted", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(14), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("close_requested_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("closing_at", sa.DateTime(timezone=True)),
        sa.Column("awaiting_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("end_cause", sa.String(16)),
        sa.Column("decided_by", sa.String(40)),
        sa.Column("summary", JSONB()),
        sa.Column("summary_by", sa.String(8)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("cycle_id", "key"),
        sa.CheckConstraint(f"status IN ({_in(STAGE_STATUSES)})", name="ck_cycle_stages_status"),
        sa.CheckConstraint(f"ends_when IN ({_in(ENDS_WHEN)})", name="ck_cycle_stages_ends_when"),
    )
    op.create_index("ix_cycle_stages_cycle_id", "cycle_stages", ["cycle_id"])
    op.create_index("ix_cycle_stages_business_id", "cycle_stages", ["business_id"])
    op.create_index("ix_cycle_stages_status", "cycle_stages", ["status"])
    op.create_index("ix_cycle_stages_one_holding", "cycle_stages", ["business_id"], unique=True,
                    postgresql_where=sa.text(f"status IN ({_in(HOLDING_STAGE)})"))

    op.add_column("projects", sa.Column("stage_id", sa.Integer(), sa.ForeignKey("cycle_stages.id")))
    op.create_index("ix_projects_stage_id", "projects", ["stage_id"])
    op.add_column("llm_calls", sa.Column("stage_id", sa.Integer(), sa.ForeignKey("cycle_stages.id")))
    op.create_index("ix_llm_calls_stage_id", "llm_calls", ["stage_id"])
    # Every job queued or running now was enqueued before the gate existed: it keeps running, once.
    op.add_column("jobs", sa.Column("gate", sa.String(6), nullable=False, server_default="direct"))
    op.add_column("jobs", sa.Column("stage_id", sa.Integer(), sa.ForeignKey("cycle_stages.id")))
    op.add_column("jobs", sa.Column("business_id", sa.Integer()))
    op.create_check_constraint("ck_jobs_gate", "jobs", f"gate IN ({_in(JOB_GATES)})")
    op.create_index("ix_jobs_gated", "jobs", ["business_id"], postgresql_where=sa.text("status = 'gated'"))

    backfill(op.get_bind())

    # From here on a row made outside queue.enqueue must say which gate it passed, or it fails.
    op.alter_column("jobs", "gate", server_default=None)
    op.alter_column("cycles", "state", server_default=None)
    op.alter_column("cycles", "mode", server_default=None)


def _id(key: str) -> str:
    return f"(CASE WHEN j.payload->>'{key}' ~ '^[0-9]+$' THEN (j.payload->>'{key}')::int END)"


# The business an in-flight stage-kind job works for, by the ids its payload names (services/scope.owner_of, in SQL).
_JOB_BUSINESS = f"""
SELECT j.id, COALESCE(
    (SELECT p.business_id FROM projects p WHERE p.id = {_id('project_id')}),
    (SELECT p.business_id FROM specs sp JOIN projects p ON p.id = sp.project_id WHERE sp.id = {_id('spec_id')}),
    (SELECT p.business_id FROM dev_jobs d JOIN specs sp ON sp.id = d.spec_id JOIN projects p ON p.id = sp.project_id
      WHERE d.id = {_id('dev_job_id')}),
    (SELECT p.business_id FROM agent_requests r JOIN projects p ON p.id = r.project_id WHERE r.id = {_id('request_id')}),
    (SELECT b.business_id FROM bets b WHERE b.id = {_id('bet_id')}),
    {_id('business_id')}) AS business_id, j.kind
FROM jobs j WHERE j.status IN ('queued', 'running') AND j.kind IN ({_in(STAGE_WORK + RECURRING)})
"""


def _lang(conn) -> str:
    lang = conn.execute(sa.text("SELECT value->>'language' FROM app_settings WHERE section = 'general'")).scalar()
    return lang if lang in ADOPTED else "en"


def backfill(conn) -> None:
    """Lay a draft cycle over each business with an approved plan. Safe to run again: a business that already has an
    open cycle is left as it is."""
    conn.execute(sa.text("UPDATE cycles SET state = 'closed', closed_at = COALESCE(closed_at, now()) "
                         "WHERE closed_at IS NULL AND NOT EXISTS "
                         "(SELECT 1 FROM cycle_proposals cp WHERE cp.cycle_id = cycles.id)"))
    conn.execute(sa.text(f"UPDATE jobs SET gate = 'none' WHERE kind IN ({_in(FREE)})"))
    title, doing = ADOPTED[_lang(conn)]
    jobs = conn.execute(sa.text(_JOB_BUSINESS)).all()
    rows = conn.execute(sa.text(
        "SELECT b.id, b.cycle_days FROM businesses b WHERE b.status = 'ACTIVE' AND b.name NOT LIKE '[TEST]%' "
        "AND EXISTS (SELECT 1 FROM business_briefs bb WHERE bb.business_id = b.id AND bb.approved_at IS NOT NULL) "
        "AND NOT EXISTS (SELECT 1 FROM cycles c WHERE c.business_id = b.id AND c.closed_at IS NULL) ORDER BY b.id")).all()
    for bid, days in rows:
        live_projects = conn.execute(sa.text(
            f"SELECT id FROM projects WHERE business_id = :b AND status IN ({_in(LIVE_PROJECT)})"), {"b": bid}).scalars().all()
        live_bets = conn.execute(sa.text(
            f"SELECT 1 FROM bets WHERE business_id = :b AND status IN ({_in(LIVE_BET)}) LIMIT 1"), {"b": bid}).first()
        live_jobs = [(jid, kind) for jid, b, kind in jobs if b == bid]
        ever = conn.execute(sa.text("SELECT 1 FROM projects WHERE business_id = :b LIMIT 1"), {"b": bid}).first()
        mode = "adopt" if ever or live_bets or live_jobs else "new"
        cycle_id = conn.execute(sa.text(
            "INSERT INTO cycles (business_id, number, starts_on, ends_on, state, mode) "
            "VALUES (:b, (SELECT COALESCE(MAX(number), 0) + 1 FROM cycles WHERE business_id = :b), current_date, "
            "current_date + GREATEST(:days, 1), 'draft', :mode) RETURNING id"),
            {"b": bid, "days": days or 14, "mode": mode}).scalar()
        conn.execute(sa.text("INSERT INTO cycle_proposals (cycle_id, version, status, stages, changes, basis) "
                             "VALUES (:c, 1, 'drafting', '[]', '[]', '{}')"), {"c": cycle_id})
        if not (live_projects or live_bets or live_jobs):
            continue
        kinds = sorted({kind for _, kind in live_jobs if kind in RECURRING})
        stage_id = conn.execute(sa.text(
            "INSERT INTO cycle_stages (cycle_id, business_id, key, position, title, doing, owner_does, decision_points, "
            "estimate_basis, ends_when, allows_kinds, allows_owner_work, adopted, status, round, started_at) "
            "VALUES (:c, :b, 'adopted', 0, :title, :doing, '', '[]', 'guess', 'run_settled', CAST(:kinds AS jsonb), "
            "'[]', true, 'running', 1, now()) RETURNING id"),
            {"c": cycle_id, "b": bid, "title": title, "doing": doing, "kinds": json.dumps(kinds)}).scalar()
        if live_projects:
            conn.execute(sa.text("UPDATE projects SET stage_id = :s WHERE id = ANY(:ids)"),
                         {"s": stage_id, "ids": list(live_projects)})
        if live_jobs:
            conn.execute(sa.text("UPDATE jobs SET gate = 'stage', stage_id = :s, business_id = :b WHERE id = ANY(:ids)"),
                         {"s": stage_id, "b": bid, "ids": [jid for jid, _ in live_jobs]})


def downgrade() -> None:
    op.execute("UPDATE jobs SET status = 'queued' WHERE status = 'gated'")
    op.execute("UPDATE cycles SET closed_at = COALESCE(closed_at, now()) WHERE state <> 'closed'")
    op.drop_index("ix_jobs_gated", "jobs")
    op.drop_constraint("ck_jobs_gate", "jobs")
    op.drop_column("jobs", "business_id")
    op.drop_column("jobs", "stage_id")
    op.drop_column("jobs", "gate")
    op.drop_index("ix_llm_calls_stage_id", "llm_calls")
    op.drop_column("llm_calls", "stage_id")
    op.drop_index("ix_projects_stage_id", "projects")
    op.drop_column("projects", "stage_id")
    op.drop_index("ix_cycle_stages_one_holding", "cycle_stages")
    op.drop_index("ix_cycle_stages_status", "cycle_stages")
    op.drop_index("ix_cycle_stages_business_id", "cycle_stages")
    op.drop_index("ix_cycle_stages_cycle_id", "cycle_stages")
    op.drop_table("cycle_stages")
    op.drop_index("ix_cycle_proposals_one_drafting", "cycle_proposals")
    op.drop_index("ix_cycle_proposals_cycle_id", "cycle_proposals")
    op.drop_table("cycle_proposals")
    op.drop_constraint("ck_cycles_mode", "cycles")
    op.drop_constraint("ck_cycles_state", "cycles")
    op.drop_column("cycles", "retro")
    op.drop_column("cycles", "stop_reason")
    op.drop_column("cycles", "started_at")
    op.drop_column("cycles", "mode")
    op.drop_column("cycles", "state")
