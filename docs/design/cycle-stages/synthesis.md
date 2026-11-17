# Cycle stages: synthesis

The owner approves, and what the owner approved runs by itself. Nothing that spends model usage or creates owner work
runs without that approval (owner, 2026-11-06). The screen brief is `docs/ux/shape-cycle.md`.

Two designs were drawn in parallel from the same grounding. `design.md` is the base (a new `cycle_stages` table owns
the stage state machine). `alternative-b.md` made a stage a `Bet` with `kind='stage'`. This file says which parts of
each the build follows. Where this file and `design.md` disagree, this file wins.

## Why the base is `design.md`

- Both designs converged on the gate: one default-deny policy table, enforced at `queue.claim`, with `queue.enqueue`
  raising on an undeclared kind and a test that the table and the worker's handler list are equal. That agreement is
  kept as is.
- They diverged on what a stage is. A stage row of its own keeps one meaning per entity. A stage-as-Bet puts
  experiment machinery (slots, windows, the bet sweep, auto-pick) behind `kind` branches in `window_end`, `due_move`,
  `bet_retro.run` and the roadmap read model, the "two ways to do one task" and "split ownership" red flags. Stage
  projects get no Bet. An experiment stays an optional `bet_id` on a stage.

## Grafts from `alternative-b.md`

1. **Fail closed outside a job.** `run_agent` with no gate context (a model call not reached from a worker job) raises
   `GateViolation`. CLI and test entry points that call a model on purpose wrap the call in
   `gate.asked("cli:<name>")`, which is greppable. This is in addition to `design.md` 2.4b point 4.
2. **Owner-asked call sites are pinned.** `test_asked_enqueue_sites` lists, per `ASKED` kind, the call sites allowed to
   enqueue it. A new site fails the test until someone reviews it.
3. **Confidence is capped by the evidence ledger.** A summary line's confidence is at most what its cited evidence
   supports in `services/evidence.py`, not only "low when no id survives".
4. **`post_reactions` is `FREE`.** It reads reactions to posts the owner already made by hand and makes no model call.
   The tripwire fails it loudly if that ever changes.

## Decisions on the open questions

- `derive_door_rules` stays `ASKED`. It is one call caused by the owner's approval click and writes no owner to-dos.
- Company-scope recurring kinds (`portfolio_review`, `skill_factory`) run only while some business's open stage lists
  them.
- Schedules with nothing in flight pause at upgrade until a stage the owner starts lists them. This is the owner's
  principle. The adoption proposal lists each existing schedule so the owner sees it before it runs again.
- One project per stage in v1. A child (reassess) project inherits the stage.
- No per-stage spend cap in v1. Project and task budgets still bound a stage.
- Starting a picked Bet outside a stage parks its run, and the Business page says "start a stage first" next to it.

## Build order

Each step ends in a check before the next starts.

1. **Gate core.** `services/gate.py` (`POLICY` with a `why` per row, `admit`, `open_stage`), the migration, the
   `enqueue` stamp, the `claim` predicate, `check_lease`, the `run_agent` tripwire, the single `owner_tasks._insert`,
   and `bet_retro` follow-ups written into the retro only. Proof: `test_gate_policy`, `test_default_deny_after_approval`,
   `test_claim_gate`, `test_enqueue_admission`, `test_bet_retro_followups`, `test_migration_adopt`.
2. **Cycle core.** `services/cycle.py` (`open_cycle`, `decide`, `tick`, `store_*`), `services/cycle_ceo.py` and the four
   prompts, `start_business` opening a cycle. Proof: `test_stage_machine`, `test_decide_idempotent`,
   `test_tick_crash_safe`, `test_summary_server_numbers`, `test_stop_stage_in_flight`.
3. **Read model and API.** `services/work_now.py`, `services/cycle_board.py`, `api/cycles.py`, the SSE tables, the glossary
   terms `cycle` and `stage`. Proof: `test_board_shape`, i18n parity.
4. **Console.** The dashboard board (status sentence, proposal and gate cards, stage strip, current stage tasks,
   action cards), Composer Done going to the dashboard, the Business page history. Proof: screenshots at 1440 and 2550px
   on the demo console, and the live flow on a demo business: approve, proposal, start, stage ends, gate, next stage.

## Amendments from the adversarial review (2026-11-06)

Two adversaries (opus, sonnet) attacked the design with runs against the real tree. Every finding below was rerun and
reproduced. The probes are kept as regression evidence in `tests/probes_cycle_review/` (copied from the reviewers'
worktrees); the build turns each into a real test.

1. **The FREE spend check lives in `run_agent`, not `check_lease`.** `check_lease` runs before every commit under a lease
   (`db/session.py:41`), so a FREE check there fails every housekeeping job at its first commit, `cycle_tick` included,
   and no stage could ever close.
2. **A stage that lists a recurring kind runs it in that stage.** `scheduler.tick` advances `next_run_at` even when the
   job is skipped, so a weekly kind skipped once would not fire for a week. When a stage starts, the schedules of the
   kinds it lists get `next_run_at = now`. A skipped tick still advances (no per-minute retry storm).
3. **Parked rows are in flight.** `queue.in_flight()`, `busy_lock_keys`, `why_busy`, `roadmap.retro_live` and every
   "already queued?" guard count `gated` rows, so parking never creates duplicates (three retros for one bet, four
   retries from four clicks). A bet whose run job is parked does not run its window clock: the bet sweep leaves it alone
   until its run has started.
4. **Every owner click is an owner act.** Each path where the owner's own click enqueues work passes `by_owner=True`:
   `requests.respond` effects (approving, declining, answering a card), console Retry on a job and on a project,
   `people_finder.request` ("find now"), growth and capability buttons, `scheduler.run_now`, CLI. `test_asked_enqueue_sites`
   pins them. An owner-approved card creates its to-do whether or not a stage lists that to-do kind; only to-dos an agent
   creates on its own (plan node, auto-approved cards, finders) need the open stage's `allows_owner_work`. Owner acts are
   never parked silently; if one cannot run, the API refuses with a sentence.
5. **State machine fixes.** `stopped -> done` exists (cause `stopped_here`, spends nothing). `StartCycle` first moves a
   settled adopted stage (`awaiting_owner`) to `done` with cause `adopted_past` in the same transaction, then starts the
   next stage, so the one-holding-stage index never blocks Start.
6. **`closing` admits no new stage work.** Admission and claim treat only `running` as open for `STAGE_WORK` and
   `STAGE_RECURRING`; `closing` admits `CYCLE` kinds and lets already-running work finish.
7. **Repair paths always create a live job.** Every repaired job key carries a round counter (as `roadmap.queue_retro`
   does), and `queue._fail_subject` gains branches for `cycle_propose` / `cycle_revise` (`fail_proposal`), `cycle_summarize`
   (server summary) and `cycle_retro`, so a dead CEO job can never leave the owner looking at a spinner with no button.
8. **The gate card names what waits.** Pending non-blocking cards and parked jobs of the stage are listed on the gate card
   by title, not only counted, and "stop here" releases nothing silently: parked jobs of a stopped cycle are closed with a
   visible reason.
9. **"Working" means working.** `work_now` (shared with Overview) excludes tasks of paused, failed, done or archived
   projects, and dev jobs waiting on the owner (`CLARIFICATION_REQUIRED`, awaiting merge). Those appear as "waiting on you"
   or not at all, never as a working card.
