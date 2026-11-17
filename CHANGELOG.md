# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow the steps in [docs/ROADMAP.md](docs/ROADMAP.md);
before 1.0 anything may change.

## [Unreleased]

### Security
- Pre-release security review (step 0.8). Five read-only audits covered the console's login and HTTP surface, the
  sandbox and every subprocess, the tool gateway and credentials, untrusted content reaching prompts and files, and the
  web console with its dependencies. Fixed:
  - The console opened a network path named in a request, without a login, on Windows. It now refuses any path outside
    its build from the path's text alone.
  - Every response carries a content security policy, no framing, `nosniff` and `no-referrer`; API answers are not
    stored. The API docs pages (`/api/docs`, `/api/redoc`) are off, because they loaded a script from a CDN.
    `/api/openapi.json` stays.
  - The product preview opens at `http://localhost:4173` and the console answers only as `127.0.0.1`, so the console's
    cookie no longer reaches agent-written code. A console opened as `localhost` is redirected.
  - The host reads what a container wrote without following links (the analysis sandbox's `out/`, branch exports, the
    look check). The engineer container's access token is removed from stored output.
  - A grant is spent only by the request it was approved for. STARTTLS verifies the server's certificate. A recipient
    entry is one address. The network guard refuses two more non-public IPv6 ranges. A source check stops at 2 MB and
    30 seconds.
  - Company memory keeps only evidence a reviewer supported and decisions the owner approved, and recalls them inside
    a data fence. A role that can fetch web pages no longer gets the team chat.
  - `POST /api/requests/{id}/respond` takes only the owner's and the delegated responder's names.
  - `.env` is restricted to the owner at every start, and the database password stays out of the settings repr.
- Known limits, not fixed in this step: a role that fetches web pages still holds the plan, the uploaded files and the
  names of people to talk to (server-side fetching is the planned fix); the session cookie does not expire server-side
  (`ship api-token rotate` logs every browser out); a merge grant is bound to its commit, not to its request.

### Fixed
- A clean computer could not install: with no Python present, uv chose 3.15, which `onnxruntime` has no wheel for.
  `requires-python` now stops below 3.15, so uv picks 3.14, the version the project is developed and run on.

## [0.7] - 2026-11-16

### Added
- Rules checked or unchecked for every place the company writes for, server side (step 0.7), with a "Places and rules" list on the Numbers tab and a mark on every post card: a place
  is checked only when its rules were read at its own page, with the date; every other place is unchecked with a reason.
  The company writes nothing for an unchecked place: the weekly promotion skips it and names it in the week's record, and
  an agent's request for it is refused naming the place. dev.to, Hashnode and Product Hunt are now checked (dev.to becomes
  outline, because its AI-article rules bar promoting your own business); LinkedIn and Lobsters are new. A stored card
  says whether its place is checked now. `GET /api/businesses/{id}/channels` lists the places with their state. No
  migration.
- Merge approval card: how to try the build, scenarios with the owner's marks, doubts, built-by and checked-by (step 0.1).
- Roadmap by step, with exit criteria per step.
- Tracking, server side (step 0.5, the console follows): each new post card carries **Your link**, tagged only where a tag
  can be read back; the owner records how a person heard about the product and taps +1 for people not on the list; the
  counts per channel carry their basis. Migration `c4a8e1d7b295` adds `businesses.doors`, `people.heard_via` and
  `people.heard_words`.

## [0.6] - 2026-11-16

### Added
- First 10 kit, at the top of the People tab: up to 3 people to reach today with the reason for each, a message draft
  for each person who gets one, profile wording in three sizes and invite wording in two, each with a character count
  and Copy. One click writes them; nothing is sent, and no decision card or connector is involved. A person found in a
  public post and not yet contacted gets no draft, only "answer their post first". A person already contacted gets a
  follow-up that does the next step. A text with contact details, a link other than your own place, a blank, an ask to
  pass it on or an invented quote is refused and shown as not written with its reason; a failed rewrite keeps the
  earlier text. A text written before the person or the customer description changed says so. The Today screen shows
  one line that opens the kit. Migration `d5f1a8c3e726` adds `kit_texts`.

## [0.5] - 2026-11-16

### Added
- Tracking: every post card shows **Your link** to your own site, store page, map listing or repository, with a tag
  where the place would hide where visitors came from, the plain address on Hacker News and Reddit, and a line to say
  where no link fits. You record how each person heard about you in the people list and on a person to-do, and tap +1
  for someone who is not on the list; contact details are refused. The Numbers tab has **Where people came from**:
  posts, visits, sign-ups, people and who got value per place, each number marked read, told, typed or at least, and
  the places of your first 10 and 100 users (step 0.5). Needs migration `c4a8e1d7b295`: `businesses.doors`,
  `people.heard_via`, `people.heard_words`.

## [0.4b] - 2026-11-15

### Added
- Decisions and To do put each decision in the order of the work it frees, and each card says what it holds. The
  Decisions page has a **By project** view with progress per project. A card's **Show on the path** opens the project's
  new Path tab on that decision, with keyboard use and a list for small windows and very large projects (step 0.4b).
  New console dependency: `@dagrejs/dagre`.

## [0.4a] - 2026-11-15

### Added
- Decision cards say where they came from, what they hold up, what answering does and what happens if nobody answers.
  A card that holds some tasks lets the rest of the project run, and expiry rules now apply (step 0.4a). Needs migration
  `a9d4f7b2c613`: cards store `hold_scope`, and `blocking` is now worked out from it.

## [0.3] - 2026-11-15

### Added
- Customer description: the business page shows who buys, how they are reached and what you already have, one line
  each, marked said, confirmed, guess or unknown. Right, Not right, Change and Undo write to the activity log with no
  decision card. The kickoff asks once at its end. Promotion, the people finder, the numbers and the contact card read
  only these values, a post picked from a guess says so, and `ship profile backfill` reads an older business's plan
  as guesses for the owner to check.

### Fixed
- Weekly promotion: a post card the checks refuse, or one the marketing agent did not write, is asked for once more
  with the reason. A week that ends with no post is now in the activity log with why: no post passed, or the posts were
  ready and no started stage lists post to-dos (step 0.3).
- Customer description: Undo puts a value back as whose it was. It used to store the company's reading as the owner's
  own words (step 0.3).

## [0.2] - 2026-11-14

### Added
- Team chat, on every screen: talk to the agent who owns what is on screen, hand out small work, and have a fix round
  run on the build's branch while the merge card waits (step 0.2). Needs migration `7c1e5a9d3f20`.

## [0.1.0]

First version that runs on its author's own PC: the company loop, approvals, engineering, and the console (Phase A to C
in the roadmap).
