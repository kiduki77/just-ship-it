#!/usr/bin/env bash
# Runs the test suite in a Linux container against the compose Postgres.
#
# usage: scripts/test-linux.sh [--shards K] [<commit | worktree-path>] [pytest args...]
#
# No first argument tests the current tree, uncommitted changes included. A commit is exported with `git archive`
# and a worktree with `git ls-files`, then streamed into the container, so the host .venv, .env and Windows file
# locks never reach it and nothing is bind-mounted. Each run gets its own test database.
#
# --shards K (or SHIP_TEST_SHARDS=K) splits the test files over K containers, each with its own test database,
# balanced by the per-file durations of the newest full junit.xml under .test-linux (file count when
# there is none). The shards' junit files are merged and one RESULT is printed. Pytest args turn sharding off,
# because they select tests that must run in one process (the rerun-alone check relies on that).
#
# Env: SHIP_TEST_PG_USER (Postgres role and database, default ship), SHIP_TEST_OUT (result dir), SHIP_TEST_TIMEOUT (seconds, default 2400), SHIP_TEST_NETWORK
# (compose network; default: ship_default, else aiteam_default, the name the stack had before the rename), SHIP_TEST_SHARDS (default 3;
# --shards 1 runs one container).
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
IMAGE=ship-test-linux:local
NETWORK=${SHIP_TEST_NETWORK:-}
if [ -z "$NETWORK" ]; then
  for n in ship_default aiteam_default; do
    docker network inspect "$n" >/dev/null 2>&1 && { NETWORK=$n; break; }
  done
  NETWORK=${NETWORK:-ship_default}
fi
START=$(date +%s)

hostpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
die() { echo "test-linux: $*" >&2; exit 64; }

docker info >/dev/null 2>&1 || die "docker is not running"

SHARDS=${SHIP_TEST_SHARDS:-3}
if [ "${1:-}" = --shards ]; then
  [ $# -ge 2 ] || die "--shards needs a number"
  SHARDS=$2; shift 2
fi
case $SHARDS in
  ""|*[!0-9]*|0) die "--shards needs a positive number" ;;
esac

SOURCE_KIND=tree
SOURCE=$(git rev-parse --show-toplevel 2>/dev/null || echo "$REPO")
LABEL=tree
if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
  if [ -d "$1" ] && [ -z "$(git -C "$1" rev-parse --show-prefix 2>/dev/null || echo x)" ]; then
    SOURCE=$(cd "$1" && pwd); LABEL=$(basename "$SOURCE"); shift
  elif sha=$(git -C "$REPO" rev-parse --verify -q "$1^{commit}"); then
    SOURCE_KIND=commit; SOURCE=$sha; LABEL=${sha:0:7}; shift
  fi
fi
[ $# -gt 0 ] && SHARDS=1

RUN_ID="$(date +%H%M%S)$RANDOM"
SAFE=$(printf '%s' "$LABEL" | tr -c 'a-zA-Z0-9' '_' | tr 'A-Z' 'a-z')
OUT=${SHIP_TEST_OUT:-$REPO/.test-linux/$LABEL-$RUN_ID}
mkdir -p "$OUT"

if [ -z "${SHIP_TEST_IMAGE_READY:-}" ]; then
  docker build -q -t "$IMAGE" "$(hostpath "$HERE/test-linux")" >/dev/null || die "image build failed"
fi

net_args=()
env_args=()
if docker network inspect "$NETWORK" >/dev/null 2>&1; then
  net_args=(--network "$NETWORK")
else
  net_args=(--add-host host.docker.internal:host-gateway)
  env_args+=(-e SHIP_TEST_PG_HOST=host.docker.internal -e SHIP_TEST_PG_PORT=55432)
fi
[ -n "${SHIP_TEST_TIMEOUT:-}" ] && env_args+=(-e "SHIP_TEST_TIMEOUT=$SHIP_TEST_TIMEOUT")

# The compose Postgres password (`ship init` keeps it in the main checkout's .env), passed by name only.
ENV_FILE=$(dirname "$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir)")/.env
# A .env from before the rename still says AITEAM_; the value is read from it and passed under the new name.
for key in SHIP_PG_PASSWORD; do
  old=AITEAM_${key#SHIP_}
  [ -n "${!key:-}" ] || export "$key=$(sed -n "s/^$key=//p;s/^$old=//p" "$ENV_FILE" 2>/dev/null | tail -n 1 | tr -d '\r')"
  [ -n "${!key}" ] && env_args+=(-e "$key")
done
# The Postgres role (and its database, named alike): ship, or aiteam on a stack whose database has not been renamed yet (scripts/cutover-rename.sh).
[ -n "${SHIP_TEST_PG_USER:-}" ] && env_args+=(-e "SHIP_PG_USER=$SHIP_TEST_PG_USER" -e "SHIP_PG_DB=${SHIP_TEST_PG_DB:-$SHIP_TEST_PG_USER}")

# The host docker socket lets the sandbox tests reach a daemon. Bind-mount paths they pass then name container
# paths, not host paths, so the tests that bind a test file (tests/sandbox_kit.py) skip themselves here and show
# in the skipped count. summary.py has no special case: any other failure is a failure.
sock_args=(-v /var/run/docker.sock:/var/run/docker.sock)

CONTAINERS=()
cleanup() { [ ${#CONTAINERS[@]} -gt 0 ] && docker rm -f "${CONTAINERS[@]}" >/dev/null 2>&1; }
trap cleanup EXIT

stream_source() {
  if [ "$SOURCE_KIND" = commit ]; then
    git -C "$REPO" archive --format=tar "$SOURCE"
  else
    git -C "$SOURCE" ls-files -z --cached --others --exclude-standard \
      | tar --null --ignore-failed-read -C "$SOURCE" -cf - -T - 2>/dev/null
  fi
}

test_files() {
  if [ "$SOURCE_KIND" = commit ]; then
    git -C "$REPO" ls-tree -r --name-only "$SOURCE" tests
  else
    git -C "$SOURCE" ls-files --cached --others --exclude-standard tests | while read -r f; do
      [ -f "$SOURCE/$f" ] && echo "$f"
    done
  fi | grep -E '(^|/)test_[^/]*\.py$' | LC_ALL=C sort
}

# `path<TAB>seconds` per test file, from the newest junit.xml that holds a full run.
file_durations() {
  local j
  while read -r j; do
    [ "$(grep -o '<testcase ' "$j" | wc -l)" -ge 500 ] || continue
    grep -o 'classname="[^"]*" name="[^"]*" time="[^"]*"' "$j" | awk '{
      split($1, a, "\""); n = split(a[2], p, "."); path = ""
      for (i = 1; i <= n; i++) { path = path (i > 1 ? "/" : "") p[i]; if (p[i] ~ /^test_/) break }
      split($NF, t, "\""); d[path ".py"] += t[2]
    } END { for (f in d) printf "%s\t%.2f\n", f, d[f] }'
    return
  done < <(ls -t "$REPO"/.test-linux/*/junit.xml "$REPO"/.test-linux/*/*/junit.xml 2>/dev/null)
}

# Longest file first onto the least loaded shard. Prints `shard<TAB>file`. Each shard then runs its files in path
# order, as the unsharded run does, because some tests lean on the state earlier files leave in the database.
partition() {
  local files durs
  files=$(mktemp); durs=$(mktemp)
  test_files >"$files"; file_durations >"$durs"
  awk -F'\t' 'FILENAME == ARGV[1] { d[$1] = $2; sum += $2; n++; next }
    { w = ($1 in d) ? d[$1] : (n ? sum / n : 1); print w "\t" $1 }' "$durs" "$files" \
    | sort -t$'\t' -k1,1nr -k2,2 \
    | awk -F'\t' -v k="$1" '{ m = 1; for (i = 2; i <= k; i++) if (load[i] < load[m]) m = i; load[m] += $1; print m "\t" $2 }'
  rm -f "$files" "$durs"
}

# run_container <index> <out dir> [pytest args...]
run_container() {
  local idx=$1 out=$2; shift 2
  local name="ship-test-$SAFE-$RUN_ID-$idx" db="ship_t_${SAFE}_${RUN_ID}_$idx"
  mkdir -p "$out"
  MSYS_NO_PATHCONV=1 docker create -i --name "$name" "${net_args[@]}" "${env_args[@]}" \
    -e "SHIP_TEST_DB=$db" "${sock_args[@]}" \
    -v ship-test-uv-cache:/uv-cache "$IMAGE" "$@" >/dev/null \
    || { echo "test-linux: could not create the container" >&2; return 64; }
  echo "test-linux: source=$SOURCE_KIND:$LABEL db=$db out=$out"
  stream_source | MSYS_NO_PATHCONV=1 docker start -ai "$name"
  local rc=${PIPESTATUS[1]}
  MSYS_NO_PATHCONV=1 docker cp "$name:/out/." "$(hostpath "$out")" >/dev/null 2>&1 || true
  docker rm -f "$name" >/dev/null 2>&1
  return "$rc"
}

for i in $(seq 0 "$SHARDS"); do CONTAINERS+=("ship-test-$SAFE-$RUN_ID-$i"); done

if [ "$SHARDS" -eq 1 ]; then
  run_container 0 "$OUT" "$@"
  RC=$?
else
  PLAN=$(partition "$SHARDS")
  [ -n "$PLAN" ] || die "no test files found to shard"
  pids=(); outs=()
  for i in $(seq 1 "$SHARDS"); do
    # path order within a shard, like the unsharded run: some tests lean on state earlier files leave behind
    mapfile -t sfiles < <(printf '%s\n' "$PLAN" | awk -F'\t' -v i="$i" '$1 == i { print $2 }' | LC_ALL=C sort)
    [ ${#sfiles[@]} -gt 0 ] || continue
    sout="$OUT/shard$i"; outs+=("$sout")
    echo "test-linux: shard $i runs ${#sfiles[@]} files"
    run_container "$i" "$sout" "${sfiles[@]}" >"$sout.log" 2>&1 &
    pids+=($!)
  done
  RC=0
  for pid in "${pids[@]}"; do wait "$pid"; r=$?; [ "$r" -gt "$RC" ] && RC=$r; done

  # Merge into one junit.xml, one failures.tsv and a RESULT line in summary.py's format.
  : >"$OUT/failures.tsv"; MISSING=0
  { echo '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">'
    for d in "${outs[@]}"; do
      [ -f "$d/junit.xml" ] && sed -e 's/^.*<testsuites[^>]*>//' -e 's/<\/testsuites>.*$//' "$d/junit.xml"
    done
    echo '</testsuites>'; } >"$OUT/junit.xml"
  echo; echo "======================================================================"
  for d in "${outs[@]}"; do
    if grep -q '^RESULT:' "$d.log"; then
      cat "$d/failures.tsv" >>"$OUT/failures.tsv"
    else
      MISSING=$((MISSING + 1)); echo "NO RESULT: $(basename "$d") produced no report, see $d.log"
    fi
  done
  if [ "$MISSING" -gt 0 ]; then
    echo "test-linux: wall time $(( $(date +%s) - START ))s, results in $OUT"; exit 2
  fi
  LC_ALL=C sort -k2 -o "$OUT/failures.tsv" "$OUT/failures.tsv"
  grep -h '^RESULT:' "${outs[@]/%/.log}" | awk -v rc="$RC" '{
      sub(/\(.*$/, ""); gsub(/[^0-9 ]/, " "); p += $1; f += $2; e += $3; s += $4 }
    END { printf "RESULT: %d passed, %d failed, %d errors, %d skipped (%d tests, pytest exit %d)\n", p, f, e, s, p+f+e+s, rc }'
  awk -F'\t' '{ printf "  %-7s %s\n", toupper($1), $2 }' "$OUT/failures.tsv"
  [ -s "$OUT/failures.tsv" ] && [ "$RC" -eq 0 ] && RC=1
fi

echo "test-linux: wall time $(( $(date +%s) - START ))s, results in $OUT"
exit "$RC"
