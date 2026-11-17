# Cutover to the new names (aiteam -> ship)

The code, the command (`ship`), the env prefix (`SHIP_`) and the Docker names changed in one step. The data did not move:
the Postgres and Redis volumes keep their old names (`aiteam_pgdata`, `aiteam_redisdata`), pinned in `docker-compose.yml`, so
`docker compose up` under the new project name `ship` mounts the same data. What still has to be renamed by hand, once,
is the database and its role. `scripts/cutover-rename.sh` does that.

## What changed for the owner

| Was | Is |
|---|---|
| `uv run aiteam ...` | `uv run ship ...` |
| `AITEAM_*` in `.env` | `SHIP_*`. The old key is still read when the new one is missing, for one release. `ship init` rewrites the keys without reading the values. |
| database and role `aiteam` | database and role `ship` (cutover script) |
| compose project `aiteam` | `ship`. Containers `ship-postgres-1`, `ship-redis-1`, `ship-egress`, networks `ship_default`, `ship-sandbox`, `ship-outbound` |
| images `aiteam/engineer`, `aiteam/analysis`, `aiteam/egress` | `ship/...` (`docker tag` or rebuild, below) |
| cookie `aiteam_token`, header `X-AITeam` | `ship_token`, `X-Ship` (the old header is still accepted for one release). One re-login. |
| Redis keys `aiteam:*`, labels `aiteam.owner` | `ship:*`, `ship.owner`. Short-lived; nothing to migrate. |

## Steps, in order, with the app stopped

1. Stop the worker and the console (Ctrl+C in the `supervise` window, or end its process). Wait for any engineer container
   (`aiteam-eng-*`) to finish; a job in flight is given back to the queue and runs again.
2. Cutover the database. Postgres must still be up, from the old stack:
   `bash scripts/cutover-rename.sh --dry-run`, then `bash scripts/cutover-rename.sh`.
   It refuses while sessions are open on the old database, writes `var/backups/pre-rename-<time>.dump` and reads it back,
   renames database and role through a temporary superuser, sets the role's password (from `.env`, else the default
   `ship`), and checks a password login. Running it again changes nothing.
3. Take the old stack down, keeping the volumes: `docker compose down` in the checkout. Never pass `-v`.
   (It leaves `aiteam-sandbox` in place if an old engineer container is still attached; remove that container first.)
4. Merge `refactor/rename-ship`, then `uv sync` and `cd web && npm run build`.
5. Bring the stack up under the new name: `docker compose up -d`. Compose prints a warning that `aiteam_pgdata` "was created for
   project aiteam". That is expected: it is the pinned volume, and the data in it is what comes up.
6. Sandbox images: `docker tag aiteam/engineer:4 ship/engineer:4` and `docker tag aiteam/analysis:1 ship/analysis:1`
   (or `uv run ship sandbox build`).
7. `uv run ship init` rewrites `AITEAM_*` keys in `.env` to `SHIP_*`, runs the migrations (none are pending) and checks both
   services. Then `uv run ship doctor` should be all green.
8. If the Windows logon task was installed: `uv run ship supervise --install` (its arguments change to `-m justshipit.cli`; the
   old task names are replaced).
9. `uv run ship supervise --concurrency 4 --port 8000`, then `uv run ship api --login` for a login address.

## Undo

The dump from step 2 restores into a fresh database (`pg_restore -U <role> -d <db> --clean --if-exists`). Before step 3 the
rename can be reversed in place: `ALTER DATABASE ship RENAME TO aiteam; ALTER ROLE ship RENAME TO aiteam;` through a second
superuser, as the script does.

## Testing before the cutover

`scripts/test-linux.sh` finds the compose network by itself (`ship_default`, else `aiteam_default`). Until the role is
renamed run it as `SHIP_TEST_PG_USER=aiteam SHIP_PG_PASSWORD=aiteam scripts/test-linux.sh`.

## Leftovers that are meant to stay

`AITEAM_` is read by `settings.py` and `credentials.py` for one release; `x-aiteam`, `aiteam.source`, `.aiteam/` are accepted the
same way; `aiteam_pgdata`/`aiteam_redisdata` are the pinned volume names; `OLD_TASK_NAMES` keeps "AI Company OS"; this file, the
cutover script and the migration history keep the old names on purpose.
