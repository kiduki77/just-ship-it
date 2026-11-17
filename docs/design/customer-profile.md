# Customer description: who buys, how they are reached, what the founder has

Status: design, no code, grounded in `main` 549be0d. Roadmap 0.3. I could not open a browser or read the owner's
database, so nothing here says how many businesses exist or what the classifier returns for them. Example sentences
are made up.

## Why

`services/audience.py` sorts every business into `dev`, `local` or `general` by counting words
(`i18n/match/audience.py`) in five plan sections and the one-liner. A business with `github_repo` is `dev` whatever it
says. The owner never chose that value and cannot see it. It decides which channels the weekly promotion picks
(`services/promotion.py:238`), where the people finder searches (`services/people.py:84-103`,
`services/people_finder.py:100-136`), whether the numbers page has a GitHub stars row (`services/metrics.py:507`),
which questions a contact card asks (`services/people_tasks.py:135`), and whether agents are told to say "customers"
(`orchestration/context.py:104`, `i18n/prompting.py:36`). `general` hides consumer apps, services for small
businesses, content and professional services.

The owner chose three lines instead (who buys, how they are reached, what the founder already has), each
value with its source and confidence, shown on the business screen, asked once at the end of the kickoff, and the
only input those readers use. Market stays a separate field (`audience.market_of_business`), and the `region: ko`
channels keep working with no new Korean-only logic. The evidence behind promotion is thin and leans to developer
products (ROADMAP.md, "근거와 한계"), so the description picks places and no screen attaches a number to it.

## 1. Data model

### 1.1 Where it lives

On the business, in its own table, not inside the plan. A plan version means "the plan changed", and code reads it
that way. `door_rules.derive` drops its job when the approved brief is no longer the one it started from
(`services/door_rules.py:116,127`), project goals carry "plan v{n}", and approval refuses while a gate section is
unsettled. A one-click "right" would mint a plan version, trip all of that, and look to the owner like a plan change.

### 1.2 Table `customer_profile_values`

Append-only. The only update ever issued sets `replaced_at`.

| Column | Type | Meaning |
|---|---|---|
| `id`, `business_id` | int PK, FK indexed | |
| `line` | varchar(8), check | `buyer`, `reach`, `has` |
| `code` | varchar(40), nullable | A vocabulary key (1.4). No DB check, so the vocabulary grows without a migration |
| `sentence` | varchar(400), nullable | Free text for what the codes miss |
| `provenance` | varchar(9), check | `said`, `confirmed`, `guess`, `unknown`, the plan's own values (`roadmap.Provenance`) |
| `source`, `quote` | varchar(80), text | `answer:<id>`, `plan:<brief_id>`, `console`, `backfill:<brief_id>`; the owner's verbatim words behind a `said` value |
| `brief_id` | FK `business_briefs`, nullable | The approved plan when the row was written. Staleness reads it (6) |
| `written_by`, `cause` | varchar(40), varchar(24) | `owner` or `ceo`; `kickoff_answer`, `plan`, `plan_changed`, `backfill`, `owner_right`, `owner_not_right`, `owner_change`, `owner_undo` |
| `call_id`, `written_at`, `replaced_at` | varchar(40), timestamptz | A live row has `replaced_at` null |

A row is one coded value, the line's sentence, or the line's `unknown` marker. `CHECK (code IS NULL OR sentence IS
NULL)` and `CHECK ((provenance = 'unknown') = (code IS NULL AND sentence IS NULL))` enforce that. Partial unique
indexes allow one live row per `(business_id, line, code)` and one live code-less row per `(business_id, line)`. A
line with no live rows was never asked and reads as unknown.

`customer_profile_answers` holds the owner's own words for the description: `id`, `business_id`, `text` (2,000
characters, null when skipped), `asked_in` (`kickoff` or `business`), `kickoff_session_id`, `created_at`.
Append-only. The inference reads it, and quotes are checked against it.

### 1.3 Edits and history

`services/profile/store.py` is the only writer, and it writes a whole line at a time. `set_line` locks the business
row, retires the line's live rows and inserts the new set in one transaction. It does nothing when the new set equals
the live one. The caller passes `if_live`, the live row ids it read, so a slow model call never overwrites an edit the
owner made meanwhile. The inference may write only into a line whose live rows are all `guess` or `unknown`.

Every real change writes `activity.record(verb="profile", entity="business", data={line, cause, before, after})`. As
I18N.md asks, `data` holds codes and the console words them. **Undo** is `set_line(before)` with cause `owner_undo`.
A profile edit is reversible, so it never files a decision card.

### 1.4 Vocabulary

`services/profile/vocab.py` holds one tuple of codes per line. Labels live only in the server catalog
(`i18n/messages/profile.py`, `profile.code.<line>.<code>`), and the API sends them, so no console catalog repeats
them. The Korean is a draft to check against KOREAN-STYLE.md.

| Line | Code | English | Korean |
|---|---|---|---|
| buyer | `consumers` | People buying for themselves | 자기 돈으로 사는 개인 |
| buyer | `small_business` | Small businesses and shops | 작은 가게와 회사 |
| buyer | `professionals` | Professionals (doctors, lawyers, designers, tutors) | 전문직 (의사, 변호사, 디자이너, 강사) |
| buyer | `companies` | Teams inside larger companies | 회사의 팀과 담당자 |
| buyer | `developers` | Developers | 개발자 |
| reach | `search` | They search for it (web, maps, app stores) | 검색해서 찾아요 (웹, 지도, 앱 스토어) |
| reach | `communities` | Online communities where they gather | 모여 있는 온라인 커뮤니티 |
| reach | `introductions` | Introductions and word of mouth | 소개와 입소문 |
| reach | `local` | In person, nearby | 가까운 곳에서 직접 만나요 |
| reach | `direct` | Messages, calls or email to people you choose | 고른 사람에게 보내는 메시지, 전화, 메일 |
| reach | `short_video` | Short videos and creators | 짧은 영상과 크리에이터 |
| has | `network` | People you know who could buy or introduce you | 사거나 소개해 줄 아는 사람 |
| has | `customers` | Customers you already have | 이미 있는 고객 |
| has | `audience` | Followers or subscribers | 팔로워나 구독자 |
| has | `registration` | Registered to sell (a company or sole trader) | 사업자 등록 |
| has | `place` | A shop or place customers can visit | 손님이 올 수 있는 가게나 장소 |
| has | `budget` | Money for ads | 광고에 쓸 돈 |
| has | `time` | Hours each week for this | 매주 쓸 수 있는 시간 |
| has | `face` | Willing to show your face (video, events) | 얼굴을 드러낼 마음 (영상, 행사) |

The research proposed many more attributes: walk-in or mobile, appointments, delivery, radius, ticket size, repeat
visits, sales cycle, decision makers, proof needed, regulated category, demo-able in 15 seconds, network effects,
shareability, switching cost, product format. Each stays in the line's sentence until a reader branches on it.

**How it grows.** Add a code to `vocab.py` and its two labels to `profile.py`. Codes are never renamed or deleted.
A `retired` code drops out of the picker and the model's schema, and old rows still render it.
`tests/test_profile_vocab.py` fails when a code lacks a label in either language. It also fails when a `buyer` or
`reach` code is tagged on no channel and no place, since that code would steer nothing. In 0.3 the `has` codes feed
agent prompts and the screen only. A channel `needs` tag (for example `face` for short video) belongs to 0.7, which
reworks the global channel list.

The glossary gains `Customer description` / `고객 설명` (code name `customer_profile`): "Who buys, how they are reached,
and what you already have. The company picks places and people from it." Regenerate `term.ts` and PRODUCT.md's table.

### 1.5 Migration and backfill

One hand-written migration after `7c1e5a9d3f20_team_chat`, the current head. It creates both tables and writes no
data. The 0.4a design names the same parent, so whichever lands second moves its `down_revision`. Stop the worker
first, check that autogenerate leaves the LangGraph checkpoint tables alone, and let the owner apply it.

The owner runs the backfill while the classifier still drives the readers.

1. `ship profile backfill [--business N] [--dry-run]` enqueues one `profile_infer` job (2.1) per business that has an
   approved plan and no rows. It writes every value as `guess`, never `confirmed`, with `source = backfill:<brief_id>`.
   A line the plan does not settle stays `unknown`. `github_repo` goes into the prompt as a hint, so "a repository
   means developers" becomes a visible guess the owner can correct. Jobs run under the usage caps and wait at a cap.
2. `ship profile audit --compare` prints, per business, the old kind, the new codes, and the channels and places
   each would pick. The owner reads it before unit U4 switches the readers.
3. An archived business or one with no approved plan gets no rows. Its first approval runs the same job.

Until the owner confirms, promotion keeps working on the guesses and marks every card it picks from one (4.3).

## 2. How values are produced

### 2.1 One inference, three triggers

`profile.infer(s, business_id, *, answer_id, lang)` runs `run_agent("ceo", "customer_profile", prompt, ProfileOut,
business_id=...)`, the subscription CLI path the kickoff uses (`agents/executor.py`, `runtime/claude_cli.py`). The CEO
wrote the plan, and its guesses already read "CEO's guess" there.

```python
class ProfileValueOut(BaseModel):
	line: Literal["buyer", "reach", "has"]
	code: ProfileCode | None        # Literal built from vocab.py at import, like contracts/kickoff.SectionKey
	sentence: OneLine(120) | None   # only when code is null, in the owner's language
	owner_quote: str                # verbatim, as in BriefPatchLine; empty for a guess

class ProfileOut(BaseModel):
	values: list[ProfileValueOut]   # a line the material does not settle is left out, so it stays unknown
```

The prompt holds the answer and the plan lines `one_liner`, `customer_problem`, `path`, `known_people`, `owner_time`,
`budget_usage` and `background`, each inside `services/fence.fenced`. It also holds the live description and the
values the owner rejected, so the call refines instead of restarting. The server sets provenance, never the model. A
value is `said` only when its `owner_quote` is verbatim in the answer or in a `said` plan line and backs the value
(the `owner_quote.problem` rule that `kickoff._judge` uses). Everything else is `guess`. The inference never writes
`confirmed`.

Three triggers enqueue it. An answer to the kickoff question (5) or the card's text box uses job key
`profile:<bid>:answer:<id>`. The first plan approval or `ship profile backfill`, with no rows yet, uses
`profile:<bid>:plan:<brief_id>`, and so does approving a plan that changed those lines (open question 2). The keys
make every trigger idempotent.

### 2.2 Owner edits and unknown

`POST /api/businesses/{id}/profile/{line}` takes one `action`. The route reads the language once and passes `lang`
down, and refusals are `Said` values (I18N.md).

- `right` copies the line's `guess` rows as `confirmed`. It does nothing when no row is a guess.
- `not_right` writes the `unknown` marker. The next inference reads the retired rows as rejected and does not offer
  them again.
- `change` takes `codes` and an optional `sentence`. Picked codes are `confirmed`, as a picked chip is in the kickoff,
  and a typed sentence is `said` with itself as the quote.
- `answer` stores free text as a `customer_profile_answers` row and enqueues the inference.

Unknown is a state, not a gap to fill. Only a new answer or a plan change turns it back into a guess. Every reader
treats it the same way (3.1). It matches nothing that depends on it, and the screen says so and asks.

## 3. Readers

### 3.1 One read, one match rule

`profile.of(s, business_id)` reads the live rows and the approved plan, with no cache, as today.
`prof.has("reach", "local")` is true for any live value except unknown. `profile.fit(ch.fits, prof)` returns
`Fit(basis, guessed)`, `Unsure(lines)` or `Miss()`. A channel or place declares `fits: {buyer: [...], reach: [...]}`, and a line it leaves out means "any". It fits when
every declared line shares a code with the description. It misses when a declared line is known and shares none.
Otherwise a declared line is unknown, the result is `Unsure`, and the channel is not picked. `Fit.guessed` names the
lines whose matching values are guesses. `profile/core.py` holds `assemble`, `fit` and `stale` as pure functions.

A channel with launch `requires` items (Show HN, for example) is a one-time launch. It is picked only when the lines
it declares are `said` or `confirmed`, and otherwise skipped with "a one-time launch waits until you confirm who
buys" (open question 1).

### 3.2 Channel tags

`audience:` becomes `fits:` in `config/channels.yaml`. `channels.registry()` checks every code against `vocab.py` at
load, so a typo fails startup. `region` is untouched, so the Korean channels keep their market filter. The tags say
what each place is.

| Old `audience` | Channels | New `fits` |
|---|---|---|
| `[dev]` | hacker_news, show_hn, reddit_opensource, reddit_sideproject, reddit_selfhosted, reddit_claudeai, discord_global, geeknews, okky, careerly, facebook_dev_groups, discord_ko | buyer `[developers]`, reach `[communities]` |
| `[dev]` | devto, hashnode, dev_newsletter, awesome_list, github_release | buyer `[developers]`, reach `[communities, search]` |
| `[dev, general]` | product_hunt, disquiet, x | reach `[communities]` |
| `[local, general]` | naver_cafe, kakao_openchat, facebook_groups | buyer all but `developers`, reach `[communities, local]` |
| `[local]` | daangn_community, reddit_local, nextdoor | buyer `[consumers]`, reach `[local, communities]` |
| `[local]` | instagram_local | buyer `[consumers]`, reach `[local, short_video]` |
| `[local]` | daangn_jobs (`weekly: false`) | reach `[local]` |
| generic | reddit, other | `{}`, never picked weekly, as today |

Traced by hand for the Korean market, the old picks hold. A developer tool (developers, communities) gets the dev
channels plus product_hunt, disquiet and x. A shop (consumers, local) gets daangn_community, naver_cafe,
kakao_openchat and instagram_local, and not product_hunt. A consumer app (consumers, communities) gets product_hunt,
disquiet, x, naver_cafe and kakao_openchat. One case changes on purpose. An owner whose customers come only through
introductions gets no community channel, and the report says no known place reaches people that way.

### 3.3 Each reader

| Reader | Today | With the description |
|---|---|---|
| `promotion.pick` (`promotion.py:232-266`) | `kind in ch.audience` | `profile.fit`. `Unsure` adds one skip reason per line. `Fit.guessed` goes on the card (4.3) |
| `people.places`, `listing` (`people.py:97,419`) | `SEARCHED[market][kind]`, `BY_HAND[market][kind]` | Each `Place` gains `fits` and a role per market (`searched`, `by_hand`). Both tables go. With nothing to search, the People tab asks (4.3) |
| `people_finder.where_to_look`, `_where`, the drop rule (`:100,341,369,433`) | the same tables | `places(prof, market)`. The prompt and the drop rule read one list |
| `people_finder.where_it_is` (`:122`) | `local` only | `prof.has("reach", "local")` |
| `people_finder.people_prompt`, `threads_prompt`, `promotion.run_week` | the plan | the plan plus `profile.prompt_block(prof, lang)`, fenced |
| `metrics.stage_trends` (`metrics.py:507`) | stars when `dev` | stars when `prof.has("buyer", "developers")`, with a note while that is a guess |
| `people_tasks.person_card` (`:135`) | `contact_local` when `local` | `contact_local` when `prof.has("reach", "local")` |
| `context.project_brief` (`context.py:104`) | `LOCAL_WORDS` when `local` | `LOCAL_WORDS` when `prof.has("reach", "local")`, plus the description block |

A grep for `audience`, `of_business`, `words_for` and `LOCAL_WORDS` over `backend/`, `config/`, `prompts/`, `tests/`
and `web/src` found no other reader, and no API response or web type exposes the kind. `Bet.audience`,
`NextBet.audience` and `owner_time.of_business` only share the name and stay. Guesses count in every reader, because
the owner decided promotion keeps working on them. The marks make that visible.

### 3.4 Transition and deletion

The mapping from the old enum lives in tests and the audit, not in code. `dev` becomes buyer `developers`, `local`
becomes reach `local`, and `general` sets neither. In one wave, after the owner has read the audit, every reader
moves to `profile.of`. The same commit deletes `audience.of_text`, `of_business`, `words_for`, `_DEV`, `_LOCAL`,
`i18n/match/audience.py`, `channels.Audience`, `AUDIENCES`, `Channel.audience`, `people.SEARCHED`, `people.BY_HAND`
and `tests/test_business_audience.py`. `audience.py` keeps `market_of_text`, `market_of_business` and `_brief_text`.
`tests/test_profile_readers.py` fails if a module imports a deleted name.

## 4. Console

A shape brief with no human round (`.claude/skills/impeccable/reference/shape.md`), so every choice is an assumption
until `/impeccable shape` runs with the owner before unit U5. The owner checks in a few times a day, reads three plain
sentences, fixes a wrong one in two clicks, and sees which values are only the CEO's guess.

### 4.1 Placement and layout

A **Your customers** card on the business Overview tab (`web/src/pages/Business.tsx`), below `OwnerWork` and beside
`BriefSummary` in `.cols-main`. The Plan tab links to it under `customer_problem`. Each line is one row:

1. A small label: **Who buys**, **How they're reached**, **What you already have**.
2. The sentence, or one composed from the code labels (`profile.compose.<line>`, "Small businesses and
   professionals buy it."), with the list placed so no particle depends on it.
3. `ProvBadge` (`web/src/kickoff/Brief.tsx`) with its labels "You said it", "You confirmed", "CEO's guess",
   "Unknown". The row shows its weakest value's badge, and **Details** shows each code's badge.
4. **The company uses** and the code labels, quieter. Readers branch on codes and a sentence can drift from them, so
   the owner confirms what steers the work.
5. Text buttons **Right** (hidden when said or confirmed), **Not right** and **Change**.

**Change** opens the row in place, with the codes as checkboxes in vocabulary order, a sentence field, **Save** and
**Cancel**. **Not right** marks the line unknown and opens the same editor under "What is it instead?". **Save** shows
"Changed. Undo". The activity row keeps the undo after the toast goes.

### 4.2 States

| State | What shows |
|---|---|
| Loading | Three skeleton rows the card's height |
| Not written yet (before backfill, or no approved plan) | "The company hasn't written this yet." with the kickoff question and an answer box |
| Reading an answer | The previous values and "The CEO is reading your answer." No spinner on the rows |
| Unknown line | Dotted badge, "Not known yet. Promotion and the people finder skip places that depend on it.", **Tell us** |
| Backfilled | Every row "CEO's guess", and in the card head "The CEO wrote these from your plan. Check each one." |
| Stale (6) | "Your plan changed on {date}, after this was written. Is it still right?" beside **Right** |
| Error | The last values, a line saying the card couldn't load, and **Try again** |

The Today list (`services/today.py`) shows one non-blocking item, "Check who your customers are", while a backfilled
guess has had no owner action or while `buyer` or `reach` is unknown (open question 3).

### 4.3 Marks elsewhere

- **Promotion card** (`PostCard`, `web/src/components/task.tsx:131`). At pick time the server writes
  `card["fit"] = {"guessed": [...], "codes": {...}}` into the post to-do. The model never writes it. The card shows
  "Picked from a guess about who buys: small businesses. Check it" and links to the customer card. When the owner has
  since rejected a value the pick used, the card says "Your customer description changed after this was picked."
- **Elsewhere.** The promotion report gives one skip reason per unknown line. With nowhere to look, the People tab
  (`web/src/business/People.tsx`) says "Tell the company who buys and how they're reached, and it'll know where to
  look." While developers is a guess, the stars row says "Shown because the CEO guesses your buyers are developers."

### 4.4 Copy outline

The server area `i18n/messages/profile.py` holds code labels, compose sentences, refusals and activity words. The
console area `web/src/i18n/areas/business-profile.ts` holds the card. Skip reasons go in `i18n/messages/promotion.py`.
Glossary words only (계획, 결정, 내 할 일, 시작 대화), Korean per KOREAN-STYLE.md. `lines` joins
`ENDS_IN_HANGUL_OR_DIGIT` in `tests/test_i18n_particles.py`, since every line label ends in Hangul.

| Key | English | Korean |
|---|---|---|
| `business.profile.title` | Your customers | 고객 설명 |
| `business.profile.line.*` | Who buys / How they're reached / What you already have | 누가 사나 / 어떻게 닿나 / 이미 가진 것 |
| `business.profile.right` / `.not_right` / `.change` | Right / Not right / Change | 맞아요 / 아니에요 / 고치기 |
| `business.profile.unknown` | Not known yet. Promotion and the people finder skip places that depend on it. | 아직 몰라요. 홍보와 사람 찾기가 이 값이 필요한 곳은 건너뛰어요. |
| `task.post.fit_guess` | Picked from a guess about who buys: {list}. Check it | 누가 사는지 짐작해서 고른 곳이에요({list}). 확인해 주세요 |
| `promotion.skip.profile_unknown` | Needs to know {lines} | {lines|을} 알아야 해요 |
| `promotion.skip.launch_guess` | A one-time launch waits until you confirm who buys | 한 번뿐인 출시라 누가 사는지 확인한 뒤에 올려요 |
| `kickoff.profile.question` | One last question. Who is your first customer, and how will you meet them? | 마지막으로 하나만 물어볼게요. 첫 고객은 누구이고, 어떻게 만날 생각이세요? |

**Widths and readers.** At 900px and below (`business.css:130`) the card is one column and the buttons wrap under
the sentence. No text truncates in either language. Badges keep their border shapes, so state is never colour alone.
Each button names its line ("Not right: who buys"), and the checkboxes are a labelled group. 390px is out of scope.

## 5. Kickoff

**The question.** When a session reaches DONE for any reason except abandoned and the business has no customer
answer, `kickoff.view` returns `customer_question`. The console shows it as the CEO's last message after the closing
turn, with an answer box and **Skip**. An answer becomes a `customer_profile_answers` row and enqueues the inference.
**Skip** stores a row with null text, and the first plan approval infers from the plan alone. The question comes last
because by then the plan holds `customer_problem`, `known_people`, `owner_time` and `budget_usage`, so it asks only
what the plan rarely says, who pays and where the owner will meet them.

**Not a `KickoffTurn`.** The question is server text, like the strengths turn 0 (`kickoff._answer_strengths`), and
uses no CEO turn. A row would collide with three rules. `turns_used <= max_turns` is a DB check, so a question after a
closing turn 20 fails. `continue_on` numbers from `turns_used + 1`. `run_turn` expects `turns_used == n - 1`. The
20-turn budget, `TARGET_TURNS = 12` and `TURN_CAP = 25` stay as they are.

**Failure modes.** An early `finish` still runs the closing turn and ends DONE, so the question shows. A skip or a
closed tab leaves the plan-derived guesses, and the card asks. A failed or capped inference retries under the queue's
rules while the card shows the previous values. Two answers (kickoff and card) are two jobs, and neither overwrites a
said or confirmed line. A session that `continue_on` reopens asks nothing new.

## 6. Safety and failure modes

- **A wrong guess steering promotion.** Every card picked from a guess says so and links to the line. A one-time
  launch needs a confirmed line. A rejected value becomes unknown, and cards already picked from it say the
  description changed. A guess can still send ordinary posts to the wrong community until the owner looks. The Today
  item and the backfill note reduce that. They do not prevent it.
- **A stale description after a plan change.** Each row keeps the `brief_id` it was written against, and
  `core.stale` compares that plan's lines with the current plan's on read, with no hook to forget. With open question
  2, a changed plan also re-runs the inference on guess and unknown lines. Said and confirmed lines show the stale
  note, and a typo fix flags them too. One **Right** clears it.
- **Prompt injection through the owner's answer.** The answer and the plan lines enter the prompt only inside
  `fence.fenced`. The schema allows only vocabulary codes and a 120-character sentence. The server sets provenance,
  so text cannot claim `confirmed`, and `said` needs a verbatim quote. Readers branch on codes only, and sentences
  reach other agents only through the fenced `prompt_block`. A hostile answer can at most mislabel its own business's
  description, which the owner sees on the card.
- **Two writers at once.** The row lock and `if_live` refuse the later write, and the inference refuses any line
  that holds an owner value, so the owner's edit always wins. The load check and `tests/test_profile_vocab.py` catch
  a tag that drifts from the vocabulary.

## 7. Phases

Each unit ends in a check. Run only the named tests (`uv run pytest -q tests/<file>`).

1. **U1, records.** The migration, the models, `vocab.py`, `store.py`, the activity rows. Check: new
   `tests/test_profile_store.py` on a fixture DB (a line replaced whole, a repeated write changes nothing, a stale
   `if_live` is refused, the inference cannot touch a said line, undo restores the line) and
   `tests/test_profile_vocab.py`.
2. **U2, inference and kickoff.** `contracts/profile.py`, `profile.infer`, the job, the answer route,
   `customer_question`, `ship profile backfill` and `audit --compare`. Check: new `tests/test_profile_infer.py` (the
   quote rule gives said or guess, nothing is ever confirmed, an unsettled line stays unknown, injected text stays
   fenced), `tests/test_kickoff.py` (the question after DONE and after `finish`, never after abandon, `turns_used`
   unchanged) and `tests/test_kickoff_strengths.py`.
3. **U3, match rule and tags.** `core.fit`, `fits` in `channels.yaml`, `Place.fits`. Check: new
   `tests/test_profile_fit.py`, which replays the four traces of 3.2 and the old tables at `people.py:84-94` per
   market, with every intended difference named. Also `tests/test_channels.py` and `tests/test_people_sources.py`.
4. **U4, readers and deletion,** one commit after the owner reads the audit. Check: `tests/test_promotion.py`,
   `test_people_local.py` (rewritten on descriptions), `test_people_finder.py`, `test_people_cards.py`,
   `test_owner_handout.py`, `test_metrics.py`, `test_bet_retro.py`, and new `tests/test_profile_readers.py` (no import
   of a deleted name, a guessed line marks the card, a one-time launch skips on a guess).
5. **U5, console.** The card, the editor, the marks, the kickoff message, the catalogs. Check: `tests/test_i18n.py`,
   `test_korean_style.py`, `test_i18n_particles.py`, `test_owner_text_surfaces.py`, `cd web && npx tsc -b && npm run
   build`, then http://127.0.0.1:8000 in both languages and themes at 1440 and 900. `/impeccable critique` into
   `.impeccable/critique/`, then `/impeccable polish`.

Exit criteria for 0.3, fixed before it starts:

- The business screen shows the three lines with their marks, and the owner marks each right, not right or changed
  with an activity row and no decision card.
- On a `[TEST]` business the kickoff ends with the question and the answer fills the lines. A skip leaves guesses or
  unknown, never a forced value.
- Promotion, the people finder, the numbers page and contact cards read only the description. A card picked from a
  guess says so. `audience.of_business` and the word lists are gone.
- Every existing business has a backfilled description of guesses, the owner has read `audit --compare`, and the
  owner has checked the card on a real business in both languages.

## 8. Design alternatives considered

Two whole shapes were sketched in parallel, the description as three plan sections with tagged lines and the
description as its own record. The base is its own record. The plan-sections sketch gave the vocabulary, the rule of
every declared line with any value inside a line, the channel table, and the single skip reason per line. Its own
weakest point decided against it. A plan version means "the plan changed", and door rules, goal labels and approval
gates read it that way.

| Question | Options | Choice | Why |
|---|---|---|---|
| Where it lives | three plan sections; a JSONB column on `businesses`; its own append-only table | Own table | Edits need no plan version or approval. JSONB keeps no per-value history, and two writers overwrite each other |
| Shape of a value | one category; free text; closed codes; codes plus a sentence per line | Codes plus a sentence | Readers need codes. Free text alone brings word counting back. Codes alone lose "after a call" |
| Where codes live | yaml with labels; Python tuples with labels in the catalog | Python and the catalog | The i18n lint already checks the catalogs |
| Confidence words | new words ("company guess"); the plan's provenance | The plan's provenance and badges | One word per idea, and the owner already reads "CEO's guess" |
| Unknown in matching | matches anything; misses; abstains and asks | Abstains and asks | The owner decided unknown means ask. "Anything" would send a shop to Hacker News |
| Backfill | the old classifier's mapping; a model call per business over its plan | A model call, all guesses, with `audit --compare` | The mapping keeps the hidden logic alive. The audit gives the same check for review |
| The kickoff question | a CEO turn; a server-written `KickoffTurn`; server text after DONE | Server text after DONE | Turn checks and numbering stay as they are |
| Overlap with `known_people`, `owner_time`, `budget_usage` | move them into the description; derive from them | Derive | Those sections hold names and numbers other code reads |

## 9. Open questions for the owner

1. **Should one-time launches (Show HN, Product Hunt) wait until you confirm who buys?** Recommendation: yes. A
   launch happens once, so a guess wastes it. Ordinary posts keep going on a guess, with the mark.
2. **When a later plan changes who it is for, may the CEO rewrite the guessed lines itself?** Recommendation: yes,
   guesses and unknowns only, one model call per such approval. Lines you said or confirmed get only the "still
   right?" note.
3. **Should the Today list carry "Check who your customers are"?** Recommendation: yes, one non-blocking item while a
   backfilled guess has had no action from you, or while who buys or how they're reached is unknown. It goes after
   your first right, not right or change.

## 10. Proposed ROADMAP.md edit

Replace the 0.3 row (`docs/ROADMAP.md:26`) with:

```
| 0.3 | 고객 설명 | 사업 화면에 "누가 사나 / 어떻게 닿나 / 이미 가진 것"이 한 줄씩 보이고, 오너가 줄마다 맞아요, 아니에요, 고치기를 누른다. 고친 내용은 결정 카드 없이 활동 기록에 남는다. 값마다 직접 말함, 확인함, CEO 짐작, 모름이 붙는다. 시작 대화 끝에 한 번 묻는다. 홍보, 사람 찾기, 숫자, 연락 카드가 이 값만 읽고, 짐작으로 고른 홍보 카드에는 그 표시가 보인다. 채널은 같은 값으로 표시되고 단어 세기 분류는 코드에서 없어진다. 기존 사업은 계획을 보고 짐작으로 채우고 오너에게 확인을 묻는다 |
```

## What I could not verify

- No browser, so no screen here has been seen.
- No owner DB, so the number of businesses, their current kinds and what the backfill will infer are unknown.
- The traces in 3.2 are worked by hand from `config/channels.yaml` and `people.py:84-94`, not run.
- Whether `test_metrics.py`, `test_people_cards.py` and `test_owner_handout.py` pin a developer business through
  the classifier. Unit U4 finds out.
- The Korean copy has not been through `test_korean_style.py` or the particle test.

## Built

Branch `feat/customer-profile`, one commit per unit in the order U1, U2, U3, the audit command, U5, the fixes from an
independent review, this note, and U4 last. U4 is the switch-over: the readers move to the description and the classifier is deleted. The owner reads
`ship profile audit --compare` on the branch without it, then takes it. Where the code disagreed with this design,
the code won. The differences:

- **Neighborhood tags.** daangn_community, reddit_local and nextdoor fit reach `[local]`, not `[local, communities]`.
  Under the match rule, the table's tag sent a consumer app reached through communities to them, which section 3.2's
  own trace rules out. The traces in 3.2 hold for both markets with that change, and `tests/test_profile_fit.py`
  freezes the old picks.
- **Places.** Each `Place` has `fits`, a `roles` entry per market (`searched` or `by_hand`) and a `rank` that keeps
  today's order. Reddit needs `fits_in` per market, because it is a topic community in Korea and a city's subreddit in
  English. `email` and `direct` carry `introductions` and `direct` so those codes steer something, but no market
  offers them yet.
- **One-time launches.** They wait until every value of who buys, and every value the channel's declared lines match,
  is one the owner stands behind: confirmed, or words the owner typed. A `said` value the CEO read from a quote does
  not count (open question 1 said "confirm"), and **Right** turns it into confirmed. Every launch channel declares
  reach, so the skip reason names both lines.
- **Quote rule.** A quote backs a code only when it names one of the label's words (in Korean, with an ending of at
  most two characters), not a generic word such as "people", and does not deny it. The shared-word rule the kickoff
  uses let a Korean word for "the company" back the word for "buys" inside it, and "not for developers" back
  developers.
- **Draft plans.** A value read while the plan is still a draft names no plan, because the kickoff rewrites the draft
  under the same id. The first approval then reads the guessed and unknown lines again.
- **Editor.** Saving with the CEO's sentence left as it was confirms it instead of marking it said.
- **Skip reasons.** An unknown line gives one reason keyed `profile:<line>`, not one per channel.
- **Composed sentence.** "Who buys: Small businesses and shops; Professionals (...)" replaces "... buy it". Most
  labels do not read as the subject of a sentence. The card shows the line label once and lists the values.
- **Store.** A repeated write compares code, sentence and provenance only. The same values written against a newer plan
  still count as a write, so **Right** or a re-read clears the stale note.
- **Queue.** `profile_infer` is gated like `derive_door_rules`, runs one at a time per business, and archiving a
  business closes a waiting job instead of refusing.
- **Today.** The item shows until the owner first acts on the description (open question 3), whether or not a line is
  unknown.
- **Post cards.** `fit_view` drops a guessed line the owner has since confirmed.
- **People finder.** It makes no model call when no place fits. The People tab then asks for the description, but it
  cannot tell an unknown line from a description that no place reaches.
- **Audit.** It needs `--compare`. After U4 it lists only what each business picks now, since the word count is gone.
- **Agent prompts.** The description block is English in the finder and the project brief, the working language of
  those prompts, and in the owner's language in promotion.

Not verified: the screens (no browser, so no `/impeccable shape`, critique or polish, and no check at 1440 and 900 in
either theme or language), and the backfill and audit on the owner's database. The migration is
`d4c8e1f6a2b3_customer_profile` on `7c1e5a9d3f20`, written by hand and not applied.

## Checked on a real run (2026-11-15)

Two test businesses on the owner's database, console rebuilt, real model calls.

- Seen working: the card's three lines with all four marks in English and Korean, at 1440 and 900, light and dark;
  Right, Not right, Change and Undo; one activity row per edit and no decision card; the kickoff question shown once
  after the chat ends, by Skip and by an answer, and gone after either; the answer's own sentences kept as quotes; the
  Today item until the owner first acts; the promotion picker choosing channels from the description and naming the
  guessed line; the people finder running on it.
- Fixed: Undo stored the company's values as the owner's own words (`store.undo`), so Right vanished from the line.
- Seen after a fix to the weekly promotion: the guess mark on a post card ("Picked from a guess about who buys:
  Developers. Check it", linking to the card), in English and Korean, at 1440 and 390, light and dark. Before the fix
  three weekly runs made no card: the marketing agent wrote cards for two of five channels and the card rules refused
  both each time (`forbidden_ask` on a checklist line that warns against asking for votes, `outline_too_long`), and
  such a week left no activity row. The week now asks once more for each refused or unwritten channel, with the
  reason, and is recorded whatever came of it. On the next real run the first cards failed (`outline_many_sentences`)
  and the rewritten two passed.
- Seen only half through the product's own path: those two cards passed the checks in the weekly job, but the test
  business had no started stage, so the stage gate held them (`owner_task_held`). They were stored as to-dos by hand
  from the job's own output to look at the screen. A weekly job making a marked to-do by itself, under a started
  stage, is not seen yet.
- Seen: the backfill. A test business's rows and its earlier reading job were set aside (table
  `zz_set_aside_profile_b5`), which is what a business from before this step looks like. `ship profile backfill`
  queued it, the job wrote all three lines as guesses with cause `backfill`, the card said "The Project Manager wrote
  these from your plan. Check each one", and Today asked the owner to check.
