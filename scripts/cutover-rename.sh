#!/usr/bin/env bash
# One-time cutover of the compose Postgres from the old names to the new ones: database aiteam -> ship, role aiteam -> ship.
# Run it once, with the app stopped (no worker, no console), while the Postgres container is up. See docs/RENAME-CUTOVER.md.
#
# usage: scripts/cutover-rename.sh [--container NAME] [--force] [--dry-run]
#
#   --container NAME  the Postgres container (default: the running one of ship-postgres-1, aiteam-postgres-1)
#   --force           end sessions still open on the old database instead of refusing
#   --dry-run         only report what it would do
#
# What it does, in order: refuses while the app is connected; takes a pg_dump into var/backups/ and checks it lists;
# renames the database and the role through a temporary superuser (a session cannot rename its own role); sets the
# role's password to SHIP_PG_PASSWORD (or the old AITEAM_PG_PASSWORD) from .env, else the default "ship"; checks a
# password login over TCP. Passwords travel on stdin only, never on a command line, in a log or on the screen.
# Running it again after it finished changes nothing.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
OLD=aiteam NEW=ship TMP=ship_cutover
CONTAINER= FORCE= DRY=
die() { echo "cutover: $*" >&2; exit 64; }

while [ $# -gt 0 ]; do
  case $1 in
    --container) CONTAINER=${2:-}; shift 2 || die "--container needs a name" ;;
    --force) FORCE=1; shift ;;
    --dry-run) DRY=1; shift ;;
    *) die "unknown argument $1" ;;
  esac
done

docker info >/dev/null 2>&1 || die "docker is not running"
if [ -z "$CONTAINER" ]; then
  for c in ship-postgres-1 aiteam-postgres-1; do
    [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" = true ] && { CONTAINER=$c; break; }
  done
fi
[ -n "$CONTAINER" ] || die "no running Postgres container (ship-postgres-1 or aiteam-postgres-1); use --container"
[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" = true ] || die "$CONTAINER is not running"

# psql inside the container over the unix socket, where the image trusts local users: no password is asked or sent.
sock() { docker exec -i "$CONTAINER" psql -X -q -v ON_ERROR_STOP=1 -tA -U "$1" -d "$2"; }
# The superuser of the cluster, whichever name it still has.
SUPER=
for u in $NEW $OLD; do
  [ "$(echo "select 1" | sock "$u" postgres 2>/dev/null)" = 1 ] && { SUPER=$u; break; }
done
[ -n "$SUPER" ] || die "cannot log in to $CONTAINER over its socket as $NEW or $OLD"
ask() { echo "$1" | sock "$SUPER" postgres; }

has_db() { [ "$(ask "select count(*) from pg_database where datname = '$1'")" = 1 ]; }
has_role() { [ "$(ask "select count(*) from pg_roles where rolname = '$1'")" = 1 ]; }

# The password the role will have: .env of the checkout (the main one when run from a worktree), else the default.
MAIN=$(dirname "$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" 2>/dev/null)
PW=
for f in "$REPO/.env" "$MAIN/.env"; do
  [ -f "$f" ] || continue
  PW=$(sed -n 's/^SHIP_PG_PASSWORD=//p;s/^AITEAM_PG_PASSWORD=//p' "$f" | tail -n 1 | tr -d '\r')
  [ -n "$PW" ] && break
done
if [ -n "$PW" ]; then PW_FROM=".env"; else PW=$NEW; PW_FROM="the default"; fi

if has_db "$NEW" && has_role "$NEW" && ! has_db "$OLD" && ! has_role "$OLD"; then
  echo "cutover: $CONTAINER already has database and role $NEW, nothing to do"; exit 0
fi
has_db "$OLD" || die "no database $OLD in $CONTAINER (and the cutover is not finished); look at it before going on"
has_db "$NEW" && die "both databases $OLD and $NEW exist; look at it before going on"
has_role "$NEW" && die "role $NEW exists next to role $OLD; look at it before going on"
has_role "$OLD" || die "no role $OLD in $CONTAINER"

busy=$(ask "select count(*) from pg_stat_activity where datname = '$OLD' and pid <> pg_backend_pid()")
echo "cutover: container=$CONTAINER superuser=$SUPER sessions on $OLD=$busy password from $PW_FROM"
if [ "$busy" != 0 ] && [ -z "$FORCE" ]; then
  die "$busy session(s) still use $OLD. Stop the worker and the console (ship supervise) first, or use --force"
fi
[ -z "$DRY" ] || { echo "cutover: dry run, nothing changed"; exit 0; }

# 1. Backup, read back before anything is renamed.
mkdir -p "$REPO/var/backups"
DUMP="$REPO/var/backups/pre-rename-$(date +%Y%m%d-%H%M%S).dump"
docker exec "$CONTAINER" pg_dump -U "$SUPER" -d "$OLD" -Fc >"$DUMP" || { rm -f "$DUMP"; die "pg_dump failed"; }
[ -s "$DUMP" ] || die "the backup is empty"
docker exec -i "$CONTAINER" pg_restore --list <"$DUMP" >/dev/null || die "the backup does not read back: $DUMP"
echo "cutover: backup $DUMP ($(wc -c <"$DUMP") bytes)"

# 2. The rename, through a temporary superuser. Ends sessions first when --force.
if [ -n "$FORCE" ]; then
  ask "select pg_terminate_backend(pid) from pg_stat_activity where datname = '$OLD' and pid <> pg_backend_pid()" >/dev/null
fi
ask "create role $TMP superuser login" >/dev/null || die "could not create the temporary role"
cleanup() { echo "drop role if exists $TMP" | sock "${FINAL:-$SUPER}" postgres >/dev/null 2>&1; }
trap cleanup EXIT
sock "$TMP" postgres <<SQL || die "the rename failed; the backup is $DUMP and nothing else changed"
alter database $OLD rename to $NEW;
alter role $OLD rename to $NEW;
SQL
FINAL=$NEW

# 3. A role rename clears an MD5 password, and the new default differs from the old one: set the password again.
# The statement goes to psql on stdin, so the value appears in no process list.
SQ="'"
Q=${PW//"$SQ"/"$SQ$SQ"}
printf "alter role %s password '%s';\n" "$NEW" "$Q" | sock "$TMP" postgres || die "could not set the password of $NEW"
echo "drop role $TMP" | sock "$NEW" postgres >/dev/null || echo "cutover: could not drop $TMP, drop it by hand"
trap - EXIT

# 4. A password login over TCP, as the app makes it.
got=$(printf '%s' "$PW" | docker exec -i "$CONTAINER" sh -c \
  "IFS= read -r PGPASSWORD; export PGPASSWORD; psql -X -tA -h 127.0.0.1 -U $NEW -d $NEW -c 'select current_user || chr(47) || current_database()'" 2>&1)
[ "$got" = "$NEW/$NEW" ] || die "renamed, but a password login as $NEW failed ($got). The backup is $DUMP"
echo "cutover: done. Login as $NEW on database $NEW works. Next: docs/RENAME-CUTOVER.md, step \"Bring the stack up under the new name\""
