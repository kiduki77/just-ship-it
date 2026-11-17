# Staffing structure: a team assembled for each job

Status: design, no code, grounded in `main` 4868556. Roadmap 0.2c and 0.2d. Two independent designs were drawn from the
same verified facts (one on opus, one on sonnet) and merged. Two adversaries (opus and sonnet) probed that merge against
the tree, and the fixes are in this version. The owner then decided four open points (section 1), which changed the
scope: the old data may be erased, builds are recorded too, the CEO is replaced by two roles, and placement must learn.
A second adversarial pass on those four changes proved seven problems, and this version fixes them.

## Why

Today the product shows a standing company. `config/agents.yaml` lists 12 roles with titles and departments,
`OrgChart.tsx` draws owner, CEO, departments and agents, and every task runs the same path: one author call, then one
call by a fixed reviewer. The owner expects a chart of AI employees to read as a copy of Paperclip, which went viral, so
the shape itself is a positioning risk. The single path also wastes calls, because a simple research task still gets an
opus review.

The owner wants a staffing agency. A Project Manager says what a job needs. A Staffing Agency Manager places the people,
records why, and lets the team dissolve when the job ends. Placement improves as results accumulate. When the owner
later asks "why did you do this?", the product calls the role back, and the role answers from what was recorded or says
there is no record.

## 1. Decisions

| Point | Decision | What it changes |
|---|---|---|
| Sequential and parallel | Not chosen inside one task. They show as relations the plan made. Placement must improve as data accumulates. | Section 7 adds a learning loop. Section 5 records what the loop needs. |
| Old data | May be erased, so this step is built whole. | No legacy path anywhere. Recall covers tasks and builds from the start. The migration refuses to run on old rows (section 11). |
| CEO | Replaced by a Project Manager and a Staffing Agency Manager. | Section 2. The rename is part of this step. |
| Names | Team for this job, Roster, `/crews`. | Section 10. |

The CEO and the "mission" wording from the first draft are both gone. A task keeps the name task (T-n) and a build keeps
the name build (J-n). "Mission" is not used, because it already means the company's purpose (`comp["mission"]`).

## 2. Two roles instead of the CEO

Today `ceo` is the only coordinating model role. One grep finds 17 `run_agent("ceo", ...)` calls in 9 files, and unit 0 counts the other forms.

| What the CEO does today | Where | New owner |
|---|---|---|
| Plans a project into tasks, replans, reviews results, decides after a meeting, writes the report | `company_graph.py:137`, `:751`, `:861`, `:965`, `:984`, `:1029`, `realign.py:116` | Project Manager |
| Runs a business kickoff, triages ideas, plans cycles and stage summaries, retro, KPI and weekly review, infers the customer description, writes door rules | `kickoff.py:919`, `ideas.py:593`, `cycle_ceo.py:142`, `:176`, `:197`, `bet_retro.py:418`, `jobs.py:118`, `:149`, `profile/infer.py:298`, `door_rules.py:121` | Project Manager |
| Picks the owner role of each task from the roster | the plan prompt, `tasks._assignable_owners` | Project Manager asks for a role. Staffing Agency Manager places it. |
| Picks the reviewer of each task | `independence.reviewer_for` | Staffing Agency Manager |
| Decides nothing about who works on a task or why | nowhere | Staffing Agency Manager |

**Project Manager** is the renamed `ceo`: the same opus role and prompt, id `project_manager`. It keeps every call above,
including the business-scale ones. The name fits kickoff and cycles loosely, and the owner chose two roles, so this
design keeps one coordinating model role.

**Staffing Agency Manager** is not a model. It is the `staffing` package, a pure rule that returns a placement for each
task and the owner of the records and the statistics behind it. The crews screen shows it by name ("placed by the
Staffing Agency Manager: nothing waits on this task, so one role did it"). It makes no calls, so it never appears in
the ledger as an agent and is not listed in `agents.yaml`. It becomes a model role only if the learning loop (section 7)
shows a decision a rule cannot make, and the data decides that.

The plan hands the Project Manager's request to the Staffing Agency Manager through the task:

```python
class TaskDraft(BaseModel):
	owner: str                 # the role the Project Manager asks for, as today
	family: str | None = None  # new, optional: one of the task families in config/staffing.yaml
```

Version 1 keeps the requested role and the role's configured model. `family` is what the learning loop groups by. Only
the Project Manager's plan prompt asks for it. `TaskDraft` is also built when the owner adds a task (`control.py:386`),
when an idea becomes a task (`ideas.py:475`), and as the type of every author's `proposed_follow_up_tasks`
(`contracts/work.py:127`), so the field cannot be required. The server checks the value against the family list and
stores `other` with a warning when it is missing or unknown, the way an unknown owner falls back to `research`
(`tasks.py:108-112`). A task from one of the other paths gets the default family of its owner role.

The id rename from `ceo` to `project_manager` is large. A grep for the quoted string finds 26 backend files and 72 test
files. The string is also a stored value or a literal the models write, not only an agent key: `contracts/door.py:12`
(`decider: Literal["owner", "ceo"]`), `policy.DECIDERS`, `roadmap.py:122`, `profile/store.py:22` (`Writer`), `bets.origin`
(`cycle.py:286`, `Bets.tsx:35`), `requests.by_ceo`, and the `Who` type in `Approvals.tsx`. It is also special-cased in
`gate.py` rules, `chat_tags._ceo`, `chat_tags.allowed()` (escalate and handoff), `tasks._assignable_owners` (`k != "ceo"`),
`prompts/chat_reply.md` (`to: "ceo"`) and `prompts/door_rules.md`. Old rows would carry the old id, so the rename depends on
the data reset in section 11. It is done by a codemod, not by hand: a script renames the literal across `backend`,
`config`, `prompts`, `web/src` and `tests`, lists each special case above for a human check, and is rerun until the grep
is empty. The rename is the first unit of step 0.2d, so step 0.2c does not wait on it.

## 3. Diagnosis

| Fact | Where it comes from |
|---|---|
| Every business task gets one author call and one review call. "No review" does not exist. | `company_graph.work` (`company_graph.py:497`, review at `:532`) |
| The reviewer is a hardcoded function: `risk` reviews everyone, and `strategy` reviews `risk`. Three places that have a task and one roster-wide list call it. | `independence.reviewer_for` (`independence.py:36`), callers `company_graph.py:532`, `console.py:805`, `stops.py:106`, `work_now.py:80`, and `console.py:877` over the whole roster |
| Independence is judged by role name only, never by model. | `independence.assess` (`independence.py:28`) |
| `tasks.review` is overwritten on every iteration, so earlier reviews survive only as ledger rows without text. | `company_graph.py:585` |
| The ledger has no prompt or output columns. Full text lives in `var/calls/<day>/<call_id>/` and is deleted after 30 days. | `LLMCallRecord` (`db/models.py:394`), `retention.prune_calls`, `config/company.yaml:156` |
| Nothing records how a task was staffed or why. Cost, calls and time can be summed from `llm_calls` by `task_id` or `dev_job_id`. | `db/models.py:367-420` |
| A build keeps the engineer's summary and plan decisions in `DevJob.steps`, the reviewer's verdict and summary in a step (the findings list is not kept), and the product role's acceptance output in `DevJob.result`. | `worker.py:1067`, `:1098`, `:1173`, `:1463`, `contracts/handoff.py` (`CodeReviewOut`, `AcceptanceOut`) |
| The org chart hierarchy is hardcoded in `layout()`. `GET /org` has no job, composition, reason, cost or result. A test reads per-business agent usage from it. | `OrgChart.tsx:141`, `api/app.py:382`, `_REVIEW_LINKS` at `:373`, `tests/test_business_scope.py:346-347` |
| Chat has `task` and `dev_job` subjects and `chat.send` accepts any configured role as the recipient. `about` is accepted only as a `walkthrough_step` on a build thread. A task thread gets only a title line as context. | `chat_tags.py:153`, `chat.py:173-196`, `chat_reply._subject_records` (`chat_reply.py:101`) |
| "CEO" appears on 205 lines in 18 console catalog files and on 356 lines in 69 server files. | `grep -rn CEO web/src/i18n`, `grep -rn CEO backend --include=*.py` |
| `DEPARTMENTS` and the `department` field are used well beyond the org chart: the chat system prompt (`chat_reply.py:45`), the chat sender label (`web/src/chat/model.ts:116`), role eligibility (`tasks.py:62`, `context.py:48`, `factory.py:104`), and `tests/test_agent_names.py:54`. | grep at `main` |
| The migration head is `d4c8e1f6a2b3` (step 0.3). The repository has no command that resets the database. | `db/migrations/versions/`, `cli.py` |

## 4. Usage

### 4.1 What the owner sees

The crews screen at `/crews` replaces `/org`. It lists projects, and under each project one row per task and per build.

```
P-3 Price research            Project Manager
  T-12 Competitor price list     alone, after T-11      market_research (sonnet)
       Placed by the Staffing Agency Manager: nothing waits on it and it has one criterion.
       1 call, 2m 10s, $0.41      Result: done, no review       [Ask market_research why]
  T-14 Price hypothesis check    checked, after T-12    strategy, checked by risk
       Placed by the Staffing Agency Manager: the build spec reads this result.
       3 calls (1 rework), 9m, $2.10     Result: review passed  [Ask strategy why]
  J-7  Build                     engineer, code review, product check (fixed order)
       6 calls, 41m, $5.80        Result: waiting to merge       [Ask engineer why]
```

"Ask" opens team chat on the task or the build with that role as the recipient. A reply carries a standing label.

```
market_research (rebuilt from records, not the same memory)
  Based on [R2] and [R5], I listed three competitors because ...
  When the records do not say: No record. The records I have are R1 to R6.
```

### 4.2 Call sites

```python
# company_graph.work, first lines. work() holds no row lock, so the unique constraint is what keeps one crew per task
crew = staffing.ensure(s, t)  # places the task by the rule if no crew exists, otherwise returns it

# _author, in the same transaction that writes t.result
staffing.record_turn(s, crew, staffing.AuthorTurn(iteration=i, call_id=last_call_id(), fixing=feedback, ...))

# after the author pass
if (slot := staffing.verifier(crew)) is None:
	crew = staffing.settle_solo(s, crew, t)  # adds a checker when the result asks the owner something
	if staffing.verifier(crew) is None:
		staffing.enqueue_audit(s, crew, t)  # on a sample of alone tasks, queues a job that runs the checker later
		return _finish_unchecked(s, p, t, lang)
reviewer = staffing.verifier(crew).role  # replaces reviewer_for(owner)

# console.py:805, stops.py:106 and work_now.py:80, which each have a task
who = staffing.verifier_role(s, t)
# console.py:877 asks "whom does this role review" over the whole roster, with no task. That list is deleted with the
# department view, so it has no replacement.
```

```python
# chat_reply.run, when an owner line carries about={"kind": "recall", ...}
bundle = staffing.recall_bundle(s, subject, about["role"])
out = run_agent(agent, PURPOSE, bundle.prompt(asked, history), RecallReplyOut, runtime="isolated", tools=[],
                system_override=bundle.system(agent))
reply, recall_data = bundle.check(out, lang)  # an answer with no valid citation becomes "no record"
```

## 5. Data model

| State | Where | Single writer |
|---|---|---|
| The crew decision: slots, family, reasons, the facts the rule read, later additions | new table `crews`, one row per task or build | `staffing.ledger` |
| What each role produced in each iteration, the review text, and shadow checks | new table `crew_turns`, append only | `staffing.ledger` |
| Calls, time, cost, failed calls | `llm_calls` summed by `task_id` or `dev_job_id` (existing) | `agents.executor` (unchanged) |
| Verdict, start and end time | `tasks.review`, `started_at`, `finished_at` (existing) | `company_graph` (unchanged) |
| Whether the crew is dissolved | derived from the subject's terminal status | nobody |
| The labels "alone", "checked", "after", "alongside" | derived from slots and facts | nobody |
| Placement statistics (section 7) | a query over `crews`, `crew_turns` and `llm_calls`, not a table | nobody |

A build is a crew too. Its slots are the fixed chain (engineer, `eng_reviewer`, product acceptance), and its reason is
`fixed.build`. A crew is made when the subject starts work, so a task cancelled before it starts has none, and the crews
screen shows it as "cancelled before it started" without a team. Every subject that did work has a crew, so the crews
screen and recall each read one table. No column on `tasks` or `dev_jobs` links to a crew, because `create_from_drafts`
makes tasks before any crew exists. The package owns the two tables. Code outside imports only `staffing/__init__.py`, and a test fails if it imports an inner file.

```python
# staffing/crew.py: pure, no database
class SubjectKind(StrEnum): TASK = "task"; BUILD = "dev_job"
class Duty(StrEnum): AUTHOR = "author"; VERIFIER = "verifier"
class Flow(StrEnum): ALONE = "alone"; AFTER = "after"; ALONGSIDE = "alongside"

@dataclass(frozen=True)
class Slot: duty: Duty; role: str; model: str | None  # model is recorded in version 1, chosen in version 2

@dataclass(frozen=True)
class JobFacts:  # stored in crews.facts, so an old decision can be explained after the rule changes
	owner: str; family: str; criteria: int; dependents: int; after: tuple[int, ...]; alongside: tuple[int, ...]
	feeds_build: bool; origin: str; analysed: bool
	resumed: Literal["fresh", "in_review", "reworked"]

@dataclass(frozen=True)
class Reason: rule: str; facts: dict[str, int | str]  # rule is also the catalog key staffing.why.<rule>

@dataclass(frozen=True)
class Placement: slots: tuple[Slot, ...]; why: tuple[Reason, ...]; audit: bool  # audit: run a shadow check afterwards

RULES: tuple[Rule, ...]  # the one list of rules; a test walks it and requires both languages for each key
def place(f: JobFacts, limits: Limits, history: History | None = None) -> Placement: ...  # pure, no model call
def verifier_role(owner: str) -> str: ...  # the old reviewer_for, now the only copy; an unknown role gets risk
def fixed_path(owner: str) -> tuple[Slot, ...]: ...  # what the old path would have used, recorded for comparison only
```

```python
# db/models.py
class Crew(Base):  # unique (subject_kind, subject_id), which makes ensure() idempotent
	subject_kind: str; subject_id: int; family: str
	slots: JSONB; facts: JSONB; why: JSONB; amendments: JSONB (append only)
	rules_version: str; assembled_at: datetime
class CrewTurn(Base):  # unique (crew_id, call_id): one turn per ledger call, so recording a call twice leaves one row
	crew_id: FK; call_id: str; duty: str; role: str; round: int; iteration: int; shadow: bool; record: JSONB; at: datetime
```

```python
# staffing/turns.py: validated at the boundary, trusted inside
class AuthorTurn(BaseModel): iteration; call_id; fixing  # the rework or send-back text this pass answered
	summary; findings_markdown; assumptions; risks; open_questions; unmet_criteria; evidence_ids
class VerifierTurn(BaseModel): iteration; call_id; review: ReviewOut; independence: str  # whole review, every iteration
class BuildTurn(BaseModel): role; round; iteration; call_id; summary; decisions; commit  # engineer pass, plan decisions
class BuildReviewTurn(BaseModel): round; iteration; call_id; review: CodeReviewOut  # the whole findings list
class AcceptanceTurn(BaseModel): round; call_id; acceptance: AcceptanceOut  # criteria results, design findings, notes
```

`ensure` inserts with `ON CONFLICT DO NOTHING` and rereads, so a retry, a checkpoint replay and a concurrent call all
leave one row. `amend` only adds a slot and never removes one.

The turn key is the call id because a build repeats iteration numbers. `test_and_review` loops `range(max_fix + 1)` from
0 on every call (`worker.py:1112`), and it is called on the first run (`:1106`), on a resume (`:1030`) and on every fix
round (`revise.py:394`). Each iteration also makes two engineer calls (the build and the fix), and acceptance runs after
every delivery. `round` is the fix round (0 for the first run and for every task) and `iteration` is the loop counter. Both
are descriptive columns. A turn is recorded in the transaction that records its call's result. Because a build's steps
already live in `DevJob.steps` and `result`, a turn keeps what those do not: the whole findings list, the plan decisions
and the acceptance output. It does not copy the steps, so one fact has one writer.

## 6. The rule, version 1

The rule runs when `work` first sees the task, not when the task is created. All six task-creation paths go through
`tasks.create_from_drafts`, but a task can also arrive in the middle of a run, and one place handles both. A build gets
its crew from `fixed.build` when the build starts. A forcing line adds the checker to a task.

| Rule id | Holds when | Result |
|---|---|---|
| `check.feeds_build` | the build spec reads this result | checked |
| `check.analysed` | the owner's runtime is `analysis` | checked |
| `check.numbers` | the owner is `finance` | checked |
| `check.reviewer_role` | the owner is `risk` | checked, by `strategy` |
| `check.resumed` | the task was already authored or in rework | checked |
| `check.decision` | it came from a meeting decision | checked |
| `check.owner_added` | the owner added it | checked |
| `check.has_dependents` | another task waits on it | checked |
| `check.criteria` | criteria exceed `staffing.solo_max_criteria` | checked |
| `check.config` | `staffing.always_check` is true | checked |
| `solo.small_leaf` | none of the above | alone |
| `fixed.build` | the subject is a build | fixed chain |

`solo_max_criteria` starts at 1. A task that is alone and ends with a card for the owner is upgraded by `settle_solo`.
The card contract says a card reaches the owner only after an independent review (`contracts/work.py:128`), and that
promise stays.

The checker is chosen by `verifier_role`, the moved `reviewer_for`. `assess` is unchanged and still judges independence
by role. `verifier_role` keeps today's behavior for an unknown role, which `tests/test_independence.py` pins.

`dependents` is counted when `work` first sees the task. A follow-up, realign or decision can add a task later that
depends on one that already finished alone, so the stored `dependents` can be stale. The rule is not rerun. Instead
`context.dependency_outputs` tells the dependent task's author that the input was not reviewed.

### 6.1 The alone path

A task with no checker finishes after one author call, a server-side source check (`evidence.verify_sources`, no model
call) and no review. Its `t.review` becomes `{"verdict": "unchecked", "reviewer": null, "outcome": "pass" | "partial" |
"unmet", "criteria_unmet": <the author's unmet_criteria>}`. The verdict differs from `unreviewed`, which means a review
was due and failed. The outcome and the unmet list have their own fields because `results_overview` (`context.py:280`)
is the only place the Project Manager's review, the meeting, the decision and the report learn that a task finished
partly, and it prints `Review: {verdict} / unmet: {criteria_unmet}`. Without them an alone task that missed a criterion would read as
"unchecked, nothing unmet". Evidence from an unchecked task is never promoted to `verified_fact`, and the report's
evidence line shows the cost of that. The task finishes as `PASSED` when the author listed no unmet criterion, as
`PARTIAL` when it met some criteria and missed others, and fails as `UNMET` when it met none (it listed as many unmet
criteria as the task has). A checked task whose review meets nothing fails the same way (`review_outcome`), so the
owner's retry and the lists of failed work see an alone one too.

Every reader of `t.review` must handle a task with no reviewer. A probe of the current tree shows
`_write_task_artifact` renders "Reviewed by : (unchecked word)" and the catalog has no
`work.doc.task.verdict.unchecked`. The readers found by grep are `context.py:280` and `:299`, `console.py:297`, `:752`
and `:869`, `capability/factory.py:180`, `cli.py:309`, `company_graph.py:789`, the report and `review_outcome`. The
artifact writer skips the review line when there is no reviewer and writes a "not reviewed" line from the new key.
`stops.py` treats the reviewer as a role, so its `_REVIEWER_NEXT` branch needs a test for a task with no checker.

`rounds.admit` sets its room from `per_task = total / n` over the project's tasks that spent money (`rounds.py:60-80`).
Cheap alone tasks pull that mean down, so the envelope would widen by itself. The mean changes to cover only tasks that
have a checker, so an alone task never changes how many tasks are admitted.

## 7. Placement that learns

The owner wants placement to improve as results accumulate. The first design could not do that, because an alone task
has no verdict and a checked task has one, so their pass rates are not comparable. This section fixes that and says how
version 2 of the rule uses the data. Version 2 is step 0.2d. Version 1 records what version 2 needs.

**What is recorded.** Every crew stores its `family`, its `facts` (criteria count, dependents, whether it feeds a build,
origin) and its slots. Outcomes come from the ledger and the turns: calls, rework count, wall time, the checker's
verdict, and what happens downstream, such as a follow-up task, a contested claim or an owner decision card.
Facts are stored because difficulty differs between tasks, and a pass rate on easy tasks says nothing about hard ones.

**A fair comparison.** On a sample of alone tasks (`staffing.audit_rate`, starting at 0.2), the server queues a job named
`audit_task` through the existing job worker. The job runs the checker from `verifier_role` on the finished task and
stores the result as a `shadow` turn. It runs outside `work()`, because inside it the graph's step barrier would hold the
next `schedule` step and make dependent tasks wait. It does not call `apply_review`, so it gates nothing, promotes no
evidence and never changes `t.review`. The owner never sees it as a decision. Over time each family has a measured rate
at which the checker would have found unmet criteria in alone work, next to the rate for tasks that were checked. That is
the number that says whether a family is safe to run alone.

The audit call uses a new ledger purpose, `audit`, and is paid for separately. `rounds.envelope` sums the round's task
spend and puts other calls in `elsewhere`, and `budget.reserve` checks `task_cap` per task, so an audit that charged a
task would shrink the room of the round and could trip the cap of the task it audits. The audit therefore reserves
against a project-level audit budget (`staffing.audit_budget_usd`), is excluded from the envelope, and has no `task_id`
reserve check. `Outcome` reports audit calls and cost on their own line and leaves them out of the task's calls and cost,
so an alone task still shows one call. A test fixes both numbers.

**What version 2 does.** `place` receives `history`, the statistics for the task's family, role and model. It uses them
in two ways.

- Alone or checked. A family may run alone when it has at least `min_samples` shadow checks and the shadow unmet rate is
  below `solo_max_unmet_rate`. A family that has not met `min_samples` keeps the version 1 rule. A family whose rate rises
  returns to checked, and the reason code says which.
- Model. When a role has run on two models for the same family with at least `min_samples` each, the cheaper one is
  chosen if its outcome rate is within `model_tolerance` of the better one. Until then the role's configured model runs,
  and the other model gets a sample at `explore_rate` on tasks that no forcing rule covers.

A forcing rule (`check.feeds_build`, `check.numbers`, a card for the owner) is never overridden by history. History can
only remove an optional check or lower a model, and every such decision records the statistics it used.

**Limits.** A single owner produces few tasks per family, so `min_samples` will take weeks to reach for common families
and may never be reached for rare ones. Those families stay on version 1, which is the intended failure. Cost is counted
in calls and in how much of the subscription a call uses, not in dollars. Internal pass rates do not measure business
results, so the statistics stay separate from the customer and revenue outcomes the cycle tracks. The thresholds are
settings, not constants, and the first values are guesses until a real project run shows the spread.

## 8. Recall

The owner clicks "Ask" on a row of the crews screen. Chat opens on the task or build subject with that role as the
recipient, and the owner's line carries `about = {"kind": "recall", "role": <role>}`. `chat.send` already accepts any
configured role, and `_checked_about` is extended to accept `recall` when the subject has a crew and the role has at least
one recorded turn. The chat-thread `CHECK` constraint and its migration are not touched.

The recall is a mode of one line, not of the thread. A build thread keeps its ordinary behavior for the owner's other
lines, including a request to start a fix round, which is how the merge card works today. A recalled line is answered by
`recall_bundle` and can only answer.

Today a thread's unanswered lines to one agent are batched into a single reply job. `enqueue_reply` keys the job on the
thread, the agent and `answered_seq`, `unanswered()` returns every owner line since the agent's last reply, and `apply`
marks them all answered (`chat.py:116-147`). A recall line and a "fix X" line sent to the engineer would join one call, and
one of them would take the wrong path. So recall lines are separate. `unanswered()` for the ordinary job leaves out lines
whose `about.kind` is `recall`. Each recall line gets its own job, keyed on the line's sequence number, and its own answered
marker, so neither kind of reply can mark the other kind's lines answered. `chat_tags.allowed()` gets a `recall` argument
and returns true only for `answer` when it is set, which also blocks the `work` intent inside a build thread.

The bundle is a numbered list of records and nothing else.

| Record | Source |
|---|---|
| assignment | the task's objective and criteria, or the build's spec title and criteria, with status |
| crew | `crews.slots`, `why`, `facts`, `amendments`, and the statistics used when version 2 placed it |
| turn | `crew_turns` for every iteration, including `fixing`, the whole review, a build's decisions and acceptance output |
| evidence | `evidence` rows of the task with verification status |
| request | `agent_requests` the subject raised |
| ledger | `llm_calls` rows: role, purpose, attempt, model, status, duration, error |
| not kept | a line naming what has no record, such as a prompt or a code diff |

The model sees the role's own prompt, these records fenced as data through `services/fence.py`, and the question. It
cannot read `var/calls`, so an answer is the same on day 3 and day 300. A test pins the bundle byte for byte across
`prune_calls`. A recalled engineer does not read the branch. The commits and the summary of each round are records, and
the code itself is not.

The model returns `RecallReplyOut{reply, cites, no_record}`. The server checks it. If `no_record` is false and `cites`
is empty or names an unknown id, the server replaces the reply with the catalog sentence "No record". The server cannot
check that a cited record supports the words of the reply, and the screen says so by showing each citation as a chip that
opens the record, so the owner can check it. A subject still running can be recalled. Its bundle says it is in progress.

Each reply line carries `data["recall"] = {rebuilt, cites, no_record, in_progress}`. `chat.view()` (`chat.py:289`) does
not return `data` today, so it is extended. The console renders "rebuilt from records, not the same memory" on the thread
header and on each line.

## 9. The crews screen

`GET /crews?project_id|business_id|state` returns `CrewView` rows. Each has the members who worked (from the ledger), the
slots that did not, the shape labels, the reasons as `{rule, facts}` rendered in the owner's language, the placing
statistics when version 2 used them, and an `Outcome` (calls, failed calls, cost, busy time, wall time, verdict,
`fixed_path_calls`). It reads one table, `crews`, plus `llm_calls`. A build reads the same way as a task. No row is
derived or marked legacy. A task cancelled before it started has no crew and shows as "cancelled before it started".

`/org` redirects to `/crews`, and the query parameters carry over (`?project=` becomes `project_id`, `?scope=business`
becomes `business_id`). `Org.tsx`, `OrgChart.tsx`, `GET /org` and `_REVIEW_LINKS` are deleted in the same change,
because two ways to show a team is the failure this design is removing. React Flow stays, since 0.4b uses it.
`Agents.tsx` stays as the roster and loses the "who reviews whom" list. It keeps the department as a plain label, because
`department` also drives role eligibility and the chat sender label.

Places that point at `/org` and move: `Project.tsx:105` and `:126`, `Agents.tsx:126`, `nav.tsx:185` and the breadcrumb at
`:477`, and the command palette at `shell.tsx:20`.

The chat page ref `org` becomes `crews` in `chat_tags.PAGES`, `subject.tsx` and `tests/test_chat_routes.py`. With the data
reset there is no thread to migrate. `routes.ts` keeps an entry for `/org` that maps to the same subject as `/crews`,
because `test_every_route_has_a_chat_subject` fails when `main.tsx` declares a `/org` route that `routes.ts` does not
list. The per-business usage check in `tests/test_business_scope.py` moves to the new read, which carries per-role usage.
The screen is a new layout, so `/impeccable shape` runs before code.

## 10. Vocabulary

| Term id | en | ko | Retires |
|---|---|---|---|
| `project_manager` | Project Manager | 프로젝트 매니저 | CEO, AI CEO |
| `staffing_manager` | Staffing Agency Manager | 인력 파견 매니저 | nothing |
| `crew` | Team for this job | 꾸린 팀 | org chart, 조직도 |
| `roster` | Roster | 인력풀 | nothing |

"Agent" stays and its definition changes to "a role on the roster that is called in per job". The four shape labels are
screen copy, not glossary terms. A retired word is added in the same commit that replaces its uses, because
`tests/test_i18n.py` fails on any catalog that still contains it. "CEO" is retired by the rename series: console catalogs
by area, then server sentences, then the role prompts ("the CEO" becomes "the Project Manager"), then the retired entry.
The output-language rule built from the same table (`prompting.py:47`) then tells the models not to say "CEO".
The 0.3 catalogs say "CEO 짐작" for a guessed value, which becomes a word that does not name a role.

A probe that retired "team member" and "department" failed the test on 3 or 4 console keys for "department", 7 for "team
member" (including the merge card's `decide.mr.built_by` and `decide.mr.checked_by`) and one server sentence. Those sit
in chat, the merge card and the roster, which this step does not rewrite. So "team member" and "department" are not
retired here, and `DEPARTMENTS` and the `department` field stay.

## 11. Migration and reset

The owner has said the existing data may be erased. This step relies on that and has no path for old rows: no legacy
derivation, no partial records, no backfill. The repository has no reset command, so the owner resets the database (the
Postgres volume and `var/`) before the migration runs. Nothing in this step deletes data. The migration enforces the
contract. Revision A adds `crews` and `crew_turns`, and it aborts with a plain message if `tasks` or `dev_jobs` already
has rows, because readers assume that every subject that did work has a crew. Otherwise an old task would break
`GET /crews` and recall.

The guard changes an existing test. `tests/test_migrations.py` (around line 88) upgrades to `b4e9c2a7d815`, commits a
`Task`, and upgrades to head. Unit 2 makes that test stop at the revision before A, and adds one test that A refuses a
database with a task in it and one that it runs on an empty database.

Confirm the head first (`d4c8e1f6a2b3` at design time). Stop the worker, run `alembic upgrade head`, and read the
autogenerate output for dropped LangGraph checkpoint tables before starting it again. The `ceo` to `project_manager` rename
needs no data change because there is no data. The role config, prompts and catalogs change in code.

Rollout puts recording before behavior. With `staffing.always_check: true` the rule always adds a checker, so behavior
matches today and only records accumulate. The unit that turns on alone tasks flips it to false.

## 12. Steps and units

The scope no longer fits one box of one to two weeks, so it is cut into steps. Rule 2 of the roadmap fixes the time and
varies the scope.

- **0.2c, rule and records.** Units 0 to 7. The rule, the records for tasks and builds, alone tasks with shadow audits,
  and `GET /crews`. The Staffing Agency Manager exists here as the `staffing` package.
- **0.2d, roles, screen and recall.** Units 8 to 12. The `ceo` id rename, the crews screen, the vocabulary terms, recall and
  its owner check.
- **0.2e, placement learns.** Unit 13. Version 2 of the rule reads the statistics. It starts when enough samples exist.

Units 2 and 6 touch migrations, so `pstack:interrogate` or a second model reviews them (CLAUDE.md). Unit 12 puts recorded
text into a model prompt, so `/security-review` runs after it. The four changes the owner made after the first review
(section 1) get their own adversarial pass before unit 1 starts.

| Unit | Content | Done when |
|---|---|---|
| 0 | Confirm the migration head, list every reader of `t.review` against section 6.1, count `ceo` ids and "CEO" lines, define the task family list in `config/staffing.yaml` with the owner | the list and counts are in this document and the family list is agreed |
| 1 | `staffing/crew.py`: types, `RULES`, `place`, `verifier_role`, `fixed_path` | table tests per rule, both-language keys for every rule id, the unknown-role case |
| 2 | Revision A, models, `ensure`, `amend`, `record_turn`, `turns.py`, the reset guard, `tests/test_migrations.py` stopped at the revision before A | `ensure` twice leaves one row, a call recorded twice leaves one turn, `amend` only adds, migration up and down, A refuses a database with a task and runs on an empty one |
| 3 | `TaskDraft.family` (optional), the plan prompt, boundary validation with the `other` fallback and the owner-role default | an unknown or missing family becomes `other` with a warning, a valid one is stored in `facts`, the owner-add and idea paths still build a `TaskDraft` |
| 4 | Connect `work` and `_author`, move `console.py:805`, `stops.py:106` and `work_now.py:80` to `verifier_role`, delete the `console.py:877` list, delete `reviewer_for`, `always_check: true` | `tests/test_independence.py` moved to `verifier_role` and still pinning the same cases, the other graph tests unchanged, one crew row and one turn per iteration, the same review call count as before, a `_REVIEWER_NEXT` test |
| 5 | `always_check: false`, `settle_solo`, the `unchecked` verdict, the `audit_task` job and the `audit` purpose, `staffing.audit_budget_usd`, `_write_task_artifact`, the catalog key, every reader from section 6.1, `rounds.per_task` over checked tasks only, the "input not reviewed" note | a small leaf task finishes in one call, an alone task that misses some criteria reads as partial in the Project Manager's review and the report and one that meets none fails, a task with a card gets a checker, an audit gates nothing and promotes no evidence and runs after dependents may have started, an audit leaves the round's admitted count, the task's cap and the task's call count unchanged, a real small project run with the call counts read from the ledger |
| 6 | Build crews and turns: the build's crew at start, `BuildTurn`, `BuildReviewTurn` (the whole findings list), `AcceptanceTurn`, keyed by call id and `round`, recorded where `worker.py` writes steps today | a build run leaves one crew and the turns of each round, a fix round and a resume add turns without dropping any, the two engineer calls of one iteration both appear, acceptance after a second delivery adds a turn |
| 7 | `Outcome`, `CrewView`, `views`, `GET /crews` with per-role usage | calls and cost equal the `llm_calls` sums with audits on their own line, builds read like tasks, a task cancelled before it started has no crew and says so, `tests/test_business_scope.py` moved off `/org` |
| 8 | The `ceo` to `project_manager` rename by codemod: `agents.yaml`, prompts, contracts, the stored literals and special cases in section 2, `activity.py`, chat defaults, tests, web types | the codemod's grep is empty outside history, the special-case list is reviewed by a human, the existing suite passes with the new id |
| 9 | `/impeccable shape`, `Crews.tsx`, redirect with query parameters, every `/org` link in section 9, the chat page ref, `routes.ts` keeps `/org`, delete `Org.tsx`, `OrgChart.tsx`, `GET /org` | `npm run build`, `tests/test_chat_routes.py` including `test_every_route_has_a_chat_subject`, the real screen at http://127.0.0.1:8000/crews in both languages |
| 10 | The four terms, the agent definition, `term.ts` regenerated, `PRODUCT.md` rows, the catalog rename series for "CEO", "org chart" and "조직도" retired in the commits that remove their uses | `tests/test_i18n.py`, `tests/test_agent_names.py`, the "CEO 짐작" wording replaced |
| 11 | `recall_bundle` for tasks and builds, `TagRule.records` replacing the `if/elif` in `_subject_records`, `_checked_about` accepting `recall` | stable record ids, in-progress flag, the same bundle before and after `prune_calls`, no read of `var/calls` |
| 12 | `RecallReplyOut`, `check`, `prompts/chat_recall.md`, recall lines kept out of `unanswered()` for the ordinary job and given their own job and marker, `allowed(recall=...)`, `data.recall` returned by `chat.view()`, the thread label, the "Ask" button | fake-model tests (no citation, unknown id, `intent` always `answer` on a recalled line even in a build thread, an ordinary build line sent in the same moment still starts a fix round and is not marked answered by the recall reply), then the owner recalls a real past task and a real past build, asking one answerable and one unanswerable question |
| 13 | Placement statistics query, `place` reading `history`, the settings for `min_samples`, `solo_max_unmet_rate`, `model_tolerance`, `explore_rate`, `audit_rate` | table tests of the history rules, a forcing rule never overridden, each history decision records its statistics, a family under `min_samples` keeps version 1 |

If a box runs short, unit 10's catalog series is the part to move to the next step, and unit 13 never starts before
the earlier units have produced samples.

### Unit 0 results

Taken on branch `feat/staffing-records` at `100b399` (the design commit over `4868556`).

**Migration head.** `d4c8e1f6a2b3` (customer profile values; after merging main the staffing revision follows `a9d4f7b2c613`, the work path records), confirmed by reading every revision's `down_revision`
(one head, no branch). Revision A takes it as `down_revision`.

**Readers of `t.review`** against section 6.1. A task with no checker has `{"verdict": "unchecked", "reviewer": null,
"outcome", "criteria_unmet"}`: no `reviewer_verdict`, no `notes`, no `counter_arguments`, no `claim_checks`. Each row says
what the reader does with that value today. Line numbers are in `backend/justshipit/`, and the design's older numbers
(`context.py:280` and `:299`, `cli.py:309`) have moved.

| Reader | Line | What it reads | With an unchecked task | Unit 5 work |
|---|---|---|---|---|
| `company_graph._finish` | `orchestration/company_graph.py:662`, `:666` | `criteria_unmet`, `reviewer_verdict` | no `reviewer_verdict`, so an alone task never takes the "kept as partial after a stopped rework" branch and goes on to raise its requests | decide the branch for an alone `PARTIAL`, test it |
| `company_graph._write_task_artifact` | `:690`, `:701` and the `independent_review` block at `:712` | `reviewer`, `verdict`, `criteria_unmet`, `notes`, `counter_arguments` | `role(None)` renders as an empty name (`glossary.py:221`), `_said_or` yields an empty verdict (no `work.doc.task.verdict.unchecked`), and the block heads an "independent review" over the author's own unmet list | skip both when `reviewer` is null, write the "not reviewed" line from the new key |
| `company_graph._contested_meeting` | `:789` | `counter_arguments`, `criteria_unmet` | only contested tasks reach it, and an alone task is never contested | none, a test pins it |
| `context.dependency_outputs` | `orchestration/context.py:295-298` | `criteria_unmet`, `notes` | prints "The reviewer found unmet" for the author's own list | say "not reviewed" and add the input-not-reviewed note |
| `context.results_overview` | `:314`, `:321` | `verdict`, `criteria_unmet`, `notes`, `counter_arguments` | prints `Review: unchecked / unmet: ...`, which is correct only because `criteria_unmet` is carried | keep the carried list, add a test |
| `console.task_failure` | `api/console.py:297` | `verdict == UNMET` | never UNMET for an alone task | none |
| `console` task list | `api/console.py:752` | `verdict` | sends `"unchecked"` to the web, whose `Tasks.tsx:366` and `:412` print `label(verdict)` and treat anything but `pass` as a warning | add the label, show alone as not a warning |
| `console` agent detail | `api/console.py:869`, `:884` | `Task.review` column, `verdict` | same value reaches `Agents.tsx:315` | same label |
| `capability/factory.build_and_evaluate` | `capability/factory.py:180-181` | `criteria_unmet` of every task with a review | would feed the author's own unmet list to the skill builder as "review findings" | filter on a non-null reviewer |
| `cli` project table | `cli.py:339` | `verdict` | prints `unchecked` | none |
| `stops.waiting_on` | `services/stops.py:106` | the task's `status`, not the column (it calls `reviewer_for(t.owner)` when `status == "REVIEW"`) | an alone task never reaches `REVIEW`, so the branch is not taken | a test for a task with no checker |

Not readers of the column but of the verdict: `review_outcome` (`company_graph.py:640`) takes a `ReviewOut`, so the alone
path needs its own outcome from the author's `unmet_criteria`. The report and the meeting read it through
`results_overview`. `work_now.py:80` and `console.py:805` call `reviewer_for` and read no review.

**Counts** (commands from section 3, run at this commit). `grep -rn '"ceo"' backend --include=*.py` finds 91 uses in 26
files (design: 26 files). `grep -rl '"ceo"' tests` finds 72 files (design: 72). `run_agent("ceo"` appears 17 times in
9 files (design: 17 in 9). `grep -rn CEO web/src/i18n` finds 213 lines in 19 files (design: 205 in 18) and
`grep -rn CEO backend --include=*.py` finds 376 lines in 75 files (design: 356 in 69). The "CEO" counts grew by 8 and 20
lines since the design was drawn. `prompts/` has 27 lines naming ceo in any case and `web/src` has 12 quoted `"ceo"`.

**Task families.** Ten, in `config/staffing.yaml`, drawn from the role prompts and confirmed by the owner on 2026-11-14 (still editable):
`market_research`, `regulation_check`, `data_analysis`, `strategy_synthesis`, `customer_channels`, `outreach_drafts`,
`financial_model`, `product_scope`, `risk_review` and `other`. The file also holds the starting settings and
`owner_default_family`, one family per assignable role. The five placement thresholds (`min_samples`,
`solo_max_unmet_rate`, `model_tolerance`, `explore_rate`, `audit_rate`) are guesses and say so in the file. The list is
not yet agreed with the owner.

## 13. Not built, and costs accepted

Not built. A multi-author executor for sequential and parallel work inside one task. A model for the Staffing Agency
Manager. Alone tasks widening `rounds.envelope`. Storing prompts or raw output in the database, or extending
`call_transcript_days`. Retiring "team member" and "department". Recall of specs and plans, which already exist as
documents in the registry. A recalled role that acts. A recalled engineer that reads the branch. Splitting the Project
Manager's business-scale work (kickoff, cycles, ideas) from its project work.

Costs accepted.

- Alone tasks produce evidence that is never `verified_fact`. The report's evidence line shows it. The shadow checks
  measure whether that is safe, at the price of one extra call on about one in five alone tasks.
- Every iteration of a task or build adds tens of kilobytes to the database, so a recalled answer stays the same after 30
  days. The text is the same kind that `tasks.result` keeps today. Archiving a project leaves evidence, decisions and
  artifacts in the database, and archiving a person cleans only `OwnerTask` rows (`people.py:581`). So `crew_turns` text,
  including full review notes, is kept with no deletion path, as `tasks.result` is. A purge path would cover both and is
  its own step.
- The server can check that a citation exists but not that it supports the sentence. The chips and the owner's check
  are the defense.
- The database has to be reset before this step runs, and any history the owner would have wanted from the old data is
  gone.
- Placement statistics need weeks of data. Until then the product behaves as version 1.

## 14. Shapes considered

1. A JSONB `staffing` column on `tasks`, plus a records table. One fewer table, and a team decision shown at plan time.
   It needs a `NOT NULL` backfill written twice and does not cover builds. With the old data gone it would work, but a
   subject-keyed table covers tasks and builds with one read.
2. Derive everything from `llm_calls`, as `team.py` does. Smallest, but "alone on purpose" and "alone because a call
   failed" look the same, and the reason is not in any call row.
3. A coordinator model chooses the placement. It cannot be tested as a table, adds to the plan call, and
   `rounds.envelope` is computed before the plan is known. The statistics in section 7 may show a decision that needs a
   model, and the Staffing Agency Manager can become one then.
4. Store every prompt and output in the database. A recalled role could answer anything, but a task grows by hundreds of
   kilobytes, the owner's plan text and evidence list go in whole, and the 30-day design is undone.
5. A new chat subject kind for recall. It needs the `CHECK` constraint migration and splits a subject's conversation. A
   line-level `about` keeps one thread.

## 15. Open risks

- A recalled role can cite correctly and still embellish. If the owner's check finds this, the next measure is a second
  call that verifies the answer against the cited records.
- Chat history lines feed the next recall prompt. A recalled role's earlier answers are not records and cannot be cited,
  but they can echo. Unit 12 decides whether a recalled line shortens the history (`history: 20` today).
- The Project Manager keeps business-scale work, so "Project Manager" names kickoff and cycle planning loosely. If that
  reads wrong on the screen, a third role is a later change.
- The family list in unit 0 is a judgment about how tasks differ. Too coarse and the statistics mix easy and hard work.
  Too fine and no family reaches `min_samples`.
- "Staffing Agency Manager" as a name for code that has no model may look like a character to someone expecting a model.
  The screen says "placed by", and the role makes no call.
- The first thresholds in section 7 are guesses. Unit 13 does not start until a real run shows the spread.

## Implementation notes

Accepted deviations, with the reason. Phase 1 (units 0 to 3) built `staffing/crew.py` and these differ from section 5.

- `JobFacts` has a last field `kind: SubjectKind = TASK`. `fixed.build` is a row of the one rules table, and a row can
  only hold on facts, so the facts need to say that the subject is a build. A build's facts are `owner="engineer"`,
  `family="build"`. `crews.subject_kind` repeats it in the row, which is harmless.
- `Rule` has a `shape` (`Shape`: `CHECKED`, `SOLO`, `CHAIN`) and a `cite` function, and `place` is `place(f, limits)`
  with no `history` parameter. History is added with unit 13, with the statistics type it carries, because version 1
  never reads it and an unused parameter would be dead code. A flag for the rules history may never remove (section 7's
  "forcing" rules) is also left for unit 13, since the design does not yet say which `check.*` rules are optional.
- `Placement.audit` is true when the job ran alone, meaning a shadow check may follow. Whether this task is in the
  `audit_rate` sample is drawn where the check is queued, because `place` is pure and a random draw is not.
- `JobFacts.flow` is a property over `after` and `alongside`, so `Flow` has one place that computes it.
- `Limits` holds every setting of `config/staffing.yaml`'s `staffing` block, and `Limits.from_config` refuses a value
  out of range and names a setting the file lacks. `staffing.load_limits()` reads the file. Section 5 lists only
  `Limits` by name. `audit_budget_usd: 0` is allowed and turns the shadow checks off: `enqueue_audit` queues none,
  rather than jobs the audit budget would only refuse, and the round keeps nothing back for them.
- `RULES_VERSION` and `Shape` are exported from `staffing/__init__.py` next to the names section 5 lists.
- `ensure(s, subject_id, facts, limits)` takes ready-made `JobFacts`. The call site `ensure(s, t)` of section 4.2 needs
  code that reads a task's dependents, its build link and its runtime into facts, which is unit 4's wiring.
- `amend` appends to `crews.amendments` (with a timestamp) and leaves `crews.slots` as the decision made. It does
  nothing when a role already holds that duty in the slots or an earlier amendment.
- Every turn type carries `role`, `round` and `iteration` (the last two default to 0), not only `BuildTurn`. A shadow
  check by a role the crew has no slot for needs its role on the turn, and one base class keeps the columns in one
  place. The record column keeps the rest of the turn, so a fact is stored once.
- `db/models.py` repeats the two enum value tuples for its `CHECK`s (`CREW_SUBJECT_KINDS`, `CREW_DUTIES`) instead of
  importing the package, which imports the models. A test pins them to `SubjectKind` and `Duty`.
- Revision A is `b8e2c6a4d1f7`. `ship init` runs `alembic upgrade head` (`cli.py:42`), so on a database that holds
  tasks or builds it now stops with the revision's message until the owner has reset.
- Nothing kept the family between `create_from_drafts` and `ensure` (open after phase 1). Unit 3b adds a nullable
  `tasks.family` String(40) inside revision A (`b8e2c6a4d1f7`, never applied anywhere, so editing it is safe), set by
  `create_from_drafts` from the checked family. Section 5 says no column on `tasks` links to a crew, and this one does
  not: it is the Project Manager's request, held until the crew stores it in `crews.family` and `facts`. A task with
  no family (made directly, as tests do) reads as `other`. `tests/test_migrations.py` inserts its old-revision task
  rows with only the columns those revisions had, since the model now describes head.
- `staffing.task_facts(s, t, alongside=...)` (`staffing/facts.py`) is the one place a task becomes `JobFacts`.
  `dependents` counts the project's tasks whose `depends_on` names the task, leaving out cancelled and replaced ones.
  `after` is `depends_on`, `alongside` is the batch the scheduling step started (less the task itself), `feeds_build`
  reads `project.plan["build"]["task_ids"]`, and `resumed` is `in_review` for a task in REVIEW, `reworked` for one in
  REWORK or with an iteration behind it, and `fresh` otherwise.
- The family check follows section 2 on origin. A missing family from the Project Manager (`ceo_plan`, `follow_up`,
  `decision`, `realign`) becomes `other` with a warning. A task the owner adds or an idea makes has no family to give,
  so it gets its role's default (`owner_default_family`) without a warning. A name off the list is `other` with a
  warning on every path. The warning goes to the log, not to the returned `warnings`, which `plan` stores in
  `projects.context` where nothing reads it.
- `prompts/agents/ceo.md` names the ten families in one line, and a test fails if that line and
  `config/staffing.yaml` differ. Putting the list in the plan input instead would have meant editing `company_graph`,
  which this phase leaves alone.
- `staffing.checked_family`, `default_family` and `task_families` are public next to the rule, and `services/tasks.py`
  is the first code outside the package to import it.
- `verifier_role(owner)` was a second copy of `reviewer_for` until unit 4 moved the callers and deleted the old one.
  The test that pinned the two together now pins `verifier_role`'s answers, including for a role not on the roster.

Phase 2 (units 3b to 5) wires the rule into the running graph.

- Section 4.2 names two different things `verifier_role`: the pure `verifier_role(owner)` of section 5 and the call
  `staffing.verifier_role(s, t)` for the three callers that have a task. One name for two signatures would hide which
  one a caller gets, so the task form is `staffing.verifier_of(s, t) -> str | None`: the checker of the task's crew, or
  before the task has a crew the checker the rule would place now (None when it would run alone). `staffing.verifier(crew)`
  and `staffing.task_crew(s, task_id)` are the readers 4.2 uses. All three live in `staffing/ledger.py`, next to the
  writer of the JSON they read.
- `work` takes the batch its scheduling step started (`Send` now carries `batch`) for `alongside`. A checkpoint written
  before this change has no `batch`, and the task then reads as having nothing alongside.
- A turn is recorded only when the call left a ledger id (`last_call_id()`). Every real call does. The graph tests that
  replace `run_agent` itself make no ledger row, so their passes record no turn, and they pass unchanged.
- The agent page's "who reviews whom" list (`reviews_for` on `GET /agents/{id}`, read by `Agents.tsx`) is deleted. The
  page keeps the line for a role marked `reviewer` in `agents.yaml`.
- A crew whose `work` entry was the step that found the task ruled out by an owner directive keeps its crew: `ensure`
  runs at entry, before the loop's first `_ruled_out` check. Such a crew has no turns and its subject is cancelled, so
  it reads as dissolved. Moving `ensure` behind that check would add a flag to the loop for a case the crews screen
  already shows correctly.
- Unit 5's alone verdict is `t.review = {"verdict": "unchecked", "reviewer": None, "outcome", "criteria_unmet"}`,
  exactly section 6.1's four keys, written by `company_graph._finish_unchecked`, which then runs `_finish` with the
  outcome. An alone task drafted no request (it would have a checker), so `_finish` raises nothing and the task is
  DONE, for a pass and for a partial alike. One that met none of its criteria is `unmet` and FAILED, with
  `work.task.alone_unmet` as its error instead of "didn't pass review". Since `solo_max_criteria` is 1, an alone task
  that lists an unmet criterion meets none today, so it fails, and a partial needs a higher setting.
  `staffing.UNCHECKED` is the one home of the value, because `orchestration/context.py` reads it and cannot import
  `company_graph`.
- `settle_solo` adds the checker with `amend` and the reason `check.owner_card`. That reason is not a placement rule
  (`place` never reads it), so it is listed in `staffing.AMENDMENT_REASONS`, and the test that every
  `staffing.why.*` sentence names a rule now accepts those reasons too.
- `settle_solo` also adds the checker, with the reason `check.calculation`, when an alone result carries a financial
  model with scenarios. The finance engine (`company_graph._author`) turns it into calculation evidence, and figures
  are checked when finance computes them (`check.numbers`), so they are when another role does. A result with both a
  card and a model records `check.owner_card`, since one checker answers both.
- A failed task the owner retries (`control._reset_failed`) has been authored, so `check.resumed` holds for it, but its
  stored crew was placed before. `staffing.settle_retry` amends a crew with no checker with the checker and the reason
  `check.resumed`, which is in `AMENDMENT_REASONS` and keeps the rule's own sentence. The rule is still not rerun; the
  amendment is the record. `audit.run` skips a task whose crew now has a checker, so a check queued on the failed pass
  never runs beside the real review.
- The review prompt moved out of `work` into `company_graph.review_prompt(s, t)`, read from what the author committed,
  so the audit job asks the same question the review would have.
- The audit job is `orchestration/audit.py` (`audit_task`, a STAGE_WORK row in `gate.POLICY`). It lives outside the
  staffing package because it calls `run_agent`, and `budget`, which `run_agent` uses, imports the package. It runs
  `verifier_role(owner)` on a task whose verdict is still `unchecked`, records a shadow `VerifierTurn`, and does
  nothing else. A retry finds the shadow turn and makes no second call. A refusal (the audit budget is spent) or a
  failed call is a missing sample and ends the job quietly. Its lock key is `audit:<project>`, not the project's, so
  it never waits for the run or holds it, and one audit per project runs at a time.
- An audit call names its task in the ledger (`llm_calls.task_id`) so `staffing.task_spend` can show it on the task's
  own line, but `executor` reserves and settles it with no task (`budget.AUDIT`): the task's spend and cap never see
  it. `budget.reserve` also refuses it when the project's audits (spent, plus holds of running `audit_task` jobs)
  would pass `staffing.audit_budget_usd`. The project's budget still pays for it, so the project total stays the sum
  of its calls. `rounds.envelope` takes `budget.audit_usd` out of `elsewhere` and keeps the whole audit budget back
  from the round instead (nothing while `always_check` is true or `audit_rate` is 0), so an audit within its budget
  never shrinks a round. The round's room is also never more than a call that is not development may still take
  (`budget - spent - reserved - dev_hold`) less the closing calls, so checks that overran their budget cannot leave
  the CEO's review, decision and report without room.
- `rounds.envelope`'s mean per task leaves out tasks whose crew holds no checker (`staffing.alone_task_ids`), rather
  than counting only tasks that have one. The two are the same after the reset, since every task that spent has a
  crew. The difference is a task with no crew at all, which only a test makes, and it still counts as before.
- The Outcome read model does not exist yet (unit 7). `staffing.task_spend(s, task_id)` sums `llm_calls` by purpose
  and returns the task's own calls and cost and its audits' on a separate line, for unit 7 to build on.
- The console's task list and agent page send `outcome` beside `verdict`. The task page shows "done alone, not
  reviewed" (a warning only when the outcome is partial), "None: one role does it alone" for the reviewer, and the
  author's unmet list under its own heading instead of "Independent review".

Unit 6, the crew and turns of a build.

- The build's crew is made in `worker._run`, once the handoff is approved and the run is going to work (a build refused
  before that has none). `engineering/build_crew.py` holds the build's `JobFacts` (neutral values: no criteria,
  dependents or earlier task, `origin="build"`) and `keep`, which asks `ensure` again each time a turn is kept, so a
  resume, a fix round and a new attempt all find the one crew. It imports only names `staffing/__init__.py` exports.
- `BuildTurn` has a new field, `stage`: `plan`, `build` (a fix round's change too), `review` (the engineer's own review
  pass) or `fix`. The design's `BuildTurn` could not tell the engineer's two calls of one iteration apart, nor a plan
  from a fix, since both share a round and an iteration. `tests/test_staffing_ledger.py` names the stage in its two
  `BuildTurn`s.
- Recorded: the plan, the build, the review pass, the fix, the code reviewer and the acceptance. Not recorded: the
  intake (`IntakeOut` is not a `BuildTurn`, and the intake step keeps its outline and assumptions whole) and the product
  role's answers to the engineer's questions (`_clarify`).
- Section 3 and the paragraph on the turn key say a build's steps lose the plan decisions. They do not: the plan and the
  build steps keep their decisions in step data, and their summary clipped to 4000 characters. The plan and build turns
  therefore repeat those fields, because `BuildTurn` requires them and recall reads one table. The review pass and the
  fix leave nothing in the steps, and the findings list and each delivery's acceptance exist only as turns (the step
  keeps a count, and `result["acceptance"]` is replaced by the next delivery's).
- The acceptance turn holds what the product role said (`AcceptanceOut`), before `held_to_core_flow` and the
  walkthrough check adjust it. `result["acceptance"]` holds the adjusted one. It is kept even when the job no longer
  takes the verdict (merged or thrown away while the role read), since the call happened.
- A turn is kept in the transaction of the step that follows its call (`_step(turns=...)`: plan, build, a fix round's
  change, the reviewer in the `review_done` step) or of the acceptance verdict. The review pass and the fix have no step
  after them, so each is kept in a transaction of its own right after the call. A stub that gives `last_call_id()` no
  value has no ledger row, so no key and no turn.
- `round` is passed down: `test_and_review(..., fix_round=n)` from `revise._fix`, 0 from the first run and from a resume
  (a run of a round the queue took back goes through `revise.run`, never `_run`). The acceptance's round is
  `revise.judged(job)`, the last delivered round, so `accept_again` after a stopped round still says the round it judges.
- `BuildTurn.commit` is never filled. The commit a pass left is known to the host only after `_branch_to_review` fetches
  it, and the reviewing step already keeps the commit the reviewer read. Reading it per pass would mean running git in
  a tree the engineer had, which `prepare_worktree` avoids. Unit 11 can read the commit from the steps.

Unit 7, the crews read.

- `staffing.crew_views(s, projects, lang, standing=None)` (`staffing/views.py`) returns one `CrewView` per task and
  build: the subject, a `Standing`, the `Team` (family, shape, flow, every held slot, the slots whose role made no call,
  each reason as `{rule, facts, text, at}` with amendments last), the members from the ledger, and an `Outcome`.
  `GET /crews` passes one project, or the projects `scope.hidden_projects` leaves in for a business or for all.
- `state` filters on `Standing`: `waiting` (not started, no crew yet), `working`, `dissolved`, `cancelled_before_start`
  and `no_record` (ended with no crew, such as a build refused before its handoff was approved). The last three carry a
  `staffing.crew.*` sentence. The design names `state` but not its values. No paging: the design names none, and
  `/engineering` lists the same projects whole.
- The ledger is read in one grouped query per kind (`outcome.ledger_calls`), so `task_spend` and `spend_by_purpose`
  are deleted and their one test reads the view. Members are grouped by role and model, so a fallback shows twice.
- `Outcome.reworks` is a task's author passes after the first, or a build's highest fix round, from `crew_turns`.
  `fixed_path_calls` is a task's author passes times `len(fixed_path(owner))`, and None for a build, whose chain is
  the fixed path, and for a job with no crew.
- A build's verdict is `accepted` or `not_accepted` from `result["acceptance"]`, and its wall time runs from
  `assembled_at` to its last step once the job is settled. A task's verdict is `tasks.review.verdict`, and its wall
  time is `finished_at - started_at`.
- Per-role usage is each row's members, not a roster-wide total. `test_business_scope` reads a task's finance member
  from `/crews` under each business. `GET /org` stays until unit 9 deletes it.

Left as they are after the step 0.2c review, by choice.

- A turn can be missing when a crash falls between a paid call's ledger commit and the transaction that keeps its
  turn, and the call stays in `llm_calls`, so the spend is counted and only the record of what the role produced is
  lost.
- A build's plan and build turns repeat the decisions and summary that `DevJob.steps` already keeps, since
  `BuildTurn` requires them and recall reads one table.
- Downgrading revision `b8e2c6a4d1f7` and upgrading again refuses on a database that holds tasks or builds, by
  design, because the upgrade's reset guard cannot tell a round trip from an old database.

Unit 8, the id rename (step 0.2d).

- `scripts/rename_ceo.py` did the rename with its default flags: 1838 edits in 174 files, and `git mv` of
  `prompts/agents/ceo.md`, `services/cycle_ceo.py` and `tests/test_ceo_holds_the_line.py`. A rerun makes 0 edits.
  The script keeps the lines in its `HISTORY_PINS`: what old migrations write (`tests/test_roadmap_model.py`), an
  old revision's seed rows (`tests/test_work_path_migration.py`), and the one spelling of the former id on each side
  (`agents/roles.py`, `web/src/labels.ts`).
- Stored and wire names keep the old word, a deliberate carry-over. The owner kept the live database (8 projects
  whose plans and `context` JSON use these names), and renaming them needs a migration: `ceo_plan` (task origin),
  `ceo_review` (project phase, the graph node and `route_ceo_review`), `ceo_reviews` (project context key),
  `ceo_decided` (activity verb, with `label.verb.ceo_decided` and `audit.event.ceo_decided`), the `ceo_text` column
  and API field, and 17 more catalog keys with the word in their names (`decide.who.ceo`, `cycle.ceo_says`,
  `door.problem.not_ceo`, ...). The script renames them with `--rename-stored --rename-catalog-keys` when a migration
  comes.
- Section 11 assumed a reset and no old rows. The owner kept the data instead, so rows written before the rename hold
  the old id, and every reader that matches one takes it: `agents/roles.py` maps it (`current`, `stored_ids`) for
  door rules (`policy.current_rule`, `roadmap.DoorRule`, the derived-rule withdrawal), a card decided under a rule
  (`responded_by`, `responders.DECIDED_UNDER_A_RULE`) or withdrawn (`by`), an owner's stop on `agent:ceo` (it holds
  the Project Manager, and resuming lifts it), a chat thread's `default_agent` and a queued reply job's agent,
  profile values' `written_by`, the work path's call label and backfill, the agent page and run list, and the
  owner-facing name (`glossary.role`, the console's `labels.currentAgent` under `agentName`, the bets proposal tag).
  New rows get the new id, `responded_by` included (`project_manager (결정 권한)`). Migrations `5b1e7c2d9a40` and
  `8c41f0d2e7a9` still write the old id over old rows, which these readers take.
- `cycle_stages.summary_by` is String(8) and `project_manager` does not fit, so the codemod's write would fail on
  Postgres. It keeps storing the old id as a code (`roles.SUMMARY_BY`), and the board reads it as `project_manager`.
  Widening the column is a migration, left for the one that renames the stored names.
- An old link to `/inbox?tab=done&by=ceo` shows the unfiltered list. The `?by=` value is not mapped.
- The display word follows section 10 for the catalogs: `label.agent.project_manager` and `glossary.ROLES` still say
  "CEO" until unit 10. The prompts are done earlier in one respect: every line where a prompt tells the model who it
  is (`agents/project_manager.md` and the eight purpose prompts' role lines) says the Project Manager now, because the
  sentences this unit hand-wrote (`contracts/door.py`, `prompts/door_rules.md` and its kind list, `contracts/chat.py`,
  `prompts/chat_reply.md`) name the role next to its id, and a model told it is the CEO could not tell that "handoff:
  Project Manager only" meant it. `door_rules.md` also says a plan written before the rename calls the role the CEO.
  The prompts' other mentions of the CEO stay for unit 10. `card_words.BADGE` also cuts "Project Manager's pick" and
  "프로젝트 매니저 추천".
- `tests/console_run` runs a console module under node with the repo's own rolldown transform, so a test checks what
  `agentName` shows for an old responder. It skips when `web/node_modules` is missing.

Units 11 and 12, recall (step 0.2d).

- `staffing.recall_bundle(s, kind, subject_id) -> Bundle | None` is one bundle per subject, not per role: every
  turn of the crew is in it, labelled by role, because "why did you fix it that way" needs the review the fix
  answered. `Bundle.roles` is who has a turn (shadow checks included), and it decides who may be recalled. None means
  the subject has no crew. A record id comes from its row: `T-n` or `J-n` (the assignment), `crew-n`, `turn-n`,
  `E-n`, `REQ-n`, `call-n` (a ledger row) and `not-kept`, so a citation names the same record after later work adds
  more.
- A build's assignment holds its spec key and title, status, attempt, branch, error and every step (with the commit a
  review step read), since unit 6 left the commit in the steps. It does not hold the spec's criteria, which live in
  the registry documents, not in the database. The acceptance turns hold how each criterion was judged, and the
  `not-kept` record says the criteria are not there. Section 8 lists "the spec title and criteria".
- A ledger record leaves out `raw_path` and clips `error` to 300 characters. Every string of every record goes
  through `secrets_scan.scrub` with an English marker, so neither the prompt nor `GET /chat/recall` carries a token a
  turn or an error recorded.
- `TagRule.records(s, ref, lang, asked)` replaces the `if/elif` in `_subject_records`. The build and card rows call
  `chat_reply.build_records`, `step_records` and `card_records` (now public) through a function-level import, since
  `chat_reply` reads the table. A task's ordinary conversation still gets no records: recall is its records.
- A recall line names a role on the roster with a turn on the subject. `to_agent` must be empty or that role, and the
  line goes to the role. Refusals: `chat.refuse.recall_subject` (not a task or build, or no crew),
  `chat.refuse.recall_role` and `chat.refuse.recall_to`.
- The recall reply is a `chat_reply` job with `recall_seq` in its payload and the key `chat_reply:<thread>:<role>:r<seq>`,
  so it keeps the thread's lock, its place in the reply list and `reply_gave_up`. `chat_reply.run(recall=)` hands it to
  `services/chat_recall.py`. Its answer carries `recall_answered: <seq>` and no `answered_seq`, so each kind of reply
  marks only its own lines.
- `check` keeps a reply only when it cites at least one id and every cited id is in the bundle the role was shown
  (square brackets stripped). A reply with `no_record` true and valid citations is kept, as a partial answer. Any
  other reply becomes a line with an empty body and `data.key = chat.recall.no_record`, worded in the owner's language
  when it is read (`line_text` now words a text line that carries a key), like a status line.
- `RecallReplyOut` has no intent and the recall job carries out no effect, so a recalled line cannot start a fix round,
  escalate, hand off or file a card by construction. `chat_tags.allowed(recall=True)` permits only `answer` and is
  pinned by a table test, but no production path asks it, because there is no intent to check. It stays because this
  unit's scope names it, and it is the rule a future recall contract with an intent would go through.
- The prompt is `prompts/chat_recall.md` with the role's own brief (`prompts/agents/<role>.md`) appended, the records
  between one data fence and the question between another. It carries no conversation history (section 15's open
  risk): an earlier answer is not a record, and the answer has to be the same whenever it is asked. Ordinary replies
  still see recall lines in their history, as chat.
- The call runs on `chat.reply_model` with the isolated runtime and no tools, purpose `chat`, and names only the
  project, never `task_id` or `dev_job_id`. So a recall never adds a ledger record to the bundle it reads.
- `chat.view()` returns `data.recall = {rebuilt, cites, no_record, in_progress}` on an answer, and `thread_view` has
  `recall`, true once the thread holds one. The dock shows "rebuilt from records, not the same memory" above the
  thread's lines and under each answer, with the cited ids. The citation chip that opens a record and the "Ask" button
  belong to the crews screen (unit 9). `GET /chat/recall/{kind}/{ref}` gives them the records, the in-progress flag and
  the roles that can be asked.
- The bundle has no size cap. A task with many reworks sends every turn whole. A cap would make some records
  uncitable, so it waits for a real bundle to be too large.
- `/security-review` over `ae00936..HEAD` found nothing at confidence 8 or more. It noted that a ledger error was
  clipped before it was scrubbed, so a token cut at 300 characters could pass the scrub. The error is now scrubbed
  first, and a test pins it.
- Pending: the owner's check on real data (a real past task and a real past build, one answerable and one unanswerable
  question each) is not done. The database has no past task or build after the reset, and it is not faked. The dock's
  label was type-checked and built but not looked at on the running console, which serves `main`'s build.

Unit 13, placement that learns (step 0.2e; section 7 calls it 0.2d, but section 12 moved it).

- Built before any samples exist, at the owner's request, so it is inert on today's database: a family with fewer
  than `min_samples` judged tasks is placed exactly as version 1 placed it (`place(f, limits, history) ==
  place(f, limits)`, a table test over facts, histories and draws). Only new crews read history. `ensure` returns a
  stored crew before it computes anything, so a crew is never placed again, and each new one records
  `rules_version` "2". A version 1 crew reads on `GET /crews` as it was.
- `staffing.placement_stats(s, ladder, family)` (`staffing/stats.py`) returns one `Stat` per family, role and model.
  A task counts once, under the role and model of its first author pass, and only when a check has judged it: `alone`
  when the first verifier turn was a shadow check, `checked` when it was the task's own checker. Each tally says how
  many of those first checks listed an unmet criterion. `calls` is the tasks' own `llm_calls` (failed ones included,
  audits apart) and `reworks` is author passes after the first. Cost is counted in calls, never dollars. The ledger
  stores the served model's full name, so it is read on `model_ladder` ("claude-haiku-4-5" is `haiku`); a model off
  the ladder keeps its name and is never chosen.
- `History(stats, configured, draw)` is what `place` reads besides the facts: the statistics, each role's model on
  the roster, and the explore draw. `ensure(..., draw=random.random)` draws it, the way `enqueue_audit` draws the
  audit sample, and `staffing.recorded_history` builds the rest. `verifier_of` predicts with the same history and a
  draw that never explores.
- Forcing checks are a flag on the rule (`Rule.forcing`): `check.feeds_build`, `check.analysed`, `check.numbers`,
  `check.reviewer_role`, `check.resumed`, `check.decision`, `check.owner_added` and `check.config`. Where one holds,
  history changes nothing at all, the model included. The card for the owner and the computed figures are
  amendments made after the author ran, so history never sees them and `settle_solo` still adds their checker. Only
  `check.criteria` and `check.has_dependents` are optional, because they are guesses about difficulty.
- Four decisions, each with its sentence and the statistics it read in its facts. `history.solo`: the family has
  `min_samples` shadow checks and less than `solo_max_unmet_rate` of them found unmet work, so the optional checks are
  waived (`waived` names them) and the task runs alone. `history.checked`: the rate is at or above the limit, so a task
  version 1 would run alone is checked, and a checked one adds the reason. `history.model`: the author's configured
  model and a cheaper one on the ladder both have `min_samples` judged tasks of the family, and the cheapest whose
  first-check pass rate is within `model_tolerance` of the best is chosen. `history.explore`: the configured model has
  its samples, the next cheaper one does not, and the draw is under `explore_rate`. Rates compare as exact fractions
  of the decimal the owner wrote, so 2 of 20 is at a 0.1 limit, not under it.
- Explore is gated on the configured model having `min_samples`, which section 7 does not say. Without the gate a
  tenth of the unforced tasks would run on a cheaper model on an empty database. A task that ran alone on an explored
  model is always shadow-checked (within the audit budget, and not when `audit_rate` is 0), since its sample counts
  only once a check judges it.
- `Slot.model` is None for "the role's configured model", and history names a model only when it chooses another.
  Recording the roster's model on every slot would have made a version 2 placement differ from version 1's when
  nothing changed. The author's call passes the slot's model as `model_name`; the analysis runtime does not take one,
  and an analyst's task is forced. History never changes the checker's model, because no record says how well a
  checker did.
- Settings as shipped, still guesses: `min_samples` 20, `solo_max_unmet_rate` 0.1, `model_tolerance` 0.05,
  `explore_rate` 0.1, `audit_rate` 0.2, and the new `model_ladder` `[haiku, sonnet, opus]`, cheapest first.
- Accepted limits. A family that returns to checked gets no more shadow checks, so it stays checked until the owner
  changes a setting. The shadow rate comes from tasks small enough to run alone, and `history.solo` applies it to
  larger ones, so a family's first alone tasks above the criteria limit are an extrapolation the next shadow checks
  correct. `verifier_of` runs the statistics query for each task that has no crew yet when the console lists it.
- Pending for real data: none of this has run on a real sample. The live database has none since the reset, and none
  was made up. The proof is table tests, a hand-counted query over seeded crews, turns and ledger rows, and 18
  mutations of the rule, the query and the wiring, each caught by a test. Once a real project has produced judged
  tasks, read `placement_stats` against the crews screen and revisit the five thresholds and the ladder.

Unit 9, the crews screen (step 0.2d).

- `/impeccable shape` ran without the owner, who could not be interviewed in this run. The brief
  (`.impeccable/surfaces/web-src-pages-crews-tsx.md`) is inferred from sections 4.1, 9 and 10 and marks its assumptions.
  The structure is section 4.1's sketch: one section per project, one row per task and build in plan order, and under
  each row three labelled lines, Team, Why and Outcome, so the reasons show without a click. The surface roll dealt a
  timeline, a list with a side panel and a dependency graph. The brief overrides all three: a side panel hides the why,
  and a graph is a second drawing of a team, the failure this section removes.
- The screen title is "Teams for each job" / "꾸린 팀" (`shell.nav.crews`). Section 10's term is the singular "Team for
  this job", which names one job's team; a screen that lists every job's team reads as the plural in English. Unit 10
  decides whether the term and the title share one string.
- `/crews` takes the endpoint's own parameters: `?business_id=`, `?project_id=` (which wins, as on the server) and
  `?state=`. With none it lists every business, which was the org chart's default. `FromOrg` in `Crews.tsx` maps the old
  address: `?project=` becomes `project_id`, `?scope=business` the business on screen, and it waits for the business
  list before it redirects so the business is not lost.
- A row says only what the record holds. The reasons are `why[].text` verbatim, after "Placed by the Staffing Agency
  Manager:"; an amendment adds "Added <when>:". Members are the ledger's (`members`), each with its slot's duty when it
  holds one, its model and its calls; a placed slot whose role made no call shows as "placed, no call yet". A job with
  no crew shows the server's `note` and no Team, Why or member line, and an Outcome line only if the ledger has calls
  for it. The outcome line also says `fixed_path_calls` ("the path before staffing would have made N calls") and the
  spot checks apart, as `Outcome` carries them.
- The one chip per row is the subject's status, not the standing, since the owner reads Done, Failed or Cancelled
  first; the standing is the State filter and shows in the row as a team or a note. A working row's chip and the status
  sentence read `useWorker`, so neither says a team is working while the background process is off.
- The recall button has a named place and draws nothing: `AskWhy` in `Crews.tsx`, between the title and the chip. Unit 12
  fills it.
- The roster stays the Agents page (section 9). The crews screen links to it as "Roster" / "인력풀", while the Agents page
  is still titled "Agents" until unit 10's vocabulary series names it.
- Deleted with the org chart, because nothing used them after the callers moved: `Org.tsx`, `OrgChart.tsx`,
  `orgchart.css`, `GET /org`, `_REVIEW_LINKS`, the server keys `console.review.cross` and `console.review.code`, and the
  console keys `company.org.*`, `company-people.org.*`, `company-people.agents.org` and `shell.nav.org`. React Flow stays
  in `package.json` for 0.4b. No test read `/org` any more (unit 7 moved `test_business_scope`). Comments and the
  README, PRODUCT.md, DESIGN.md and docs/I18N.md lists that named the org chart screen now name this one. With those
  keys gone no catalog says "org chart" or "조직도" any more, so unit 10 can add both as retired words with no other
  change. "CEO" is untouched and still in 20 console catalog files.
- The critique (two isolated assessments, `.impeccable/critique/2026-11-13T17-12-38Z__web-src-pages-crews-tsx.md`,
  24/40) and the polish after it changed four things the notes above would otherwise get wrong. An amended row says
  "First placed by the Staffing Agency Manager:" and "Then added <when>:", because a checker added later otherwise sits
  under a first reason that says one role does it alone. "After" and "alongside" name the earlier tasks by title through
  `RefLink`, and section headings show the project's name without `P-n`, since `tests/test_log_pages_name_records.py`
  forbids a bare record code on any screen; a row's own `T-n` / `J-n` stays beside its title, as on the Tasks page. With
  a State filter on, the status sentence counts what is shown instead of saying no team is working. The server's
  `staffing.why.check.criteria` and `solo.small_leaf` English sentences now say "done criteria", replacing "Criteria:
  3, above the 1 one role may take alone".
- Left from the critique, for a later unit: a link from a task or build to its row (row anchors, or the team line in
  the task drawer and on the build page), folding settled rows to one line on long projects, and defaulting to the
  business on screen. Each is a layout or routing decision beyond unit 9's scope.

Unit 10, the vocabulary (step 0.2d).

- The four terms are in `glossary.TERMS` after `agent`, with section 10's words. Their code names are
  `project_manager`, `staffing` (the package), `crew` and `agents` (`config/agents.yaml`). The agent definition is
  "A role on the roster that is called in per job, such as research or marketing". `org chart` and `조직도` are
  retired on `crew` in the first commit, since unit 9 had removed their uses. `CEO` and `AI CEO` are retired on
  `project_manager` in the last commit of the series, in both languages.
- The rename was a codemod (a scratch script, not kept): it changed only string values whose nearest label was
  `en:` or `ko:`, never keys or comments, and wrote "Project Manager" or "프로젝트 매니저" by that label. Counts of
  language texts changed: 336 in 20 console areas, 124 in 14 server areas, 14 in the glossary (12 term
  definitions and the two `ROLES` names). Every Korean particle after the word was the form for a word that ends in
  a vowel (가, 는, 와) or one that fits any word (의, 에), and 프로젝트 매니저 ends in a vowel too, so no particle
  changed. In English, a label that began with the role
  and a noun ("CEO plan, round 2") reads as the role's thing ("Project Manager's plan, round 2"). Short labels that
  begin with the role and a verb keep the siblings' style without an article ("Project Manager decided", beside
  "You decided" and "System closed it").
- The 0.3 "CEO 짐작" / "CEO's guess" (`label.provenance.guess`) is "짐작" / "Guess", the server's word for the same
  value (`roadmap.provenance.guess`). A guess is a plan line's standing. Who made it is not part of the label, and
  naming the Project Manager would be wrong for lines a later model call guessed.
- `decide.inbox.week` had a placeholder named `{ceo}`, which the retired-word test reads as the word. It is
  `{theirs}`, and `ENDS_IN_HANGUL_OR_DIGIT` in `tests/test_i18n_particles.py` follows it.
- The kickoff chat's speaker was a hard-coded "CEO" in `Conversation.tsx`. It now renders
  `agentName("project_manager")`.
- The roster. The Agents page title (`company.agents.title`), its breadcrumb and the nav group (`shell.nav.agents`), and
  the sidebar link (`shell.nav.agents_all`, `agents_all_n`: "Roster of {n}" / "인력풀 {n}명") say Roster / 인력풀, the
  term the crews screen links with. "Agent" stays the word for one role on it.
- The crews screen keeps its own title, "Teams for each job" / "꾸린 팀" (`shell.nav.crews`), instead of reading
  `term.crew`. The screen lists every job's team, so English needs the plural, and Korean is the same words either way.
- Prompts. Besides `prompts/*.md` and the roster title (`agents.yaml`, "AI CEO"), the strings the server writes into
  model input said the CEO: schema descriptions (`contracts/planning.py`, `requests.py`, `work_path.py`), the
  `gate.POLICY` job descriptions the cycle prompt lists, and prompt headings and labels (`orchestration/context.py`,
  `bet_retro.py`, `ideas.py`, `kickoff._render_talk`). They say the Project Manager now. `door_rules.md` said that a
  plan written before the rename calls the role "the CEO". It now says the plan uses the old title that the owner's word
  list retires, because the output-language rule at the end of every prompt now ends the Project Manager's line with
  "(never call it 'CEO', 'AI CEO')".
- Left on purpose: catalog key names and stored or wire names with `ceo` in them (unit 8's list); comments and
  docstrings in code outside the catalogs; `match/card_words.py`, which still strips "CEO 추천" and "ceo's pick"
  from older model output; `docs/ARCHITECTURE.md` and `docs/I18N.md`, which record what happened before the rename.
  README and PRODUCT.md prose now describe a Project Manager and a team for each job. "Team member" and "department"
  are not retired (section 10).
