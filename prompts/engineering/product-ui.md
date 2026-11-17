# Product screen standard

Real users of a real business open this product every day. The moment it opens, it should look made for this business. An admin screen of one table with an input form attached, or a screen that looks stamped out of an AI template, fails even when it meets the whole spec. Read this standard with `.ship/spec/design-brief.md` (this product's users, their moment, the core task, the feel, and the screen for each state) and, in a new product, `DESIGN.md`. design-brief.md decides content, copy, states and which actions exist; DESIGN.md decides structure, layout, components and look. Where this standard differs from either, they win.

**A product that already has screens** (the build prompt says so) keeps its own look. Use its existing styles, components and copy file, and match them in what you add. Skip section 2: write no DESIGN.md, add no token layer and restyle nothing the spec does not ask for. Sections 1 and 3 to 7 apply to the screens and states this change touches.

## 1. Design from the user's moment
- The first screen's lead is what the core task needs (for example, the beans to order today and by when). A table holding all the data is not the lead; it is the workspace below it.
- The hierarchy of size, weight, position and color follows design-brief.md's priority (what matters most at first glance), inside the archetype a new product's DESIGN.md gives the core task: DESIGN.md places things, the brief ranks them. The one most important thing is the largest, or the first thing seen.
- The core task finishes within design-brief.md's step count. Do frequent work (recounting stock, say) right in the list row, never on a separate screen or a long form.
- Use standard controls (buttons, inputs, selects, tabs, sortable tables). Do not imitate the look of another tool (a terminal, a paper ledger).

## 2. A new product: DESIGN.md is given
`DESIGN.md` at the repository root is the style the owner picked for this business. Never rewrite or edit it: the host checks it is as given and that the code keeps to it.
- Pick the archetype DESIGN.md gives the product's core task, and build its structure, layout and components.
- Before any screen code, put DESIGN.md's values in CSS custom properties (`:root`) under its own names. Components use tokens only. No color or font DESIGN.md lacks, and no raw color values or arbitrary px outside the token file.
- Its type, spacing, color, contrast and interaction-state rules already meet this standard. Keep them as given.
- Accent roles: text never takes `primary`, the fill (`color: var(--primary)` is out); accent text and icons take `primary-text`, marks take `highlight`, labels on the fill take `on-primary`.
- Design the browser defaults from its tokens too: link color and underline, text selection color, inputs and selects, date inputs, scrollbars, numbers in tables.

## 3. Build every state
Each row of design-brief.md's state table is a screen. Build at least these.
- Empty (the first day): one sentence on what the screen does for the user, and one first action (a button). A screen showing only an empty table's header row is out.
- Error: what went wrong and how to fix it, in place (beside the input). Never clear what the user typed.
- Success: short feedback that what they just did worked (saved, the changed row highlighted).
- Warnings and urgency: state color and words together. Never tell states apart by color alone.
- Loading, if there is any: a skeleton that holds the space, so the screen does not jump.
- Nothing breaks with long names, large numbers, zero items or thirty. Never assume a string's length.

## 4. Avoid (the tells of an AI template)
Where DESIGN.md prescribes a pattern this list names (a lead number, a count strip), DESIGN.md wins.
- An admin screen of one table, one form and default buttons. If the core task is one cell in a table, redesign.
- A grid of same-size cards, repeated icon-title-description cards, cards inside cards.
- The "hero metric" pattern: a big number, a small label and a supporting stat.
- A small label above a heading (an eyebrow or kicker). Numbers like 01 / 02 / 03 that carry no information.
- Gradient text, decorative glass or blur, cards with a colored left (or right) border over 2px, colored glow shadows, hard offset shadows with no blur.
- Emoji or Unicode symbols in place of icons. If you use icons, one library bundled from npm, one weight.
- Modals you do not need. What can finish on the screen finishes on the screen.
- Animation scattered everywhere. If you use it, in one meaningful place, 150–250ms ease-out, and respect `prefers-reduced-motion`.

## 5. Copy
- Write every word the user reads in the product's language, which `.ship/spec/prd.md` names at its top (`Product language`), whatever language the spec or these instructions are in. Set `<html lang>` to it. If the spec gives exact screen text, use it as written.
- Use the product's own words. A button names its action ("Add beans", "Recount stock", rather than "Save"). An error says what is wrong and how to fix it.
- The product speaks to its own users. Its text never addresses the owner by name ("Text this to Alex" is out); when a user must reach someone, name the role or say "us".
- When the product ships in more than one language, every message carries the same facts in each language. If the Korean banner says how many days late an order is, the English one says it too.
- Write as people who speak that language write. In English: plain words, sentence case. In Korean: 해요체, short and active, not translated phrasing. In any other language, the same bar.

## 6. Look and fix (required)
Never judge a screen from the code alone. The engineer container has `chromium`.
- Add `scripts/preview.sh` (POSIX sh). Take the dependencies `scripts/ci-setup.sh` installed as given, build, then serve in the foreground on `127.0.0.1:4173`. For example: `npm run build && exec npx vite preview --host 127.0.0.1 --port 4173 --strictPort`.
- Add `scripts/ui-states.txt`. One `name path` per line (the name uses letters, digits, `-` and `_`; the path starts with `/`). For each state in design-brief.md, give a path that opens that state directly (for example `three-beans /?fixture=three-beans`, `save-failed /?fixture=storage-full`). A fixture lives in memory only and never writes to the user's storage (localStorage and the like), and without a fixture parameter the app works as usual. The empty state is `/`, so it needs no line.
- A fixture shows a state; it does not prove the product works. The core daily task is proved by `scripts/core-flow.sh` on `fixtures/core-flow/` inputs shaped like the owner's real files, through the real code (the build instructions say how). A screen that works on a fixture and blocks a real file (a column of all "no", say, or a column named the way the owner's file names it) fails.
- Take the captures and look. Set `--lang` to the product's language (`ko-KR`, `en-US`, ...):
  ```sh
  sh scripts/preview.sh > /tmp/preview.log 2>&1 &
  until curl -s -o /dev/null http://127.0.0.1:4173/; do sleep 1; done
  chromium --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage --no-proxy-server --lang=en-US --hide-scrollbars \
    --window-size=1440,900 --virtual-time-budget=5000 --screenshot=.ship/shots/first-1440.png "http://127.0.0.1:4173/"
  ```
  Open the PNG with Read. At 1440×900 and 1280×800, for the empty state and each state in ui-states.txt. Fix what falls short of design-brief.md's priority and feel, of DESIGN.md in a new product, and of the Avoid list above, then take them again. Write in the output's decisions what you looked at and what you fixed.
- Keep the captures under `.ship/` (never commit them). At the acceptance check the worker takes them again the same way and shows them to the CPO.

## 7. Accessibility floor
Contrast of 4.5:1 or more for body text and input hints and 3:1 or more for large text and marks (a new product's DESIGN.md already meets it), everything operable by keyboard, a label tied to every input, a visible focus, never color alone for a state, and tables with a header row and a caption or aria-label.
