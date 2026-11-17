# Tracking: which channel brought the first users

Status: design, no code, grounded in `main` 18d1476. Roadmap 0.5. I could not open a browser or read the owner's
database, so nothing here says how many posts, people or GitHub points exist. Example tags, hosts and counts are made
up. The channel norms in 3.2 are the company's judgment unless a rule text is cited.

## Why

The owner posts by hand from a post to-do (`services/owner_tasks.py:211` `build_post_card`) and records the post's
address or a note (`done`, `:669`). The company reads HN and Reddit scores for that address (`services/metrics.py:376`)
and GitHub's referrers and paths for a business with `github_repo` (`metrics.py:255-264`). Nothing says which channel
a person came from. `people.source` is the relationship, not a channel (`services/people.py:32-35`). No card links to
the owner's product: `where_url` is where to post, and a business has no site address (`db/models.py:29`).

So 0.5 adds three things. Each post card gets a link to the owner's own place, tagged where a tag can be read back.
The owner records "how did you hear about us?" when they talk to someone, and taps a count for people who never go
on the list. A section on the Numbers tab counts posts, visits, people and users per channel, and labels each number
with where it came from. Nothing costs money and nothing needs a public server.

## 1. What can be measured, per product kind

The company runs on the owner's PC, so it cannot host a redirect or a landing page. A tag only helps where the
owner's own place keeps it and something the company can read reports it. Visits are never users.

| Product kind | Link on the card | Read back for free | Cannot be measured |
|---|---|---|---|
| CLI or library on GitHub | The plain repo address. GitHub drops the query, and `popular/paths` has no query (`metrics.py:262`) | GitHub referrer hosts, top 10, 14-day totals, push access needed (`metrics.py:243-250`). People's answers | Which post a visit came from when two posts share a host (HN and Show HN, every subreddit). Stars, clones and installs per channel |
| Package on npm or PyPI | The plain registry or repo address | Nothing per channel. People's answers and counts the owner taps | Downloads per channel. Registry download totals exist but give no source, so 0.5 adds no collector |
| Web app with its own sign-up | `?ref=<tag>` on the owner's site | Counts the owner reads in their own analytics, if any, and types in. People's answers | Anything the owner does not type in. The company never reads the owner's product database |
| Local shop | The map or store listing, plain, or no link. The owner says "tell us you saw this on <place>" | Counts the owner taps at the counter. People's answers | Walk-ins who do not say. Print and QR (not in 0.5) |
| B2B sold through conversations | Usually none | The answer the owner records after each conversation. Who the company found is known already | Who told whom inside a company |

Self-reports catch what links miss, such as a link passed on in a chat app, an email or a call. They are also
unreliable, since people forget or name the last place they saw the product, so the screen says which numbers are
told and which are read. First-launch visit-to-sign-up rates of about 0.2% to 5% come from anecdotes, and nothing
covers Korean channels, so no screen compares a channel to a benchmark or predicts one.

## 2. Data model

### 2.1 Reuse before adding

The post to-do owns its channel and when it was posted (`card["channel"]`, `done_at`). The person owns their stage,
and activity `person_moved` has each move (`people.py:472-475`). `metric_points` owns read numbers (`metrics.py:85`).
0.5 adds three columns, one card key, three channel keys and three metrics, and no table.

### 2.2 The owner's places: `businesses.doors`

`doors JSONB NOT NULL DEFAULT '[]'`, a list of `{kind, url, param}`. `kind` is `site`, `store`, `map`, `package` or
`repo`. `param` is the query name the tag rides in, or null when the place drops or cannot report it. It defaults by
kind: `site` gets `ref`, and the others get null. The owner may set `utm_source` when their analytics reads only UTM.
`services/tracking.set_doors` is the only writer and checks each address like `channels.link_problem`. A `repo` door
is derived on read from `github_repo`, never stored. The daily GitHub read starts keeping the repo's `homepage` (new
code in `read_github`, `metrics.py:287`). When it names a site and no `site` door exists, the Numbers section offers
it ("Your repository lists acme.dev. Use it?"), and one click stores it. No collector writes the owner's doors.

### 2.3 The link on a post: `card["track"]`

```python
class Track(TypedDict):
	tag: str                  # "<channel tag>-<base36 task id>", e.g. "hn-1c"; ^[a-z]{2,4}-[0-9a-z]{1,7}$
	link: Literal["tagged", "plain", "none"]
	links: list[DoorLink]     # [{"door": "site", "url": "https://acme.dev/?ref=dg-1c"}, {"door": "repo", "url": ...}]
```

`tracking.attach(s, task)` writes it wherever a post card is set on a to-do: in `_insert` after the flush
(`owner_tasks.py:457`, reached from `create_for_request` `:474` and `create_post_task` `:592`), and in `_take_over`
(`:572`), which replaces the whole card on a revision. A tag whose channel still matches is kept, so a retry or a
revision converges. The task id makes the tag unique with no counter and holds no name, business or person. The
model never writes it: `PostCardDraft` (`contracts/post_card.py:44`) is unchanged.

`resolve(channel_link, door)` is pure. A `tagged` channel and a door with a `param` give the address with
`?<param>=<tag>`. A `plain` channel, or a door with no `param`, gives the address as it is. A `none` channel, or a
business with no door, gives no link, and the console words the line to say (`tracking.say`) from the channel's name,
so no stored sentence is frozen in one language (I18N.md, "Prefer data over sentences").

### 2.4 "How did you hear": two columns on `people`

`heard_via varchar(40) NULL` holds a channel key or one of `friend`, `search`, `other`. `heard_words varchar(120) NULL`
holds the person's own words. `contact_info_problem` (`people.py:184`) screens the words like every other people
field, so the list still stores no email or phone (`test_people_privacy.py`). The code validates `heard_via` against
`channels.keys()` and the three words, since channel keys live in yaml. Each change writes activity `person_heard`
with `data={from, to, words}`, which is the history.

Null is not one state. The counter reads it with `source`:

| `heard_via` | `source` | Row on the screen | Why |
|---|---|---|---|
| set | any | that channel or word | The owner recorded it |
| null | `warm` | People you know | The owner knew them before |
| null | `public_problem`, `similar_tool`, `participation` | You reached out | The company found them, and the owner wrote first |
| null | `inbound` | No answer yet | They came on their own, and nobody asked |

The last three rows are derived and change the moment the owner records an answer. Nothing is backfilled.

### 2.5 Counts the owner enters: three metrics

| Metric | Stage, agg | Source, ref | Meaning |
|---|---|---|---|
| `heard_tally` | `first_value`, `sum` | `owner`, channel key or word | Customers not on the list who said they came from there, per day. The day's value is replaced, so a double tap is harmless |
| `tag_visits` | `interest`, `latest` | `owner`, tag | Visits the owner's analytics shows for that tag, as of the day typed |
| `tag_signups` | `interest`, `latest` | `owner`, tag | Sign-ups the owner's analytics or sign-up list shows for that tag |

They join `METRICS` (`metrics.py:51-69`), so a bet retro's `observed()` lists them as "told to us" and "typed from your
analytics" (`bet_retro.py:142`). `STAGE_METRIC` (`:492`) does not name them, so the plan's goal still counts the people
list (open question 3). With a tally and no people, `observed()` no longer reports first value as missing. The retro
wording has to say a tally is a count the owner told, and `tests/test_bet_retro.py` pins that.

### 2.6 Per-channel counts are derived

`services/tracking_count.py` is pure: `standing(posts, people, moves, points, doors, today) -> Standing`. The shell
`services/tracking.py` reads the rows and calls it. Nothing is stored.

```python
@dataclass(frozen=True)
class Figure:
	n: int
	basis: Literal["read", "told", "typed", "at_least"]   # read from GitHub; told by people; typed by the owner
	refs: tuple[str, ...]                                 # "OT-41", "PPL-7", "mp-88", as bet_retro cites them

@dataclass(frozen=True)
class ChannelRow:
	key: str                    # channel key, a word, or known | reached_out | no_answer
	posts: int                  # done post to-dos, a note-only post included (promotion.posts, :104)
	visits: Figure | None       # GitHub referrer views or typed tag visits, never both added
	signups: Figure | None      # typed only
	people: Figure              # live, not dropped, on the list with this answer
	got_value: Figure           # of those, at first_value or repeat (north_star's rule, people.py:393)
	tallied: Figure | None      # heard_tally sum

@dataclass(frozen=True)
class Mix:
	n: int                      # 10 or 100
	have: int                   # got-value people so far, capped at n
	by_key: dict[str, int]      # sums to have; no_answer is a key like any other
	reached_on: date | None     # the day the n-th person got value
```

Counting rules:

1. A person counts once, in one row. Moving from got value to came back does not count twice. A dropped or archived
   person leaves every count, as in `north_star`.
2. Users are got-value people. Posts, visits and sign-ups are never added to them or to each other.
3. Tallies stay in their own column and are never added to people. The tally form says "people who are not on your
   list". The server cannot see an overlap, and the screen says so.
4. GitHub referrer points are 14-day totals saved daily, so they overlap and are never summed. Per host the section
   shows the newest snapshot as "last 14 days, as of {date}" (`read`), and an "at least" total from snapshots taken 14
   or more days apart (`at_least`). A missed day makes the gap larger, never the number.
5. A host maps to channels through `domains` in `config/channels.yaml`. A snapshot is credited to a channel only when
   that channel has a done post in the 14 days up to the snapshot's day. When two channels share the host, the visits
   go on one host line that names both, never split and never given to one. A host with no such post (Google,
   github.com, t.co with no post on X) goes to "Found you on their own".
6. A row's visits come from the door its link pointed at: GitHub referrers for the repo, typed `tag_visits` for a
   site. One cell never adds the two.
7. The milestones order got-value people by their first `person_moved` row to `first_value` or `repeat`. A person
   moved before the activity log existed falls back to `updated_at`, and the line says "estimated for 2 people".
   Tallies are not in the milestones (open question 3).
8. `tests/test_tracking_count.py` holds the invariant: the sum of `got_value` over every row equals
   `north_star(...)["first_value"]`. A mismatch is a bug, not rounding.

### 2.7 Migration and backfill

One hand-written migration in the style of `a7c2e9d4b681_people.py` adds the three columns, with no CHECK on
`heard_via`, and writes no rows. Downgrade drops them. Its parent is the head, `7c1e5a9d3f20_team_chat`, which the 0.3
design (`customer-profile.md` on `feat/customer-profile-design`, 1.5) and the 0.4a design (`work-path.md`, 1.6) also
name. The three touch different tables, so whichever lands later moves its `down_revision` to the one before. Stop the
worker first, check that autogenerate leaves the LangGraph checkpoint tables alone, and let the owner apply it.

No backfill job. An open post to-do from before 0.5 gets a derived track on read in `owner_tasks.view` (`:822`), and
`attach` stores it on the next write. A done post keeps no tag, since none was posted, but still counts as posted and
still gates referrer credit (rule 5). Old people read through the table in 2.4. `metric_points` needs no migration.

### 2.8 Where 0.5 meets the other designs

- **0.4a request contract** (`docs/design/work-path.md`, 2). 0.5 files no decision and adds no `AgentRequestDraft`
  field. A request's post card reaches `_insert` through `create_for_request`, so `origin` and `context` are untouched.
- **0.3 customer description.** 0.5 never reads `audience.of_business`, which 0.3 deletes. The prep-card question keys
  on `source == "inbound"` alone, and `card["fit"]` and `card["track"]` are separate keys. Both designs edit every
  `channels.yaml` block (3.1) and `Numbers.tsx` (0.3 the stars row, 0.5 a section), so they land in sequence.
- **0.2 team chat and 0.2b merge cards** share no record with this design.
- **0.6 first 10 kit** takes its profile and invite links from `tracking.links_for(business, channel)`.
- **2.1 channel comparison** reads `standing` and adds the advice. `promotion.droppable` still judges a channel on
  business-wide first value (`promotion.py:115-133`), which is 2.1's to fix.

## 3. The link on the post card

### 3.1 Three keys per channel in `config/channels.yaml`

```yaml
hacker_news:
  tag: hn              # 2-4 lowercase letters, unique in the file; only makes a tag readable
  link: plain          # tagged | plain | none
  link_basis: none     # verified | secondary | none, as for rules: none means the company chose it
```

`channels.registry()` refuses a channel without them and a duplicate `tag`, as it refuses a `post_link` without an
example. Every value starts at `link_basis: none`, and 0.7 reviews them with the global channel list. The `region: ko`
channels get the same keys and keep their region, so the Korean places still work and are not widened.

### 3.2 Which places get a tag

A place gets a plain link when it names itself as the referrer and its readers watch for promotion (HN, Reddit), when
it adds its own tag (Product Hunt), or when the link stays on GitHub. There a tracking query adds nothing the company
can read and costs trust. Everywhere else the link is tagged, since chat apps, newsletters, in-app browsers and
`t.co` hide or blur the source and the tag is the only trace.

| `link` | Channels (start values, all `link_basis: none`) |
|---|---|
| `plain` | hacker_news, show_hn, reddit and its five subreddit entries, reddit_local, product_hunt, github_release, awesome_list |
| `tagged` | x, devto, hashnode, discord_global, discord_ko, dev_newsletter, geeknews, disquiet, okky, careerly, facebook_dev_groups, facebook_groups, naver_cafe, kakao_openchat, other |
| `none` | daangn_community, daangn_jobs, instagram_local, nextdoor |

`none` means the card offers the spoken line, not that the place bans links. Where the rules do ban them, the
channel's `rules` text already says so and the card shows it. No channel ever gets a shortened link. Shorteners cost
money or need a server, and some communities filter them.

### 3.3 What the owner sees

A **Your link** block in `PostCard` (`web/src/components/task.tsx`, after the Where block at about `:139`), with the
same open and copy buttons as Where. Each door is one line, the primary door first. Under it, one line on the choice:

- tagged: "This place hides where visitors come from, so the link carries `ref=dg-1c`. Your analytics shows it as the
  source."
- plain: "Hacker News shows up as the source by itself, so the link has no tag. People there dislike tracking links."
- none: "Say this in your post: Tell us you saw this on 당근. When someone mentions it, tap +1 on the Numbers tab."
- no door: "Add your site, store page or map listing on the Numbers tab, and the next cards get a link."

The model writes no link into the body. The outline checklist keeps its fixed last line (`fixed_check`).

## 4. The "how did you hear" line

### 4.1 Where it is asked and recorded

The owner's own product and its sign-up form belong to the owner. The company supplies the wording and a place to
record what people say.

1. **After a conversation.** The people list's record form (`RecordForm`, `web/src/business/People.tsx:40`) and the
   outcome of a person to-do (`people_tasks.Outcome`, `services/people_tasks.py:347`, not the `requests.py` class of
   the same name) get one optional field, **How did they hear about you?**: a select of the business's channels,
   then Someone told them, Search, Other, with a 120-character box for their words. It saves through
   `PUT /people/{id}/heard`, a fourth step after stage, quote and next. The select shows the derived row from 2.4 as
   its current value, and leaving it there stores nothing.
2. **On the prep card** of an `inbound` person (`person_card`, `people_tasks.py:129`), one fixed question, "How did
   you hear about us?", after the other questions. A person the company found or the owner knows needs no question.
3. **At the counter or in a DM**, for someone who never goes on the list, the Numbers section's **+1** per row.
4. **On the owner's own sign-up form**, the section's **Ask on your sign-up form** block gives the question and a
   short list of answers to copy, in the owner's language, with "ask it once, make it optional, keep a free-text
   Other". The owner reads the answers in their own product and types counts in.

When the owner types words instead of picking, `channels.named` (aliases in every language) proposes the channel.
A typed tag such as `hn-1c` resolves by its task id to that post's channel. No model call reads an answer.

### 4.2 Copy

Server words go in `i18n/messages/tracking.py`, console words in `web/src/i18n/areas/business-tracking.ts`. The Korean
is a draft to check against KOREAN-STYLE.md and `test_korean_style.py`.

| Key | English | Korean |
|---|---|---|
| `tracking.heard.label` | How did they hear about you? | 어디서 알게 됐대요? |
| `tracking.heard.question` | How did you hear about us? | 저희를 어디서 알게 되셨어요? |
| `tracking.heard.word.friend` / `.search` / `.other` | Someone told them / Search / Other | 누가 알려 줬어요 / 검색 / 기타 |
| `tracking.row.known` / `.reached_out` / `.no_answer` | People you know / You reached out / No answer yet | 아는 사람 / 먼저 연락함 / 아직 답 없음 |
| `tracking.row.organic` | Found you on their own | 스스로 찾아옴 |
| `tracking.signup.title` | Ask on your sign-up form | 가입 화면에서 물어보기 |
| `tracking.signup.options` | A post (where?) / A friend or colleague / Search / Other | 글을 보고(어디서?) / 지인 소개 / 검색 / 기타 |
| `tracking.tally.help` | People who are not on your list and said they came from here | 명단에 없고 여기서 왔다고 말한 사람 |
| `tracking.say` | Tell us you saw this on {place} | {place}에서 보고 왔다고 말해 주세요 |
| `tracking.too_few` | Too few people to tell channels apart yet. These are counts, not a ranking. | 아직 사람이 적어서 채널끼리 비교하기 일러요. 순위가 아니라 개수예요. |

The glossary gains one term, **Your link** / **내 링크** (code name `tracked_link`): "The address on a post card that
leads to your own site, store page or repository, with a tag when the place would hide where visitors came from."
Regenerate `term.ts` and PRODUCT.md's table. The answer itself reuses plain words, not a new term.

## 5. The screen

### 5.1 Where it lives

A **Where people came from** section on the Numbers tab (`web/src/pages/Business.tsx:325-330`), between
`NumbersTab` and `Reactions`. There is no promotion screen, and the Numbers tab is where the owner reads counts. The
referrer rows move out of `Reactions` (`web/src/business/Numbers.tsx:118`), so one number shows in one place. `GET
/businesses/{id}/tracking` (`api/growth.py`, beside `/numbers` at `:79`) returns `Standing`. `POST .../tally`, `PUT
.../doors` and `POST .../tag-counts` are reversible owner writes, so none files a decision card.

### 5.2 Layout

1. **First 10** and **First 100**, one line each: "First 10 users: 4 Show HN, 2 people you know, 1 no answer. 7 of 10
   so far." When a milestone is met: "10th user on Nov 20."
2. A table, one row per channel or word with anything in it: Posts, Visits, Sign-ups, On the list, Got value, Told
   you (tallied). Each figure carries a small text basis, "read", "told", "you typed" or "at least", never color alone.
   A shared host shows as one line, "news.ycombinator.com, Hacker News and Show HN". A row with a tagged link has
   **Add from your analytics**: visits and sign-ups for its tags, as of today.
3. **Your places** (the doors), with the repo door shown as derived, and the homepage offer when there is one.
4. **Ask on your sign-up form**, folded once the business has any answer.

Rows sort by got-value people, then by posts. While fewer than 30 people have an answer or a tally (the same 30 as
`bet_retro.MIN_PEOPLE`, `bet_retro.py:66`, so the retro and the screen agree), the head says `tracking.too_few`. The
screen shows counts only, never a percentage, a rate or a benchmark.

### 5.3 States

| State | What shows |
|---|---|
| Loading | Skeleton rows the table's height |
| No posts and no answers | "Nothing to count yet. Your next post card carries a link, and the people list asks how each person heard." |
| No door | The row table, plus "Add your site, store page or map listing so post cards carry a link" |
| GitHub traffic unreadable | Visits show "unknown" with the stored reason (`metrics.gap.traffic_access`), never 0 |
| Newest referrer point older than 2 days | "GitHub numbers as of {date}. The daily read has not run since." |
| Error | The last values, a line that the section could not load, and **Try again** |

### 5.4 Widths and themes

At 900px and below (`web/src/business/business.css:130`) the table becomes one block per row, each figure a label and
a value, so nothing scrolls sideways and no label truncates in either language. Colors come from the existing
tokens, so dark mode needs no new values. Bases are words and badges keep their border shapes. **+1** names its row
("+1 to 당근"), and the table has a caption and column headers for screen readers. 390px is out of scope, as in 0.3.

## 6. Safety and failure modes

- **A tag that leaks who the owner is.** A tag is a channel code and a base36 task id, built by the server. A test
  checks that no tag holds business or owner text and that every tag passes `contact_info_problem`. `set_doors`
  refuses an address with an email in it, as `people.py:280` does.
- **Private data in answers.** The words pass `contact_info_problem` or are refused whole. A tally holds no person.
- **Tracking seen as spam.** HN and Reddit get plain links, and the card says why. The company still never posts. The
  `link` values are the company's judgment until 0.7 checks them, and the place's own rules sit beside the link.
- **Counts that look precise.** Every figure has a basis word, referrer figures say "last 14 days" or "at least", a
  shared host is never split, unknown is not 0, and below 30 people the section says it cannot tell channels apart.
- **Double counting.** One row per person. Tallies, people, GitHub windows and typed visits are never added together.
  The invariant test (rule 8) catches drift.
- **A stale GitHub window.** GitHub keeps 14 days and the collector saves each day, so a week with the worker off
  loses those days. The section shows the newest point's date and warns after 2 days. A gap only lowers "at least".
- **A revised card, a retry, two writers.** `_take_over` replaces the card, so `attach` runs there too, inside the
  transaction that moves the to-do. The tally upsert replaces the day's value, and `set_doors` replaces the list under
  the business row lock. A retry of any of them changes nothing.

## 7. Phases

Each unit ends in a check. Run only the named tests (`uv run pytest -q tests/<file>`). No full suite, no browser.

1. **U1, records.** Migration, columns, the `channels.yaml` keys and load checks, the metrics. Check:
   `tests/test_channels.py` (a missing key or a duplicate tag fails the load) and new `tests/test_tracking_migration.py`
   on a fixture DB (columns added, no rows written, downgrade drops them).
2. **U2, links.** `resolve`, `attach`, the `_insert` and `_take_over` hooks, the derived track in `view`, `set_doors`.
   Check: new `tests/test_tracking_links.py` (every `resolve` case, the same tag after a retry, a new tag for a revision
   to another channel, a derived link on a pre-0.5 card with no write, no identity in a tag), `test_owner_tasks.py`,
   `test_promotion.py`, `test_owner_handout.py`.
3. **U3, answers.** The people columns, `PUT /people/{id}/heard`, `Outcome.heard`, the prep-card question, the tally
   and tag-count writes. Check: new `tests/test_tracking_heard.py` (names in both languages, a typed tag, the derived
   rows of 2.4, a repeated tally replaces), `test_people_privacy.py`, `test_people_api.py`, `test_people_cards.py`.
4. **U4, counts.** `tracking_count.standing`, `GET /tracking`, the referrer rows moved. Check: new
   `tests/test_tracking_count.py` (overlapping snapshots never summed, the "at least" chain, a shared host on one line,
   a host with no post on "their own", the invariant, milestone ties and the fallback), `test_metrics.py`,
   `test_bet_retro.py`.
5. **U5, console and copy.** Card block, record field, section, catalogs, glossary term. Check: `test_i18n.py`,
   `test_korean_style.py`, `test_i18n_particles.py`, `test_owner_text_surfaces.py`, `cd web && npx tsc -b && npm run
   build`. `/impeccable shape` runs with the owner before U5, and the owner checks the screens at http://127.0.0.1:8000.

Exit criteria for 0.5, fixed before it starts:

- A new post card shows **Your link** with a tag where the channel is `tagged` and the door keeps a query, the plain
  address otherwise, and the spoken line where there is no link. The model writes none of it.
- The owner records how a person heard in the people list and in a person to-do's outcome, and taps +1 for someone not
  on the list. A contact detail in the words is refused.
- The Numbers tab shows per-channel posts, visits, people and got-value people, each with its basis, and First 10 and
  First 100 by channel. GitHub numbers never sum overlapping windows, and the got-value total matches the people list.
- The owner has seen the section on a real business in both languages and both themes.

**Built and checked on a real run (2026-11-16).** Units U1 to U5 landed with migration `c4a8e1d7b295` (parent
`b8e2c6a4d1f7`). The owner could not do the shape round, so section 5 was built as written. The three open questions
took the recommendations: plain addresses on Hacker News and Reddit, no change to the planning prompts, and tallies
kept out of the first 10 and 100.

Where the code differs from the plan above:

- `standing` takes one `Facts` object. Typed visits win a row's visits cell, and GitHub shows there only for a host one
  channel owns.
- A tally's day is the owner's local day, the page sends the day it showed, and the server accepts today or the day
  before. A tally is dated at the owner's local midnight, never after now.
- A door address is refused for an email or userinfo, not for digits in its path (a Naver map listing's id read as a
  phone number).
- An open card reads its addresses from the owner's places as they stand, with the tag it has; finishing a post stores
  them as they stood then. Both were found on the real run: a card made before the first place was added showed no
  link for good, and its tag could not be counted.
- The owner's tallies do not open a bet review's routine verdict.
- The table becomes blocks at 900px and also when its own column is under 780px. A picker counts someone for a place
  that has no row yet, and a small -1 takes a slip back.
- A review found nine defects and the real run two more. All are fixed, each with a test.

Seen on a real test business (B-4): places saved (a site and a Naver map listing); **Your link** on a plain card
(Hacker News, a real weekly card from before this step, derived on read), on a tagged card (`?ref=xc-3`) and on a
card with only a line to say; how a person heard, refused with a phone number in the words and saved without; +1 and
-1; typed visits and sign-ups for a tag with "you typed" and the date; the table, the first 10 and 100 lines and the
"too few" note; English and Korean, light and dark, 1440, 1200, 900 and 390.

Not seen on a real business: GitHub referrer figures (`read`, `at_least`, a shared host on one line, the stale date
note), since the test business has no repository; the homepage offer; a weekly job making a card that carries a link
(the tagged and spoken-line cards were made by hand as fixtures); the heard field on a person to-do's outcome; and real
screen-reader speech. The tests cover the first four.

## 8. Design alternatives considered

Two whole shapes were sketched in parallel, tracking as its own four tables and tracking on existing records. A
cross-judge preferred the second, and so do I. The first copied the channel and tag into a link row beside the card,
lost the card key when `_take_over` replaced the card, and had the owner's product publish a counts file. From it I
kept the basis word on every figure, the mix that sums with a "no answer" key, and the "10th user on" line.

| Question | Options | Choice | Why |
|---|---|---|---|
| How a tag travels | UTM on the owner's URL; `ref`; promo codes; a spoken line; self-report only; a built-in redirect | `ref` on the owner's site (UTM if the owner asks), a spoken line where there is no link, self-report everywhere | A redirect needs a public server the PC cannot be. Codes cost the owner a discount. Self-report alone misses who never talks to the owner |
| Which places get a tag | every place; none; all but the places that name themselves | All but HN, Reddit, Product Hunt and GitHub | Those name themselves as the source, and HN and Reddit readers distrust tracking links |
| Where the tag lives | a `tracked_links` table; `card["track"]` | The card | One to-do is one post, and the task id is already unique. A table would copy the channel and the tag |
| Where the answer lives | an answers table; a column on `people`; activity only | Two columns plus an activity row | One answer per person. Tallies cover people not on the list. Activity only makes "no answer" a scan |
| People not on the list | a person row each; a daily tally | A daily tally in `metric_points` | No contact data, three seconds, and a retry replaces |
| Where the owner's places live | `businesses.product`; a table; `businesses.doors` | `doors` | `product` is the import contract. A table is too much for a short list with one writer |
| GitHub visits per channel | sum daily snapshots; one snapshot per post at day 13; newest plus an "at least" chain | Newest plus "at least", credited only to a channel that posted | The others overlap or claim a post-level precision the host-level data lacks |
| Web-app sign-ups | the product publishes a counts file the company polls; a connector to its database; the owner types counts | The owner types counts | A public counts file leaks the owner's numbers, and the owner's database is not the company's to read |
| Benchmarks | show typical rates; none | None | The research has anecdotes only, and none for Korean channels |

## 9. Open questions for the owner

1. **Should Hacker News and Reddit posts carry the plain address, with no tag?** Recommendation: yes. Both show up as
   the source on their own, and their readers watch for promotion. Chat apps, newsletters and phone apps hide the
   source, so links there carry the tag.
2. **When the company plans a web app with a sign-up, should the product plan include one line: keep the `ref` the
   visitor arrived with, and ask "how did you hear about us?" once, optionally?** Recommendation: yes, as a line you
   approve with the plan, like any other. Without it, the tags on your site are counted only if you have analytics.
3. **Should customers you tally (+1, not on the list) count toward the first 10 and 100 in your plan's goal?**
   Recommendation: not in 0.5. They show in their own column. The goal keeps counting the people list, where the
   company can tell got value from came back. A local shop may want them counted, so this returns at 0.9 with real
   numbers.

## 10. Proposed ROADMAP.md edit

The 0.5 row (`docs/ROADMAP.md:30`) says what is on screen but not how to tell it is done, so it fails rule 5. Replace
it with:

```
| 0.5 | 추적 | 새 홍보 카드에 오너의 사이트, 저장소, 가게 페이지로 가는 "내 링크"가 붙는다. 출처를 숨기는 곳에는 태그를 달고, 출처가 보이는 곳(Hacker News, Reddit)은 태그 없는 주소를, 링크를 못 쓰는 곳은 말할 문구를 준다. 회사가 쓰는 태그에는 오너와 사업 정보가 없다. 오너가 명단과 사람 할 일 결과에 "어디서 알게 됐는지"를 적고, 명단에 없는 사람은 +1로 센다. 연락처는 거부한다. 숫자 탭에 채널별 글, 방문, 명단의 사람, 도움 됨과 첫 10명·100명의 채널이 근거(읽음, 들음, 직접 입력, 최소)와 함께 보이고, GitHub의 14일 숫자는 겹쳐 더하지 않는다. 비율과 기준치는 보여 주지 않는다 |
```

## What I could not verify

- No browser, so no screen here has been seen. No owner database, so the number of posts, people per `source`, doors
  to derive and GitHub points is unknown.
- That HN and Reddit send a referrer naming them, and that Product Hunt adds its own `ref`, is common knowledge, not
  rule text I read. No rule text I read mentions tracking parameters, so every `link_basis` is `none`. Which tagged web
  places (dev.to, GeekNews, Disquiet, OKKY, Careerly) pass a referrer is unknown. A tag costs little there.
- That a repo address loses its query in GitHub's reports comes from what `metrics.py:262` stores, not GitHub's docs.
- Which analytics tools group by `ref`. The door's `param` lets the owner switch to `utm_source`.
- `customer-profile.md` was read from its branch, not `main`. No `merge-card-walkthrough.md` exists on any branch.
- The Korean copy has not been through `test_korean_style.py` or the particle test.
