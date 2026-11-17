---
name: Just Ship It
description: The owner's console for an AI-run company, read like a report to a superior. A dense, calm control room: the state in one line, then the work laid out in rows and boards.
colors:
  ground: "#f4f3f0"
  panel: "#ffffff"
  panel-2: "#f3f2ef"
  elevated: "#ffffff"
  hover: "#f1f0ec"
  selected: "#ebeaf3"
  line: "#e7e5e0"
  line-strong: "#cfccc5"
  ink: "#1c1b19"
  text-2: "#5f5c56"
  text-3: "#67645e"
  primary: "#1c1b19"
  on-primary: "#ffffff"
  accent: "#5a58c8"
  accent-soft: "#ecebfa"
  on-accent: "#ffffff"
  ok: "#1f7a48"
  ok-bg: "#e4f3ea"
  run: "#2f63c4"
  run-bg: "#e7eefb"
  warn: "#a8560f"
  warn-bg: "#fbf0e3"
  bad: "#b8302b"
  bad-bg: "#fbe9e7"
  info: "#6a44b0"
  info-bg: "#f0ebfa"
  on-tone: "#ffffff"
typography:
  title:
    fontFamily: "Pretendard Variable, Pretendard, Apple SD Gothic Neo, Malgun Gothic, system-ui, sans-serif"
    fontSize: "20px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  section:
    fontFamily: "Pretendard Variable, Pretendard, Apple SD Gothic Neo, Malgun Gothic, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "Pretendard Variable, Pretendard, Apple SD Gothic Neo, Malgun Gothic, system-ui, sans-serif"
    fontSize: "13.5px"
    fontWeight: 400
    lineHeight: 1.5
  row:
    fontFamily: "Pretendard Variable, Pretendard, Apple SD Gothic Neo, Malgun Gothic, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 500
    lineHeight: 1.4
  meta:
    fontFamily: "Pretendard Variable, Pretendard, Apple SD Gothic Neo, Malgun Gothic, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.45
  prose:
    fontFamily: "Pretendard Variable, Pretendard, Apple SD Gothic Neo, Malgun Gothic, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.6
  metric:
    fontFamily: "Pretendard Variable, Pretendard, Apple SD Gothic Neo, Malgun Gothic, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.02em"
    fontFeature: "\"tnum\""
  mono:
    fontFamily: "ui-monospace, Cascadia Code, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 400
    fontFeature: "\"tnum\""
rounded:
  tag: "4px"
  control: "6px"
  container: "8px"
  pill: "999px"
spacing:
  s-1: "4px"
  s-2: "8px"
  s-3: "12px"
  s-4: "16px"
  s-5: "20px"
  s-6: "24px"
  s-7: "28px"
  s-8: "32px"
  row-h: "36px"
  control-h: "30px"
  nav-h: "28px"
  topbar-h: "48px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.row}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "{spacing.control-h}"
  button-ghost:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.row}"
    rounded: "{rounded.control}"
    padding: "0 10px"
    height: "{spacing.control-h}"
  icon-button:
    backgroundColor: "transparent"
    textColor: "{colors.text-2}"
    rounded: "{rounded.control}"
    height: "{spacing.control-h}"
    width: "{spacing.control-h}"
  input:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 10px"
    height: "{spacing.control-h}"
  row:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.row}"
    padding: "0 12px"
    height: "{spacing.row-h}"
  box:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.container}"
    padding: "12px 14px"
  board-column:
    backgroundColor: "{colors.panel-2}"
    rounded: "{rounded.container}"
    padding: "8px"
  board-card:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.row}"
    rounded: "{rounded.control}"
    padding: "8px 10px"
  chip-run:
    backgroundColor: "{colors.run-bg}"
    textColor: "{colors.run}"
    typography: "{typography.meta}"
    rounded: "{rounded.pill}"
    padding: "0 8px"
    height: "20px"
  chip-ok:
    backgroundColor: "{colors.ok-bg}"
    textColor: "{colors.ok}"
    typography: "{typography.meta}"
    rounded: "{rounded.pill}"
    padding: "0 8px"
    height: "20px"
  chip-warn:
    backgroundColor: "{colors.warn-bg}"
    textColor: "{colors.warn}"
    typography: "{typography.meta}"
    rounded: "{rounded.pill}"
    padding: "0 8px"
    height: "20px"
  chip-bad:
    backgroundColor: "{colors.bad-bg}"
    textColor: "{colors.bad}"
    typography: "{typography.meta}"
    rounded: "{rounded.pill}"
    padding: "0 8px"
    height: "20px"
  chip-info:
    backgroundColor: "{colors.info-bg}"
    textColor: "{colors.info}"
    typography: "{typography.meta}"
    rounded: "{rounded.pill}"
    padding: "0 8px"
    height: "20px"
  chip-muted:
    backgroundColor: "{colors.panel-2}"
    textColor: "{colors.text-2}"
    typography: "{typography.meta}"
    rounded: "{rounded.pill}"
    padding: "0 8px"
    height: "20px"
  tag:
    backgroundColor: "transparent"
    textColor: "{colors.text-2}"
    typography: "{typography.meta}"
    rounded: "{rounded.tag}"
    padding: "0 6px"
    height: "18px"
  banner-bad:
    backgroundColor: "{colors.bad-bg}"
    textColor: "{colors.bad}"
    rounded: "{rounded.container}"
    padding: "8px 12px"
  banner-warn:
    backgroundColor: "{colors.warn-bg}"
    textColor: "{colors.warn}"
    rounded: "{rounded.container}"
    padding: "8px 12px"
  segmented:
    backgroundColor: "{colors.panel-2}"
    rounded: "{rounded.control}"
    padding: "2px"
    height: "{spacing.control-h}"
  segmented-on:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.tag}"
    padding: "0 10px"
  nav-item:
    textColor: "{colors.text-2}"
    typography: "{typography.row}"
    rounded: "{rounded.control}"
    padding: "0 8px"
    height: "{spacing.nav-h}"
  nav-item-active:
    backgroundColor: "{colors.selected}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "0 8px"
    height: "{spacing.nav-h}"
  sidebar:
    backgroundColor: "{colors.ground}"
    width: "232px"
  topbar:
    backgroundColor: "{colors.panel}"
    height: "{spacing.topbar-h}"
    padding: "0 20px"
  popover:
    backgroundColor: "{colors.elevated}"
    rounded: "{rounded.container}"
    padding: "8px"
    shadow: "0 8px 24px rgb(20 20 22 / .10), 0 0 0 1px var(--border)"
  meter:
    backgroundColor: "{colors.panel-2}"
    rounded: "{rounded.pill}"
    height: "4px"
---

# Design System: Just Ship It

## Overview

**Creative North Star: "The Control Room"**

The owner console is a company control room you can read in ten seconds, written like an employee's report to a superior. Every owner screen opens on a page title and one status sentence: the conclusion first (what waits on the owner, what broke), with the next action as the one primary button. Then the work itself, laid out by state in rows and boards.

Revised 2026-11-07 (the Linear-inspired redesign): the room is denser and quieter than before. The sidebar sits on the canvas tint and the page on a white stage, so the eye lands on the work; the top bar carries the location (business first) and one company-state chip that says whether the company can work at all; type runs one step smaller (20px title, 13.5px body, 36px rows); radii shrink to 6 and 8px; nothing casts a shadow unless it floats. Light is the default; dark is the owner's opt-in under Settings, applied as `:root[data-theme="dark"]` with a full mirrored token set. Every text tier measures at least 4.5:1 on every surface it sits on, in both themes (ratios in the redesign doc, section 2).

The material is paper-flat and slightly warm: a warm-grey ground, white surfaces, hairline borders. Depth comes from tone (ground, surface, surface-2, elevated) and borders, not from shadow. The primary action is ink, not brand colour; the single indigo accent marks what is selected, focused, linked, or in progress. Five state colours (blue running, amber waiting, green done, red failed or blocked, violet review) do real jobs on chips, column headers, meters, and chart series, and nowhere else.

**Key Characteristics:**
- Conclusion first: PageHead carries the title (from the route), a status sentence with a state dot, and at most one primary action.
- One frame on every screen: PageHead, toolbar, sections of 36px rows with hairlines, a side panel for the item under the cursor.
- Company state in the top bar: running, stopped by the owner, waiting for the worker, console unreachable, with the one fix command (`uv run ship supervise`).
- Flat at rest: hairlines and tonal layers; shadows only on layers that float (popovers, palette, toasts, drawer).
- Ink primary buttons; one indigo accent for selection, focus, links, and progress.
- Pretendard Variable throughout, tabular numerals wherever numbers change, mono only for reference IDs and code.
- Reduced motion honoured: live pulses stop and transitions drop.

## Colors

A warm neutral room with ink for action, one indigo for selection, and five working state hues. Tokens live on `:root` in `web/src/styles.css`; the old names (`--sidebar`, `--panel`, `--panel-2`, `--line`, `--line-strong`, `--muted`) remain as aliases of the new ones, so page CSS keeps working.

### Primary
- **Console Ink** (`primary`, `ink`): body text, the primary button fill, the brand mark, and the active filter pill. Inverts to near-white in dark mode.

### Secondary
- **Selection Indigo** (`accent`): focus rings (2px outline, 2px offset), links, the selected row and card ring, the progress meter, the caret, form `accent-color`, and the spend chart series. Never a button fill.
- **Indigo Wash** (`accent-soft`): text selection, and the chip of a selected filter.
- **Selected** (`selected`): the active nav item and the selected row, a quiet lavender-grey tint, not a white tile.

### Tertiary (state)
- **Running Blue** (`run` on `run-bg`): agents and tasks in progress; live dots pulse in this hue.
- **Waiting Amber** (`warn` on `warn-bg`): awaiting the owner, past a due date, a cap at 70% or more, a stop or pause.
- **Done Green** (`ok` on `ok-bg`): completed and healthy. Meters under 70% are neutral, not green.
- **Failed Red** (`bad` on `bad-bg`): failures only. Waiting on the owner, a reached cap or the owner's own rejection is never red.
- **Review Violet** (`info` on `info-bg`): review and informational states.
- **On Tone** (`on-tone`): text on solid state fills; white in light mode, near-black in dark mode where the tones are light.

### Neutral
- **Warm Ground** (`ground`): the canvas behind everything; the sidebar sits directly on it.
- **Surface** (`panel`): the page stage, rows, boxes, cards, inputs, the top bar.
- **Recess** (`panel-2`): board columns, segmented trays, wells inside cards, code.
- **Elevated** (`elevated`): popovers, the palette, toasts and the side panel; the only surfaces with a shadow.
- **Hover** (`hover`) and **Selected** (`selected`): row and nav states, distinct from each other.
- **Hairline** (`line`) and **Strong Hairline** (`line-strong`): every border; strong for inputs, hover, and dashed empty states.
- **Ink** (`ink`), **Text 2** (`text-2`, labels and secondary text) and **Text 3** (`text-3`, metadata on tinted surfaces). All three hold at least 4.5:1 on ground, surface, surface-2, hover, selected and accent-soft in both themes (measured: text-3 light 4.94 on selected, dark 4.90 on accent-soft).

### Named Rules
**The State Means Colour Rule.** Run, warn, ok, bad, and info appear only when they encode a state. Each solid hue pairs with its own `-bg` wash; never use a state hue for decoration or emphasis.

**The Ink, Not Indigo Rule.** Primary actions are Console Ink. Indigo is reserved for selection, focus, links, and progress, so a coloured element always means "this one" or "this far".

**The Measured Contrast Rule.** A new text colour or tinted surface is added with its ratio measured (the redesign doc's table), not eyeballed. Nothing below 4.5:1 for text, 3:1 for icons and hairlines that carry meaning.

## Typography

**Display Font:** Pretendard Variable (self-hosted dynamic subset from the `pretendard` package; falls back to Apple SD Gothic Neo, Malgun Gothic, system-ui)
**Body Font:** Pretendard Variable
**Label/Mono Font:** ui-monospace, Cascadia Code, Consolas

**Character:** One neutral Korean-first grotesque does every job; hierarchy comes from weight (400, 500, 600) and a tight six-step scale, not from a second face. The scale is the token set `--fs-title`, `--fs-section`, `--fs-body`, `--fs-row`, `--fs-meta`, `--fs-prose`.

### Hierarchy
- **Title** (600, 20px, 1.3, -0.01em): the page title in PageHead. One per screen.
- **Section** (600, 14px, 1.4): section headings, the side panel title, settings section titles.
- **Body** (400, 13.5px, 1.5): the default running text of the console, including the status sentence.
- **Row** (500, 13px, 1.4): row titles, buttons, nav items, breadcrumbs, tab and segment labels, board card titles.
- **Meta** (400, 12px, 1.45): chips, counts, timestamps, row metadata, sidebar group labels (600 for group labels and chips).
- **Prose** (400, 14px, 1.6): long text that is read rather than scanned (plans, meeting notes, markdown), capped at 72ch.
- **Metric** (600, 22px, 1.15, -0.02em, tabular): the one figure a page exists for, when it has one; its unit in meta.
- **Mono** (400, 12px, tabular): reference IDs (REQ-66, T-68), codes, and the fix command.

### Named Rules
**The Tabular Rule.** Every number that can change (counts, money, times, percentages) uses tabular numerals so columns and rows do not jitter under live updates.

**The One Face Rule.** Pretendard for everything; mono only for identifiers and code, never for prose or headings.

**The One Title Rule.** A screen has one 20px title, set from the route table in `nav.tsx`, and it is the same string as the last breadcrumb and the document title.

## Layout

A dense web shell: a 232px sidebar on the ground tint, a 48px top bar on the stage, and a main stage on surface white padded 20px top, 24px sides, 48px bottom, filling the width of the screen. The stage, not the sidebar, is the lighter surface, so the work is what the eye lands on.

**The Wide Screen Rule.** The page fills the stage and starts under the top bar at every width; nothing is centred or pinned left with an empty band beside it. Text keeps its own measure (72ch on prose), so the extra width of a 1920 or 2560 monitor goes to columns, boards, tables and the side panel, never to longer lines. Past 2560px the page and the top bar share one gutter (`--gutter`, `--page-max`), so they centre together.

**The One Frame Rule.** Every owner screen follows one order: `PageHead` (title from the route, the status sentence with its dot, an optional quiet second line, at most one primary action and ghost actions on the right), an optional `Banner` row (everything that stopped, one row each), an optional `Toolbar` (view switch, filters, search, count), then `Section`s. Sections are spaced 24px apart; each has a 14px heading with an optional count and right-aligned actions. Lists are `Rows` of 36px `Row`s separated by hairlines; the item under the cursor opens in the side panel (at least 400px or 42%) beside the list, never on a new page. Multi-column rows use `cols-2`, `cols-3`, or a 1.6:1 main/side split with 16px gaps.

Boards run as horizontal grids of columns on recess (at least 232px each, 12px gap); an empty column keeps its header and one quiet line and gives its width to columns with cards. When the columns do not fit (the side panel is open) they wrap onto rows instead of squeezing. Columns show 12 cards before a dashed "N more" button.

Breakpoints (desktop only; 390px is out of scope): at 1200px three-column rows drop to two and the main/side split stacks; at 1100px secondary list columns hide; at 900px the sidebar becomes a drawer (at most 300px or 86vw, over a scrim) behind a top-bar menu button and main padding drops to 16px.

### Sidebar
Grouped around the owner's jobs, every route reachable and every URL unchanged:
- **Today, Decisions, My to-dos** (no label): the owner's own turn. Decisions carries a red count when a decision blocks work, a quiet count otherwise; under each, when another business has work waiting, one line "n decisions waiting in other businesses" (from `today.elsewhere`, when the server sends it).
- **Business**: Business, Projects.
- **Company work** (collapsible, remembers its state; the collapsed label carries the failed count): Tasks, Engineering, Routines, Agents (All n agents, then at most five agents with live dots), Teams for each job, Skills.
- **Records** (collapsible): Activity, Model calls, Background work.
- **Costs, Settings**, then the "Show test projects" switch at the end.

Items are 28px, 13px/500 in text-2, icon 15px; the active item is a `selected` tint with ink text and 600 weight, not a white tile. Group labels are 12px/600 in text-3. The business switcher sits under the brand: the current business name, and a menu of every business with its waiting decisions and to-dos; picking one puts `?b=` in the address.

### Top bar
48px on the stage with a hairline below. Left: the breadcrumb, business name first (a link to Today), every middle crumb a link, the current page last in ink. Right: the company-state chip, the stop-everything control, the theme toggle. The chip is the one place that says whether the company can work: Running (quiet), Stopped by you (amber), Waiting for the worker (amber, with "never started" or "last seen n ago"), Console unreachable or Connection lost (red). Its popover holds the fix command with a copy button, "Check again", and, when data is stale, "Showing data from n ago".

## Elevation & Depth

Flat at rest. Depth is tonal: ground behind, surface for content, recess for wells and columns, elevated for what floats, each edged with a hairline. Shadows exist only for layers above the page.

### Shadow Vocabulary
- **Pop** (`--shadow-pop`: `0 8px 24px rgb(20 20 22 / .10)` plus a hairline ring): popovers, the state popover, the switcher menu, the palette, toasts, the side panel, the drawer.
- **Lift** (`--shadow-lift`: `0 2px 8px rgb(20 20 22 / .06)`): a board card while dragged or hovered. Nothing else.
- **Selection ring** (`box-shadow: 0 0 0 1px var(--accent)`): the selected or open board card.
- **Live halo** (`0 0 0 3px var(--run-bg)`, pulsing 1.8s): running agent dots and live card dots; stops under reduced motion.

### Named Rules
**The Flat-By-Default Rule.** Nothing casts a shadow at rest. A shadow means the layer floats above the page, or a card is being moved.

## Shapes

Small radii in three tiers: containers (boxes, board columns, banners, popovers, the side panel) at 8px; controls (buttons, inputs, nav items, segmented trays, board cards) at 6px; tags and segment buttons at 4px. Chips, counts, and meters are full pills. Borders are always 1px; dashed strong hairlines mark empty states and "more" buttons.

**The No Nested Box Rule.** A bordered box never sits inside another bordered box. Entries inside a box are separated by hairline rules, not boxed again.

## Components

The shared primitives live in `web/src/components/kit.tsx`, `ui.tsx`, `frame.tsx`, `nav.tsx`, `stop.tsx` and `shell.tsx`. Pages compose them and add only their own content; the redesign doc's section 6 lists each with its usage rule.

### Buttons
Restrained and solid, 30px tall.
- **Primary** (`.btn-primary`, `button.primary`): Console Ink fill, white 13px/500 label, 0 12px padding; hover brightens. One per PageHead.
- **Ghost** (`.btn-ghost`, `.btn-ghost.small`): surface fill, hairline border, ink label; hover strengthens the border and tints to `hover`.
- **Danger** (`.danger`): red text, red border on hover; `.danger-solid` for the confirming step only.
- **Icon button** (`.icon-btn`): 30px square, no border at rest, text-2 glyph; hover tints. Always has `aria-label` and `title`.
- **Focus / Disabled:** 2px indigo outline at 2px offset; disabled at 55% opacity. Transitions 0.12s ease-out on border and background.
- **ActionButton**: a button that runs a request. With `confirm` it arms in place: an optional `what` sentence above the pair, the safe "Keep as is" button first and focused, the action button named for the action; Escape disarms. The result (`done`) is announced to the toast area and focus settles on the next thing, never on the body.
- **ConfirmBox**: the same pattern for a destructive step that needs a sentence of its own; safe button first, Escape and unmount restore the opener's focus.

### Rows
`Rows` is a hairline-separated list on surface; `Row` is 36px with `lead` (dot, icon or reference), `title`, `meta` (12px text-2), and `end` (chip, time, action). A `to` makes the row a link; `panel` opens it in the side panel; `selected` tints it. Hover tints to `hover`. Rows never carry more than one chip.

### Chips and Tags
- **Chip:** 20px pill, 12px/600, state text on its `-bg` wash with no border; neutral chips on recess with text-2. One chip per row or card.
- **Tag:** 18px, 4px radius, hairline, text-2; secondary attributes (kind, risk, result).
- **Count:** a pill with tabular digits; red solid only for a count that blocks work.

### Cards / Containers
- **Box:** surface on ground or stage, 8px radius, 12px 14px padding; flush boxes hold edge-to-edge `Rows`.
- **Board column:** recess, 8px radius, 8px padding, headed by a chip and a count.
- **Board card:** surface, 6px radius, 8px 10px padding, a mono reference and live dot on top, a two-line title, one meta line, one chip at most.
- **Side panel:** elevated with pop shadow, a 14px title, close button, and the item's own sections; keyboard-closable.

### Inputs / Fields
30px tall, surface fill, strong hairline, 6px radius, 0 10px padding; selects drop native chrome for a drawn chevron. Search fields wrap a leading icon. Focus: 2px indigo outline. Labels 12px/600 text-2; error text in bad on its wash.

### Navigation
- **Sidebar:** see Layout. `NavItem`, `Group` (collapsible, remembers `ship.nav.<id>`), `BusinessSwitcher` (role=menu, arrow keys, counts per business).
- **Top bar:** `Topbar` renders the crumbs from the `ROUTES` table, sets the document title ("page · business · Just Ship It"), and moves focus to `#main` on each route change.
- **Tabs:** three kinds, chosen by use. `RouteTabs` for sub-screens with their own URL; `Tabs` + `TabPanel` for ARIA tabs within one screen (roving tabindex, arrow keys, `aria-controls`); `ViewSwitch` (`.seg`) for board/list and period filters, with `aria-pressed`.
- **Command palette:** Ctrl K; screens by their sidebar names and aliases, businesses by name, and ids.

### Status Line (signature)
One sentence under the page title, 13.5px ink, led by an 8px state dot (run, warn, bad, ok, muted). It states the conclusion and names the count only when the owner must act; the next action is the primary button on the right. A quiet 12px text-2 line below may carry the one figure the page exists for. Loading says so in the same slot.

### Company state (signature)
`useWorker()` from `frame.tsx` is the one truth about whether the company can work: `down`, `stopped`, `lastSeen`, `label`, and `running(code)`, which turns "Running" into "Stopping at its next step" when the owner has stopped the company and "Waiting for the worker" when the worker is down. Every "working" chip on every screen reads it, so the console never shows work moving while nothing can move.

### Load, empty and error states
- **Loading** / **Skeleton**: a skeleton of the rows to come, never a spinner alone.
- **LoadError**: a titled block, "Couldn't load this screen", with the cause by status (the console server is not answering, this item no longer exists, you need to log in, the server hit an error) and a "Try again" button, or "Go to today" when the item is gone. "Failed to fetch" never reaches the screen.
- **EmptyState**: an ink title and one sentence saying what will fill the area and when, with at most one action; `kind="filtered"` when a filter hides everything.
- **Banner**: one row on the tone's wash with an icon, text and an optional ghost action; for what stopped.
- **Toast**: elevated, pop shadow, bottom right, two at most; action results stay six seconds and are read by `role=status`.

## Do's and Don'ts

### Do:
- **Do** open every owner screen with `PageHead` and its status sentence, then an optional toolbar, then sections.
- **Do** use `Rows`/`Row` for any list, and open the item beside the list in the side panel.
- **Do** lay any collection that has states out as a Board with chip-and-count column headers, and offer the list view through `ViewSwitch`.
- **Do** read `useWorker()` for every chip that claims work is moving.
- **Do** use `LoadError` for every failed load and `ActionButton`/`ConfirmBox` for every action and confirmation.
- **Do** use Console Ink for the primary action and reserve indigo for selection, focus, links, and progress.
- **Do** pair every state hue with its own `-bg` wash and use it only for that state.
- **Do** use tokens (`var(--…)`) for every colour, size, radius and duration; define both light and dark values for any new token and measure its contrast.
- **Do** honour `prefers-reduced-motion`: live pulses stop and transitions drop.

### Don't:
- **Don't** fill buttons with indigo; that is the legacy `button.primary` look the kit overrides.
- **Don't** nest a bordered box inside another; separate entries with hairlines.
- **Don't** add shadows to surfaces at rest.
- **Don't** use state colours for decoration, category, or brand emphasis.
- **Don't** open a screen with a row of same-size number tiles, or show a zero as a big number.
- **Don't** pile chips on a row or card; one status signal.
- **Don't** write a page's own error sentence, confirm pattern, or "worker is down" text; the shared primitives own them.
- **Don't** put a radius above 8px on anything but a pill.
- **Don't** introduce a second display face or set prose or headings in mono.
- **Don't** fix widths to Korean string lengths; the UI ships in English and Korean.
