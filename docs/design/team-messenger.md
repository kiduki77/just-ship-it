# Team chat: talk to the team from any screen

Status: phases 1, 2 and 4 built, and the dock of phase 3 on branch `feat/team-messenger`. Grounded in the code at
`main` c6d065f. Section 10 lists where the build departs from this design and why. The owner answered section 9: work
starts at once, the waiting merge card is set aside, and a chat fix stays local until the merge is approved.

## Why

On the merge card (REQ-4) the owner follows the "Try it" steps, `npm install` fails, and the only way to say "fix
this" is Decline, which is heavy. The owner also wants the product to use the Claude subscription instead of an API
key and has nowhere to ask. The owner wants a Slack-like conversation on every screen, addressed to whoever owns what
is on screen, with the screen as its subject, and small pieces of work handed out from it. Decisions the owner already
made are taken as given. Section 9 lists the three calls only the owner can make.

## 1. What the owner gets

A **Team** button on every screen opens the chat dock. The composer already carries the screen's subject (`Merge
approval REQ-4 · Product uxscope · Branch ai/projec-0001-a1`) and is addressed to its owner (`To: Engineer ·
Engineering`). One conversation per subject, plus General. A request for work becomes a task card that runs on the
subject's branch through the existing queue. Chat never merges, pushes or approves.

## 2. Data model

### Reuse before adding

- `agent_messages` (`db/models.py` `AgentMessage`) already holds owner-to-agent messages. `services/control.py`
  `comment_task` writes `from_agent="owner"`, `to_agent=<task owner>`, `ref_type="task"`, and
  `orchestration/context.py` feeds them to the task's next run. A task comment is a chat message about a task, so chat
  extends this table instead of adding a second message store.
- The **fix round** (`engineering/revise.py`) is already "the engineering team changes a delivered build on its own
  branch as the owner said, then delivers it again for the acceptance check and a new merge decision". Its states
  (`waiting_budget`, `queued`, `running`, `delivered`, `stopped`) live in `DevJob.result["fixes"]`. A chat task on a
  build is a fix round, and the card reads its state from there.
- `services/existing_product.py` `SECRET_PATTERNS` already lists GitHub, Slack, Anthropic, OpenAI, Stripe, AWS,
  Google and private-key shapes. The chat scrubber uses the same table.

### New table `chat_threads`

One row per subject per business, created by the first message, so opening the dock creates nothing.

| Column | Type | Meaning |
|---|---|---|
| `id` | int PK | |
| `business_id` | FK `businesses`, nullable | Null only for the company-wide General. |
| `tag_kind` | varchar(16), check | `general`, `business`, `project`, `task`, `request`, `owner_task`, `plan`, `spec`, `dev_job`, `page` |
| `tag_ref` | varchar(40) | Canonical id (`"12"` for J-12, `"engineering"` for a page), empty for `general` |
| `default_agent` | varchar(40) | Role key (`config/agents.yaml`) that owned the subject when the thread was made |
| `owner_read_seq`, `last_seq` | int | One owner, one read pointer. Unread is an agent message past `owner_read_seq`. |
| `created_at`, `last_message_at`, `archived_at` | timestamptz | Archive, never delete. A new message un-archives. |

Unique `(business_id, tag_kind, tag_ref) NULLS NOT DISTINCT` (Postgres 17 in `docker-compose.yml`), so two first
messages racing on one subject land in one thread (`ON CONFLICT DO NOTHING`, then select). The subject's label is not
stored. The server builds it from the live entity on each read, so a new REQ number shows and the label follows the
owner's language.

### Columns added to `agent_messages`

| Column | Type | Meaning |
|---|---|---|
| `thread_id` | FK `chat_threads`, nullable | Null only on old rows not backfilled |
| `seq` | int | Order in the thread, assigned under the thread row lock |
| `business_id` | int, nullable | Scoping and the `[TEST]` filter, as on `llm_calls` |
| `data` | JSONB `{}` | Structured content per kind. System lines store `{"key", "vars"}`, never a sentence (I18N.md) |
| `scrubbed` | JSONB `[]` | What the scrubber removed, `[{"kind": "github_token", "n": 1}]`, never the value |
| `call_id` | varchar(40), nullable | The `llm_calls.call_id` that wrote an agent reply |
| `in_reply_to` | FK `agent_messages`, nullable | The owner message a reply or task answers |

`from_agent` is the sender (`owner` or a role key), `to_agent` the recipient, `body` the scrubbed text. `kind`, under a
new check constraint:

| `kind` | Written by | `data` |
|---|---|---|
| `comment` | existing task comments | |
| `text` | owner or agent | |
| `task` | server, when a reply asks for work | `{"work": {"kind": "dev_fix", "dev_job_id": 12, "n": 2}, "title"}` |
| `handoff`, `escalation` | server | `{"from": "engineer", "to": "ceo", "why"}` |
| `decision` | server | `{"request_id": 31}`, a card this conversation filed |
| `status` | server | `{"key": "chat.status.task_done", "vars"}` |

The migration adds the columns nullable, creates `chat_threads`, and backfills a `task` thread for each task with
comments. Stop the worker first and check autogenerate leaves the LangGraph checkpoint tables alone (CLAUDE.md).

### The subject registry

`services/chat_tags.py` holds `TAGS: dict[TagKind, TagRule]`, four pure functions per kind:

- `canonical(s, ref)` maps a subject to what owns the work. A `request` with `action_kind == "dev.merge"`, or one
  carrying `revise.ASK_KEY`, becomes `dev_job:<id>`. Every fix round ends in a new merge card with a new REQ number
  (`worker._ask_merge`), so a thread keyed by the request would split. Keyed by the build it stays whole.
- `owner_agent(s, ref)`: `dev_job` and `page:engineering` → `engineer`; `spec`, `plan` → `product`; `task` →
  `Task.owner`; other `request` → `from_agent`; `project`, `business`, `general`, other pages → `ceo`.
- `facts(s, ref)`: the chips as data (`[{"k": "merge", "ref": "REQ-4"}, {"k": "product", "v": "uxscope"}, {"k":
  "branch", "v": "ai/projec-0001-a1"}]`), worded by the console's catalog.
- `work(s, ref)`: the work kinds the subject takes. v1 has one, `dev_fix`, on a delivered `dev_job`.

A new subject kind is one row here and one entry in the console's route table. Nothing else branches on tag kind.

### Who did what: recorded today, and what is new

Recorded today:

- Every model call: `llm_calls.agent`, `model` (what actually answered), `dev_job_id`, `task_id`, `project_id`.
  Grouping a build's calls by `agent` gives "built by Engineer (opus), reviewed by Code reviewer (sonnet), checked by
  Product (sonnet)" for every build, past ones included.
- The review step's label, `review by eng_reviewer (claude/sonnet) of engineer (claude/opus)` (`worker.py`,
  `agents/independence.assess`).
- `AgentRequest.from_agent`, `Task.owner`, and per job kind `gate.POLICY[kind].agent`.
- Names: `i18n/glossary.py` `ROLES` and `config/agents.yaml` `title`, `department`, served by `GET /api/agents`.

Newly recorded:

1. Sender and recipient of each chat message (`from_agent`, `to_agent` on the new rows).
2. `"by": "<role>"` on each build step, written by `worker._move` (JSONB, no migration). Today the actor is only in
   free text. Older steps fall back to a status-to-role table (`IMPLEMENTING`, `QA` → engineer, `REVIEWING` →
   eng_reviewer, acceptance → product).
3. `asked_in` (the chat message id) on a fix round. Today `fix["request_id"]` is its only origin.
4. Escalations and handoffs, as messages.

Agents have no personal names. "Name and role" shows the glossary name and the department: `Engineer · Engineering`,
`엔지니어 · 개발`. Personal names would be a glossary change outside this design.

## 3. API

New `api/chat.py`. Each route reads `app_settings.language()` once and passes `lang` down.

| Route | Does |
|---|---|
| `GET /api/chat/threads?business_id=` | Threads with unread flag, clipped last message, live facts, default agent, open task count |
| `POST /api/chat/subject` `{kind, ref}` | Resolves a screen's subject without creating anything: canonical subject, facts, default agent, `thread_id` or null |
| `GET /api/chat/threads/{id}/messages?before=&limit=50` | A page of messages with sender name and role, and for `task` the derived state (4.4) |
| `POST /api/chat/messages` `{subject \| null, to_agent \| null, body, client_id}` | Scrub, find or create the thread, insert, enqueue the reply. `client_id` makes a double press or retry send once. Returns what was scrubbed. |
| `POST /api/chat/threads/{id}/read` `{seq}`, `/archive`, `/unarchive` | Read moves `owner_read_seq` forward only. Archive, never delete. |
| `POST /api/chat/tasks/{message_id}/cancel` | Cancels the task's job before it starts (`queue.cancel_unstarted`). A running round stops the way every build does, by pausing the agent or the company. |

No new live channel: `services/events.py` sends every flushed row over SSE, so the dock uses
`useLive(path, ["agent_messages", "chat_threads", "jobs", "dev_jobs"])`. `body` is capped at 20,000 characters, the
check-result memo limit, so a pasted install log fits.

## 4. How a message is answered and routed

### 4.1 The reply job

The send route enqueues `chat_reply` `{thread_id, agent}` in the same transaction as the insert (ARCHITECTURE.md 1.3).

- `gate.POLICY` row: `"chat_reply": Rule(Gate.ASKED, "an agent answers a message the owner wrote in team chat")`. The
  owner typed it, like `kickoff_turn` and `triage_idea`.
- `lock_key = f"chat:{thread_id}"`: replies in a thread run one at a time, in order.
- `idempotency_key = f"chat_reply:{thread_id}:{agent}:{last_owner_seq}"`: three quick messages make one waiting job,
  which answers every owner message since the agent's last reply.
- Usage limits (`MustWait`, `wait_reason`), a paused agent (`waits_on`) and the monthly budget apply as to any job,
  and the thread says why it waits. Cost goes to the subject's project or business through the executor's normal
  reservation.

### 4.2 Who answers, and from where

One `run_agent(agent, "chat_reply", prompt, ChatReplyOut, ...)` call, recorded under the real role, so ledger and
message both say Engineer.

The engineer's roster entry is `runtime: engineering`: a fresh writable container with `bypassPermissions`. Wrong for
an answer. `run_agent` already overrides `tools` per call (the CPO reads screens with `tools=["read_workspace"]`), so
add the same override for `runtime`: `run_agent(..., runtime="isolated", tools=["read_workspace"], cwd=export)`. The
engineer answers from an export of the delivered branch (`worker._export_branch`, then `worker.drop_links`), the same
read-only view the CPO and code reviewer get, with `Read`, `Glob`, `Grep` only (`runtime/claude_cli.py`). Other agents
answer isolated with their configured tools.

The prompt fences (`services/fence.fenced`) the subject's read model (for a build: `engineering/merge_review.card`,
try-it steps, `engineering/status.steps`, the open fix round), the thread's last 20 messages clipped, and the owner's
new messages last. The output contract is the only way a reply causes anything:

```python
class ChatReplyOut(BaseModel):
    reply: str
    intent: Literal["answer", "work", "escalate", "handoff", "decision"]
    work: WorkAsk | None                # {title, instructions}
    to: str | None                      # role key, for handoff or escalate
    why: str | None                     # one line on the handoff or escalation
    decision: AgentRequestDraft | None
```

The server checks the intent against the table in 6.2. A disallowed intent becomes a plain answer, and the job's log
says why.

### 4.3 Work becomes a fix round on the subject's branch

For `intent == "work"` on a `dev_job` subject:

1. The server writes a `task` message answering the owner's message.
2. A pending merge card for the build is set aside with `requests.withdraw_pending(s, req_id, by="owner", note=...)`,
   and a `status` line says so. `revise._begin` already refuses a round while a merge waits (`_merge_waiting`),
   because approving that card would merge the commit the round replaces.
3. `revise._begin(s, job, words, None, lang, by_owner=True)` starts the round, with `asked_in` on the fix record.
   `words` is the owner's messages verbatim (scrubbed), then the engineer's `work.title` and `work.instructions`.
   `by_owner=True` runs it whatever stage is open, like a round from a declined merge.
4. The existing machine runs: `dev_fix` implements, tests, reviews, delivers; acceptance runs; `worker._ask_merge`
   raises a new merge card, or `revise.failed_check` handles a failed check. A `revise.shortfall` leaves the round
   `waiting_budget`, and the card shows the existing budget path.
5. Where the round already changes state (`revise._begin`, `revise.run`, `revise.delivered`, `revise.stopped`,
   `worker._ask_merge`), one `status` line goes to the thread found by `asked_in`. The merge card refreshes on its
   own, since it reads the same `dev_jobs` row over SSE.

A build that is not delivered takes no work. The engineer answers, and a catalog line says it can take changes once
delivered. On a merged build, changes go through a new spec version, so the agent files a decision instead.

**Push.** Delivery pushes and opens a PR when the product has a remote and the spec asks for PRs (`worker._deliver`,
`git push -u origin`). The owner said chat never pushes, so a round with `asked_in` delivers locally only. The next
merge card says the branch is not pushed, and approving it pushes the delivered commit, then merges under the
existing `--match-head-commit` check (`worker.merge`). A product with no remote merges `--ff-only` and never pushes.
Open question 3.

### 4.4 Task card state is derived, not stored

`chat_tags.TASK_STATE["dev_fix"](job, n)` reads the fix record. No second copy exists, so the card and the
Engineering screen cannot disagree.

| Fix round | Card | Who |
|---|---|---|
| none yet, or `queued` | received | Engineer |
| `waiting_budget` | waiting for budget | Engineer |
| `running` | in progress, with `k of M` from `worker.note_progress` | Engineer; Code reviewer while `REVIEWING` |
| `delivered`, acceptance pending | checking | Product |
| `delivered`, new merge card open | done, linking the new card | built by Engineer, checked by Code reviewer and Product |
| `delivered`, check failed | needs fixing (the failed-check card) | Product |
| `stopped` | didn't finish, branch put back | Engineer |

### 4.5 Escalation, handoff, and the CEO's view

- An @CEO message has `to_agent = "ceo"`. `intent == "escalate"` writes `Engineer → CEO: <why>` and enqueues
  `chat_reply` for `ceo` on the same thread. `intent == "handoff"` (CEO only) writes `CEO → Marketing` and enqueues
  that agent.
- One owner message causes at most 3 agent replies (`chat.max_hops`, `config/company.yaml`). Past it, the last agent
  answers or files a decision, so two agents cannot pass a message back and forth on the owner's subscription.
- No summary call. `orchestration/context.py`, which already feeds owner task comments to runs, gains a fenced "team
  chat" block of the last 7 days (subject, task cards, escalations, last owner message clipped to 300 characters).
  Every CEO call (`run_project`, `portfolio_review`, `kpi_review`, `cycle_*`) reads it.

### 4.6 Cost and latency (estimates, not measured)

A reply is one isolated `claude -p` call: 1 to 2k tokens of isolation overhead (measured, ARCHITECTURE.md 1.1) plus a
guessed 4 to 12k of context. The engineer is configured as opus; add `chat.reply_model`, default sonnet, since an
answer is reading, not building, and check that guess on real threads in phase 2. Latency is a guess of 15 to 60
seconds plus a few seconds of branch export, behind running jobs for a slot (`providers.*.max_concurrency`). Work
costs what a fix round costs today, up to `engineering.fix_budget_usd` (6) from the project budget.

## 5. UI

The console is an Operate surface. Chat must not compete with decision cards for attention.

### 5.1 Placement

The hard case: REQ-4 is open in the side panel (`components/panel.tsx` `SidePanel`, at least 400px or 42%), and the
owner reads the try-it steps while typing. A third column beside the panel leaves the page about 300px at 1440, and a
split inside the panel halves the merge card when the owner needs it whole. So:

- **1200 and wider:** a floating dock, 400px by `min(640px, 100vh - 96px)`, 16px above the bottom, its right edge
  16px left of the side panel, or at the gutter with no panel. Elevated surface with the Pop shadow, the one layer
  that may float (DESIGN.md).
- **900 to 1199 with a panel open:** a bottom sheet across the panel's width, at most 55% of its height.
- **Below 900:** a full-height sheet over a scrim, like the nav drawer. 390px is out of scope (DESIGN.md).

This is a stated assumption, not an owner-confirmed brief (`/impeccable shape` had no human in this run). Confirm it
with a throwaway prototype at 1440, 1200 and 900 with REQ-4 open before phase 3.

Non-modal at 900 and wider (`role="dialog" aria-modal="false"`, as `SidePanel`), modal with a focus trap below. Focus
goes to the composer on open and back to the Team button on close; `Esc` closes unless the `@` list is open. Open
state and the last thread live in the shell, so they survive navigation. A draft per subject is kept in
`sessionStorage`, inside try/catch.

### 5.2 How a screen gives its subject

- `web/src/chat/subject.tsx`: `ChatSubjectProvider`, mounted in `Frame` beside `PanelHost`, and
  `useChatSubject(subject, layer)` with `layer` `"page"` or `"panel"`. A stack: panel beats page, newest wins, unmount
  pops.
- `web/src/chat/routes.ts` maps every route in `main.tsx` to a default (`/engineering` → `page:engineering`,
  `/projects/:id/*` → `project:<id>`, `/settings` → `general`), so a screen that never calls the hook still has one. A
  test fails when a route has no entry.
- v1 explicit calls: `DecisionPanel` (`components/decision.tsx`) registers `request:<id>`, which the server turns into
  the build for a merge card; `pages/Engineering.tsx` registers `dev_job:<id>` for the selected build; the project page
  and task panel register `project:<id>` and `task:<id>`.

### 5.3 Inside the dock

- **Header and list.** Subject chips, "All conversations", minimize, close. The list puts General first, then 36px
  rows by last message with subject, last line, time, unread dot and an open task's state chip.
- **Messages.** All left-aligned under a meta line (name and role, time), so neither language's length sets the
  layout. Handoffs and escalations are quiet meta lines. Long pastes fold to 8 lines and "Show all 214 lines".
- **Task card.** Title, a state chip that carries the word as well as the colour, the "who" line, the branch, a link
  to the subject's card, and Cancel before it starts.
- **Composer.** Subject chips with × (to General) and Change (this screen, recent, General), and `To: Engineer ·
  Engineering ▾`. `@` opens an agent listbox (`aria-activedescendant`). Enter sends, Shift+Enter adds a line, and
  Enter never sends during IME composition (`isComposing`), or Korean input sends half a syllable. After send, a
  scrub notice says what kind was removed.

### 5.4 States, motion, unread, theme

| State | What shows |
|---|---|
| Empty | One line naming the recipient and the decision boundary |
| Loading | Skeleton bars, as `PanelSkeleton` |
| Replying | "Engineer is writing a reply", from an in-flight `chat_reply` job (`queue.in_flight`), static text |
| Waiting on usage | The job's `wait_reason` and reset time, worded as the build board does |
| Worker off | From `useFrame().company`: saved, answered once the background process runs |
| Agent paused | "You paused Engineer", with the resume link |
| Send failed | Text stays in the composer with "Not sent" and Try again |

The dock fades in over 150ms with an 8px slide, and a running task's dot uses the existing live halo. Under
`prefers-reduced-motion` both are still (DESIGN.md). The Team button counts **conversations** with unread replies,
with an `aria-label`. No sound and no toast, for replies or for finished tasks. The message list is `role="log"`,
announcing agent messages politely. Colours are existing tokens only (`elevated`, `panel`, `panel-2`, `accent-soft`,
`chip-*`), so light and dark come free.

### 5.5 Built by and checked by

`engineering/merge_review.card` gains `team: {built, reviewed, checked}`, each `[{agent, model}]` from `llm_calls`
grouped by `agent` for the build. `MergeReview.tsx` shows it under the headline. `engineering/status.steps` passes each
step's `by`, and the Engineering timeline names who did each step.

### 5.6 Copy keys

Console `web/src/i18n/areas/chat.ts`, server `i18n/messages/chat.py`. Korean follows KOREAN-STYLE.md. A name can end
in a Latin letter ("CEO"), so no `{value|을}` after one; a fixed word follows instead.

| Key | English | Korean |
|---|---|---|
| `chat.launcher` | Team | 팀 대화 |
| `chat.unread` | {one: "{n} conversation has a new reply", other: "{n} conversations have new replies"} | 새 답이 온 대화 {n}개 |
| `chat.general` / `chat.to` | General / To | 전체 / 받는 사람 |
| `chat.subject.merge` / `branch` | Merge approval {ref} / Branch {name} | 머지 승인 {ref} / 브랜치 {name} |
| `chat.placeholder` | Message {agent} | {agent}에게 보낼 메시지 |
| `chat.empty` | Ask {agent} about this, or ask for a change. Spending, sending outside and plan changes still come to you as decisions. | 이 일에 대해 묻거나 고쳐 달라고 하세요. 돈이 들거나, 밖으로 보내거나, 계획을 바꾸는 일은 지금처럼 결정으로 와요. |
| `chat.replying` | {agent} is writing a reply | 답을 쓰는 중: {agent} |
| `chat.task.received` / `running` / `budget` / `done` | Received / In progress / Waiting for budget / Done | 받았어요 / 하는 중 / 예산을 기다려요 / 끝났어요 |
| `chat.task.stopped` | Didn't finish. The branch is back where it was. | 끝내지 못했어요. 브랜치는 원래대로 돌려 놨어요. |
| `chat.status.merge_set_aside` | Merge approval {ref} is set aside while this is fixed. A new one comes after the check. | 고치는 동안 머지 승인 {ref} 카드는 내려 둬요. 확인이 끝나면 새 카드가 와요. |
| `chat.scrubbed` | {one: "Removed {n} secret before saving. Change it where it was issued.", other: "Removed {n} secrets before saving. Change them where they were issued."} | 저장하기 전에 비밀 값 {n}개를 지웠어요. 발급한 곳에서 바꿔 주세요. |
| `decide.mr.built_by` / `checked_by` | Built by {agent} / Checked by {agents} | 만든 팀원: {agent} / 확인한 팀원: {agents} |

The glossary gains one term, `Team chat` / `팀 대화` (code name `chat`): "A conversation with the team about what is on
your screen. Anything that cannot be undone still comes to you as a decision." Regenerate `term.ts` and PRODUCT.md's
table from it.

## 6. Safety

### 6.1 Secrets in pasted text

- Move `SECRET_PATTERNS` to `contracts/secrets_scan.py`, shared by both callers, and add what chat needs and the repo
  scan does not: `Authorization: Bearer <value>`, and `KEY=value` lines whose key ends in `TOKEN`, `SECRET`, `KEY`,
  `PASSWORD` or `PASS`.
- Scrub in the route, before the row exists, so no copy reaches the database, an SSE event, a prompt or a log. The
  stored text says `[removed: GitHub token]`; `scrubbed` keeps kind and count only.
- Scrub agent replies too. The engineer reads branches that may hold a committed `.env`.
- Never log a request body. The route logs the thread id and the scrub counts.

### 6.2 What an agent may do from chat

`chat_tags.ALLOWED[(agent, intent, subject kind)]` decides; the model's output is only a request.

| May | Who | Through |
|---|---|---|
| Answer | any agent | the reply |
| Start a fix round on a delivered build | engineer, on a `dev_job` subject | `revise._begin(by_owner=True)` |
| Set the build's pending merge card aside | server, only as step 2 of 4.3 | `requests.withdraw_pending` |
| Escalate to the CEO | any agent | `chat_reply` for `ceo` |
| Hand off | CEO | `chat_reply` for that agent |
| File a decision card | any agent | `requests.create`, normal policy and dedupe (`services/matters.py`) |

Never, whatever a message says: merge, push, deploy, approve, answer or decline a card, raise a budget, change a
connector or setting, send outside, change the plan or a spec, start or stop a stage, archive or delete, or run host
commands. A fix round keeps every existing guard: the credential-free container, the `git push/remote/merge` and `gh`
deny rules, and the re-check before delivery (ARCHITECTURE.md 1.5).

### 6.3 Prompt injection from pasted logs

An `npm install` log can carry text a package author wrote. Owner text, pasted logs, thread history and subject data
all enter the prompt through `fence.fenced`, which neutralizes the fence markers. The reply's only effects are the
typed intents, checked by the server, so a log that says "approve REQ-4" can at most make an agent file a card the
owner still decides. The chat reply runs read-only, so an injected "write this file" has no tool. The engineer acts
on the owner's words through `.ship/fix.md`, as for a merge declined with a note today.

### 6.4 The approval boundary

Chat changes no `AgentRequest` status except `withdraw_pending` on a build's own merge card when a fix round starts,
which sets the card aside and does not decide it. Everything else that needs the owner is a decision card, linked
from the thread by a `decision` line and answered in Decisions.

## 7. Phases

Each unit ends in a check. Run the named tests only (`uv run pytest -q tests/<file>`, or `bash scripts/test-linux.sh`
for container-backed ones).

**Phase 1: store, subjects, scrubbing.**

1. Move `SECRET_PATTERNS` with no behaviour change. Check: `tests/test_existing_product.py` unchanged and green.
2. `secrets_scan.scrub(text) -> (text, counts)`. Check: new `tests/test_chat_scrub.py` hides a GitHub PAT, a Slack
   token, `sk-ant-…`, a Bearer header and an `.env` block inside a real `npm` log, and leaves `npm ERR! code ERESOLVE`
   alone.
3. Migration and backfill. Check: new `tests/test_chat_store.py`: two racing first messages share one thread, `seq`
   has no gaps, an old task comment appears in its task thread. `tests/test_console.py` comment tests stay green.
4. `services/chat_tags.py`. Check: new `tests/test_chat_tags.py`: a merge request resolves to its build, the next merge
   card of that build resolves to the same thread, each kind's default agent.

**Phase 2: answering.**

5. `run_agent(runtime=...)`. Check: a test in the style of `tests/test_gate_sites.py` asserts an engineer `chat_reply`
   runs isolated with `Read`, `Glob`, `Grep` only.
6. `chat_reply` policy row, handler, `ChatReplyOut`, intent table. Check: `tests/test_gate_policy.py`,
   `tests/test_enqueue_admission.py`, and new `tests/test_chat_reply.py` with a stubbed `run_agent`: three quick
   messages make one job, a disallowed intent becomes an answer, escalation writes the line and enqueues the CEO, the
   hop limit holds, a `decision` intent files a card and resolves none.
7. The CEO's context block. Check: a context test asserts a recent thread appears fenced and clipped.

**Phase 3: the whole dock, a subject on every screen.**

8. Provider, hook, `routes.ts`, dock, Team button, every state, catalogs, glossary term. Check: new
   `tests/test_chat_routes.py` parses `main.tsx` and fails on a route with no subject; `tests/test_i18n.py`,
   `test_korean_style.py`, `test_i18n_particles.py`, `test_owner_text_surfaces.py`, `test_agent_names.py`;
   `cd web && npx tsc -b && npm run build`.
9. In the browser at http://127.0.0.1:8000: both languages, both themes, 1440, 1200 and 900, REQ-4 open in the side
   panel, reduced motion on. `/impeccable critique` into `.impeccable/critique/`, then `/impeccable polish`.

**Phase 4: work from chat, merge card and Engineering first.**

10. `asked_in`, the merge card set aside, status lines per round transition, local delivery and push-at-merge for chat
    rounds. Check: `tests/test_dev_fix.py` gains a round started from a message (card withdrawn, round queued
    `by_owner`, new merge card after delivery, one line per state); `tests/test_merge_gate.py` gains push-then-merge
    with the head check.
11. Derived card state. Check: `tests/test_chat_reply.py` covers each row of 4.4 from `DevJob` fixtures.
12. `team` on the merge card and `by` on steps. Check: `tests/test_i18n_merge_review.py` and `tests/test_dev_board.py`
    in both languages, and for a job from before `by`.
13. The REQ-4 story on a `[TEST]` business: on the merge card write "npm install fails with ERESOLVE, please fix",
    see received, in progress, done, and a new merge card. Then ask "use the Claude subscription instead of an API
    key" and see a fix round or a decision card, never an approval.

Later, not v1: work on other subjects, Slack mirroring through the existing notify path (outbox by default), and
folding Ideas (`owner_ideas`) into General if threads make them redundant.

## 8. Note: optional scenario marks on the merge card

Separate small branch. `components/reviewModel.ts` has `Mark = "ok" | "issue"`, and a check marked `issue` becomes a
"marked" doubt that `isChecked` ticks for the decline note. Change: every scenario starts "not checked yet", marking
stays optional, only checks marked as a problem go into the decline note, and nothing waits on marks.

## 9. Open questions for the owner

1. **Start work as soon as the engineer reads the message, or wait for a Start press on the task card?**
   Recommendation: start at once. The message is the ask, the round is capped by the project budget and the 6 USD fix
   budget, the card shows the cost, and Cancel works until it starts. A Start press brings back the friction this
   removes.
2. **When chat starts a fix on a build whose merge card waits, set that card aside until the fix is checked?**
   Recommendation: yes. The code already refuses a fix round while a merge waits, since approving would merge the
   commit being replaced. The thread says so, and a new card comes after the check.
3. **For a product with a GitHub remote, should a chat fix push at delivery as a declined-merge fix does today, or
   stay local until the merge is approved?** Recommendation: stay local. "Chat never pushes" stays literally true, and
   the merge approval, already a strong approval, pushes the checked commit just before merging. The cost: the PR on
   GitHub shows the old commit until then.

## 10. As built: where the code departs from this design

Each departure follows the code where the design and the code disagreed.

- **Reply key.** The design keys a reply job by `last_owner_seq`, which would make one job per owner line. The key is
  the line the agent last answered (`data.answered_seq` on its reply), so every line sent before the job runs joins
  it. A line that arrives while the reply runs gets the next job, enqueued by the reply itself. An escalated reply is
  keyed by its escalation line, and does not move the CEO's own answered pointer.
- **A retried send.** `client_id` is a column on `agent_messages`, unique per thread, so a retry finds its line.
- **Model per call.** `run_agent` takes `runtime`, `tools` and `model_name` per call. `model_name`, not `model`: every
  test stub of `run_agent` names its output-model argument `model`.
- **Who did it on the merge card.** `team` sits beside `review` in the request read model (`requests._merge_card`), not
  inside `merge_review.card`, which reads no session. `engineering/team.py` reads it from `llm_calls` by role:
  engineer built, code reviewer reviewed, the product agent's acceptance calls checked.
- **Work from chat.** `revise.from_chat` sets the build's waiting merge cards aside, then calls `_begin` with
  `asked_in`. The engineer's fix prompt says the change was asked in team chat, not that the owner turned the build
  down. The "done" line comes from the acceptance check's new merge card (`worker._accept`), so asking for the merge
  again later does not repeat it.
- **Cancel.** Cancel also ends a round still waiting for budget, which has no queue job yet. The round ends `stopped`
  with `cancelled: true`, and the build offers its merge card again (`merge_again`).
- **Local delivery.** A chat round never pushes. `result.unpushed` holds the delivered commit only when the build
  already has a PR on the forge; the merge card says it is on this computer only, and `worker._merge` pushes exactly
  that commit, then merges under `--match-head-commit`.
- **The CEO's view.** The team chat block is in `context.project_brief`, which the project run's CEO calls read.
  `portfolio_review` and the cycle calls do not read it yet.
- **Task comments.** `control.comment_task` now writes its line through the chat store, scrubbed, into the task's
  conversation.
- **A reply that gave up** writes `chat.status.reply_failed` and lets go of its key, so the owner's next line asks
  again.
- **Phase order.** The work path (phase 4) was built before the dock (phase 3), since the dock's first use is the
  merge card's fix.
- **Review fixes.** A chat ask while the build has a check, merge or round on its way starts nothing and says so
  (`revise.busy`), so no request is lost; every waiting card is set aside, fix-or-drop too. The owner's lines reach
  the engineer fenced as context, the task is the engineer's own words, and rounds after a chat round stay local and in
  its conversation. Chat decisions never auto-approve and file no development ask; replies get no tools; status
  values, decision drafts and activity data are scrubbed, which catches more shapes now (URL passwords, JSON, flags).
