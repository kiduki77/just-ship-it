# Shape: spec wireflow

The confirmed brief for the wireflow on the spec approval card and the merge card. The build design is
`docs/design/spec-wireflow/synthesis.md`.

## Job and audience

The owner is a solo founder who is usually not a developer. They open a spec approval card in the side panel on a
desktop browser, a few times a day, between other things. Today the card is text, and the owner approves without
reading it. The surface is Operate: the owner decides, so the picture must answer "is this what I meant?" in the time
it takes to scroll once.

## Outcome and proof

- The owner sees, before approving, every screen a P1 story passes through, in order, with the product's real words.
- The owner can point at one screen and say what should change, and the next card shows where that note landed.
- On the merge card the owner sees each planned screen beside what was built.
- Proof data is the real spec BUSINE10-0001 (warehouse CSV exception report) and the captures of its build J-10.

## Selected direction

The direction was picked from a throwaway prototype on the real data, in a 482px panel, with three layouts and two
skins behind one switcher.

| Variant | What the prototype showed |
|---|---|
| Horizontal row | The third frame is clipped at 482px. Arrow labels wrap mid-word. |
| One screen at a time | Each screen reads best, but the flow hides behind clicks the owner may not make. |
| **Vertical stepper** | The whole flow fits with no horizontal scroll. The step text sits beside its frame. **Picked.** |
| Hand-drawn skin | Korean at frame size is unreadable. Dropped. |
| **Neat grey skin** | Console font and neutral tokens. Reads in both languages. **Picked.** |
| Capture at half panel | A 1440px capture shrunk to about 210px is less readable than the sketch. Click to enlarge is required. |

The structure is a vertical stepper. Each step has the frame on the left, the step number, screen name and result
on the right, and an arrow down labelled with what the user does. Branch frames (error, empty) sit smaller beside the
step they branch from.

## Scope and boundaries

- Fidelity is low: what each screen holds, in what order, in which words. The engineering team owns the look.
- The approval card shows P1 stories as flows. P2 and P3 stay a list under them.
- The owner cannot edit a sketch. They leave words on a screen.
- Untouched: the spec text fold, the owner-trace section and the approve gate on unread text. The wireflow goes above
  the story list and replaces the P1 lines in it.
- Anti-goals: a canvas with pan or zoom, colour as decoration, anything that looks like a finished design and invites
  pixel feedback.

## States and ranges

- One to three P1 stories, two to six steps each, up to 12 planned screen states in total.
- Old specs and products with no screen have no wireflow, and the card looks as it does today.
- Partly drawn: one line says how many screens could not be drawn.
- A redraft card: frames that changed say "changed", and each note shows its outcome on its frame.
- A failed redraft: the card says so, keeps the notes, and offers "try again".
- Text in sketches is in the product's language. Console words are in the owner's language. Either can be longer.

## Interaction and layout

- Clicking a frame opens it large in place under its step, with the full words and the note box. Esc closes it and
  returns focus to the frame.
- A frame with a note shows a numbered pin. While a note is typed, Approve is off, and the decline button reads "send
  back with n notes".
- The merge card pairs each planned frame with its capture, row by row, and one line under the pair says whether
  they match. Both open large on click.
- Keyboard: every frame is a button with an accessible name of screen and state. The sketch itself is hidden from
  screen readers, and its words are the description.

## Constraints

- The side panel is 400 to 520px wide (`--panel-w`). Users are on desktop only.
- DESIGN.md rules: neutral tokens only, state colour only on a mark that states something, one bordered box (the
  frame), radius at most 8px, no shadow at rest, no box sized to a string.
- Every console string comes from `t()` in English and Korean. Sketch text is data and renders as written.
