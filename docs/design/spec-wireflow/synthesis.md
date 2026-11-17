# Spec wireflow: synthesis

The owner does not read a spec that arrives as text, so the owner approves without seeing what will be built
(owner, 2026-11-11: "글만 있으면 안보게 돼"). The spec approval card gets a wireflow: grey screen sketches in the order a
user meets them, one flow per P1 story. The same planned screens come back on the merge card beside the real
captures. A note on one screen sends the spec back, and the next draft shows where that note landed. The screen brief
is `docs/ux/shape-wireflow.md`.

Four runners (opus, fable, sonnet, haiku) drew designs from the same grounding. `design.md` is the fable design. This
file says which parts of it the build changes and what it takes from the other three. Where this file and `design.md`
disagree, this file wins.

## Why the base is `design.md`

A cross-judge from another model family scored it 27 of 30. The opus design scored 23, sonnet 21 and haiku 14. My
first read favoured the opus design. I changed my pick after checking one fact in the captures of a real build.

- **Its screen id fits real captures.** The opus and haiku designs name a frame by screen plus one of six states, so
  a screen holds one frame per state. The J-10 build of BUSINE10-0001 captured `error-empty`, `error-unreadable` and
  `error-xlsx` for one upload screen, and `report-dismissed`, `report-nothing-to-export` and `report-usage-failed` for
  one report screen. A key per screen state, as `design.md` has it, holds all of them.
- **It only adds to the draft contract.** `ScreenStateOut` gains `key` and `sketch`, and each P1 story gains `flow`
  and `empty_screen`. Old payloads and tests still validate. `shows` stays the source of the UX acceptance criteria
  and is what a frame draws when its sketch is empty.
- **It handles products that already exist.** The screen a user meets on opening is keyed `first-open` only when the
  spec changes it. The opus and sonnet designs joined `first-open` to the first step of the first story, which is
  wrong for a change that leaves the opening screen alone.
- **It found a gap the others missed.** `registry.write_files` (`services/registry.py:74`) writes and adds but never
  deletes. A redraft under the same spec key must remove files the new draft no longer has, or the card serves a stale
  `questions.yaml`.
- **It switches the engineer's naming rule on per spec.** `HandoffWorkflow.planned_screens` tells the worker whether
  `screens.yaml` exists, so a build of an old spec is never told to read a missing file.

The sonnet design's main bet was a second model call that draws the sketches after the spec call. It isolates
failure completely and knows the real story ids. It lost because the lenient validators in `design.md` give the same
isolation inside the one existing call, and a second call adds a failure mode, a retry policy and a sketch that can
disagree with the spec it was drawn from. It stays the fallback (graft 8).

## Changes to `design.md`

1. **The layout is a vertical stepper, and the sketches are neat grey.** A throwaway prototype on the real
   BUSINE10-0001 data, in the 482px panel, compared three layouts and two skins
   (`docs/ux/shape-wireflow.md` has the findings). Each step is one line: the frame on the left (about 168px), the
   step number, screen name and result on the right, and an arrow down labelled with what the user does. An error or
   empty frame sits beside the step it branches from, smaller. `design.md` rule 6.1's `data-flow` two-up option and
   its `data-sketch="rough"` skin are deleted. The hand-drawn skin made Korean unreadable at frame size, and a
   horizontal row clipped the third frame.
2. **A frame opens large in place (from the opus design's `FrameFocus`).** Clicking a frame opens it under its step at
   full panel width, with every block, the full words, any earlier note's landing and the note box. Esc closes it and
   returns focus to the frame. The merge card does the same for a capture, because a 1440px capture shrunk to half
   the panel cannot be read.
3. **A note counts as landed only where a frame changed (from the opus design).** `design.md` step 7.5 lets a screen
   note count as included through a requirement alone, so it can land with nothing visible. Here a screen-note row is
   `included` only when a screen it names really changed between the two commits. Each note gets one outcome:
   `changed` (on its screen), `moved` (on another screen), `dropped` (with the spec's reason) or `missed`. A note
   whose screen is gone and that landed nowhere goes in an `unplaced` list at the top of the wireflow.
4. **A failed redraft is derived, not stored (adapted from the sonnet design).** `design.md` leaves the failure path
   open, and the runner passes only the payload to a handler, so a handler cannot tell its last attempt. The card
   reads it instead: a spec in `DRAFT` with no queued or running `redraft_spec` job has a failed redraft. The card
   says so, keeps the owner's notes, and offers "try again", which enqueues the job again. The notes are already in
   `owner_answers`, so nothing is lost either way.
5. **`redraft_spec` takes the spec lock.** `queue._lock_key` gives `write_spec` the key `spec:{project_id}`. A
   redraft gets the same key, so it never races a draft of the same product.
6. **A new job kind is registered in five places.** These are `gate.POLICY`, `workers/runner.py`, the `console.job.*`
   and `company.job.*` labels, and `queue._lock_key`. Migration `e6c3a9f1b204` is a snapshot of the policy at its
   revision, so the kind needs no migration (verify in phase 2 before relying on it).

## Grafts

7. **The engineer's naming rule comes from code, and a misnamed line is caught early.** The `ui-states.txt` sentence
   in `_ui_build` is built from a constant in `wireflow.py` (from the haiku design). `wireflow.lint_ui_states` lists
   lines whose name is no planned id and planned ids with no line, and the worker adds them to the engineer's fix
   feedback (from the sonnet design). A capture that still matches nothing shows under "also built" on the merge card.
8. **The second draw call is the fallback (from the sonnet design).** If phase 1's check on a real spec shows that one
   call draws poor sketches, a small second call draws them from the finished stories. The same call can later redraw
   after clarification answers.
9. **Clipping is never silent (from the haiku design).** When `wireflow.plan` drops a screen, a step or a block, the
   card shows one plain line from `msg()`, such as "2 screens could not be drawn".

## Decisions the owner already made

- Visual form is a wireflow from a fixed component vocabulary, not free HTML or images (2026-11-11).
- Scope is the approval card and the merge comparison (2026-11-11, shape round 1).
- A note on a screen drives a redraft (2026-11-11, shape round 1).

## Defaults the build takes unless the owner says otherwise

- A decline with only a general note does not redraft, as today. Only screen notes send the spec back.
- While a screen note is typed, Approve is off. The owner sends the notes back or clears them.
- When the owner answers a spec question differently from the recommendation, the sketches are not redrawn. Each
  frame drawn before that answer says "drawn before your answer" and shows the answer.
- A planned screen with no capture shows as "not captured" on the merge card. It does not fail the acceptance check.

## Phases

Each phase is built whole, then evaluated once, then fixed, then checked once more (owner rule).

1. **Approval card.** Contracts (`key`, `sketch`, `flow`, `empty_screen`, lenient validators), `wireflow.plan` and
   `dump`, `screens.yaml` in the draft commit, `for_card` on `GET /requests/{id}`, the Approve gate, the naming rule
   and lint, the stepper and in-place enlarge. Done means one real spec call drew readable sketches for a
   BUSINE10-shaped request, and the card shows them in the browser at 1280 and 1440.
2. **Notes and redraft.** `screen_notes` on decline, `send_back`, `redraft_spec`, `write_files` removal, owner sources
   per note, the diff-checked landing, the failure state.
3. **Merge comparison.** `for_card` built mode, `PlannedVsBuilt`, `AcceptanceOut.screens` keyed by id.

Phases 2 and 3 do not depend on each other. Phase 1 already writes the ids and the naming rule, so every build
approved after phase 1 produces captures phase 3 can join.

## Throughput checkpoint

- **Blocking first steps.** The phase 1 contracts, `wireflow.plan` with its tests, the schema budget test, and one
  real spec call on a BUSINE10-shaped request. The console waits for this because its payload type comes from it.
- **Independent workstreams.** After the contract commit, the backend (`specs.py`, `wireflow.py`, `app.py`,
  `worker.py`) and the console (`web/src/wireflow/`, two call sites in `decision.tsx`) touch disjoint files. The
  console builds against a fixture payload made from the real spec.
- **Shared mutable state.** Only the i18n catalogs are shared, and they are separate files on each side
  (`backend/justshipit/i18n`, `web/src/i18n`). Each delegate gets its own worktree.
- **Smallest safe decomposition.** Two delegates in phase 1, backend and console. Phase 2 is one owner, because
  `requests.py`, `specs.py`, `owner_trace.py` and the card change together. Phase 3 is one owner.

## Found while briefing phase 1

10. **A branch frame attaches to the step it branches from (from the opus design).** `design.md` puts every frame no
    row walks into `loose`, so an error frame lands at the bottom instead of beside its step. `for_card` derives
    `branches` for each step: the frames no row walks whose `name` equals that step's screen name. Only the frames
    left over stay in `loose`. `prompt_section` tells the writer to give every state of one screen the same name.
11. **The plan payload carries `dropped`.** It is the count `wireflow.plan` clipped, for graft 9's line.
