---
name: linear
description: "Dense keyboard-first operator workspace: quiet cool-neutral chrome, a rail plus work regions on one shared header line, one accent kept for action, focus and done, and a drawn status glyph on every item."
accent-derive: { main: "#5e6ad2", canvas: "#e7e9ef", ink: "#1b1c21" }
colors:
  canvas: "#f3f4f7"
  surface: "#ffffff"
  surface-2: "#f8f9fb"
  surface-hover: "#f0f1f5"
  surface-active: "#e7e9ef"
  border: "#e2e4ea"
  border-strong: "#7d828d"
  text: "#1b1c21"
  text-2: "#5b5f6a"
  text-3: "#a2a6af"
  primary: "#5e6ad2"
  primary-hover: "#515bc1"
  primary-press: "#444cb1"
  on-primary: "#ffffff"
  on-strong: "#ffffff"
  primary-text: "#555fc6"
  primary-wash: "#f0f3ff"
  primary-wash-strong: "#dfe5ff"
  primary-border: "#5e6ad2"
  highlight: "#5e6ad2"
  danger-fg: "#b42318"
  danger-bg: "#fdf0ee"
  danger-border: "#f1c3bc"
  danger-solid: "#b42318"
  warning-fg: "#8a5300"
  warning-bg: "#fdf5e1"
  warning-border: "#ecd39a"
  success-fg: "#1c7a43"
  success-bg: "#ebf6ef"
  success-border: "#b8dfc7"
  info-fg: "#4a5263"
  info-bg: "#f0f2f6"
  info-border: "#c6cbd5"
typography:
  family-sans: '"Pretendard Variable", Pretendard, Inter, -apple-system, "Segoe UI", "Malgun Gothic", sans-serif'
  family-mono: '"JetBrains Mono", "Pretendard Variable", Pretendard, monospace'
  source: "npm pretendard (OFL, bundle the dynamic-subset woff2); @fontsource/jetbrains-mono 400 only if raw code or file content is shown"
  numbers: "font-variant-numeric: tabular-nums on every count, amount, date, time and ID"
  micro: { size: 11px, line: 16px, weight: 500 }
  meta: { size: 12px, line: 16px, weight: 400 }
  ui: { size: 13px, line: 20px, weight: 400 }
  ui-strong: { size: 13px, line: 20px, weight: 500 }
  body: { size: 14px, line: 22px, weight: 400 }
  title: { size: 14px, line: 20px, weight: 600 }
  subject: { size: 16px, line: 24px, weight: 600 }
  heading: { size: 18px, line: 26px, weight: 600 }
  display: { size: 24px, line: 32px, weight: 600, tracking: -0.01em }
rounded: { xs: 4px, sm: 6px, md: 8px, lg: 12px }
spacing: [2px, 4px, 6px, 8px, 12px, 16px, 20px, 24px, 32px, 48px]
density: { row: 36px, row-compact: 30px, row-form: 44px, group-header: 32px, table-header: 32px, nav-item: 28px,
  control: 28px, control-lg: 32px, header: 44px, icon: 16px, glyph: 14px }
layout: { rail: 240px, panel-inset: 8px, inspector: "clamp(380px, 30vw, 480px)", peek: 420px,
  doc-column: 720px, properties: 280px }
elevation:
  overlay: "0 8px 24px rgba(27, 28, 33, 0.12), 0 2px 6px rgba(27, 28, 33, 0.08)"
  scrim: "rgba(27, 28, 33, 0.32)"
motion: "background-color and color 120ms ease-out; popovers fade 120ms; peek slides 160ms; none under prefers-reduced-motion"
---

<!-- shared:start -->
## 0. Shared rules (identical in every style pack)

**Precedence.** This pack decides structure, layout, components and tokens. The product's design brief decides content, copy, states, and which items, verdicts and buttons exist. When the brief describes layout ("a big upload area", "a summary card on top"), read it as content to place in this pack's structure, not as layout to copy. The brief's headline copy is used as written, in the slot this pack gives a headline. Never add a verdict, filter, search, help, account or settings slot that the brief lacks because this pack has room for one: leave the slot out. Where the brief is silent, this pack's default applies.

**Language.** Quoted copy in this pack ("필수", "닫기", "문제 아님 2건") is a Korean example of what a label or message says, never the words to ship. Write every label, message and count in the product's language: the brief's words where it gives them, otherwise natural copy that means the same. The Korean copy, number and date rules in the pack's Korean section apply only when the product's language is Korean.

**Verdict model.** Before building, put the brief's items in one of three models and build only that model's controls.

| Model | Per item | Progress | Done | Finishing action |
|---|---|---|---|---|
| (a) Resolved one by one (approve, fix, reply) | one or more verdicts | items with a verdict, "3 / 5" | every item has one: say it in words | primary from the start, unless the brief gates it |
| (b) Dismiss-only, one bulk action (export, print, send) | one optional verdict ("문제 아님"), nothing else | no bar and no "n / N": show what the bulk action carries ("문제 아님 2건 · 내보낼 3건") once, and no receipt line when a count strip and the button label already show both numbers | none, the set is ready at any time | the bulk action, always primary, its label naming its output and count |
| (c) Read-only (report, digest) | none | none | none | share or export the whole |

- A dismissed item is neutral: muted text and a neutral badge with the verdict word, no success colour, no check icon. Success styling is only for a verdict that fixed or approved something. Every verdict is undoable in place, and a handled item stays where it is until the user refilters or reloads.
- A count, receipt or label that describes the finishing action counts what that action outputs per the brief, not what this pack's examples output.
- When the finishing action would output nothing, it stays in place, disabled, with the reason as visible text beside it. Its label drops the count ("예외 내보내기", never "예외 0건 내보내기") and the reason says why ("내보낼 예외가 없어요"). Nothing else is promoted to primary.
- **Severity is not state.** State is where an item stands (to do, done, dismissed, error) and uses this pack's status mark. Severity is how much it matters and is a separate word badge: the top level in the danger tint ("높음"), a middle level in the warning tint, the lowest with no badge. A domain with two levels uses the top and the lowest: its high level gets the danger badge, its other level ("확인") none. A severity badge never sits next to a state badge of the same tint: when both would be danger or both warning, the state badge goes neutral and keeps its icon and word. Severity never colours a row, a status mark or an outline, and it stays visible after the item is handled.
- **Blocking** means the brief says the item stops the user's core task (labels can't print, an order can't ship). Every other item is a check, whatever its severity.

**Evidence first.** When an item comes from source data (rows of a file, a message, a log line), its detail region shows the source by default, not a summary of it.
- One row: the source rows around it (2 above, 2 below) as a compact table with the file's row numbers and column names, the item's row marked, and the faulty cell marked with the state tint, a 1px state border and a word ("빈칸", "중복"). When the file has more columns than fit, show these first, in this order: the row number, the record id, the faulty column, then the columns the rule reads. Other columns get only the room left.
- A group (duplicates, a split order): every row of the group side by side, fields as rows, the differing fields marked, and one line naming the difference ("유입 시각만 달라요"). Then the item's whole row as a field list, unless the group table already shows every field.
- On first load with items present, the first unresolved item is already open, so evidence shows without a click. A set summary never occupies the detail region while items exist.
- A region taller than its content fills with evidence, or with what was checked and found clean (one line per check with its count, zero included). Never filler, illustration, or invented figures.

**Privacy.** Mask personal data (names, phone numbers, addresses, emails, account numbers) everywhere it is previewed: source rows, mapping samples, export previews, tooltips, and DOM attributes. Mask before render, so the raw value never reaches the page. Partial masks keep the shape (김*수, 010-****-5678). A hidden value is a word in the muted text colour ("있음" when presence matters, otherwise "가림"), never a grey bar, which reads as loading. An empty value is the word "빈칸", marked as the fault when it is one.

**Import and column mapping.** One row per field the product needs: required fields first under their own group header, optional ones after. Each row holds the field name (with a "필수" badge when required), a select of the file's columns prefilled with the best guess whose last option, on optional fields only, is the brief's "not in this file" choice (a field the product strictly requires never offers it), two sample values from the file (masked), and how many rows are filled ("310 / 312", plus "빈칸 2행" in the warning colour when short). The primary action stays disabled until every required field is mapped, with the reason beside it. Placement follows this pack's Structure.

**Errors.** One message per problem, in one place. A problem tied to a field shows under that field only. A banner is for a failure with no field (an unreadable file), or one summary line when two or more fields fail ("2개 열을 골라 주세요"), never repeating the field messages. A brief's sentence written for a banner ("아래에서 직접 골라 주세요") moves under its field reworded for that place, meaning kept ("이 열을 직접 골라 주세요"). A state the brief calls a warning (a guessed column, short rows) uses the warning colours on a field too; the danger colours are for what blocks the action. One recovery control per failure: while a banner shows its recovery button, the region below drops its own and shows one muted line saying what is missing ("읽은 파일이 없어요") in its place.

**Accessibility across re-renders.**
- After every action, focus stays on the same logical element (keyed by item ID), or moves to the next item when that element left. Every scrolling region keeps its scroll position. An app that re-renders the whole screen saves the focused key and each region's `scrollTop` before rendering and restores them after. Focus never falls to `body`.
- Cursor, selection and focus survive forced colours: draw them with `outline` or `border`, never with `box-shadow` or a background alone. Marked cells carry a border, not only a tint.
- Row actions have unique accessible names that include the item ("19행 문제 아님").
- One `role="status"` node, rendered once outside the re-rendered tree, announces each verdict and undo ("19행을 문제 아님으로 표시했어요"). Error banners use `role="alert"`.
- Animation runs only on nodes that persist between renders. A full re-render shows the end state without animation, and nothing waits on an animation. An app that redraws the whole view limits motion to CSS transitions on elements outside the redrawn tree, or has none, and this pack's signature still reads with no motion at all.
- **Core keys** work in every build: ↑/↓ to move, Enter to open or act, Esc to step back, the item's verdict key, Ctrl/⌘ Z to undo, and a Tab order that follows the layout. **Optional keys** exist only when the brief has the feature: selection keys only with bulk actions, `/` only with search, a command menu only with two or more views or six or more actions, `?` only with six or more shortcuts. Verdict letters come from the English verb and never use O, I or L, which read as 0, 1 and l. Kbd chips show the letter uppercase in the sans face. On a filled primary button the chip is `primary-press` with the `on-primary` letter and no border.

**Accent roles.** The owner picks one main colour per business. It is derived into exactly these roles, and component CSS names only the roles, never a hex.

| Role | Use |
|---|---|
| `primary` | a fill under an `on-primary` label: the primary button |
| `primary-hover`, `primary-press` | that fill's hover and pressed states; `primary-press` is also a kbd chip on that fill |
| `on-primary` | label and icon on those fills only. It is dark on light mains, so never hard-code white, and labels on danger fills or a dark toast use the fixed `on-strong` |
| `primary-text` | every accent text or icon on a neutral or wash ground: links, the selected tab or segment label, quiet accent buttons, accent figures under 24px |
| `primary-wash` | selected row, current filter, weak-button fill |
| `primary-wash-strong` | hover on a wash. Only the main and second text greys sit on it, never `primary-text` or the third grey |
| `primary-border` | focus ring, selected outline, mapped-field outline, drop-target border |
| `highlight` | marks without a label and text 24px and up: done mark, progress fill, checkbox fill, the first chart series, the pack's one big figure |

- Text never uses `primary`: a lint finds no `color: var(--primary)`. Marks use `highlight`, with any check knocked out in the surface colour. The accent hue is never reused for info or state colours: info is a low-chroma slate in every pack, so no main colour collides with it.
- A main colour can sit near the danger, warning or success hue (red, orange, yellow and green mains). So state badges and banners always carry their icon and their word, and no state is told by colour alone.
- The company derives them from the owner's main colour with this pack's `accent-derive` canvas and ink, so each meets its contrast floor. `highlight` is the main colour when it reaches 3:1 on that canvas, otherwise `primary-border`. The frontmatter holds the values for this product's main colour. An accent hex in a table below is the pack's measured example, never a value to use.

**Build.** Fonts install from npm and ship in the bundle, with no CDN. No inline `style` attributes, so a strict CSP passes: use classes, and set custom properties from the stylesheet or through the CSSOM.
<!-- shared:end -->

## 1. World

A quiet, fast instrument for a set worked through every day: cool grey chrome that recedes, dense rows, every move
on the keyboard, aligned columns and exact counts.

What the source language lends: one neo-grotesk voice (Pretendard, whose Latin is drawn from Inter) at 400, 500
and 600 with tabular figures; a cool neutral scale where hierarchy comes from surface steps, not shadows; 36px rows,
28px controls and 44px headers; the ground-and-panel shell; and the signature move, the status glyph. Not taken:
logos, names, type above 24px, framed screenshots, a rainbow label palette (section 6 has the rest). Light theme
only, because the accent roles are derived for light grounds.

### Signature: the status glyph
Every item that can be worked on carries a 14px drawn glyph: at the start of its row, in the inspector header, on
its group header and in the rail filters. One SVG component, `viewBox="0 0 14 14"`, `stroke-width="1.5"`, a circle
of radius 5.5 centred at 7,7. Never a Unicode or emoji character.

| State | Drawing | Colour |
|---|---|---|
| to do | circle outline | `text-2` |
| in progress / partly done | outline, right half filled as a pie | `warning-fg` |
| done (model a only) | filled circle, check knocked out in `surface` | `highlight` |
| dismissed (not an issue, skipped) | outline with a diagonal slash | `text-2` |
| blocked / error | filled 12px square, `rounded.xs`, `on-strong` "!" drawn as a bar and dot | `danger-fg` |

Products map their states onto these five shapes, never a sixth, and the state name is always visible as text or
a tooltip. The shape carries the state, so a main colour near red or green never confuses done with blocked.

## 2. Structure

### The shell (every archetype)
- The window is 100vh and never scrolls. Each region scrolls on its own, and its header stays put.
- **Rail** 240px on `canvas`, no borders. The top is 44px: the product name (`title`) and the current file or
  period (`meta`, `text-2`). Nav items are 28px: 16px icon, `ui` label, count at the right (`meta`, tnum), inset
  8px, `rounded.sm`. The selected item is `surface-active` with `text`. Groups sit 16px apart under a `meta` 500
  `text-2` name. The bottom holds one standing-context sentence (privacy, sync time) in `meta` `text-2`, two lines max.
- **Work panel.** Inset 8px from the window's top, right and bottom: `surface`, `rounded.md`, 1px `border`. Every
  work region lives inside it, divided by 1px `border` lines, never by gaps or cards.
- Every region header is 44px with a bottom `border`, so one line runs across the panel.
- At 1600px the extra width goes to the list or table, never to side margins.
- At most one primary button per region, ideally one per screen.

### Pick by the product's core verb
- **Clear a finite set** (review, approve, fix, reply, dispatch): A. Workbench.
- **Look up and edit an open-ended set of records** (stock, customers, bookings, staff): B. Table with peek.
- **Read a period's summary and pass it on** (weekly report, digest, month close): C. Document with properties.
When two fit, the daily task wins and the other becomes a view in the rail. Import, mapping and settings happen
inside the chosen shell (see Import step), never on a separate centred page.

### A. Workbench: rail, list, inspector
At 1440: rail 240, list fluid (at least 520), inspector `clamp(380px, 30vw, 480px)`.
- **List header** (44). Left: the headline in `subject` as a plain fact with the real numbers (the brief's headline
  when it has one). Right: up to three kbd hints for core moves, then the progress for the verdict model (model a:
  done glyph and "3 / 5"; model b: "문제 아님 2 · 내보낼 3" in `meta`), then the one primary button for the
  finishing action.
- **Column header** (32, sticky). Under it, **group header rows** (32, `surface-2`): the group's glyph, its name in
  `ui-strong`, a one-line instruction in `text-2`, and the count at the right. A group never repeats its count in a
  sentence. **Item rows** (36): glyph, identifier (`text-2`, tnum), main text, secondary columns, a severity column
  when the brief has severity, and hover actions right after the last text column. After the last row, one line
  says what was not flagged ("나머지 307행은 확인에 걸리지 않았어요").
- **Rail.** Source context (file name, row count, loaded time), then views as filters with counts (All, one per
  group), then nothing else.
- **Inspector.** Opens on the first unresolved item at load. Header (44): glyph, identifier, quiet prev/next icon
  buttons, close. Body padded 20px 24px, in this order:
  1. the subject (`subject`) and one `body` sentence naming the next action;
  2. the action row: the verdict as a primary `control-lg` button with its kbd chip, plus a secondary "다음";
  3. the evidence (section 0), titled in `title` ("파일에서 보면"): `row-compact` rows, a `meta` header, the
     file's row numbers in a 40px `surface-2` column, the item's row in `primary-wash` with its number in `text`;
  4. the item's whole row as a two-column field grid (column letter and name in `meta` `text-2`, value in `ui`);
  5. the activity log: what changed and when. Add "by whom" only when the brief has more than one user.
- **Inspector with no item.** With zero items (a clean run), it lists each check with its count of 0 and the file
  facts, with no action row. Before data, it says in 3 to 4 lines what will be checked.

### B. Table with peek
Rail 240, then a full-width table. Space or a click opens a **peek** of 420px over the table's right edge: `surface`,
a 1px left `border`, the `overlay` shadow. It does not reflow the columns, and Esc closes it. The peek's body follows
the inspector order above. Examples: inventory, a customer list, bookings.
- **Header** (44). Title and count, then view tabs. Right: search (240px), a quiet Filter button, the primary "New",
  each only when the brief has it. Applied filters show as tokens in a 40px **filter bar** with "Clear".
- **Table.** Header 32 and sticky, rows 36 with hairline separators, first column sticky under horizontal scroll.
  Headers sort with an arrow on the sorted column only.
- Enter or a double-click edits a cell in place (28px input; Enter saves, Esc cancels). With bulk actions, a 32px
  checkbox column appears on hover, and checked rows swap the header's right side for "3개 선택 · actions · Esc".
- **First screen.** The whole table, sorted by what needs attention first, with a status column of glyph and word.

### C. Document with properties
Rail 240. The document column is at most 720px, centred between the rail and the **properties** panel (280px at
the panel's right, behind a 1px `border`). Examples: a weekly sales report, a support digest, a month close.
- **Header** (44). Breadcrumb (report, then period) and prev/next period icon buttons. On the right, the primary
  Share or Export.
- **Document.** Title in `display`, then one status line with a glyph. Sections with a `heading`, `body` text and
  inline tables (32px rows). Key figures as at most 4 cells in one row: label (`meta` `text-2`), value (`display`,
  tnum), and a delta with an arrow icon and sign, coloured by state. Hairlines between cells. No cards, no hero.
- **Properties.** 32px label and value rows (period, source, updated, status), then the section outline as anchor
  links, the section in view in `text` and the others in `text-2`.

### Import step (any archetype)
The work panel holds one region with the mapping content from section 0. The rail keeps the file context and lists
what each check looks for. The inspector, when the archetype has one, shows the file facts (encoding with a quiet
"다른 인코딩으로 다시 읽기", row count, detected format).
- **Header** (44): "열을 확인해 주세요" in `subject` and the row count in `meta`. Right: the reason in `meta`
  `text-2` while required fields are missing ("주소 열을 골라 주세요"), then the primary "리포트 보기".
- **Column header** (32): 필요한 칸 (200px), 파일의 열 (a 240px `control-lg` select), 파일에서 본 값 (fluid, two
  values joined by " · "), 채워진 행 (right-aligned, tnum).
- Group header rows (32) "꼭 골라야 하는 열 3 / 3" and "더 정확하게 보기 (선택)" with the brief's one-line reason.
  Field rows are `row-form` (44) so the select fits. A field set to "not in this file" shows its name in `text-2`
  and "–" in the sample and count cells.

## 3. Interaction

**Keyboard.** Core and optional keys follow section 0. In this style:
- **Core.** ↑/↓ move the cursor, which is always the row open in the inspector. Enter moves focus to the
  inspector's first action; it never runs a verdict. Esc returns focus to the cursor row, then closes the top layer
  (menu, then peek, then selection). The verdict key (D for dismiss) and Ctrl/⌘ Z work in the list and the inspector.
- **Optional.** J/K as aliases for ↓/↑ in lists. X and Shift+↑/↓ for selection. `/` for search. The command menu on
  Ctrl K (⌘K on Mac). `?` for the shortcut sheet.
- Single-letter keys never fire while focus is in a text field. Labeled buttons carry a kbd chip. Icon buttons show
  a tooltip such as "문제 아님 · D". The list header shows at most three hints.

**Cursor, selection, hover.** The cursor row is `surface-active`, `aria-selected="true"`, with a 1px `border-strong`
outline at -1px offset so it survives forced colours. Selection (checked for bulk) is `primary-wash` plus a checked
box. Hover is `surface-hover`. Focus is `:focus-visible` only: a 2px `primary-border` outline at 1px offset, inset
at -2px on rows.

**Inline over buttons.**
- Row actions are quiet 28px icon buttons, at most two, shown on hover, on the cursor row and on focus-within. The
  same actions always appear as labeled buttons in the inspector.
- To change one property, click its value: a menu opens there. One field never gets an edit mode or a Save button.
  Only a form whose fields must commit together gets one Save.

**Progress toward done.** Handled items change their glyph in place and stay where they are. Dismissed rows turn
`text-2`. Nothing jumps or vanishes until the user refilters or reloads. The header updates with each change. In
model (a), when the last item is handled, the header shows a success badge ("모두 확인했어요") and the finishing
action stays primary. In model (b) there is no finished state, only the updated counts.

**Undo over confirm.** Reversible changes apply at once, with a toast and Undo, and Ctrl/⌘ Z works after the toast
fades. Confirm only what loses work undo cannot restore (a new file replacing data that carries verdicts; with no
verdicts, no dialog), deletes user data, or sends to other people. A download or export is not sending out. The
dialog is 440px. Its title names the consequence with the number ("문제 아님 2건이 사라져요. 새 파일을
열까요?"), then one sentence, a quiet Cancel (which holds focus), and a button named by verb and count.

**States live inside their region. The shell never disappears.**
- **Loading.** Skeleton rows at the real row height (bars in `surface-active`, `rounded.xs`). The header stays live.
  Known progress shows as `meta` text ("1,240 / 3,100행 읽는 중"). A spinner appears only inside the button that
  started the work, and that button keeps its width.
- **No data yet.** The column header stays, with one line in `text-2` and one button in the list body. When a file
  is expected, the whole window is the drop target (the brief's "upload area" lives here). On dragover the panel
  turns `primary-wash` with a 1px dashed `primary-border` border and "놓으면 바로 읽어요" in its centre. This is the
  only dashed border in the system.
  A filter with no results says "이 조건에 맞는 항목이 없어요" with a quiet "필터 지우기".
- **Error and warning.** A banner at the top of the affected region, under its header, per section 0's Errors rule.
  The region keeps its column header. Warning is for what works but needs a look (a guessed column).
- **Success.** A badge in the header, or a toast. Never a full-region success page.

**Toasts.** Bottom right, 16px from the edges, 360px wide, `surface`, 1px `border`, `overlay`, `rounded.md`. One
`ui` line and one action in `primary-text` with its kbd chip. Up to 3 stack. Each lives 6s and pauses on hover or
focus. An error the user must act on never lives in a toast. The optional **command menu** is 640px wide at 15vh,
`rounded.lg` over the `scrim`, with a 48px input and 36px result rows ending in their shortcut chip.

## 4. Components

**Icons.** Lucide only, 16px, `stroke-width` 1.75, in `text-2`, or `text` inside an active control.
**Buttons.** `rounded.sm`, `ui-strong`, a 16px icon with a 6px gap, width from the label. `control` (28) in headers
and rows, padding 0 10px. `control-lg` (32) in the inspector, forms and dialogs, padding 0 12px.
- **Primary:** `primary` fill, `on-primary` label. Hover `primary-hover`, active `primary-press`.
- **Secondary:** `surface` fill, `text` label, 1px `border`. Hover `surface-hover`.
- **Quiet:** transparent, `text-2`. Hover `surface-hover` with `text`. Icon-only quiet buttons are square and have a
  tooltip.
- **Danger:** `danger-solid` fill, `on-strong` label. Only in confirm dialogs that delete.
- **Disabled:** `surface-2` fill, `text-3` label, no hover, the reason as visible `meta` text beside it.
- **Kbd chip:** 18px tall, min-width 18px, padding 0 4px, `micro`, `rounded.xs`, letter centred. On a primary,
  as section 0 says; elsewhere `surface-2` with `text-2` and a 1px `border`.

**Input and select.** `control-lg` in forms, `control` in toolbars. `surface`, 1px `border-strong`, `rounded.sm`,
padding 0 10px, `ui` text, placeholder in `text-2`. Focus is the 2px `primary-border` outline. Invalid: a `danger-fg`
border, an alert icon and a `meta` message in `danger-fg` under the field; a warning (section 0) uses `warning-fg`.
Disabled is `surface-2` with `text-3`. A select is a native select styled the same, or a button that opens a
`rounded.md` popover with the `overlay` shadow, 32px rows and a check in `primary-text` on the chosen row. A
checkbox is 14px, `rounded.xs`, a `border-strong` outline. Checked, it fills `highlight` with a check in `surface`.

**Table and list.**
- Header 32px, sticky, `meta` 500 in `text-2`, bottom `border`. Rows 36px in `ui`. A table has 1px `border` lines
  between rows. A workbench list has none, and group headers separate it.
- Cell padding 0 12px; the first column starts 16px from the region edge. Numbers right-aligned in tnum, units in the
  header ("수량 (개)"). IDs and codes `text-2` tnum in the sans face. Long text truncates with an ellipsis and a
  `title`; a list row never wraps.
- **Cause mark.** In evidence tables the faulty field gets the state's `-bg` fill, a 1px `-fg` border, `-fg` text,
  and the word (section 0). Warning for a check, danger for an error.

**Tabs.** 28px text tabs, padding 0 10px, `ui-strong`. Selected is `surface-active`, `text`, `rounded.sm`, and a 1px
transparent outline that forced colours turn visible. Others `text-2`. No underline tabs. **Filters** are rail items
with counts in a workbench, and 24px "field: value" tokens with an × in a table's filter bar. A 0 count disables.

**Badge.** 20px, padding 0 6px, `rounded.xs`, `meta` 500. The state's `-bg` fill, `-fg` text, 1px `-border`, and an
optional 12px icon. Neutral is `surface-2`, `text-2`, `border`. Always words. Never a pill.

**Banner.** Inset 16px from the region's sides and 16px under its header. Padding 10px 12px, `rounded.sm`, 1px state
`-border`, state `-bg`. A 16px icon in the state `-fg` (alert-circle danger, alert-triangle warning, check-circle
success, info info). Message in `body` and the state `-fg`, first sentence at 500, detail in `text`. A secondary
recovery button at the right.

**Empty state.** One `body` line in `text-2`, aligned to the first column, and at most one button. No illustration
and no icon tile.

**Inspector and side panels.** `surface`, 1px `border` on the left, 44px header. Sections 24px apart, named in
`title`. Field grid: 112px label column (`ui`, `text-2`), value column (`ui`, `text`), 32px rows. An editable value
shows `surface-hover` on hover and opens a menu. Evidence tables use `row-compact` and a `meta` header.

**Measured contrast** (WCAG ratio on this frontmatter: 4.5 for text, 3 for UI).

| Pair | Ratio |
|---|---|
| `text` on `surface` / `canvas` / `surface-active` / `primary-wash-strong` | 17.01 / 15.47 / 14.01 / 13.60 |
| `text-2` on `surface` / `canvas` / `surface-active` / `primary-wash` / `primary-wash-strong` | 6.38 / 5.80 / 5.26 / 5.77 / 5.10 |
| `primary-text` on `surface` / `canvas` / `surface-active` / `primary-wash` | 5.47 / 4.97 / 4.51 / 4.94 |
| `on-primary` on `primary` / `primary-hover` / `primary-press`; `on-strong` on `danger-solid` | 4.70 / 5.81 / 7.21; 6.57 |
| `danger-fg` / `warning-fg` / `success-fg` / `info-fg` on own `-bg` | 5.91 / 5.82 / 4.84 / 7.00 |
| `text` on each state `-bg` | 15.18 to 15.65 |
| `border-strong` on `canvas` / `surface-active` / `primary-wash` | 3.50 / 3.17 / 3.53 |
| `primary-border` / `highlight` on `surface-active` (focus, done glyph on the cursor row) | 3.87 |
| `surface` on `highlight` (knocked-out check) | 4.70 |

**Derived main colours** (the host's `derive()` in `backend/justshipit/engineering/accent.py`, 23 pairs each). The
host's look check holds the build to the frontmatter; nothing in the product repo runs either.

| Main | `primary` / `on-primary` | `primary-text` | `highlight` | Tightest pair |
|---|---|---|---|---|
| #16a34a green | #00883a / white | #007a33 | #009942 | `primary-text` on `surface-active` 4.51 |
| #ea580c orange | #cf4b00 / white | #b84100 | #e85607 | `primary-border` on `surface-active` 3.00 |
| #facc15 yellow | #facc15 / `text` | #7f6600 | #a08100 | `primary-text` on `surface-active` 4.55 |
| #0f2e4f navy | #0f2e4f / white | #0f2e4f | #0f2e4f | `text-2` on `primary-wash-strong` 5.13 |

All pass. `text-3` fails on purpose and appears only on disabled controls. `border` is a decorative hairline.

## 5. Korean

The copy, number and date rules here apply only when the product's language is Korean; in another language, follow that language's own conventions. The size, type and length rules hold in every language.

- **Sizes.** `ui` 13/20, `body` 14/22, paragraphs over two lines 14/24. Hangul never below 12px; `micro` (11px) is
  for digits and Latin only. `word-break: keep-all; overflow-wrap: anywhere`, `text-wrap: pretty` on paragraphs.
- **Numbers.** Thousands commas (1,240). The counter word attaches to the number (5건, 312행), and English writes
  "5 items". Money is 12,000원, or ₩12,000 in English.
- **Dates.** Tables use 2026-11-05 08:28 in tnum. Sentences use "11월 5일(목) 오전 8:28" or "Nov 5, 8:28 AM".
  Relative time ("3분 전") appears only in `meta`, with the absolute time in the tooltip.
- **Length.** English runs longer, so widths come from content: never fix a button, tab or badge width. Key names
  stay English (Esc, Enter). **Voice:** 해요체, short; actions are verbs; errors say what happened and what to do.

## 6. Never

Round 1's tells:
1. One centred column of about 1000px floating in a wide window. Pick an archetype and let its regions fill it.
2. "Heading, then a sentence repeating the count, then a full table", per group. Use one list with group rows.
3. A bordered button at the far right of every row. Row actions are hover-revealed quiet icons after the text.
4. A dashed upload box on an empty page. Show the shell, and make the window the drop target.
5. An error drawn on a neutral surface. Every state uses its state tokens, its icon, and words.
6. Brand marketing carried into the app: a near-black canvas, hero type, gradients, glow, logo-like marks.

This style's own ways to go wrong:
7. An inspector that opens on a summary while items exist. It opens on the first item's evidence.
8. Grey on grey with no ground. `canvas` and `surface` must differ, and states are coloured.
9. Accent creep onto headings, icons, group names, borders or badges. The accent keeps its roles only.
10. `text-3` for anything someone must read. Hints and placeholders use `text-2`.
11. Shadows on resting surfaces, cards in a list, nested panels, pills, thick left borders, monospace outside code.
12. Shortcuts that exist only on a help page, fire while the user types, or serve a feature the brief lacks.
13. Rows that vanish or jump when handled, confirm dialogs for reversible changes, modals a panel could hold.
14. Spinners on blank regions, full-page success screens, Unicode or emoji standing in for icons or the glyph.
