#!/usr/bin/env bash
# Runs inside the test container. stdin is a tar of the source tree. Everything the run writes goes to /out.
set -uo pipefail

mkdir -p /work /out
tar -x -C /work || { echo "test-linux: could not unpack the source from stdin" >&2; exit 70; }
cd /work

# The suite and the settings default to 127.0.0.1:55432. Forward it to the compose Postgres so any commit runs
# unmodified, and so each container gets its own loopback and parallel runs never collide.
socat TCP-LISTEN:55432,fork,reuseaddr,bind=127.0.0.1 "TCP:${SHIP_TEST_PG_HOST:-postgres}:${SHIP_TEST_PG_PORT:-5432}" &

echo "test-linux: uv sync (frozen) ..."
uv sync --frozen --quiet || { echo "test-linux: uv sync failed" >&2; exit 71; }

for _ in $(seq 1 30); do
  /venv/bin/python - <<'PY' && break
import psycopg
from justshipit.settings import Settings
psycopg.connect(Settings().checkpoint_dsn, connect_timeout=2).close()
PY
  sleep 1
done

timeout --signal=TERM --kill-after=30 "${SHIP_TEST_TIMEOUT:-2400}" \
  /venv/bin/python -m pytest -rfE -q -p no:cacheprovider --junitxml=/out/junit.xml "$@"
rc=$?

/venv/bin/python /opt/runner/summary.py /out/junit.xml /out/failures.tsv "$rc"
summary_rc=$?

/venv/bin/python - <<PY 2>/dev/null
import psycopg
from justshipit.settings import Settings
with psycopg.connect(Settings().checkpoint_dsn, autocommit=True) as c:
    c.execute('DROP DATABASE IF EXISTS "${SHIP_TEST_DB}" WITH (FORCE)')
PY

[ "$rc" -eq 0 ] && exit "$summary_rc"
exit "$rc"
