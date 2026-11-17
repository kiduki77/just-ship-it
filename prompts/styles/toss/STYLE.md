---
version: 2
name: spoken-answer-decision-ui
description: "Light working-app style. Each screen opens with one spoken sentence that holds its key number, white rounded sections sit on a cool grey ground, the accent is kept for the one next action, rows are generous, and a sticky decision panel holds the primary button."
accent-derive: { main: "#3182F6", canvas: "#E5E8EB", ink: "#191F28" }

colors:
  # ground and surfaces
  canvas: "#F2F4F6"          # page ground behind every section
  surface: "#FFFFFF"         # sections, panels, dialogs
  surface-sunk: "#F9FAFB"    # row hover, table header band, read-only value wells
  hairline: "#E5E8EB"        # row dividers inside a section (decorative, never the only cue)
  border-strong: "#8B95A1"   # input, select, checkbox boundaries (white surfaces only)
  # text
  text: "#191F28"            # values, headlines, row titles
  text-2: "#4E5968"          # sentences under a headline, labels, banner body
  text-3: "#636E7C"          # metadata, column heads, placeholders, row-sub
  text-disabled: "#B0B8C1"   # disabled labels (exempt from contrast, always paired with a reason)
  # accent roles (derived from the main colour; section 0)
  primary: "#1F72E5"
  primary-hover: "#0662D4"
  primary-press: "#0054BB"
  on-primary: "#FFFFFF"
  on-strong: "#FFFFFF"        # label on danger fills and filled badges (fixed, not derived)
  primary-text: "#0662D3"
  primary-wash: "#EDF4FF"
  primary-wash-strong: "#D8E8FF"
  primary-border: "#3182F6"
  highlight: "#3182F6"
  # state layer: fg is text and icons, bg is the tint, border marks a field, a cell or the current item in that state
  danger:
    fg: "#D22030"
    bg: "#FFEEEE"
    border: "#F04452"
    fill-hover: "#BC1B2A"    # danger button hover (fill is danger.fg)
  warning:
    fg: "#A64B00"
    bg: "#FFF9E7"
    border: "#D96A00"
  success:
    fg: "#027648"
    bg: "#F0FAF6"
    border: "#029359"
  info:
    fg: "#3A4555"
    bg: "#EEF1F5"
    border: "#7B8594"
  # overlays
  toast: "#191F28"           # toast fill; dialog scrim is this at 48% opacity
  on-toast: "#FFFFFF"

typography:
  family: '"Pretendard Variable", Pretendard, "Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", system-ui, sans-serif'
  source: "npm pretendard (SIL OFL 1.1), import the Pretendard Variable dynamic-subset CSS from the package so the bundler ships the woff2. Latin comes from the same face."
  numbers: 'font-variant-numeric: tabular-nums on every figure, count, ID, date, money'
  headline:   { size: 30px, line: 40px, weight: 700, tracking: -0.02em }   # one per screen: the spoken answer
  title:      { size: 22px, line: 31px, weight: 700, tracking: -0.02em }   # panel title, dialog title, detail headline
  figure:     { size: 26px, line: 35px, weight: 700, tracking: -0.02em }   # the overview's one big figure
  section:    { size: 18px, line: 26px, weight: 700, tracking: -0.01em }   # section header inside a card
  body:       { size: 16px, line: 24px, weight: 400, tracking: -0.01em }   # sentences, row titles (row titles at 500)
  row-sub:    { size: 14px, line: 21px, weight: 400, tracking: 0 }         # second line of a row, evidence cells
  label:      { size: 14px, line: 21px, weight: 600, tracking: 0 }         # field labels, segmented control, small buttons
  button:     { size: 16px, line: 24px, weight: 600, tracking: -0.01em }
  caption:    { size: 13px, line: 19.5px, weight: 400, tracking: 0 }       # "can skip": column heads, hints, timestamps
  badge:      { size: 13px, line: 18px, weight: 600, tracking: 0 }

rounded:
  sm: 8px      # badges, 32px row buttons, marked cells, skeleton bars
  md: 12px     # 40/52px buttons, inputs, selects, icon tiles, toasts, evidence tables
  lg: 20px     # sections, panels, dialogs, the current item
  full: 9999px # progress track, status dots only

spacing: [4, 8, 12, 16, 20, 24, 32, 40, 48, 64]

density:
  topbar: 64px
  page-gutter: "32px below 1440px wide, 40px at 1440px and above"
  content-max: 1520px
  section-padding: "24px sides, 20px top"
  row: 56px            # one-line row
  row-two-line: 72px   # title + row-sub
  table-row: 52px
  evidence-row: 40px   # source rows inside the current item
  table-head: 40px
  control: 40px        # default button, input 48px, select 48px
  control-cta: 52px    # the decision panel's primary button
  row-button: 32px
  icon-tile: 40px      # 20px icon inside
  panel: "clamp(360px, 26vw, 420px)"
  list-pane: 440px
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

# World

Calm, plain, and sure. The screen talks to one person in short spoken sentences and asks for one thing at a time. Space and size do the work that borders and ornament do elsewhere.

The source language lends four things. Type is one neutral Korean sans at large, confident sizes with slight negative tracking, set in a fixed role scale where each role also says how much it must be read (caption means "can skip"). The palette is a cool grey ground under white sections, near-black text in three tested greys, and one accent that marks the next action, the key number, and focus. Density is generous: 56px rows, 24px section padding, and a 16 to 24px rhythm between groups. The signature move is **the spoken answer**: every screen opens with one headline sentence in 30px bold that states the answer with its one number in `highlight`, such as "출고 312건 중 **5건**을 확인해 주세요". The sentence rewrites itself as the person acts, and the primary button names the consequence with its own count ("예외 3건 내보내기"). When the brief gives the headline, its words are used and only the number is set in `highlight`.

We do not take the source's logo, its proprietary typeface, 3D or emoji illustration set, marketing scenes with 40px corners, bottom sheets, or any product or brand name. Nothing here may render a brand name.

# Structure

Every screen shares one frame. A white top bar (64px, hairline bottom) holds the product name at left, the step names next to it during a step flow, and file metadata at right. Step names: the current one in `label` `text`, finished ones in `label` `text-2`, later ones in `label` `text-3`, joined by a 16px chevron icon in `text-3`. They are links only when the brief lets the person go back. Help or account appear only when the brief has them. Below the bar the canvas fills the window. Content spans the full width minus the page gutter, capped at 1520px (1360px at 1440 wide). There is no centered column narrower than that.

Pick the archetype by the product's core task verb, never by habit. The same frame also carries the first-open, loading, and error states of that archetype.

| The person's task | Archetype |
|---|---|
| Review a short set of items, then commit one thing (print, send, export, publish, book) | Decision page |
| Work through many items one by one (requests, exceptions over days, a digest) | Queue |
| Give information before anything exists (first upload, setup, mapping, a booking form) | Step flow |
| Check how things stand, regularly (weekly report, today's schedule, sales) | Overview |

**Height and empty space.** Sections and panes are as tall as their content. No pane gets a viewport height unless it scrolls a list (the Queue). Content starts at the top of its section and is never centred vertically in a tall box. Below the last section the canvas simply ends. Where a section would be short, add the evidence or the checked-and-clean lines from section 0, never filler.

## Decision page

Use it when there are about 20 items or fewer before the commit. A product with an upload opens in the Step flow and lands here.

- **Grid.** `grid-template-columns: minmax(0, 1fr) clamp(360px, 26vw, 420px)`, gap 24, `align-items: start`. At 1440 the main column is about 956px wide and the panel 375px.
- **Main column, top.** The spoken answer sits directly on the canvas, not in a card. Headline, then one `body` sentence in `text-2` that says what to do, then a `caption` meta line (file name, row count, loaded time). 32px of space above and 24px below.
- **Main column, groups.** One white `lg` section per group (a rule, a category, a day). The header row has a 40px icon tile in the group's state tint, the group name in `section`, and the count as a plain figure beside the name ("주소 빈칸 2건"). One `row-sub` sentence of what to do follows on the same row, right-aligned, max 48ch. No other sentence repeats the count.
- **Items are a table.** Items with two or more comparable fields render as a table (real `<table>` or `role="table"`), one `caption` head row per section, so screen readers get column names. Items that are one sentence each (a digest) render as a list of rows instead.
- **Item columns.** Every item row in every group shares one column template so columns line up down the page. Content columns are sized by content and the template ends with the action column and then an empty spacer: `56px minmax(96px, max-content) minmax(160px, max-content) minmax(96px, max-content) minmax(180px, 360px) auto 1fr`. The row's chevron or row button therefore sits right after the content, never at the section's far edge. The field that caused the item shows as a state chip (state bg, state fg, `sm` radius) in its own column, never as a whole tinted row. Severity, when the brief has it, is its own badge after the chip (section 0), and a chip that would share its tint goes neutral, keeping its word.
- **One current item.** Exactly one unresolved item is current, and at load it is the first one. It expands in place to a `lg` block with a 2px `primary-border` outline, inset 8px from the section edge. Inside, in order: one sentence of explanation, the evidence (section 0) as a compact table (`evidence-row` rows, `row-sub` text, `md` radius, `surface-sunk` head, the file's row numbers in `text-3`, the item's row in `primary-wash`, the faulty cell with the state bg, a 1px state border and its word), then the button pair left-aligned with the text. The pair is the brief's verdict as a weak button and, only if the brief has one, a quiet button for the other path. Model (b) has one weak verdict button and a quiet "다음". Other items stay one line with a chevron, and clicking one makes it current.
- **Handled items.** A dismissed item collapses to one line in place: neutral icon tile, row text in `text-2`, a neutral badge with the verdict word, and a quiet "되돌리기". No strike-through, because a dismissed item is still part of the job. Only a positive verdict (fixed, approved) gets the success tile.
- **Decision panel** (`position: sticky; top: 88px`). A white `lg` panel with 24px padding. From top to bottom: the decision's name in `title` ("예외 내보내기"); in model (a) only, a 6px progress bar (`highlight` fill on `hairline` track, `full` radius) with a caption ("5건 중 2건 확인"); receipt rows for what the finishing action outputs, per the brief (for an export of exceptions: "찾은 예외 5건", "문제 아님 2건", "내보낼 예외 3건"), label at left in `row-sub` `text-3`, value at right in `body` `text` with tabular numbers, 40px each. Never restate the headline's number as a big figure. Label-left/value-right rows live only here, where 380px keeps them close. Then a hairline, the keyboard hint caption, and the primary button (52px, full panel width, consequence label). Under it, one quiet button for the alternative ("새 파일 올리기").
- **First screen.** The answer, every group, the current item's evidence, and the panel are visible at 1440×900 for up to about 6 items. The primary action is always in the panel and always visible.

## Queue

Use it when items run past about 20 or each one needs reading.

- The spoken answer spans the full content width on the canvas ("문의 40건 중 12건이 답을 기다려요"), with a segmented filter at right on the same baseline.
- **Grid.** `grid-template-columns: 440px minmax(0, 1fr)`, gap 24, both filling the viewport height below the headline.
- **List pane.** A white `lg` section with its own scroll. A sticky header holds the filter tabs with counts and, in model (a), the progress bar and its caption. Rows are two-line (72px): title in `body` 500, a `row-sub` line, and a badge and time at right. The selected row has a `primary-wash` fill and a 2px `primary-border` inset outline. A sticky footer holds the job's primary button at full pane width ("처리한 12건 내보내기").
- **Detail.** A white `lg` section with 32px padding, opened on the first unresolved item at load. It opens with the item's own answer in `title`, then the evidence (section 0), then the fields in a two-column label/value grid (label `caption` `text-3` above value `body`), then the action row at the end of the content, left-aligned: weak verdict, quiet secondary only if the brief has one, and "다음" with its shortcut. After a verdict the next unresolved item opens.

## Step flow

Use it when the person must give something before there is anything to show. **One decision per step, not one input per step.** The answers stack.

- **Grid.** `grid-template-columns: minmax(440px, 5fr) minmax(0, 7fr)`, gap 24, `align-items: start`. Both panes size to content.
- **Question pane.** A white `lg` section with 40px padding, content from the top. A step line (`caption`, "2/3 단계", three 4px segments in `highlight` and `hairline`), with "이전" as a quiet button beside it. Then answered steps as compact summary rows (label `caption`, answer `body` 500, quiet "바꾸기" at right). Then the current question as the headline, one `text-2` sentence of why, the input, and the primary button directly after the input at full pane width (52px). A reassurance line (privacy, what happens next) sits 12px under the button as a `caption` in `text-3` with a 16px icon.
- **Upload step.** The question ("오늘 출고 CSV를 올려 주세요") and a primary "파일 고르기". The brief's "upload area" is this pane plus the whole window as the drop target: on dragover both panes get a 2px dashed `primary-border` outline and the headline changes to "놓으면 바로 읽어요". There is no separate dashed box. Help for other file types sits as a `caption` row right after the reassurance line, not pinned to the pane's bottom.
- **Mapping step.** One step, whatever the field count. The question is "열을 확인해 주세요". Required fields come first as rows (section 0's content): field `caption` with "필수", chosen column `body` 500, two masked samples and the filled count in `row-sub` `text-3`, and a quiet "바꾸기" that turns the row into a 48px select in place. Optional fields sit under one disclosure row, "더 정확하게 보기 (선택) · 8개", closed by default and opened automatically when one of them has a warning. The primary "리포트 보기" stays disabled with its reason above it until the required fields are mapped.
- **Preview pane.** A white `lg` section showing the real consequence of the current answer, never an illustration and never empty. Upload: what will be checked, one row per check with a neutral icon tile, its name and a one-line description, under a `section` title ("올리면 이렇게 확인해요"). Mapping: the file's first 8 rows as a table, masked, with the mapped columns' heads in `primary-wash` and `primary-text` and a caption naming them. Booking: the calendar.

## Overview

Use it when the person returns to see how things stand (verdict model c).

- The spoken answer is on the canvas at full width ("이번 주 매출은 지난주보다 **12%** 늘었어요"), with the period control (segmented: 이번 주 · 지난주 · 4주) at right.
- **Grid.** `grid-template-columns: minmax(0, 2fr) minmax(320px, 1fr)`, gap 24, `align-items: start`.
- **Lead section** (left, white `lg`). The one number in `figure`, a comparison sentence, and a bar chart of the last 8 periods: the current bar `highlight`, the others `hairline`, values in `caption` on hover and focus. The rows that explain the number sit under the chart.
- **Side column.** Stacked white `lg` sections of different heights, each a `section` title row with a quiet "전체 보기" at right and up to 5 rows. Never a row of equal KPI tiles. One big number per screen.

# Interaction

- **One thing at a time.** Each viewport has exactly one filled primary button. The current item owns its weak and quiet buttons. Everything else is reachable but quiet.
- **Keyboard** (section 0 sets core and optional). Core: `↑`/`↓` move the current item, `Enter` on the current item runs its verdict, `Esc` collapses it, `Ctrl+Z` undoes the last verdict. Optional: `j`/`k` as aliases, `Ctrl+Enter` for the panel's primary button. Show the keys once, as a caption in the panel or list header ("↑↓ 이동 · Enter 문제 아님 · Ctrl+Z 되돌리기"). Focus is a 2px `primary-border` outline at 2px offset that follows the element's radius. Rows show focus as an inset 2px outline.
- **Inline actions vs. buttons.** A reversible verdict on one item is a weak button in the current item. Committing the whole job is the panel or footer primary button. A reversible action never opens a dialog.
- **Progress toward done.** The headline, the panel's receipt rows and, in model (a), the progress bar update together. Model (a) headline: "3건 남았어요", then "모두 확인했어요. 송장을 뽑아도 돼요", and a finished group's header shows a success badge "모두 확인". Model (b) headline keeps the brief's sentence with its numbers updated. It never says "남았어요", because nothing has to be done.
- **Undo.** Every verdict is undone from its row's "되돌리기", with `Ctrl+Z`, or from the toast's action within 5 seconds.
- **Loading.** Skeleton rows in the exact row layout: `canvas` bars with `sm` radius at the text's height, shimmer off under reduced motion. The headline says what is happening ("파일을 읽고 있어요"). A busy button keeps its width and shows three 5px dots, with `aria-busy`. Never a spinner on an empty page.
- **Empty.** The headline is the empty state ("312건에서 확인할 예외가 없어요") with a success icon tile before it. The sections still list what was checked, one row per rule with "없음", so the person sees the work. The finishing action follows section 0: enabled if it still outputs something, otherwise disabled with its reason as the `row-sub` sentence above it.
- **Error.** A page-level failure is a danger banner at the top of the main column or question pane, in the flow, naming the problem, with the recovery as the pane's primary button ("다른 파일 고르기"). Field errors follow section 0. Background failures are silent unless data is at risk.
- **Success.** Said once. The headline rewrites, the progress bar (model a) fills with `success.border`, and the primary button's label updates. No extra banner or confetti.
- **Toast.** Confirms things already done, with optional undo. Bottom center, 24px above the window edge, `toast` fill, `on-toast` text in `body`, `md` radius, padding 14px 20px, max 480px wide, the action in `on-toast` 600 with an underline. 4s, or 5s with an action, paused on hover. One at a time.
- **Confirmation dialog.** Only for losing work undo cannot restore (a new file replacing verdicts), deleting, or sending to other people. A download is not sending. 400px wide, `lg` radius, 28px padding, scrim at 48%. A `title` question ("고객 120명에게 보낼까요?"), one sentence of consequence, then a button pair at the bottom right: neutral weak "닫기" and the action named by its verb (danger fill when destructive). Never "취소" and never "확인".
- **Motion.** Buttons scale to 0.98 while pressed (100ms). A handled item collapses over 220ms `cubic-bezier(.22,1,.36,1)`. The headline number rolls to its new value. All three run only when the node persists between renders (section 0), and all are off under `prefers-reduced-motion`.

# Components

- **Buttons.** Height 40, padding 0 16, `md` radius, `button` type, icon 18px with an 8px gap, width by label (never fixed, so English fits).
  - **Primary.** `primary` / `on-primary`. Hover `primary-hover`, pressed `primary-press`. One per viewport. The CTA size is 52px tall with 24px padding.
  - **Weak.** `primary-wash` / `primary-text`. Hover `primary-wash-strong` with the label in `text`. Use it for the current item's verdict and secondary actions.
  - **Neutral weak.** `canvas` fill / `text-2` on white surfaces. Hover `hairline`. Use it for "닫기" and "새 파일".
  - **Quiet.** No fill, `text-2` label (or `primary-text` for navigation), hover `surface-sunk`. Use it for alternatives, "바꾸기", and "되돌리기".
  - **Danger.** Fill `danger.fg` / `on-strong`, hover `danger.fill-hover`. Danger weak is `danger.bg` / `danger.fg`.
  - **Row button.** 32px tall, `sm` radius, `label` type, weak or quiet. Its accessible name includes the item (section 0).
  - **Disabled.** `canvas` fill, `text-disabled` label, `cursor: not-allowed`, plus a `row-sub` sentence directly above saying what unlocks it.
- **Input and select.** 48px tall, `md` radius, `surface` fill, 1px `border-strong`, padding 0 16, value in `body`, placeholder `text-3`. The label sits above in `label`/`text-2` with an 8px gap. Focus is a 2px `primary-border` ring. On error the border becomes `danger.border` and a `row-sub` message in `danger.fg` with an icon sits 6px below. A warning (section 0) uses `warning.border` and `warning.fg` the same way. Select uses a lucide chevron, never the native arrow. A checkbox checked fills `highlight` with a `surface` check.
- **Table** (inside a white section, never on the canvas). Head 40px, `caption` 500 `text-3`, with a hairline below. Rows are 52px with a hairline divider indented 24px on both sides, and no zebra striping. Numbers are right-aligned and tabular, IDs left-aligned and tabular. Columns size by content, and the template ends with the action column and a `1fr` spacer (see Decision page), so a row action sits right after the content. The quiet row button shows on row hover and focus. Sortable heads show a chevron on hover.
- **Marked cell.** The faulty value in any table gets the state `bg`, a 1px state `border`, state `fg` text and the word for an empty value ("빈칸"), `sm` radius, inset 4px.
- **Rows** (lists that are not tables). Left icon tile, a title in `body` 500 with an optional `row-sub` line, and a right slot for a figure, badge, or chevron. Padding 12px 24px. Hover `surface-sunk`, pressed `canvas`, selected `primary-wash` with a 2px inset `primary-border` outline.
- **Icon tile.** 40px, `md` radius, a state `bg` with a lucide icon (stroke 2, 20px) in the state `fg`. The neutral tile is `canvas` with `text-2`. Tiles encode kind or state and are never decoration.
- **Tabs and filters.**
  - **Underline tabs** separate views. `section` type: selected in `text` with a 2px `text` bar, others in `text-3`, gap 24px.
  - **Segmented control** narrows the same view. 40px tall `canvas` track with `md` radius and 4px padding. The selected segment is `surface` with `0 1px 3px rgba(0,23,51,.08)` and a 1px transparent outline (visible in forced colours), `label` type in `text`, the count after the name ("주소 빈칸 2"). A segment with zero items is disabled.
- **Badge.** 24px tall, padding 0 8, `sm` radius, `badge` type, state `bg`/`fg`, a 14px icon before the word. A filled badge (state fg fill, `on-strong` text) is allowed only for a state the brief calls blocking, once per screen.
- **Banner.** `md` radius, padding 16px 20px, state `bg`, no border. A 20px state icon, a title in `body` 600 in state `fg`, and body text in `text-2`. A recovery button at the right end (weak or danger weak). It stays in the flow at the top of the region it concerns and is never fixed over content.
- **Empty state.** It is the region's own content: the headline sentence and the checked rows. Never a centered illustration with a button inside a dashed box.
- **Side panel / inspector.** It is the decision panel (Decision page) or the detail section (Queue). Its width comes from `density`, it sticks below the top bar, and its primary action sits at its bottom.
- **Shadows.** None on sections, panels, or rows. Sections separate by the canvas around them. Toasts and dialogs use `0 8px 24px rgba(0,23,51,.08)`.

## Measured contrast

Measured on this file's frontmatter. Body text needs 4.5:1. Large text (24px+, or 18.66px bold) and UI marks need 3:1.

| Foreground | Background | Ratio | Use |
|---|---|---|---|
| `text` #191F28 | `surface` #FFFFFF | 16.56 | values, headlines |
| `text` #191F28 | `canvas` #F2F4F6 | 15.02 | headline on the ground |
| `text` #191F28 | `primary-wash` #EDF4FF | 14.97 | selected row |
| `text` #191F28 | `primary-wash-strong` #D8E8FF | 13.33 | weak button hover label |
| `text-2` #4E5968 | `surface` #FFFFFF | 7.11 | sentences, labels |
| `text-2` #4E5968 | `canvas` #F2F4F6 | 6.45 | sentences on the ground, neutral weak button |
| `text-2` #4E5968 | `surface-sunk` #F9FAFB | 6.81 | row hover |
| `text-2` #4E5968 | `primary-wash` #EDF4FF | 6.43 | sentence in a selected row |
| `text-2` #4E5968 | `danger.bg` #FFEEEE | 6.34 | banner body |
| `text-2` #4E5968 | `warning.bg` #FFF9E7 | 6.76 | banner body |
| `text-2` #4E5968 | `success.bg` #F0FAF6 | 6.68 | banner body |
| `text-2` #4E5968 | `info.bg` #EEF1F5 | 6.28 | banner body |
| `text-3` #636E7C | `surface` #FFFFFF | 5.18 | metadata, column heads, placeholder |
| `text-3` #636E7C | `surface-sunk` #F9FAFB | 4.96 | metadata in a hovered row |
| `text-3` #636E7C | `canvas` #F2F4F6 | 4.70 | meta line under the headline |
| `text-3` #636E7C | `primary-wash` #EDF4FF | 4.69 | metadata in a selected row |
| `on-primary` #FFFFFF | `primary` #1F72E5 | 4.57 | primary button |
| `on-primary` #FFFFFF | `primary-hover` #0662D4 | 5.67 | primary hover |
| `on-primary` #FFFFFF | `primary-press` #0054BB | 7.03 | primary pressed |
| `primary-text` #0662D3 | `surface` #FFFFFF | 5.68 | links |
| `primary-text` #0662D3 | `canvas` #F2F4F6 | 5.15 | links on the ground |
| `primary-text` #0662D3 | `primary-wash` #EDF4FF | 5.14 | weak button |
| `primary-border` #3182F6 | `surface` #FFFFFF | 3.71 (3:1) | focus ring, current-item outline |
| `primary-border` #3182F6 | `canvas` #F2F4F6 | 3.37 (3:1) | focus ring on the ground |
| `primary-border` #3182F6 | `primary-wash` #EDF4FF | 3.36 (3:1) | focus ring on a selected row |
| `highlight` #3182F6 | `surface` #FFFFFF | 3.71 (3:1) | progress fill, headline number 24px+ |
| `highlight` #3182F6 | `canvas` #F2F4F6 | 3.37 (3:1) | headline number on the ground |
| `surface` #FFFFFF | `highlight` #3182F6 | 3.71 (3:1) | check knocked out of a mark |
| `border-strong` #8B95A1 | `surface` #FFFFFF | 3.04 (3:1) | input boundary |
| `danger.fg` #D22030 | `danger.bg` #FFEEEE | 4.69 | danger text on tint |
| `danger.fg` #D22030 | `surface` #FFFFFF | 5.26 | field error |
| `on-strong` #FFFFFF | `danger.fg` #D22030 | 5.26 | danger button |
| `on-strong` #FFFFFF | `danger.fill-hover` #BC1B2A | 6.31 | danger hover |
| `danger.border` #F04452 | `surface` #FFFFFF | 3.71 (3:1) | error field border |
| `danger.border` #F04452 | `danger.bg` #FFEEEE | 3.31 (3:1) | marked cell, current item in danger |
| `warning.fg` #A64B00 | `warning.bg` #FFF9E7 | 5.50 | warning text on tint |
| `warning.fg` #A64B00 | `surface` #FFFFFF | 5.79 | warning text |
| `warning.border` #D96A00 | `surface` #FFFFFF | 3.49 (3:1) | warning field border |
| `warning.border` #D96A00 | `warning.bg` #FFF9E7 | 3.32 (3:1) | marked cell in warning |
| `success.fg` #027648 | `success.bg` #F0FAF6 | 5.35 | success text on tint |
| `success.fg` #027648 | `surface` #FFFFFF | 5.69 | success text |
| `success.border` #029359 | `surface` #FFFFFF | 3.95 (3:1) | done progress fill, border |
| `success.border` #029359 | `success.bg` #F0FAF6 | 3.70 (3:1) | current item in success |
| `info.fg` #3A4555 | `info.bg` #EEF1F5 | 8.57 | info text on tint |
| `info.fg` #3A4555 | `surface` #FFFFFF | 9.71 | info text |
| `info.border` #7B8594 | `info.bg` #EEF1F5 | 3.30 (3:1) | info border |
| `on-toast` #FFFFFF | `toast` #191F28 | 16.56 | toast text and action |
| `on-primary` #FFFFFF | `highlight` #3182F6 | 3.71 | banned: white label on the bright blue |

The source's own values fail in two places, and this pack moves both. Its tertiary grey measures 4.19 on the canvas, so `text-3` is one step darker. Its bright blue under a white label measures 3.71 (last row, banned), so the filled button uses the derived `primary` and the main colour appears only as `highlight` marks and the 24px+ headline number. Use only the pairs above. A new combination is measured before it ships.

**Derived main colours.** The host derives the roles (`derive()` in `backend/justshipit/engineering/accent.py`, with this pack's `accent-derive`) and its look check holds the build to the frontmatter; nothing in the product repo runs either. 21 pairs each, all pass.

| Main | `primary` / `on-primary` | `primary-text` | `highlight` | Tightest pair |
|---|---|---|---|---|
| #16a34a green | #00883a / white | #007932 | #009942 | `on-primary` on `primary` 4.58 |
| #ea580c orange | #cf4b00 / white | #b84100 | #e65503 | `on-primary` on `primary` 4.51 |
| #facc15 yellow | #facc15 / `text` | #7e6500 | #a08100 | `text-3` on `primary-wash` 4.68 |
| #0f2e4f navy | #0f2e4f / white | #0f2e4f | #0f2e4f | `text-3` on `primary-wash` 4.67 |

# Korean

The copy, number and date rules here apply only when the product's language is Korean; in another language, follow that language's own conventions. The size, type and length rules hold in every language.

- Body is 16px with a 1.5 line height. The headline is 30/40 and never wraps past two lines (`text-wrap: balance`). Long-form text keeps 45 to 70 characters per line.
- `word-break: keep-all` on all Korean text. `overflow-wrap: anywhere` only on IDs, file names, and URLs.
- Tracking is -0.02em at 22px and above, -0.01em at 16 to 18px, and 0 below.
- Numbers come before the counter with no space and the noun before the number: "예외 5건", "행 312개", "직원 4명". Money is "32,000원", or "3만 2,000원" in sentences. Dates in sentences read "11월 5일 (수)", in tables "11월 5일 08:28" (24-hour, tabular). Recent times read "3분 전".
- Copy is 해요체, short, and conclusion first. A button is a verb or a verdict ("내보내기", "문제 아님", "다음"). A close button reads "닫기" or "다음에", never "취소". Errors name what happened and the fix in one or two sentences. No exclamation marks, no "되었습니다", no "성공적으로".
- English must fit the same layout. Labels run about 1.3 times longer, so buttons, chips, and segments size by content and the headline may take its second line. Never set a fixed width on anything with text.

# Never

From round 1, which looked AI-made in every palette:

- One centered column of about 1000px floating in a 1440 window with empty margins.
- The block "heading + count, a sentence repeating the count, a full-width table" stacked once per group.
- A lone action button at the far right edge of each row, hundreds of pixels from its content.
- A dashed upload box floating on an empty first screen.
- A row of count chips or KPI tiles under the title.

This style's own ways to go wrong:

- **The stretched phone.** A 1-column list whose rows put a label at the far left and a value at the far right across more than 480px. Rows wider than that carry aligned columns.
- **The shrunk phone.** Any 375 to 720px column centered on the canvas, including a centered "card" for a step or a login.
- **The tall empty pane.** A pane stretched to the viewport with one question floating in its middle.
- **A literal bottom bar.** A fixed full-window-width button bar at the bottom of the viewport. The primary button lives in the panel, the pane footer, or right after the input.
- **Accent everywhere.** Two filled primaries in one viewport, accent section titles, accent decorative icons, or `highlight` text under 24px.
- **An invented verdict.** A second per-item button ("고쳐야 해요") the brief does not have, added because the current item has room for two.
- `text-disabled` or `border-strong` used for text a person must read.
- Cards with borders or shadows, cards inside cards, or equal-size card grids.
- Bottom sheets sliding up on desktop, or modals for reversible choices.
- Emoji, 3D cartoon icons, mascots, celebration confetti, playful copy ("짠!"), or exclamation marks.
- An eyebrow label above the headline. Section numbers, except the step line, which carries real information.
- Gradients, glass, a colored left border on alerts or rows, or the source's brand colour or name anywhere in a product.
