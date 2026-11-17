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
