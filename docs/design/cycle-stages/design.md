# Candidate A: stages as a first-class entity (`cycle_stages` owns the machine)

Direction: revive `cycles` as the owner-started unit, add `cycle_stages` as the only thing that says "which stage is
running", stamp work with the stage, and enforce the gate at the one place every job must pass (queue claim), backed by
a stamp at the one place every job is created (`queue.enqueue`). Verified against the repo (file:line below); nothing
in the repo was edited.

Facts this design leans on (all verified):
- Every `Job` row is created in exactly one place: `services/queue.py:210` (`enqueue`). All ~35 enqueue sites go through it.
- Every model call is made from a job handler (`run_agent` callers: analysis, capability, engineering, operations,
  orchestration, services/*; none from `api/`). Every handler runs under a `Lease` (`workers/runner.py:303`), and
  `queue.check_lease()` runs before each model call (`agents/executor.py:107`) and each graph node
  (`company_graph._lease_checked`).
- `queue.claim` (`queue.py:454`) is the only way a job starts; it already filters `status == "queued"`.
- `scope.owner_of(s, payload)` (`services/scope.py:49`) already resolves any job payload to (project, business).
- `LLMCallRecord` already has `business_id` (migration `d4a8e2c6b917`, the current head), so per-stage cost is one more column.
- `cycles` has no writer of `closed_at` and no caller of `open_cycle` besides an unused route, so reviving it breaks nobody.
- `adopt_project` (`roadmap.py:1067`) gives every project a Bet; a stage project must not get one (bets carry slots,
  windows and retros that belong to experiments).

---

## 1. Usage (caller's view)

### 1.1 What the owner experiences (one line per screen state)

```
approve brief --> "CEO is shaping your first cycle" (Waiting)  --> proposal card [Start] [Say what to change]
Start --> stage 1 runs (one stage only) --> tasks 3/7, action cards per working agent
stage settles --> "Stage 1 is done. Choose the next step." gate card: summary (done, cost, evidence+confidence)
   [Start next stage: Plan] [Say what to change] [Stop here]
last stage accepted --> cycle retro --> proposal for cycle 2
```

### 1.2 Call sites (the three that matter)

```python
# (1) An enqueue site that does NOT change. Everything it needs is stamped inside queue.enqueue.
queue.enqueue(s, "retry_project", {"project_id": pid}, idempotency_key=f"retry:{pid}:{_now()}", max_attempts=1)
#   stage running  -> job queued, gate="stage", stage_id=<running stage>
#   no stage open  -> job parked (status="gated"); starts by itself when the owner starts the next stage
#   scheduled kind the running stage did not list -> returns None (skipped, no catch-up)

# (2) The owner's only write path (API handler, CLI, and "claude (오너 위임)" all call this).
from justshipit.services import cycle
cycle.decide(s, cycle_id, cycle.StartNext(after_stage_id=41), by="owner", lang=lang)   # idempotent; stale -> StaleDecision

# (3) A handler that needs to know the stage it spends under: it does not. run_agent stamps llm_calls.stage_id from the
#     lease; check_lease() stops the handler when its stage is stopped. No handler imports cycle.
```

### 1.3 HTTP (one read, one write)

```
GET  /api/businesses/{id}/cycle-board            -> CycleBoardOut     (the dashboard, one round trip)
GET  /api/businesses/{id}/cycles                 -> list[CycleRowOut] (history for the Business page)
GET  /api/cycles/{cycle_id}                      -> CycleDetailOut    (proposal versions + diff, stages, summaries)
POST /api/cycles/{cycle_id}/decisions   {kind: "start_cycle", version: 3}
                                        {kind: "revise", note: "...", base_version: 3, at_stage_id: null}
                                        {kind: "start_next", after_stage_id: 41}
                                        {kind: "finish_cycle", after_stage_id: 44}
                                        {kind: "stop_here", after_stage_id: 41}
                                        {kind: "retry_stage" | "stop_stage" | "resume_stage" | "close_stage_now", stage_id: 41}
                                        -> CycleBoardOut (fresh), 409 StaleDecision when the card the owner clicked is old
```

The old `POST /businesses/{id}/cycles` (raw `open_cycle`) is removed, not kept (two ways to do one task).

---

## 2. Explicit answers

### 2.1 Single source of truth for "which stage is running"

`cycle_stages.status` is the only store. A partial unique index admits **at most one holding stage per business**:

```sql
CREATE UNIQUE INDEX ix_cycle_stages_one_holding ON cycle_stages (business_id)
  WHERE status IN ('running','closing','awaiting_owner');
```

`gate.open_stage(s, business_id)` (`status IN ('running','closing')`) is the one query; every other fact is derived from
it: the project/job stamp, the claim predicate, the dashboard, the status sentence. There is no `current_stage_id` on
`businesses` or `cycles` (nothing to sync). Cycle-level `state` is stored but written in the same function, the same
transaction, as the stage change that implies it.

**One owner module.** `services/cycle.py` is the only writer of `cycles`, `cycle_stages`, `cycle_proposals`,
`projects.stage_id`, and of the *decision* to park or release jobs. Job rows stay `queue.py`'s: cycle asks it through
`queue.release_gated(...)` / `queue.park_stranded(...)` (split ownership avoided: queue owns Job rows, cycle owns when).
Other modules read through `gate.py` (pure policy and `open_stage`) or `cycle_board.py` (read model). A test greps the
repo for `CycleStage.status =`, `.stage_id =` outside `cycle.py` and `queue.py` and fails on a match.

### 2.2 What stops at a stage boundary, and what happens to in-flight work

Stops: everything in the gate table (2.4) whose `Gate` is `STAGE_WORK` or `STAGE_RECURRING`; kinds in no row are denied outright (2.4b).
A stage does not end while work is in flight; it **drains**, it is not cut:

1. `cycle.tick` (a 60 s scheduled FREE job; a pure function of DB state) finds a `running` stage that is *settled*
   (2.5) and does `running -> closing` with a compare-and-swap under the stage row lock.
2. In the same transaction it enqueues `cycle_summarize` (idempotency key `cycle_summary:{stage_id}:{round}`).
   `closing` still counts as open, so the summarize job (the only thing allowed to start in `closing`) and any
   late-finishing job can run; nothing new is admitted in `closing` except `CYCLE`-gate kinds.
3. `cycle.store_summary` does `closing -> awaiting_owner`. In `awaiting_owner` the gate is shut: claim refuses every
   stage-stamped job (stage not open), enqueue parks new `STAGE_WORK` jobs as `gated`.

In-flight cases:
- **Normal end**: nothing is in flight by definition of "settled" (no queued/running job stamped with the stage, no
  unsettled dev job, every project of the stage DONE/FAILED/ARCHIVED).
- **Owner stops mid-stage** (`stop_stage`): stage -> `stopped` (a CAS). Effects, none of which need a second code path:
  claim stops handing out the stage's queued jobs immediately (predicate); running handlers hit
  `queue.check_lease()` at their next model call or graph node, which now also checks `gate.stage_is_open(lease.stage_id)`
  and raises `queue.Stopping`, so the worker calls the existing `queue.release` (attempt returned, run kinds become
  `retry_project`, resumes from the LangGraph checkpoint); `cycle.tick` parks the stranded queued rows
  (`queue.park_stranded`: `queued -> gated`), so `in_flight()` and archive checks stay honest. `resume_stage` flips back
  and `queue.release_gated` re-queues them. Nothing is deleted, nothing is retried from scratch.
- **Stale queued job after a restart/reclaim**: same predicate, same park.
- **Crash halfway**: every transition is one DB transaction that also contains its enqueue (the queue is a
  transactional outbox on the same DB). `tick` is idempotent (CAS on `status`, deterministic idempotency keys), so a
  crash between "settled" and "closing" repeats harmlessly. A summarize job whose worker died is reclaimed by the
  existing lease machinery; `store_summary` is a CAS on `closing`, so the first summary wins.
- **Double click**: each decision is idempotent by *target state*, plus carries the id of the card clicked
  (`after_stage_id`, `version`, `stage_id`). Second `StartNext(after=41)`: stage 41 is already `done` and its successor
  `running` -> returns the board unchanged (200). A click on an old card (`StartNext(after=40)` when 41 is the gate) ->
  `StaleDecision` (409), the UI refetches. Two simultaneous clicks serialize on `SELECT ... FOR UPDATE` of the cycle row.
- **Summarize fails**: the job's `max_attempts` end -> `tick` sees `closing` with no live summarize job and either
  re-enqueues (round-keyed, up to 3) or, after that, `store_summary(out=None)` writes a **server-built summary** (numbers
  and task titles only, `summary_by="server"`, "the CEO's write-up failed"). The gate always opens. The owner is never
  left looking at a spinner.

### 2.3 Where the gate lives, and why it cannot be bypassed

Three points, one rule (`gate.POLICY`), one authority:

| Point | Role | Authority |
|---|---|---|
| `queue.enqueue` (stamps) | resolves business + open stage via `gate.admit(...)`; writes `jobs.gate` (`none`/`direct`/`stage`), `jobs.stage_id`, `jobs.business_id`; raises `UndeclaredJobKind` for a kind not in `POLICY`; parks (`status="gated"`) a `STAGE_WORK` job when no stage is open; returns `None` for an unlisted `STAGE_RECURRING` job | convenience and honest UX; **not** the guarantee |
| `queue.claim` (SQL predicate) | `jobs.gate IN ('none','direct') OR (jobs.gate = 'stage' AND jobs.stage_id IN open stages)`; NULL/unknown gate matches nothing (fail closed) | **the guarantee**: nothing starts without passing it |
| `queue.check_lease` (in-flight) | `Lease.stage_id` checked at every model call / graph node; raises `Stopping` when the stage left `running/closing` | stops work already started |

Why enqueue alone is not enough: ~35 sites, a race (stage closes between enqueue and claim), and a backlog (queued jobs
outlive the stage that stamped them). Why not `run_agent` alone: it is per call, mid-run, after a project/worker/
container was already started, and cannot hold a job without failing it. Why not `runner._invoke`: covers only 3 of the
~30 model-spending kinds. Why not LangGraph `human_gate`: interrupt-based, run kinds only. The claim predicate is
unavoidable because **every** handler starts through `claim` and **every** row is created by `enqueue`, which always sets
`jobs.gate` (NOT NULL, no default after the migration, so a hand-built insert fails loudly). A kind missing from the
policy table is **denied** (section 2.4b), not defaulted into anything.

### 2.4 Exactly which job kinds the gate covers (default-deny)

Binding owner principle (coordinator, 2026-11-06): the owner approves; what was approved runs by itself; **nothing that
spends model usage or produces owner work runs without that approval**. Observed failure: after plan approval the
scheduled `find_people` / `find_threads` (`scheduler._people_schedules`, `first_in_hours=0`) ran unasked and put
"contact these people" to-dos in the owner's list. So the rule is inverted from my first draft: **a kind is denied unless
`gate.POLICY` declares why it may run**, and there is no "company, ungated" bucket any more.

`gate.POLICY` is the single list (kind -> `Rule(gate, agent, scope)`; `agent` also feeds job action cards).
Four ways to be allowed, one way to be denied:

| Gate | Kinds | Behaviour | Why this is approved |
|---|---|---|---|
| `STAGE_WORK` | run_project, resume_project, retry_project, reassess_project, write_spec, spec_approved, spec_questions, dev_job, dev_resume, dev_deliver, dev_fix, dev_fix_answered, acceptance, dev_merged, bet_retro | needs an open stage; no stage -> parked `gated`, joins the next stage that starts | the owner pressed Start on a stage whose card said what the company does; parking (not refusing) means an owner approval or answer is never lost |
| `STAGE_RECURRING` (scope=business) | find_people, find_threads, promotion_week, github_collect, kpi_review, evidence_watch, routine_run | admitted only if the **open stage lists the kind** in `allows_kinds` (CEO names them per stage; the stage card prints "recurring: weekly promotion"); else **skipped** (not parked: no catch-up flood) | the owner saw it on the card before pressing Start |
| `STAGE_RECURRING` (scope=company) | portfolio_review, skill_factory, post_reactions | admitted only if **any business's open stage lists the kind**; stamped with that stage (lowest id) so its cost has a home; else skipped | no business payload, so "which stage" is "any approved one that names it". Nobody lists it -> it never runs |
| `ASKED` | kickoff_turn, product_report, triage_idea, idea_review, realign_project, dev_merge, dev_merge_declined, skill_activate, derive_door_rules; plus any `STAGE_RECURRING` kind enqueued with `by_owner=True` (the owner's "run now" / manual button: `api/growth.py:111,159`, `api/capability.py:39`, `scheduler.run_now`, `cli.py:383`) | admitted without a stage; stamped with the project's/open stage when there is one (`gate='direct'`) | each is one bounded call **the owner directly asked for**: kickoff_turn = the conversation itself; product_report = the product the owner brought / asked to read again; triage_idea, idea_review = the owner submitted an idea and is waiting; realign_project = the owner answered a card or wrote a directive; dev_merge(_declined), skill_activate = the owner decided the card; derive_door_rules = the consequence of the brief approval click, one call, writes rules and no owner to-dos (open question: fold it into `cycle_propose` to remove the exception); by_owner = a button |
| `CYCLE` | cycle_propose, cycle_revise, cycle_summarize, cycle_retro | admitted; summarize stamped with its (closing) stage (`gate='direct'`) | the CEO drafting/revising the cycle is what the owner asked for by approving the plan and writing "what to change"; summarize and retro are how the gate opens. They cannot be stage-gated or the gate could never open |
| `FREE` | notify, daily_briefing, backup, retention, bet_sweep, execute_action, cycle_tick | admitted, `gate='none'`; **cannot spend** (run_agent backstop, 2.8) and cannot create owner work | housekeeping, collectors, and `execute_action` = sending what the owner already approved on a card |
| *(undeclared)* | any other kind | **denied**: `enqueue` raises `UndeclaredJobKind`; the worker refuses to start; claim matches nothing | a new job kind is denied until someone writes its row in `POLICY` and says why |

A business is *cycle-managed* when it has an approved brief or any cycle row (`gate.managed`). An unmanaged business
(CLI projects, `[TEST]` fixtures, a business still in kickoff) has no approver to ask: its STAGE_* kinds are admitted
`gate='direct'` so tests and the CLI keep working, **except** STAGE_RECURRING, which is skipped for any business without
an open stage (a business in kickoff has no cron work; there is nothing to "keep running"). Brief approval alone
(before Start) leaves the business managed with no open stage: the first `find_people` tick after approval is skipped.

**Follow-ups that create owner work.** Two paths today create owner work as a side effect of a job that is itself fine:
- `bet_retro` (`services/bet_retro.py:_next_bets`, `_routine_card`, `_decision_card`) creates PROPOSED/PICKED bets and
  decision cards. In a managed business it changes to **write the suggestions into `bet.retro["next_bets"]` /
  `["routine"]` only** (no Bet rows, no `requests.create`). The next `cycle_propose` reads them; the owner approves
  them as stages (a stage `experiment`, `allows_kinds=["routine_run"]`). One way to propose next work: the cycle.
  Unmanaged businesses keep the old behaviour.
- `OwnerTask` rows (`services/owner_tasks.py:466,546`, `services/people_tasks.py:195`, incl. the plan node's
  `people_tasks.first_talks`, `company_graph.py:~145`). The three constructors collapse into one
  `owner_tasks._insert(s, task)` which calls `gate.owner_work_allowed(s, business_id, task.kind)`: allowed only when the
  open stage lists that kind in `allows_owner_work` (closed enum derived from the OwnerTask kinds: `talk`, `post`, ...;
  the CEO fills it from `owner_does` so the owner reads "you will contact up to 5 people" before Start). Not allowed ->
  the row is not created, an activity line says why. This is the second layer behind the job gate, because
  `run_project` is approved but "the plan node writes contact to-dos" is a separate consent.

### 2.4b Where default-deny is enforced (so a new job kind is denied unless declared)

1. **Declaration is mandatory at creation.** `gate.rule(kind)` raises `UndeclaredJobKind` for a kind not in `POLICY`
   (no fail-open fallback). `queue.enqueue` calls `gate.admit`, so the first `enqueue(s, "new_kind", ...)` in a test or
   at runtime fails loudly before any row exists.
2. **The worker cannot start with an undeclared handler.** `workers/runner._handlers()` result is checked at startup:
   `missing = set(handlers) - set(POLICY)` -> `RuntimeError`. A new handler without a policy row never runs.
3. **Claim matches nothing undeclared.** `jobs.gate` is NOT NULL with no default; claim admits only
   `gate IN ('none','direct')` or (`gate='stage'` and `stage_id` in an open stage). A row inserted by hand or by an
   old binary has no valid gate and is never claimed.
4. **Spend backstop at the point of spend.** `Lease` carries `gate`. `agents/executor.run_agent` already calls
   `queue.check_lease()`; `check_lease` now also raises `GiveUp("kind X declared FREE tried to spend")` when
   `lease.gate == 'none'`, and `Stopping` when `lease.gate == 'stage'` and its stage is not open. A `FREE` kind that later
   grows a model call (or a `STAGE_RECURRING` kind whose stage was closed) is denied at the call, not just at claim.
5. **Owner-work backstop** at the three-to-one `owner_tasks._insert` (above).
6. **Tests make the list self-checking** (`test_gate_policy`): `set(POLICY) == set(_handlers())`; every `FREE` handler
   is exercised with `run_agent` patched to raise; every `ASKED` row has a `why` string (a field of `Rule`, printed in
   docs and tests); `RECURRING_KINDS` is derived, so the CEO contract cannot name a kind that is not declared.

### 2.5 How a stage ends, and how the summary is produced

Who decides "done": **the existing machinery decides, the cycle observes**. A stage's project runs the existing
LangGraph; its `ceo_review -> decide -> report` nodes already decide the project is complete. The cycle adds one
data-only predicate, `ends_when`, chosen by the CEO per stage from a closed enum:
- `run_settled` (default): all projects with `stage_id = S` are DONE/FAILED/ARCHIVED (or PAUSED when `close_requested_at`
  is set), no job stamped S is queued/running (`queue.in_flight`), no unsettled dev job on those projects.
- `window_closed` (reaction/listening stages): `now >= listen_until` and the stage's bet (if any) is out of `LISTENING`
  and its retro is stored, and no stamped job is in flight.
- `owner_marks` (stages the owner works, e.g. posting by hand): the owner pressed `close_stage_now`.
`close_stage_now` is also valid on any stage: it sets `close_requested_at`, pauses the stage's live projects through
`control.pause`, and the stage settles when the running tasks finish (existing pause semantics: "running tasks finish,
no new ones start").

Summary: `cycle_summarize` (CEO, purpose `cycle_summary`) gets a packet built **by the server** from DB facts: tasks
done/failed with titles, artifacts, decisions, `SUM(llm_calls.cost_usd WHERE stage_id = S)`, elapsed, and the
evidence mix/grades from `services/evidence.py`. The model writes only judgement (`StageSummaryOut`: headline,
what was done / not done, claims each with `evidence_ids` and a `confidence`, suggestion for the next stage, things
the owner must decide). The server then (a) drops any evidence id not in the packet, (b) caps confidence to `low` when
no id survives, (c) overwrites `cost_usd`, `task_counts`, `elapsed_s` with its own numbers (the model never types a
number the owner will read as fact), as `bet_retro` already does. Stored on `cycle_stages.summary` JSONB with
`summary_by` ("ceo" | "server").

### 2.6 Dashboard read model (one endpoint, SSE tables)

`GET /businesses/{id}/cycle-board` -> `CycleBoardOut` (section 4.4 / 4.5): `sentence` (server-written in the owner's
language via `msg()`), `state`, `waiting` (proposal card / gate card / blocked decisions / gated-job note), `strip`
(chips with status and n/m), `current` (stage detail, tasks, summary if at the gate), `working` (one card per working
agent: agent, cycle number, stage, task title, started_at, spent_usd), `idle_agents` (count; no cards for idle agents),
`past` (collapsed history). Built by `services/cycle_board.py` with at most 6 queries; the running-work rows come from
the same function `console._agent_states` uses (extracted to `services/work_now.py`) so Overview and the board never
disagree.

SSE: `useBusinessLive(`/businesses/${id}/cycle-board`, BOARD_TABLES)` with
`BOARD_TABLES = ["cycles","cycle_stages","cycle_proposals","projects","tasks","dev_jobs","agent_requests","jobs"]`.
Add `cycle_stages`, `cycle_proposals` to `events._WATCHED` (`cycles` already there); both rows carry `business_id` and
`status`, which `_describe` already forwards. `llm_calls` is deliberately not watched: spend shown on a card is
`Task.spent_usd`, which changes with the task row; elapsed ticks client-side from `started_at`.

### 2.7 Composer Done, `start_business`, `adopt_project`

- **Composer Done** (`web/src/kickoff/Composer.tsx:216`): still `POST /briefs/{id}/approve` then
  `POST /businesses/{id}/start`, but `start` no longer returns a project. It returns `{cycle_id, state}` and the page
  navigates to `/` (the dashboard) instead of `/projects/{id}`. `business.project_ids[0]` stops being the "started"
  signal; `business.cycle_id` (new field of `roadmap.business_list`) is.
- **`start_business`** (`services/projects.py:41`): keeps every refusal (kickoff live, abandoned draft, plan gaps,
  archived), then calls `cycle.open_cycle(s, business_id, mode="new")` instead of `create(...)`. It never creates a
  project. A second call returns the open cycle (idempotent: the `ix_cycles_one_open` unique index plus a lookup).
- **`adopt_project`** (`roadmap.py:1067`): gains `stage: CycleStage | None = None`. With a stage: sets
  `p.business_id`, `p.stage_id`, creates **no Bet**. Without a stage: unchanged (CLI/legacy path, unmanaged businesses).
  `projects.create(..., stage=...)` is called only by `cycle._start_stage`; a project created for a *child*
  (reassess) inherits the parent's `stage_id`.
- `move_bet(RUNNING)` outside a running stage on a managed business: its `run_project` is parked (`gated`) by the
  generic path; `_take_slot` still enforces `bets.max_running`. `cycle._start_stage` is the intended path for
  experiments (stage `bet` draft -> `roadmap.create_bet` -> `move_bet`).

---

## 3. State machines

### 3.1 Stage (`cycle_stages.status`)

```
planned --start_cycle/start_next--> running --tick:settled--> closing --store_summary--> awaiting_owner
running --stop_stage--> stopped --resume_stage--> running
awaiting_owner --start_next/finish_cycle/stop_here--> done
awaiting_owner --retry_stage--> running           (round += 1)
planned --revision removes it / cycle stopped--> skipped
```

```python
STAGE_TRANSITIONS: dict[str, frozenset[str]] = {
    "planned":        frozenset({"running", "skipped"}),
    "running":        frozenset({"closing", "stopped"}),
    "closing":        frozenset({"awaiting_owner", "stopped"}),
    "awaiting_owner": frozenset({"done", "running"}),
    "stopped":        frozenset({"running"}),
    "done": frozenset(), "skipped": frozenset(),
}
```
`_move(stage, to, cause, by)` is the only function that assigns `stage.status`; it checks the table, stamps the
timestamp for `to`, writes `activity.record`, and is a no-op returning False when `stage.status == to`.

### 3.2 Cycle (`cycles.state`)

`draft` (proposal being written / waiting for Start; stage rows do not exist yet) -> `running` (Start) -> `retro`
(last stage accepted) -> `closed`; `draft|running -> stopped` (stop_here). `closed_at` is set on `closed|stopped` and
frees the "one open cycle" unique index. "CEO is writing" / "proposal ready" / "revising" are **not** cycle states:
they are derived from the latest `cycle_proposals.status` (`drafting|ready`), so a revision at a gate (cycle `running`)
needs no extra state.

### 3.3 Proposal (`cycle_proposals.status`)

`drafting -> ready -> superseded` (when a later version becomes ready) and `drafting -> failed` (the owner keeps the
previous `ready` version and a "revision failed, try again" line). Unique `(cycle_id, version)` makes "revise" a
natural idempotency point; a partial unique index allows one `drafting` per cycle.

---

## 4. Type sketch

### 4.1 SQLAlchemy (`db/models.py`)

```python
CYCLE_STATES = ("draft", "running", "retro", "closed", "stopped")
STAGE_STATUSES = ("planned", "running", "closing", "awaiting_owner", "done", "skipped", "stopped")
OPEN_STAGE = ("running", "closing")                      # spend allowed
HOLDING_STAGE = ("running", "closing", "awaiting_owner") # at most one per business
ENDS_WHEN = ("run_settled", "window_closed", "owner_marks")
PROPOSAL_STATUSES = ("drafting", "ready", "superseded", "failed")


class Cycle(Base):                                   # existing table, revived
    __tablename__ = "cycles"
    # existing: id, business_id, number, starts_on, ends_on, closed_at, created_at; ck_cycles_span; ix_cycles_one_open
    state: Mapped[str] = mapped_column(String(10), default="draft")           # CYCLE_STATES
    mode: Mapped[str] = mapped_column(String(6), default="new")              # new | adopt (existing business)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_reason: Mapped[str | None] = mapped_column(Text)
    retro: Mapped[dict[str, Any] | None] = mapped_column(JSONB)              # CycleRetroOut + server numbers
    # starts_on / ends_on stay: the planned span (cycle_days). A target, shown as a guess, never an enforcer.


class CycleProposal(Base):
    """One version of the cycle's stage list. The CEO writes it; the owner reads it, starts it, or asks for a change."""
    __tablename__ = "cycle_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("cycles.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="drafting")      # PROPOSAL_STATUSES
    owner_note: Mapped[str | None] = mapped_column(Text)                     # the owner's "what to change"
    intro: Mapped[str | None] = mapped_column(Text)                          # 2-3 sentences on this cycle
    stages: Mapped[list[Any]] = mapped_column(JSONB, default=list)           # list[StageDraft] incl. frozen done stages
    changes: Mapped[list[Any]] = mapped_column(JSONB, default=list)          # server-computed diff vs previous version
    basis: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)       # what the estimates were grounded on
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("cycle_id", "version"),
                      Index("ix_cycle_proposals_one_drafting", "cycle_id", unique=True,
                            postgresql_where=text("status = 'drafting'")))


class CycleStage(Timestamped, Base):
    """One owner-started piece of a cycle. The only store of "which stage is running"."""
    __tablename__ = "cycle_stages"
    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("cycles.id"), index=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)   # immutable copy of the cycle's
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("cycle_proposals.id"))
    key: Mapped[str] = mapped_column(String(40))                  # stable across revisions; unique per cycle
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(120))
    doing: Mapped[str] = mapped_column(Text)                      # what the company does (becomes the project goal)
    owner_does: Mapped[str] = mapped_column(Text)
    decision_points: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    est_minutes: Mapped[int | None] = mapped_column(Integer)      # a guess; the UI says so
    est_usd: Mapped[float | None] = mapped_column(Float)
    estimate_basis: Mapped[str] = mapped_column(String(8), default="guess")   # guess | history
    ends_when: Mapped[str] = mapped_column(String(14), default="run_settled")
    allows_kinds: Mapped[list[Any]] = mapped_column(JSONB, default=list)      # STAGE_RECURRING kinds this stage enables
    allows_owner_work: Mapped[list[Any]] = mapped_column(JSONB, default=list) # OwnerTask kinds this stage may create (owner_does, machine-readable)
    listen_days: Mapped[int | None] = mapped_column(Integer)                   # window_closed only
    listen_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bet_id: Mapped[int | None] = mapped_column(Integer)           # the experiment this stage runs, if any
    adopted: Mapped[bool] = mapped_column(Boolean, default=False) # carries pre-cycle work (existing business)
    status: Mapped[str] = mapped_column(String(14), default="planned", index=True)
    round: Mapped[int] = mapped_column(Integer, default=1)        # bumped by retry_stage; keys summary jobs
    close_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    awaiting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_cause: Mapped[str | None] = mapped_column(String(16))     # accepted | stopped_here | revised_away | adopted_past | cycle_stopped
    decided_by: Mapped[str | None] = mapped_column(String(40))    # owner | "claude (오너 위임)"
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB) # StageSummary (model judgement + server numbers)
    summary_by: Mapped[str | None] = mapped_column(String(8))     # ceo | server
    __table_args__ = (UniqueConstraint("cycle_id", "key"),
                      CheckConstraint(f"status IN ({_in(STAGE_STATUSES)})", name="ck_cycle_stages_status"),
                      CheckConstraint(f"ends_when IN ({_in(ENDS_WHEN)})", name="ck_cycle_stages_ends_when"),
                      Index("ix_cycle_stages_one_holding", "business_id", unique=True,
                            postgresql_where=text("status IN ('running','closing','awaiting_owner')")))

# existing tables, new columns (nullable unless noted)
#   Project.stage_id   FK cycle_stages.id   index   -- set once, by cycle._start_stage / child inheritance
#   Job.gate           String(6) NOT NULL   -- 'none' (FREE: may not spend) | 'direct' (owner asked) | 'stage'; no default after the migration
#   Job.stage_id       FK cycle_stages.id   -- attribution (both gates) and the claim condition (gate='stage')
#   Job.business_id    Integer              -- partial index WHERE status='gated'
#   Job.status         gains the value 'gated' (String(16): no schema change; claim already selects 'queued' only)
#   LLMCallRecord.stage_id FK cycle_stages.id -- stamped from the lease in agents/executor._run_once
```

### 4.2 `services/gate.py` (new, leaf module: policy and the one open-stage query; no imports from queue or cycle)

```python
class Gate(StrEnum):
    STAGE_WORK = "stage_work"; STAGE_RECURRING = "stage_recurring"; ASKED = "asked"
    CYCLE = "cycle"; FREE = "free"
    # no COMPANY gate: company-wide kinds are STAGE_RECURRING with scope="company". Every spend is approved or denied.

class UndeclaredJobKind(RuntimeError): ...

@dataclass(frozen=True)
class Rule:
    gate: Gate
    agent: str | None = None            # who a job action card names (None: cards come from tasks/dev jobs)
    scope: Literal["business", "company"] = "business"   # company: no business payload; any approved stage naming it
    why: str = ""                       # REQUIRED for ASKED / CYCLE / FREE: the sentence that says who asked (test enforces)

POLICY: dict[str, Rule] = {             # THE list. tests/test_gate_policy.py: keys == _handlers() keys
    "run_project": Rule(Gate.STAGE_WORK), "resume_project": Rule(Gate.STAGE_WORK), ...
    "find_people": Rule(Gate.STAGE_RECURRING, "research"), "promotion_week": Rule(Gate.STAGE_RECURRING, "marketing"), ...
    "portfolio_review": Rule(Gate.STAGE_RECURRING, "ceo", scope="company"), ...
    "kickoff_turn": Rule(Gate.ASKED, "ceo", why="the owner's own conversation"), ...
    "cycle_summarize": Rule(Gate.CYCLE, "ceo", why="how the gate opens"), ...
    "notify": Rule(Gate.FREE, why="delivers a message; no model, no owner work"), ...
}   # a kind not in this dict is denied
RECURRING_KINDS: tuple[str, ...] = tuple(k for k, r in POLICY.items() if r.gate is Gate.STAGE_RECURRING)  # derived

Verdict = Literal["run", "park", "skip"]

@dataclass(frozen=True)
class Admission:
    verdict: Verdict
    gate: Literal["none", "direct", "stage"]   # none: FREE (may not spend). direct: owner asked, no stage needed. stage
    stage_id: int | None
    business_id: int | None

def rule(kind: str) -> Rule:
    """Default-deny. A kind with no row raises UndeclaredJobKind (enqueue fails, the worker refuses to start)."""
    try:
        return POLICY[kind]
    except KeyError:
        raise UndeclaredJobKind(f"job kind {kind!r} is not declared in gate.POLICY") from None

def owner_work_allowed(s: Session, business_id: int | None, kind: str) -> bool:
    """Whether the open stage lists this OwnerTask kind in allows_owner_work. owner_tasks._insert asks before creating one."""
    raise NotImplementedError

def managed(s: Session, business_id: int | None) -> bool:
    """Approved brief or any cycle row. Unmanaged businesses (CLI projects, [TEST], kickoff in progress) have no approver:
    STAGE_WORK is admitted 'direct'; STAGE_RECURRING is still skipped."""
    raise NotImplementedError

def open_stage(s: Session, business_id: int, *, lock: Literal["share", None] = None) -> CycleStage | None:
    """THE query for 'which stage is running': status IN ('running','closing'). lock='share' is taken by enqueue so a
    close (FOR UPDATE) and an admission serialize."""
    raise NotImplementedError

def stage_is_open(s: Session, stage_id: int) -> bool:
    raise NotImplementedError   # PK read, used by check_lease

def admit(s: Session, kind: str, payload: dict[str, Any], *, by_owner: bool = False) -> Admission:
    """Pure policy over (kind, payload, DB reads). No writes.
    # TODO:
    #   pid, bid = scope.owner_of(s, payload)                    # existing resolver
    #   r = rule(kind)                                            # raises for an undeclared kind
    #   if r.gate is FREE: return Admission("run","none", None, bid)
    #   st = open_stage(s, bid, lock="share") if bid else None
    #   if r.gate is CYCLE: return Admission("run","direct", payload.get("stage_id") or (st and st.id), bid)
    #   if r.gate is STAGE_RECURRING and by_owner: r = ASKED       # the owner pressed "run now"; the only upgrade, by_owner defaults False
    #   if r.gate is ASKED: stage_id = (project(pid).stage_id if pid else None) or (st and st.id)
    #                       return Admission("run","direct", stage_id, bid)
    #   if r.gate is STAGE_RECURRING:
    #       if r.scope == "company": st = lowest-id open stage over all businesses whose allows_kinds has kind
    #       return Admission("run","stage", st.id, bid) if st and kind in st.allows_kinds else Admission("skip","none",None,bid)
    #   # STAGE_WORK
    #   if not managed(s, bid): return Admission("run","direct", None, bid)
    #   return Admission("run","stage", st.id, bid) if st else Admission("park","stage", None, bid)
    """
    raise NotImplementedError
```

### 4.3 CEO contracts (`contracts/cycle.py`, Pydantic; strict-schema style: every field required, nullable ones say so)

```python
Confidence = Literal["high", "mid", "low"]
EndsWhen = Literal["run_settled", "window_closed", "owner_marks"]
RecurringKind = Literal[*RECURRING_KINDS]            # derived from gate.POLICY, so the model can only name real kinds
OwnerWorkKind = Literal[*OWNER_WORK_KINDS]           # derived from the OwnerTask kinds in services/owner_tasks.py (verify at build: talk, post, ...)

class ExistingRef(BaseModel):                         # adopt mode only
    kind: Literal["project", "bet", "schedule"]
    id: int

class StageDraft(BaseModel):
    key: str = Field(description="Stable slug, 3-30 chars, unique in the cycle. Keep a stage's key when you edit it")
    title: str = Field(description="Short stage name in the owner's language, for example 'Validate'")
    doing: str = Field(description="What the company does in this stage, in 2-3 sentences. This becomes the goal of its run")
    owner_does: str = Field(description="What the owner does in this stage. 'Nothing' when nothing")
    decision_points: list[str] = Field(description="Moments the owner will be asked to decide. Empty when none")
    est_minutes: int | None = Field(description="Guess of the working time. null when you cannot tell", ge=1, le=20160)
    est_usd: float | None = Field(description="Guess of the usage in USD equivalent. null when you cannot tell", ge=0)
    estimate_basis: Literal["guess", "history"] = Field(description="history only when the input has earlier stages or projects of this business to compare")
    ends_when: EndsWhen = Field(description="run_settled for work the company does. window_closed for waiting on market reaction. owner_marks when the owner does the work")
    listen_days: int | None = Field(description="Only for window_closed. Otherwise null", ge=1, le=60)
    allows_recurring: list[RecurringKind] = Field(description="Recurring jobs that may run in this stage. Empty when none. A job not listed here does not run, so list every one this stage needs and say each in doing")
    allows_owner_work: list[OwnerWorkKind] = Field(description="Kinds of to-do this stage may put in the owner's list (talk, post, ...). Empty when none. Must agree with owner_does: the owner reads both before starting")
    experiment: NextBet | None = Field(description="An experiment this stage runs (promotion, reaction). null for other stages")  # reuses contracts.retro.NextBet
    maps_existing: list[ExistingRef] = Field(description="Adopt mode: running work this stage takes over. Empty otherwise")

class CycleProposalOut(BaseModel):
    intro: str = Field(description="Two or three sentences on what this cycle is for")
    stages: list[StageDraft] = Field(description="4 to 9 stages, in order", min_length=4, max_length=9)
    past_stage_keys: list[str] = Field(description="Adopt mode: keys of stages whose work is already finished. Empty otherwise")
    current_stage_key: str | None = Field(description="Adopt mode: the stage the running work belongs to. null otherwise")

class ChangeNote(BaseModel):
    stage_key: str
    why: str = Field(description="Why you changed this stage, one sentence, answering the owner's note")

class CycleRevisionOut(BaseModel):
    reply: str = Field(description="One to three sentences to the owner: what you changed and what you did not")
    intro: str
    stages: list[StageDraft] = Field(description="The whole list after the change, in order")
    why: list[ChangeNote] = Field(description="One entry per stage you added, removed, edited or moved")

class Claim(BaseModel):
    claim: str = Field(description="One sentence the owner can check")
    evidence_ids: list[str] = Field(description="Ids from the input only, such as T-12, mp-3. Empty when none")
    confidence: Confidence = Field(description="low when there is no evidence id")

class StageSummaryOut(BaseModel):
    headline: str = Field(description="One sentence: how the stage went")
    done: list[str] = Field(description="What the company finished, as short sentences")
    not_done: list[str] = Field(description="What it did not finish and why. Empty when nothing")
    claims: list[Claim] = Field(description="What the stage showed, each with evidence and confidence")
    needs_owner: list[str] = Field(description="What only the owner can decide or do next. Empty when nothing")
    next_key: str | None = Field(description="Key of the stage you suggest starting next. null at the last stage")
    next_reason: str | None = Field(description="Why that one, one sentence. null at the last stage")
    change_suggestions: list[str] = Field(description="Changes to the remaining stages you would suggest. Empty when none")

class CycleRetroOut(BaseModel):                       # reuses contracts.retro building blocks
    causes: list[Cause]; counter_evidence: list[str]; verdict: Verdict; verdict_reason: str
    next_cycle_note: str = Field(description="What the next cycle should be about, two sentences")
```

### 4.4 Read-model types (Pydantic on the API edge; domain rows are parsed behind `cycle_board.py`)

```python
BoardState = Literal["none", "proposing", "proposal", "revising", "running", "closing", "gate", "retro", "stopped", "legacy"]

class StageChip(BaseModel):
    id: int; key: str; position: int; title: str
    status: Literal["planned","running","closing","awaiting_owner","done","skipped","stopped"]
    tasks_done: int | None; tasks_total: int | None          # null for planned stages; counts, never percent
    is_current: bool; adopted: bool

class TaskRow(BaseModel):
    id: int; ref: str; title: str; agent: str; status: str; started_at: datetime | None; project_id: int

class StageEstimate(BaseModel):
    minutes: int | None; usd: float | None; basis: Literal["guess", "history"]   # the UI always prints "guess"

class StageCard(BaseModel):                                # a stage in a proposal or at the gate
    key: str; title: str; doing: str; owner_does: str; decision_points: list[str]
    estimate: StageEstimate; recurring: list[str]; owner_work: list[str]; change: Literal["added","edited","moved","same"] | None

class ProposalCard(BaseModel):
    kind: Literal["proposal"] = "proposal"
    cycle_id: int; version: int; intro: str; stages: list[StageCard]
    changes: list[ChangeOut]                               # what changed vs the version before, server-computed
    reply: str | None; revision_failed: str | None
    actions: list[Literal["start_cycle", "revise"]]

class GateCard(BaseModel):
    kind: Literal["gate"] = "gate"
    cycle_id: int; stage_id: int; stage_title: str
    summary: StageSummaryView | None                       # headline, done, not_done, claims(+confidence), cost_usd, elapsed_s, tasks, by
    next_stage: StageCard | None                           # None at the last stage -> finish_cycle
    parked_jobs: int                                       # gated jobs waiting for the next stage
    actions: list[Literal["start_next", "finish_cycle", "revise", "stop_here", "retry_stage"]]

class BlockedCard(BaseModel):
    kind: Literal["blocked"] = "blocked"; request_id: int; title: str; project_id: int

class ActionCard(BaseModel):                               # one per WORKING agent; idle agents get no card
    agent: str; cycle_number: int; stage_id: int; stage_title: str
    kind: Literal["task", "review", "dev", "job"]
    task_id: int | None; dev_job_id: int | None; job_id: int | None
    title: str; project_id: int | None; started_at: datetime | None; spent_usd: float

class CurrentStage(BaseModel):
    stage: StageChip; doing: str
    tasks: list[TaskRow]; tasks_done: int; tasks_total: int
    cost_usd: float                                        # SUM(llm_calls.cost_usd WHERE stage_id)
    stopped: bool

class CycleBoardOut(BaseModel):
    state: BoardState
    sentence: str                                          # server-written, owner's language
    cycle: CycleHead | None                                # id, number, state, starts_on, ends_on
    waiting: list[ProposalCard | GateCard | BlockedCard] = Field(discriminator="kind")
    strip: list[StageChip]
    current: CurrentStage | None
    working: list[ActionCard]
    idle_agents: int
    history: list[CycleRowOut]                             # closed cycles, collapsed in the UI
```

```ts
// web/src/cycle/types.ts
export type BoardState = "none"|"proposing"|"proposal"|"revising"|"running"|"closing"|"gate"|"retro"|"stopped"|"legacy";
export type StageStatus = "planned"|"running"|"closing"|"awaiting_owner"|"done"|"skipped"|"stopped";
export interface StageChip { id:number; key:string; position:number; title:string; status:StageStatus;
  tasks_done:number|null; tasks_total:number|null; is_current:boolean; adopted:boolean }
export interface StageCard { key:string; title:string; doing:string; owner_does:string; decision_points:string[];
  estimate:{minutes:number|null; usd:number|null; basis:"guess"|"history"}; recurring:string[]; owner_work:string[];
  change:"added"|"edited"|"moved"|"same"|null }
export interface ProposalCard { kind:"proposal"; cycle_id:number; version:number; intro:string; stages:StageCard[];
  changes:{stage_key:string; kind:string; what:string; why:string|null}[]; reply:string|null;
  revision_failed:string|null; actions:("start_cycle"|"revise")[] }
export interface GateCard { kind:"gate"; cycle_id:number; stage_id:number; stage_title:string;
  summary:StageSummaryView|null; next_stage:StageCard|null; parked_jobs:number;
  actions:("start_next"|"finish_cycle"|"revise"|"stop_here"|"retry_stage")[] }
export interface ActionCard { agent:string; cycle_number:number; stage_id:number; stage_title:string;
  kind:"task"|"review"|"dev"|"job"; task_id:number|null; dev_job_id:number|null; job_id:number|null;
  title:string; project_id:number|null; started_at:string|null; spent_usd:number }
export interface CycleBoard { state:BoardState; sentence:string; cycle:CycleHead|null;
  waiting:(ProposalCard|GateCard|BlockedCard)[]; strip:StageChip[]; current:CurrentStage|null;
  working:ActionCard[]; idle_agents:number; history:CycleRow[] }
export type Decision =
  | {kind:"start_cycle"; version:number} | {kind:"revise"; note:string; base_version:number; at_stage_id:number|null}
  | {kind:"start_next"|"finish_cycle"|"stop_here"; after_stage_id:number}
  | {kind:"retry_stage"|"stop_stage"|"resume_stage"|"close_stage_now"; stage_id:number};
```

---

## 5. Function signatures

### 5.1 `services/cycle.py` (the only writer; ~9 public names)

```python
class StaleDecision(ControlError): ...        # 409: the card the owner clicked is old

# Decisions: the owner's whole write surface, as data
class StartCycle(BaseModel):   kind: Literal["start_cycle"] = "start_cycle";  version: int
class Revise(BaseModel):       kind: Literal["revise"] = "revise"; note: str; base_version: int; at_stage_id: int | None = None
class StartNext(BaseModel):    kind: Literal["start_next"] = "start_next";    after_stage_id: int
class FinishCycle(BaseModel):  kind: Literal["finish_cycle"] = "finish_cycle"; after_stage_id: int
class StopHere(BaseModel):     kind: Literal["stop_here"] = "stop_here";      after_stage_id: int
class RetryStage(BaseModel):   kind: Literal["retry_stage"] = "retry_stage";  stage_id: int
class StopStage(BaseModel):    kind: Literal["stop_stage"] = "stop_stage";    stage_id: int
class ResumeStage(BaseModel):  kind: Literal["resume_stage"] = "resume_stage"; stage_id: int
class CloseStageNow(BaseModel): kind: Literal["close_stage_now"] = "close_stage_now"; stage_id: int
Decision = Annotated[StartCycle | Revise | StartNext | FinishCycle | StopHere | RetryStage | StopStage
                     | ResumeStage | CloseStageNow, Field(discriminator="kind")]


def open_cycle(s: Session, business_id: int, *, mode: Literal["new", "adopt"], lang: Lang) -> Cycle:
    """Open the business's next cycle and ask the CEO for proposal v1. Idempotent: returns the open cycle if there is one.
    # TODO:
    #   lock business row; if open cycle: return it
    #   number = max+1; starts_on = today; ends_on = today + cycle_days          (ck_cycles_span holds)
    #   Cycle(state="draft", mode=mode); CycleProposal(version=1, status="drafting")
    #   queue.enqueue("cycle_propose", {"cycle_id", "version": 1, "mode"}, key=f"cycle_propose:{cycle.id}:1", max_attempts=3)
    #   activity.record(...)
    """
    raise NotImplementedError


def decide(s: Session, cycle_id: int, decision: Decision, *, by: str, lang: Lang) -> None:
    """The owner's single write path. Locks the cycle row, applies the decision by target state (idempotent), raises
    StaleDecision when the card is old, ControlError for a refusal with a sentence. Every branch ends in _move().
    # TODO: cycle = s.get(Cycle, id, with_for_update=True); dispatch on decision.kind:
    #   StartCycle   : cycle.state=="running" -> return (idempotent); require state=="draft", latest ready version == d.version
    #                  (else StaleDecision), no drafting proposal; _materialize(cycle, proposal); cycle.state="running";
    #                  cycle.started_at=now; _start_stage(first non-past planned stage | adopted stage)
    #   Revise       : base_version must be the latest ready version (else StaleDecision); no drafting proposal;
    #                  insert CycleProposal(version+1, drafting, owner_note); enqueue cycle_revise key f"cycle_revise:{id}:{v+1}"
    #                  (the INSERT conflicts on a double click -> return)
    #   StartNext    : stage=after; if stage.status=="awaiting_owner": no drafting proposal; nxt=_next_planned(cycle, stage);
    #                    _move(stage,"done","accepted"); _start_stage(nxt)           # same txn; order keeps the unique index happy
    #                  elif stage.status=="done" and successor running/done: return   # double click
    #                  else StaleDecision
    #   FinishCycle  : stage awaiting_owner and no planned stage left -> _move(stage,"done"); cycle.state="retro";
    #                  enqueue cycle_retro key f"cycle_retro:{id}"
    #   StopHere     : _move(stage,"done","stopped_here"); skip remaining planned; cycle.state="stopped"; closed_at=now
    #   RetryStage   : awaiting_owner -> running, round+=1; queue.release_gated(...); control.retry_project for FAILED projects
    #   StopStage    : running -> stopped (claim stops immediately; tick parks; check_lease stops handlers)
    #   ResumeStage  : stopped -> running; queue.release_gated(business, stage)
    #   CloseStageNow: stage.close_requested_at=now; pause live projects of the stage (control.pause); tick settles it
    #   stamp stage.decided_by = by
    """
    raise NotImplementedError


def tick(s: Session, now: datetime | None = None) -> list[str]:
    """The system's only transition driver (scheduled FREE job `cycle_tick`, every 60 s). A pure function of DB state, safe to
    repeat and safe on several workers (row locks + CAS). Returns what it did, for the log.
    # TODO per business with a holding stage (SELECT ... FOR UPDATE SKIP LOCKED on cycle_stages):
    #   running  and settled(s, st, now) -> _move(st,"closing"); enqueue cycle_summarize
    #   closing  and no live summarize job: attempts_left -> re-enqueue (round-keyed) else store_summary(st.id, None)
    #   stopped/non-open: queue.park_stranded(s)              # queued jobs whose stage is not open -> gated
    # per cycle: draft + latest proposal 'drafting' + no live cycle_propose/revise job -> enqueue it   (repairs migration rows)
    # per cycle: retro + no live cycle_retro job and no stored retro -> enqueue it
    """
    raise NotImplementedError


def store_proposal(s: Session, cycle_id: int, version: int, out: CycleProposalOut | CycleRevisionOut,
                   packet: ProposalPacket, *, lang: Lang) -> CycleProposal:
    """Server-side validation then drafting -> ready. Validates 4..9 stages; unique keys; done/running stages untouched
    (a mid-cycle revision may only change stages after the gate); every live project/bet/schedule of an adopt packet is
    in exactly one stage's maps_existing (leftovers are appended to current_stage_key); recurring kinds exist;
    estimates clamped to limits(). Computes `changes` with diff_stages(prev, new): the model's `why` is attached to the
    server's diff, never the other way round. Supersedes the previous ready version. Mid-cycle: rewrites the planned rows
    (existing keys updated, new keys inserted, removed keys -> skipped/'revised_away').
    """
    raise NotImplementedError


def fail_proposal(s: Session, cycle_id: int, version: int, error: str) -> None:
    """drafting -> failed. The previous ready version stays the one the owner sees, with `revision_failed`."""
    raise NotImplementedError


def store_summary(s: Session, stage_id: int, round: int, out: StageSummaryOut | None, packet: SummaryPacket) -> bool:
    """closing -> awaiting_owner (CAS on status and round; the first writer wins; False when it already moved).
    out=None writes the server-built summary (summary_by="server"). Overwrites every number with the packet's.
    Notifies the owner (queue.notify(notices.stage_gate(...)))."""
    raise NotImplementedError


def store_retro(s: Session, cycle_id: int, out: CycleRetroOut, packet: RetroPacket) -> None:
    """retro -> closed (closed_at=now); then open_cycle(mode="new") for the next cycle (its proposal reads this retro)."""
    raise NotImplementedError


# --- private; nothing outside this module imports these
def _move(s: Session, st: CycleStage, to: str, cause: str | None = None, by: str | None = None) -> bool: ...
def _materialize(s: Session, cycle: Cycle, proposal: CycleProposal) -> list[CycleStage]: ...
def _start_stage(s: Session, st: CycleStage, *, lang: Lang) -> None:
    """planned -> running, then in the same transaction:
    # TODO:
    #   _move(st,"running"); st.started_at=now; st.listen_until = now + listen_days if window_closed
    #   queue.release_gated(s, st.business_id, st.id)           # parked jobs from before join this stage
    #   if st.bet_id or draft.experiment: bet = roadmap.create_bet(...)/existing; roadmap.move_bet(bet, RUNNING)  # project is stamped below
    #   elif no live project to carry: p = projects.create(s, goal_for_stage(st), name=st.title, business=biz, stage=st)
    #        -> adopt_project(stage=st) sets p.stage_id and creates no Bet; enqueue run_project passes the gate (stage is running)
    """
def _settled(s: Session, st: CycleStage, now: datetime) -> bool: ...
def _next_planned(s: Session, cycle: Cycle, after: CycleStage) -> CycleStage | None: ...
```

### 5.2 `services/queue.py` (changes, all small)

```python
def enqueue(s, kind, payload, *, idempotency_key=None, delay_s=0, max_attempts=3, by_owner=False) -> Job | None:
    # + adm = gate.admit(s, kind, payload, by_owner=by_owner)   # by_owner: new kwarg, default False; only the owner-button sites pass True
    # + if adm.verdict == "skip": log.info(...); return None
    # + Job(..., gate=adm.gate, stage_id=adm.stage_id, business_id=adm.business_id,
    # +      status="gated" if adm.verdict == "park" else "queued")
    ...

def claim(s, worker_id, kinds=None, *, held=None) -> Job | None:
    # + open_stages = select(CycleStage.id).where(CycleStage.status.in_(OPEN_STAGE))
    # + q = q.where(or_(Job.gate.in_(("none", "direct")), and_(Job.gate == "stage", Job.stage_id.in_(open_stages))))
    ...

@dataclass(eq=False)
class Lease:     # + stage_id: int | None = None ; + gate: str = "direct"   (workers/runner.py:303 passes them from the claimed row)

def check_lease() -> None:
    # + lease = current_lease.get()
    # + if lease and lease.gate == "none": raise GiveUp("a FREE job kind tried to spend model usage")   # default-deny backstop, at the call
    # + if lease and lease.gate == "stage" and not gate.stage_is_open(lease.stage_id): raise Stopping()
    ...

def release_gated(s: Session, business_id: int, stage_id: int) -> int:
    """gated -> queued for the business, stamped with the stage (gate='stage'). Skips (fails with a note) jobs whose project is archived."""

def park_stranded(s: Session) -> int:
    """queued, gate='stage', stage not open -> gated (the stage keeps stage_id so a resume re-opens the same rows)."""
```

### 5.3 `services/cycle_ceo.py` (new; the CEO jobs, thin shells over `run_agent`)

```python
def propose(cycle_id: int, version: int, mode: str, *, attempt: int, max_attempts: int) -> None:
    """Handler `cycle_propose`. Packet = approved brief, business/product state, past cycles' retros and summaries,
    cost priors (median task/project spend by owner agent, durations of earlier stages: 'history' basis), and for
    mode='adopt' the live work (projects with status/phase/task counts, bets, active schedules, finished projects).
    run_agent("ceo", "cycle_propose", ..., CycleProposalOut, project_id=None, business_id=...) -> cycle.store_proposal."""
    raise NotImplementedError

def revise(cycle_id: int, version: int, *, attempt: int, max_attempts: int) -> None:
    """Handler `cycle_revise`: previous version + owner_note (+ done stages' summaries when mid-cycle) -> CycleRevisionOut."""
    raise NotImplementedError

def summarize(stage_id: int, round: int, *, attempt: int, max_attempts: int) -> None:
    """Handler `cycle_summarize`: build SummaryPacket (server facts), run the CEO, cycle.store_summary(out, packet).
    On the last attempt, store_summary(None, packet)."""
    raise NotImplementedError

def retro(cycle_id: int, *, attempt: int, max_attempts: int) -> None:
    """Handler `cycle_retro`: all stage summaries + bet retros + numbers -> CycleRetroOut -> cycle.store_retro."""
    raise NotImplementedError

def diff_stages(prev: list[StageDraft], new: list[StageDraft]) -> list[ChangeOut]:
    """Pure. added / removed / edited (which fields) / moved, keyed by StageDraft.key."""
    raise NotImplementedError
```

### 5.4 `services/cycle_board.py` and `api/cycles.py`

```python
def board(s: Session, business_id: int, *, lang: Lang) -> CycleBoardOut:
    """Read-only. Queries: (1) latest cycle + latest ready/drafting proposal, (2) its stages, (3) task counts per stage
    (GROUP BY projects.stage_id, tasks.status), (4) the current stage's tasks, (5) work_now.running_rows(stage_id)
    (tasks RUNNING/REVIEW/REWORK + unsettled DevJobs + in-flight stage-stamped jobs via gate.POLICY agent),
    (6) SUM(llm_calls.cost_usd) by stage_id + gated job count + pending blocking AgentRequests of the stage's projects.
    State is derived from (cycle.state, proposal.status, holding stage.status); sentence = msg(f"cycle.sentence.{state}", ...)."""
    raise NotImplementedError

# api/cycles.py: GET board / GET cycles / GET cycle / POST decisions. The POST body is `Decision`; the handler is
#   with session_scope() as s: cycle.decide(s, cycle_id, body, by="owner", lang=...); return cycle_board.board(s, ...)
```

---

## 6. Module map

New:
| File | Purpose |
|---|---|
| `backend/justshipit/services/gate.py` | Gate enum, `POLICY` (the one list), `admit`, `open_stage`, `stage_is_open`, `managed`. Leaf: imports models and `scope` only |
| `backend/justshipit/services/cycle.py` | State machines, `decide`, `tick`, `store_*`, the only writer of cycle tables and `projects.stage_id` |
| `backend/justshipit/services/cycle_ceo.py` | The four CEO job handlers, packets, validation, `diff_stages` |
| `backend/justshipit/services/cycle_board.py` | Dashboard read model |
| `backend/justshipit/services/work_now.py` | Extracted "who is working on what" rows (shared by `/overview` and the board) |
| `backend/justshipit/contracts/cycle.py` | CEO output contracts + board view models |
| `backend/justshipit/api/cycles.py` | Router (board, history, detail, decisions) |
| `backend/justshipit/db/migrations/versions/<rev>_cycle_stages.py` | Section 7 |
| `prompts/cycle_propose.md`, `cycle_revise.md`, `cycle_summary.md`, `cycle_retro.md` | CEO prompts (default stage template lives in cycle_propose.md) |
| `backend/justshipit/i18n/messages/cycle.py` | en/ko strings: sentences, gate, errors, activity. Glossary: `cycle`, `stage` in `i18n/glossary.py` |
| `web/src/cycle/{types.ts, Board.tsx, Strip.tsx, ProposalCard.tsx, GateCard.tsx, ActionCard.tsx}` + `web/src/i18n/areas/cycle.ts` | UI |
| `tests/test_cycle_*.py` | Section 8 |

Changed:
| File | Change |
|---|---|
| `db/models.py` | `Cycle` columns, `CycleProposal`, `CycleStage`, `Project.stage_id`, `Job.gate/stage_id/business_id`, `LLMCallRecord.stage_id` |
| `services/queue.py` | enqueue stamp, claim predicate, `Lease.stage_id/needs_stage`, `check_lease`, `release_gated`, `park_stranded`; `in_flight()` unchanged (gated is not in flight) |
| `workers/runner.py` | register `cycle_*` handlers and `cycle_tick`; pass `stage_id/gate` into `Lease` |
| `operations/scheduler.py` | `ensure(name="Cycle tick", kind="cycle_tick", hours=1/60)`; `tick` treats `enqueue(...) is None` as "skipped" in the log |
| `agents/executor.py` | `_run_once` writes `LLMCallRecord.stage_id` from `queue.current_lease` (one line) |
| `services/projects.py` | `start_business` -> `cycle.open_cycle`; `create(..., stage=)` |
| `services/roadmap.py` | `adopt_project(stage=)`; delete raw `open_cycle` (moved to `cycle.open_cycle`); `roadmap()` cycle block adds `state`; `business_list` adds `cycle_id` |
| `services/events.py` | `_WATCHED += cycle_stages, cycle_proposals` |
| `services/bet_retro.py` | in a managed business `_next_bets` / `_routine_card` / `_decision_card` write suggestions into `bet.retro` only; no Bet rows, no `requests.create` (2.4) |
| `services/owner_tasks.py`, `services/people_tasks.py` | the three `OwnerTask(...)` constructors become one `owner_tasks._insert`, which asks `gate.owner_work_allowed` |
| `api/growth.py`, `api/capability.py`, `operations/scheduler.run_now`, `cli.py` | owner-button enqueues pass `by_owner=True` (the only way a recurring kind runs outside a stage) |
| `workers/runner.py` | at start: `set(_handlers()) <= set(gate.POLICY)` else refuse to start |
| `api/console.py` | `_agent_states` calls `work_now`; remove the unused `POST /businesses/{id}/cycles` route |
| `orchestration/context.py` | `project_brief` adds "this stage" (title, doing, previous stage summaries) when `project.stage_id` |
| `services/today.py` | one owner-waiting item for a stage at the gate or a proposal ready |
| `web/src/kickoff/Composer.tsx` | `Done.start()` navigates to `/` ; `started` from `business.cycle_id` |
| `web/src/pages/Home.tsx` | renders `<Board/>` above the existing sections; `OVERVIEW_TABLES` unchanged |
| `web/src/business/*` | cycle history reads `/cycles` |
| `docs/` | DESIGN.md width line (2400px) per the brief; ARCHITECTURE.md gate section |

---

## 7. Alembic migration outline (`<rev>_cycle_stages.py`, `down_revision = "d4a8e2c6b917"`)

Operator steps: stop the worker; `alembic upgrade head`; start the worker. Review any autogenerate for dropped LangGraph
checkpoint tables (this migration is hand-written; no autogenerate). Touches migrations => multi-agent review per CLAUDE.md.

```python
def upgrade() -> None:
    # 1. schema
    op.add_column("cycles", sa.Column("state", sa.String(10), nullable=False, server_default="closed"))
    op.add_column("cycles", sa.Column("mode", sa.String(6), nullable=False, server_default="new"))
    op.add_column("cycles", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("cycles", sa.Column("stop_reason", sa.Text)); op.add_column("cycles", sa.Column("retro", JSONB))
    op.create_check_constraint("ck_cycles_state", "cycles", f"state IN ({_in(CYCLE_STATES)})")
    op.create_table("cycle_proposals", ...); op.create_table("cycle_stages", ...)   # + the 3 indexes in 4.1
    op.add_column("projects", sa.Column("stage_id", sa.Integer, sa.ForeignKey("cycle_stages.id"))); op.create_index(...)
    op.add_column("llm_calls", sa.Column("stage_id", sa.Integer, sa.ForeignKey("cycle_stages.id")))
    op.add_column("jobs", sa.Column("gate", sa.String(6), nullable=False, server_default="direct"))  # backfill: rows already queued/running keep running
    op.add_column("jobs", sa.Column("stage_id", sa.Integer, sa.ForeignKey("cycle_stages.id")))
    op.add_column("jobs", sa.Column("business_id", sa.Integer))
    op.create_index("ix_jobs_gated", "jobs", ["business_id"], postgresql_where=sa.text("status = 'gated'"))
    op.alter_column("jobs", "gate", server_default=None)            # from now on a hand-built insert must say which gate

    # 2. old cycles (never used): close, keep. No row is deleted.
    op.execute("UPDATE cycles SET state='closed', closed_at=COALESCE(closed_at, now()) WHERE closed_at IS NULL")

    # 3. map every business that has an approved brief  (raw SQL; no model imports; idempotent: guarded by NOT EXISTS)
    #    live work = projects(status IN CREATED,RUNNING,WAITING_HUMAN,PAUSED, not archived) OR bets(RUNNING,LISTENING)
    #                OR jobs(queued,running) whose payload resolves to the business
    #    a. business with live work  -> cycles(state='draft', mode='adopt', number=max+1, starts_on=today, ends_on=today+cycle_days)
    #       + cycle_stages(key='adopted', position=0, title='Work already in progress', status='running', adopted=true,
    #                      ends_when='run_settled', allows_kinds=<RECURRING kinds that have a queued/running job right now, nothing else>,
    #                      allows_owner_work=<kinds of OwnerTask rows being created by those jobs: none>, started_at=now())
    #       + cycle_proposals(version=1, status='drafting')              # the CEO fills it after deploy (tick repairs: enqueue cycle_propose)
    #       + UPDATE projects SET stage_id=<adopted> WHERE live; UPDATE jobs SET gate='stage', stage_id=<adopted>, business_id=b
    #         WHERE status IN ('queued','running') AND payload resolves to b            -- in-flight work is stamped, not stopped
    #    b. business with an approved brief, finished projects, nothing live -> cycle draft/adopt + proposal v1 drafting, no stage row
    #       (nothing is in flight, so the gate is simply shut until the owner starts a stage)
    #    c. approved brief, no project ever -> cycle draft/new + proposal v1 drafting
    #    d. no approved brief (kickoff still open) -> nothing; Composer Done opens the cycle later
    #    e. ARCHIVED / [TEST] businesses -> nothing (unmanaged: never gated)
    # 4. jobs not stamped by (3) keep gate='direct' (the backfill default): anything queued at upgrade time still runs once.
    #    New rows get 'none'/'direct'/'stage' from enqueue; a recurring SCHEDULE that has no job in flight is NOT carried over
    #    (see below): it runs again only when a stage the owner starts lists it.

def downgrade() -> None:
    # drop jobs.gate/stage_id/business_id, llm_calls.stage_id, projects.stage_id, cycle_stages, cycle_proposals, the new cycles columns.
    # Data loss on downgrade is accepted and named in the migration docstring (stage history); jobs in 'gated' are set back to 'queued' first.
```

### Existing businesses, step by step (owner decision: nothing in flight stops; CEO maps; gate from the next stage)

1. **At upgrade**: in-flight projects are stamped with a synthetic `adopted` stage that is already `running`.
   Queued/running jobs are stamped with it and its `allows_kinds` lists exactly the recurring kinds that have a job in
   flight right now. Result: every run, dev job, retro and in-flight recurring job continues. A schedule with nothing in
   flight (weekly promotion, find_people, GitHub collection) is not "in flight"; under the default-deny principle it
   does **not** fire again until a stage the owner starts lists it. The adoption proposal lists each existing schedule
   (`maps_existing` kind `schedule`) so the CEO puts it in a stage and the owner sees it before it runs again.
2. **After deploy**: `cycle.tick` finds `draft` cycles whose proposal v1 is `drafting` with no live job and enqueues
   `cycle_propose(mode="adopt")`. The packet lists live work (ids). The CEO returns `CycleProposalOut` with
   `maps_existing` on stages, `past_stage_keys` (stages whose work is already finished), `current_stage_key`.
   `store_proposal` guarantees every live id is mapped (leftovers go to the current stage).
3. **Owner reads the proposal** on the dashboard (state `legacy` sentence: "Your running work is kept; here is how I
   would lay it out") and presses Start or asks for changes. Start: stages in `past_stage_keys` are created `done`
   (`end_cause="adopted_past"`), the `adopted` stage row is **relabelled in place** as the current stage (same id, so every
   stamp stays valid), later stages are `planned`.
4. **Gate from the next stage**: the current (adopted) stage ends by the same `ends_when` rule; `tick` closes it, the
   CEO summarizes it, the owner decides. Stage N+1 only starts on `StartNext`.
5. If the owner never presses Start, the adopted stage still ends when its live work settles (no live project, bet,
   or in-flight job); the cycle stays `draft`, the proposal card stays on top, and the gate is shut for new work until
   Start. Nothing scheduled runs meanwhile.

---

## 8. Tests (the ones that pin the design)

- `test_gate_policy`: `set(POLICY) == set(workers.runner._handlers())`; `RECURRING_KINDS` derived; an undeclared kind raises `UndeclaredJobKind` at enqueue and the worker refuses to start; every ASKED/CYCLE/FREE row has `why`; every FREE handler run with `run_agent` patched to raise never reaches it; a FREE lease calling `check_lease` raises `GiveUp`.
- `test_default_deny_after_approval`: approve a brief, tick the scheduler: no `find_people` / `find_threads` job exists and no `OwnerTask` was created; start a stage that lists `find_people` and `allows_owner_work=["talk"]`: both appear; stop the stage: the next tick enqueues nothing.
- `test_bet_retro_followups`: in a managed business a retro writes `bet.retro['next_bets']` and creates no Bet and no AgentRequest.
- `test_claim_gate`: stage `running` claims; `awaiting_owner`/`stopped`/none does not; `gate IN ('none','direct')` always; NULL gate never.
- `test_enqueue_admission`: park when no stage; skip an unlisted recurring kind; ASKED kinds admitted and attributed.
- `test_stage_machine`: every edge of `STAGE_TRANSITIONS`; illegal edges raise; one holding stage per business (unique index).
- `test_decide_idempotent`: double `StartNext`; stale `StartNext`; concurrent `StartNext` x2 (threads) -> one successor.
- `test_tick_crash_safe`: run `tick` twice; kill between settle and enqueue (rollback) -> second tick finishes.
- `test_summary_server_numbers`: model-typed cost/evidence ids are overwritten/dropped; failed summary -> server summary, gate opens.
- `test_stop_stage_in_flight`: handler hits `check_lease` -> `Stopping` -> `release` -> kind becomes `retry_project`; resume re-queues.
- `test_migration_adopt`: a seeded business with a RUNNING project, a queued job and a LISTENING bet keeps all of them claimable after upgrade.
- `test_single_writer`: grep test for assignments to `CycleStage.status`, `.stage_id` outside `cycle.py`/`queue.py`.
- `test_board_shape`: one `CycleBoardOut` for each `BoardState`; i18n parity (en/ko) for `cycle.*`.
