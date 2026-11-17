# Candidate B: a stage is a Bet the owner starts (reuse the experiment machinery)

Direction: minimize new concepts. A **stage** is an existing `Bet` row with `kind='stage'`, living in a revived `cycles`
row. Its run is the existing `Project` (`Project.bet_id`). "Start" is the existing owner `PICKED -> RUNNING`
(`roadmap.move_bet` -> `_start_run`). "Stage ends" is the existing `RUNNING -> REVIEW -> DONE` with the existing
`bet_retro` job slot writing the summary. The gate is one policy table consulted at `queue.claim`.

New schema: 5 columns on `bets`, 8 on `cycles`, 2 on `businesses`, 2 partial unique indexes. No new tables, no new job
status, no new graph node, one added line in `company_graph.report`.

Binding principle folded in (coordinator, 2026-11-06): **the owner approves; what was approved runs automatically; nothing that
spends model usage or produces owner work runs without that approval.** The gate (section 3) is default-deny: a job kind runs
only if `JOB_GATE` declares why (FREE allow-list, ASKED = the owner directly asked, OF_STAGE = part of a stage the owner
started, RECURRING = listed in the stage the owner saw before pressing Start). Undeclared kinds are denied at enqueue (error),
at claim (authority) and at `run_agent` (fail-closed tripwire).

Verified in repo (file:line as read 2026-11-06): `Cycle` models.py:141 (never written), `Bet` :168, `move_bet`
roadmap.py:971 (idempotent same-status return, row lock via `_bet`), `_start_run` :940 (creates Project, enqueues
`run_project` with idempotency key `run:{id}`), `_take_slot`/`running_count` :880/:930, `BET_TRANSITIONS` :64
(RUNNING -> REVIEW and RUNNING -> DONE already legal), `bet_loop.due_move`/`sweep`/`_chase_retro` (retro rounds, stuck
flag, retry flip-back), `queue.claim` queue.py:450, `queue.Hold`, `scope.owner_of`, worker `_handlers()`
workers/runner.py:30 (the one list of job kinds), `run_agent` executor.py:98 (`queue.check_lease()` first line),
`report` node sets Project DONE company_graph.py:1043, `overview` console.py:422, `_agent_states` :86.

---------------------------------------------------------------------------------------------------------------------

## 1. Usage (caller's view)

### 1.1 Owner story, as the backend sees it

```
brief approved (Composer Done = one POST /briefs/{id}/approve; no /start call any more)
  -> cycles.on_brief_approved   (inside the approve transaction)
       creates cycles row #1 status=DRAFT, enqueues cycle_propose (ASKED: the approval is the request)  [phase PROPOSING]
       meanwhile find_people / find_threads / promotion_week ... are DENIED at claim: no RUNNING stage lists them
  -> job cycle_propose  -> cycles.save_proposal(...)  cycle.proposal=v1      [phase PROPOSED, card: Start / Say what to change]
  -> owner: POST /cycles/7/revise {version:1,text:"skip design"} -> job cycle_revise -> proposal v2 + changes[]
  -> owner: POST /cycles/7/start {version:2}
       one transaction: reconcile proposal into stage Bets (PICKED, ordered), cycle ACTIVE,
       business.cycle_mode='staged', then move_bet(stage1, RUNNING)  -> Project + run_project job   [STAGE_RUNNING]
  -> run ends (Project DONE; company_graph untouched) -> sweep / report hook -> move_bet(REVIEW, end_cause=run_done)
       -> bet_retro job (kind=stage) -> stage_summary.apply: bet.summary written, move_bet(DONE)    [AT_GATE]
  -> NOTHING ELSE RUNS. Owner: POST /cycles/7/start {version:2}  (starts next PICKED stage)
       or /revise (changes remaining stages, shown as a diff) or /stop.
  -> last stage summary also carries cycle_retro + next_cycle proposal -> cycle CLOSED, next cycle DRAFT with proposal   [PROPOSED]
```

### 1.2 Call sites (real code the sketch must satisfy)

```python
# api/cycle.py (thin)
@router.post("/cycles/{cycle_id}/start")
def cycle_start(cycle_id: int, body: Version) -> dict:
    return _act(cycles.start, cycle_id, expect_version=body.version)          # returns board for the business

# services/roadmap.py::_on_approved  (the only enqueue site for the first proposal)
cycles.on_brief_approved(s, brief, lang)

# services/queue.py::claim  (the only gate site)
while job is not None:
    verdict = stage_gate.admit(s, job.kind, job.payload)     # unknown kind -> DENY
    if verdict.allowed: break
    decline(s, job, verdict.reason)           # finished as done, wait_reason='stage_gate', no attempt burned
    job = next_candidate()

# orchestration/company_graph.py::report  (after Project DONE; one added line)
bet_loop.settle_now(s, p)                     # same due_move/_move the 15-min sweep uses, so the stage ends at once

# web Home.tsx
const { data: board } = useBusinessLive<CycleBoard>(`/businesses/${id}/cycle`, CYCLE_TABLES)
<StatusSentence line={board.headline}/> <GateCard gate={board.gate}/> <StageStrip stages={board.stages}/> <WorkCards cards={board.cards}/>
```

---------------------------------------------------------------------------------------------------------------------

## 2. Type sketch

### 2.1 SQLAlchemy deltas (db/models.py)

```python
CYCLE_MODES = ("legacy", "staged")
CYCLE_STATUSES = ("DRAFT", "ACTIVE", "CLOSED")            # DRAFT = a proposal the owner has not started
BET_KINDS = ("experiment", "stage")

class Business:                                            # +2
    # legacy = had owner-started work before the gate existed (that work is grandfathered); staged = everything else.
    # NOT read by stage_gate.admit: legacy businesses get no exemption for background work.
    cycle_mode: Mapped[str] = mapped_column(String(8), default="staged", server_default="staged")
    staged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # gate applies from here

class Cycle:                                               # revived; +8
    status: Mapped[str] = mapped_column(String(8), default="DRAFT", server_default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # proposal version the owner saw (optimistic lock)
    origin: Mapped[str] = mapped_column(String(8), default="first")               # first | next | mapped
    proposal: Mapped[dict | None] = mapped_column(JSONB)       # the PENDING proposal (CycleProposal JSON) or null. The stage Bets are truth once started
    revisions: Mapped[list] = mapped_column(JSONB, default=list)   # [{version, by, text|None, changes:[...], at}] history, never read for logic
    retro: Mapped[dict | None] = mapped_column(JSONB)          # CycleRetroOut after the last stage
    end_reason: Mapped[str | None] = mapped_column(String(12)) # completed | owner_stop | legacy_unused
    # existing: number, starts_on, ends_on (target, a guess; not enforced), closed_at, partial unique "one open per business"
    # (DRAFT and ACTIVE both have closed_at NULL, so a business has exactly one live cycle row)

class Bet:                                                 # +5
    kind: Mapped[str] = mapped_column(String(10), default="experiment", server_default="experiment")
    stage_key: Mapped[str | None] = mapped_column(String(24))
    position: Mapped[int | None] = mapped_column(Integer)      # gapped: 10,20,30 so a revision can insert without renumbering
    stage_spec: Mapped[dict | None] = mapped_column(JSONB)     # StageSpec JSON (what the company does, what the owner does, guesses)
    summary: Mapped[dict | None] = mapped_column(JSONB)        # StageSummary JSON; `retro` stays experiment-only
    __table_args__ += (
        CheckConstraint("kind IN ('experiment','stage')", name="ck_bets_kind"),
        CheckConstraint("kind <> 'stage' OR (cycle_id IS NOT NULL AND stage_key IS NOT NULL AND position IS NOT NULL)",
                        name="ck_bets_stage_fields"),
        # THE single source of truth for "which stage is running": the database allows one live stage per business.
        Index("ux_bets_one_live_stage", "business_id", unique=True,
              postgresql_where=text("kind = 'stage' AND status IN ('RUNNING','REVIEW')")),
        Index("ux_bets_stage_key", "cycle_id", "stage_key", unique=True,
              postgresql_where=text("kind = 'stage' AND status <> 'DROPPED'")),
    )
```

No column on `Task`, `Project`, `Job`, `LLMCallRecord`. Stage of a task = `Task.project_id -> Project.bet_id -> Bet(kind=stage)`.
Stage spend is derived, not stored (see 4.4).

### 2.2 Domain types (services/cycles.py, pure, frozen)

```python
class Phase(StrEnum):
    NO_CYCLE = "no_cycle"             # legacy business not yet mapped, or a business with no approved brief
    PROPOSING = "proposing"           # DRAFT, no proposal yet, cycle_propose alive
    PROPOSAL_FAILED = "proposal_failed"  # no proposal, no job alive, rounds spent: owner can ask again
    PROPOSED = "proposed"             # pending proposal, nothing live
    REVISING = "revising"             # cycle_revise alive
    STAGE_RUNNING = "stage_running"   # live stage RUNNING
    STAGE_SUMMARIZING = "stage_summarizing"   # live stage REVIEW
    AT_GATE = "at_gate"               # no live stage, a finished stage, a next PICKED stage
    STAGE_STOPPED = "stage_stopped"   # last stage failed/stopped: redo, revise or stop
    STOPPED = "stopped"               # owner ended the cycle; no pending proposal

@dataclass(frozen=True)
class StageRow:
    id: int; key: str; title: str; position: int
    status: BetStatus; spec: StageSpec
    started_at: datetime | None; finished_at: datetime | None
    project_id: int | None
    tasks_done: int; tasks_total: int          # total excludes CANCELLED/REPLACED/REJECTED
    spent_usd: float

@dataclass(frozen=True)
class CycleFacts:                              # everything phase_of needs; assembled by ONE function (load_facts)
    business_id: int; mode: Literal["legacy", "staged"]
    cycle: CycleRow | None                     # the one open (DRAFT/ACTIVE) row, else the latest CLOSED
    stages: tuple[StageRow, ...]               # ordered, DROPPED excluded
    pending: CycleProposal | None
    jobs_alive: frozenset[str]                 # {"cycle_propose","cycle_revise","bet_retro"} for this business
    propose_rounds: int

def phase_of(f: CycleFacts) -> Phase: ...      # PURE. The phase is derived, never stored.
```

### 2.3 CEO structured outputs (contracts/cycle.py, Pydantic; these are also the stored JSON)

```python
class StageSpec(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,23}$")
    title: str = Field(max_length=60)
    company_does: str; owner_does: str
    decision_points: list[str] = Field(default_factory=list, max_length=4)
    est_hours_guess: float = Field(ge=0, le=400)       # always shown labelled "guess"
    est_usd_guess: float = Field(ge=0, le=500)
    wait_hint_days: int | None = Field(None, ge=0, le=60)   # "start ~3 days later so reactions arrive"; shown, never enforced
    recurring: list[str] = Field(default_factory=list)       # scheduled job kinds this stage permits; checked against stage_gate.RECURRING_KINDS
    question: str | None = None; exit_signal: str | None = None   # experiment-shaped stages (promote, reaction) fill the Bet columns

class CycleProposal(BaseModel):                         # CEO output of cycle_propose, and the stored pending proposal
    pitch: str = Field(max_length=500)                  # two or three sentences
    stages: list[StageSpec] = Field(min_length=4, max_length=9)
    @model_validator(mode="after")
    def _unique_keys(self): ...

class StageReason(BaseModel): key: str; why: str
class CycleRevisionOut(CycleProposal):                  # CEO output of cycle_revise: the WHOLE plan again + why per touched stage
    reasons: list[StageReason] = Field(default_factory=list)
    # `changes` is NOT model output. cycle_plan.diff(old, new) computes it, so "what changed" is true by construction.

class MappedStage(BaseModel):
    key: str
    state: Literal["done", "running", "ahead"]
    bet_ids: list[int] = Field(default_factory=list)    # legacy bets the stage stands for (validated against the business)
    note: str = ""
class LegacyMapOut(CycleProposal):                      # CEO output of cycle_propose(kind='map')
    mapping: list[MappedStage]

class EvidenceLine(BaseModel):
    claim: str
    confidence: Literal["low", "medium", "high"]        # the model's claim; code lowers it to the ledger cap (4.4)
    refs: list[str] = Field(min_length=1)               # T-12, E-5, A-3 ... must exist in StageFacts or the line is dropped to low
class StageSummaryOut(BaseModel):
    done: list[str]; not_done: list[str]; evidence: list[EvidenceLine]; next_note: str
class CycleRetroOut(BaseModel):
    learned: list[str]; went_wrong: list[str]; verdict: Literal["continue", "change", "stop"]
class LastStageSummaryOut(StageSummaryOut):             # used iff the stage is the cycle's last: one call, cost lands on that stage
    cycle_retro: CycleRetroOut
    next_cycle: CycleProposal
```

### 2.4 Read-model contract (contracts/cycle.py) and TypeScript view types (web/src/cycle/types.ts)

```python
class BoardOut(BaseModel):
    business_id: int; mode: Literal["legacy", "staged"]; phase: Phase
    headline: Headline                       # {key, params}; web renders it from catalogs (no prose from the backend)
    cycle: CycleHead | None
    gate: GateCard | None                    # top of the page: proposal, stage end, stage failed, proposal failed
    stages: list[StripStage]
    current: CurrentStage | None
    cards: list[WorkCard]; idle_agents: int
    history_url: str
```

```ts
export type Phase = "no_cycle" | "proposing" | "proposal_failed" | "proposed" | "revising" | "stage_running"
  | "stage_summarizing" | "at_gate" | "stage_stopped" | "stopped";
export type Headline = { key: string; params: Record<string, string | number> };   // e.g. cycle.line.running {n:2, stage:"validate", agents:3}
export type StageSpec = { key: string; title: string; company_does: string; owner_does: string; decision_points: string[];
  est_hours_guess: number; est_usd_guess: number; typical_usd: number | null; wait_hint_days: number | null; recurring: string[] };
export type StripStage = { id: number | null; key: string; title: string; position: number;
  state: "done" | "running" | "summarizing" | "next" | "ahead" | "stopped" | "proposed";
  tasks: { done: number; total: number } | null; spent_usd: number; stop_after: boolean };   // stop_after = the pause marker
export type Change = { key: string; kind: "added" | "removed" | "changed" | "moved"; fields: string[]; why: string | null };
export type Evidence = { claim: string; confidence: "low" | "medium" | "high"; capped_by: string | null; refs: string[] };
export type StageSummary = { done: string[]; not_done: string[]; evidence: Evidence[]; cost_usd: number; guess_usd: number;
  tasks: { done: number; total: number }; duration_s: number; next_note: string; source: "stage" | "mapped" };
export type GateCard =
  | { kind: "proposal"; version: number; pitch: string; stages: StageSpec[]; changes: Change[] | null; actions: ("start" | "revise")[] }
  | { kind: "working"; what: "proposing" | "revising"; since: string }
  | { kind: "stage_end"; ended: { id: number; title: string; summary: StageSummary }; next: StageSpec & { id: number } | null;
      version: number; actions: ("start_next" | "revise" | "stop")[]; suggest_after: string | null }
  | { kind: "stage_stopped"; stage: { id: number; title: string; why: string }; version: number; actions: ("redo" | "revise" | "stop")[] }
  | { kind: "failed"; what: "proposal"; actions: ("propose")[] };
export type CurrentStage = { id: number; title: string; spec: StageSpec; started_at: string; project_id: number;
  tasks: { id: number; title: string; owner: string; status: string }[]; counts: { done: number; total: number };
  blocked_on_owner: boolean };
export type WorkCard = { agent: string; task_id: number | null; dev_job_id: number | null; title: string; role: "work" | "review" | "dev";
  cycle_number: number | null; stage: { id: number; key: string; title: string } | null;     // null = pre-gate work still finishing
  started_at: string | null; spent_usd: number; project_id: number };
export type CycleBoard = { business_id: number; mode: "legacy" | "staged"; phase: Phase; headline: Headline;
  cycle: { id: number; number: number; version: number; pitch: string } | null; gate: GateCard | null;
  stages: StripStage[]; current: CurrentStage | null; cards: WorkCard[]; idle_agents: number };
```

---------------------------------------------------------------------------------------------------------------------

## 3. The gate: services/stage_gate.py (default-deny, one policy module)

Binding owner principle (coordinator, 2026-11-06): the owner approves; what was approved runs automatically; **nothing that
spends model usage or produces owner work runs without that approval.** The gate is therefore **default-deny**: a job kind
runs only if it is declared in `JOB_GATE` with a class that says why it may run. An undeclared kind is denied, in three places
(3.2). The gate does not read `Business.cycle_mode` to decide: the rule is the same for new and legacy businesses.
(The failure it fixes: after plan approval, scheduled `find_people`/`find_threads` ran with `first_in_hours=0` and filled the
owner's to-do list. With no RUNNING stage, both are now denied, because no approved stage lists them.)

### 3.1 Classes and the explicit table

| Class | Meaning | Admitted when |
|---|---|---|
| `DENY` | the default for any kind not in the table | never |
| `FREE` | allow-list: **no model call and no owner work**; consequences of something the owner already decided | always |
| `ASKED` | the owner directly asked for this exact call | always (named exceptions, each justified below) |
| `OF_STAGE` | work belonging to a run the owner started | its governing stage is live (4.2) |
| `RECURRING` | scheduled background work | a RUNNING stage whose `spec.recurring` lists this kind, i.e. the owner saw it in the proposal before pressing Start |

```python
JOB_GATE: dict[str, Gate] = {
  # FREE: no run_agent, creates no owner work. The tripwire (3.3) makes a wrong entry fail loudly in tests.
  "notify": FREE, "daily_briefing": FREE, "execute_action": FREE,     # delivers text/an action the owner already approved (outbox default)
  "retention": FREE, "backup": FREE, "bet_sweep": FREE, "post_reactions": FREE,
  "spec_approved": FREE, "dev_merge": FREE, "dev_merge_declined": FREE, "dev_merged": FREE,   # consequences of an owner decision card
  # ASKED: one owner act enqueues one call, nothing repeats by itself
  "kickoff_turn": ASKED,        # the owner typed a message in the kickoff conversation
  "product_report": ASKED,      # the owner chose "read my product" in the kickoff
  "triage_idea": ASKED,         # the owner submitted an idea; this is the answer
  "idea_review": ASKED,         # the owner asked for the idea panel on that idea
  "derive_door_rules": ASKED,   # enqueued by the owner's own approval click, once per brief version; produces rules, not to-dos
  "reassess_project": ASKED,    # the owner approved a reassessment card
  "skill_activate": ASKED,      # the owner approved a skill card
  "cycle_propose": ASKED,       # the plan approval, "propose next cycle", or the one owner-decided mapping of existing work (4.6)
  "cycle_revise": ASKED,        # the owner wrote what to change
  # OF_STAGE: hangs off a Project (or Bet) of the stage the owner started
  "run_project": OF_STAGE, "resume_project": OF_STAGE, "retry_project": OF_STAGE, "realign_project": OF_STAGE,
  "write_spec": OF_STAGE, "spec_questions": OF_STAGE, "dev_job": OF_STAGE, "dev_resume": OF_STAGE, "dev_deliver": OF_STAGE,
  "dev_fix": OF_STAGE, "dev_fix_answered": OF_STAGE, "acceptance": OF_STAGE,
  "bet_retro": OF_STAGE,        # stage summary of the live stage in REVIEW; retro of an owner-started (grandfathered) experiment
  # RECURRING: only inside a stage that lists the kind
  "find_people": RECURRING, "find_threads": RECURRING, "promotion_week": RECURRING, "github_collect": RECURRING,
  "kpi_review": RECURRING, "evidence_watch": RECURRING, "routine_run": RECURRING,
  "portfolio_review": RECURRING, "skill_factory": RECURRING,          # company level: any business's RUNNING stage listing the kind
}
```

Notes on the choices:
- `github_collect` makes no model call, but it feeds the promotion loop and writes numbers the owner reads, so per the principle
  it runs only inside a stage that lists it. `post_reactions` only reads reactions already posted by approved posts: FREE.
- **Routines** (`routine_run`) are RECURRING. Approved routine schedules do not run between stages and do not run in a stage
  that does not list `routine_run`. On the first mapped proposal (4.6) the CEO lists them in the stages where they belong, so
  approving that proposal is the approval. Until then they pause and the board says "2 routines wait for the plan".
- **`bet_retro`-created follow-ups are removed for every business**: `_next_bets` (new bets, auto-PICK), `_routine_card` and the
  out-of-scope decision cards no longer create rows. They are written into the retro JSON (`proposed_next`, `routine_suggestion`)
  and become input to the next `cycle_propose`, which is where the owner sees and approves them. The retro of an
  owner-started experiment still runs (it closes work the owner started); it just cannot start anything.
- **The owner's own typed/clicked calls stay available at any time** (the listed ASKED kinds), because the owner asked for
  them; they spend one call per click and enqueue nothing else on their own. Any job one of them enqueues is gated by its own
  class. `test_asked_enqueue_sites` pins the enqueue sites of every ASKED kind to the owner-act call paths; a new site fails
  the test until a person reviews it.
- `RECURRING_KINDS` (what `StageSpec.recurring` may contain) is **derived**: `tuple(k for k, g in JOB_GATE.items() if g is RECURRING)`.

### 3.2 Where default-deny is enforced (three places, one table)

1. **`queue.enqueue` (programmer-error fence).** First line: `stage_gate.require_declared(kind)` raises `UndeclaredJobKind` when
   `kind not in JOB_GATE`. A developer who adds a job kind finds out on the first test or run, not in production.
2. **`queue.claim` (the authority).** `claim` is the only function that turns a queued row into running work (workers/runner.py:294),
   so no enqueue site, old row, or raw SQL can bypass it. The decision is made at claim, not at enqueue, because a stage can end
   between the two (a retry queued in stage 1 must not run in the gap before stage 2) and because 12+ enqueue sites exist.

```python
def claim(...):
    ...existing query...
    job = s.scalar(q)
    for _ in range(CLAIM_SCAN):                      # 50; each pass returns a job or removes one from the queue
        if job is None: return None
        verdict = stage_gate.admit(s, job.kind, job.payload)    # JOB_GATE.get(kind, Gate.DENY): unknown kind -> denied
        if verdict.allowed: break
        stage_gate.decline(s, job, verdict)           # status='done', last_error='stage_gate: <why>', wait_reason='stage_gate'
        job = s.scalar(q.where(Job.id.not_in(...)))
    ...existing: mark running, lease token, attempts += 1 ...
```

   `claim` runs under `take_claim_lock`, so `admit` reads a consistent view and two workers cannot disagree. A declined job leaves
   the queue (no polling, no pile-up); a declined schedule tick is skipped and the next tick asks again. One `activity` row per
   (business, kind, day) records the decline so the board can say "2 scheduled jobs skipped while no stage ran".
3. **`executor.run_agent` (the tripwire, fail-closed).** See 3.3.

```python
def admit(s: Session, kind: str, payload: dict) -> Verdict:
    gate = JOB_GATE.get(kind, Gate.DENY)             # <- the default-deny line
    if gate in (Gate.FREE, Gate.ASKED): return ALLOW
    if gate is Gate.DENY: return deny("undeclared job kind")
    pid, bid = scope.owner_of(s, payload)
    if gate is Gate.OF_STAGE: return _of_stage(s, pid, bid, payload)       # governing() (4.2)
    return _recurring(s, kind, bid)                  # RECURRING: live RUNNING stage whose spec.recurring contains kind;
                                                     # business None (company level): any business's RUNNING stage lists it
```

### 3.3 Tripwire (second line; adds no rule, calls the same `admit_call`)

`workers/runner._execute` sets `stage_gate.current_gate` (ContextVar) to `JOB_GATE.get(kind, DENY)` next to `queue.current_lease.set`.
`executor.run_agent`, right after `queue.check_lease()`, calls `stage_gate.tripwire(business_id, project_id)`:

- `FREE` or `DENY` -> raise `GateViolation` (a job declared model-free called a model, or an undeclared kind slipped through: fails loudly, the job is marked failed and alerts)
- `OF_STAGE`/`RECURRING` -> re-run `admit_call` for the business; if the stage closed mid-job raise `queue.Hold(900, "stage_closed")` (not an error; the job goes back and `claim` then declines it)
- `ASKED` -> pass
- **no gate context at all (a model call outside a worker job)** -> raise `GateViolation`. Fail-closed. The CLI/test entry points that
  legitimately call a model wrap it in `with stage_gate.asked("cli:<name>")`, which is greppable. (Open question: confirm via
  `rg "run_agent\("` that every caller is reached from a job handler; the 15 modules found are handlers or helpers of handlers.)
- **owner-work tripwire**: `services/owner_tasks.add(...)` and `people_tasks`, i.e. every function that writes an `OwnerTask`,
  calls `stage_gate.owner_work_allowed()` when a worker lease is present: it raises unless `current_gate` is `OF_STAGE`, `RECURRING`
  (already admitted for a live stage) or `ASKED`. So even a model-free kind cannot put a to-do in the owner's list unless an
  approved stage or an owner act is behind it.

### 3.4 Completeness is a build failure, not a convention

```python
def test_every_handler_is_declared():            assert set(workers.runner._handlers()) == set(stage_gate.JOB_GATE)   # a new kind without a class fails CI
def test_undeclared_kind_is_denied():            assert stage_gate.admit(s, "brand_new_kind", {}).allowed is False
def test_enqueue_rejects_undeclared_kind():      with pytest.raises(UndeclaredJobKind): queue.enqueue(s, "brand_new_kind", {})
def test_enqueue_is_the_only_job_constructor():  grep "Job(" in services/ operations/ == queue.py only
def test_only_cycles_creates_stage_bets():       grep 'kind="stage"' / "kind='stage'" only in services/cycles.py (+ migration)
def test_asked_enqueue_sites():                  per ASKED kind, the enqueue call sites equal a reviewed allow-list
def test_no_gate_no_model():                     run_agent outside any gate context raises GateViolation
def test_scheduled_kinds_denied_without_stage(): find_people/find_threads/promotion_week/github_collect/kpi_review/
                                                 evidence_watch/portfolio_review/skill_factory/routine_run declined with no RUNNING stage,
                                                 admitted only in a stage listing them
```

---------------------------------------------------------------------------------------------------------------------

## 4. State machine, stage lifecycle, boundary behavior

### 4.1 Stage = Bet status (no new states)

```
        (revise: key dropped)
PICKED --------------------------------> DROPPED
  |  owner Start (cycles.start -> move_bet)
  v
RUNNING --run settled (Project DONE and quiet)--> REVIEW --stage_summary.apply--> DONE
  |  \-- run FAILED past grace / budget --------> REVIEW(end_cause=run_failed|budget_limit) --apply--> STOPPED
  |  owner "stop now"                            (REVIEW -> RUNNING flip-back exists: _chase_retro when owner retries the run, only before a summary exists)
  v
STOPPED    (redo = cycles.redo_stage: clones a PICKED stage with the same key at the same position)
```

Cycle: `DRAFT --start--> ACTIVE --last stage summary or owner stop--> CLOSED`. Business: `legacy --first accepted proposal--> staged` (one way).

Differences from experiment bets, all in three small branches (named, tested):
1. `roadmap.window_end` returns None for `kind='stage'` -> no time window, no `time_limit`.
2. `bet_loop.due_move`: run DONE and stage -> `(REVIEW, "run_done")` instead of `(LISTENING, None)`; and the stage must be quiet (4.3).
3. `bet_retro.run`: `bet.kind == 'stage'` -> `stage_summary.run`. `_next_bets` never runs for a stage (no new experiments appear).
Everything else (retry rounds, "stuck" flag, redo retro button, budget-stop, retry flip-back, pause of the run on STOPPED) is the existing code path.

### 4.2 Single source of truth for "which stage is running", and the single writer

- **Truth**: the row `Bet(kind='stage', status IN (RUNNING, REVIEW))`, at most one per business, enforced by `ux_bets_one_live_stage`.
  `cycles.live_stage(s, business_id) -> Bet | None` is the only reader of that fact; `phase_of`, `stage_gate`, the board and
  `executor` tripwire all call it. Nothing caches it (no flag on Business or Cycle).
- **Single writer**: `services/cycles.py` owns every write to `cycles` rows and every creation/edit of stage Bets
  (`_create_stage`, `reconcile`, `adopt_running_bet`, `redo_stage`, `stop`). Status moves of a stage Bet are made by
  `roadmap.move_bet` (the mechanism the sweep and summary also use); `move_bet` calls `cycles.check_stage_move(s, bet, new)`
  (lazy import) for `kind='stage'`, which holds all policy: only the next PICKED stage in position order may start; the
  cycle must be ACTIVE and the business `staged`; no live stage; no pending proposal with a higher version than the owner saw
  (the `expect_version` check lives in `cycles.start`). The generic `POST /bets/{id}/move` route therefore still works on a
  stage and cannot skip the rules.
- `governing(s, project) -> Governing` (in stage_gate) is how any project maps to its stage:
  `bet.kind=stage` -> that stage must be live; `bet.kind=experiment` with `loop_started_at` -> **grandfathered** (admit); no bet ->
  needs a RUNNING stage of its business; no business at all -> not covered by a stage, so an OF_STAGE kind is denied.
  `Business.cycle_mode` is **not** an input to `admit`: legacy businesses get no exemption for scheduled or background work.
  The only exemption is **grandfathering of owner-started runs**: a project whose bet is an experiment with `loop_started_at`
  (the owner pressed Start before the gate existed) keeps running to its end, with its dependent OF_STAGE jobs. It needs no marker
  column: in a staged business `move_bet(experiment -> RUNNING)` is refused, and new businesses default to `staged`, so every
  experiment with `loop_started_at` in such a business was started before the gate.

### 4.3 What stops at a boundary, and what happens to in-flight work

A stage ends by **quiescence, not preemption**. `due_move` returns `(REVIEW, run_done)` only when:
`Project.status == DONE` and no `OF_STAGE` job is queued or running for the stage's project, and no `RECURRING` job is running
for the business (`stage_gate.stage_busy(s, bet)` via `scope.owner_of`, the same helper `archive_business` uses). Queued
RECURRING jobs do not block: they are declined at claim once the stage is in REVIEW.
A dev job still queued or merging, a promotion round still posting: the stage waits for it (it is the owner's stage; the spend is theirs).
Nothing is killed by the gate.

From REVIEW on, `admit` refuses new `RECURRING` jobs (they require `RUNNING`), so the stage cannot be re-fed. The project's own
`OF_STAGE` jobs (retry/resume, and the `bet_retro` that writes the summary) are still admitted in REVIEW (the owner may retry a failed
run, which flips the stage back to RUNNING via the existing `_chase_retro`).

| What | At the boundary |
|---|---|
| Next stage | never starts by itself: the only caller of `move_bet(stage -> RUNNING)` is `cycles.start`, which only an owner route calls |
| Queued `OF_STAGE`/`RECURRING` jobs of the business | declined at claim while no stage is live (default-deny) |
| Scheduled ticks (`find_people`, `find_threads`, `promotion_week`, `github_collect`, `kpi_review`, `evidence_watch`, `routine_run`, `portfolio_review`, `skill_factory`) | enqueued by the scheduler as today, declined at claim unless the RUNNING stage lists the kind (no accumulation) |
| Follow-ups of a retro (`_next_bets`, auto-PICK, out-of-scope decision cards, routine cards) | suppressed for **every** business: stored in the retro JSON (`proposed_next`, `routine_suggestion`); the next `cycle_propose` reads them, so the owner sees them in a proposal they approve |
| Owner decisions inside the stage (WAITING_HUMAN) | unchanged: stage stays RUNNING, `blocked_on_owner=true` on the board (orange) |
| Owner `control.pause` of the stage's run | unchanged, orthogonal to the gate |
| Owner Stop of a RUNNING stage | existing `move_bet(STOPPED)` pauses the run; phase `STAGE_STOPPED` |

Idempotency and crashes:
- **Double click Start**: `_bet` takes the row lock; `move_bet` returns early when already RUNNING; two different stage ids
  racing hit `ux_bets_one_live_stage` -> `IntegrityError` caught in `cycles.start` -> `ControlError("another stage is running")`.
  A stale page (old `expect_version`) -> `ControlError` 409.
- **Crash during Start**: Project insert, `run_project` enqueue (idempotency key `run:{pid}`), `started_at`, status are one
  transaction: all or nothing.
- **Crash between Project DONE and REVIEW**: `settle_now` is a fast path only; the 15-minute sweep finds the same
  `due_move` (idempotent, `_move` under savepoint).
- **Crash between REVIEW and summary**: `_chase_retro` re-queues `bet_retro` (key `bet_retro:{id}:{n+1}`); `stage_summary.apply`
  returns unchanged when `bet.status != REVIEW or bet.summary is not None`, so a double run stores one summary.
- **Crash during Accept (materializing stages)**: single transaction; `reconcile` is keyed by `stage_key`, so re-running it
  changes nothing.
- **Revision lands while owner clicks Start on the old plan**: `expect_version` mismatch -> refused, board refetches.

### 4.4 How a stage ends and its summary (done, cost, evidence with confidence)

`stage_summary.run(bet_id)` (the `bet_retro` job for a stage; the job holds no transaction during the model call, like `bet_retro.run`):

1. `build_facts(s, bet) -> StageFacts` (code, no model): tasks with status/owner/artifact ids, counts n/m, duration,
   **cost** = `Project.spent_usd` of the stage's run + `llm_calls.cost_usd` with `business_id` and no project inside
   `[bet.started_at, bet.finished_at or now]` (a stage is the only live thing in its window, so a window sum is exact; no
   `stage_id` stamping on the ledger, no second copy of cost), the owner to-dos done in the window, the evidence ledger rows
   of the project with their verification status (`services/evidence.py`), the failure reason if the run failed.
2. `run_agent("ceo", "stage_summary", prompt, StageSummaryOut | LastStageSummaryOut, project_id=run.id, ...)`.
3. `apply(...)` in one transaction: validate refs; **confidence = min(model's claim, ledger cap)** where the cap comes from the
   evidence rows cited (`verified_fact` -> high, `sourced_claim` -> medium, hypothesis/assumption/unknown ref -> low) and the
   line records `capped_by`; store `Bet.summary` (facts + narrative + `guess_usd` from `stage_spec`), `move_bet(DONE)`
   (or `STOPPED` when `end_cause` is a failure), activity row `stage_done`. For the last stage also: `Cycle.retro`, close the
   cycle (`closed_at`, `status=CLOSED`, `end_reason=completed`), insert the next `DRAFT` cycle with `proposal = next_cycle`.
   The next-cycle proposal is part of the last stage's own call, so no unstarted spend happens after a cycle ends.
4. Where the guess comes from: `est_*_guess` is the CEO's, shown as guess. `typical_usd` is derived from earlier CLOSED
   cycles' stage summaries with the same `stage_key` (`cycles.typical_spend`), shown next to it from cycle 2 on.

### 4.5 Proposal, revision, mapping (CEO flow)

- `request_proposal(s, business_id, kind)` (kind in first|next|map): lock the business, get-or-create the one open `DRAFT` cycle,
  enqueue `cycle_propose` with key `cycle_propose:{cycle_id}:{rounds+1}` (lock_key `cycle:{business_id}` added to `queue._lock_key`,
  so propose/revise never overlap). Packet = approved brief, product reading, prior stage summaries, retro `next_bets`,
  typical spend; for `map` also the business's live projects/bets/tasks (like `current_state.py`) and the JSON-schema for `LegacyMapOut`.
- `save_proposal(s, cycle_id, out, base_version)`: validates (`cycle_plan.check`), stores `cycle.proposal`, `version += 1`, appends to
  `revisions`. If `cycle.version != base_version` it returns unchanged (a repeated job writes once).
- `revise(s, cycle_id, text, expect_version)`: refuses when a live stage exists unless the text only concerns later stages (the
  prompt pins started stages; `reconcile` ignores any model edit to a started stage, re-pinning the stored spec). Appends the
  owner's text to `revisions`, enqueues `cycle_revise` keyed by `(cycle_id, version)` so a double-submit is one job.
  `save_revision` computes `changes = cycle_plan.diff(old_pending_or_applied, new)` in code and attaches the CEO's `reasons[key]`.
- `start(s, cycle_id, expect_version)`: if a pending proposal exists, `reconcile` it: create missing stages as `PICKED`
  (position gapped), update untouched `PICKED` stages in place by `stage_key`, `DROPPED` removed `PICKED` stages (no hard delete),
  clear `proposal`; set `ACTIVE`, `business.cycle_mode='staged'`, `staged_at`. Then start the first startable stage
  (`move_bet(RUNNING)`), unless an adopted stage is already RUNNING. One transaction.
- `stop(s, cycle_id)`: remaining `PICKED` -> `DROPPED`, cycle `CLOSED(owner_stop)`, pending proposal cleared. Nothing runs;
  `phase=STOPPED` offers "propose next cycle".
- **Mapping existing businesses** (4.6 below + migration section): `LegacyMapOut.mapping` is validated by
  `cycle_plan.check_mapping`: every legacy bet of the business that is `RUNNING` must appear exactly once; at most one `running`
  stage (the unique index would reject two); `done` entries only name `DONE/STOPPED/LISTENING/REVIEW` bets; an `ahead` stage names none.

### 4.6 Existing businesses: nothing in flight stops; the gate starts at the next stage

- Migration touches no run, no job, no task. Existing businesses that have any project or bet get `cycle_mode='legacy'`; the others
  `staged`. Owner-started work in flight (RUNNING bets and their projects, dev jobs, retros) is grandfathered and keeps going.
  **Not grandfathered, by the new principle**: scheduled background kinds (`find_people`, `find_threads`, `promotion_week`,
  `github_collect`, `kpi_review`, `evidence_watch`, `portfolio_review`, `skill_factory`) and approved routines. They were never an
  owner-started item, so from the upgrade they are denied until a stage the owner starts lists them. The mapped proposal lists
  them (and the existing routines) in the stages where they belong, so approving the mapping is the approval. The board shows
  "N scheduled jobs and M routines wait for the plan" meanwhile.
- After upgrade, `scheduler.sync` calls `cycles.queue_legacy_mappings(s)`: for each ACTIVE business with an approved brief, mode
  `legacy`, no cycle row ever: enqueue **one** `cycle_propose(kind='map')` (ASKED: the owner decision of 2026-11-06 that the CEO maps
  current work; key `cycle_map:{business_id}`; app setting `cycles.auto_map`, default true; the one autonomous model call in this
  design, bounded to one per business and visible as an ordinary proposal). A business with no projects gets `kind='first'` instead. One CEO call per business, announced
  in release notes; this is the owner's decision ("the CEO maps current work into a cycle proposal").
- The proposal card appears on the dashboard. While it is unanswered the business stays `legacy`: owner-started work keeps running,
  background kinds stay denied.
- On owner **Start** of the mapped proposal, `reconcile` materializes: `done` stages as `DONE` stage Bets with
  `summary.source='mapped'` (no model call; `mapped_bet_ids` point at the original experiments, which stay experiments); the one
  `running` stage by **`adopt_running_bet`**: the live legacy Bet is reclassified in place (`kind='stage'`, `cycle_id`,
  `stage_key`, `position`, `stage_spec`, `loop_started_at` kept or set), so its Project, tasks, spend and `started_at` are
  untouched and it shows on the strip as the running stage; `ahead` stages `PICKED`. `cycle_mode` flips to `staged`.
  Other RUNNING legacy bets (parallel runs) stay experiments, grandfathered through `governing()`; their retros still run and their
  `_next_bets` are held for the next proposal.
- Because a stage is RUNNING at the flip, no job is refused at that moment. The first refusal can occur only after that stage
  ends: "the gate applies from the next stage".

---------------------------------------------------------------------------------------------------------------------

## 5. Signatures

### 5.1 services/cycles.py (new; the one owner module)

```python
def live_stage(s: Session, business_id: int, *, lock: bool = False) -> Bet | None:
    """The one stage Bet in RUNNING or REVIEW, else None. The only reader of 'which stage is running'."""
    raise NotImplementedError   # TODO select Bet where business_id, kind='stage', status in (RUNNING, REVIEW) [for update if lock]

def open_cycle_row(s: Session, business_id: int) -> Cycle | None:     # the DRAFT/ACTIVE row (closed_at is null)
    raise NotImplementedError

def load_facts(s: Session, business_id: int) -> CycleFacts:
    """One pass: cycle, ordered stage rows with task counts and derived spend, pending proposal, in-flight cycle jobs."""
    raise NotImplementedError   # TODO 4 queries: cycle+stages(+Project), Task counts grouped by project_id, Job in_flight by kind+payload.business_id, spend

def phase_of(f: CycleFacts) -> Phase:
    """PURE.
    TODO no cycle row -> NO_CYCLE
         draft and no pending: cycle_propose alive -> PROPOSING ; rounds spent -> PROPOSAL_FAILED
         pending: cycle_revise alive -> REVISING else PROPOSED
         live stage: RUNNING -> STAGE_RUNNING ; REVIEW -> STAGE_SUMMARIZING
         ACTIVE, no live: last non-dropped stage STOPPED -> STAGE_STOPPED ; has PICKED stage -> AT_GATE
         cycle CLOSED and owner_stop -> STOPPED"""
    raise NotImplementedError

# --- owner verbs (the whole write API for the owner; each is idempotent and checks expect_version) ---
def on_brief_approved(s: Session, brief: BusinessBrief, lang: Lang) -> None:
    raise NotImplementedError   # TODO no cycle row yet: request_proposal(kind='first') (ASKED); a legacy business is mapped by scheduler.sync, not here
def request_proposal(s: Session, business_id: int, kind: Literal["first", "next", "map"], *, lang: Lang) -> Cycle:
    raise NotImplementedError
def revise(s: Session, cycle_id: int, text: str, *, expect_version: int, lang: Lang) -> Cycle:
    raise NotImplementedError
def start(s: Session, cycle_id: int, *, expect_version: int, lang: Lang) -> Bet:
    """Reconcile a pending proposal (if any), then start the next startable stage through roadmap.move_bet."""
    raise NotImplementedError   # TODO lock business -> lock cycle -> version check -> reconcile -> pick next -> move_bet(RUNNING); catch IntegrityError
def stop(s: Session, cycle_id: int, *, expect_version: int, lang: Lang) -> Cycle:
    raise NotImplementedError
def redo_stage(s: Session, stage_id: int, *, lang: Lang) -> Bet:
    """Clone a STOPPED stage as a new PICKED stage with the same key and position; the old row stays as history."""
    raise NotImplementedError

# --- called by the CEO jobs and by move_bet; not by routes ---
def save_proposal(s: Session, cycle_id: int, out: CycleProposal | LegacyMapOut, *, base_version: int, lang: Lang) -> None: raise NotImplementedError
def save_revision(s: Session, cycle_id: int, out: CycleRevisionOut, *, base_version: int, lang: Lang) -> None: raise NotImplementedError
def reconcile(s: Session, cycle: Cycle, proposal: CycleProposal, mapping: list[MappedStage] | None, lang: Lang) -> list[Bet]: raise NotImplementedError
def adopt_running_bet(s: Session, cycle: Cycle, bet: Bet, ms: MappedStage, spec: StageSpec, position: int) -> Bet: raise NotImplementedError
def check_stage_move(s: Session, bet: Bet, new: BetStatus, lang: Lang) -> None:
    """Hook called by roadmap.move_bet for kind='stage'. Raises ControlError for a move the cycle does not allow."""
    raise NotImplementedError
def guard_experiment_start(s: Session, bet: Bet, lang: Lang) -> None:
    """In a staged business an experiment cannot go RUNNING (no gate-free start). Hook in roadmap._start_run."""
    raise NotImplementedError
def stage_goal(s: Session, bet: Bet, business: Business, brief: BusinessBrief | None, lang: Lang) -> str:
    """Project goal for a stage run: brief goal + cycle pitch + this stage's spec + prior stages' summaries (bounded)."""
    raise NotImplementedError
def run_propose(cycle_id: int, kind: str, base_version: int) -> None: """cycle_propose / cycle_revise handler""" ; raise NotImplementedError
def queue_legacy_mappings(s: Session) -> int: raise NotImplementedError          # called from scheduler.sync
def typical_spend(s: Session, business_id: int, stage_key: str) -> float | None: raise NotImplementedError
def history(s: Session, business_id: int) -> list[dict]: raise NotImplementedError    # closed cycles + stage records for the Business page
```

### 5.2 services/stage_gate.py (new; policy only; default-deny)

```python
class Gate(StrEnum): DENY="deny"; FREE="free"; ASKED="asked"; OF_STAGE="of_stage"; RECURRING="recurring"
JOB_GATE: dict[str, Gate] = {...}            # section 3.1, one entry per handler; absence means DENY
RECURRING_KINDS: tuple[str, ...] = tuple(k for k, g in JOB_GATE.items() if g is Gate.RECURRING)   # derived

@dataclass(frozen=True)
class Verdict: allowed: bool; reason: str | None = None

class UndeclaredJobKind(Exception): ...
class GateViolation(Exception): ...

def require_declared(kind: str) -> None:       # queue.enqueue's first line
    raise NotImplementedError   # TODO if kind not in JOB_GATE: raise UndeclaredJobKind
def admit(s: Session, kind: str, payload: dict) -> Verdict:
    """Read-only over the DB. TODO: gate = JOB_GATE.get(kind, DENY); DENY -> deny; FREE/ASKED -> allow;
    pid,bid = scope.owner_of(s,payload); OF_STAGE -> governing(project): stage live | grandfathered | business RUNNING stage;
    bet_retro: bet.kind=='stage' -> bet is the live stage in REVIEW, bet.kind=='experiment' -> loop_started_at not null;
    RECURRING -> RUNNING stage of bid whose spec['recurring'] contains kind; bid None (portfolio_review, skill_factory) ->
    any business's RUNNING stage lists it. Never reads Business.cycle_mode."""
    raise NotImplementedError
def governing(s: Session, project: Project) -> Literal["stage", "grandfathered", "business", "none"]: raise NotImplementedError
def decline(s: Session, job: Job, v: Verdict) -> None: raise NotImplementedError
def stage_busy(s: Session, bet: Bet) -> bool: raise NotImplementedError    # 4.3 quiescence
def tripwire(business_id: int | None, project_id: int | None) -> None: raise NotImplementedError   # fail-closed (3.3)
def owner_work_allowed() -> None: raise NotImplementedError                # called by every OwnerTask writer inside a worker job
@contextmanager
def asked(why: str): ...                                                    # explicit gate context for CLI/test entry points
current_gate: ContextVar[Gate | None]
```

### 5.3 services/cycle_plan.py (new; model-facing, pure where possible)

```python
def build_packet(s, cycle: Cycle, kind: str, feedback: str | None, lang: Lang) -> Packet: raise NotImplementedError
def check(out: CycleProposal, *, recurring_allowed: Collection[str], lang: Lang) -> None:     # raises PlanInvalid -> job retries (max 3)
    raise NotImplementedError   # unique keys, 4..9 stages, recurring subset, key slug, guesses present
def check_mapping(s: Session, business_id: int, out: LegacyMapOut, lang: Lang) -> None: raise NotImplementedError
def diff(old: CycleProposal | None, new: CycleProposal, reasons: Mapping[str, str]) -> list[Change]: raise NotImplementedError   # PURE
def pin_started(new: CycleProposal, started: Mapping[str, StageSpec]) -> CycleProposal: raise NotImplementedError             # PURE
```

### 5.4 services/stage_summary.py (new)

```python
def build_facts(s: Session, bet: Bet) -> StageFacts: raise NotImplementedError
def spend_of(s: Session, bet: Bet) -> float: raise NotImplementedError
def cap_confidence(claimed: str, refs: list[str], facts: StageFacts) -> tuple[str, str | None]: raise NotImplementedError    # PURE
def apply(s: Session, bet_id: int, out: StageSummaryOut | LastStageSummaryOut, facts: StageFacts, *, lang: Lang) -> Bet: raise NotImplementedError
def run(bet_id: int, *, lang: Lang) -> None: """bet_retro job body when bet.kind == 'stage'""" ; raise NotImplementedError
```

### 5.5 services/cycle_board.py (new; read model)

```python
def board(s: Session, business_id: int, *, lang: Lang) -> BoardOut:
    """TODO facts = cycles.load_facts; phase = cycles.phase_of(facts); headline from (phase, facts);
    gate from phase; strip from facts.stages (+ pending proposal stages as state 'proposed'); current from live stage;
    cards = working.running_items(s, hidden) filtered to the business, each joined to Project.bet_id -> stage -> Cycle.number;
    idle_agents = len(agents) - distinct agents with a card."""
    raise NotImplementedError
```

### 5.6 services/working.py (new, extracted)

`api/console._agent_states` currently builds `running[]` inline (console.py:86-133). Extract that query into
`working.running_items(s, hidden) -> list[RunningItem]` (task_id, title, agent, role, project_id, started_at, spent_usd, dev_job_id) and
have both `_agent_states` and `cycle_board.board` consume it. One query, two readers, no divergence.
`Task.started_at` / `Task.spent_usd` already exist; DevJob uses `created_at` / `cost_usd`.

### 5.7 Edits to existing functions (signatures unchanged unless noted)

```python
# roadmap.py
def running_count(...): # add Bet.kind == 'experiment': stages never take an experiment slot
def _take_slot(s, bet, lang): # kind == 'stage': lock the business row, skip the max_running check (ux_bets_one_live_stage is the limit)
def _start_run(s, bet, lang): # + cycles.guard_experiment_start for experiments; stage -> goal=cycles.stage_goal; budget stays default
def move_bet(...):           # + `if bet.kind == "stage": cycles.check_stage_move(s, bet, new, lang)` after the transition check
def bet_goal(...):           # kind == 'stage' -> cycles.stage_goal
def window_end(...):         # kind == 'stage' -> None
def adopt_project(s, p, *, running, business=None):   # + cycles.refuse_ungated_start(s, business, running) when business staged -> ControlError
def _on_approved(...):       # + cycles.on_brief_approved(s, brief, lang)
def roadmap(...):            # lanes filter kind='experiment'; `cycle` block comes from cycles.load_facts
# bet_loop.py
def due_move(...):           # stage branch (4.1) + quiescence via stage_gate.stage_busy
def settle_now(s, project):  # NEW: run due_move/_move for the project's bet only (fast path of the sweep)
# bet_retro.py
def run(bet_id, *, lang):    # stage -> stage_summary.run
def _next_bets(...), _routine_card(...), out-of-scope cards:   # ALL businesses: store in retro JSON (proposed_next, routine_suggestion), create nothing
# queue.py
def enqueue(...):            # first line: stage_gate.require_declared(kind)
def claim(...):              # section 3.2 (default-deny)
def _lock_key(kind, payload):# + cycle_propose/cycle_revise -> f"cycle:{business_id}"
# projects.py
def start_business: DELETED. Replaced by cycles.request_proposal (route POST /businesses/{id}/cycle/propose, used for retry and for "next cycle").
# executor.py
def run_agent(...):          # + stage_gate.tripwire(business_id, project_id) after check_lease (fail-closed)
# owner_tasks.py, people_tasks.py: every OwnerTask writer calls stage_gate.owner_work_allowed()
# workers/runner.py
_handlers():                 # + "cycle_propose", "cycle_revise"; _execute sets stage_gate.current_gate
# company_graph.py::report   # + bet_loop.settle_now after Project DONE (one line)
# scheduler.py::sync         # + cycles.queue_legacy_mappings(s); schedules are still created, the gate declines their ticks
# control.py::retry/resume   # + early ControlError when the project's stage is not live (UX message; claim is the backstop)
```

---------------------------------------------------------------------------------------------------------------------

## 6. API (api/cycle.py, new; all `_act`/`_read` like kickoff.py)

| Method path | Purpose |
|---|---|
| `GET /businesses/{id}/cycle` | `CycleBoard` (the dashboard). Also embedded nowhere else |
| `GET /businesses/{id}/cycles` | history: closed cycles, stage records, summaries (Business page) |
| `POST /businesses/{id}/cycle/propose` | first / next / retry proposal (replaces `POST /businesses/{id}/start`) |
| `POST /cycles/{id}/start {version}` | accept pending proposal if any, start next stage |
| `POST /cycles/{id}/revise {version,text}` | "say what to change" |
| `POST /cycles/{id}/stop {version}` | "stop here" |
| `POST /stages/{id}/redo` | restart a stopped stage |

Existing `POST /bets/{id}/move` stays and routes through `move_bet`, so it obeys `check_stage_move`.

## 7. Dashboard read model and live refresh

One endpoint, one shape (`CycleBoard`, 2.4). Per-agent cards: one per `RunningItem` of the business (role work/review/dev), each
joined `Task -> Project.bet_id -> Bet(stage) -> Cycle.number`, so `agent`, `cycle_number`, `stage`, `title`, `started_at`,
`spent_usd` come from rows that already exist; elapsed is computed in the browser from `started_at` (no polling).
Rest of agents: `idle_agents`. Status sentence: `headline = {key: f"cycle.line.{phase}", params}` e.g. phase `stage_running` ->
`{n: cycle.number, stage: title, agents: len(cards)}`; both catalogs get the keys; `glossary.py` gets `cycle` and `stage`.
Stage tasks n/m: `Task` rows of the stage's Project, excluding CANCELLED/REPLACED/REJECTED; `null` until the CEO's plan node has run
(the strip shows "planning tasks").

SSE (`events._WATCHED` already has all of these; no change): `bets` (stage state), `cycles` (proposal/version/phase), `tasks`
(counts, cards, spend), `projects` (WAITING_HUMAN -> blocked_on_owner), `jobs` (CEO drafting/revising alive, declined jobs),
`agent_requests` (decisions inside the stage), `dev_jobs` (engineer cards), `businesses` (mode). `Home.tsx` `OVERVIEW_TABLES`
gains `bets, cycles, businesses` and the new `useBusinessLive<CycleBoard>` uses the same list (600 ms debounce already there).

## 8. Composer Done, start_business, adopt_project

- **Composer.tsx** (216-257): Done now calls only `POST /briefs/{id}/approve` and navigates to `/` (dashboard, business selected).
  The `/businesses/{id}/start` call and the `/projects/{id}` navigation are removed. The wait state is the board's
  `gate.kind='working'` (`proposing`, `since`) rendered with the existing kickoff `Waiting` component.
- **`projects.start_business` is deleted** (one way). Its goal-from-brief logic moves into `cycles.stage_goal`/`roadmap.goal_from_brief`
  (already separate). Tests that called it (`test_brief_targets`, `test_goal_source`, `test_kickoff:1715`) use a helper
  `tests/helpers.start_first_stage(s, business)` which creates a minimal proposal and calls `cycles.start`.
- **`adopt_project(running=True)`**: for a `staged` business it raises `ControlError(cycle.refuse.ungated_start)` ("the company works in
  stages: ask the CEO to add this to the plan"). `running=False` still creates the PICKED experiment; `move_bet(RUNNING)` of it is
  refused by `guard_experiment_start`. A business that `adopt_project` itself creates for a quick project (no brief, no cycle) is created `legacy`: the owner's Start on that project is the approval, its run is grandfathered, and its background kinds stay denied like everyone's. `projects.create(start=True)` for
  a staged business therefore refuses before enqueueing.

## 9. Module map

New:
- `backend/justshipit/services/cycles.py` (owner module, ~450 lines), `stage_gate.py` (~200), `cycle_plan.py` (~250), `stage_summary.py` (~250), `cycle_board.py` (~200), `working.py` (~80)
- `backend/justshipit/contracts/cycle.py` (Pydantic: CEO outputs + BoardOut)
- `backend/justshipit/api/cycle.py` (routes)
- `backend/justshipit/db/migrations/versions/e7b1c93a4d52_cycle_stages.py`
- `prompts/cycle_propose.md`, `prompts/cycle_revise.md`, `prompts/cycle_map.md`, `prompts/stage_summary.md`
- `web/src/cycle/{types.ts, GateCard.tsx, StageStrip.tsx, WorkCards.tsx, StatusLine.tsx}`, `web/src/i18n/areas/cycle.ts`
- tests: `tests/test_cycles.py`, `test_stage_gate.py`, `test_cycle_migration.py`, `test_cycle_board.py`

Changed:
- `db/models.py` (2.1), `services/roadmap.py` (5.7), `services/bet_loop.py`, `services/bet_retro.py`, `services/queue.py`,
  `services/projects.py` (delete `start_business`), `services/control.py`, `agents/executor.py`, `workers/runner.py`,
  `orchestration/company_graph.py` (one line in `report`), `operations/scheduler.py`, `api/console.py` (`_agent_states` uses `working`),
  `api/kickoff.py` (remove `business_start`), `api/app.py` (include router), `i18n/messages/*.py` + `i18n/glossary.py`
  (cycle, stage; refusal strings; en and ko), `settings` (`cycles.auto_map`)
- `web/src/kickoff/Composer.tsx`, `web/src/pages/Home.tsx`, `web/src/business/*` (roadmap lanes only experiments; history), `web/src/i18n/areas/*`
- `DESIGN.md` max width 1760 -> 2400 (the brief asks for it)

## 10. Alembic migration outline (`e7b1c93a4d52_cycle_stages.py`, down_revision `d4a8e2c6b917`)

Hand-written (not autogenerate: it would drop LangGraph checkpoint tables). Run with the worker stopped.

```python
def upgrade():
    # businesses
    op.add_column("businesses", sa.Column("cycle_mode", sa.String(8), nullable=False, server_default="staged"))
    op.add_column("businesses", sa.Column("staged_at", sa.DateTime(timezone=True)))
    op.create_check_constraint("ck_businesses_cycle_mode", "businesses", "cycle_mode IN ('legacy','staged')")
    # cycles (revived)
    op.add_column("cycles", sa.Column("status", sa.String(8), nullable=False, server_default="ACTIVE"))
    op.add_column("cycles", sa.Column("version", sa.Integer, nullable=False, server_default="0"))
    op.add_column("cycles", sa.Column("origin", sa.String(8), nullable=False, server_default="first"))
    op.add_column("cycles", sa.Column("proposal", JSONB)); op.add_column("cycles", sa.Column("revisions", JSONB, nullable=False, server_default="[]"))
    op.add_column("cycles", sa.Column("retro", JSONB)); op.add_column("cycles", sa.Column("end_reason", sa.String(12)))
    op.create_check_constraint("ck_cycles_status", "cycles", "status IN ('DRAFT','ACTIVE','CLOSED')")
    # backfill: nothing writes cycles today. Close strays that hold no bet so the one-open index is free.
    op.execute("UPDATE cycles SET status='CLOSED' WHERE closed_at IS NOT NULL")
    op.execute("""UPDATE cycles SET status='CLOSED', closed_at=now(), end_reason='legacy_unused'
                  WHERE closed_at IS NULL AND NOT EXISTS (SELECT 1 FROM bets WHERE bets.cycle_id = cycles.id)""")
    # bets
    op.add_column("bets", sa.Column("kind", sa.String(10), nullable=False, server_default="experiment"))
    op.add_column("bets", sa.Column("stage_key", sa.String(24))); op.add_column("bets", sa.Column("position", sa.Integer))
    op.add_column("bets", sa.Column("stage_spec", JSONB)); op.add_column("bets", sa.Column("summary", JSONB))
    op.create_check_constraint("ck_bets_kind", "bets", "kind IN ('experiment','stage')")
    op.create_check_constraint("ck_bets_stage_fields", "bets",
        "kind <> 'stage' OR (cycle_id IS NOT NULL AND stage_key IS NOT NULL AND position IS NOT NULL)")
    op.create_index("ux_bets_one_live_stage", "bets", ["business_id"], unique=True,
                    postgresql_where=sa.text("kind = 'stage' AND status IN ('RUNNING','REVIEW')"))
    op.create_index("ux_bets_stage_key", "bets", ["cycle_id", "stage_key"], unique=True,
                    postgresql_where=sa.text("kind = 'stage' AND status <> 'DROPPED'"))
    # existing businesses that already have owner-started work are 'legacy' (their running work is grandfathered); the rest are 'staged'.
    op.execute("UPDATE businesses SET cycle_mode='legacy' WHERE EXISTS (SELECT 1 FROM projects WHERE projects.business_id = businesses.id)"
               " OR EXISTS (SELECT 1 FROM bets WHERE bets.business_id = businesses.id)")
    # NOT done here, on purpose: no cycles, no stage Bets, no jobs, no change to any run, task or queued job.

def downgrade():
    # best effort: stage Bets become plain experiments (PICKED/DONE keep their meaning); cycle rows keep their data.
    drop indexes, constraints, columns in reverse order
```

Existing businesses after upgrade, step by step: (1) businesses with work are `legacy`; owner-started runs and their dependents continue unchanged, while scheduled background kinds and routines are denied from now on (new principle);
(2) the next `scheduler.sync` enqueues one `cycle_propose(kind='map')` per ACTIVE business with an approved brief (a business with
no projects gets `kind='first'`); (3) the CEO's `LegacyMapOut` appears as a proposal card; (4) on Start, `reconcile` adopts the
live run as the running stage (4.6), flips to `staged`, and the gate applies from the end of that stage. Nothing in flight is stopped,
re-queued or re-planned at any step.

## 11. Verification plan (tests that pin the invariants)

- `phase_of` table test over `CycleFacts` (pure). `diff`, `pin_started`, `cap_confidence`, `check` pure tests.
- Gate: `set(_handlers()) == set(JOB_GATE)`; every class behavior with a seeded business (no stage: RECURRING/OF_STAGE and undeclared kinds declined, ASKED/FREE admitted; a legacy business gets no exemption for scheduled kinds; grandfathered experiment project admitted; declined job leaves queue and does not burn attempts); tripwire FREE-class or undeclared-kind model call raises; a model call with no gate context raises.
- Start: double call -> one Project and one `run_project`; two stages racing -> one wins, other `ControlError`; stale `expect_version` refused; start with pending proposal reconciles once.
- End: Project DONE + running dev job -> stays RUNNING; quiet -> REVIEW; `bet_retro` twice -> one summary; summary confidence capped by ledger; last stage closes the cycle and creates next DRAFT with proposal.
- Migration: upgrade on a DB with a RUNNING bet+project+queued jobs leaves every row and job byte-identical except new columns; mapping fixture adopts the RUNNING bet in place (same project id, same spend), `ux_bets_one_live_stage` holds, first refusal only after that stage settles.
- Regression: experiments in a legacy business behave as before (`window_end`, `due_move` LISTENING path, `_next_bets`).
