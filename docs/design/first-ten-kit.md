# First 10 kit: what the owner needs in hand to reach the first ten people

Status: design, roadmap 0.6, grounded in `main` 8094374. File and line references come from a code survey on that
commit. Character limits of outside sites were not checked and no screen states one as a fact.

## Why

The first users come from the owner meeting people one at a time (`docs/ux/08-first-100.md` §12). The parts for that
exist but are scattered, and three are missing.

- `people.today()` (`services/people.py:462`) already picks up to 3 people to reach today. Only the Slack briefing
  shows it. No console screen calls `GET /people/today`.
- A message for one person exists only as a `Handout` on an approved request, one decision card per person
  (`contracts/requests.py:167`, `owner_tasks.talk_with`). The expert panel called that too slow for 1:1 contact
  (`docs/ux/09-expert-panel.md:50`, `:91`).
- Nothing writes profile wording (the short text on the owner's own accounts) or invite wording.

0.6 puts four things in one place on the People tab, written on the owner's click, each with a Copy button.
**It prepares and never sends.** No connector, no outbox row, no decision card.

## 1. What the kit holds

| Part | What it is | Built from |
|---|---|---|
| Today's people | Up to 3 people, each with why this person, where they were found, their latest words, and the next step | `people.today()` as it is |
| Message draft | One message for one person, in the owner's voice ("I"), at most 600 characters | The person's quotes, `why`, stage, `next_action`; the customer description; the product's plan |
| Profile wording | Three sizes: one line (at most 80 characters), short (at most 160), about (at most 400) | The plan, the customer description, what the owner said about themselves in the kickoff |
| Invite wording | Two texts that invite one person to try the product: for someone the owner knows, and for someone who said they have the problem. Each at most 500 characters | The plan, the owner's place from `tracking.links_for(business, None)` |

### 1.1 Who gets a message draft

| Person | Draft |
|---|---|
| Source `warm` or `inbound`, any stage | Yes |
| Any source, stage `contacted` or later (a follow-up) | Yes |
| Source `public_problem`, `similar_tool` or `participation`, stage `found` | No. The row says "Answer their post in your own words first" with the post link, and shows the prepared questions and points as today |

The third row keeps two rules already in the product: the finder and the thread finder write no reply sentences
(`prompts/find_people.md:4`, `prompts/find_threads.md:37`), and a person card's checklist says public first. A cold
private message to a stranger found on a forum is the pattern the roadmap's "never" list is closest to.

### 1.2 The link in a text

Invite texts and the about-size profile carry the owner's own place, plain, from `tracking.links_for(business, None)`
(`services/tracking.py:156`, written for this use). No tag: nothing reads a tag back for a private message. When the
place is one where the owner asks people to say where they heard (no link), the invite carries that line instead.
When the business has no place, the text has no link and the section says "Add your place to put a link here", linking
to the places form from 0.5. A message draft carries a link only at stage `talked` or later.

## 2. Data model

One new table, `kit_texts`, append-only. No new to-do kind, so `ck_owner_tasks_kind` and the stages' allowed kinds do
not change.

| Column | Type | Meaning |
|---|---|---|
| `id` | bigint pk | |
| `business_id` | fk businesses, not null | |
| `part` | varchar(10), check in (`message`, `profile`, `invite`) | |
| `slot` | varchar(20), not null | `message`: `"p"`; `profile`: `line`, `short`, `about`; `invite`: `known`, `problem` |
| `person_id` | fk people, null | Set exactly when `part = 'message'` (check constraint) |
| `lang` | varchar(5), not null | Language the text is written in |
| `body` | text, null | Null when the text was refused |
| `refused` | varchar(40), null | The rule code that refused it. Exactly one of `body`, `refused` is set (check constraint) |
| `basis` | jsonb, not null | What it was built from: `{quotes: [..], profile: [codes], link: url or null, guess: bool, person_seen: {stage, quotes_n, next_action_at}}` |
| `job_id`, `llm_call_id` | fk, null | The job and call that wrote it |
| `made_at` | timestamptz, not null | |
| `replaced_at` | timestamptz, null | Set when a newer text takes the same `(business, part, slot, person)` |

A partial unique index on `(business_id, part, slot, coalesce(person_id, 0)) where replaced_at is null` keeps one
live text per slot. Writing again marks the old row replaced and inserts a new one in the same transaction, so two
clicks cannot leave two live rows.

Forgetting a person (`people.archive`) sets `body` to null, `refused = 'forgotten'` and empties `basis.quotes` on that
person's rows, like the scrub of their to-dos. Rows are otherwise never deleted.

**Stale.** Computed on read, never stored. A message is stale when the person's stage, number of quotes or
`next_action_at` differ from `basis.person_seen`. Profile and invite are stale when the customer description or the
owner's places changed after `made_at`. A stale text stays readable with the line "Written before the latest change"
and a Write again button.

## 3. Writing

- Job kind `kit_prepare`, queued only by the owner's click with `queue.enqueue(..., by_owner=True)`. No recurring
  job in 0.6, so no stage has to allow it. Payload: `{business_id, parts: [...], person_ids: [...]}`. Idempotency key
  per business and requested slots, so a double click queues once.
- One marketing call per job (new prompt `prompts/first_ten_kit.md`), cost recorded like `promotion_week`
  (`services/promotion.py:386`). The prompt gets the customer description block (`profile.prompt_block`), the plan
  facts the promotion prompt gets, and for each person only: ref, source, stage, quotes, `why`, `next_action`.
- The call returns JSON keyed by slot. The server checks every text before storing it:
  - `people.contact_info_problem` (no email, phone or messenger id);
  - the handout author checks in `owner_tasks.author_problems` (`:365`): no `[blank]`, one register, one voice,
    no invented earlier talk;
  - `copy_rules.public_copy_problem` for profile and invite (no invented experience, no internal ids);
  - `channels.forbidden_reason` (no ask for votes, stars or reviews);
  - the length limit of the slot; a link other than the one given is refused;
  - a message may only state things about the person that are in their quotes or `why`.
- Refused texts get one rewrite round with the reasons, the same shape as `promotion._again_prompt`. What is still
  refused is stored with `refused` set and shown as "Not written" with the reason in the owner's language.
- Language: the message follows the language of the person's quotes when they have any, else the language rule the
  promotion cards use. Profile and invite use that same promotion rule.
- A guess is marked. When the customer description lines used are still the company's guess, `basis.guess` is true
  and the text carries the same guess mark post cards carry (0.3).
- Nothing about the owner is invented. If the kickoff holds nothing the owner said about themselves, profile wording
  describes the product and who it is for, and the section says so.
- An `activity` line `kit_prepared` records what was written and what was refused.

## 4. API

- `GET /api/businesses/{id}/kit` returns `{milestone: {n: 10, have, estimated}, today: [{person, reason, draft:
  text | null, no_draft_reason | null}], profile: {line, short, about}, invite: {known, problem}, link, writing: bool,
  can_write: bool}`. Each text is `{id, body, refused, refused_text, lang, chars, limit, stale, guess, made_at,
  basis_view}`. `milestone` comes from `tracking_count` (`MILESTONES`), so the kit and the Numbers tab never disagree.
  `writing` is true while a `kit_prepare` job for the business is queued or running.
- `POST /api/businesses/{id}/kit/prepare` with `{parts?: [...], person_id?: n}` queues the job. It needs the
  `x-ship` header like other console writes. It answers 409 with a message when a job is already writing.
- Marking a person as contacted reuses `POST /people/{id}/stage`. Nothing new records a copy.

## 5. The screen

Where: the top of the People tab on the business page (`web/src/pages/Business.tsx:335`, `business/People.tsx`), a
section "First 10 kit" above the list, open while fewer than 10 people got value and folded to one line after that.
New component `web/src/business/Kit.tsx`, catalog `web/src/i18n/areas/business-kit.ts`.

Order, top to bottom:

1. Header line: "First 10 kit · 3 of 10 got value" and the main button "Prepare the kit" (first time) or "Write
   again" (per text afterwards). One sentence under it: "The company writes these for you to copy. It sends nothing."
2. **Reach out today.** Up to 3 rows. Each: name or handle, stage, why today (overdue next step, or new), the latest
   quote, the post link. Under it the draft with Copy, its character count, "Written from: ..." (the quotes used),
   the guess mark when set, and "I reached out" which moves the person to contacted. A row with no draft shows why
   (public first, or not written yet with "Write a draft").
3. **Profile wording.** Three texts with count and limit ("72 of 80"), each with Copy.
4. **Invite wording.** Two texts with Copy, and the link line from 1.2.

States: nothing prepared yet (the two sentences and the button, no empty boxes); writing (the button is replaced by
"Writing..." and existing texts stay readable); refused (reason and Write again); stale; no people for today ("No one
to reach today. Add someone you know, or look for people" with the two existing actions); no place.

Today screen: when `people.today()` is not empty and none of those people has an open person to-do, `today.collect`
adds one item "Reach out to up to 3 people today" at the today tier that opens the kit. It never adds one item per
person, and it disappears when the list is empty.

Widths 1440, 1200, 900, 390; light and dark; English and Korean; keyboard reachable; Copy announces "Copied" to a
screen reader through a live region. No string length is assumed: counts come from the server.

Glossary: `first_ten_kit` = "First 10 kit" / "첫 10명 키트". "Message draft" / "메시지 초안", "Profile wording" /
"프로필 문구", "Invite wording" / "초대 문구" live in the catalogs.

## 6. Safety

- No send path. `services/kit.py` imports no connector, outbox or request module. A test fails if it does.
- No contact details stored or written: every text passes `contact_info_problem`; the table has no column for them.
- One message for one person. The prompt and the check refuse a text addressed to several people, and the screen has
  no "copy all".
- A model call only inside the job (`GateViolation` otherwise).
- The job reads only the one business's rows.

## 7. Done when (the ROADMAP row)

1. On the People tab the owner sees up to 3 people to reach today with the reason for each.
2. One click writes a draft for each person who gets one; a person found in a public post and not yet contacted gets
   no draft and the row says why.
3. Profile wording in three sizes and invite wording in two are written, each with a count, a limit and Copy. The
   invite carries the owner's own place, or says how to add one.
4. A refused text is shown as not written with its reason; no stored text holds an email address, a phone number or
   a messenger id; a text built on a guessed customer description is marked.
5. A changed person or customer description marks the text as written before the change.
6. Nothing is sent: no outbox row, no decision card and no connector call comes from the kit.
7. The Today screen shows one line that opens the kit when there are people to reach and no to-do covers them.
8. Both languages, both themes, four widths, checked on a real test business with real model calls.

## 8. Phases

1. Migration, model, `services/kit.py` (read model, stale, checks), tests.
2. Job, prompt, rewrite round, activity line, API, Today item, tests.
3. Console section, catalogs, glossary.
4. Review, fixes, a real run.

## 9. Decided here, for the owner to overturn

1. No message draft for a stranger found in a public post until the owner has contacted them (1.1).
2. Written only on the owner's click; no daily automatic writing in 0.6.
3. Profile wording comes in three generic sizes, not per site, because each site's limit is unchecked. Per-site
   limits belong to 0.7 (channel rules).
4. Messages are not editable in the console. The owner copies and edits where they send.

## Not in 0.6

Sending, scheduling, storing contact details (1.3), reply sentences for public threads (1.4), per-site profile
limits (0.7), counting tallied people toward the first 10 (left to 0.9 by `tracking.md` §9).


## Deviations found while building

Phases 1 and 2 (backend) were built against the code, which differs from the survey in these places. The product
decisions of 1.1, 6, 7 and 9 are unchanged.

- **Base.** The worktree started 16 commits behind `main` and had no tracking code (`services/tracking.py`,
  `tracking_count.py`), so `feat/first-ten-kit` was rebased onto `main` 8094374 before any work. The migration's parent
  is `c4a8e1d7b295` as the design says; its id is `d5f1a8c3e726`. `tests/test_tracking_migration.py` now allows the kit's
  table when it checks the tracking revision against the models.
- **The place.** `tracking.links_for(business, None)` does not exist in that form: it takes a channel key. The kit calls
  `links_for(business, "other")` with no tag, which gives every place's plain address, and uses the first. The "say where
  you heard" line of 1.2 belongs to a post channel whose link is `none`, and a private text has no channel, so an invite
  never carries that line. With no place the read model says `link.state = "no_place"` and the prompt says to write no
  link.
- **The text checks.** `owner_tasks.author_problems` takes request drafts, so the kit reuses the checks it is made of
  (`blanks`, `register_mixed`, `voice_mixed`, `past_talk`), plus `people.named` for a message that names someone else.
  An earlier talk is refused only before the stage `talked`; at `talked` and later it happened. `public_copy_problem` runs
  on profile and invite wording as the design says; a message runs `copy_rules.internal_note` only, since "I tried" in a
  first message would otherwise refuse true sentences. "A message may only state things about the person that are in
  their quotes or why" is enforced for what the server can read, words in quotation marks (`unsaid_quote`), and
  otherwise by the prompt.
- **Refusal codes** (`kit.refused.<code>`, both languages): `no_draft`, `empty`, `too_long`, `contact`, `foreign_link`,
  `no_link`, `blanks`, `mixed_register`, `mixed_voice`, `past_talk`, `invented_experience`, `internal_note`,
  `asks_votes`, `several_people`, `unsaid_quote`, `forgotten`. `no_link` refuses an invite or an about text that leaves
  the owner's place out when the owner has one. The line and short profile texts carry no link, and a message carries one
  only from the stage `talked`.
- **Language.** Promotion has no language rule of its own: every call ends with the owner's output-language rule
  (`i18n/prompting.output_rule`). Profile and invite wording are in the owner's language. A message is in Korean when any
  of the person's recent quotes holds Hangul and in English when it has quotes without, else the owner's language. The
  prompt names each text's language, and the system prompt says it wins over the output rule.
- **The job.** `promotion_week` is a recurring kind (`STAGE_RECURRING`). `kit_prepare` is `Gate.ASKED`, queued with
  `by_owner=True` from `services/kit.py:request`, registered in `gate.POLICY`, `operations/growth.py` and the pinned
  sites of `tests/test_gate_sites.py`, and holds the lock key `kit:<business>`. The payload is `{business_id, parts,
  person_ids}`: the people are chosen at click time and re-checked when the job runs.
- **No idempotency key.** A job's key is unique for the life of its row, so a key per business and slots would refuse
  "Write again" for ever. A second click is refused instead with 409 while a `kit_prepare` job for the business is
  queued, parked or running, read under the business row lock so two clicks that arrive together queue one job.
- **Cost.** The ledger is written by `run_agent`, as for `promotion_week`; the kit adds the purpose `kit`, priced as a call
  the owner waits on. Columns: `call_id` (the `llm_calls.call_id` string, as `AgentMessage.call_id` does) replaces
  `llm_call_id`, `job_id` is a plain integer with no foreign key, and `id` is an integer like every table's.
- **Basis and stale.** `basis.profile` is not only codes: it holds each known value of the description as
  `line:code:provenance` (a sentence is hashed), so an edited sentence, or the owner confirming a guess, marks the text
  stale. A text that carries the owner's place is also stale when the first place changes; the line and short texts are
  not. A message is stale only by its person, as designed.
- **API additions.** `GET /kit` adds `owner_said` (the plan holds something the owner said about themselves: false means
  the section says the wording describes the product only), `prepared` (any text exists), `link: {url, state}` with state
  `ready` or `no_place`, and per person `reason` (`overdue` or `new`) with `reason_text`, and `no_draft_reason` (`public_first`
  or `not_written`) with `no_draft_text`. `can_write` means no write is running, the business is not archived and a plan is
  approved. `POST /kit/prepare` answers 200 `{queued, job_id, parts, person_ids}`; 400 for an unknown part, a person who gets
  no draft or has dropped; 404 for an unknown business or person; 409 while a write runs, with no plan, or with nothing to
  write; 422 for a body that does not parse.
- **A failed rewrite replaces a good text.** (Changed in the review fixes below: a refused result no longer replaces a
  good live text.)
- **Today.** The item shows only for an active business, and links to `/business?b=<id>&tab=people`.
- **Left for the console phase.** `web/` was not touched, so three checks wait on it: `web/src/i18n/areas/term.ts` is
  generated from the glossary (`uv run python -m justshipit.i18n.glossary`), `company.job.kit_prepare` and
  `label.purpose.kit` are needed by `tests/test_audit_labels.py`, and the activity verb `kit_prepared` has no label yet.

## Deviations found while building the console (phase 3)

- **Mount.** `FirstTenKit` (`web/src/business/Kit.tsx`) sits above `PeopleTab` in `pages/Business.tsx`, not inside it, so
  `People.tsx` is unchanged. It reads `GET /kit` on its own and reloads on the `jobs`, `activity_events`, `people`,
  `owner_tasks` and `businesses` tables (`kit_texts` is not a watched table), and polls every 4 seconds while `writing`.
- **The guess mark.** `FitMark` is not reused: it is private to `task.tsx` and draws the lines of a `fit_view` (which
  customer-description line was a guess, with its labels), while the kit sends one `guess` flag. The kit draws the same dashed
  note and the same "Check it" link (`CustomersLink`).
- **Write again is by part.** `POST /kit/prepare` takes parts and a person, not a slot, so Profile and Invite each have one
  Write again for the whole part, and a message has one per person. A refused or stale profile text points at its part's button.
- **The Today link** is `/business?b=<id>&tab=people#first-ten-kit`, so the route lands on the section. A folded kit opens when
  the address names it.
- **"Look for people"** in the no-one-today state calls the existing `POST /businesses/{id}/people/find`, which the console had no
  button for. "Add someone you know" scrolls to and focuses the add form's first field.
- **"I reached out"** shows only while the person may move to `contacted` (stage `found`). Someone already contacted whose
  follow-up is due is recorded with Record progress in the list below, which also sets the next step.
- **Left unused.** `milestone.estimated` is not shown: it counts estimated dates of the first ten, not people.

## Review fixes

A review of the branch found defects in the text checks, the store, the read model and the console. Each fix has a
regression test that failed before it (`tests/test_kit_review.py`, `tests/test_kit_race.py`, `tests/test_kit_job.py`).

**What changed**

- **Links and contact ids.** An address is any `name.tld` where the ending is 2 to 24 letters, all lower or all upper
  case, followed by `/` or the end of the word (`bit.ly/3xYz`, `discord.gg/abc123`, `mybrand.shop/try`). It ends at a
  space, a bracket, `—`, `–`, a quotation mark or Hangul, so `https://myshop.com에서` and `링크(https://myshop.com)에서`
  are the owner's place. `it.Then` is not an address (a capital after the dot), and neither are file names such as
  `node.js`. A messenger word followed by `@handle`, by "ID is ownerjk", or by a token with a digit or underscore is a
  contact, with no `:` needed. This is the kit's own check (`services/kit.py`, words in `i18n/match/kit.py`);
  `people.contact_info_problem` is unchanged for every other caller.
- **Placeholders.** The rewrite round no longer reuses the post card's "leave that spot as [My experience: ...]". The kit
  has its own reason for `invented_experience` (`kit.reason.invented`, both languages): remove that sentence and write
  nothing about the owner's experience. Any `[...]` of two characters or more in a text is refused as `blanks`.
- **A person forgotten while the job stores.** `_store` locks the person's row after the business row and before the kit
  rows. `people.archive` takes tasks, then the person, then the kit rows, so the order is the same where they meet and
  neither can wait on the other. `tests/test_kit_race.py` runs both interleavings on two connections. Only one of them
  failed before the fix: a forgetting that starts first let the job write past it. A job that stores first was already
  safe, because the new row's foreign key to the person blocks the forgetting's update of that row until the job commits.
- **Write again keeps a good text.** A refused result for a slot whose live row has a body is stored beside it as a row
  already replaced (the one-live-row-per-slot index is untouched), and the read model gives the text
  `last_refused: {refused, refused_text, at} | null`, set only while that attempt is newer than the live text. The console
  shows one quiet line under the text. A slot with no good text still shows the newest refusal as "Not written".
- **Good texts no longer refused.** The person's own handle is left out of the link and contact checks. A Korean sentence
  that places the words in the person's own post ("글에서 이야기했던 …") is not a past talk, unless it also says the two of them
  ("우리", "함께"). The business's name may stand in quotation marks. Only `to all/everyone/everybody/folks/both` is a group,
  so "talk to friends of yours" passes.
- **Job state.** `GET /kit` gains `job: {status, error_text, waiting_until, waiting_text} | null` (the latest `kit_prepare`
  job; the sentences come from the catalog, never from the row's own error text) and `prepared_at` (when a write last
  stored a text). The console says "The kit was updated." only when `prepared_at` moved while it wrote, shows the failure
  sentence for a failed or cancelled write, and shows "Waiting for the ... until ..." in place of "This can take a few
  minutes" while the job is parked on a usage limit.
- **Wording and messages are two calls** in one job. The call for profile and invite wording has no person block: none of
  their quotes, refs or handles. Each call has its own rewrite round, so a write of everything is two to four calls, each
  recorded in the ledger as before. A `MustWait` out of the second call leaves nothing stored, as before, so the first
  call is paid for twice on a wait.
- **Naming someone else.** A message is refused for naming another of the business's people only by an `@handle`, by a
  display name of two words or more, or by a one-word name or handle of four letters or more (three in Hangul) that is not
  in a short list of ordinary words (`COMMON_WORDS`). The reason names the word.
- **Today.** The kit line is not added when the kit cannot be written (no plan, or a write running) and has nothing
  prepared.
- **Passing the text on.** A text that asks the reader to pass it on to several people ("share it with your friends",
  "tell your colleagues", "친구들에게 공유") is refused as `asks_spread`, in an invite as well as a message. The invite's
  "Know someone who needs this?" asks about one person and is not matched. Quotations in `‘ ’`, `' '` and `「 」` are checked
  like `" "`; an apostrophe inside a word is not a quotation mark.
- The prompt says the same: no square bracket, no asking to pass it on, an id with or without an `@` is still an id.

**Still slips through, in either direction**

- A sentence run into the next with no capital ("quick.see you") reads as an address and is refused. A file extension
  that is also an ending (`.sh`, `.py`, `.md`) is let through, as `node.js` is.
- A messenger id with only letters that is shorter than five letters after "ID", or has no "ID" word at all ("DM me on
  Telegram, I am ownerjk", "카톡 아이디 jkim"), is not seen. A spelled-out address ("myshop dot com") is still left to the
  shared check.
- A name of two Hangul characters, or a handle of three letters or fewer, is named only with its `@`. A handle that is an
  ordinary word outside `COMMON_WORDS` ("grant") is read as a name and refused.
- The Korean "talk in their own post" rule is by words (글에서, 게시글, 댓글, 쓰신 ...): a past talk phrased after one of them
  in the same sentence passes, and a post cited in another sentence is not seen. English has no such rule and needs none,
  as its past-talk patterns all say "we".
- `asks_spread` knows a list of audiences (friends, colleagues, team, followers, 친구들, 주변 분들 ...), not every way to say it.
- The product name is the business's name. A product the owner calls something else in quotation marks is refused.

**Not solved**

- The raw model call files (`var/`) keep a forgotten person's quotes, as every other call file does. Forgetting a person
  scrubs the database, not those files. This is the existing pattern, not new in the kit.
- `writing` stays true while a reclaimed job's old holder may still be mid-call (the queue's normal reclaim window), so
  the owner can wait out that window before asking again.

## Checked on a real run (2026-11-16)

On the owner's local database, test business B-4 "Issue Digest (test run)", after backup
`var/backups/ship-20261116-113227.dump` and migration `d5f1a8c3e726`. Three test people were added by the owner's form:
an acquaintance already contacted with a next step one day overdue (Korean words), a person who came in through a
GitHub issue (English words), and a person found in a public Reddit post.

Seen on the real screen, with real model calls (three calls, about $0.09):

- Nothing prepared: the two sentences and one "Prepare the kit" button. One click wrote two messages, three profile
  texts and two invite texts in about 24 seconds; none was refused.
- The person from the public post got no draft and the line "Answer their post in your own words first".
- The Korean speaker's message came in Korean, the other in English; profile and invite carried the owner's place
  only where the design says.
- A second click while writing answered 409 with the catalog sentence.
- A new quote on a person marked their draft "Written before the latest change"; the other draft stayed unmarked.
- Copy by keyboard put the text on the clipboard and the button said "Copied". "I reached out" moved the person to
  contacted and the row left today's list.
- The Today screen of B-4 showed one line that opens the kit.
- English and Korean, dark theme, widths 1440, 1200, 900 and 390 with no sideways scroll.
- No decision card came from the kit, and the kit has no send path.

Two defects found on this run and fixed (branch `fix/kit-follow-up`):

1. The acquaintance who was already contacted got a first-contact message that said the owner "saw" their words and
   ignored the next step. The prompt now separates a first message from a follow-up and says how to write to someone
   the owner knows. Written again, the message asked whether they had tried it. The row now shows the next step.
2. The guess note repeated under all seven texts. When every text stands on a guess the section says so once.

Not seen on a real run: a refused text and its reason, a failed rewrite keeping the earlier text, the wait on the
subscription limit, a business with no place, the fold at 10 of 10, a first message to an acquaintance not yet
contacted, what a screen reader says. These are covered by tests or were seen only on a throwaway database.
