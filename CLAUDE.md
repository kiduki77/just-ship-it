# Just Ship It: working agreement

Product context: [PRODUCT.md](PRODUCT.md). Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Hard constraints

- Use the Claude **subscription CLI only** (`claude -p`). Codex is retired as a provider (owner decision, 2026-11-03).
  Never add LLM API keys or API-key code paths.
- Connector credentials (Slack, Gmail, GitHub, Cloudflare) come from Settings and are never printed, logged, or passed
  on a command line.
- Never print, log, or pass tokens on a command line. The engineer container gets only the short-lived access token,
  passed by environment-variable name (`runtime/host_auth.py`).
- External connectors default to outbox. Only the owner turns a connector live, in Settings.
- Approve Approval Center requests only when the owner explicitly asks in that turn. Record
  `responded_by="claude (오너 위임)"`.
- Don't hard-delete user data. Archive projects instead (`ship project archive`).

## Development workflow (pstack)

pstack is Lauren Tan's skill stack, installed as the Claude Code plugin `pstack@cursor-plugins`
(cursor/plugins). That build has no SessionStart hook, so the Route rule below is the only routing. Its entry point is `/pstack:poteto-mode`, which picks a playbook (Feature, Bug fix,
Investigation, Refactoring, Prototype, Multi-phase plan, ...) and verifies every step. `no-session/pstack` (a gstack
fork with `pstack-*` skill names) is not pstack. It is parked in `~/.claude/skills-disabled/`.

`main` is the baseline. Do every change on a branch (`feat/…`, `fix/…`, `ui/…`) and never push without being asked.

1. **Route.** Pick the lightest tier that fits. When unsure, start one tier lower and escalate if a check fails.
   - **Direct** (no pstack, no other process skills, no subagents): questions; one-file changes; multi-file changes
     that follow an existing pattern (add a field across model, schema, API, and test); copy or CSS tweaks; bugs whose
     cause is known. Verify with targeted tests only.
   - **`/pstack:poteto-mode`**: a new design choice, module, or abstraction; a signature change other code calls; a
     bug of unknown cause; a performance issue.
   - **Multi-agent** (`pstack:arena`, `pstack:interrogate`, `pstack:swarm`, `pstack:architect`): only when the owner
     asks, or the change touches auth, sandboxing, the tool gateway, or migrations.
   - The owner's word wins: "pstack으로" forces poteto-mode, "바로 해" forces Direct.

   New screens and layout changes also go through `/impeccable shape` before code.
2. **Build.** Match the surrounding code. Add a regression test for every bug fix (`uv run pytest -q tests`).
3. **Review.** Use the review that the playbook names (`pstack:interrogate`, `pstack:thermo-nuclear-review`).
   Use `pstack:arena` or a second Claude model (for example opus reviewing sonnet) on risky changes.
4. **Security.** Run `/security-review` before the open-source release and after changes to auth, sandboxing, or the
   tool gateway.
5. **Verify.** For console changes, rebuild (`cd web && npm run build`) and check the real screens at
   http://127.0.0.1:8000 in the browser.
6. **Land.** Work lands locally. Where a playbook says to open or land a PR, commit on the branch instead, report
   the change in the PR-description format, and merge to `main` locally only when the owner agrees.

## UI work (impeccable)

The console is an **Operate** surface: decisions first, calm under long runs, and the confidence of every claim
visible. Use the impeccable skill (`.claude/skills/impeccable`). Its design hook checks UI files after each edit.

- `/impeccable critique` and `/impeccable audit` to evaluate. Snapshots go in `.impeccable/critique/`.
- `/impeccable polish`, `layout`, `clarify`, and `harden` to fix. Finish with `/impeccable polish`.
- English by default, Korean as the other choice (Settings > Language). Every owner-facing string, in the console
  and in the server, comes from a catalog in both languages: `t()` in `web/src/i18n`, `msg()` in
  `backend/justshipit/i18n`. `tests/test_i18n.py` fails on Korean outside the catalogs. See [docs/I18N.md](docs/I18N.md).
- Use the glossary's words (`backend/justshipit/i18n/glossary.py`, repeated in PRODUCT.md), never the retired ones
  (지시서, 도전, 결정함, 오너 할 일, 첫 가치, 사다리). Never assume string length in either language.

## Running

```bash
docker compose up -d
uv run ship supervise --concurrency 4 --port 8000   # worker + console: http://127.0.0.1:8000
```

Stop the worker before running Alembic migrations, and review autogenerate output for dropped LangGraph checkpoint tables.
