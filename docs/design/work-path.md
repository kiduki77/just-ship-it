# Work path: every decision in its place

Status: design, no code, grounded in `main` e8773bd. Roadmap 0.4a (records, contract, card context) and 0.4b (list,
outline, path view). I could not open a browser or read the owner's database, so what this says about REQ-6 comes from
the code paths that can file it, not from its row. Task and evidence ids, stage names and counts in the examples
(T-41, EV-14, "Validate", "8 of 14") are made up. REQ-6, P-3 and the experiment's name are real.

## Why

REQ-6, a CEO QUESTION in P-3, asks for the next feature to build (name, feature list, number of screens). The card does
not say that it belongs to the experiment "compare plain chat vs checklist chat vs CLI", that it was asked because the
comparison was designed and never run, what it holds up, what answering does, or what happens if nobody answers. The
owner wants every decision shown in its place in the work, recorded exactly on the server and required of every agent,
plus a list ordered by what each decision frees and a React Flow view of the path.

The gap is in the records. `agent_requests` knows `project_id` and, from one call site, `task_id`
(`db/models.py:488-511`). Every CEO path files with the project id only (`orchestration/company_graph.py:154`, `:762`;
`operations/jobs.py:122`). `blocking` is one boolean that parks the whole run (`human_gate`, `:212-237`). No code writes
`RequestStatus.EXPIRED`, and `open_blocking` ignores `expires_at` (`services/requests.py:1455-1458`).

## 1. Data model

### 1.1 Reuse before adding

`requests.create` (`services/requests.py:280`) stays the one funnel, and `outcome` (`:1083-1128`) keeps deriving server
effects from `_effect` (`:963-1011`), the table that queues follow-up jobs. `Task.depends_on`, `requests.current`
(`:561`), `revises`/`replaces`/`into`/`superseded_by`, `OwnerTask.request_id` and the `approval_request_id` columns on
decisions, specs, plans and skills are already exact. The path reads them and copies nothing.

### 1.2 New table `review_rounds`

One row per CEO step that can file cards. It replaces `Project.context["ceo_reviews"]`, whose entries have no id.

| Column | Type | Meaning |
|---|---|---|
| `id`, `project_id` | int PK, FK indexed | |
| `seq` | int | Order in the project, unique with `project_id` |
| `kind` | varchar(16), check | `plan`, `review`, `realign`, `decision` |
| `plan_round` | int | Not an id: a review can run twice in one round (`company_graph.py:771-773`, `:1150`) |
| `assessment`, `action`, `unmet` | text, varchar, JSONB | What `ceo_reviews` holds today |
| `call_id`, `created_at` | varchar(40), timestamptz, nullable | The model call that produced it. Null on backfilled rows |

### 1.3 New table `request_links`

Typed edges from one request, written only by `services/requests.py`, in the transaction that writes the request.

| Column | Type | Meaning |
|---|---|---|
| `request_id` | FK `agent_requests`, indexed | |
| `rel` | varchar(16), check | `raised_by`, `holds`, `answer_use`, `followed` |
| `target_kind`, `target_id` | varchar(16) check, int | `task`, `round`, `decision`, `chat_message`, `dev_job`, `spec`, `plan`, `bet`, `idea`, `skill`, `job`. No FK. Index `(target_kind, target_id, rel)` |
| `phase` | varchar(8), nullable | For a task hold: `start` (may not begin) or `finish` (its result waits, today's `APPROVAL_REQUIRED`) |
| `option_key`, `effect` | varchar, nullable | For `answer_use` and `followed`: which answer, and a closed effect key (2.3) |
| `revision`, `source`, `known` | int, varchar | Revision that wrote it; `agent`, `server` or `backfill`; `recorded`, `backfilled` or `matched` (1.6) |
| `recorded_at`, `ended_at` | timestamptz | `ended_at` is set when a later revision replaces an agent row |

A `CHECK` lists the allowed `(rel, target_kind)` pairs, generated from one dict in `contracts/work_path.py`. A unique
index over the edge and `revision` makes every write idempotent.

`raised_by` is what led to the card, one row per asker: a draft that `matters.place` folds into an open card, or that
`find_similar_pending` matches, adds its own row instead of being lost. `holds` is a task that waits (1.5).
`answer_use` is what the agent declared answering does; server effects stay in `_effect`. `followed` is what actually
ran, written by `respond` and by expiry. Today that is lost once a card is decided (`report`, `requests.py:1290`).

### 1.4 Columns added to `agent_requests`

| Column | Type | Meaning |
|---|---|---|
| `context_version` | smallint, default 0 | 0 = filed before this design, 1 = filed under it |
| `hold_scope` | varchar(10), check | `none`, `tasks`, `planning`, `run`. The one source of scope |
| `ask_reason` | varchar(24), nullable | Closed `AskReason` key (2.1) |
| `reason_refs` | JSONB `[]` | `[{"kind": "evidence", "id": 14}, {"kind": "task", "id": 41}]`, each checked to exist in the project |
| `reason_note` | varchar(240), nullable | One line in the agent's words, always shown labelled with agent and time |
| `expiry_policy`, `default_option` | varchar, nullable | `close`, `default` or `keep_waiting`, and the option `default` uses |
| `blocking` | generated, `hold_scope <> 'none'` | Was a stored boolean. Now `GENERATED ALWAYS AS (...) STORED` |

`CHECK (context_version = 0 OR (ask_reason IS NOT NULL AND expiry_policy IS NOT NULL AND (expiry_policy <> 'default'
OR default_option IS NOT NULL)))`, and `create` refuses a row without a `raised_by` link in the same transaction. The
generated `blocking` keeps its 52 backend readers in 20 files and the console's `r.blocking` unchanged (they read
"holding up work", the glossary's meaning), and no code can write it. Its writers (`create` `:335-336`, merges `:485-486`
and `:1494`, `reopen` `:741`) move to `hold_scope`. `task_id` gets the index it lacks (`models.py:493`).

### 1.5 Project holds and task holds, one predicate

| `hold_scope` | What waits |
|---|---|
| `run` | The whole project run, as `blocking` does today |
| `planning` | The CEO's next plan step: the next `ceo_review`, follow-up planning, `decide`, `final_gate`, `report`. Planned tasks go on |
| `tasks` | The tasks in `holds` rows. A `start` hold stops promotion, a `finish` hold keeps the result from counting |
| `none` | Nothing. The card is still in the owner's list |

`services/holds.py` owns one pure function, `park_reason(s, project_id, now)`: `paused`, `run`, `planning`, `starved`
(no task can start, some are held, none running) or nothing. `human_gate`, `schedule`, `_maybe_resume` and
`runner.resume` all call it, so the gate and the waker cannot disagree. A hold is live while its request is PENDING
(after `requests.current`) and its task is not terminal, so a task that realign cancels drops out with no write. A
`start` hold acts like an unmet dependency in `promote_ready` (`services/tasks.py:203`). The owner answered yes to open
question 1 (unit 9), so a `tasks` card parks the run only when it is starved, and a `planning` card only once no task
is left to go on before the next plan step; a `ceo_review` that files a `planning` or `run` card goes to the gate
instead of `decide`. Only then does the card say the whole project waits.

On resume, `finish` holds are settled from the hold rows. Today `human_gate` reads "the latest request with this
`task_id`" (`company_graph.py:231-235`), which can judge a task by a later, unrelated card.

### 1.6 Migration and backfill

One hand-written migration after `7c1e5a9d3f20_team_chat`. Stop the worker first and check that autogenerate leaves
the LangGraph checkpoint tables alone (CLAUDE.md). The owner applies it.

1. Create both tables and the columns, with `context_version = 0` everywhere.
2. Backfill in SQL only what is recorded today (`source = backfill`). `hold_scope = 'run'` where `blocking`, else
   `'none'`, which is what `open_blocking` means; resolved rows get `ended_at = resolved_at`. A `finish` hold for each
   `APPROVAL_REQUIRED` task with its pending blocking APPROVAL. `raised_by` from `task_id`, the `action_params` keys
   `decision_id`, `need.task_ids`, `spec_id`, `plan_id`, `dev_job_id`, `use_of_job`, `bet_id`, `idea_id`, `skill_id`,
   `re_request_of`, and the chat message whose `data.request_id` names the row (`services/chat_reply.py:278-279`).
   `review_rounds` from each `ceo_reviews` entry in order, plus a `plan` row per planned project. `expiry_policy =
   'keep_waiting'` where `expires_at` is null (`engineering/questions.py:71`, `engineering/usable.py:140`).
3. Swap `blocking` for the generated column and add the `CHECK`. Downgrade makes it a plain column again.
4. Move the `ceo_reviews` writer (`company_graph.py:763-766`) and reader (`api/rows.py:14-16`) to `review_rounds`.

Left unknown, never guessed: which CEO step filed a project-only card (plan, review or KPI review), its reason, its
answer use, and its expiry rule where a date exists. `ship work-path audit` prints how many cards have each field
recorded, backfilled, matched or unknown, before and after.

**One more exact source, for CEO cards.** Every model call keeps a `raw_path` folder with `output.json`
(`agents/executor.py:193`, `capability/factory.py:160-161`). An opt-in command, `ship work-path backfill --from-calls`,
links a card to the one call by the same agent and project, before the row's `created_at`, whose `requests[]` holds a
draft with the identical title. It sets `known = matched` with the `call_id`; a revised title, two matches or a missing
file leave the card unknown. This is open question 3. I did not check that the owner's `var/` still has these folders.

**REQ-6 after the backfill:** `hold_scope = run`, no `raised_by` (only `plan`, `ceo_review` or the KPI review file it,
all project-only), reason and expiry rule unknown. The chain reads goal, experiment, P-3, "not recorded", the question.
A single `--from-calls` match turns the fourth step into "CEO plan" or "CEO review" (the call's `purpose`).

## 2. The request contract

### 2.1 What an agent must send

`AgentRequestDraft` (`contracts/requests.py:182-219`) loses `blocking` and gains a required `context`:

```python
class RequestContext(BaseModel):
    reason: AskReason                       # closed list, each worded by the catalog
    refs: list[Ref] = Field(max_length=5)   # EV-n, task keys, REQ-n of an earlier answer; each must exist
    note: OneLine(120)                      # the agent's own line
    holds: HoldDecl                         # 2.2
    on_answer: list[AnswerUse] = Field(max_length=4)   # per option or any answer, 2.3
    if_unanswered: ExpiryDecl               # close | default(option) | keep_waiting

class AskReason(StrEnum):
    GOAL_UNCLEAR; INPUT_MISSING; STEP_NOT_RUN; RESULT_NEEDS_CALL; EVIDENCE_CONFLICTS
    NEEDS_PERMISSION; SPEND; BUILD_NEEDED; SPEC_UNCLEAR; FOLLOWS_DECISION
```

The agent never says where the card came from. `requests.create` takes a required keyword `origin` (`TaskOrigin`,
`RoundOrigin`, `DecisionOrigin`, `ServerOrigin(kind, ref_id)`) from the call site, and `tests/test_request_sites.py`
fails on any `requests.create(` or `from_agent_drafts(` call without it. `plan` and `ceo_review` insert their
`review_rounds` row first. `_create_requests` takes `origin` in place of the unused `task_id` (`company_graph.py:85-87`).
`refs` may be empty only for `GOAL_UNCLEAR`. REQ-6's real reason is `STEP_NOT_RUN`, with the comparison task and its
evidence as refs, so the card links to the step that did not run instead of describing it.

### 2.2 What an agent may hold

A task author sees only its own key (`orchestration/context.py:232-235`), so it cannot name other tasks.

| Filer | `holds` it may declare |
|---|---|
| Task agent (`TaskOrigin`) | `this_task` (finish hold on itself), `my_dependents` (start holds on tasks that depend on it, resolved by the server), `planning`, `nothing` |
| CEO (`RoundOrigin`, `DecisionOrigin`) | the above, `tasks` with keys from the plan it was shown, and `run` only with `GOAL_UNCLEAR` (the rule in `prompts/agents/ceo.md:18`) |
| Server code (`ServerOrigin`) | its `ORIGIN_RULES` row (2.4), never text |

`ceo_review` files its requests before it creates its follow-up tasks (`company_graph.py:762`, then `:769`). The order
flips, so a review's card can hold the tasks it just planned. A hold on a task the filer depends on, or on a terminal
task, is refused (`hold_target_is_ancestor`, `hold_target_terminal`), so hold edges cannot close a cycle.

### 2.3 Who fills "after you answer", and how it stays true

Three parts, and only the first comes from the agent. (1) `on_answer`, closed keys: `informs` (agents read the answer,
true today), `replans` (the CEO re-plans unstarted tasks) and `feeds` (the answer goes into the named tasks' prompts).
`respond` executes each. `replans` enqueues `realign_project` whatever the project status, which closes the gap where an
answer on a WAITING_HUMAN project never reaches a re-plan (`requests.py:838`). (2) Server effects from `_effect` and
`outcome`, unchanged. (3) Released holds, derived ("T-41 can start").

The sentence and the behaviour come from the same keys, so a card cannot promise what `respond` will not do. A test
walks every effect key and asserts that `respond` wrote its `followed` row and queued its job. Once decided, the card
reads `followed`, not a prediction. "If you don't answer" is `expiry_policy` plus `expires_at`, whose clock stays the
server's (created + 2 x `grant_ttl_hours`, `requests.py:333,344`).

### 2.4 Rules per request type

`requests.check(draft, origin, project) -> list[Refusal]` runs before insert. A refusal writes no row and no links.

| Type | `holds` | `if_unanswered` | Extra refusals |
|---|---|---|---|
| QUESTION, CLARIFICATION, ESCALATION | any the filer may hold | `close`; `default` (must be the recommended option); `keep_waiting` only with `none` | `run_not_goal_unclear`, `refs_empty` |
| APPROVAL, RESOURCE | `this_task`, `my_dependents`, `none` | `close` | `expiry_cannot_approve` |
| PROPOSAL | `none`, `planning` | `close`, `keep_waiting` | `default_not_allowed` |
| DEVELOPMENT, agent ask | `none`; the server holds the build | `close` | `development_holds_server_owned` |
| Merge, plan, spec approval, spec question, fix, use-of-build | `ORIGIN_RULES`: a `job` hold on the build or spec | merge and plan `close`; spec question and use-of-build `keep_waiting` | a path with no row cannot ship |
| Chat (`services/chat_reply.py:272`), ideas, bet retro, skills, KPI, portfolio | `ORIGIN_RULES`, `none` unless the row says otherwise | `ORIGIN_RULES` | same |
| Owner to-do | not a request. It inherits its request's context through `OwnerTask.request_id` | | |

`ORIGIN_RULES: dict[OriginKind, OriginRule]` gives each server path its reason, holds and expiry policy. Adding a path
is one row, and a test fails when a `ServerOrigin` kind has none.

### 2.5 Refused, old and partial cards

- **Shape errors** fail at schema parse: `--json-schema` makes `context` required in every output model that carries
  drafts (`contracts/work.py:128`, `planning.py:46,75`, `operations.py:36,43`, `chat.py:27`).
- **Meaning errors** (unknown key, missing ref, wrong scope) send a task back once through `_send_back` with the
  refusal key. A second refusal files nothing and writes `request_not_filed` to the task result and the activity log,
  which the CEO reads in `project_brief`. A CEO step's refusal goes on its `review_rounds` row for the next CEO call.
- **Old cards** (`context_version = 0`) stay answerable, nothing is invented for them, and the expiry sweep skips them.
- **A revised card** (same REQ id, new `revision`) writes new agent rows and ends the old ones. Server rows stay.

`prompts/agents/ceo.md:18,25,29` and `prompts/company_preamble.md:25` replace "set `blocking`" with a paragraph on
`context`, and the schema descriptions repeat it (new Korean prompt lines follow KOREAN-STYLE.md).

## 3. Endpoints

| Route | Returns |
|---|---|
| `GET /api/requests/{id}` | Today's read model plus `context: CardContext` (4.2) |
| `GET /api/businesses/{id}/your-turn` | `YourTurn {items, outlines}` (5) |
| `GET /api/projects/{id}/work-path?focus=request:6&open=` | `PathView {nodes, edges, focus, folded, rev, too_big}` (6) |

All three read one graph from `services/work_path.py`: `load(s, business_id, projects=None) -> WorkGraph` (a handful of
`IN` queries), then pure `card_context(g, id)`, `your_turn(g)` and `path_view(g, focus, open)`. One function counts what
a card frees, so the card, the list and the graph agree. Text is catalog keys plus vars (docs/I18N.md). There is no cache. A few
hundred rows per business is my estimate, not a measurement, and the console already refetches on table events
(`useLive`, `web/src/api.ts:173`). `rev` hashes node states so the client skips a re-layout when nothing moved.

## 4. Card context

### 4.1 Layout

`DecisionPanel` (`web/src/components/decision.tsx:1070`) gets a **Where this sits** block between the from-line and
`ReportHead` (`Body`, `:1014`), compact so the question and recommendation stay first (PRODUCT.md). In order:

1. **Why you're asked.** The reason sentence, refs as links (`EV-14`, `T-41`), the agent's note quieter, with time.
2. **Chain.** 4 to 6 linked steps, each an icon and a word (goal, experiment or stage, project, the filing step, this
   question). It wraps instead of truncating, so neither language's length breaks it.
3. **Holding up**, **After you answer** (a line per option when they differ) and **If you don't answer** (rule and
   date), always visible, then **Show on the path**. Long refs and notes fold under **Details**, two levels at most.

### 4.2 Read model

`CardContext` has `why` (reason key, refs, note, agent, time, stale), `chain` (goal first, each `Step` a kind, ref,
`label_key` plus vars, state and `known`: recorded, derived, backfilled, matched or unknown), `holds` (scope, tasks with
phase and state, `behind`, `nothing_live`), `after` per option (source declared, server or followed), `unanswered`
(policy or none, date, default option, what it releases or cancels), `complete` and `as_of`.

### 4.3 States

| State | What shows |
|---|---|
| Loading | Three skeleton rows the block's height, as `PanelSkeleton` |
| Unknown or matched step | "Not recorded", muted, with "Decisions asked before {date} don't record this." once; a matched step adds "matched from the CEO's recorded output" |
| Stale note | The note plus "Written {ago} by {agent}. {thing} has changed since." when its round was superseded or a ref task ended |
| Holds nothing now | "The task this held was cancelled by a re-plan on {date}." The card stays answerable |
| Decided | The three always-visible rows become **What happened**, from the `followed` rows |
| Error | The card renders; the block says it couldn't load and offers Try again |

### 4.4 Copy

Console area `web/src/i18n/areas/path.ts`, server area `i18n/messages/path.py` for refusals. Korean follows
KOREAN-STYLE.md. Glossary words only: 결정, 업무, 프로젝트, 실험, 단계, 계획, 일을 막음, 만료.

| Key | English | Korean |
|---|---|---|
| `path.block.title` / `path.why.title` | Where this sits / Why you're asked | 이 결정의 자리 / 묻는 이유 |
| `path.reason.STEP_NOT_RUN` | A step this needs was planned but never run. | 이 결정에 필요한 단계를 계획만 하고 아직 하지 않았어요. |
| `path.holds.title` | Holding up | 막고 있는 일 |
| `path.holds.behind` | {one: "and 1 task after them", other: "and {n} tasks after them"} | 그 뒤 업무 {n}개 |
| `path.holds.run` | The whole project waits while this is open. | 이 결정이 열려 있는 동안 프로젝트 전체가 기다려요. |
| `path.holds.planning` | The next plan for {project} waits. Work already started goes on. | {project}의 다음 계획이 기다려요. 이미 시작한 업무는 계속해요. |
| `path.holds.none` | Nothing waits on this. | 이 결정을 기다리는 일은 없어요. |
| `path.after.title` / `path.after.replans` | After you answer / The CEO re-plans the tasks that haven't started. | 답하면 / CEO가 아직 시작하지 않은 업무를 다시 계획해요. |
| `path.lapse.default` | If you don't answer by {when}, the team goes with "{option}" and records it as the default, not your answer. | {when}까지 답이 없으면 "{option}"으로 진행하고, 오너의 답이 아니라 기본값으로 적어 둬요. |
| `path.lapse.close` | If you don't answer by {when}, this closes and the tasks it holds are cancelled. The CEO re-plans without it. | {when}까지 답이 없으면 이 결정은 닫히고 기다리던 업무는 취소돼요. CEO가 이 결정 없이 다시 계획해요. |
| `path.lapse.unknown` | No rule was recorded for this. It waits for you. | 답이 없을 때의 규칙이 기록되지 않았어요. 오너를 기다려요. |
| `path.unknown` / `path.show` | Not recorded / Show on the path | 기록 없음 / 경로에서 보기 |
| `path.stale` | Written {ago} by {agent}. {thing} has changed since. | {agent}가 {ago}에 쓴 글이에요. 그 뒤로 {thing\|이} 바뀌었어요. |

The glossary gains `Work path` / `일의 경로` (code name `work_path`): "How the work behind a decision connects: the
goal, the experiment, the project, the task, and what waits on your answer." Regenerate `term.ts` and PRODUCT.md's
table from it.

### 4.5 The block and team chat

`DecisionPanel` already registers chat subject `request:<id>` (`decision.tsx:1075`). The dock does not repeat the block;
`chat_tags.facts` for a request gains the project and the filing step as chips. When the owner asks "why is this
asked?" in chat, the reply prompt carries the rendered `card_context` through `services/fence.fenced`, so the answer
comes from the same records. Chat changes no link (`chat_tags.ALLOWED` is unchanged). A card filed from chat gets
`ServerOrigin(chat, message_id)`, and its "asked in team chat" step opens the thread.

## 5. Your-turn list and project outline

### 5.1 What "frees" means

For a pending card `r` in project `p`, over non-terminal tasks, `frees(r)` is the tasks with a live hold from `r` and
from no other pending card, so nothing else stops them once `r` closes. `behind(r)` is the tasks downstream of `r`'s
held tasks through `Task.depends_on`, minus `frees(r)`. They get closer to running but are not free. A `run` card frees
every non-terminal task of `p`. A `planning` card counts `p`'s unstarted tasks plus one for the next plan step.

The sort key is `(scope: run, planning, tasks, none; -(|frees| + |behind|); expires_at, nulls last; id)`. It replaces
`(estimate, due)` for decisions inside today's BLOCKING tier (`services/today.py:96-97`, `:158-165`), so the To do page
(`web/src/pages/MyTasks.tsx:295-302`) and the Decisions page (`web/src/pages/Approvals.tsx`, route `/inbox`) share one
order. Stage gates and the kickoff (`today._turns`, `:205-208`) keep their tier. A card whose holds are all moot shows
"holds nothing now" and sorts with `none`.

### 5.2 Outline

The Decisions page gains a **By project** view beside the list, from the same `your_turn` result:

```
Goal: <the approved plan's goal line>
  Cycle 1 > Validate > Experiment: compare plain chat vs checklist chat vs CLI
    P-3   8 of 14 tasks done · 2 held · 1 decision waiting for you
      REQ-6  Next feature to build      holds T-41, T-42, 5 after them
      T-38   Run the comparison         failed
```

Each project rolls up `done` (DONE) over `total` (all but CANCELLED and REPLACED), plus held and failed tasks and the
cards and to-dos waiting on the owner. Stage and cycle rows sum their projects. A project with nothing waiting folds to
one row. The outline is also the text version of the path view.

## 6. Path view (React Flow)

This is a shape brief with no human round (`.claude/skills/impeccable/reference/shape.md`). Every choice is an
assumption until `/impeccable shape` runs with the owner before 0.4b code.

**Job and placement.** The owner comes from a card, or from a project's new **Path** tab
(`web/src/pages/Project.tsx:80-89`), to see where a decision sits and what moves when it is answered. The side panel
(`components/panel.tsx:137`) is too narrow for a graph, so the path is a tab and the card stays open in the panel
beside it. One project per view, with goal, cycle, stage and experiment as the top row.

**Nodes and edges.** State is always an icon and a word, never colour alone. Colours are existing tokens (`chip-*`,
`panel`, `line`), so dark mode comes from `:root[data-theme="dark"]` (`DESIGN.md:249`). Edges are never animated.

| Node | Shows | Enter opens |
|---|---|---|
| Goal, cycle, stage, experiment | its icon and name | its screen |
| Task | status icon, `T-41`, title, status word (done, running, waiting, held, failed) | task panel |
| Decision | question icon, `REQ-6`, headline, "waiting for you" or the outcome | decision panel |
| CEO step | "CEO review, round 2", plan, realign or meeting decision | the project's decisions tab |
| To-do | `OT-n` and title | the to-do |
| Folded group | "12 tasks done", "3 earlier rounds" | unfolds in place |

Edges carry a text label as well as a line style: `depends_on` solid ("after"), `holds` dashed with a lock marker
("holds", "holds result"), `raised_by` dotted ("asked in"), `followed` solid once decided ("started", "cancelled").

**Layout.** Dagre (`@dagrejs/dagre`, a new dependency whose cost `npm run build` must show first), in the client, left
to right from 1200px, top to bottom below. Nodes are flat, with no `parentId`, because sub-flow layout breaks when a
child links outside its group; folded nodes do the grouping. Layout runs only when `rev` changes. Custom nodes are
`memo`ed and read only their own `data`.

**Defaults, focus, limits.** Unfolded by default: the focused card's chain, every pending card with the tasks it holds,
and one done neighbour each. The rest folds by toggling `hidden`, two levels at most. The view opens with
`fitView({nodes: [{id: "r:6"}], padding: 0.3, maxZoom: 1.2})` and keyboard focus on that node, with no duration under
`prefers-reduced-motion`. At most 20 nodes show by default, where the hairball risk starts, and up to 60 when unfolded.
Past 150 nodes the tab opens the outline ("This project is too big to draw well, so here is the list.") with a link to
draw only the card's neighbourhood. These numbers are guesses to check on a `[TEST]` project in 0.4b.

**States.** Loading and error show the outline, not an empty canvas. Empty says the CEO plans tasks when the project
starts. An old card with no `raised_by` sits beside its project on a "not recorded" edge. Live updates change nodes in
place and never re-fit, so the view does not jump.

**Keyboard, readers, width.** Tab moves through nodes and edges, `autoPanOnNodeFocus` keeps the focused one in view,
Enter opens it, Space folds a group, and Escape returns to **Show on the path**. Each node and edge has a catalog
`aria-label` ("Task T-41, held by REQ-6"), and `ariaLabelConfig` takes React Flow's own labels from `path.flow.*` in
both languages. A **List** toggle shows the outline. Below 900px the graph runs top to bottom under the panel overlay;
390px stays out of scope (DESIGN.md).

## 7. Safety and failure modes

- **Stale text.** Only `reason_note` can go stale, and 4.3 labels it. Chain, holds and counts are derived on read.
- **Wrong edges and cycles.** Keys resolve only inside the card's own project; cross-project, unknown and terminal
  targets are refused, and origin comes from the call site. `hold_target_is_ancestor` refuses a hold on work the filer
  depends on, tasks stay a DAG (`services/tasks.py:182`), and no request waits on a task.
- **Order on resume.** When an answer both frees the run and `replans`, `respond` enqueues only `realign_project` with
  `resume_after`, and the realign enqueues `resume_project` once `park_reason` is clear. The run never resumes on the
  plan the answer changed. The order must be explicit because realign has its own lock key (`services/queue.py:268-271`).
- **A stranded wait.** `_maybe_resume` returns unless the project is already WAITING_HUMAN (`requests.py:928-929`), and
  `human_gate` commits WAITING_HUMAN after its check, so an answer between the two can strand the run. I read this from
  the code and did not reproduce it. A worker-tick sweep, `reconcile_parked`, resumes any WAITING_HUMAN project whose
  `park_reason` is clear and that has no run job in flight (`queue.in_flight`).
- **Expiry.** `requests.expire_due(s, project_id, now)` runs every worker tick and at the top of `human_gate`, locks
  the row and skips non-PENDING rows, so an answer that took the lock first wins. It writes `EXPIRED`, applies the
  policy and writes `followed`. `default` is stored as `{"by": "expiry"}` in `owner_answers`, never as the owner's
  words. `close` cancels start-held tasks, rejects finish-held results and enqueues a realign. A past-due card holds
  until the sweep, so its default is recorded before the work goes on. Two strings that already claim expiry closes
  cards (`common.activity.decision.expired`, `decide.who.system_note`) become true.
- **Held tasks cancelled or replaced.** A cancelled target drops out of the live join and the card says "holds nothing
  now". A task replaced through `TaskDraft.redoes` gets its live holds copied to the successor in the same transaction.
- **Answer against replan.** `respond` and `consolidate` both lock the request row (I did not confirm `consolidate`
  does today; the unit adds it if missing). If the answer wins, realign sees a closed card. If realign wins, `respond`
  refuses with the existing "already resolved" error and the card shows where it went (`into`).
- **Injection through the why.** The reason is a closed key, refs must be rows the project owns, and the note is 120
  Korean characters (stretched for English by `clip`), plain text with no links, reaching other agents only through
  `fence.fenced`. A hostile note can mislead one labelled line. It cannot set a hold elsewhere, a server effect, a clock
  or an expiry that approves.

## 8. Phases

Each unit ends in a check. Run only the named tests (`uv run pytest -q tests/<file>`).

**0.4a: records, contract, card context.**

1. Migration, models, backfill, `ship work-path audit`. Check: new `tests/test_work_path_migration.py` on a fixture DB
   (a pending blocking card becomes `run`; an `APPROVAL_REQUIRED` task gets its finish hold; `ceo_reviews` become rows
   in order; a CEO-only card stays unknown; `blocking` reads as before; downgrade restores it).
2. `contracts/work_path.py`, the draft change, prompts. Check: new `tests/test_request_contract.py` (shape refusals; the
   allowed-pairs dict matches the `CHECK`), `tests/test_i18n.py`.
3. `create(origin=...)`, `check`, `ORIGIN_RULES`, joined cards, the flipped `ceo_review` order. Check: new
   `tests/test_request_sites.py` (AST) and `tests/test_request_refusals.py` (a case per row of 2.4; a joined card keeps
   both askers), plus `tests/test_decision_card.py`, `test_re_request.py`, `test_approval_integrity.py`.
4. `park_reason` in the gate, `schedule`, `_maybe_resume`, `runner.resume`, and `reconcile_parked`, still parking for
   any hold. Check: new `tests/test_park.py` (gate and waker agree per scope; a later card no longer decides a held
   task; a stranded project resumes), `tests/test_gate_sites.py`, the `human_gate` cases in `tests/test_console.py`.
5. `expire_due`. Check: new `tests/test_request_expiry.py` (each policy; an approval never defaults; old rows skipped;
   an answer and the sweep racing on one row).
6. `respond` runs `on_answer`, writes `followed`, realigns before resuming. Check: new `tests/test_respond_followed.py`
   (each effect key queues its job; `replans` on a waiting project realigns, then resumes), `test_decide_idempotent.py`.
7. `work_path.load` and `card_context` on `GET /api/requests/{id}`. Check: new `tests/test_card_context.py` (a
   REQ-6-shaped fixture ends in "not recorded" and holds `run`; a new card has every field), `test_i18n_server_words.py`.
8. The block, `path.ts`, the glossary term. Check: `tests/test_i18n.py`, `test_korean_style.py`,
   `test_i18n_particles.py`, `cd web && npx tsc -b && npm run build`, then http://127.0.0.1:8000 in both languages and
   themes at 1440 and 900, and `/impeccable critique` into `.impeccable/critique/`.
9. Only if open question 1 is yes: siblings of a held task run. Check: `tests/test_park.py` gains "a sibling runs; the
   run parks only when starved; `ceo_review` never judges a round while held tasks are open".

Exit criteria for 0.4a, fixed before it starts:

- No new card can be stored without its origin, reason and expiry rule (the `CHECK` and the AST test).
- REQ-6 shows goal, experiment, P-3, "not recorded" or its matched CEO step, and the question, with nothing guessed.
- On a `[TEST]` business, a new CEO question shows why, what it holds, what answering does and what happens on expiry.
  Answering one and letting another expire do what the cards said, as their `followed` rows record.
- No PENDING card past `expires_at` holds a project, except `keep_waiting` and old rows.
- The owner checks the block on a real card in both languages.

**Built (0.4a, 2026-11-13).** Units 1 to 9 landed. Where the code differs from the plan above:

- `planning` parks a run only once no task is left before the next plan step, so the gate and the wakers agree while
  planned tasks can still run.
- `ceo_review` has an edge to `human_gate`: when a card it just raised holds the next plan step or the run, it waits
  for the answer before `decide` or a meeting, then reviews again.
- The card's "the paused project starts again" line asks whether answering this card alone clears the run
  (`park_reason(besides=...)`), not whether it is the only parking card.
- A re-plan could cancel a task while the run promoted or started it, and the cancelled task ran. `schedule` now takes
  the project row and `_author` the task row (`FOR NO KEY UPDATE`) before moving a task.
- The build ask takes the project row with the same `FOR NO KEY UPDATE` as `schedule`. An upgrade to `FOR UPDATE`
  deadlocked with any approval on the project, whose Grant insert holds the row's key share.
- A spent round envelope cancels what it left out, but not a task an open card holds. That task waits for its
  answer and the run parks as starved.
- A planning or tasks card says the project waits only while the project's run is live.
- Known gaps, no test:
  - On a running project, a task freed by an answer can start before that answer's re-plan revises it. The window
    is seconds to minutes. The task still reads the answer in its brief, but the re-plan can no longer cancel or
    rewrite it.
  - Answering a card at the moment a settling round merges into it can deadlock. Postgres aborts one side: the
    answer fails, or the run fails and is retried.
  - A pause during a CEO review sends the run to the gate, so on resume the CEO reviews again (one more call).
- The owner's check of the block is still open.

**Checked on a real run (0.4a, 2026-11-15).** A test project (P-4, $20) on the owner's database, real model calls.

- The Project Manager's question REQ-7 held one task (`hold_scope` tasks, T-19) while T-17 and T-18 ran to the end.
- The card showed why it asks, the line from the goal to the decision, what it holds, what answering does and what
  happens with no answer, in English and Korean.
- With `expires_at` moved into the past, the worker's next tick closed it as EXPIRED with the recommended option,
  stored as `{"by": "expiry"}`, wrote the `followed` rows, and T-19 started. The card then read "Nobody answered by
  the deadline, so the team went with the default. It's not recorded as your answer."
- A row with `context_version` 1 and no reason was refused by `ck_agent_requests_context`.
- No card from before the work path is left on the database. Two cancelled test cards (REQ-1, REQ-2) were put back into
  that shape, their links and reason set aside in `zz_set_aside_links_req12` and `zz_set_aside_req12`. Both read "Not
  recorded". `ship work-path backfill --from-calls` linked REQ-2 (one call with its title, shown as "matched from the
  Project Manager's recorded output") and left REQ-1 unknown (two calls with its title).
- Not seen on a real run: an approval that only closes at expiry. The tests cover it.

**0.4b: your-turn list, outline, path view.**

10. `your_turn`. Check: new `tests/test_your_turn.py` (run before planning before tasks; a task held by two cards is in
    neither's `frees`; a moot card sorts as `none`; roll-ups exclude CANCELLED and REPLACED).
11. The order on the Decisions and To do pages, and **By project**. Check: tsc and build, `tests/test_i18n.py`, browser.
12. `path_view`. Check: new `tests/test_path_view.py` (the focus chain never folds; at most 20 nodes by default; ids
    survive a revision; past 150 nodes it returns `too_big`).
13. `PathView.tsx`, Dagre, the Path tab, the card's link. Check: tsc and build with the bundle change noted, then the
    browser: focus lands on the card, Tab, Enter and Escape work, a screen reader reads labels in both languages,
    reduced motion, both themes, 1440, 1200 and 900. `/impeccable critique`, then `/impeccable polish`.

Exit criteria for 0.4b:

- The Decisions and To do pages order cards by what they free, matching the counts on each card.
- From any pending card, **Show on the path** opens focused on its node with its holds visible.
- A project past the node limit opens as the outline, and the outline carries every fact the graph shows.
- The owner reviews a real project's path in both languages.

**Built and checked on a real run (0.4b, 2026-11-15).** Units 10 to 13 landed. The owner could not do the shape round,
so section 6 was built as written. Where the code differs from the plan above:

- Edges run from what came first to what follows, so their words read that way: "then" between tasks, "asked" from the
  asker to the card. A fifth edge kind, `contains`, links the goal, experiment and project chain and has no visible word.
- Unfolding is the server's (`unfold=`), so the 20 and 60 limits are enforced and tested there. A group that has no room
  left says so and offers the list.
- The first fit frames the decision and its direct neighbours and never goes below a readable size (0.85). The
  direction follows the path's own width (left to right from 900px), not the window's, because the decision's panel
  takes half the window.
- Under 640px the tab shows the outline.
- A review found six defects (a layout keyed on the box count, a to-do page that could fail on a card filed between two
  reads, a shared hold worded as holding nothing, a group that could not open further, a neighbourhood that went on
  through the project, a stale order when only a hold changed). All six are fixed.
- Seen on a real project (P-5, a pending question REQ-10 holding T-23 with T-25 after it): the order line on the
  Decisions and To do pages, **By project** with "3 of 5 tasks done · 1 task held · 1 decision waiting for you",
  **Show on the path** landing on the card's box with the holds edge in sight, Tab, Enter and Escape, both languages,
  both themes, 1440, 1200, 900, and the outline at 390.
- Not seen on a real project: more than one pending card in one order, a project past the node limit, and real
  screen-reader speech (only the labels in the page were read). Every pending card rebuilds the business's whole graph
  on each refresh of the two pages; fine at today's size, not measured on a large one.
- The owner's review of a real path is still open.

### Proposed ROADMAP.md edit

Replace the 0.4 row of the 1.0 table (`docs/ROADMAP.md:27`) with two rows and move each later row down one number. 0.9
stays, so the old 0.7 fills the empty 0.8:

```
| 0.4a | 일의 경로와 결정 맥락: 기록과 카드 | 새 결정은 어디서 나왔는지, 무엇을 막는지, 답하면 무엇이 일어나는지, 답이 없으면 어떻게 되는지가 없으면 만들어지지 않는다(DB 검사). 결정 카드에 묻는 이유, 목표 > 실험 > 프로젝트 > 업무 > 이 결정 줄, 막고 있는 일, 답한 뒤, 답이 없을 때가 보인다. 예전 카드는 기록된 것만 보이고 나머지는 "기록 없음"이다. 만료가 실제로 적용된다 |
| 0.4b | 일의 경로와 결정 맥락: 순서와 경로 보기 | 결정 목록이 각 결정이 풀어 주는 일의 순서로 놓이고, 프로젝트별 개요에 진행률이 보인다. 카드의 "경로에서 보기"가 React Flow 경로를 그 결정에 맞춰 열고, 키보드와 화면 낭독기로 쓸 수 있고, 너무 크면 개요로 열린다 |
```

The rows now numbered 0.4 (추적), 0.5 (첫 10명 키트), 0.6 (글로벌 채널 정리) and 0.7 (안전과 설치) become 0.5 to 0.8 with
their conditions unchanged.

Rule 7 allows one open step and one shaped next step. 0.2 is open and 0.3 is next, so 0.4a and 0.4b stay one-line rows
until 0.3 closes, unless the owner moves them.

## 9. Design alternatives considered

Three whole shapes were designed in parallel (relations as rows, holds that live on the work, one graph module) and a
second model judged them. The base is relations as rows. From the second come the park predicate, the stranded-wait
sweep, required refs and the call-site test; from the third, the round row, `known`, flat nodes and keeping `blocking`.

| Question | Options | Choice | Why |
|---|---|---|---|
| Where relations live | JSONB in `action_params`; a column per relation; a link table; a holds table on the work side; an edge table under a graph module | `request_links`, four `rel` values, allowed-pairs `CHECK` | One writer, indexed both ways. JSONB has no reverse index, and columns break on a card with two askers |
| What "blocks" means | task ids listed on the request; hold rows read through a liveness join | Hold rows, live join | A cancelled or replaced task drops out with no sync write |
| `blocking` | drop it and move 52 readers; keep it written; generate it from `hold_scope` | Generated column | One source, no reader moves, no writer can set it |
| CEO round identity | the round number; a row unique on `(project, kind, round)`; a row ordered by `seq` | `review_rounds` by `seq` | A review can run twice in one round |
| Who names the origin | the agent; the call site | The call site, required keyword | The server knows it, and an agent can be wrong or hostile |
| What a task agent may hold | any task key; itself, its dependents, planning | Itself and server-resolved dependents | It sees only its own key (`context.py:232-235`) |
| "After you answer" | stored sentences; declared keys plus `_effect` plus `followed` rows | Keys that `respond` executes | A sentence can promise what the code won't do |
| Expiry | dropped from holds at the deadline; written inside a read; a sweep plus the gate | Sweep plus gate | The default must be recorded before the work it frees goes on, and a read must not write |
| Read models | a query per view; one graph per business | One graph, three projections | Card, list and graph show the same counts |
| Layout | hand layout as `OrgChart.tsx`; ELK; Dagre with sub-flows; Dagre with flat nodes | Dagre, flat nodes, folded groups | ELK is about 1.46MB and React Flow advises against it; sub-flows break on outside links |
| Old CEO cards | infer from timing; leave unknown; exact match on `output.json` | Unknown, with the match as an opt-in command | Open question 3 |

## 10. Open questions for the owner

**Answered by the owner on 2026-11-13: yes to all three, as recommended below.** The decisions are part of the design.

1. **When a card holds only some tasks, may the other tasks keep running?** Recommendation: yes, as the last unit of
   0.4a. The card then says exactly what waits, and a question about one feature no longer stops research that doesn't
   need it. Until you say yes, any open hold parks the whole project, and the card says so.
2. **May an unanswered question go ahead with its default?** Recommendation: yes for questions only, with the default
   fixed to the recommended option and shown on the card from the start with its date, a Slack notice a day before, and
   the answer recorded as "default on expiry", never as yours. Approvals, spending and anything sent outside only close.
3. **Link old CEO cards to the call that wrote them?** Recommendation: yes, with the separate `--from-calls` command,
   after you read the audit. It uses only the call's recorded output and an identical title, accepts a single match, and
   labels the result "matched". REQ-6 would then say whether a plan or a review raised it.
