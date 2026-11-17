# Development guide

[한국어](DEVELOPMENT.ko.md)

How to work on the Just Ship It repository itself. [CLAUDE.md](../CLAUDE.md) holds the working agreement: the hard
constraints, the branch rules, and when to use which pstack skill.

## Set up Claude Code for this repository

Development uses pstack, Lauren Tan's skill stack, as the Claude Code plugin `pstack@cursor-plugins`. In Claude Code,
run `/plugin marketplace add cursor/plugins`, then `/plugin install pstack@cursor-plugins`. Start a change with
`/pstack:poteto-mode`.

The engineer container that builds products for the company is separate. Its image installs a pinned commit of
`michael-denyer/pstack-claude` (see [the operations guide](OPERATIONS.md#the-engineer-sandbox-signs-in-with-your-subscription)).

## Work on the console

After you change or pull the console, rebuild it:

```sh
cd web && npm install && npm run build
```

For development mode, run `npm run dev` in `web/`. It serves on :5173 and proxies `/api` to :8000.

## Run the tests on Windows

On a Windows host, a full `uv run pytest -q tests` run is not reliable, because the tests assume Linux.
`test_worker_recovery.py` kills pytest itself with `os.kill(os.getpid(), SIGTERM)`. `os.fchmod`, `git init`, the fake
`claude` and `docker` scripts that start with `#!/bin/sh`, and the case of `https_proxy` also behave differently on
Windows. So run the full suite in Linux containers, and run only one or two files on the host.

```bash
docker compose up -d                                  # Postgres
scripts/test-linux.sh                                 # the whole working tree, uncommitted changes included
scripts/test-linux.sh <commit or worktree path>       # that commit only (exported with git archive)
scripts/test-linux.sh --shards 1 <commit>             # in one container (the default splits into 3 in parallel)
scripts/test-linux.sh <commit> tests/test_queue_lease.py -k expiry   # later arguments go to pytest
scripts/test-linux-diff.sh [--shards K] <base> <head>   # PR gate: the tests that newly fail on head
```

The script streams the source into the container, so the host's `.venv`, `.env`, and file locks stay out of it. Each
run uses its own test database. Inside the container, 127.0.0.1:55432 leads to the compose Postgres, so any commit's
`conftest.py` runs as it is. A commit from before Redis was removed fails its Redis tests. At the end it prints the
passed, failed, error, and skipped counts and the ids of the failed tests. The results (`junit.xml`, `failures.tsv`) stay in `.test-linux/`.

`test-linux-diff.sh` runs the base and the head together and sorts failures into NEW (fail only on head), FIXED, and
PRE-EXISTING. It runs each NEW test again alone on head. If it passes alone, it is listed apart as LOAD SENSITIVE,
because load made it flaky. The container image is `scripts/test-linux/Dockerfile` (Python 3.13, git 2.4x or newer, uv,
and the docker CLI). It downloads the dependencies only the first time.

A full run splits the test files across K=3 containers by default (`--shards K` or `SHIP_TEST_SHARDS`, and
`--shards 1` for one). Each shard has its own test database, and no file runs in two shards. The split
balances by each file's time from the latest full run's `.test-linux/**/junit.xml`, or by file count when there is
none. At the end the script merges the junit files and `failures.tsv` and prints one RESULT line. If you pass pytest
arguments, it does not split. It does not use in-process parallelism (xdist), because the tests share module-level
state, the queue, and the var directory.

`scripts/dev_pipeline_smoke.py` checks the development pipeline end to end with a [TEST] project.

## How a goal flows through the code

```
Owner goal ─► CEO plan (DAG) ─► [plan gate: blocking questions] ─► tasks run in parallel
   ─► each task reviewed by a different role from its author (all Claude) ─► rework (up to N times)
   ─► CEO review ─┬─ disagreement → meeting (positions, rebuttals, decision recorded) ─► follow-up tasks
                 ├─ criteria not met → another round of follow-up tasks (up to N)
                 └─ done → final decision ─► [final gate: approval] ─► final report
   ─► needs development → the CPO writes the product plan (plan.md) ─► (approval, dev.plan_approve) the plan commit
      is pinned ─► the CPO writes the spec from it ─► (approval) the spec commit is pinned
   ─► engineering: intake → pstack:architect → build and test → pstack:thermo-nuclear-review + independent review
      → fix loop → guarded ship (PR or local branch) → CPO acceptance check → (strong approval) merge
```

[ARCHITECTURE.md](ARCHITECTURE.md) (in Korean) explains each step and the decisions behind it.

## Directory layout

```
backend/justshipit/
  contracts/      # technical contracts: work (tasks, evidence), requests (Agent Request Protocol), handoff (spec to code), skills, planning
  runtime/        # the claude -p adapter (structured output, isolation, retries, concurrency)
  agents/         # the agent executor (prompt assembly, budget, ledger)
  orchestration/  # the LangGraph company graph, context builder, run and resume
  services/       # policy, requests (approvals), budget, queue (outbox), tasks (DAG), evidence, finance, registry
  engineering/    # specs (PRD, approval, pinning), worker (pstack plugin pipeline, acceptance check, merge)
  capability/     # Skill Factory (gap → proposal → package → safety → replay A/B → promotion → injection)
  operations/     # scheduler, gateway (connectors), jobs (change watch, KPI review, portfolio review, reassess)
  workers/        # the job worker
  api/ · cli.py
config/           # company.yaml (limits, autonomy), agents.yaml (org, models, tools), tool_policy.yaml
prompts/          # shared principles and per-role prompts (the version hash goes into the call ledger)
registry/         # (separate git) plans, task output, meeting notes, reports, specs, skills
products/         # (each its own git) the repositories the company builds
web/              # the React console
scripts/          # dev_pipeline_smoke.py and the Linux test runners
var/calls/        # each call's raw prompt and output, for audit (gitignored)
var/outbox/       # the outbox for external actions (unsent mail as .eml, events as .ics, survey packages)
```
