# Merge card walkthrough: understand and try a build from its card

Status: design, no code, grounded in `main` 18d1476. Roadmap 0.2b. What this says about REQ-7 comes from the code that
builds the card and the card's API data the owner's session read, not from its row. Mina, `habit-app` and the step ids
in the examples are made up.

## Why

REQ-7 asks the owner to merge uxscope, a CLI that turns a feature-list file into a flow spec per screen (`init`,
`generate`, `check`; it writes `spec.json`, `spec.md` and `prompt.md` and judges 12 UX states). The owner could not
tell from the card what uxscope is, who it is for, what the scenarios test or why, what "run generate twice" means, or
whose fault a failure would be. The owner wants the card alone to explain the product and walk a non-developer
through real user journeys: what to type, what it does, and what they should see, taken from output the agent
captured on this build. Developer tests fold under "Already checked by the agent". A step that goes wrong opens team
chat with the step attached.

## 1. Diagnosis

The card shows what the acceptance check recorded, and the acceptance check records test verdicts, not a walkthrough.

| What the owner saw | Where it comes from |
|---|---|
| A list headed "Scenarios" with 27 rows, most not scenarios | `MergeReview.tsx:153` titles every criterion `decide.mr.checks` ("시나리오"). The list mixes the agent's own criteria, the core-flow criterion, P1 scenarios, owner fix points and constitution rules (`worker.py:1404-1407`, `:1485-1500`, `:1527`) |
| Each row headed by a test action ("generate를 두 번 실행해요(두 번째는 다른 --out 폴더)") | `checkLabel` is the scenario's `when` (`reviewModel.ts:33`), and the spec's P1 scenarios are written as tests: `SPEC_ASK` tells the writer they "are run against the build at the acceptance check" (`specs.py:63-64`), and `core_flow_build` makes the engineer's script "check its Then" (`worker.py:625-628`). Determinism and "does not overwrite" are what a checkable Then looks like |
| Given/When/Then cut short | `stories._words` cuts each part to 90 characters (`stories.py:96-101`) before `criteria` stores the joined sentence (`:115`). The full text exists only in `stories.yaml` at the spec commit |
| The same evidence on every scenario ("만든 코드의 확인 스크립트로 돌려서 통과했어요.") | Evidence is the catalog line for the marker `[scenario US1.2] pass` (`stories.py:114-116`, `build.py:609`). `run_core_flow` keeps only those markers and the last 4000 characters of the log (`worker.py:1470-1472`). Nothing a step printed reaches the card |
| Eight bare commands in one block | `UseOut.local_steps` is "the exact commands" with no purpose per step (`handoff.py:492`). `merge_review._use` puts two clone commands in front (`merge_review.py:46-55`), and `UseIt` prints them as one `<pre>` (`MergeReview.tsx:43`). No step says what it does or what appears |
| Needs that are not the owner's (`codex`, `ANTHROPIC_API_KEY`) | `_USE_ASK` asks for every key the product's files name (`worker.py:1511-1513`). They are the product's own needs, read from its README, with no line saying what the owner needs to try it |
| No "what is this" | `SpecDraftOut.prd_markdown` holds problem and users as free text (`handoff.py:151`), and the plan has `goal` and `users` (`contracts/plan.py:41-42`). Neither reaches the merge card (`requests.py:1381-1395`) |
| No "whose fault" | `CriterionResultOut` is `criterion, met, evidence` (`handoff.py:509-512`). Nothing records product bug, wrong scenario or missing environment |
| No way to ask about one step | The dock opens only from the Team button (`Dock.tsx:32-52`), and `chat.send` takes a body and nothing structured (`chat.py:162-183`) |

The root cause is one structure doing two jobs. P1 scenarios are both the people-facing story and the script's test.
The fix gives people their own record (journeys), runs it the way a person would, and moves the tests under
"Already checked".

## 2. Data model

### 2.1 Journeys, in the spec

The spec writer fills two new fields of `SpecDraftOut` (`handoff.py:149`). `specs.py` numbers them and writes
`journeys.yaml` beside `stories.yaml` (`specs.py:269`, added to `_OPTIONAL` at `:582`), so the engineer gets
`.ship/spec/journeys.yaml` the way it gets the stories (`worker.py:476-481`).

```python
class AboutOut(BaseModel):          # "What is this", restating the approved plan's title, goal and users lines
    what: OneLine(140)              # uxscope turns your app's feature list into a flow spec for each screen
    who: OneLine(140)               # a solo founder planning an app who is not a designer
    problem: OneLine(140)           # before building, they don't know which screens they need

class JourneyStepOut(BaseModel):
    do: OneLine(100)                # what the person does, in their words. Never a command
    why: OneLine(100)               # what the step is for
    see: OneLine(140)               # what they see when it worked, concrete enough to compare with a capture

class JourneyOut(BaseModel):
    title: OneLine(60)              # Turn a feature list into screen specs
    persona: OneLine(80)            # Mina, planning a habit-tracker app alone
    why: OneLine(120)               # she wants to know which screens to build before she pays a developer
    brings: OneLine(120)            # what she has at the start, like owner_data: 6 features in a text file
    success: OneLine(140)           # she has one spec per screen and a list of states she hadn't thought of
    priority: Literal["P1", "P2"]
    steps: list[JourneyStepOut]     # 2 to 8

SpecDraftOut.about: AboutOut | None = None
SpecDraftOut.journeys: list[JourneyOut] = []   # 1 to 3; ids JN1, JN1.1 given by the server, like stories.number
ScenarioOut.guards: OneLine(80) = ""          # what the test catches: "the same list gives the same spec every time"
CriterionResultOut.about: Literal["spec", "rule", "asked"] = "spec"   # so the fold groups the agent's own entries
```

`ScenarioOut` stays the script's test, and `guards` is its one-line purpose in the fold.

### 2.2 The binding, in the branch

The engineer writes `scripts/walkthrough.yaml`: how each journey step is done on this build. It is the only part the
engineer controls, and it holds actions, never output.

```yaml
setup:                       # what the owner runs once after copying the branch; captured like a step
  - sh scripts/ci-setup.sh
steps:
  JN1.1: { run: "npx uxscope init habit-app" }
  JN1.2: { run: "npx uxscope generate habit-app/features.md --out habit-app/out", show: "habit-app/out/spec.md",
           seed: { "habit-app/features.md": "fixtures/journeys/habit-features.md" } }
  JN2.1: { run: "npx uxscope check broken.md", expect: error }   # a journey where the user is told what to fix
  JN3.2: { open: "/today" }                                      # a product with screens: the state at this step
```

`run` is one shell line of at most 300 characters (the card's `_STEP`, `merge_review.py:22`). `show` is a file the step
writes, whose first lines join the capture. `seed` copies a sample input from `fixtures/journeys/` before the step, and
the card offers the same file to the owner. `expect` is `ok` (default) or `error`. `open` is a path on the preview
address (`try_it.PREVIEW_ADDRESS`). `walkthrough.lint(binding, journeys) -> list[str]` (new module
`engineering/walkthrough.py`) lists unbound steps, unknown ids, a `run` that never calls the product (`echo`, `printf`,
`cat` of a file the branch ships), and a `show` or `seed` outside the folder. The lint goes back to the engineer as
`_misnamed_screens` does (`worker.py:585-596`). The file sits under `scripts/` because `_clear_ship` removes anything
the branch puts in `.ship/` (`worker.py:1413`).

### 2.3 The run record, on the build

The worker runs the binding and stores one record per delivered commit in `DevJob.result["walkthroughs"]`, keyed by
`result["head"]`, the commit the merge card is about (`worker.py:1322-1325`, `:1919`). A repeated acceptance on the same
head overwrites its own entry, and only the last three heads are kept. No table and no migration (6.6). The record
copies the spec's journeys and stories, so the card never reads the registry and an old card keeps its own text.

```python
class Capture(TypedDict):
    text: str; lines: int    # cleaned output (6.1): 60 head + 20 tail lines, 6000 characters; lines before cutting
    exit: int | None; ms: int; shot: str | None       # an `open` step has no exit; its shot is in var/dev-screens/J-n
    volatile: list[tuple[int, int]]                   # spans that differ on every run (6.4)
    scrubbed: list[dict]     # [{"kind", "n"}] from secrets_scan.scrub, never the value

StepState = Literal["passed", "differs", "not_run"]
Fault = Literal["product", "scenario", "environment"]
NotRun = Literal["unbound", "no_docker", "setup_failed", "earlier_failed", "out_of_time", "needs", "unreachable"]

class StepRun(TypedDict):
    id: str; do: str; why: str; see: str; action: str   # spec text copied at acceptance; the exact command or address
    capture: Capture | None; state: StepState; not_run: NotRun | None; need: str | None
    fault: Fault | None; fault_by: Literal["server", "agent"] | None
    looks_like: str; hint: str   # the agent's lines: what the capture shows; "If you see 'command not found', ..."

class WalkthroughRun(TypedDict):
    v: Literal[1]; head: str; spec_commit: str; ran_at: str; ms: int; setup_ms: int
    about: dict | None; setup: list[StepRun]; journeys: list[dict]   # journey fields plus steps: list[StepRun]
    stories: list[dict]      # the stories with full scenario text and guards, for the fold (no 90-character cut)
```

### 2.4 The card read model, and old cards

`merge_review.card(job, repository, head)` gains `walkthrough` (the record for the card's `action_params.head`, or the
newest when the card has no head) and `checked` (the fold's groups, 2.5). `_merge_card` passes the head
(`requests.py:1394`). Everything else on the card stays.

| What is missing | What the card shows |
|---|---|
| No record (REQ-4, REQ-7, any build accepted before 0.2b) | `walkthrough: null`. **What this is** shows the spec title and "This build was checked before builds recorded what they are for." **Before you try** and **Try it** show today's `use` block under the line "These commands come from the product's files. Nobody ran them as a walkthrough." The 27 criteria move into **Already checked** grouped by 2.5, with their 90-character text as stored |
| A record from a spec written before 0.2b (accepted after) | No **What this is** and no journeys, so **Try it** is the legacy block. The stories fold with their full text |
| The card's head has no record, a newer head has one | The newest record, under "Captured on a later version of this build than this card" |
| A step with no capture | `not_run` with its reason, never passed |

### 2.5 "Already checked by the agent"

`checked` groups the criteria by what they protect, each group with one line of purpose and a met count:

1. The daily task on data shaped like yours (the core-flow criterion, `worker.py:1495`).
2. One group per story, headed by its title, with the story sentence as purpose. Its items are the P1 scenarios with
   `guards` as their line, full text one click away. A legacy job has no copied stories, so its scenario criteria form
   one group, "Scenarios the build's own script ran", parsed by `merge_review.scenario` (`merge_review.py:66`).
3. The rules you set for the product (`about == "rule"`).
4. What you asked to change in the last round (`about == "asked"`).
5. The spec's other criteria.

The fold starts closed with "{n} checks, {m} passed". An unmet check also lists itself in **Before you decide**, as
today (`reviewModel.ts:59-72`).

## 3. Who writes what, and when

### 3.1 The spec writer

`SPEC_ASK` (`specs.py:51-85`) gains one paragraph, and `prompts/agents/product.md:6,10` says the same in one line each.

> Write `about` from the approved plan's title, goal and users lines (the PRD's, when there is no plan), adding no
> claim they don't make. Then write 1 to 3 `journeys`: how real people will use this version. Each names one person
> (a role and a moment, not a tester), why they came, what they bring, 2 to 8 steps in their words, what they see at
> each step, and what they have when it worked. A journey is not a test. Never repeat a step to compare runs, never
> check what the product leaves alone, never name a command or a file format the person would not know. Those belong
> in `user_stories` scenarios, which the build's script runs. Give each scenario `guards`: what it catches, in one
> line the owner reads.

`specs.journeys_problem(out) -> str | None` sends a draft back once, as `_NO_P1` does (`specs.py:131-139`), when there
is no journey, a journey has fewer than 2 or more than 8 steps, a `see` is empty, or a step reads like a test. "Reads
like a test" is a word list per language in `i18n/match/test_words.py` (twice, again and compare, same result,
overwrite, unchanged, regression; 두 번, 다시 돌려, 똑같, 덮어쓰, 그대로인지), checked against `do` and `see`. A second
failure keeps the draft and writes `journeys_missing` on the spec card, so the owner sees it before approving.

The spec approval card lists the journeys under the stories (`stories.card`, `stories.py:72-74`): title, persona and
success, one line each. The owner approves them with the spec.

### 3.2 The engineer

A sentence beside `core_flow_build` (`worker.py:618-631`): "For each step in `.ship/spec/journeys.yaml`, write in
`scripts/walkthrough.yaml` how a person does it on this build, with the product's real commands as its README gives
them, or the screen path they reach. The worker runs them as written and shows the owner what they print, so never
write output into this file." The lint of 2.2 runs after each engineer call.

### 3.3 The worker

`walkthrough.walk(repo, branch, binding, journeys) -> Walk` runs in `_accept` after `run_core_flow`
(`worker.py:1415`) on a fresh export, because `core-flow.sh` writes into its own and a journey would find `habit-app/`
already made. `docker.run` has no exec (`sandbox/docker.py:45-60`), so one container runs one fixed runner script the
server writes from the binding (`walkthrough.runner(binding, nonce) -> str`), never one the branch supplies. It runs
`setup` once and copies the set-up folder per journey, so each journey starts clean without a second install. Each
step runs under `timeout 90`, and the runner prints one line per step: `@@<nonce>@@` and JSON with the step id, exit,
milliseconds, and the output and the head of the `show` file in base64. A `show` that is a link or leaves the journey
folder is refused. The nonce is new per run, and base64 keeps product output from forging a frame. The host parses the
frames and calls `walkthrough.clean(raw) -> Capture` (6.1). `open` steps go to the screen capture (`_capture`,
`worker.py:1536-1560`), which already photographs named paths.

Every step is `not_run` when the branch has no binding (`unbound`) or Docker is unreachable (`no_docker`). A failed
setup marks the rest `setup_failed`, a step that differs marks the rest of its journey `earlier_failed`, and steps past
`test_timeout_s` (1200 seconds, `config/company.yaml:64`) are `out_of_time`. The container gets the core flow's spec:
`network="sandbox"`, whose egress allows only `api.anthropic.com`, npm and PyPI (`docker/egress/allowlist`), 4g, 2
CPUs and `env={"HOME": "/tmp"}` (`worker.py:1462-1465`). It never gets the owner's Claude token or a connector
credential, so a step that needs a login fails, and 3.4 turns that into `not_run: needs`.

### 3.4 The product agent at acceptance

`_accept` adds a `## User journeys, as run` section after `_core_flow_ask` (`worker.py:1416`): each step's `do`, `see`,
action and capture, the captures inside `fenced("journey run", ...)` as text the product printed, not instructions.
`AcceptanceOut` (`handoff.py:409`) gains:

```python
class StepVerdictOut(BaseModel):
    step_id: str                          # JN1.2
    verdict: Literal["passed", "differs"]
    fault: Literal["product", "scenario", "environment"] | None   # None when passed
    need: str = ""                        # for environment: the name from use.needs it lacked
    looks_like: OneLine(120)              # what the capture shows, for the owner to compare with their own run
    hint: OneLine(120) = ""
AcceptanceOut.journeys: list[StepVerdictOut] = []
```

`walkthrough.held(run, verdicts, needs) -> WalkthroughRun` is a pure function and has the last word:

- No capture means `not_run`, whatever the agent said.
- `passed` needs a capture, an exit that matches `expect`, and the agent's `passed`. An exit that does not match is
  `differs` even when the agent says passed.
- `environment` holds only with a server signal (the egress proxy refused a host, or exit 137 under the 4g cap) or a
  `need` that names a required entry of `use.needs`. Then the step becomes `not_run: needs` with the need named. Any
  other `environment` becomes `product`, with `fault_by = "server"` and the agent's reason kept. A timeout and exit 127
  ("command not found") are the build's until shown otherwise.
- `scenario` (the journey step asks for something the approved spec never promised) stays as the agent said, labelled
  as the agent's reading. A wrong binding is the engineer's, so it is `product`.
- A captured step with no verdict is `differs` with no fault, shown as "not judged".

`held_to_core_flow` (`worker.py:1485-1500`) adds one criterion per P1 journey, "{title}, as {persona} would".
It is unmet when a step differs with a `product` fault, or the run is `unbound`, `setup_failed` or `out_of_time`, all
of which a fix round can repair, and `gate.failures` (`engineering/gate.py:20`) sends the build back. A step that is
`not_run: needs` or `no_docker`, or has a `scenario` fault, leaves the criterion met and goes to **Before you decide**
as a medium doubt ("Nobody ran this step: it needs {need}"), because no fix round can install a login.

## 4. The card

A shape brief with no human round (`.claude/skills/impeccable/reference/shape.md`): every choice is an assumption until
`/impeccable shape` runs with the owner. The owner, often neither the product's user nor a developer, must learn what
they approve, try the main journey once in the time the card states, and say what went wrong without declining.

**Layout.** In `Body` (`decision.tsx:1014`), a new `Walkthrough` component replaces `TryIt` and the `Checks` part of
`MergeReview` for merge cards, at the same place (`:1053-1054`). The 0.4a **Where this sits** block stays above
`ReportHead`, compact, so the two never stack. Order:

1. **What this is.** `about.what`, then "For {who}. {problem}", two lines, no heading chrome.
2. **Before you try.** What the owner needs (the required `use.needs`, plus "a terminal: Git Bash or WSL on Windows"
   for a CLI), the measured time ("The agent's run took {setup} to install and {steps} for the journeys"), and the
   setup commands (clone, then `setup`) with their captures folded.
3. **Try it.** One block per journey, P1 first and open, the rest folded. Heading is the title. Under it, "You play
   {persona}. {why}" and `brings`, with the `seed` file folded and a copy button when there is one. Then numbered
   steps. Each step shows `do` in bold, `why` muted, the action in a mono line with a copy button (a link to
   `http://127.0.0.1:4173{path}` for `open`), **You should see** with `see`, and the capture under "What the agent got
   on this build", with its state chip and `looks_like` above it. **Copy** and **Something went wrong** end the step.
   A journey ends with its `success` line and **Copy all steps**.
4. **Already checked by the agent.** The fold of 2.5.
5. **Before you decide**, built items and code, as today (`MergeReview.tsx:135-137`).

**States.**

| State | What shows |
|---|---|
| Passed | Chip "Matched" (icon and word), the capture, `looks_like` |
| Differs | Chip "Didn't match", the capture, then the fault line and `hint` |
| Not run | Chip "Not run", the reason line, no capture box. Never a green state |
| Not judged | Chip "Not judged", the capture, "The product agent didn't say whether this matches." |
| Another version, legacy | The line from 2.4 above the journeys; legacy shows the old command block in **Try it** |
| Decided | Same content, without **Something went wrong** |

**Long output, widths and themes.** The capture box shows 12 lines and "Show all {n} lines", and a cut capture says
"{k} lines cut" between head and tail. The box scrolls sideways inside itself (`overflow-x: auto`), so a long line
never scrolls the page. Volatile spans get a dotted underline and one legend line. A `shot` is a thumbnail that opens
full size, like `dp-shots` (`decision.tsx:432-441`). Below 900px the action line and the two links wrap under `do`.
Colours are existing tokens (`chip-*`, `panel`, `line`), so dark mode follows `:root[data-theme="dark"]`, and a chip is
never colour alone.

**Something went wrong.** The link calls a new `useDock().ask(subject, about)` (`Dock.tsx:14-27`). It opens the dock on
`{kind: "request", ref: <card id>}`, which `chat_tags` turns into the build's conversation with the engineer
(`chat_tags.py:95-97`, `:167`). The composer shows a chip "Step 2: {do}" and a catalog draft, "I tried step {n} and
got something else. What I see:". The owner can paste what their terminal printed, and `chat.send` scrubs it as today.
The message carries `about: {"kind": "walkthrough_step", "step": "JN1.2", "head": "<sha>"}`. Chat can answer or start
a fix round, which sets the card aside (team chat design). It never declines, approves or merges.

**Copy.** Console area `web/src/i18n/areas/decide.ts` under `decide.wt.*`, Korean per KOREAN-STYLE.md, glossary words
only (개발 작업 for build, 명세, 에이전트, 팀 대화, 결정).

| Key | English | Korean |
|---|---|---|
| `decide.wt.what` / `decide.wt.before` | What this is / Before you try | 무엇을 만들었나요 / 써 보기 전에 |
| `decide.wt.for` | For {who}. {problem} | 쓰는 사람: {who}. {problem} |
| `decide.wt.time` | The agent's run took {setup} to install and {steps} for the steps. | 에이전트가 돌렸을 때 설치에 {setup}, 단계에 {steps} 걸렸어요. |
| `decide.wt.play` | You play {persona}. {why} | 오너님은 {persona} 역할이에요. {why} |
| `decide.wt.see` / `decide.wt.got` | You should see / What the agent got on this build | 이렇게 보이면 돼요 / 에이전트가 이 개발 작업으로 실제 돌린 결과 |
| `decide.wt.state.passed` / `differs` / `not_run` | Matched / Didn't match / Not run | 맞음 / 다르게 나옴 / 돌리지 못함 |
| `decide.wt.not_run.needs` | Not run: it needs {need}, which the agent's sandbox never gets. You can run it; nobody has seen its output yet. | 돌리지 못했어요. 필요한 것({need})이 에이전트 샌드박스에는 없어요. 오너님 컴퓨터에서는 돌릴 수 있지만 아직 아무도 결과를 보지 못했어요. |
| `decide.wt.fault.product` | Looks like a problem in the build. | 만든 코드의 문제로 보여요. |
| `decide.wt.fault.scenario` | The product agent thinks the step itself is wrong. | 제품기획 에이전트는 단계 설명이 틀렸다고 봐요. |
| `decide.wt.volatile` | Times and ids differ on every run. | 시간과 ID는 돌릴 때마다 달라요. |
| `decide.wt.wrong` / `decide.wt.copy_all` | Something went wrong / Copy all steps | 다르게 나와요 / 단계 전체 복사 |
| `decide.wt.checked` | Already checked by the agent · {n} checks, {m} passed | 에이전트가 이미 확인한 것 · {n}개 중 {m}개 통과 |
| `decide.wt.legacy` | This build was checked before builds recorded what they are for. These commands come from the product's files; nobody ran them as a walkthrough. | 이 개발 작업은 무엇을 위한 것인지 기록하기 전에 확인했어요. 아래 명령은 제품 파일에서 옮겨 왔고, 아무도 따라 해 보며 돌리지 않았어요. |
| `chat.about.step` | Step {n}: {do} | {n}단계: {do} |

The glossary gains `User journey` / `사용 흐름` (code name `journey`): "How one kind of person uses the product from
start to finish: who they are, why they came, each step, and what they see when it worked." Regenerate `term.ts` and
PRODUCT.md's table. `{who}` and `{need}` can end in a Latin letter, so the Korean puts a fixed word around them
instead of a particle (`tests/test_i18n_particles.py`).

## 5. Product kinds

| Kind | Persona | Bound action | Capture | Owner's way to try |
|---|---|---|---|---|
| CLI (uxscope) | Whoever runs it, often the owner | `run` | stdout and stderr, exit, `show` file head | Copy each line into a terminal |
| Screens (web app) | The product's user | `open` path, and `run` for a seeded state when the spec needs data | A 1440 capture at that path after setup and earlier `run` steps | Open the address after `sh scripts/preview.sh`, then do the step |
| Library | A developer who imports it | `run` of an example file the branch ships (`python examples/first.py`) | stdout of the example | Copy and run the example; the card says it is for developers and the owner plays one |
| Service with no UI | The program or person that calls it | `setup` starts `scripts/preview.sh` in the background, then `run: curl -s 127.0.0.1:4173/...` | Status line and the first lines of the body | The same `curl` lines after starting the preview |

For screens, v1 does not click. The capture is the state the engineer exposes at a path, and the chip area says "The
agent opened this screen at this point; it did not click through." Driven clicks (a small action list run by a
worker-owned browser script) are a later step that needs its own design: the engineer image has chromium and CJK fonts
but no Playwright or Puppeteer (`docker/engineer.Dockerfile:18`), and the egress allowlist blocks a browser download.

The owner is often not the persona, so the card always says who they play ("You play Mina"), taken from the spec the
owner approved. When the persona is a developer (a library), **Before you try** says so first.

## 6. Safety and failure modes

1. **Secrets and machine paths in captures.** `walkthrough.clean` decodes as UTF-8 with replacement, drops ANSI escapes
   and control characters other than newline and tab, maps the journey folder and `/work` to `./` and `HOME` (`/tmp`,
   `worker.py:1464`) to `~`, then runs `secrets_scan.scrub` (`contracts/secrets_scan.py:54`) with a neutral marker
   `[secret:<kind>]` the console words from the catalog. It runs before the capture is stored, so no copy reaches the
   database, an event, a prompt or a log, as in chat (`chat.py:5-7`). The container holds no token or connector
   credential, so a capture can leak only what the branch contains. Terminal output never goes through
   `owner_text.one_line`, which drops code names and newlines (`owner_text.py:155`).
2. **Prompt injection from product output.** The acceptance agent reads captures fenced (`services/fence.fenced`) and
   cannot change `held`'s rules. The card renders them as plain text in a `<pre>`, with no markdown or auto-links. Chat
   reads the step through the same fence as `merge_review.card` today (`chat_reply.py:64`). `looks_like` and `hint`
   pass through `OneLine` and are labelled as the agent's.
3. **A capture the engineer controls.** The engineer picks the commands, not the output. The worker runs them, the lint
   refuses a `run` that only prints, and the nonce frames keep a background process from writing a step's record. The
   product itself can still print "Success" without doing the work. Its real `show` file then shows the gap, and the
   agent judges it. The worst case is an honest capture of a weak command, visible to the owner.
4. **Flaky output.** Timestamps, ids, durations, temp paths and ports differ between the agent's run and the owner's.
   The card never compares exactly. `clean` marks spans matching ISO dates, clock times, UUIDs, hex runs of 8 or more
   with a digit, and `\d+ ?(ms|s)` as volatile, and the owner compares by `see` and `looks_like`. A product that is
   nondeterministic in content (an LLM step) gets "Your output will differ in wording" from the agent's `hint`.
5. **Wrong expected output after a fix round.** Each delivery queues an acceptance (`worker.py:1334-1335`), which
   writes a record under the new head. A card reads the record for its own head (2.4), so a fix never shows the old
   output as current, and an older card keeps the output it was decided on.
6. **Collisions with 0.3 and 0.4a.** 0.2b adds no migration, so it never competes for the `down_revision` that both
   designs set to `7c1e5a9d3f20_team_chat`. It files no new request and leaves `_request_merge` (`worker.py:1906-1919`)
   and `requests.create` alone, which 0.4a changes (`origin`, `hold_scope`, the merge row of `ORIGIN_RULES`). 0.4a adds
   request facts to chat (`work-path.md` 4.5) and 0.2b adds `about` to messages. Both edit
   `chat_reply._subject_records`, a small conflict for whichever lands second, and all three add a glossary term.
   0.3's "who buys" is the business's customer and `about.who` is the product's user. 0.2b reads no 0.3 table.
7. **Cost and time.** No new model call. The acceptance call reads at most 24,000 more characters of captures, which I
   guess at 6,000 to 8,000 tokens. The walkthrough container installs once more, beside the core flow and the screen
   capture, inside the existing 1200-second cap. I guess a minute or two for an `npm install` and measured none; the
   card shows the times the run measured. The spec call writes up to 3 journeys more.
8. **A step needs something the sandbox lacks.** uxscope lists a Claude CLI login as optional. The sandbox never gets
   one, so such a step is `not_run: needs`, never passed. A binary `show` file reads "{name}: a binary file, {size}".

## 7. Phases

Each unit ends in a check, with only the named tests (`uv run pytest -q tests/<file>`).

1. **Contracts and the spec writer.** `AboutOut`, `JourneyOut`, `ScenarioOut.guards`, `CriterionResultOut.about`,
   `journeys.yaml`, the `SPEC_ASK` paragraph, `journeys_problem`, `test_words.py`, journeys on the spec card. Check: new
   `tests/test_journeys.py` (a draft with "generate를 두 번" in a step goes back once; ids number like stories; 1 to 3
   journeys, 2 to 8 steps), `tests/test_spec_kit.py`, `tests/test_i18n.py`.
2. **The binding and its lint.** `walkthrough.lint`, the engineer sentence, the lint fed back. Check: new
   `tests/test_walkthrough_binding.py` (unbound step, unknown id, `echo` refused, `show` outside refused).
3. **Run and capture.** `walkthrough.runner`, `walk`, `clean`, `open` steps through the screen capture. Check: new
   `tests/test_walkthrough_capture.py` on a recorded container stdout, no Docker needed (every shape in `secrets_scan`
   scrubbed, `/work` and `/tmp` mapped, ANSI gone, head and tail cut, volatile spans, a frame with the wrong nonce
   ignored, a link `show` refused, each `NotRun` reason).
4. **Verdicts and the gate.** `AcceptanceOut.journeys`, the prompt section, `held`, the journey criterion. Check: new
   `tests/test_walkthrough_verdict.py` (no capture never passes; a mismatched exit differs; an unproven environment
   becomes product; a `needs` step does not fail the gate), `tests/test_merge_gate.py`, `tests/test_engineering.py`.
5. **Read model.** `merge_review.card(..., head)`, `walkthroughs` keyed by head, `checked` groups, the legacy shape.
   Check: new `tests/test_merge_card_walkthrough.py` (a REQ-7-shaped legacy job gives `walkthrough: null` and five
   groups; a new job gives every field; a card reads its own head), `tests/test_i18n_merge_review.py`,
   `tests/test_i18n_server_words.py`.
6. **Console.** `Walkthrough.tsx`, `decide.wt.*`, the glossary term. Check: `tests/test_i18n.py`,
   `tests/test_korean_style.py`, `tests/test_i18n_particles.py`, `cd web && npx tsc -b && npm run build`. Then the owner
   opens a new merge card at http://127.0.0.1:8000 in both languages and themes at 1440 and 900, and
   `/impeccable critique` writes into `.impeccable/critique/`.
7. **Chat.** `about` on `POST /api/chat/messages`, checked in `chat.send` (the step must exist in that build's record
   for that head), kept in `AgentMessage.data`, fenced into the reply prompt; `useDock().ask`. Check:
   `tests/test_chat_store.py`, `tests/test_chat_reply.py`, `tests/test_chat_routes.py`, plus a new case that an
   unknown step is refused and that `about` never adds an intent (`chat_tags.allowed` unchanged).

Exit criteria for 0.2b, fixed before it starts:

- A new spec cannot be approved without at least one journey, or the card says why it has none.
- On a `[TEST]` CLI build, the merge card shows what it is, what is needed, the measured time, and every P1 journey step
  with a real capture or a `not_run` reason. No step without a capture reads as passed.
- Developer tests are only under **Already checked**, grouped, each with one line of purpose.
- **Something went wrong** opens the build's conversation with the step attached, and nothing in chat can merge.
- REQ-4 and REQ-7 still render, with the legacy lines of 2.4.
- The owner follows a real merge card's P1 journey and says it is understandable.

## 8. Design alternatives considered

Two whole shapes were sketched in parallel by two runners. In one, the worker runs bound steps. In the other, a tester
agent with a shell walks the journeys while the runtime records its tool results. The base is the first, and from it
come the fresh export, the nonce frames, `seed` and the `JN` ids, which keep a step from reading like build `J-12`.
From the second come `brings`, the sandbox signals for "environment", and the rule that only the server may accept
that fault.

| Question | Options | Choice | Why |
|---|---|---|---|
| Where user journeys come from | written at acceptance by the product agent; derived from P1 scenarios; written in the spec | The spec | The owner approves them before building, and the checker does not write its own test |
| What "you should see" is | the spec's prediction only; the raw transcript only; the spec's `see` beside the real capture with the agent's verdict | `see` + capture + verdict | A prediction alone can be invented, a transcript alone means nothing to a non-developer |
| Who runs the steps | an engineer script printing markers (core-flow style); a tester agent with Bash; the worker running bound lines | The worker | A script can print anything. An agent container carries `CLAUDE_CODE_OAUTH_TOKEN` (`runtime/claude_cli.py:189`), so the product's code would run beside the owner's token. It also costs a model call per acceptance and varies run to run. The worker's run is cheap and repeatable |
| Where the run lives | a new table; files under `var/`; `DevJob.result` keyed by head | `DevJob.result` | No migration beside 0.3 and 0.4a, and it commits with the verdict. Three heads of at most 24,000 characters each stay small |
| Comparing outputs | exact; masked before storing; marked spans and a "looks like" line | Marked spans | The owner sees the real output, and nothing claims an exact match it cannot keep |
| Screens | driven clicks now; the state at a path, labelled | Path, labelled | It reuses `_capture`. Clicks need a browser script and their own design |
| Asking about a step | a prefilled message only; a structured `about` | `about` | The reply agent gets the exact step and capture, not the owner's retelling |
| What fails the gate | any step that differs; product faults in P1 journeys | Product faults | A missing login or a wrong step is not something a fix round can repair |

## 9. Open questions for the owner

1. **May the team write journeys for REQ-7 now?** Its spec has none and its branch has no binding. Recommendation:
   yes, one command, `ship dev walkthrough J-<n>`. The product agent writes journeys from the approved spec's stories,
   labelled "written at the check, not in the approved spec". A fix round has the engineer add only
   `scripts/walkthrough.yaml`, and the acceptance check runs it, which files a new merge card. That is one spec-sized
   call plus a fix round, which needs `fix_budget_usd` (6, `config/company.yaml:58`) left. Without it, REQ-7 keeps the
   legacy card.
2. **Should the agent run a step that needs your Claude login, outside the sandbox?** Recommendation: no. The card says
   the step was not run and why, and you run it yourself. Running the product's code next to your login is the risk
   the sandbox exists to avoid.
3. **Must every new spec have a journey before you can approve it?** Recommendation: yes, with the card saying why when
   the writer could not produce one after one retry, so you decide to approve without it.

## 10. The 0.2b ROADMAP row

The row (`docs/ROADMAP.md:26`) is right on what the card shows. It leaves out three conditions this design needs to be
checkable: a step the agent could not run never reads as passed, a failed step says whose fault it looks like, and old
cards still read. Rule 7 allows one shaped next step, and with 0.3 already designed, 0.2b and 0.3 are both shaped
while 0.2 is open. The owner placed 0.2b, so I only note it. The proposed third cell:

```
머지 카드 하나만 읽고 이 제품이 무엇이고 누구에게 어떤 문제를 풀어 주는지 이해한다. 써 보기 전에 필요한 것, 단계별 입력과 예상 출력(에이전트가 이 개발 작업으로 실제 돌려 저장한 출력), 에이전트가 잰 시간이 보인다. 시나리오는 명세에 적은 실제 사용자 흐름(누가, 왜, 단계, 성공했을 때 보이는 것)이고, 개발 시험은 "에이전트가 이미 확인한 것"으로 목적 한 줄과 함께 접힌다. 에이전트가 돌리지 못한 단계는 이유와 함께 "돌리지 못함"으로 보이고 통과로 보이지 않는다. 다르게 나온 단계는 만든 코드 문제인지, 단계 설명 문제인지, 빠진 준비(로그인 등) 때문인지 보인다. 막히면 카드에서 팀 대화창으로 해당 단계를 붙여 보낸다. 예전 머지 카드(REQ-4, REQ-7)는 기록된 것만 보인다. 오너가 실제 머지 카드로 따라 해 보고 이해된다고 확인한다
```

## What I could not verify

I did not open the console, so the layout and copy are unchecked on screen. I did not read REQ-7's row or its job's
`result`. The 27 criteria, the 8 commands and the needs come from the card's API data the owner's session read. I did
not run a container, so the time and token figures in 6.7 are estimates. I did not check whether REQ-7's spec has a
plan or stories file, which question 1 depends on.

## Built

Built on `feat/merge-card-walkthrough` in the seven phases of section 7, with no migration. The owner's answers to
questions 2 and 3 are in: a step that needs a login the sandbox never gets is `not_run: needs`, and a spec whose writer
gives no journey after one retry says so on its card. `ship dev walkthrough J-<n>` (question 1) is not built. It needs
a spec-sized model call, journeys written outside an approved commit, and a fix round from the fix budget, so it is not
a cheap or safe unit. It needs its own design. Where the build differs from this design:

- **journeys.yaml.** Numbering and reading live in a new module, `engineering/journeys.py`, beside `stories.py`, not
  in `specs.py`. The file also holds `missing` (`none`, `too_many`, `steps`, `see`, `test`), so every path that raises
  a spec card (the first draft, a redraft, the card after the owner's answers) reads the same reason.
- **Retries.** A draft is asked again at most once in total, with the no-P1 note and the journeys note together. A
  second draft that loses the P1 story the first had keeps the first, so a retry for journeys never fails a spec.
- **Schema size.** `SpecDraftOut`'s new field descriptions are short, because its schema passed the Windows
  command-line budget that `tests/test_wireflow.py` guards.
- **The record's types.** `Capture` gains `cut_after` (where the head ends) and `file` (the `show` file's head, or
  that it is binary). `StepRun` gains `kind`, `expect`, `seed` and `judged`. `result["walkthroughs"]` is a list, newest
  last, because a JSONB object keeps no key order.
- **Frames.** Each frame is sealed with an HMAC of its step id under the run's nonce, and the nonce never enters the
  container. A process running as the same user can read the container's stdout through `/proc/1/fd/1`, so one stolen
  frame would have given away a run-wide marker. The runner arrives on stdin, runs on a system-only `PATH`, mounts
  `/work` read-only, and stops every other process while it prints a frame. Without that stop, an open step's frame
  was lost in 2 of 6 real runs; with it, in none of 18.
- **Times.** A setup line runs under what is left of the container's budget, not 90 seconds, because an install takes
  minutes. A step whose exit does not match its `expect` stops its journey at run time, and the rest are
  `earlier_failed`.
- **Screens.** An open step's PNG is kept under `var/dev-walk/J-<job>/<head[:12]>/` with its own route, because
  `capture_screens` empties `var/dev-screens/J-<job>` at every acceptance. It is copied into the export at
  `.ship/walk/` after the branch's scripts ran, so the product agent can open it.
- **held.** For `expect: error`, an exit of 124, 127 or 137 is not the refusal the step expects. A captured step with
  no verdict whose exit is against its `expect` stays "not judged" on the card. It is the product's fault by the
  server's word, so an agent that says nothing cannot let a broken must-have journey through the gate.
- **After an independent review.** The runner prints a sealed start frame before any branch code, and every frame
  on a line of its own. A run that started and sent no step frame is `out_of_time`, never `no_docker`, so a product
  cannot garble its way past the gate. The memory signal comes from the cgroup's `oom_kill` count, not from exit 137,
  which `timeout -k` and `kill -9` also give. `unreachable` (a preview that never answered) fails the journey like
  the other faults a fix round can repair. A binding with YAML anchors or invisible characters is refused, and a
  bound line is scrubbed of secrets before it is stored.
- **Chat.** A merge card's chat subject is its build (`chat_tags.resolve`), so `about` is accepted on a `dev_job`
  subject whose record for that head holds the step. The message keeps `{kind, step, head}`. The reply reads the
  step from the record again, inside a fence.
- **Console.** The owner's Works/Problem mark on each check went with the old Checks list. The doubts the owner ticks
  still write the decline note. The legacy line is split into one sentence under What this is and one under Try it.

Unverified: no browser opened the card in either language or theme, the walk has not run end to end through the
egress proxy, and no real build has been accepted with journeys. REQ-4 and REQ-7 render through the legacy path in
tests only.
