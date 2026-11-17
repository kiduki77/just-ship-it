# Operations guide

[한국어](OPERATIONS.ko.md)

How to keep Just Ship It running on your PC, sign in to the console, and look after the sandboxes. The
[README](../README.md#get-started) covers the first setup.

## Run it on a PC no one watches

One command runs the worker and the console. If either exits, `supervise` starts it again. If it exits again right
away, for example while the database is still coming up after a reboot, the wait grows to 5 seconds, 15 seconds,
1 minute, and then 5 minutes.

```sh
uv run ship supervise                # the worker (4 jobs at once) and the console (:8000). Ctrl+C stops both
uv run ship supervise --install      # register the Windows logon task "Just Ship It", which runs the line above at every sign-in
uv run ship supervise --uninstall    # remove that task
uv run ship backup                   # make one database backup in var/backups/ now (the worker makes one a day)
```

- To come back on its own after a reboot, run `--install` once and turn on **Start Docker Desktop when you sign in**
  in Docker Desktop. The Postgres container uses `restart: unless-stopped`, so it starts with Docker. The
  task is a logon task, so it starts only after you sign in to Windows. The Claude subscription login lives in your
  user account.
- The first Ctrl+C asks the worker to stop. It stops the calls it is running and puts the jobs back in the queue, so a
  restarted worker continues from the checkpoint at once. If you close the window, or force the exit with a second
  Ctrl+C, the jobs wait until their lease lapses (about an hour).
- Logs are in `var/logs/worker.log`, `var/logs/api.log`, and `var/logs/supervisor.log`. Each rolls over at 10 MB and
  keeps 5 files.
- The console checks the API, the database, and the worker at `/api/health`. If the worker has not reported
  for more than 10 minutes, the console process sends one Slack message (the failure notification in Settings). It
  does not send again for the same outage.
- An install from before Redis was removed: after updating, run `docker compose up -d --remove-orphans` once. It
  removes the old Redis container. The `aiteam_redisdata` volume stays on disk, and you can remove it by hand
  (`docker volume rm aiteam_redisdata`). The `SHIP_REDIS_PASSWORD` line in `.env` is no longer read.
- Backups come from `docker compose exec postgres pg_dump`. The latest 7 are kept (`operations.backups_kept`). To
  restore, stop the worker and the console, then run this command:

  ```sh
  docker compose exec -T postgres pg_restore -U ship -d ship --clean --if-exists < var/backups/<file>
  ```
- An install from before the rename (the `aiteam` command, `AITEAM_` settings) moves to the new names with
  [RENAME-CUTOVER.md](RENAME-CUTOVER.md).

## Log in to the console

The console API needs a token for every `/api` request and for live updates (SSE), whatever address you open it on.
Other programs on the same PC (scripts, `curl`, the engineer container) can fake the Host and any header, so the
address alone proves nothing. The token is created once in `var/api_token` (mode 0600). It never appears in the
address bar or in a log.

1. Run `uv run ship api`, or `supervise`. The terminal prints a login address (`http://127.0.0.1:8000/?login=…`).
2. Open that address in your browser. Its code works once, within 10 minutes. Opening it gives the browser a login
   cookie (HttpOnly, SameSite=Lax, 30 days) and moves to the same address without the code.
3. If the address has expired, or you want to sign in from another browser, keep the server running and get a new
   address with `uv run ship api --login`. If the console says you need to log in, do the same.

Scripts send the token from `var/api_token` in an `Authorization: Bearer` header. Requests that change something also
need an `X-AITeam: 1` header. Read the token from the file, and never pass it as a command-line argument. If you think
the token leaked, run `uv run ship api-token rotate`. A running server picks it up at once, and it refuses every
existing login cookie, the old token, and any login address not used yet.

The login cookie holds a value made from the token, not the token itself. Another port on this PC (a dev server, for
example) that receives the cookie still cannot learn the token. Whoever steals the cookie can still use the whole
console API until you rotate the token. Earlier versions put the token itself in the cookie. When you upgrade from one
of them, the first `ship api` run makes a new token once (recorded in `var/cookie_is_hmac`) and says why in the
terminal. Log in again once when that happens.

The server never ends a login on its own, and the console has no log-out button. A login cookie works until the token
changes (the browser itself drops it after 30 days). To log every browser out, run `uv run ship api-token rotate`,
then log in again with `uv run ship api --login`. Scripts must read the new token from `var/api_token`.

To use the console from another device, prefer a private network such as Tailscale (`--host <Tailscale address>`) or
an SSH tunnel (keep the server on its default and run `ssh -L 8000:127.0.0.1:8000 <this PC>`) over
`ship api --host 0.0.0.0`. Plain http is not encrypted. On a network many people share, anyone on it can read the
login address and the cookie. If a request arrives over https (including `X-Forwarded-Proto` from a proxy in front),
the cookie is marked Secure.

The server refuses, whatever the token, a request that changes something when it comes from another site's page. When
the console listens only on this PC, it also refuses a request with a different Host. The access log never records
query strings.

On this PC the console answers on `127.0.0.1`, not on `localhost`. A page opened as `http://localhost:8000` moves to
`http://127.0.0.1:8000`, and an API call sent to `localhost` is refused. A product's preview opens at
`http://localhost:4173`. A browser sends a cookie to every port of the same host, so the two names keep the console's
login away from a preview, which is code an agent wrote. A proxy in front of the console must send `127.0.0.1` as the
Host.

Every response tells the browser to load scripts, styles, fonts and images from the console alone and never to show
the console inside another page. The pages that draw the API's description (Swagger UI, ReDoc) load code from another
site, so the console does not serve them. The description itself is at `/api/openapi.json` and needs the token.

## The engineer sandbox signs in with your subscription

This needs no setup. The engineer agent runs in a new container (`ship/engineer`) for every call, and the container
sees only that build's clone. Claude Code inside it uses the Claude subscription you are signed in to on this PC. There
is no API key. Only the short-lived access token from the host's `~/.claude/.credentials.json` goes in, as an
environment variable. The refresh token stays on the host, so the container cannot sign the host out. When the access
token is close to expiring, the host's `claude` refreshes it the normal way before passing it on.

Check the state with `uv run ship sandbox check`. A long-lived token made with `claude setup-token`
(`SHIP_CLAUDE_OAUTH_TOKEN`) never goes into the container, because a leaked one stays valid for a year.

Without a login, a build stops at its first step and records why. It never falls back to running on the host. If you
need the old mode (a host worktree with rules that forbid certain commands), set `engineering.isolation: host` in
`config/company.yaml`.

The engineer image installs the pstack plugin at build time, from a pinned commit of `michael-denyer/pstack-claude`.

## Data analysis

The `analyst` agent takes public raw data (statistics as Excel or CSV) and aggregates it with code. The server
downloads each file itself: http or https from a public address only, up to 100 MB a file. The script runs in a
container with no network. The final script then runs again in a fresh container, and if the result matches, the
evidence is marked `reproduced`.

For data behind a login, put the file in `var/data/<project slug>/` and the agent can use it as `local:<file name>`.

## Git version

Version 2.45.1 or newer is recommended. The worker does not go by the version number. It tries the CVE-2024-32465
attack (the same fix as CVE-2024-32004) in a temporary repository. If git does not block it, or git is older than
2.41, the worker takes no container engineering jobs. With git older than 2.41, it still saves registry documents and
logs one warning about it. If the test itself cannot finish, the worker also takes no such jobs, and it tests again
at the next job it accepts. Ubuntu 24.04's git 2.43.0 with security updates passes. `ship sandbox check` shows the
result.

## Command reference

Most work happens in the console. The CLI does the same things from a terminal.

```sh
uv run ship init                     # a random Postgres password (.env), recreate the services, migrations, registry/, checkpointer
uv run ship sandbox build            # the engineer and data-analysis images (once, several minutes; tags in config/company.yaml)
uv run ship sandbox check            # whether everything is ready
uv run ship worker                   # the worker alone: company runs, resumes, specs, builds (supervise runs it for you)
uv run ship api                      # the console alone, http://127.0.0.1:8000

uv run ship project new "Check whether an AI assistant for home-care agencies is a viable SaaS ..." --slug care-saas
uv run ship project list
uv run ship project show 1
uv run ship project report 1
uv run ship inbox                    # decisions waiting on you
uv run ship show REQ-3
uv run ship answer REQ-3 "Agency directors first"
uv run ship approve REQ-7 --note "Go with the MVP scope"
uv run ship approve REQ-9 --confirm REQ-9     # strong approval (merges, ads, contracts)
uv run ship dev                      # specs and builds
uv run ship usage                    # calls, tokens, and USD-equivalent per agent
uv run ship skills [--factory]       # skills, missing skills, and a Skill Factory run
uv run ship kpi list | add | record
uv run ship schedules [--run ID]     # routines
uv run ship consent <contact_id>     # record a person's consent to be contacted (only a person can)
```

New businesses start in the console. **Start a business** on the dashboard opens the kickoff chat. `project new` is a
shortcut that makes a project from a one-line goal, with no kickoff chat.
