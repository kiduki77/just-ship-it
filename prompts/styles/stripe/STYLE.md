---
name: precise-ledger
description: A precise data style for working screens where numbers decide things. Navy ink on white, one accent for action and selection, tabular figures everywhere, status counts that double as the filter, one well-set table per set of records, a detail drawer per record.
accent-derive: { main: "#533AFD", canvas: "#EBEEF1", ink: "#0D253D" }

colors:
  ink: "#0D253D"
  ink-2: "#3C4F69"
  ink-3: "#5E6E86"
  ink-disabled: "#A3ACBA"
  canvas: "#FFFFFF"
  canvas-soft: "#F6F9FC"
  neutral-bg: "#EBEEF1"
  hairline: "#E3E8EE"
  border-control: "#7D8A9C"
  primary: "#533AFD"
  primary-hover: "#4921EB"
  primary-press: "#3F00D5"
  on-primary: "#FFFFFF"
  on-strong: "#FFFFFF"
  primary-text: "#533AFD"
  primary-wash: "#F0F3FF"
  primary-wash-strong: "#E0E5FF"
  primary-border: "#533AFD"
  highlight: "#533AFD"
  on-dark-2: "#C3CBD8"
  danger-fg: "#B3093C"
  danger-bg: "#FFE7EF"
  danger-border: "#D6456C"
  danger-solid: "#DF1B41"
  warning-fg: "#A82C00"
  warning-bg: "#FDF0C8"
  warning-border: "#C9700A"
  success-fg: "#006908"
  success-bg: "#D7F7C2"
  success-border: "#3D9A50"
  info-fg: "#34495F"
  info-bg: "#EDF1F6"
  info-border: "#7D8A9C"
  series-2: "#0A82C2"
  series-3: "#EA2261"
  series-4: "#9B6829"
  series-compare: "#7D8A9C"

typography:
  sans: "'Geist Variable', 'Geist', 'Pretendard Variable', 'Pretendard', 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif"
  mono: "'Geist Mono Variable', 'Geist Mono', 'Pretendard Variable', 'Pretendard', monospace"
  numerals: "font-variant-numeric: tabular-nums on :root (every number in the product is tabular)"
  page-title:    { size: 24px, weight: 600, lineHeight: 32px, letterSpacing: -0.01em }
  figure-lg:     { size: 32px, weight: 400, lineHeight: 40px, letterSpacing: -0.02em }
  figure-md:     { size: 24px, weight: 400, lineHeight: 32px, letterSpacing: -0.01em }
  figure-sm:     { size: 20px, weight: 400, lineHeight: 28px, letterSpacing: -0.01em }
  section-title: { size: 16px, weight: 600, lineHeight: 24px }
  body:          { size: 14px, weight: 400, lineHeight: 22px }
  body-strong:   { size: 14px, weight: 600, lineHeight: 22px }
  label:         { size: 13px, weight: 600, lineHeight: 20px }
  small:         { size: 13px, weight: 400, lineHeight: 20px }
  caption:       { size: 12px, weight: 500, lineHeight: 16px }
  code:          { family: mono, size: 13px, weight: 400, lineHeight: 20px }

rounded:
  xs: 4px     # badges, marked cells, checkboxes
  sm: 6px     # buttons, inputs, selects, menus, banners, count segments, nav items
  lg: 12px    # modal dialog only
  pill: 9999px  # filter chips only

spacing:
  xxs: 2px
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
  xxl: 32px
  xxxl: 48px

density:
  row: 40px
  row-form: 48px
  evidence-row: 32px
  table-header: 36px
  control: 32px
  control-sm: 28px
  topbar: 56px
  rail: 232px
  count-segment: 64px
  inspector: 480px
  form-drawer: 560px
  side-column: 336px
  page-gutter: 32px

elevation:
  level-1: "0 1px 2px rgba(13,37,61,0.08)"        # buttons, inputs
  level-2: "0 8px 24px rgba(13,37,61,0.12), 0 2px 6px rgba(13,37,61,0.06)"  # menus, form drawer, toast, modal
  scrim: "rgba(13,37,61,0.40)"                     # modal and form drawer only
---

# Precise ledger

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

Calm, exact, and quick to read. Every figure lines up, every status has a word, and the screen tells you where to look before you go looking.
The page is white with navy ink. The accent appears only where you can act or where you have chosen something. Big numbers are set large and light, labels small and firm.
It feels like the finance tool you already trust, applied to whatever the business counts: orders, bookings, tickets, stock.

What the source language contributes:
- **Type.** A neutral grotesk, with large figures set at regular weight and slight negative tracking (never bold numbers), and tabular figures on every number.
- **Palette.** Deep navy ink instead of black, one accent for action and selection, a cool off-white for secondary surfaces, and a full state layer (the source had none, so it is built here and measured).
- **Density.** Dashboard density. 40px rows, 32px controls, modules separated by headings and hairlines rather than boxed in cards.
- **Signature move: the count strip.** The summary numbers are the filter. A row of status segments sits above the table, each with a label and a count. Clicking one filters the table, and the segment stays selected. There is no separate summary block and no count chips. When a record is handled, its segment's count ticks down in place, so progress is the strip itself.

What we do not take: logos, the brand name, marketing copy, the pastel gradient mesh, the proprietary typeface, pill-shaped buttons, weight-300 body text (too faint for Hangul), composited product mockups, and dark "product mockup" panels.

## 2. Structure

Pick the archetype by the product's core task, not by habit. Most products use A with its drawer, and D for import or setup.

| Core task | Archetype |
|---|---|
| Find and act on records in a set (exceptions, orders, tickets, bookings, stock lines) | A. Record list |
| Understand one record fully (history, related items, source rows) | B. Record detail |
| Check how a period went and what drove it (weekly report, sales summary) | C. Period report |
| Bring data in, map it, create or edit a record, change settings | D. Focus task |

### Shell (all archetypes)

- **Three or more destinations:** left rail, 232px, `canvas-soft`, 1px `hairline` right edge. Product name `body-strong` in a 56px top row. Nav items 32px tall, 16px drawn icon plus `body` text in `ink-2`, radius `sm`. The selected item gets `primary-text` on `primary-wash`.
- **One or two destinations:** no rail. A 56px top bar on `canvas` with a 1px `hairline` bottom. Product name `body-strong` at the left, then a 1px vertical hairline, then the working context in `small` `ink-3` (file name, period, location). Help and account sit at the right only when the brief has them; never a placeholder avatar or icon.
- **Content** fills the rest of the window with 32px gutters. Do not center a narrow column, and do not cap the table width. At 1440 without a rail and with no drawer, the table is 1376px wide.
- **Page header:** 24px above. Title row 40px tall: `page-title` left, page actions right (secondary buttons first, then the one primary button at the far right). Under the title, one context line in `small` `ink-3` (source, period, "updated 09:59") that never repeats a count shown elsewhere. 24px below the header.

### A. Record list

Use when the user works through a set of records. This is the default for reports whose point is a list of things to check.

Regions, top to bottom at 1280 to 1600:
1. **Page header.** The title is the brief's headline when it has one, used as written. Otherwise it states the set in one phrase ("오늘 출고 예외") and leaves counts to the strip.
2. **Count strip**, 64px tall, 24px below the header (the shell's gap): `display: grid; grid-template-columns: repeat(N, minmax(128px, 200px)); gap: 8px`, at most 6 segments, left-aligned.
   - First the action set: the records still waiting, labelled with the brief's noun ("남은 예외"). Model (c) has no action set.
   - Then one segment per category, each counting that category's waiting records.
   - Then the handled set, labelled by the verdict ("문제 아님").
   - Last, "예외 전체" (every record in the set), shown only once something is handled, because before that it equals the action set. Never a bare "전체", which reads as every row of the source.
   - The action segment may carry the same number as the title at load. That is the one allowed repeat, because the segment ticks down and filters. No third place shows it, so there is no receipt line: the strip carries the handled count and the export label the output count.
3. **Search and filters.** Only what the brief has. A lone search (240px, search icon, `/` focuses it) sits at the right end of the strip row. Filter chips, column settings or a bulk bar get their own 32px bar, 16px below the strip.
4. **Table**, 12px below, sticky header.
5. **Table footer**, 48px, only above 200 rows: "312건 중 1–50" in `small` `ink-3` at the left, previous and next at the right, 50 per page. Up to 200 rows, render all and show no footer.
6. **Record drawer.** A 480px column at the content's right, in the layout (`grid-template-columns: minmax(0, 1fr) 480px`), full height under the top bar, its own scroll, non-modal. With 20 or fewer records in the action set, the first one is open at load, so evidence fills the right side. Clicking a row or Enter opens a record; the opened row stays `primary-wash` with a 2px inset `primary-border` outline; ↑/↓ moves the drawer to the next record. Use B instead when the record has more than three modules of content.
7. **Checked module** (below a table shorter than the viewport): `section-title` "확인한 내용", a 1px `hairline`, then one 40px row per check with its name, what it looks for in `small` `ink-3`, and its count, zero included, and a last row for the rest ("나머지 307건은 문제 없어요").

**Columns when the drawer is open.** Each column is given a priority at build time: 1 for identity, status and any column that holds a marked cell; 2 for main text; 3 for the rest. While the table is narrower than its columns' minimum widths, priority 3 columns hide first, then 2. Priority 1 never hides and nothing squeezes below its minimum width or scrolls sideways.

First screen: the strip with the action segment selected (never "예외 전체" while something waits), the table filtered to it, and the first record's drawer open. When nothing needs action, every count reads 0, no segment is selected, the table is replaced by a success banner above the strip ("확인할 예외가 없어요. 규칙 3개로 312건을 확인했어요."), and the checked module follows it.

Where actions live: the page header holds the one action on the whole set (export, new record, new file). The action on one record lives in its row (see Interaction) and in the drawer footer.

### B. Record detail

Use for a record that has history, related records, or source data worth reading.

1. Breadcrumb in `small`. The parent list is a link ("출고 예외 /"). Do not repeat the title as the last crumb.
2. **Title row.** The record's key figure or identifier in `figure-lg`, the status badge on the same baseline 12px to its right, and actions at the far right (primary rightmost, secondary to its left, the danger action inside a ⋯ menu at the left end of the group).
3. **Fact strip**, 16px below. Up to 5 facts in one row, each a `caption` `ink-3` label over a `body` `ink` value, separated by 1px vertical `hairline` rules with 24px padding on each side.
4. **Two columns**, 32px gap. The main column flexes and holds modules: the evidence (section 0) first, then timeline or line items. The 336px side column holds "세부 정보" as a property list (label `small` `ink-3` above value `body`, 16px between pairs), with a copy button on IDs.
5. A module is a `section-title` heading with optional actions at its right, a 1px `hairline` 8px below, then content. 32px between modules. No card boxes, no shadows on modules.

### C. Period report

Use when the core task is reading how a period went (model c). Each headline number must move over time. A number without history belongs in a count strip, not here.

1. Page header. Under the title, a period select ("지난 7일") and a compare select ("이전 기간과 비교"), each 32px. Export is the primary action at the right.
2. **Metric row.** 2 to 4 metric modules in equal columns with 24px gaps, no boxes, separated from the table by a 1px `hairline` with 24px space on each side. Each module stacks: label in `label` `ink-2`; value in `figure-md`; a delta line in `small` with a drawn up or down icon, "+12.4%", and "지난주 대비" in `ink-3`, coloured `success-fg` or `danger-fg` only when the direction is good or bad for the business, else `ink-2`; a 72px line chart (current period a 2px `highlight` line, compare period a 1.5px dashed `series-compare` line, no fill, start and end date labels only).
3. Clicking a metric selects it (2px `primary-border` underline under the label) and scopes the table below to what drives it.
4. The table, as in A.

### D. Focus task

Use to bring data in (upload, map columns), to create or edit a record, or for settings.

- **Create or edit one record:** the form drawer, 560px from the right, with `scrim` over the page. Header 56px: title left, close (×) right. Fields in one column. Footer 64px with a top `hairline`: secondary "취소", then the primary at the far right.
- **Import steps** take the content area inside the shell. The page header carries the step title ("열 확인") and, during import only, a step line under it ("1 파일 고르기 · 2 열 확인 · 3 결과"): current step `body-strong` `ink`, others `ink-3`. Finished steps are links only when the brief lets the user go back. The step line disappears once the result shows. The primary ("리포트 보기") sits at the header's right, disabled until required inputs are valid, with the reason beside it in `small` `ink-3`.
- **Column mapping** is section 0's content as a table: 필요한 칸 (220px, the field in `body-strong` and a "필수" neutral badge), 파일의 열 (a 240px select), 파일에서 본 값 (fluid, two masked samples joined by " · " in `ink-2`), 채워진 행 (right-aligned, "310 / 312", with "빈칸 2행" in `warning-fg` under it when short). Rows are `row-form` (48). Two group header rows (36px, `canvas-soft`, `label`): "꼭 골라야 하는 열 3 / 3" and "더 정확하게 보기 (선택)" with the brief's reason in `small` `ink-3`. On optional fields only, the select's last option, after a divider, is the brief's "이 파일에는 없어요"; choosing it turns the field's sample and count cells into "–" in `ink-3`. A mapped required field gets a 1px `primary-border` outline on its select. A 336px side column lists the file facts (name, rows, encoding with a quiet "다른 인코딩으로 다시 읽기") and what each check uses.
- **Upload with no data yet** is not a page of its own. It is archetype A's empty state (see Interaction).

## 3. Interaction

**Keyboard** (section 0 sets core and optional). Core: ↑/↓ move the row focus and the open drawer with it. Enter opens the focused record; if the drawer already shows it, Enter moves focus to the drawer's verdict button. Esc returns focus from the drawer to its row, then closes menus or the modal. The verdict letter (D for "문제 아님", from dismiss) works on the focused row and in the drawer, shown in the button's tooltip and as a `caption` key chip in the drawer button. Optional: j/k, x for the row checkbox (only with bulk actions), `/` for search (only with search). The focused row shows a 2px `primary-border` outline inset on the row. Tab order follows the visual order.

**Row actions.** The verdict users take on almost every record appears in the row as a quiet button in its own column right after the last text column, visible on hover and row focus and always on the opened row. Every other row action goes in a ⋯ menu (28px icon button) beside it. Never a permanent bordered button on every row.

**Selection and bulk** (only with bulk actions). A 40px checkbox column. When rows are checked, the filter bar is replaced in place by the bulk bar: "3건 선택됨" in `body-strong`, the bulk actions as secondary buttons, the destructive one last, and "선택 해제" as a quiet button at the right.

**Progress toward done.** A handled record does not disappear. Its badge changes ("확인 필요" to "문제 아님", neutral, per section 0). The row stays in place until the filter is changed, then leaves the action segment. The strip's counts update at the same moment. In model (a), the action segment reaching 0 shows a drawn check in `success-fg` before its label. In model (b) the export's label count updates ("예외 3건 내보내기") and nothing claims completion.

**Undo, not confirm.** Reversible actions apply at once and raise a toast with "되돌리기". A confirm dialog is only for losing work undo cannot restore (a new file replacing records with verdicts), deleting, or sending to other people. Downloading an export is not sending. The dialog is 440px, radius `lg`, level-2 shadow, over the `scrim`. The title is the question ("12건을 삭제할까요?"), the body names the consequence in one sentence, and the buttons are secondary "취소" and the action naming its count ("12건 삭제", danger-solid when destructive).

**States always appear in the region they concern**, never as a box floating on an empty page. Each state has at most one action in the table body.
- **Empty, nothing yet:** the real shell. The strip shows "–" for each count, and the table shows the column headers the product expects. The table body holds the empty state with the brief's action for getting data ("파일 고르기"). The whole window is the drop target (the brief's "upload area"): on drag-over the table body gets a 1px dashed `primary-border` border and a `primary-wash` fill. A one-line privacy note sits under the action in `small` `ink-3` with a drawn lock icon.
- **Empty, filtered out:** "필터에 맞는 결과가 없어요." and a quiet "필터 지우기". Never a "create first" action when records exist.
- **Empty, clean result:** the success banner above the strip and the checked module. No action in the table body.
- **Loading:** skeleton rows in the table (8 rows of `neutral-bg` bars 12px tall at the real column widths, shimmer off under reduced motion), and `neutral-bg` 48px bars in place of the strip's counts. For parsing a file, a 2px `highlight` progress line on the table's top edge plus "파일을 읽고 있어요" in the table body. Spinners only inside a pending button (14px, replaces the icon, label stays).
- **Error:** a danger banner at the top of the content it concerns (above the strip for a page-level failure, above the fields for a form), per section 0's Errors rule. Its recovery is a secondary button inside the banner; the table body shows section 0's one muted line ("읽은 파일이 없어요") instead of a second picker.
- **Success:** a toast for an action the user just took. A success banner only for a result the user came to see.

**Toast.** Bottom center, 24px above the window edge, `ink` background, `on-strong` text in `body`, radius `sm`, level-2 shadow, at most about 30 characters, one optional action in `on-strong` 600 with an underline. 5 seconds, 8 with "되돌리기". Hover pauses it. One at a time. Errors are never toasts.

**Motion.** Hover and colour changes take 120ms ease-out. The drawer slides in 200ms with `cubic-bezier(0.16,1,0.3,1)`. The one authored moment is the count strip: a changed count slides up and out while the new one slides in (240ms, same curve), only on segments whose node persists (section 0). With `prefers-reduced-motion`, everything swaps instantly.

## 4. Components

**Buttons.** 32px tall (28px small), radius `sm`, `body-strong`, 12px horizontal padding, 16px leading icon with 6px gap, minimum width 64px, sized to the label.
- Primary: `primary` fill, `on-primary` text, level-1 shadow. Hover `primary-hover`, active `primary-press`. One per region (page header, drawer footer, banner).
- Secondary: `canvas` fill, 1px `border-control`, `ink` text, level-1 shadow. Hover `canvas-soft`.
- Quiet: no fill or border, `primary-text`. Hover `primary-wash`. For row actions, "필터 지우기", and inline links that act.
- Danger: secondary geometry with `danger-fg` text and a `danger-border` border. Danger-solid (`danger-solid` fill, `on-strong` text) only as a dialog's confirm button.
- Disabled: `neutral-bg` fill, `ink-disabled` text, no shadow, the reason stated next to it.
- Key chip: 18px tall, min-width 18px, padding 0 4px, `caption`, radius `xs`, the letter centred, 8px after the label. On a primary, `primary-press` with `on-primary` (section 0); on a secondary or in a tooltip, `neutral-bg` with `ink-2`.
- Focus on any control: `outline: 2px solid primary-border; outline-offset: 2px`.

**Input and select.** 32px (48px rows in mapping keep the 32px control), radius `sm`, `canvas` fill, 1px `border-control`, 10px padding, `body` text, placeholder in `ink-3`. Label above in `label` `ink` with a 6px gap. Help text below in `small` `ink-3`. Focus: border `primary-border` plus the focus outline. Invalid: border `danger-border`, message below in `small` `danger-fg` with a 14px drawn alert icon. A warning (section 0) is the same in `warning-border` and `warning-fg`. A select has a 16px chevron in `ink-3` and uses the native `<select>` or a listbox (level-2 shadow, 32px options, a check in `primary-text` on the chosen one). A checked checkbox fills `highlight` with a `canvas` check.

**Filter chip.** 28px, radius `pill`, `small`. Suggested: 1px dashed `border-control`, `ink-2`, a 14px plus icon, opens a menu. Active: 1px solid `border-control`, the label in `ink-2`, a hairline divider, the value in `primary-text` `body-strong`, and a 14px × that clears it. Labels match the column headers they filter.

**Count strip segment.** 64px tall, radius `sm`, 1px `hairline` border, `canvas` fill, padding 10px 14px. The label in `label` `ink-2`, the count below in `figure-sm` `ink`. Hover: border `border-control`. Selected: border `primary-border` plus `outline: 1px solid primary-border; outline-offset: -2px` (2px total, survives forced colours), label and count in `primary-text`. A waiting category's count is `danger-fg` when its records are blocking (section 0), otherwise `warning-fg`, while above 0, and the label always says what it is. A `button` with `aria-pressed`.

**Table.**
- Header row: 36px, `canvas`, `label` in `ink-3`, 1px `hairline` bottom, sticky. Sortable headers show a 12px arrow on hover and keep it when sorted.
- Rows: 40px, `body` `ink`, 1px `hairline` between rows, 16px cell padding (12px between narrow numeric columns). Hover `canvas-soft`. Opened or checked rows `primary-wash`.
- The first column is the record's identity in `body-strong`. Numbers right-aligned with right-aligned headers. Dates tabular and left-aligned. System IDs people copy use `code` in `ink-2`. Status is a badge column right after the identity; a severity badge (section 0) follows the state badge in the same cell.
- Cells never wrap. They truncate with an ellipsis and a tooltip. Fixed widths for numbers, dates and badges; the longest text column flexes.
- **Marked cell.** The value that caused a status gets `danger-bg` (or `warning-bg`) fill, a 1px inset `danger-border` (or `warning-border`), and the matching `-fg` text, radius `xs`, inset 4px. An empty value is the word "빈칸". Rows are never tinted by status.
- **Evidence table** (drawer and B): `evidence-row` rows, `caption` header, the file's row numbers in `ink-3` in a 48px first column, the record's row in `primary-wash`, marked cells as above. A duplicate group puts fields as rows and each record as a column.

**Badge.** 20px tall, radius `xs`, padding 0 6px, `caption`, a 12px leading drawn icon, always with a word.
- Neutral: `neutral-bg` with `ink-2` (dismissed, draft). Info: `info-bg` with `info-fg` (in progress, a note). Success: `success-bg` with `success-fg` (fixed, matched). Warning: `warning-bg` with `warning-fg` (needs a look). Danger: `danger-bg` with `danger-fg` (needs action, failed).
- Urgent: `danger-solid` with `on-strong`, only for a state the brief calls blocking, at most one per screen.

**Banner.** Content width, radius `sm`, the state's `-bg` fill and 1px `-border`, padding 12px 16px. A 16px drawn icon in the state's `-fg`, the title in `body-strong` `ink`, the body in `body` `ink-2`, an optional action button at the right. Dismissible only for info. At most two stacked, danger first.

**Empty state.** Inside the table body or module, centered in a 240px-tall area. Title in `body-strong` `ink` stating what is missing ("아직 올린 파일이 없어요."). One sentence under 14 words in `small` `ink-3` saying when data appears. At most one action, whose label matches the title. A 20px drawn icon in `ink-3` above the title is allowed; no illustration.

**Tabs.** For sections inside a page, not for status filtering. 40px tall, `body` 500, `ink-2`, 24px gap, a 1px `hairline` under the row. Selected: `primary-text` plus a 2px `primary-border` bottom border.

**Record drawer.** 480px, `canvas`, 1px `hairline` left edge, no shadow (it is in the layout).
- Header 56px: the identifier in `section-title`, its badges, and up/down and close icon buttons at the right.
- Body (24px padding): what is wrong in one sentence, then the evidence (section 0) under the `label` "파일에서 보면", then the record's fields as a property list, left out after a group table that already shows every field.
- Footer 64px with a top `hairline`: the verdict as the primary at the far right with its key chip.

**Charts.** Horizontal gridlines only, 1px `hairline`. Axis labels in `caption` `ink-3`. Series in order `highlight`, `series-2`, `series-3`, `series-4`; the compare period always dashed `series-compare`. Legends are inline labels, never a box. Tooltip on `ink` with an `on-strong` value and an `on-dark-2` label. At most 4 series.

**Browser defaults.** `::selection` is `primary-wash`, the caret `primary-text`, scrollbars `hairline` thumbs on `canvas`.

## 5. Korean

The copy, number and date rules here apply only when the product's language is Korean; in another language, follow that language's own conventions. The size, type and length rules hold in every language.

- **Type.** Hangul from Pretendard Variable (npm `pretendard`, `dist/web/variable/pretendardvariable-dynamic-subset.css`). Latin and digits from Geist (`@fontsource-variable/geist`, mono from `@fontsource-variable/geist-mono`), which is why Geist leads the stack. Body stays 14/22. Nothing below 12px. Headings track -0.01em at most; Hangul body is never tracked. Weights 400, 500, 600 only.
- **Line breaks.** `word-break: keep-all; overflow-wrap: anywhere` on all text. `text-wrap: balance` on titles, `pretty` on sentences.
- **Numbers.** Thousands separators always ("1,234"). Counts take the unit with no space ("312건", "5개"). Money "12,300원". Percent "12.5%". A negative value uses a true minus ("−3"). Never abbreviate in tables; "1.2만" only on chart axes.
- **Dates.** Tables use "2026-11-05 08:28", or "11-05 08:28" when every row shares the year. Prose uses "11월 5일(수) 08:28". Relative time ("3분 전") only under an hour, with the absolute time in a tooltip. 24-hour time.
- **Copy.** Natural 해요체. Controls name their action ("문제 아님", "내보내기", "다시 고르기"). Errors name the problem, then the fix. Size every control to its label, because English runs 30 to 60 percent longer.

## 6. Never

From round 1 (these made screens look AI-made):
- A centered column around 1000px wide in a 1440 window with empty margins.
- A heading, then a sentence repeating the count, then a full table, repeated per category. One table plus the count strip replaces all of it.
- A row of count chips under the title that duplicates the content below.
- A permanent bordered action button at the far right of every row.
- A dashed upload box floating on an empty page.
- A small label above a heading, section numbers, or same-size cards as page structure.

This style's own ways to go wrong:
- A full-width table of five rows over a blank half screen. Open the first record's drawer and add the checked module.
- A bare "전체" segment, or a segment that always equals another one.
- Search, filter chips, column settings or a help icon the brief never asked for.
- Grey bars for hidden values. They read as loading; write the word.
- The gradient mesh or any gradient, glow, or glass. Pill-shaped buttons. Bold figures.
- The accent as decoration, body text, or a heading colour.
- Status shown by colour alone, rows tinted by status, or a coloured left border on rows, alerts, or cards.
- Metric tiles for numbers with no history, a hero number, or decorative sparklines.
- Modules boxed as shadowed cards, or cards inside cards. More than one primary button in a region.
- Left-aligned numbers, proportional figures, wrapped table cells, or mixed date formats on one screen.
- Toasts for errors, confirm dialogs for reversible actions, or a full-page spinner.
- Unicode glyphs or emoji as icons. One drawn set (Lucide) at a 1.5px stroke, 16px in controls and 12px in badges.

### Measured contrast

WCAG 2.x ratios of every pair below, measured on this frontmatter. All pass.

| Pair | Ratio | Need |
|---|---|---|
| ink on canvas / canvas-soft / primary-wash / primary-wash-strong | 15.57 / 14.73 / 14.06 / 12.47 | 4.5 |
| ink-2 on canvas / canvas-soft / neutral-bg / primary-wash / primary-wash-strong | 8.35 / 7.90 / 7.17 / 7.54 / 6.69 | 4.5 |
| ink-3 on canvas / canvas-soft / primary-wash | 5.18 / 4.91 / 4.68 | 4.5 |
| primary-text on canvas / canvas-soft / primary-wash | 6.19 / 5.86 / 5.59 | 4.5 |
| on-primary on primary / primary-hover / primary-press; on-strong on danger-solid | 6.19 / 7.78 / 9.67; 4.80 | 4.5 |
| on-strong / on-dark-2 on ink (toast, tooltip) | 15.57 / 9.53 | 4.5 |
| danger-fg / warning-fg / success-fg / info-fg on own bg | 5.93 / 6.11 / 5.94 / 8.18 | 4.5 |
| danger-fg / warning-fg / success-fg / info-fg on canvas | 6.95 / 6.94 / 6.94 / 9.27 | 4.5 |
| ink-2 on each state bg | 7.13 to 7.36 | 4.5 |
| border-control on canvas / canvas-soft | 3.51 / 3.32 | 3 |
| primary-border on canvas / canvas-soft / primary-wash | 6.19 / 5.86 / 5.59 | 3 |
| highlight on canvas, and canvas on highlight (knocked-out check) | 6.19 / 6.19 | 3 |
| danger / warning / success border on canvas | 4.26 / 3.62 / 3.54 | 3 |
| danger / warning border on own bg (marked cells), info border on info bg | 3.64 / 3.18 / 3.09 | 3 |
| series-2 / series-3 / series-4 / compare on canvas | 4.21 / 4.29 / 4.77 / 3.51 | 3 |

**Derived main colours.** The host derives the roles (`derive()` in `backend/justshipit/engineering/accent.py`, with this pack's `accent-derive`) and its look check holds the build to the frontmatter; nothing in the product repo runs either. 18 pairs each, all pass.

| Main | `primary` / `on-primary` | `primary-text` | `highlight` | Tightest pair |
|---|---|---|---|---|
| #16a34a green | #00883a / white | #007c34 | #089e45 | `on-primary` on `primary` 4.58 |
| #ea580c orange | #cf4b00 / white | #bc4300 | #ea580c | `on-primary` on `primary` 4.51 |
| #facc15 yellow | #facc15 / `ink` | #836900 | #a58600 | `ink-3` on `primary-wash` 4.68 |
| #0f2e4f navy | #0f2e4f / white | #0f2e4f | #0f2e4f | `ink-3` on `primary-wash` 4.68 |

`hairline` (1.23 on canvas) is decorative only, for dividers and row rules. It never marks a control's edge. `ink-disabled` is used only for disabled controls, which WCAG exempts.
