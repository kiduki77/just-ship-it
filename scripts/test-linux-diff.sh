#!/usr/bin/env bash
# Compares the full suite on two commits, each run through scripts/test-linux.sh.
#
# usage: scripts/test-linux-diff.sh [--shards K] <base> <head> [pytest args...]
#
# <base> and <head> take whatever test-linux.sh takes as its first argument (a commit or a worktree path). The two
# runs go in parallel with distinct test databases; SHIP_DIFF_SEQUENTIAL=1 runs them one after the other. A test
# that fails on head but not on base is rerun alone on head, and one that passes alone is reported as load
# sensitive instead of new (it is always one container). Both runs shard the suite over K containers each, K=3 by
# default; --shards 1 runs one container per side. SHIP_DIFF_NO_RECHECK=1 skips the
# recheck. Exit status is 1 when a new failure remains.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
RUN="$HERE/test-linux.sh"
START=$(date +%s)

if [ "${1:-}" = --shards ]; then export SHIP_TEST_SHARDS=${2:-}; shift 2 2>/dev/null; fi
SHARDS=${SHIP_TEST_SHARDS:-3}
[ $# -ge 2 ] || { echo "usage: scripts/test-linux-diff.sh <base> <head> [pytest args...]" >&2; exit 64; }
BASE=$1; HEAD=$2; shift 2

docker info >/dev/null 2>&1 || { echo "test-linux-diff: docker is not running" >&2; exit 64; }
docker build -q -t ship-test-linux:local "$(command -v cygpath >/dev/null && cygpath -m "$HERE/test-linux" || echo "$HERE/test-linux")" >/dev/null \
  || { echo "test-linux-diff: image build failed" >&2; exit 64; }
export SHIP_TEST_IMAGE_READY=1

STAMP=$(date +%H%M%S)$RANDOM
OUT_BASE="$REPO/.test-linux/diff-$STAMP/base"
OUT_HEAD="$REPO/.test-linux/diff-$STAMP/head"
mkdir -p "$OUT_BASE" "$OUT_HEAD"

run_one() {
  SHIP_TEST_SHARDS=$SHARDS SHIP_TEST_OUT="$2" "$RUN" "$1" "${@:3}" >"$2.log" 2>&1
}

echo "test-linux-diff: base=$BASE head=$HEAD"
if [ -n "${SHIP_DIFF_SEQUENTIAL:-}" ]; then
  run_one "$BASE" "$OUT_BASE" "$@"; run_one "$HEAD" "$OUT_HEAD" "$@"
else
  run_one "$BASE" "$OUT_BASE" "$@" & pb=$!
  run_one "$HEAD" "$OUT_HEAD" "$@" & ph=$!
  wait "$pb" "$ph"
fi

for side in base head; do
  log="$REPO/.test-linux/diff-$STAMP/$side.log"
  if ! grep -q '^RESULT:' "$log"; then
    echo "test-linux-diff: the $side run produced no result, see $log" >&2; tail -15 "$log" >&2; exit 2
  fi
  printf '%-5s %s\n' "$side" "$(grep '^RESULT:' "$log")"
done

ids() { cut -f2 "$1/failures.tsv" | LC_ALL=C sort -u; }
ids "$OUT_BASE" >"$OUT_BASE/ids"; ids "$OUT_HEAD" >"$OUT_HEAD/ids"
LC_ALL=C comm -13 "$OUT_BASE/ids" "$OUT_HEAD/ids" >"$OUT_HEAD/new"
LC_ALL=C comm -23 "$OUT_BASE/ids" "$OUT_HEAD/ids" >"$OUT_HEAD/fixed"
LC_ALL=C comm -12 "$OUT_BASE/ids" "$OUT_HEAD/ids" >"$OUT_HEAD/common"

if [ -s "$OUT_HEAD/new" ] && [ -z "${SHIP_DIFF_NO_RECHECK:-}" ]; then
  echo "test-linux-diff: rerunning $(wc -l <"$OUT_HEAD/new" | tr -d ' ') new failure(s) alone on head ..."
  mapfile -t newids <"$OUT_HEAD/new"
  SHIP_TEST_OUT="$OUT_HEAD/recheck" "$RUN" "$HEAD" "${newids[@]}" >"$OUT_HEAD/recheck.log" 2>&1
  ids "$OUT_HEAD/recheck" >"$OUT_HEAD/still" 2>/dev/null || : >"$OUT_HEAD/still"
  LC_ALL=C comm -23 "$OUT_HEAD/new" "$OUT_HEAD/still" >"$OUT_HEAD/loadsensitive"
  cp "$OUT_HEAD/still" "$OUT_HEAD/new"
else
  : >"$OUT_HEAD/loadsensitive"
fi

section() { printf '\n%s (%s)\n' "$1" "$(wc -l <"$2" | tr -d ' ')"; sed 's/^/  /' "$2"; }
section "NEW FAILURES (fail on head, pass on base)" "$OUT_HEAD/new"
section "LOAD SENSITIVE (failed in the parallel run, passed alone on head)" "$OUT_HEAD/loadsensitive"
section "FIXED (fail on base, pass on head)" "$OUT_HEAD/fixed"
section "PRE-EXISTING (fail on both)" "$OUT_HEAD/common"
echo; echo "test-linux-diff: wall time $(( $(date +%s) - START ))s, results in $REPO/.test-linux/diff-$STAMP"
[ -s "$OUT_HEAD/new" ] && exit 1
exit 0
