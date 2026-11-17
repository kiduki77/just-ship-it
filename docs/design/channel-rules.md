# Channel rules: a place whose rules were not read gets no post written

Status: design, roadmap 0.7, grounded in `main` ecec9b4. File and line references come from a code survey on that
commit. What was read at each place's own page on 2026-11-16 is in §2.

## Why

The company writes posts, or points for a post, for outside places (`config/channels.yaml`, 30 entries). Each entry
says how its rules are known (`basis`: `verified`, `secondary`, `none`), but nothing acts on that value:

- `promotion.pick` (`services/promotion.py:237`) has no `basis` check. It picks nine global places whose rules were
  never read, and for three of them (`devto`, `hashnode`, `dev_newsletter`) the company writes a full title and body.
- `owner_tasks.build_post_card` (`services/owner_tasks.py:212`) stores `rules_basis` on the card. No screen shows it.
- The agents' preamble says "if the rules of a channel were not read, add a checklist line" (`prompts/
  company_preamble.md:51`) and never says which channels those are.

A post that breaks a place's rules gets the owner's account removed or banned, and the owner cannot see that the
company never read those rules. 0.7 makes the state visible and makes the company stop writing where it did not read.

## 1. The rule

One state per place, derived, never stored on its own:

| State | When | Shown as | What the company does |
|---|---|---|---|
| `checked` | `basis: verified` and `checked_on` set | "Rules checked" with the date and a link to the source | Writes as today (points for an outline place, a draft for a draft place) |
| `unchecked` | anything else | "Rules unchecked" / "규칙 미확인" with the reason | Writes nothing: no card from the weekly promotion, and a card asked by an agent is refused |

- `secondary` counts as unchecked. A summary on someone's blog is not the rule text (ROADMAP 1.2: only what was read
  on the platform itself is "checked").
- The reason is one of three codes, `unchecked_why` in the registry: `not_read` (a source exists and we could not
  read it), `per_unit` (the rules belong to each server, list, newsletter or subreddit, so no single text exists),
  `no_source` (no rule page was found).
- `checked_on` is a date in the registry, required exactly when `basis: verified`; the registry fails at load
  otherwise. The date is shown, so the owner can judge its age. Nothing expires on its own in 0.7.
- The rule covers every region. Korean places are not researched again in 0.7 (ROADMAP: kept, not widened), so every
  Korean place except GeekNews is unchecked and gets nothing written.
- The owner's own place is not an outside place. A card on `other` whose `where_url` host is one of the business's
  own places (0.5, `tracking.places`) has no outside rules to read and is written as today. Any other use of `other`
  that `channels.named` cannot resolve to a checked place is unchecked.

## 2. What was read (2026-11-16)

Each page was fetched from the platform's own domain and the quoted sentences were found in the raw page text (a
script searched the downloaded HTML for each phrase). Nothing comes from a blog, a mirror or memory.

| Place | State after 0.7 | Source read | What the registry changes |
|---|---|---|---|
| `hacker_news` | checked | news.ycombinator.com/newsguidelines.html, /newsfaq.html | Rules text corrected: "Please don't put generated text in HN posts", "please don't automate posting"; "AI-edited" is said of comments only |
| `show_hn` | checked | news.ycombinator.com/showhn.html | The generated-text rule is attributed to the guidelines page, not this one |
| `github_release` | checked | docs.github.com acceptable use policies | None |
| `product_hunt` | checked (was `secondary`) | help.producthunt.com launch guide: articles 17734384 (promote, what to avoid), 17834537 (product page), 17734411 (relaunch) | Rules rewritten from the text: "Never ask for upvotes", no paid upvotes, no fake or extra accounts, "Skip link shorteners", name 40 / tagline 60 / description 500 characters, a relaunch inside two months needs a request. The 180-day interval stays and is the company's own choice (`repost.basis: none`) |
| `devto` | checked (was `none`) | dev.to/terms, dev.to/guidelines-for-ai-assisted-articles-on-dev | **Mode changes from draft to outline (self-written).** The AI page says AI-assisted articles "should not… Promote any business, program, or course (including your own)", so the company may not write this article. Terms: not "designed primarily for the purposes of promotion or creating backlinks"; affiliate links disclosed |
| `hashnode` | checked (was `none`) | hashnode.com/code-of-conduct, /terms | Stays draft: "AI-generated content is permitted, but you must review and take responsibility for everything you publish". Not "primarily for self-promotion without contributing to the community" |
| `linkedin` (new) | checked | linkedin.com/legal/professional-community-policies | New entry, outline. No "inappropriate commercial or promotional" messages, no promotional invitations "to people you don't know", no arranged likes or re-shares |
| `lobsters` (new) | checked | lobste.rs/about | New entry, outline. Self-promo "less than a quarter of one's stories and comments in the last year"; new users (first 70 days) may not submit it; "tracking parameters are removed from links", so `link: plain` with `link_basis: verified` |
| `reddit`, `reddit_opensource`, `reddit_sideproject`, `reddit_selfhosted`, `reddit_claudeai` | unchecked, `not_read` | none: reddit.com, old.reddit.com and redditinc.com refused the reader, support.reddithelp.com answered 403 | `reddit` moves from `secondary` to stay unchecked; texts cut to what is true |
| `x` | unchecked, `not_read` | none: every rules address answered 403 or 402 | Same |
| `discord_global` | unchecked, `per_unit` | discord.com/guidelines was read ("Do not send unsolicited bulk messages"), but it says nothing on self-promotion and each server has its own rules | The platform page is linked as background; the state stays unchecked |
| `awesome_list` | unchecked, `per_unit` (was `secondary`) | sindresorhus/awesome pull_request_template.md was read, but it covers adding a list to the main index; each list has its own contributing file | Same |
| `dev_newsletter` | unchecked, `per_unit` | no single source can exist | Same |
| `other` | unchecked, `per_unit`, except the owner's own place (§1) | | |
| Indie Hackers, Mastodon | not added | indiehackers.com answered 403; joinmastodon.org says "Each server creates their own rules" | No entry |
| Korean places | GeekNews checked (read 2026-11-03); the rest unchecked, `no_source` | not researched again | `checked_on` and `unchecked_why` only |

A background source on an unchecked place (`discord_global`, `awesome_list`) is kept in a separate field,
`background_url`, so `source_url is None` still means "no rule text for this place was read".

After 0.7 the company writes for eight global places: Hacker News, Show HN, Product Hunt, dev.to (points only),
Hashnode, LinkedIn, Lobsters and a GitHub release. It writes nothing for Reddit, X, Discord, newsletters and Awesome
lists.

## 3. Registry (`config/channels.yaml`, `services/channels.py`)

- New fields on `Channel`: `checked_on: date | None`, `unchecked_why: Literal["not_read","per_unit","no_source"] |
  None` (set exactly when the place is unchecked).
- New functions: `state(channel) -> Literal["checked","unchecked"]`, `writable(channel) -> bool`.
- Places read at their source get `basis: verified`, `source_url`, `checked_on`, and `rules` rewritten from the text
  that was read, in both languages. A rules text never claims more than the source says.
- Places not read keep their key and get `unchecked_why`. Their `rules` text is cut down to what is true: we did not
  read them. No key is renamed or removed: keys live in cards, `heard_via`, `channel_drops` and tags.
- New entries only for places that were read at their source (§2).
- `link_basis` moves from `none` to `verified` only where the source that was read says something about links with
  tracking parameters. Otherwise it stays `none` (the company's judgment), as `docs/design/tracking.md:196` left it.
- A limit a platform states on its own page is said in that place's `rules` text (Product Hunt: name 40, tagline 60,
  description 500). No limit comes from memory, and 0.7 adds no separate limits field: nothing would read it.
- New optional field `background_url` (§2).

## 4. Writing

- `promotion.pick` skips an unchecked place with the reason `rules_unchecked`, before every other check. The
  prompt lists only the places it may write for. The week's record names the places skipped for this reason.
- `owner_tasks.build_post_card` raises `CardRefused(rule="rules_unchecked")` for an unchecked place. The rule joins
  `_NOT_THE_WRITING` (`promotion.py:347`), so no rewrite is asked: rewriting cannot fix it.
- An agent's decision request for an unchecked place is refused the same way, with a message in the owner's
  language that names the place and says its rules were not read. The preamble (`agents/executor.py:42`) lists
  checked places as usable and names the unchecked ones as places no card can be made for. The sentence at
  `company_preamble.md:51` goes.
- Cards already stored are not rewritten or removed. An open card for an unchecked place shows the mark and one
  line: "Written before the rules check. Read the place's rules yourself before you post."
- When a week has no place left to write for, the weekly record says so in plain words. It is not an error.

## 5. API and screen

- `GET /api/businesses/{id}/channels` returns the places for the business's market: `{places: [{key, name, region,
  state, checked_on, source_url, rules, unchecked_why, unchecked_text, does: "points" | "draft" | "nothing",
  fits: bool, repost_days, requires_unmet: [...], dropped: bool}], counts: {checked, unchecked}}`. Read only.
- Console: one list, "Places and rules", for the business. Each row: the place, its state, the date and source link
  when checked or the reason when not, what the company does there. Unchecked places sit in their own group under
  the checked ones with one sentence: "The company writes nothing for these. You can still post there yourself after
  reading their rules." Where it sits is decided by `/impeccable shape`; it is not a new top-level page.
- Post cards (`components/task.tsx:209`, `components/decision.tsx:226`) show the state next to the rules summary:
  "Rules checked · 2026-11-16" with the source link, or the "Rules unchecked" mark in the warn tone. The mark links
  to the list.
- Glossary: `rules_unchecked` = "Rules unchecked" / "규칙 미확인"; `rules_checked` = "Rules checked" / "규칙 확인됨".
- Widths 1440, 1200, 900, 390; light and dark; both languages; keyboard reachable. The state is text, never colour
  alone.

## 6. Done when (the ROADMAP row)

1. Every global place in the registry is either checked, with a source on the platform's own domain and a date, or
   unchecked with a reason. No entry is in between.
2. The owner sees the list for their business with the state of each place.
3. The weekly promotion makes no card for an unchecked place, and its record names what it skipped.
4. An agent cannot get a post card for an unchecked place; the refusal names the reason.
5. A post card shows whether its place's rules were checked.
6. Stored keys still resolve: no card, heard-via value, drop or tag is orphaned.
7. Checked on a real test business with a real weekly promotion run, both languages, both themes, four widths.

## 7. Phases

1. Reading at the source (research), registry fields and values, `state`, tests.
2. `pick`, `build_post_card`, the request path, the preamble, the weekly record, API, tests.
3. Console list, card mark, catalogs, glossary.
4. Review, fixes, a real run.

## 8. Decided here, for the owner to overturn

1. `secondary` is unchecked: a summary is not the rule text.
2. For an unchecked place the company makes no card at all, not a card with empty text.
3. The owner has no "I read the rules myself" switch that turns writing back on for a place. Without one, a place
   stays unwritten until the registry is updated.
4. The rule also covers Korean places, which leaves GeekNews as the only Korean place the company writes for.

## Not in 0.7

An owner switch per place (8.3), re-reading rules on a schedule, an expiry for a check date, rules of a single
subreddit, server or list read on demand, place rules on thread cards (`people.PLACES` shares no keys with the
registry), the `needs` tag from `customer-profile.md:105`, per-site profile wording in the kit.

## Deviations found while building (phases 1 and 2)

1. **`other`.** §1 says any use of `other` that `channels.named` cannot resolve to a checked place is unchecked. Built
   as first built: a card on `other` is refused when its title or address names a place that is unchecked, or when it
   carries an address that is on none of the business's own sites, with rule `rules_unchecked`. See "Review fixes" below
   for what replaced this. Why: a landing page and a flyer carry no
   outside address (or a host the owner has not saved yet), and the stricter rule would have refused every one of them.
   What stays open: an `other` card with no address that names an outside place the registry does not know is written.
   `build_post_card(draft, gate, lang, own_places=(), own_page=False)` takes two keyword arguments for this, and
   `screen(draft, gate, lang, own_places=())` one; `screen` sets `own_page` from the action kind.
2. **A third value on the read side.** A card that was kept as the owner's own place carries `own_place: true`, and read
   from storage it says `rules_state: "own"` (with `rules_checked_on` and `rules_unchecked_text` null), not `checked` or
   `unchecked`: it has no outside rules, and calling it unchecked would show a mark on the owner's own site.
3. **Where `pick` checks the rules.** The rules check comes right after the fit check says the place is for this
   business, and before everything else, including the "needs to know" reason. A place that does not fit is not
   listed as skipped for rules (a shop's week does not name Hacker News), and a place whose rules were not read never
   asks the owner a question about a line it could not use anyway.
4. **Read-side keys.** The "written before the rules check" line is `rules_written_before`. It is given only for an open
   card on an unchecked place, never on a card for `other`. `readable_card` and `with_named_channel` take `open_card`
   (default true); the to-do view passes `status == OPEN` and the decision report passes `PENDING`.
5. **Reasons for places the design leaves open.** `nextdoor` is `no_source`, `facebook_groups` is `per_unit`, and
   `reddit_local` is `not_read`. Korean places are all `no_source` as §2 says, although several of their texts say each
   room or group has its own rules.
6. **`source_url`.** It is set exactly when `basis` is not `none` (`secondary` stays a legal basis, though no entry uses it
   now). `dev.to` carries `https://dev.to/terms` as its source and names the AI page in its text; Hashnode carries the
   Code of Conduct and says "Code of Conduct" for its self-promotion sentence.
   The Product Hunt article address is the one the research gave; only its article number was seen at the page.
7. **Texts cut to what was read.** Claims the quotes do not carry were taken out: "landing pages" from Show HN, "needs
   install steps" from r/selfhosted, the weekly-thread claim from the city subreddit entry, "AI-edited" from Hacker News
   posts (it stays for comments), and Discord's "many keep a promotion channel".
8. **The API.** `requires_unmet` is a list of `{item, label}`; `fits` is true only for a confirmed match (a place waiting
   on a line nobody knows is false); `generic` is added so the console can leave `other` and `reddit` out of the list.
9. **The week's record.** `activity.data.rules_unchecked` holds the keys, and the summary gets one sentence naming the
   places in the owner's language. The "no place to write for" note is used only when every place the week looked at was
   left out for its rules.
10. **A generated file.** `web/src/i18n/areas/term.ts` is regenerated from the glossary; a test pins it. Nothing else in
    `web/` changed.

## Review fixes

An independent review of the built step found these, each confirmed by running the code. Each has a regression test in
`tests/test_channel_rules.py` unless said otherwise.

| Finding | What was done |
|---|---|
| H1. `other` with no address still got a post for Reddit, X or Discord. | `channels.place_in` matches a card's title against every registry name and alias, generic places included, case-blind, longest first. Everyday words were added as aliases (Reddit, subreddit, `r/<name>`, Twitter, tweet, Discord, Instagram, TLDR, Console.dev, Awesome list, and the Korean 레딧, 서브레딧, 트위터, 트윗, 디스코드, 인스타그램, 페이스북 그룹). A not-own `other` card whose title names an unchecked place is refused with `rules_unchecked`. Body and checklist are not read. The draft has no place-naming field, so the title is the whole check. |
| H2. `other` naming a checked place read back as that place. | A not-own `other` card whose title names a checked place is refused with rule `wrong_channel` and the message `post.refuse.names_place`, which names the key to file under. A card stored before still resolves on read. |
| M3. The name check refused the owner's own pages. | The check is skipped for an own card (a landing request, or an address under a saved place). The flyer title in `test_one_card.py` stays changed, because a flyer is `external.other` with no address and is not an own card; `test_a_flyer_whose_title_holds_a_place_word_is_refused...` pins the refusal and its message, and that a landing page with the same word is kept. |
| H3. An open dev.to card stored as a draft read "Rules checked" with no warning. | `_rules_state` adds `rules_written_before` with its own wording (`post.rules.written_before_self`) and `rules_written_before_why: "self_written"` for an open card stored as `draft` for a place that is now `self_written`. `RulesMark` now shows the line under a checked place too. Console rebuilt. |
| M1. "Own place" was the whole host. | `tracking.own_places` returns the saved addresses. `channels.under_places` accepts an address on the same host (`www.` and the bare domain are one, no other subdomain) whose path starts with the saved path on a segment boundary, after percent escapes and dot segments are resolved. A place saved at the root covers the host. `post.refuse.own_place` no longer says "repository". |
| M2. A landing request blessed any outside address. | A landing request is own only with no address, an address under a saved place, or a host under `channels.STATIC_HOSTS` (`pages.dev`, `vercel.app`, `netlify.app`, `github.io`, `workers.dev`). `pages.dev` is where Cloudflare Pages deploys land; the product has no landing-publish code of its own yet, so the list is the five suffixes named in the review. Anything else gets `post.refuse.landing_place`, which says to save the address as a place first. |
| M4. `external.other` with no card could name an unchecked place. | `screen` runs the title check on a post-kind request without a card. Only an unchecked place is refused there. |
| Low. An `other` card with no address read as unchecked. | It is stored with `own_place` and reads as `own`. A stored `other` card with no address and no named place reads as `own` too. |
| Low. Own cards carried an outside rules summary. | `rules_summary` is `""` for an own card, in the stored card and on read. The console guard stays. |
| Low. The empty week did not name the places left out. | The empty-week summary names them as the made-cards branch does. |
| Low. `counts` included the generic rows. | `counts` covers the listed (non-generic) rows only. |
| Low. Registry texts said more than the pages. | Product Hunt lost "personal accounts" and names the other two article addresses in the text. Lobsters says "rule of thumb". Hashnode says "Code of Conduct". `discord_ko` lost "many keep a separate channel for promotion". The devto Korean text says 사업, 프로그램, 강의. |
| Low. A test no longer went through `build_post_card`. | `test_tracking_links.py` now pins both paths: a card on `other` naming a place is refused at build, and a card stored before still takes that place's tag rule on read. |
| Console. The decision card linked to the selected business. | `requests.read_model` carries `business_id` (one project lookup) and `decision.tsx` uses it, falling back to the selection. |

## Known slips

1. A not-own `other` card whose title happens to hold a place word is refused (a notice titled "Daangn juice menu").
   The agent rewords it. This is the cost of reading titles at all.
2. Only the card's title is read. The request's own title is read only when the request has no card. A card with a
   plain title under a request titled "Reddit post" is kept.
3. An `other` card with no address that names an outside place the registry does not know ("a Slack community") is
   still written. The registry cannot refuse what it does not list.
4. A place saved at a site's root makes the whole host the owner's own, as designed. Saving `medium.com` makes any
   `medium.com` page own.
5. A name inside a longer word can match ("Lobsters" in a recipe title). Names of four letters or fewer need a word
   boundary, longer ones only a leading one.
6. The two other Product Hunt article addresses are written by their article numbers, which the research saw; their
   slugs were not read.
7. An old open card stored as a draft for a place that was never self-written reads as before, with no line.

## Checked on a real run (2026-11-16)

On the owner's local console and database, after the merge to `main`, with real model calls.

Seen:

- `GET /api/businesses/4/channels` on test business B-4: 8 checked places with the date, 11 unchecked with a reason,
  the generic rows not counted.
- B-4's weekly promotion (two calls, about $0.07): the prompt listed one place, dev.to, and no unchecked one. The card
  came back with no title and no body, only points, and its "why" said the article must not advertise the product.
  The week's record named the eight places left out for their rules. The card was held because B-4 has no started
  stage (known since 0.3), so it did not become a to-do.
- B-5's weekly promotion (a local shop): every place that fits it is unchecked, so the run made no model call and
  recorded "there are no posts this week" with the three places left out. The two sentences ran together without a
  full stop; fixed with a test (`382c2eb`).
- The "Places and rules" list on the Numbers tab and the mark on real to-do cards: a checked card (Hacker News) and
  an unchecked one (Reddit r/opensource) with its reason, the link to the list and the written-before line. English
  and Korean, light and dark, 1440, 1200, 900 and 390 with no sideways scroll.

Not seen on a real run:

- An agent's post card being refused (`rules_unchecked`, `wrong_channel`, `landing_place`): tests only.
- The mark on a decision card: seen only with a faked response, since no live request holds a post card.
- A card for the owner's own place (`own`), and an old dev.to draft card with its written-before line.
- A newly written card for a checked place as a to-do on screen (the one written was held by the stage gate).
- Tabbing through the whole list with a keyboard, and a screen reader.

A consequence the owner should know: a business that only fits local or Korean places (every one unchecked except
GeekNews) now gets no post written at all until those rules are read at their source.
