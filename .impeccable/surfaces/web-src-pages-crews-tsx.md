---
version: 1
slug: "web-src-pages-crews-tsx"
primary_target: "web/src/pages/Crews.tsx"
related_targets: []
---

# Teams for each job (`/crews`)

Mode: Operate. Shaped 2026-11-14 without the owner (no interview was possible in this run); the brief is inferred from `docs/design/staffing-structure.md` sections 4.1, 9 and 10, PRODUCT.md and DESIGN.md. Every assumption below is marked so the owner can correct it.

## Job and audience

The solo founder, checking in a few times a day on a desktop browser, sometimes after days away. They used to see a standing org chart of AI employees; now each task (T-n) and build (J-n) gets its own team, placed by a rule, and dissolved when the job ends. The owner comes here to answer two questions about any job: who worked on it, and why that team. Secondary: what it cost and how it ended, and whether a job has no team record at all.

## Outcome and proof

- Success: in one look per row the owner reads the team, the reason sentences, and the outcome, without opening anything.
- Proof is the record only. Members come from the ledger, reasons are the server's sentences (`why[].text`) shown verbatim, outcome numbers are the ledger's sums. A job with no crew shows the server's note and no team. Nothing is derived, guessed or filled in.
- Product truth unique here: the Staffing Agency Manager is a rule, not a model, so its reasons are facts about the job, not an agent's opinion. The screen says "Placed by the Staffing Agency Manager" once per job, before the reasons.

## Selected direction

Established world (DESIGN.md, the Control Room), extended, not replaced. Structure is pinned by the design's section 4.1 sketch: projects as sections, one row per task and build in plan order, each row carrying its team, its reasons and its outcome inline. The surface roll (seed below) dealt a dependency timeline, a list with a side panel and a dependency graph; the brief overrides all three: a side panel hides the "why" behind a click, a graph is a second way to draw a team (the failure section 9 removes), and the timeline's one useful idea (plan order, "after T-11") is kept as the row's order and a linked flow label.

## Scope and boundaries

- In: `Crews.tsx`, filters that match `GET /crews` (business, project, state), the `/org` redirect with its parameters, every `/org` link moved, nav and palette entries, the chat page ref.
- Out: the recall "Ask" button (unit 12 adds it in a named empty slot in the row), the vocabulary series (unit 10), the roster page itself (Agents stays; the crews screen links to it).
- Untouched: Agents page layout, React Flow dependency.
- Anti-goals: a chart of employees, a tile row of totals, chips piled on a row, any team shown for a job that has no crew.

## States and ranges

- Rows: 0 to a few hundred across all projects; a project has 1 to ~40 tasks and 0 to a few builds.
- Standing: working, waiting (not started), dissolved, cancelled before start, no record. The last three without a team carry the server's note.
- Reasons: 1 to ~6 sentences per job, amendments last ("added later").
- Members: 1 to 3 roles; a role that fell back to another model shows twice; a placed slot whose role made no call shows as placed with no call yet.
- Load, error (LoadError), empty (no jobs yet), filtered empty (clear filters), no business (FirstRun note), worker down (working chips read useWorker).

## Interaction and layout

PageHead (title, status sentence: how many teams are working now, and how many jobs have no record), Toolbar (business, project, state selects), then one Section per project. A row: mono key and the job title as a link to the task or build, one status chip; under it three labelled lines in reading order: Team (shape, flow, members with model and duty), Why (the reasons), Outcome (calls, failures, rework, time, cost, result, shadow checks, the fixed path's call count for comparison). Labels sit in a narrow left column on wide screens and above their line when narrow. Never assume label or sentence length; lines wrap.

## Constraints and open decisions

- Every string from `t()` in both languages; reasons and notes arrive already in the owner's language.
- Assumption: the screen title is "Teams for each job" / "꾸린 팀". The glossary term (unit 10) is the singular "Team for this job"; a list of every job's team reads as the plural in English.
- Assumption: the default with no filter is every business (the old chart's default), and `?business_id=` narrows it.

## Direction contract

THESIS: Every job's team written out like a staffing record: who was placed, the rule's reasons in its own words, and what it cost, one job per row. Refuses the standing org chart and the tile dashboard of totals.

OWN-WORLD: The Control Room unchanged: white stage, hairline rows, Pretendard, mono reference ids, tabular numbers, one state chip per row, indigo only for links and focus, state hues only for status.

STORY: The owner opens the screen, reads "2 teams are working now", scans a project's rows, sees T-12 was done alone because nothing waits on it, sees T-14 was checked because the build reads it, and leaves knowing why each job cost what it did.

FIRST VIEWPORT: Sidebar and top bar as everywhere; PageHead with the status sentence; toolbar of three selects; the newest project's section with its first rows, each showing Team, Why and Outcome lines.

FORM: Sectioned hairline list with a three-line record per row, labels in a quiet gutter; the signature move is the reason sentences set as the row's body, verbatim from the rule.

FINISH: critique and polish passes, detector run, real screen checked in English and Korean.

seed: c3a5901bae557b45706585d8744b0831 (surface scope, operate; dealt 5, 2, 7; overridden by the design's pinned structure)
