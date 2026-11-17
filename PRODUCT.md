# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Solo founders who have an idea and do not know how to launch it. They may or may not be developers. Launching
feels hard and tedious: what to decide, what to build first, where to post, who to talk to. The current owner
is one of them; after the open-source release the same profile applies worldwide. They run the software on
their own machine with their own Claude subscription. They are not watching the agents work. They drop in a
few times a day, usually on a desktop browser, to answer the company and to do the few things only a founder can do.

Their recurring jobs:

1. **Align.** Tell the company what the business is and what success means, in a short kickoff conversation.
2. **Decide.** Read a decision card, then approve, reject, or answer. The Project Manager decides a short list of safe
   calls alone and only reports them.
3. **Do the founder's part.** Post by hand where the company says to, and talk to people personally.
4. **Judge.** Read reports and the evidence ledger, and decide whether a conclusion is backed by evidence.

## Product Purpose

Just Ship It runs the early stage of a business with the owner, from an idea to the first 100 users. The owner brings
an idea and whatever planning exists. A Project Manager agent and a team put together for each job decide, plan a
roadmap, build, prepare promotion and sales, analyze results, and keep the customer and mail records. A pstack-based
engineering team implements approved specs. The console is where the owner and the company work together.

The 100 users are a target, not a promise. What the product removes is the "I don't know how and it is tedious"
that stops people from trying. Success means the owner reaches the first users sooner and spends the owner's
time on people, not on reviewing machine output.

Some parts are still planned and are marked so in README.md: approving from Slack, and the Gmail connection
(reading mail, and sending after approval).

## Positioning

Agents are governed, and the founder stays the one who meets users.

- The owner and the Project Manager agree on a plan (the work brief) before any work starts. Each line shows whether
  the owner said it, confirmed it, or the Project Manager only guessed it.
- Decisions are split. The Project Manager decides a short list of safe calls inside the brief. Everything
  irreversible (publishing, spending, sending outside, changing direction, merging, deploying) reaches the owner as a
  decision card with the recommendation first.
- The company never posts to communities. It prepares where, what, and when to post, and the owner posts by hand
  and records the result. Reports only claim what the owner's to-do says is done.
- Evidence confidence is always shown. Early on most claims cannot be verified, so verification is not a gate.
  The company proceeds on reasonable evidence and shows verified, sourced, or assumed.
- A reviewer with a different role from the author checks the work. All agents run on Claude.
- It runs on the owner's own Claude subscription, with no API keys, on their own PC. Usage follows caps the owner
  sets, and work waits and resumes when a cap or the subscription limit is hit.

## Operating Context

- Runs locally: `docker compose up -d`, `uv run ship worker`, `uv run ship api`. Console at
  http://127.0.0.1:8000 (React 19 + Vite, built into `web/dist`, served by FastAPI).
- Several businesses can run at once, each with projects, tasks, deliverables, an approval queue, a team, and dev
  jobs. The owner starts a business from the console with a kickoff conversation of at most 20 turns.
- Runs take minutes to hours. The owner leaves and comes back, and live updates arrive over SSE.
- Pages: dashboard, kickoff conversation, per-project view (overview, tasks, approvals, team, dev, evidence,
  meetings), approval center, teams for each job (who worked each task and build, and why), engineering, skills, operations, usage, settings.

## Capabilities and Constraints

- Claude subscription CLI only (`claude -p`). Never API keys. Codex is retired.
- External connectors default to outbox or dry-run mode and go live only when the owner switches them on in
  Settings. Email is the one connector that can send for real. A post to a community is never sent by the company;
  it becomes an owner to-do.
- Rules and credentials (connector modes, secrets, usage caps, approval overrides, notifications) are entered in
  Settings and stored in the database and `var/secrets`, never in code or tracked files.
- Terminology: one word per idea, in plain words a first-time founder knows. The table below is generated from
  `backend/justshipit/i18n/glossary.py`, which is the single source: the console's catalogs and every agent's
  output-language rule read it, and `tests/test_i18n.py` fails when a catalog uses a retired word. Code names
  (tables, API fields) keep their old names.

| English | Korean | Id | Code name | What it means | Retired words |
|---|---|---|---|---|---|
| Business | 사업 | B-n | `business` | The idea you are turning into a company. Each has its own plan, people and work. |  |
| Kickoff chat | 시작 대화 |  | `kickoff` | The short first conversation where the Project Manager asks about your idea and writes the plan with you. |  |
| Plan | 계획 |  | `brief` | What you and the Project Manager agreed: what the business is, who it is for, what success looks like, and what is off limits. The company works inside it. | 지시서, work brief |
| Cycle | 사이클 |  | `cycle` | One round of work toward the plan, made of stages the Project Manager proposes. You start each stage, and the company stops after it and waits for you. |  |
| Stage | 단계 |  | `cycle_stage` | One part of a cycle, such as Validate or Promote. Its card says what the company and you will do and what it may cost; nothing in it runs until you start it. |  |
| Experiment | 실험 |  | `bet` | One time-boxed try at reaching users: a guess to test, a few weeks, then a look at what happened. | 도전 |
| Project | 프로젝트 | P-n | `project` | A piece of work the Project Manager plans to move the plan forward, with its own tasks and budget. |  |
| Task | 업무 | T-n | `task` | One piece of work an agent does inside a project. |  |
| Decision | 결정 | REQ-n | `request` | Something the company needs you to decide before it acts: approve, decline, or answer. Anything that cannot be undone comes to you as a decision. | 결정함 |
| To-do | 내 할 일 | OT-n | `owner_task` | Something only you can do, such as posting, paying, signing up or talking to someone. The company prepares it; you do it and mark it done. | 오너 할 일 |
| Idea | 아이디어 |  | `idea` | A note you drop in at any time. The Project Manager reads it, then uses it, asks you about it, or suggests setting it aside. |  |
| Evidence | 근거 | EV-n | `evidence` | What a claim stands on: a checked fact, a source, or a guess, always labelled as which. |  |
| Decision log | 결정 기록 | DEC-n | `decision` | What was decided, by whom and why, kept so the company remembers. |  |
| Work path | 일의 경로 |  | `work_path` | How the work behind a decision connects: the goal, the experiment, the project, the task, and what waits on your answer. |  |
| Meeting | 회의 | M-n | `meeting` | When agents disagree, they meet and the Project Manager decides. |  |
| Product plan | 기획안 |  | `plan` | What a build is for: the problem, who it serves, what the first version does and leaves out, and how to tell it worked. You approve it before the spec is written. |  |
| Spec | 명세 |  | `spec` | The written description of what to build. You approve it before building starts. |  |
| Build | 개발 작업 | J-n | `dev_job` | The engineering team building an approved spec. |  |
| Agent | 에이전트 |  | `agent` | A role on the roster that is called in per job, such as research or marketing. |  |
| Project Manager | 프로젝트 매니저 |  | `project_manager` | The AI role that runs the work: it holds the kickoff chat, plans each cycle and project, says which roles a job needs, reviews the results and decides when agents disagree. | CEO, AI CEO, CEO, AI CEO |
| Staffing Agency Manager | 인력 파견 매니저 |  | `staffing` | Not an AI but a fixed rule. It places the team for each task and build from the roster, records why, and lets the team go when the job ends. It makes no model calls. |  |
| Team for this job | 꾸린 팀 |  | `crew` | The roles placed for one task or build, with the reason for each. It is put together when the job starts and let go when it ends. | 조직도, org chart |
| Roster | 인력풀 |  | `agents` | Every role that can be called in for a job, with its model. A role on it works only when a job's team needs it. |  |
| People list | 명단 |  | `people` | The people you may talk to, and how far each has come. |  |
| Got value | 도움 됨 |  | `first_value` | A person who used it and got what they came for at least once. | 첫 가치, first value |
| Came back | 재사용 |  | `repeat` | A person who used it again on their own. |  |
| Steps to first users | 사용자가 되기까지 |  | `funnel` | Found, contacted, talked, got value, came back: how far your people have come. | 사다리, ladder |
| Routine | 정기 업무 |  | `routine` | Work the company repeats on a schedule, such as a weekly review. |  |
| Skill | 스킬 |  | `skill` | A written way of doing a recurring job that agents reuse. |  |
| Outbox | 보관함 |  | `outbox` | Where what the company would send out waits until you turn sending on. Nothing in it has left; you open it and send it yourself. | outbox, 아웃박스 |
| Sending mode | 보내기 방식 |  | `connector mode` | How each connector sends, set in Settings: Off (nothing is made or sent), Outbox (made and kept for you to send yourself), Send what I approve (only what you approve, one by one, goes out) or Send right away (goes out without asking you). |  |
| Holding up work | 일을 막음 |  | `blocking` | A decision the work behind it can't go on without. That work waits until you decide. |  |
| On hold | 보류 |  | `DEFERRED` | A decision set aside for now. Nothing it asks for happens, and an agent asks again if it is still needed. | 뺌 |
| Expires | 만료 |  | `expires_at` | When a decision can no longer be approved. A to-do has a due date instead. |  |
| Model call | 모델 호출 |  | `llm_call` | One request an agent sends to Claude. Each is recorded with what it read and answered, and counts toward usage. |  |
| Monthly budget | 월 한도 |  | `monthly_budget_usd` | The most the whole company may spend in a month, at list price. At it, new model calls are refused and projects stop until next month or until you raise it. |  |
| Usage cap | 사용량 상한 |  | `usage` | How much of your Claude subscription the company may use per day and per week. At the cap, work waits and picks up after the reset; nothing fails. |  |
| Pause | 멈춤 |  | `stop` | Stopping something for now in a way you can undo. It picks up where it left off when you resume it. |  |
| End | 끝내기 |  | `end` | Finishing something, such as a chat or a cycle, instead of pausing it. It doesn't carry on by itself. |  |
| Team chat | 팀 대화 |  | `chat` | A conversation with the team about what is on your screen. Anything that cannot be undone still comes to you as a decision. |  |
| Background process | 백그라운드 프로세스 |  | `worker` | The program that does the company's work behind the console: jobs, stages and routines. Start it with `ship supervise`; while it is off, work waits. |  |
| User journey | 사용 흐름 |  | `journey` | How one kind of person uses the product from start to finish: who they are, why they came, each step, and what they see when it worked. |  |
| Customer description | 고객 설명 |  | `customer_profile` | Who buys, how they are reached, and what you already have. The company picks places and people from it. |  |
| First 10 kit | 첫 10명 키트 |  | `first_ten_kit` | Who to reach today, a message for each, and wording for your profile and invites, all written for you to copy. The company sends nothing; you send it yourself. |  |
| Your link | 내 링크 |  | `tracked_link` | The address on a post card that leads to your own site, store page or repository, with a tag when the place would hide where visitors came from. |  |
| Rules checked | 규칙 확인됨 |  | `rules_checked` | The company read this place's rules on the place's own page and shows the date. It writes for the place, and you still post by hand. |  |
| Rules unchecked | 규칙 미확인 |  | `rules_unchecked` | The company did not read this place's rules. It writes nothing for the place. You can still post there yourself after reading the rules. |  |

- Evidence statuses: verified_fact, sourced_claim, assumption, hypothesis. Verification statuses include
  supported, weak, contradicted, unverifiable, reproduced, computed, transcribed, and derived.
- Language: English by default, Korean as the other choice (Settings > Language, one setting for the company).
  It switches the console and everything the company writes for the owner from then on. Text already written
  stays in its language. Layout must not assume either language's string lengths. See docs/I18N.md.
- [TEST] projects exist for harness runs and are hidden by default.

## Evidence on Hand

- An earlier real run (P-5 "Just Ship It 사업화", since wiped from the local database) had 67 requests, 57% of
  them noise. That is why the Project Manager now decides safe calls.
- An expert panel and a target-user review of the first-100 process, in `docs/ux/09-expert-panel.md`.
- No testimonials, customers, usage numbers, or pricing exist yet. Do not fabricate any.

## Product Principles

1. **Decisions first.** Anything waiting on the owner, especially blocking requests, is the most prominent
   thing on any screen where it applies.
2. **Show the confidence of every claim.** Verified, sourced, and assumed must never look alike.
3. **Calm under long runs.** The console is a place to check in, not to watch. Make the state legible at a
   glance and don't make noise for routine progress.
4. **Owner words are binding.** Notes and answers the owner leaves must visibly change the plan, and the UI shows
   where they did.
5. **Nothing irreversible without clear confirmation.** Archive, merge, and external actions use explicit,
   in-page confirmation.
6. **The founder meets users personally; the company prepares and records.** The company decides where to go,
   drafts what to say, and logs what happened. The owner posts, talks, and demos. The product never replaces
   that contact.
7. **Say what the company knows and what it guesses.** A brief line, a claim, or a number shows who stands
   behind it. Do not fake certainty to look finished.

## Accessibility & Inclusion

No product-specific standard has been set. Default to WCAG 2.2 AA, keyboard-operable approvals, and color that
is never the only status signal.
