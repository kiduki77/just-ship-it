"""Business records. LangGraph checkpoints live in their own tables and only hold run state;
everything the company knows or decided is stored here."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean, CheckConstraint, Computed, Date, DateTime, Float, ForeignKey, ForeignKeyConstraint, Index, Integer,
    SmallInteger, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from justshipit.contracts import work_path

EMBEDDING_DIM = 768  # google/embeddinggemma-300m (local, multilingual)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Business(Timestamped, Base):
    """What the owner is trying to do (사업). A business holds its own ladder, tracks, business models,
    door policy and checkpoints as data, so no screen hard-codes a stage template."""
    __tablename__ = "businesses"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    one_liner: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")   # ACTIVE | ARCHIVED
    cycle_days: Mapped[int] = mapped_column(Integer, default=14)
    rungs: Mapped[list[Any]] = mapped_column(JSONB, default=list)        # ladder, in order; each has an exit signal
    tracks: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    models: Mapped[list[Any]] = mapped_column(JSONB, default=list)       # business models under test
    door_policy: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # which actions the owner decides
    checkpoints: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # dated reviews, e.g. a 12-week check
    github_repo: Mapped[str | None] = mapped_column(String(200))         # owner/name the GitHub collector reads
    # A product the owner already had (contracts.product.ExistingProduct): its clone, origin and current-state report.
    product: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # The style pack and main colour its new products are built from (contracts.look.Look); none until the owner picks.
    look: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # The owner's own places a post card links to: [{kind, url, param}] (services/tracking.py is the only writer).
    # The repository is not here: it is derived from `github_repo`.
    doors: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")


class BusinessBrief(Base):
    """One version of the one-page brief (한 장 요약). Every line carries its provenance; approved rows are
    never edited, an edit becomes the next version."""
    __tablename__ = "business_briefs"
    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    sections: Mapped[list[Any]] = mapped_column(JSONB, default=list)     # groups of lines with provenance
    assumptions: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    author: Mapped[str] = mapped_column(String(40))                      # owner | project_manager | migration
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("business_id", "version"),
                      Index("ix_business_briefs_one_draft", "business_id", unique=True,
                            postgresql_where=text("approved_at IS NULL")))


PROFILE_LINES = ("buyer", "reach", "has")
PROFILE_PROVENANCES = ("said", "confirmed", "guess", "unknown")
PROFILE_ASKED_IN = ("kickoff", "business")


class CustomerProfileValue(Base):
    """One value of one line of a business's customer description (services/profile). Append-only: the only update
    is `replaced_at`, so a live row is one with it null and a line with no live rows was never asked."""
    __tablename__ = "customer_profile_values"
    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    line: Mapped[str] = mapped_column(String(8))
    code: Mapped[str | None] = mapped_column(String(40))        # a services/profile/vocab.py key, unchecked here
    sentence: Mapped[str | None] = mapped_column(String(400))
    provenance: Mapped[str] = mapped_column(String(9))
    source: Mapped[str] = mapped_column(String(80))
    quote: Mapped[str | None] = mapped_column(Text)
    brief_id: Mapped[int | None] = mapped_column(ForeignKey("business_briefs.id"))
    written_by: Mapped[str] = mapped_column(String(40))
    cause: Mapped[str] = mapped_column(String(24))
    call_id: Mapped[str | None] = mapped_column(String(40))
    written_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(f"line IN ({', '.join(repr(v) for v in PROFILE_LINES)})",
                        name="ck_customer_profile_values_line"),
        CheckConstraint(f"provenance IN ({', '.join(repr(v) for v in PROFILE_PROVENANCES)})",
                        name="ck_customer_profile_values_provenance"),
        CheckConstraint("code IS NULL OR sentence IS NULL", name="ck_customer_profile_values_code_or_sentence"),
        CheckConstraint("(provenance = 'unknown') = (code IS NULL AND sentence IS NULL)",
                        name="ck_customer_profile_values_unknown_is_empty"),
        Index("ix_customer_profile_values_live_code", "business_id", "line", "code", unique=True,
              postgresql_where=text("replaced_at IS NULL AND code IS NOT NULL")),
        Index("ix_customer_profile_values_live_codeless", "business_id", "line", unique=True,
              postgresql_where=text("replaced_at IS NULL AND code IS NULL")),
    )


class CustomerProfileAnswer(Base):
    """The owner's own words about who buys and how they are reached, kept whole. Append-only; the inference reads
    it and a `said` value's quote is checked against it. `text` is null when the owner skipped the question."""
    __tablename__ = "customer_profile_answers"
    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    text: Mapped[str | None] = mapped_column(String(2000))
    asked_in: Mapped[str] = mapped_column(String(8))
    kickoff_session_id: Mapped[int | None] = mapped_column(ForeignKey("kickoff_sessions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint(f"asked_in IN ({', '.join(repr(v) for v in PROFILE_ASKED_IN)})",
                                      name="ck_customer_profile_answers_asked_in"),)


KICKOFF_STATUSES = ("OPEN", "WAITING_OWNER", "PAUSED", "DONE", "ABANDONED")
KICKOFF_LIVE = ("OPEN", "WAITING_OWNER", "PAUSED")
KICKOFF_KINDS = ("question", "playback", "proposal")


class KickoffSession(Base):
    """The owner's kickoff conversation (시작 대화) with the CEO for one business. At most one is live per
    business. `turns_used` counts the CEO turns posted, so a turn job that dies before it posts burns none."""
    __tablename__ = "kickoff_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="OPEN")
    turns_used: Mapped[int] = mapped_column(Integer, default=0)
    max_turns: Mapped[int] = mapped_column(Integer, default=20)
    material: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)  # why a live session is stalled, in the owner's words
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(40))
    __table_args__ = (CheckConstraint(f"status IN ({', '.join(repr(v) for v in KICKOFF_STATUSES)})",
                                      name="ck_kickoff_sessions_status"),
                      CheckConstraint("turns_used >= 0 AND turns_used <= max_turns", name="ck_kickoff_sessions_turns"),
                      Index("ix_kickoff_one_live", "business_id", unique=True,
                            postgresql_where=text(f"status IN ({', '.join(repr(v) for v in KICKOFF_LIVE)})")))


class KickoffTurn(Base):
    """One CEO turn and the owner's answer to it. `closing` marks the whole-brief playback that ends a session."""
    __tablename__ = "kickoff_turns"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("kickoff_sessions.id"), index=True)
    n: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(12))
    ceo_text: Mapped[str] = mapped_column(Text)
    chips: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    section: Mapped[str | None] = mapped_column(String(24))
    closing: Mapped[bool] = mapped_column(Boolean, default=False)
    promote: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # the guess lines a "맞아요" to this turn confirms
    patch_notes: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # what the server changed in the CEO's patch
    owner_text: Mapped[str | None] = mapped_column(Text)
    owner_choice: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("session_id", "n", name="uq_kickoff_turns_session_n"),
                      CheckConstraint(f"kind IN ({', '.join(repr(v) for v in KICKOFF_KINDS)})",
                                      name="ck_kickoff_turns_kind"))


DOCUMENT_KINDS = ("pdf", "docx", "pptx", "md", "txt")


class Document(Base):
    """A file the owner attached: a plan, a deck, research. The bytes are kept once by `sha256` under var/documents,
    and the text is read once on upload into `parts` (one per page or slide, or one for a file without pages), so no
    reader parses the file again. `business_id` is empty between an upload on the new-business form and the business
    it opens. `session_id` and `after_turn` place it in a kickoff chat: 0 is with the owner's first words, n is while
    turn n was the CEO's latest. Removing a file sets `removed_at`; the row and the bytes stay."""
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int | None] = mapped_column(ForeignKey("businesses.id"), index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("kickoff_sessions.id"))
    after_turn: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(8))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    pages: Mapped[int | None] = mapped_column(Integer)
    parts: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    cut: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint(f"kind IN ({', '.join(repr(v) for v in DOCUMENT_KINDS)})",
                                      name="ck_documents_kind"),)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(repr(v) for v in values)


CYCLE_STATES = ("draft", "running", "retro", "closed", "stopped")
CYCLE_MODES = ("new", "adopt")
STAGE_STATUSES = ("planned", "running", "closing", "awaiting_owner", "done", "skipped", "stopped")
# A stage that admits new stage work is `running`; `closing` only lets what already runs finish.
OPEN_STAGE = ("running", "closing")
HOLDING_STAGE = ("running", "closing", "awaiting_owner")
ENDS_WHEN = ("run_settled", "window_closed", "owner_marks")
PROPOSAL_STATUSES = ("drafting", "ready", "superseded", "failed")
STAGE_END_CAUSES = ("accepted", "stopped_here", "revised_away", "adopted_past", "cycle_stopped")
JOB_GATES = ("none", "direct", "stage")


class Cycle(Base):
    """One run of a business through the stages the CEO proposed and the owner started one at a time. One cycle is open
    per business at a time. `starts_on`/`ends_on` are the planned span, a guess the owner reads, never an enforcer."""
    __tablename__ = "cycles"
    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    state: Mapped[str] = mapped_column(String(10), default="draft")
    mode: Mapped[str] = mapped_column(String(6), default="new")    # adopt: laid over work that ran before cycles
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_reason: Mapped[str | None] = mapped_column(Text)
    retro: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("business_id", "number"),
                      CheckConstraint("ends_on > starts_on", name="ck_cycles_span"),
                      CheckConstraint(f"state IN ({_in(CYCLE_STATES)})", name="ck_cycles_state"),
                      CheckConstraint(f"mode IN ({_in(CYCLE_MODES)})", name="ck_cycles_mode"),
                      Index("ix_cycles_one_open", "business_id", unique=True, postgresql_where=text("closed_at IS NULL")))


class CycleProposal(Base):
    """One version of a cycle's stage list. The CEO writes it; the owner reads it, starts it, or says what to change."""
    __tablename__ = "cycle_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("cycles.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="drafting")
    owner_note: Mapped[str | None] = mapped_column(Text)
    intro: Mapped[str | None] = mapped_column(Text)
    reply: Mapped[str | None] = mapped_column(Text)
    stages: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    changes: Mapped[list[Any]] = mapped_column(JSONB, default=list)   # the server's diff against the version before
    basis: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("cycle_id", "version"),
                      CheckConstraint(f"status IN ({_in(PROPOSAL_STATUSES)})", name="ck_cycle_proposals_status"),
                      Index("ix_cycle_proposals_one_drafting", "cycle_id", unique=True,
                            postgresql_where=text("status = 'drafting'")))


class CycleStage(Timestamped, Base):
    """One owner-started piece of a cycle, and the only store of which stage a business is in. `allows_kinds` and
    `allows_owner_work` are what the owner read on the stage card before pressing Start: the recurring jobs that may run
    and the kinds of to-do the company may put in the owner's list."""
    __tablename__ = "cycle_stages"
    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("cycles.id"), index=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("cycle_proposals.id"))
    key: Mapped[str] = mapped_column(String(40))
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(120))
    doing: Mapped[str] = mapped_column(Text)
    owner_does: Mapped[str] = mapped_column(Text)
    decision_points: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    est_minutes: Mapped[int | None] = mapped_column(Integer)
    est_usd: Mapped[float | None] = mapped_column(Float)
    estimate_basis: Mapped[str] = mapped_column(String(8), default="guess")
    ends_when: Mapped[str] = mapped_column(String(14), default="run_settled")
    allows_kinds: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    allows_owner_work: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    listen_days: Mapped[int | None] = mapped_column(Integer)
    listen_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bet_id: Mapped[int | None] = mapped_column(Integer)
    adopted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(14), default="planned", index=True)
    round: Mapped[int] = mapped_column(Integer, default=1)
    close_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    awaiting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_cause: Mapped[str | None] = mapped_column(String(16))
    decided_by: Mapped[str | None] = mapped_column(String(40))
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    summary_by: Mapped[str | None] = mapped_column(String(8))
    __table_args__ = (UniqueConstraint("cycle_id", "key"),
                      CheckConstraint(f"status IN ({_in(STAGE_STATUSES)})", name="ck_cycle_stages_status"),
                      CheckConstraint(f"ends_when IN ({_in(ENDS_WHEN)})", name="ck_cycle_stages_ends_when"),
                      Index("ix_cycle_stages_one_holding", "business_id", unique=True,
                            postgresql_where=text(f"status IN ({_in(HOLDING_STAGE)})")))


BET_STATUSES = ("PROPOSED", "PICKED", "RUNNING", "LISTENING", "REVIEW", "DONE", "STOPPED", "DROPPED")
BET_VERDICTS = ("continue", "stop", "change", "routine")
BET_END_CAUSES = ("run_done", "run_failed", "time_limit", "budget_limit", "owner", "legacy")
HILL_POSITIONS = ("uphill", "top", "downhill")
BET_HEALTH = ("on_track", "at_risk", "blocked")
RUN_MODES = ("ai", "owner")


def _one_of(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IS NULL OR {column} IN ({', '.join(repr(v) for v in values)})", name=name)


class Bet(Timestamped, Base):
    """A time-boxed try (도전): for whom, what we want to learn, its limits, its success signal and its stop
    condition. Its run, when it has one, is the Project whose bet_id points here."""
    __tablename__ = "bets"
    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("cycles.id"))
    track_id: Mapped[str | None] = mapped_column(String(24))
    rung_id: Mapped[str | None] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(300))
    pitch: Mapped[str] = mapped_column(Text)
    audience: Mapped[str | None] = mapped_column(Text)
    question: Mapped[str | None] = mapped_column(Text)
    appetite_days: Mapped[int | None] = mapped_column(Integer)
    budget_usd: Mapped[float | None] = mapped_column(Float)
    exit_signal: Mapped[str | None] = mapped_column(Text)
    circuit_breaker: Mapped[str | None] = mapped_column(Text)
    run_mode: Mapped[str] = mapped_column(String(8), default="ai")       # ai | owner (오너가 직접)
    origin: Mapped[str] = mapped_column(String(16), default="owner")     # owner | project_manager | migration
    status: Mapped[str] = mapped_column(String(12), default="PROPOSED", index=True)
    status_reason: Mapped[str | None] = mapped_column(Text)
    hill: Mapped[str | None] = mapped_column(String(16))
    health: Mapped[str | None] = mapped_column(String(16))
    outcome: Mapped[str | None] = mapped_column(Text)
    verdict: Mapped[str | None] = mapped_column(String(10))              # the retro's call
    retro: Mapped[dict[str, Any] | None] = mapped_column(JSONB)          # the stored retro (BetRetroOut or PeopleRetroOut)
    end_cause: Mapped[str | None] = mapped_column(String(12))            # why the run ended and the bet went to REVIEW
    source_bet_id: Mapped[int | None] = mapped_column(Integer)           # the bet whose retro proposed this one
    loop_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # set only by the loop; null = legacy
    picked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("id", "business_id", name="uq_bets_id_business"),
                      CheckConstraint(f"status IN ({', '.join(repr(v) for v in BET_STATUSES)})", name="ck_bets_status"),
                      _one_of("hill", HILL_POSITIONS, "ck_bets_hill"),
                      _one_of("health", BET_HEALTH, "ck_bets_health"),
                      _one_of("verdict", BET_VERDICTS, "ck_bets_verdict"),
                      _one_of("end_cause", BET_END_CAUSES, "ck_bets_end_cause"),
                      CheckConstraint(f"run_mode IN ({', '.join(repr(v) for v in RUN_MODES)})",
                                      name="ck_bets_run_mode"))


METRIC_STAGES = ("interest", "first_value", "repeat")


class MetricPoint(Base):
    """One observed number of a business, at a stage of the ladder from interest to first value to repeat use.
    `value` is null when the source could not be read (a blocked Reddit page), so a gap is stored as a gap. The
    unique key makes a collector run for the same moment replace its own earlier numbers."""
    __tablename__ = "metric_points"
    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    metric: Mapped[str] = mapped_column(String(48))
    stage: Mapped[str] = mapped_column(String(12))
    value: Mapped[float | None] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(24))                      # github | hn | reddit | people
    ref: Mapped[str] = mapped_column(String(300), default="")            # what the number is about: a referrer, a path, a post
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("business_id", "source", "metric", "ref", "observed_at", name="uq_metric_point"),
                      CheckConstraint(f"stage IN ({', '.join(repr(v) for v in METRIC_STAGES)})", name="ck_metric_stage"),
                      Index("ix_metric_points_lookup", "business_id", "metric", "observed_at"))


class LaunchFact(Base):
    """A launch gate item only the owner can vouch for (a recorded demo, outsiders who installed it). The owner
    confirms it; the company never does."""
    __tablename__ = "launch_facts"
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), primary_key=True)
    item: Mapped[str] = mapped_column(String(32), primary_key=True)
    note: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChannelDrop(Base):
    """A channel the retro stopped using for a business, by its key in `config/channels.yaml`. A unit of a bigger site is
    its own key (`reddit_opensource`), never `reddit:r/commandline`. The weekly promotion skips it. Only a retro writes
    it, and only after the channel earned the judgement (`promotion.droppable`)."""
    __tablename__ = "channel_drops"
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), primary_key=True)
    channel: Mapped[str] = mapped_column(String(80), primary_key=True)
    bet_id: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    dropped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Project(Timestamped, Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="CREATED")
    phase: Mapped[str | None] = mapped_column(String(48))
    thread_id: Mapped[str | None] = mapped_column(String(80))
    budget_usd: Mapped[float] = mapped_column(Float)
    spent_usd: Mapped[float] = mapped_column(Float, default=0.0)
    reserved_usd: Mapped[float] = mapped_column(Float, default=0.0)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # human answers, constraints
    plan_round: Mapped[int] = mapped_column(Integer, default=0)
    meetings_held: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    business_id: Mapped[int | None] = mapped_column(ForeignKey("businesses.id"), index=True)
    bet_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    stage_id: Mapped[int | None] = mapped_column(ForeignKey("cycle_stages.id"), index=True)  # the stage it ran for
    __table_args__ = (ForeignKeyConstraint(["bet_id", "business_id"], ["bets.id", "bets.business_id"],
                                           name="fk_projects_bet_in_business"),)


class Task(Timestamped, Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(300))
    owner: Mapped[str] = mapped_column(String(40))
    objective: Mapped[str] = mapped_column(Text)
    acceptance_criteria: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    depends_on: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # task ids
    status: Mapped[str] = mapped_column(String(32), default="PROPOSED", index=True)
    origin: Mapped[str] = mapped_column(String(32), default="ceo_plan")
    # The task family its crew is placed under (config/staffing.yaml), checked when the task is made. Empty on a task
    # made some other way, which staffing reads as `other`.
    family: Mapped[str | None] = mapped_column(String(40))
    plan_round: Mapped[int] = mapped_column(Integer, default=0)
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    max_iterations: Mapped[int] = mapped_column(Integer, default=2)
    budget_usd: Mapped[float] = mapped_column(Float, default=3.0)
    spent_usd: Mapped[float] = mapped_column(Float, default=0.0)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    review: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    contested: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    artifact_id: Mapped[int | None] = mapped_column(ForeignKey("artifacts.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("project_id", "key"),)


class LLMCallRecord(Base):
    """Execution ledger (task_runs): every model call with cost, tokens, and raw transcript path."""
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[str] = mapped_column(String(40), unique=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    # The business the call worked for: its project's, or the one a call outside any project names (the kickoff, the
    # idea panel, finding people). Empty for company-wide work, and on calls made before the column existed.
    business_id: Mapped[int | None] = mapped_column(ForeignKey("businesses.id", name="fk_llm_calls_business_id"),
                                                    index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    dev_job_id: Mapped[int | None] = mapped_column(ForeignKey("dev_jobs.id"))
    agent: Mapped[str] = mapped_column(String(40))
    purpose: Mapped[str] = mapped_column(String(40))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    provider: Mapped[str] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(80))
    prompt_version: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    raw_path: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    # The owner-started stage the call spent under, from its job's lease. Empty for calls with no stage behind them.
    stage_id: Mapped[int | None] = mapped_column(ForeignKey("cycle_stages.id"), index=True)
    # The usage windows sum cost over a day and a week of this column on every model call.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    agent: Mapped[str] = mapped_column(String(40))
    claim: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(24))
    source_url: Mapped[str | None] = mapped_column(Text)
    source_title: Mapped[str | None] = mapped_column(Text)
    quote: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    # unchecked | url_ok | quote_found | url_unreachable | supported | weak | contradicted | unverifiable
    # analysis: reproduced | computed | transcribed | derived (from this project's own ledger)
    verification_status: Mapped[str] = mapped_column(String(24), default="unchecked")
    verification_note: Mapped[str | None] = mapped_column(Text)
    superseded_by: Mapped[int | None] = mapped_column(ForeignKey("evidence.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # task_output | minutes | report | prd | handoff | skill
    title: Mapped[str] = mapped_column(String(300))
    path: Mapped[str] = mapped_column(Text)        # relative to registry/
    version: Mapped[int] = mapped_column(Integer, default=1)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Meeting(Base):
    __tablename__ = "meetings"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    agenda: Mapped[str] = mapped_column(Text)
    participants: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    transcript: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("decisions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    decision: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str] = mapped_column(Text)
    supporting_evidence_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    dissenting_evidence_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    dissent_summary: Mapped[str | None] = mapped_column(Text)
    open_questions: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    action_task_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_request_id: Mapped[int | None] = mapped_column(ForeignKey("agent_requests.id"))
    status: Mapped[str] = mapped_column(String(24), default="EFFECTIVE")  # EFFECTIVE | PENDING_APPROVAL | REJECTED | SUPERSEDED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentRequest(Base):
    """Approval Center item (approval_requests). Agent Request Protocol."""
    __tablename__ = "agent_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    from_agent: Mapped[str] = mapped_column(String(40))
    type: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    options: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    recommended_option: Mapped[str | None] = mapped_column(Text)
    action_kind: Mapped[str | None] = mapped_column(String(64))
    action_params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float)
    risk_level: Mapped[str] = mapped_column(String(16), default="low")
    # Read-only: `hold_scope <> 'none'`, what `blocking` always meant. Write `hold_scope`. After a write the attribute
    # is stale until the next flush.
    blocking: Mapped[bool] = mapped_column(Boolean, Computed("hold_scope <> 'none'", persisted=True))
    policy_mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The work path (docs/design/work-path.md 1.4). 0: filed before it, nothing was recorded; 1: filed under it.
    context_version: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    hold_scope: Mapped[str] = mapped_column(String(10), server_default=work_path.HoldScope.NONE.value)
    ask_reason: Mapped[str | None] = mapped_column(String(24))
    reason_refs: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    reason_note: Mapped[str | None] = mapped_column(String(240))
    expiry_policy: Mapped[str | None] = mapped_column(String(16))
    default_option: Mapped[str | None] = mapped_column(Text)
    # A card that asks the owner to do something by hand: minutes, why_human and if_skipped (RequestContext.owner_work).
    owner_work: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    __table_args__ = (
        CheckConstraint(f"hold_scope IN ({work_path.values(work_path.HoldScope)})", name="ck_agent_requests_hold_scope"),
        CheckConstraint(f"expiry_policy IS NULL OR expiry_policy IN ({work_path.values(work_path.ExpiryPolicy)})",
                        name="ck_agent_requests_expiry_policy"),
        CheckConstraint("context_version = 0 OR (ask_reason IS NOT NULL AND expiry_policy IS NOT NULL "
                        "AND (expiry_policy <> 'default' OR default_option IS NOT NULL))",
                        name="ck_agent_requests_context"))


class ReviewRound(Base):
    """One CEO step that can file cards: the plan, a review, a realignment or a decision. Replaces
    `Project.context["ceo_reviews"]`, whose entries had no id."""
    __tablename__ = "review_rounds"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # order in the project
    kind: Mapped[str] = mapped_column(String(16))
    plan_round: Mapped[int] = mapped_column(Integer)  # not an id: a review can run twice in one round
    assessment: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str | None] = mapped_column(String(24))
    unmet: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    refusals: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    call_id: Mapped[str | None] = mapped_column(String(40))  # the model call that produced it; null when backfilled
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("project_id", "seq", name="uq_review_rounds_seq"),
                      CheckConstraint(f"kind IN ({work_path.values(work_path.ReviewKind)})",
                                      name="ck_review_rounds_kind"))


class RequestLink(Base):
    """A typed edge from one request (docs/design/work-path.md 1.3), written only by services/requests.py in the
    transaction that writes the request. `target_id` has no foreign key: its table is named by `target_kind`."""
    __tablename__ = "request_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("agent_requests.id"), index=True)
    rel: Mapped[str] = mapped_column(String(16))
    target_kind: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str | None] = mapped_column(String(8))              # a task hold: start | finish
    option_key: Mapped[str | None] = mapped_column(String(80))        # answer_use, followed: which answer
    effect: Mapped[str | None] = mapped_column(String(32))            # answer_use, followed: a closed effect key
    revision: Mapped[int] = mapped_column(Integer)                    # the card revision that wrote it
    source: Mapped[str] = mapped_column(String(16))
    known: Mapped[str] = mapped_column(String(16))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # a later revision replaced the row
    __table_args__ = (
        Index("ix_request_links_target", "target_kind", "target_id", "rel"),
        Index("uq_request_links_edge", "request_id", "rel", "target_kind", "target_id", text("coalesce(option_key, '')"),
              "revision", unique=True),
        CheckConstraint(f"rel IN ({work_path.values(work_path.Rel)})", name="ck_request_links_rel"),
        CheckConstraint(f"target_kind IN ({work_path.values(work_path.TargetKind)})", name="ck_request_links_target_kind"),
        CheckConstraint(f"source IN ({work_path.values(work_path.LinkSource)})", name="ck_request_links_source"),
        CheckConstraint(f"known IN ({work_path.values(work_path.Known)})", name="ck_request_links_known"),
        CheckConstraint(work_path.pair_check(), name="ck_request_links_pair"),
        CheckConstraint("phase IS NULL OR (rel = 'holds' AND target_kind = 'task')", name="ck_request_links_phase"))


class Grant(Base):
    """Scoped, expiring permission created by an approval. Re-checked at execution time."""
    __tablename__ = "grants"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("agent_requests.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    action_kind: Mapped[str] = mapped_column(String(64), index=True)
    max_amount_usd: Mapped[float | None] = mapped_column(Float)
    amount_used_usd: Mapped[float] = mapped_column(Float, default=0.0)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    connector_mode: Mapped[str | None] = mapped_column(String(16))   # the connector's mode when the owner approved
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


OWNER_TASK_KINDS = ("post", "publish", "account", "spend", "other", "contact", "onboard", "follow_up", "referral",
                    "participate", "check")
OWNER_TASK_STATUSES = ("OPEN", "DONE", "SKIPPED")


class OwnerTask(Base):
    """Something only the owner can do (post, publish, sign up, pay), made when a request with no connector is
    approved. The approval alone proves nothing happened; the task's status and result do. At most one per request."""
    __tablename__ = "owner_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int | None] = mapped_column(ForeignKey("businesses.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("agent_requests.id"), unique=True)
    kind: Mapped[str] = mapped_column(String(12))
    title: Mapped[str] = mapped_column(String(300))
    card: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(8), default="OPEN", index=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint(f"kind IN ({', '.join(repr(v) for v in OWNER_TASK_KINDS)})",
                                      name="ck_owner_tasks_kind"),
                      CheckConstraint(f"status IN ({', '.join(repr(v) for v in OWNER_TASK_STATUSES)})",
                                      name="ck_owner_tasks_status"),
                      CheckConstraint("(status = 'OPEN') = (done_at IS NULL)", name="ck_owner_tasks_done_at"),
                      Index("ix_owner_tasks_person", text("(card->>'person_id')")))


IDEA_STATUSES = ("NEW", "TRIAGING", "TRIAGED", "FAILED")
IDEA_DISPOSITIONS = ("apply", "bet_candidate", "merge", "reshape", "brief_change", "park", "discard_proposed", "discard")
IDEA_TEXT_MAX = 4000
IDEA_LINKS_MAX = 5


class OwnerIdea(Base):
    """Something the owner thought of while the company works (아이디어). The CEO reads it and picks one disposition;
    `refs` holds what the CEO cited and, under `created`, what the server made from it. `owner_override` is the owner's
    reversal, applied by the same triage. `note` says why a triage is waiting or failed, in the owner's words."""
    __tablename__ = "owner_ideas"
    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    links: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(10), default="NEW", index=True)
    disposition: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    refs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    owner_override: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    triaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(f"status IN ({', '.join(repr(v) for v in IDEA_STATUSES)})", name="ck_owner_ideas_status"),
        _one_of("disposition", IDEA_DISPOSITIONS, "ck_owner_ideas_disposition"),
        CheckConstraint(f"char_length(text) BETWEEN 1 AND {IDEA_TEXT_MAX}", name="ck_owner_ideas_text"),
        CheckConstraint(f"jsonb_typeof(links) = 'array' AND jsonb_array_length(links) <= {IDEA_LINKS_MAX}",
                        name="ck_owner_ideas_links"),
        CheckConstraint("status <> 'TRIAGED' OR disposition IS NOT NULL", name="ck_owner_ideas_triaged_has_disposition"),
    )


IDEA_REVIEW_LENSES = ("market", "timing", "demand")
IDEA_REVIEW_STATUSES = ("queued", "done", "failed")


class IdeaReview(Base):
    """One lens of one round of the idea review panel (아이디어 평가단). A round has a row per lens, each written only
    by its own job, so the lenses run in parallel without sharing a row. `result` is the critique once done; `note`
    says why a lens is waiting or failed, in the owner's words. Advisory only: nothing reads it as a gate."""
    __tablename__ = "idea_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    round: Mapped[int] = mapped_column(Integer)
    lens: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(8), default="queued")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("business_id", "round", "lens", name="uq_idea_reviews_round_lens"),
        CheckConstraint(f"lens IN ({', '.join(repr(v) for v in IDEA_REVIEW_LENSES)})", name="ck_idea_reviews_lens"),
        CheckConstraint(f"status IN ({', '.join(repr(v) for v in IDEA_REVIEW_STATUSES)})", name="ck_idea_reviews_status"),
        CheckConstraint("round >= 1", name="ck_idea_reviews_round"),
        CheckConstraint("status <> 'done' OR result IS NOT NULL", name="ck_idea_reviews_done_has_result"),
    )


PERSON_STAGES = ("found", "contacted", "talked", "first_value", "repeat", "dropped")
PERSON_SOURCES = ("warm", "public_problem", "similar_tool", "participation", "inbound")


class Person(Base):
    """Someone the owner may meet, one row per person per business. Found by the company, contacted only by the
    owner. There is no column for an email address or a phone number, and none is stored anywhere in the row: what
    the person said in public is in `quotes`, each with the link it came from. An archived row is a stub that keeps
    the handle so the person is never found again."""
    __tablename__ = "people"
    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    handle: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str] = mapped_column(String(24))
    profile_url: Mapped[str | None] = mapped_column(String(500))
    where_found_url: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(16))
    why: Mapped[str] = mapped_column(Text, default="")
    stage: Mapped[str] = mapped_column(String(12), default="found")
    quotes: Mapped[list[Any]] = mapped_column(JSONB, default=list)       # [{text, url, at}]
    prep: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)    # {questions, talking_points} for the card
    next_action: Mapped[str | None] = mapped_column(String(300))
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # How they heard about the product, as the owner recorded it: a channel key or friend | search | other, and their
    # own words. Null is not one state (services/tracking_count.py reads it with `source`).
    heard_via: Mapped[str | None] = mapped_column(String(40))
    heard_words: Mapped[str | None] = mapped_column(String(120))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    __table_args__ = (UniqueConstraint("business_id", "platform", "handle", name="uq_people_handle"),
                      UniqueConstraint("business_id", "where_found_url", name="uq_people_found_at"),
                      CheckConstraint(f"stage IN ({', '.join(repr(v) for v in PERSON_STAGES)})",
                                      name="ck_people_stage"),
                      CheckConstraint(f"source IN ({', '.join(repr(v) for v in PERSON_SOURCES)})",
                                      name="ck_people_source"))


KIT_PARTS = ("message", "profile", "invite")


class KitText(Base):
    """A text the First 10 kit wrote for the owner to copy (services/kit.py is the only writer). Append-only: writing a
    slot again marks the live row replaced and adds a new one. A refused text keeps its reason and no body. The table
    has no column for an email address, a phone number or a messenger id, and a forgotten person's rows lose their
    words (`people.archive`). `call_id` is the `llm_calls.call_id` of the call that wrote it."""
    __tablename__ = "kit_texts"
    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    part: Mapped[str] = mapped_column(String(10))
    slot: Mapped[str] = mapped_column(String(20))
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"))
    lang: Mapped[str] = mapped_column(String(5))
    body: Mapped[str | None] = mapped_column(Text)
    refused: Mapped[str | None] = mapped_column(String(40))
    basis: Mapped[dict[str, Any]] = mapped_column(JSONB)
    job_id: Mapped[int | None] = mapped_column(Integer)
    call_id: Mapped[str | None] = mapped_column(String(40))
    made_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(f"part IN ({_in(KIT_PARTS)})", name="ck_kit_texts_part"),
        CheckConstraint("(part = 'message') = (person_id IS NOT NULL)", name="ck_kit_texts_person"),
        CheckConstraint("(body IS NULL) <> (refused IS NULL)", name="ck_kit_texts_body_or_refused"),
        Index("uq_kit_texts_live", "business_id", "part", "slot", text("coalesce(person_id, 0)"), unique=True,
              postgresql_where=text("replaced_at IS NULL")),
        Index("ix_kit_texts_person", "person_id"),
    )


CHAT_TAG_KINDS = ("general", "business", "project", "task", "request", "owner_task", "plan", "spec", "dev_job", "page")


class ChatThread(Base):
    """One team chat conversation per subject on the owner's screen, made by its first message (services/chat.py owns
    the table). The subject's label is not stored: it is read from the live record each time. Archived, never deleted."""
    __tablename__ = "chat_threads"
    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int | None] = mapped_column(ForeignKey("businesses.id"))  # None only for the company's General
    tag_kind: Mapped[str] = mapped_column(String(16))
    tag_ref: Mapped[str] = mapped_column(String(40), default="")
    default_agent: Mapped[str] = mapped_column(String(40))  # who owned the subject when the thread was made
    owner_read_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("business_id", "tag_kind", "tag_ref", name="uq_chat_threads_subject",
                                       postgresql_nulls_not_distinct=True),
                      CheckConstraint(f"tag_kind IN ({_in(CHAT_TAG_KINDS)})", name="ck_chat_threads_tag_kind"))


MESSAGE_KINDS = ("comment", "text", "task", "handoff", "escalation", "decision", "status")


class AgentMessage(Base):
    """A message to or from an agent: an owner's comment on a task, or a line of team chat (`thread_id`). A line the
    server writes keeps its catalog key and values in `data`, never a sentence; `body` is the scrubbed text."""
    __tablename__ = "agent_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    from_agent: Mapped[str] = mapped_column(String(40))
    to_agent: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(32))
    ref_type: Mapped[str | None] = mapped_column(String(32))
    ref_id: Mapped[int | None] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    thread_id: Mapped[int | None] = mapped_column(ForeignKey("chat_threads.id"))
    seq: Mapped[int | None] = mapped_column(Integer)  # order in the thread, given under the thread's row lock
    business_id: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    scrubbed: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    call_id: Mapped[str | None] = mapped_column(String(40))  # the llm_calls row that wrote an agent's reply
    in_reply_to: Mapped[int | None] = mapped_column(ForeignKey("agent_messages.id"))
    # The client's id of an owner message, so a double press or a retry sends it once.
    client_id: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("thread_id", "seq", name="uq_agent_messages_thread_seq"),
                      UniqueConstraint("thread_id", "client_id", name="uq_agent_messages_client"),
                      CheckConstraint(f"kind IN ({_in(MESSAGE_KINDS)})", name="ck_agent_messages_kind"))


class Spec(Timestamped, Base):
    __tablename__ = "specs"
    id: Mapped[int] = mapped_column(primary_key=True)
    spec_key: Mapped[str] = mapped_column(String(40), unique=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")
    version: Mapped[int] = mapped_column(Integer, default=1)
    registry_path: Mapped[str] = mapped_column(Text)
    draft_commit_sha: Mapped[str | None] = mapped_column(String(64))
    approved_commit_sha: Mapped[str | None] = mapped_column(String(64))
    target_repository: Mapped[str | None] = mapped_column(String(120))
    handoff: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    approval_request_id: Mapped[int | None] = mapped_column(ForeignKey("agent_requests.id"))
    # The approved DEVELOPMENT request this spec answers: a retried write_spec finds it instead of drafting again.
    request_id: Mapped[int | None] = mapped_column(ForeignKey("agent_requests.id"), unique=True)
    # The product plan it was written from, at the version the owner approved; NULL for a spec from before plans.
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id"))
    plan_commit_sha: Mapped[str | None] = mapped_column(String(64))


class Plan(Timestamped, Base):
    """The product plan the owner approves before a spec is written (engineering/plans.py). Each version is a commit of
    plan.md under the same key."""
    __tablename__ = "plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_key: Mapped[str] = mapped_column(String(40), unique=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")
    registry_path: Mapped[str] = mapped_column(Text)
    draft_commit_sha: Mapped[str | None] = mapped_column(String(64))
    approved_commit_sha: Mapped[str | None] = mapped_column(String(64))
    approval_request_id: Mapped[int | None] = mapped_column(ForeignKey("agent_requests.id"))
    # What the build asked for ({title, problem, why_software, task_ids, needs}) and the asks that joined it.
    need: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class DevJob(Timestamped, Base):
    __tablename__ = "dev_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    spec_id: Mapped[int] = mapped_column(ForeignKey("specs.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    branch: Mapped[str | None] = mapped_column(String(200))
    worktree_path: Mapped[str | None] = mapped_column(Text)
    pr_url: Mapped[str | None] = mapped_column(Text)
    steps: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)


CREW_SUBJECT_KINDS = ("task", "dev_job")
CREW_DUTIES = ("author", "verifier")


class Crew(Base):
    """How one task or build was staffed and why (justshipit.staffing). One row per subject: the unique constraint is what
    keeps a retry, a checkpoint replay and a concurrent call to one crew. `slots` is the decision as made, and a
    later addition goes to `amendments`, so the original is never rewritten. `subject_id` is a task's or a build's id,
    which no foreign key can say."""
    __tablename__ = "crews"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_kind: Mapped[str] = mapped_column(String(8))
    subject_id: Mapped[int] = mapped_column(Integer)
    family: Mapped[str] = mapped_column(String(40))
    slots: Mapped[list[Any]] = mapped_column(JSONB)
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    why: Mapped[list[Any]] = mapped_column(JSONB)
    amendments: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    rules_version: Mapped[str] = mapped_column(String(8))
    assembled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("subject_kind", "subject_id", name="uq_crews_subject"),
        CheckConstraint(f"subject_kind IN ({', '.join(repr(v) for v in CREW_SUBJECT_KINDS)})",
                        name="ck_crews_subject_kind"),
    )


class CrewTurn(Base):
    """What one role produced in one pass, append-only. The key is the ledger's call id, because a build repeats
    iteration numbers: `round` is the fix round and `iteration` the loop counter, and both only describe."""
    __tablename__ = "crew_turns"
    id: Mapped[int] = mapped_column(primary_key=True)
    crew_id: Mapped[int] = mapped_column(ForeignKey("crews.id"), index=True)
    call_id: Mapped[str] = mapped_column(String(40))
    duty: Mapped[str] = mapped_column(String(8))
    role: Mapped[str] = mapped_column(String(40))
    round: Mapped[int] = mapped_column(Integer, default=0)
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    shadow: Mapped[bool] = mapped_column(Boolean, default=False)
    record: Mapped[dict[str, Any]] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("crew_id", "call_id", name="uq_crew_turns_call"),
        CheckConstraint(f"duty IN ({', '.join(repr(v) for v in CREW_DUTIES)})", name="ck_crew_turns_duty"),
    )


class Skill(Timestamped, Base):
    __tablename__ = "skills"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="PROPOSED")
    owner_agent: Mapped[str] = mapped_column(String(40))
    target_agents: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    registry_path: Mapped[str | None] = mapped_column(Text)
    proposal: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    eval_results: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_gap_count: Mapped[int] = mapped_column(Integer, default=1)
    approval_request_id: Mapped[int | None] = mapped_column(ForeignKey("agent_requests.id"))
    __table_args__ = (UniqueConstraint("name", "version"),)


class Job(Base):
    """Transactional outbox / work queue. Enqueued in the same transaction as the state change
    that causes it (e.g. an approval), so a resume can never be lost or duplicated."""
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    idempotency_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    locked_by: Mapped[str | None] = mapped_column(String(80))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # lease: refreshed by heartbeat
    # Identifies one claim: a heartbeat or finish from a worker whose lease was reclaimed no longer matches.
    lease_token: Mapped[str | None] = mapped_column(String(32))
    # Jobs sharing a key never run at the same time (one LangGraph thread, one dev worktree).
    lock_key: Mapped[str | None] = mapped_column(String(40))
    # Set by a reclaim: the old holder may still be mid-call until then, so the key stays busy for everyone.
    held_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    alert_dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # owner closed its alert
    # Why a queued job sits until `run_after` without having failed (usage.Reason); cleared when it is claimed.
    wait_reason: Mapped[str | None] = mapped_column(String(24))
    # How often it went back to wait on usage. Past operations.max_deferrals it fails instead of waiting again.
    deferrals: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Who approved it (services/gate.py): `none` may not spend, `direct` the owner asked for it, `stage` only while its
    # stage runs. No default, here or in the database, so a row made outside queue.enqueue says nothing and never runs.
    gate: Mapped[str] = mapped_column(String(6))
    stage_id: Mapped[int | None] = mapped_column(ForeignKey("cycle_stages.id"))
    business_id: Mapped[int | None] = mapped_column(Integer)
    # The agent (role key) the owner stopped that this job reached, which a claim waits on until the owner resumes it
    # (services/stops.py). Any agent's resume clears it, so the job looks again at what it can do.
    waits_on: Mapped[str | None] = mapped_column(String(40))
    __table_args__ = (Index("ix_jobs_pick", "status", "run_after"),
                      Index("ix_jobs_running_lock", "lock_key", postgresql_where=text("status = 'running'")),
                      CheckConstraint(f"gate IN ({_in(JOB_GATES)})", name="ck_jobs_gate"),
                      Index("ix_jobs_gated", "business_id", postgresql_where=text("status = 'gated'")))


class Stop(Base):
    """An owner's stop that is in force: the whole company (`company`) or one agent (`agent:<role key>`). Resuming deletes
    the row; the activity log keeps who stopped and resumed what, and when (services/stops.py owns the table)."""
    __tablename__ = "stops"
    target: Mapped[str] = mapped_column(String(48), primary_key=True)
    stopped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stopped_by: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str | None] = mapped_column(Text)


class Worker(Base):
    """A worker process re-asserts `seen_at` on every heartbeat, so a stopped or hung worker shows as gone."""
    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # settings.PROCESS_ID
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BudgetMonth(Base):
    __tablename__ = "budget_months"
    month: Mapped[str] = mapped_column(String(7), primary_key=True)  # YYYY-MM
    limit_usd: Mapped[float] = mapped_column(Float)
    spent_usd: Mapped[float] = mapped_column(Float, default=0.0)
    reserved_usd: Mapped[float] = mapped_column(Float, default=0.0)


class BudgetReservation(Base):
    """One call's hold on the month's and the project's `reserved_usd`, from reserve to settle. The job lease it was
    taken under (none outside a job) tells the orphan release when its holder is gone."""
    __tablename__ = "budget_reservations"
    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[str] = mapped_column(String(7))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    amount_usd: Mapped[float] = mapped_column(Float)
    job_id: Mapped[int | None] = mapped_column(Integer)
    lease_token: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemoryItem(Base):
    """Company memory: embedded verified knowledge and decisions, searchable across projects."""
    __tablename__ = "memory_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))          # evidence | decision | report
    ref_id: Mapped[int] = mapped_column(Integer)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("kind", "ref_id"),)


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))           # job kind to enqueue
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    interval_hours: Mapped[float] = mapped_column(Float)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("kind", "name"),)


class Kpi(Base):
    __tablename__ = "kpis"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str | None] = mapped_column(String(40))
    target: Mapped[float] = mapped_column(Float)
    direction: Mapped[str] = mapped_column(String(8), default="up")   # up | down
    rationale: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(24), default="manual")   # manual | report | connector
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KpiValue(Base):
    __tablename__ = "kpi_values"
    id: Mapped[int] = mapped_column(primary_key=True)
    kpi_id: Mapped[int] = mapped_column(ForeignKey("kpis.id"), index=True)
    value: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40), default="manual")
    note: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ActionExecution(Base):
    """Tool Gateway ledger of external side effects. idempotency_key prevents duplicate execution."""
    __tablename__ = "action_executions"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("agent_requests.id"))
    grant_id: Mapped[int | None] = mapped_column(ForeignKey("grants.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    action_kind: Mapped[str] = mapped_column(String(64))
    connector: Mapped[str] = mapped_column(String(40))
    mode: Mapped[str] = mapped_column(String(16))           # outbox | live
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="started")  # started | done | failed | not_sent
    result_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ActivityEvent(Base):
    """Audit trail: who changed what, when. Written by a session hook (services/activity.py), so every
    status change is recorded no matter which service or graph node made it."""
    __tablename__ = "activity_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    actor: Mapped[str] = mapped_column(String(60))            # owner | agent id | system | policy
    verb: Mapped[str] = mapped_column(String(32))             # created | status | resolved | budget | paused | ...
    entity: Mapped[str] = mapped_column(String(24))           # project | task | request | decision | dev_job | ...
    entity_id: Mapped[int | None] = mapped_column(Integer)
    project_id: Mapped[int | None] = mapped_column(Integer, index=True)
    business_id: Mapped[int | None] = mapped_column(Integer, index=True)
    summary: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class AppSetting(Base):
    """One owner-edited settings section (usage, connectors, policy, notifications). `value` holds only the
    fields the owner changed; config/company.yaml supplies the rest (services/app_settings.py merges them)."""
    __tablename__ = "app_settings"
    section: Mapped[str] = mapped_column(String(24), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    updated_by: Mapped[str] = mapped_column(String(60))


class CrmContact(Timestamped, Base):
    __tablename__ = "crm_contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    organization: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(60))
    consent: Mapped[bool] = mapped_column(Boolean, default=False)   # contact consent recorded by a human
    notes: Mapped[str | None] = mapped_column(Text)


class CrmInteraction(Base):
    __tablename__ = "crm_interactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("crm_contacts.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(32))     # email | call | interview | meeting | note
    summary: Mapped[str] = mapped_column(Text)
    action_execution_id: Mapped[int | None] = mapped_column(ForeignKey("action_executions.id"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
