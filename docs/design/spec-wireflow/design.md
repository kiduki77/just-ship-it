# Spec wireflow: base design

This is the base the build follows, drawn by one of four parallel runners from the same grounding. `synthesis.md`
says what changed and what was grafted from the others. Where the two disagree, `synthesis.md` wins.


One sentence: **the planned screen is the existing `design_brief.states` item, grown a key and a flat sketch; the
server turns it into `screens.yaml` keyed by a screen id that is also the capture name, and one module
(`engineering/wireflow.py`) answers every question about planned screens for the spec card, the redraft and the merge
card.**

## Problem

The owner approves specs they have not read, because the spec card is text. They must see the screens before
approving, see the same screens beside the real captures before merging, and be able to say "not like this" on one
screen and watch that sentence change that screen. What makes the shape non-obvious, all verified in the repo:

- **The model cannot cite story ids.** `stories.number` assigns US1.. after the draft (`stories.py:27`). Anything the
  model writes about a story must nest in the story.
- **One bad field fails the whole draft.** `run_agent` validates `SpecDraftOut` with pydantic; a `ValidationError`
  costs a 900 s call and up to three retries with no feedback.
- **The schema travels on the command line.** `claude_cli.build_args` passes `--json-schema` as an argument.
  `SpecDraftOut` measures 10,895 chars today against a 32,767-char Windows limit; the prompt goes by stdin and is not
  limited. `strict_json_schema` inlines every `$ref`, so a union of component types is paid once per use.
- **`/inbox` returns all of `action_params`** for up to 200 rows, and `_ask_approval` drops any key not in `_SHOWN`.
  The spec text avoids both through `card_spec`, read at the card's pinned `commit_sha` on the detail fetch only, and
  Approve is off until it loads (`specUnread`).
- **Nothing joins a planned screen to a capture.** `scripts/ui-states.txt` names are whatever the engineer chose
  (`mapping-warning`, `report-empty` in J-10), the first screen is always `first-open`, and `capture_screens` deletes
  the folder on every acceptance run.
- **A declined spec is never redrafted.** The note becomes an owner source (`owner_answers` -> `owner_sources`), and
  `restate` shows it as missed. `write_spec` always makes a new `Spec` row with a new key. `registry.write_files`
  cannot remove a file.
- **`on_questions_answered` substitutes answers as text** into every spec file, with no model call.
- `decision.tsx` is already 1,038 lines; the console has no test runner; frontend types are hand-written.

## Usage (caller's view)

### What the spec writer emits (one call, same as today)

```jsonc
// inside SpecDraftOut.design_brief.states[]: the existing item, two new fields
{ "screen": "한 장 리포트", "state": "success",
  "shows": "'오늘 출고 312건 중 확인할 예외 5건'. 아래에 주소 빈칸 2건 ...",       // unchanged: the words, and the UX criterion
  "key": "report",
  "sketch": [
    {"kind": "nav",     "text": "출고 예외 리포트", "items": []},
    {"kind": "title",   "text": "오늘 출고 312건 중 확인할 예외 5건", "items": []},
    {"kind": "actions", "text": "", "items": ["내보내기", "새 파일"]},
    {"kind": "chips",   "text": "", "items": ["주소 빈칸 2건", "중복 주문 2건", "반품 1건"]},
    {"kind": "table",   "text": "주소 빈칸", "items": ["행 번호", "주문번호", "받는분", "내용", "처리"]}
  ] }

// inside SpecDraftOut.user_stories[] (P1 only; empty for the rest): two new fields
{ "title": "CSV를 올려 한 장 예외 리포트 받기", "priority": "P1", "...": "...",
  "flow": [ {"screen": "first-open", "do": "CSV를 올려요"},
            {"screen": "mapping",    "do": "열을 확인하고 '리포트 보기'를 눌러요"},
            {"screen": "report",     "do": ""} ],
  "empty_screen": "report-empty" }
```

### `specs.write_spec` (the only writer)

```python
told = stories.number(out.user_stories)
plan = wireflow.plan(out.design_brief, stories.ordered(out.user_stories))   # total: never raises, None with no screens
files = {..., **({f"{base}/{wireflow.FILE}": wireflow.dump(plan)} if plan else {}),
         **({f"{base}/{DESIGN_BRIEF}": design_brief_markdown(key, out.design_brief, plan)} if out.design_brief else {})}
handoff.workflow = HandoffWorkflow(screens=out.design_brief is not None, planned_screens=plan is not None, ...)
_raise_card(..., shown={..., "planned": len(plan.screens) if plan else 0})  # a number on /inbox, never the sketches
```

### The detail endpoint (`api/app.py get_request`), both cards, one call

```python
return {**requests.read_model(s, r, *reader), "business_id": ..., "spec": card_spec(s, r),
        "screens": wireflow.for_card(s, r)}   # None | {"mode": "plan", ...} | {"mode": "built", ...}
```

### The console

```tsx
// decision.tsx Body, spec card: between SpecSummary and OwnerTrace
{r.screens?.mode === "plan" &&
  <Wireflow plan={r.screens} notes={d?.typed.screens ?? null} onNote={d?.setScreenNote} />}

// decision.tsx TryIt, merge card: replaces the thumbnails it pairs, keeps the rest
{r.screens?.mode === "built" && <PlannedVsBuilt built={r.screens} />}

// useDecision: Approve is off while the screens the card promises are not on screen, or while a note is typed
const screensMissing = (r.planned ?? 0) > 0 && !r.screens;          // same rule as specUnread
body: JSON.stringify({ decision, note, revision: r.revision, screen_notes: toList(typed.screens) })
```

### The engineer and the CPO (prompt text, worker.py)

```
scripts/ui-states.txt: one line per screen in .ship/spec/screens.yaml, `<id> /path`, the id exactly as the file
gives it (the screen with id first-open has no line). States you add beyond the plan come after, with your own names.
screens.yaml says which screens exist, what is on each and in what order. It is a sketch of content, never of look:
ui-standard.md and DESIGN.md decide the look, and design-brief.md's words win where a sketch's words differ.
```

### The redraft (Phase 2), from `services/requests.respond`

```python
if req.action_kind == "dev.spec_approve" and req.status == REJECTED and resp.screen_notes:
    specs.send_back(s, req, resp.screen_notes)      # notes -> owner_answers, card closed, job queued, in this transaction
# queue: "redraft_spec" -> specs.redraft_spec(spec_id, request_id): same Spec row, new commit, new card
```

## Shape

### 1. One list of planned screens, not two

`ScreenStateOut` already is "one screen in one state", and one state is exactly what one capture shows. It gains
`key` and `sketch`. There is no second `screens` list beside `design_brief.states`, so the UX criteria
(`ux_criteria`), design-brief.md, the wireflow and the captures all count the same items (per single source of truth;
it removes the hand-synced-list red flag before it exists). `shows` stays: it is the sentence acceptance criteria are
made from, and it is the frame's fallback when a sketch is empty.

### 2. Screen id: the model proposes, the server decides, everything joins on it

- The model writes `key`, a short ASCII name, and is told to key the screen a user meets on opening the app
  `first-open` (and to use no such key when this spec does not change that screen: existing products are partial by
  design).
- `wireflow.plan` normalises (`[a-z0-9-]`, at most 32 chars, a trailing `-1440`/`-1280` removed), falls back to
  `s{n}` for an empty or non-ASCII key, de-duplicates with `-2`, and clips the list at `MAX_SCREENS` (12, the worker's
  `_MAX_STATES`, which moves here so one constant bounds both). The result is the **screen id**.
- Why not ordinals (S1, S2) like US ids: a redraft must keep the id of a screen the owner noted. A slug the model
  echoes survives reordering, insertion and removal; an ordinal survives none. Normalisation is deterministic, so the
  same key gives the same id in every draft with no map to store.
- The id is the join key everywhere, with no translation table:

| where | how the id appears |
|---|---|
| `screens.yaml` | key of the `screens` mapping |
| `flow` / `empty_screen` in a story | resolved to ids by `plan`; dangling ones dropped |
| design-brief.md table | new first column |
| `scripts/ui-states.txt` | the line's name (engineer is told; `first-open` is the worker's own shot) |
| capture file | `{id}-1440.png`; `wireflow.shot_of` is the only code that knows the suffix (`try_it.SHOT_AT`) |
| acceptance | `AcceptanceOut.screens[].screen_id`, server-filtered to known ids like `StoryCheckOut` |
| owner note | `ScreenNote.screen`; owner source ref `REQ-41@report` |
| owner_trace | `OwnerTraceOut.screens` -> row `"screens": [ids]` |

### 3. Vocabulary: eleven kinds, one block shape, no nesting

One block type, three fields, a flat top-to-bottom list per screen:

```
{kind, text, items[]}
```

| kind | text | items | drawn as |
|---|---|---|---|
| nav | product name | tabs | top bar |
| title | the screen's headline | - | large line |
| text | a sentence | - | body line, clamps at 2 lines |
| notice | a message (error, warning, empty) | - | band with a mark |
| actions | - | button labels, first is the main one | button row |
| field | label | options (none: a text input) | label + input, or label + choices |
| upload | the prompt | - | dashed drop area |
| list | heading | rows (up to 4 drawn) | lines |
| table | caption | column names | header row + 3 placeholder rows |
| chips | - | labels / counts | pill row |
| media | caption | - | crossed box (image, chart, map) |

How small can it be: six kinds (title, text, notice, actions, field, list) draw any form-and-result product, but J-10's
real report (`report-1440.png`: bar, headline, two buttons, three count chips, tables) reads as "some list" without
nav, chips and table, and a data tool's first screen is its upload area. Media is the one escape hatch for anything
drawn. Nothing below eleven kept BUSINE10 recognisable; nothing above it was needed for it.

Deliberately absent: nesting, columns, tone or variant fields, disabled state, icons. A uniform block costs about 330
schema chars once; a discriminated union of eleven shapes costs ten times that and gives the model eleven ways to be
invalid. A notice's tone comes from the screen's `state`. The kinds live in one `BlockKind` enum with a one-line
meaning each (`VOCAB`); the prompt section and the engineer's note are generated from it, and the console's renderer is
a `Record<BlockKind, ...>` so a missing kind fails `tsc`. A Python test reads the union out of
`web/src/wireflow/types.ts` and compares it with the enum (the `test_i18n` precedent for reading TS from pytest).

### 4. Lenient at the boundary, total inside

- The JSON schema still shows the model the enum and the shape. The **Python** type is forgiving: `sketch`, `flow`
  and `key` carry a `BeforeValidator` that coerces instead of raising (unknown kind -> `text`, non-string -> dropped,
  text clipped to 120 chars, items to 8, blocks to 14, a non-list -> `[]`). All three default to empty, so every
  existing test and old payload validates. No value in those fields can raise `ValidationError`; a fuzz test pins
  that (per boundary-discipline).
- `wireflow.plan` is a pure, total function: duplicate keys, dangling flow steps, a story with no resolvable step, a
  `[NEEDS CLARIFICATION Qn]` marker inside sketch text (stripped; the question id is kept in `asks`) all produce a
  smaller plan, never an error. Worst case is a plan whose frames are text-only (`shows`), which is today's card with
  boxes around it.
- **Budget, measured**: the new fields add about 1,780 chars to `SpecDraftOut` (10,895 -> about 12,700) and Phase 3
  adds about 440 to `AcceptanceOut` (6,583). The vocabulary's meanings go in `SPEC_ASK` (stdin), not in field
  descriptions. A test fails when `len(json.dumps(strict_json_schema(SpecDraftOut)))` passes 16,000, half the limit,
  so the next field added by someone who never heard of the limit fails a test instead of a Windows launch.

### 5. Stored in the registry, served on the detail fetch, never on `/inbox`

- `registry/projects/<slug>/specs/<KEY>/screens.yaml`, written by `write_spec` in the draft commit:

```yaml
screens:                       # mapping keyed by screen id, in the brief's order
  first-open: {name: 첫 화면(파일 올리기), state: empty, shows: "...", blocks: [...], asks: []}
  mapping:    {...}
  report:     {...}
  report-empty: {...}
rows:                          # one per story that has a flow, in story order
  - story: US1
    steps: [{screen: first-open, do: CSV를 올려요}, {screen: mapping, do: "..."}, {screen: report, do: ""}]
    then: 한 화면에 주소 빈칸 2건, ...          # the first scenario's Then
    empty: {screen: report-empty, then: "'오늘 출고 N건에서 확인할 예외가 없어요'라고 나와요"}
```

  A mapping keyed by id serves the dominant access (row step -> screen, capture -> screen, note -> screen) with no
  index built anywhere. The worker already copies the whole spec dir to `.ship/spec/`, so the engineer, reviewer and
  CPO get it with no worker change.
- `wireflow.for_card(s, req)` reads it at the card's pinned commit, exactly as `card_spec` reads prd.md. The owner
  therefore sees the sketches of the commit that approving pins; an old card keeps showing its own commit forever.
- **Nothing approved unseen**, three locks: (a) the card's `action_params` carries only `planned: n`; the console
  keeps Approve off while `planned > 0` and `screens` has not arrived, beside `specUnread`; (b) the sketches cannot
  change under a card, because a redraft is a new commit on a **new card** (below), and `on_spec_approved` already
  refuses a card whose commit is not the spec's draft commit; (c) a screen whose sketch was drawn before the owner
  answered a question says so: `plan` strips the marker and records `asks: [Q1]`, `for_card` joins the answer from
  questions.yaml at that commit, and the frame shows "drawn before your answer" with the answer (the existing
  provenance mark, shape + word). The text path keeps working unchanged because screens.yaml then holds no marker for
  `written_back` to substitute.
- Old specs and screenless products have no file: `for_card` returns None, `planned` is 0, the card is today's card.

### 6. Console contract at ~450 px (component and data, not pixels)

New folder `web/src/wireflow/` (decision.tsx gains two call sites and one type import, nothing else):

- `Wireflow({plan, notes, onNote})`: one `<section>`; per row an `<ol>` of steps; then the screens no row walks
  ("other planned screens"); then removed screens that carry a note.
- `Frame({screen, slot})`: the only component that draws a sketch. Used by the spec card and by `PlannedVsBuilt`.
- `Block`: `Record<BlockKind, (b: Block) => ReactNode>`.

Rules the implementation and the prototype both keep:

1. **Layout is a list, not a canvas.** No React Flow, no pan or zoom, no SVG overlay, no horizontal scroll, no
   measuring. A row is an ordered list; a step is `Frame` + connector (arrow + `do`). Whether steps run down the
   panel (one frame per line, default: a frame at full panel width reads as the product) or wrap two-up is one
   attribute on the row (`data-flow`), so the undecided layout is a CSS decision. Likewise `data-sketch="neat|rough"`
   on the root carries the undecided fidelity: same DOM, two skins.
2. **A frame's height comes from its blocks, never from its strings.** Every kind has a bounded drawn height (text
   clamps at two lines, list at four rows, table at header + three placeholder rows, at most 14 blocks). Labels
   truncate with an ellipsis; the full string is the element's accessible name and its tooltip. Nothing is sized to
   string length in either language.
3. **Block text is data**: rendered as written, in the product's language. Every other word (row heading parts,
   "if nothing yet", "drawn before your answer", note placeholder, change marks) comes from `t()`.
4. **Grey only.** Neutral tokens (`--surface-2`, `--border`, `--border-strong`, `--text-2`, `--text-3`); state colour
   appears only on marks that state something (note landed, differs from plan). The frame is the one bordered box:
   the row and the section have none, and blocks inside a frame use fills and rules, not borders.
5. **A frame with no blocks draws `shows`** as text inside the same frame. There is no second "text only" component.
6. **Accessible without the picture**: a frame is a `<figure>` whose caption is `name · state` and whose description
   is `shows`; the sketch itself is `aria-hidden`. In plan mode on a pending card each frame is followed by a
   "note on this screen" button that opens a textarea in place (no modal, no panel in the panel).
7. **Order of information in a row**: the story's sentence (already on the card as `stories[].says`, matched by
   `story` id), steps, the Then under the last frame, the empty frame attached after it with its own Then.

### 7. Note on a screen -> redraft -> the note lands on that screen (Phase 2)

Data path, each arrow one owner:

1. **Console**: notes typed per screen id live in the existing per-card, per-revision draft (`useDraft` `Typed` gains
   `screens: Record<id, string>`). While any exists, Approve is off and the decline button reads as sending the notes
   back. `HumanResponse` gains `screen_notes: list[ScreenNote]`.
2. **`requests.respond`** (decline branch) calls `specs.send_back(s, req, notes)`. In that transaction it: validates
   ids against the card's commit (a note on an unknown id is appended to the general note, never dropped: owner words
   are not lost); adds `"screens": [{screen, name, note}]` to the `owner_answers` entry the function already writes;
   sets `spec.status = DRAFT` and clears `spec.approval_request_id`, so `matters.current_spec` keeps this spec live
   and a new ask cannot draft a rival meanwhile; enqueues `redraft_spec` with key `redraft_spec:{request_id}`.
3. **`owner_trace.owner_sources`** emits one source per screen note: ref `REQ-41@report`, `where` "the owner's note
   on the planned screen '한 장 리포트 (success)'", `screen="report"`. This is the existing mechanism that makes owner
   words binding; nothing parallel is built. If the redraft never lands, `restate` already shows these as missed.
4. **`specs.redraft_spec`** builds the same prompt as `write_spec` (shared `_brief`), plus
   `wireflow.redraft_section(prev, notes)`: the previous screens with their ids and blocks, each note under its
   screen, and "keep every key; change the screens the notes name". It commits to the **same spec key and path**
   (shared `_package`), moves `spec.draft_commit_sha`, and raises a **new card** with
   `redraft_of: {request, commit}`; the declined card gets `superseded_by`, the `reask_approval` precedent. Run twice,
   it finds `spec.draft_commit_sha != card commit` and does nothing (per make-operations-idempotent).
5. **`owner_trace.settle(..., changed=ids)`**: `OwnerTraceOut` gains `screens: list[str]`. A row's `screens` is what
   the writer cited, intersected with `wireflow.changed(prev, new)` (new, changed blocks, or removed). For a
   screen-note source, "included" holds with a requirement **or** a screen that really changed; a claim on a screen
   whose blocks are identical is `missed` with a reason. The server checks the claim against the diff instead of
   trusting it.
6. **`wireflow.for_card`** joins at read time: each screen gets `change` (`new | changed | null` against
   `redraft_of.commit`) and `landed` (the card's current trace rows whose `screens` hold its id); screens that are
   gone come back as `removed` with their rows. Derived on every read from the two commits and the card's trace, so
   `restate` rewriting the trace can never leave the frames stale (per separate-before-serializing: trace on the card,
   sketches in the registry, merged at the read boundary).
7. **Console**: a frame with `landed` rows shows each as a line under it: the owner's sentence, the disposition word
   and mark the trace section already uses, and "changed" on the frame. `OwnerTrace` rows with `screens` name the
   screen. A note that was dropped shows on its screen as dropped with the reason, which is the point.

### 8. Planned vs built (Phase 3)

- `wireflow.for_card` on a `dev.merge` card returns `mode: "built"`: the plan at `spec.approved_commit_sha`, each
  screen with `built: {shot | null, verdict, differs}`, plus `extra` (captures no planned screen claims). Computed on
  read from `job.result.screens.files` and `job.result.acceptance.screens`, so a fix round's fresh captures need no
  bookkeeping.
- The CPO is given the planned ids with their capture files and returns `AcceptanceOut.screens[]` keyed by id.
  `wireflow.missing(plan, files)` is computed by the server and stated in the prompt, so "planned but not captured"
  stops depending on the CPO's free-text `unseen`.
- `PlannedVsBuilt`: per screen, in flow order, the `Frame` on the left and the capture on the right in the existing
  two-column `.dp-shots` grid, one line under the pair (verdict word + `differs`). `TryIt` then lists only `extra` as
  plain thumbnails. `try_it.card` is unchanged and still feeds the list.

### Interface depth

Callers learn four functions: `plan`, `dump`, `for_card`, `redraft_section` (plus `changed` for `settle`). Hidden
behind them: id normalisation and collision rules, reference resolution, marker stripping, the file format, the
commit to read, the capture-name rule, the diff, the trace join, the answers join, both card modes. The console learns
one payload type and two components. `specs.py`, `requests.py`, `worker.py`, `app.py` and `decision.tsx` each gain a
call, not a concept.

### What it deliberately does not do

No free HTML, no images, no model call for drawing, no layout hints from the model, no sketches for P2/P3 rows, no
editing of a sketch by the owner (only words), no redraw after question answers (flagged per screen instead), no
note-on-a-capture on the merge card (the existing fix round takes a note already).

## Synthesis decision

*(filled by arena)*

## Tradeoffs accepted

- We accept **`shows` and `sketch` describing the same screen twice** in exchange for a draft that cannot fail on a
  sketch and a frame that always has something to draw. `shows` wins on words.
- We accept **a flat, variant-free vocabulary** (no columns, no disabled button, no tone) in exchange for a schema
  cost of ~330 chars and a renderer anyone can finish in a day. A sketch says what is on a screen and in what order,
  not how it looks.
- We accept **model-authored keys** (normalised by the server) in exchange for ids that survive a redraft and capture
  names a person can read. The cost is a prompt rule ("keep each key") the server cannot fully enforce: a renamed key
  shows as one screen removed and one new, which is at least visible.
- We accept **a new card per redraft** instead of revising the card in place, in exchange for a card whose commit
  never moves. The owner sees one more closed card in history.
- We accept **the same spec key across redrafts** (git history holds earlier drafts) in exchange for not teaching
  every `product_specs` reader about abandoned keys. It needs `registry.write_files` to learn to remove stale files.
- We accept **sketches that predate question answers**, marked per screen, in exchange for keeping
  `on_questions_answered` free of model calls.
- We accept **shipping the ui-states id rule and `HandoffWorkflow.planned_screens` in Phase 1**, before anything
  compares, in exchange for every build from a Phase 1 spec being joinable when Phase 3 arrives.
- We accept **a Python test that reads a TS union** in exchange for one vocabulary list that cannot drift silently.

## Alternatives considered

1. **A separate top-level `wireflow: {screens, flows}` on `SpecDraftOut`, stories referenced by title or position.**
   Interface looks cleaner (one field), but it creates a second list of screens beside `design_brief.states` that
   every reader must reconcile, and position references break the moment `stories.number` sorts by priority. It hides
   nothing and exposes a sync problem. Rejected.
2. **Sketches nested inside each story's scenarios (a full frame per step).** No ids for the model to manage, but the
   same screen is drawn once per story that visits it, drafts drift apart, there is nothing to join a capture to, and
   output tokens multiply. Rejected: ids are the feature.
3. **Server-assigned ordinal ids (S1..) after the draft.** Simplest to implement and impossible to get wrong in one
   draft; wrong across drafts, where an inserted screen renumbers the one the owner noted. Would need a stored
   old-to-new map and a matching heuristic. Rejected for the redraft requirement.
4. **Typed components (discriminated union per kind, nested containers), the full json-render pattern.** Richer
   pictures; ten times the schema, recursion `strict_json_schema` cannot inline, and eleven validation surfaces. The
   richness is exposed to the model and the renderer, the two places least able to pay for it. Rejected.
5. **A second, small model call that draws the wireflow from the finished spec.** Decouples a bad sketch from the
   draft completely and could redraw after answers. Costs a call per draft and per redraft, a second failure mode
   with its own retry policy, and a sketch that can disagree with the spec it was drawn from. Lenient parsing buys
   the same isolation for free. Rejected; it is the fallback if one-call sketch quality proves poor in the prototype.
6. **Sketches in `action_params` behind `_SHOWN`.** No new read path, but ~10 KB per spec card on every `/inbox`
   call, and `restate`/`reask_approval` copying it around. Rejected on the grounding alone.
7. **Revise the card in place on redraft (revision + 1, same request id).** One card in history, but the card's
   `commit_sha` moves under it, the declined response has to be un-declined, and a panel open on the old revision
   holds sketches of a commit the card no longer names. The revision check would catch the approve, yet the old
   card's record of what was declined is lost. Rejected.
8. **React Flow for rows.** Bundled already, but pan/zoom inside a scrolling panel fights scroll and focus, and a
   wireflow row is a list. Rejected.

## Implementation reconciliation

*(empty until Phase D)*

## Open questions and risks

- Should a decline with a general note and **no** screen note also redraft? The path is the same
  (`send_back` with an empty list); today it changes nothing on the spec. Recommended yes, as a follow-up decision.
- With screen notes typed, is "Approve is off until you send or clear them" right, or should approving carry them to
  the engineer as clarifications (`add_clarification`) without a redraw?
- After question answers, is the per-screen "drawn before your answer" mark enough, or should answers that touch a
  screen trigger the Phase 2 redraft automatically?
- Should a planned screen with no capture **block** acceptance (a gate), or only be stated on the merge card?
- Rows are drawn top-to-bottom by default. Does the prototype confirm that over two-up? (CSS-only either way.)
- Risk: `registry.write_files` cannot delete. A redraft that drops questions.yaml, design-brief.md or screens.yaml
  must remove them, or the new commit serves stale files. Needs an additive `remove=` parameter.
- Risk: a new queue job kind (`redraft_spec`) must be registered wherever `write_spec` is (handlers, console job
  label, the cycle-stage list in migration `e6c3a9f1b204` suggests stage mapping); verify before Phase 2.
- Risk: `matters.py:209` already reads an `action_params["revises"]` key for another purpose; the redraft link is
  named `redraft_of` to stay clear of it.
- Risk: one-call sketch quality at sonnet is unproven; the prototype should run the real BUSINE10 prompt before
  Phase 1 is called done. Alternative 5 is the escape.
- Risk: the engineer ignoring the id rule. `wireflow.missing` makes it visible at acceptance; nothing prevents it.

## Next implementation step

Add `BlockKind`/`BlockOut`/`key`/`sketch`/`flow` with their lenient validators to `contracts/handoff.py`, then
`wireflow.plan` and `dump` as pure functions with tests against BUSINE10's real states and stories, plus the
schema-budget test.

---

# Type sketch

## `backend/justshipit/contracts/handoff.py` (additions)

```python
class BlockKind(StrEnum):
    """The whole vocabulary of a planned screen's sketch. One list: the schema enum, the prompt's table (VOCAB in
    engineering/wireflow.py) and the console's renderer map all follow it (tests/test_wireflow.py reads the TS union)."""
    NAV = "nav"; TITLE = "title"; TEXT = "text"; NOTICE = "notice"; ACTIONS = "actions"; FIELD = "field"
    UPLOAD = "upload"; LIST = "list"; TABLE = "table"; CHIPS = "chips"; MEDIA = "media"


class BlockOut(BaseModel):
    kind: BlockKind
    text: str = ""
    items: list[str] = []


def _blocks(value: object) -> list[dict]:
    """Whatever the model wrote, as blocks: never raises. An unknown kind is `text`, a non-string is dropped, text is
    clipped to 120 characters, items to 8, the list to 14. A sketch cannot fail a spec draft."""
    raise NotImplementedError


def _steps(value: object) -> list[dict]:
    """Flow steps with a string `screen`; anything else dropped. Never raises."""
    raise NotImplementedError


def _text(value: object) -> str:
    """A string, or an empty one. Never raises."""
    raise NotImplementedError


Sketch = Annotated[list[BlockOut], BeforeValidator(_blocks)]
Lenient = Annotated[str, BeforeValidator(_text)]


class ScreenStateOut(BaseModel):          # existing: screen, state, shows
    screen: str
    state: str
    shows: str
    key: Lenient = Field(default="", description="A short lowercase ASCII name for this screen in this state, unique "
                         "in the list (report-empty); first-open for what a user sees on opening the app. Keep the "
                         "key a screen had in an earlier draft")
    sketch: Sketch = Field(default=[], description="The screen in this state as blocks, top to bottom, from the "
                           "vocabulary the prompt gives, with the screen's exact words")


class FlowStepOut(BaseModel):
    screen: Lenient = Field(description="The key of a screen in design_brief.states")
    do: Lenient = Field(default="", description="What the user does there to reach the next screen, in a few words; "
                        "an empty string on the last")


class UserStoryOut(BaseModel):            # existing fields unchanged
    flow: Annotated[list[FlowStepOut], BeforeValidator(_steps)] = Field(
        default=[], description="For a P1 story: the screens its first scenario walks through, in order. An empty "
                                "list otherwise")
    empty_screen: Lenient = Field(default="", description="The key of the screen its empty_case ends on, or an "
                                                          "empty string")


class OwnerTraceOut(BaseModel):           # Phase 2; existing fields unchanged
    screens: Annotated[list[str], BeforeValidator(...)] = Field(
        default=[], description="The keys of the screens that changed for it; an empty list when none did")


class ScreenCheckOut(BaseModel):          # Phase 3, in AcceptanceOut.screens (default [])
    screen_id: str = Field(description="The screen's id as screens.yaml gives it")
    built: Literal["yes", "partly", "no", "unseen"]
    differs: str = Field(description="Where the capture differs from the planned screen, in one sentence in the "
                                     "owner's language; an empty string when it does not")


class HandoffWorkflow(BaseModel):         # existing
    planned_screens: bool = False         # the spec has screens.yaml: ui-states.txt names are its ids
```

## `backend/justshipit/contracts/requests.py` (Phase 2)

```python
class ScreenNote(BaseModel):
    screen: str                           # a screen id of the card's commit
    note: OneLine(400)


class HumanResponse(BaseModel):           # existing
    screen_notes: list[ScreenNote] = []   # spec cards only, with a decline: the owner's words on single screens
```

## `backend/justshipit/engineering/wireflow.py` (new; the owner of planned screens)

```python
"""A spec's planned screens: what the owner sees before approving, what the engineer names captures after, and what
the merge card sets beside the real screens. `screens.yaml` beside the spec holds them, keyed by screen id. The id
is the model's key as the server normalised it, and it is the one name a screen has: in a story's flow, in
scripts/ui-states.txt, in a capture's file name, in the acceptance check, in an owner's note and in the trace."""

FILE = "screens.yaml"
FIRST = "first-open"      # the screen the capture always takes at `/`; it has no ui-states line
MAX_SCREENS = 12          # the capture's limit (worker imports it): a plan never names more than can be shot

VOCAB: dict[BlockKind, str]   # one line per kind: what text and items mean. prompt_section() is built from it


class PlannedScreen(BaseModel):
    name: str                 # the screen's name, product language
    state: str
    shows: str
    blocks: list[BlockOut]    # empty: the frame draws `shows`
    asks: list[str] = []      # question ids (Q1) whose marker sat in this screen's words when it was drawn


class Step(BaseModel):
    screen: str               # always a key of Wireflow.screens (plan and read both enforce it)
    do: str


class Branch(BaseModel):
    screen: str | None        # None: the empty case names no planned screen; the Then still shows
    then: str


class FlowRow(BaseModel):
    story: str                # US1, as stories.number gave it
    steps: list[Step]         # at least one
    then: str
    empty: Branch | None


class Wireflow(BaseModel):
    screens: dict[str, PlannedScreen]     # insertion-ordered, keyed by screen id
    rows: list[FlowRow]

    def loose(self) -> list[str]:
        """Ids no row walks, in order."""
        raise NotImplementedError


def prompt_section() -> str:
    """The part of SPEC_ASK that teaches the vocabulary, the key rule and the flow, generated from VOCAB."""
    raise NotImplementedError


def plan(brief: DesignBriefOut | None, stories: list[tuple[str, UserStoryOut]]) -> Wireflow | None:
    """The draft's screens as the server keeps them. Pure and total: it never raises.
    None when the product has no screen. Otherwise:
    - id: the key lowercased to [a-z0-9-], at most 32 characters, a trailing -1440/-1280 removed; `s{n}` when that
      leaves nothing; a repeat gets -2, -3; the list stops at MAX_SCREENS (+ FIRST)
    - a [NEEDS CLARIFICATION Qn] marker in a block's words is removed and Qn kept in `asks`
    - a step whose key matches no screen is dropped; two steps in a row on one screen become one; a story left with
      no step has no row
    - `then` is the story's first scenario's Then; `empty` its empty_case's Then on `empty_screen` when that resolves"""
    raise NotImplementedError


def dump(w: Wireflow) -> str:
    raise NotImplementedError


def at(registry_path: str, commit: str) -> Wireflow | None:
    """The plan as that commit holds it; None for a spec without one or a file that no longer parses (logged)."""
    raise NotImplementedError


def changed(before: Wireflow | None, after: Wireflow | None) -> dict[str, Literal["new", "changed", "removed"]]:
    """Which screens differ between two drafts, by id. Blocks, name, state or shows differing is `changed`."""
    raise NotImplementedError


def redraft_section(prev: Wireflow, notes: list[dict]) -> str:
    """For the redraft prompt: the last draft's screens with their keys and blocks, each owner note under the screen
    it is about with its source id (REQ-41@report), and the rule: keep every key, change what the notes name, cite
    the screens you changed in owner_trace.screens."""
    raise NotImplementedError


# ---- capture join (Phase 3; the only code that maps a file name to a screen) ----

def shot_of(w: Wireflow, files: list[str]) -> dict[str, str]:
    """screen id -> capture file, for the files of one acceptance run ({id}{try_it.SHOT_AT}.png)."""
    raise NotImplementedError


def missing(w: Wireflow, files: list[str]) -> list[str]:
    """Planned ids with no capture, in plan order: stated to the CPO and on the merge card."""
    raise NotImplementedError


# ---- the one read the console makes ----

def for_card(s: Session, req: AgentRequest) -> dict | None:
    """What the detail fetch adds for a card with planned screens, read at the commit the card pins. None otherwise.

    dev.spec_approve -> {"mode": "plan", "screens": {id: {..., "asks": [{"question", "answer" | None}],
                         "change": "new" | "changed" | None, "landed": [trace row]}}, "rows": [...], "loose": [ids],
                         "removed": [{"id", "name", "state", "landed": [trace row]}]}
        change/removed: against action_params["redraft_of"]["commit"], when the card is a redraft
        landed: the card's current owner_trace rows whose "screens" hold the id (joined here, stored nowhere)
        asks: joined with questions.yaml at the same commit
    dev.merge -> {"mode": "built", "screens": {id: {..., "built": {"shot": {"name", "url"} | None,
                  "verdict": "yes" | "partly" | "no" | "unseen" | None, "differs": str}}}, "rows": [...],
                  "loose": [ids], "extra": [{"name", "url"}]}
        plan: spec.approved_commit_sha; shots: job.result["screens"]["files"]; verdicts: job.result["acceptance"]"""
    raise NotImplementedError
```

## `backend/justshipit/engineering/stories.py`

```python
def ordered(stories: list[UserStoryOut]) -> list[tuple[str, UserStoryOut]]:
    """The stories with the ids the server gives them (US1.. by priority). `number` and wireflow.plan both read it,
    so the numbering rule has one owner."""
    raise NotImplementedError
```

## `backend/justshipit/engineering/specs.py`

```python
_CARRIED = (*_SHOWN, "planned", "redraft_of")     # replaces _SHOWN in _ask_approval / reask_approval


@dataclass(frozen=True)
class _Brief:
    """Everything a draft or a redraft is written from, gathered in one transaction."""
    prompt: str
    owner: list[OwnerSource]
    sources: dict[str, str]
    code: tuple[Path, str] | None
    product: ExistingProduct | None
    need: dict


def _brief(s, project_id: int, request_id: int, lang: Lang, *, extra: str = "") -> _Brief:
    """write_spec's prompt, lifted out unchanged; `extra` is the redraft's section, placed before SPEC_ASK."""
    raise NotImplementedError


def _package(base: str, key: str, out: SpecDraftOut, b: _Brief, lang: Lang, *,
             prev: Wireflow | None = None) -> _Package:
    """The spec's files, trace, numbered stories, plan, questions and card text from one draft. Pure but for reading
    the constitution. `prev` is the plan a redraft answers: its diff is what owner_trace.settle checks screen claims
    against."""
    raise NotImplementedError


def write_spec(project_id: int, request_id: int) -> int: ...          # now: _brief -> _draft -> _package -> commit -> card


def send_back(s, req: AgentRequest, notes: list[ScreenNote]) -> None:
    """The owner declined a spec card with words on single screens. In the caller's transaction: add the notes to the
    owner_answers entry respond() wrote ({"screens": [{"screen", "name", "note"}]}; a note on an id the card's commit
    lacks joins the general note), set the spec DRAFT with no approval card so it stays the product's live spec, and
    queue redraft_spec (idempotency key redraft_spec:{req.id})."""
    raise NotImplementedError


def redraft_spec(spec_id: int, request_id: int) -> int:
    """The same spec written again from everything the first draft had plus the owner's notes. Same key and path, a
    new commit, a new approval card with redraft_of={"request", "commit"}; the declined card says superseded_by.
    A spec whose draft commit is no longer the declined card's was already redrafted: nothing happens.
    When every attempt fails, the old commit is asked again (reask_approval) and restate shows the notes as missed."""
    raise NotImplementedError
```

## `backend/justshipit/engineering/owner_trace.py` (Phase 2)

```python
@dataclass(frozen=True)
class OwnerSource:            # existing
    screen: str = ""          # the planned screen this note is about (ref REQ-41@report); "" for every other source


def settle(sources, trace, requirement_ids, lang, *, changed: frozenset[str] = frozenset()) -> list[dict]:
    """...existing rules. Each row gains "screens": the ids the writer cited that are in `changed`. A source with a
    `screen` is included by a requirement or by a screen that changed; a claim on a screen that did not change is
    missed (build.trace.screen_unchanged)."""
```

## `web/src/wireflow/types.ts`

```ts
// The server's BlockKind (contracts/handoff.py). tests/test_wireflow.py fails when the two lists differ.
export type BlockKind = "nav" | "title" | "text" | "notice" | "actions" | "field" | "upload" | "list" | "table"
  | "chips" | "media";
export interface Block { kind: BlockKind; text: string; items: string[] }

interface ScreenBase { name: string; state: string; shows: string; blocks: Block[] }
export interface Asked { question: string; answer: string | null }
export interface PlanScreen extends ScreenBase { asks: Asked[]; change: "new" | "changed" | null; landed: TraceRow[] }
export interface Shot { name: string; url: string }
export interface BuiltScreen extends ScreenBase {
  built: { shot: Shot | null; verdict: "yes" | "partly" | "no" | "unseen" | null; differs: string };
}
export interface Row {
  story: string;
  steps: { screen: string; do: string }[];
  then: string;
  empty: { screen: string | null; then: string } | null;
}
interface Flow<S> { screens: Record<string, S>; rows: Row[]; loose: string[] }
export interface Plan extends Flow<PlanScreen> {
  mode: "plan";
  removed: { id: string; name: string; state: string; landed: TraceRow[] }[];
}
export interface Built extends Flow<BuiltScreen> { mode: "built"; extra: Shot[] }
export type CardScreens = Plan | Built;       // Row.screens?: CardScreens | null; Row.planned?: number
```

## `web/src/wireflow/Wireflow.tsx`, `blocks.tsx`, `PlannedVsBuilt.tsx`

```tsx
/** The planned screens of a spec card: a row per story that has a flow, then the screens no row walks. Notes are
 * the caller's (the card's draft); null on a card that is decided. */
export function Wireflow(p: {
  plan: Plan;
  stories: { id: string; says: string }[];
  notes: Record<string, string> | null;
  onNote: ((screen: string, note: string) => void) | null;
}): JSX.Element { throw new Error("not implemented"); }

/** One planned screen, drawn. The only component that draws a sketch; blocks top to bottom, `shows` when none. */
export function Frame(p: { screen: { name: string; state: string; shows: string; blocks: Block[] } }): JSX.Element {
  throw new Error("not implemented");
}

/** Every kind has a renderer or the build fails. */
export const BLOCKS: Record<BlockKind, (b: Block) => ReactNode> = { /* not implemented */ } as never;

/** The plan beside what was built, a pair per planned screen in flow order; captures nobody planned stay in TryIt. */
export function PlannedVsBuilt(p: { built: Built }): JSX.Element { throw new Error("not implemented"); }
```

# Module map

| file | change | phase |
|---|---|---|
| `backend/justshipit/contracts/handoff.py` | `BlockKind`, `BlockOut`, lenient validators, `key`/`sketch` on `ScreenStateOut`, `flow`/`empty_screen` on `UserStoryOut`, `HandoffWorkflow.planned_screens`; `DesignBriefOut` docstring: the sketch is content, not look | 1 |
| `backend/justshipit/engineering/wireflow.py` | **new**: `VOCAB`, `plan`, `dump`, `at`, `for_card` (plan mode), `prompt_section` | 1 |
| `backend/justshipit/engineering/stories.py` | `ordered`; `number` built on it | 1 |
| `backend/justshipit/engineering/specs.py` | `SPEC_ASK` + `wireflow.prompt_section()`; write `screens.yaml`; id column in design-brief.md; `planned` on the card; `_CARRIED` | 1 |
| `backend/justshipit/engineering/worker.py` | `_ui_build`/`_ui_plan` text: ids as ui-states names, precedence; `_MAX_STATES = wireflow.MAX_SCREENS` | 1 |
| `backend/justshipit/api/app.py` | `"screens": wireflow.for_card(s, r)` in `get_request` | 1 |
| `web/src/wireflow/{types.ts,Wireflow.tsx,blocks.tsx,wireflow.css}` | **new**: read-only wireflow | 1 |
| `web/src/components/decision.tsx` | `<Wireflow>` in `Body`; `screensMissing` beside `specUnread` | 1 |
| `web/src/i18n` | chrome strings, en + ko | 1 |
| `tests/test_wireflow.py` | fuzz: no sketch fails a draft; `plan` on BUSINE10; schema budget <= 16,000; TS union == enum | 1 |
| `backend/justshipit/contracts/requests.py` | `ScreenNote`, `HumanResponse.screen_notes` | 2 |
| `backend/justshipit/services/requests.py` | decline branch calls `specs.send_back` | 2 |
| `backend/justshipit/services/registry.py` | `write_files(..., remove=())` | 2 |
| `backend/justshipit/engineering/specs.py` | `_brief`/`_package` lifted out of `write_spec`; `send_back`; `redraft_spec`; job registration | 2 |
| `backend/justshipit/engineering/owner_trace.py` | per-screen sources; `OwnerSource.screen`; `settle(changed=)`; row `screens` | 2 |
| `backend/justshipit/engineering/wireflow.py` | `changed`, `redraft_section`; `for_card` adds `change`, `landed`, `removed` | 2 |
| `web/src/wireflow/Wireflow.tsx`, `decision.tsx` | note per frame, landed lines, change marks; `Typed.screens`; decline label; trace rows name screens | 2 |
| `backend/justshipit/contracts/handoff.py` | `ScreenCheckOut`, `AcceptanceOut.screens` | 3 |
| `backend/justshipit/engineering/wireflow.py` | `shot_of`, `missing`; `for_card` built mode | 3 |
| `backend/justshipit/engineering/try_it.py`, `worker.py` | `SHOT_AT` constant; acceptance prompt lists planned ids with files and what is missing | 3 |
| `web/src/wireflow/PlannedVsBuilt.tsx`, `decision.tsx` | pairs in `TryIt`; extras stay as thumbnails | 3 |

## Phasing, and why no phase reworks an earlier one

- **Phase 1, the approval card shows the screens.** Everything a later phase joins on exists from the first spec
  drafted: ids, `screens.yaml`, the ui-states naming rule, `planned_screens` in the handoff. The payload is already
  the `mode: "plan"` union member; `change`, `landed`, `asks`, `removed` are present and empty.
- **Phase 2, a note redraws.** Adds fields with defaults (`screen_notes`, `OwnerTraceOut.screens`, `settle(changed=)`)
  and fills the empty payload fields. The only restructuring is lifting `_brief`/`_package` out of `write_spec`,
  which changes no behaviour and is its own commit.
- **Phase 3, planned vs built.** Adds the `mode: "built"` member and `AcceptanceOut.screens`. `Frame` is reused as
  is. Builds from Phase 1 specs already have captures named by id, so they compare without a migration; older builds
  have no `screens.yaml` and show today's thumbnails.

Each phase ends with its own check: (1) a real draft of the BUSINE10 prompt rendered in the panel at 1280 and 1440;
(2) a note on `mapping-warning` declined, redrafted, and shown landed on that frame, plus a note the writer ignores
shown as missed; (3) J-10-shaped captures paired against the plan, one planned screen left uncaptured on purpose.
