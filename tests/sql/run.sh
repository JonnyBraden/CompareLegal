#!/usr/bin/env bash
# Runs the migration and RLS tests on a throwaway Postgres cluster (no Supabase account needed).
# Needs the PostgreSQL server binaries (initdb, pg_ctl, psql). Run from the repo root: tests/sql/run.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PGBIN="${PGBIN:-$(dirname "$(ls /usr/lib/postgresql/*/bin/initdb | sort -V | tail -1)")}"
DATA="$(mktemp -d)"
PORT="${PGPORT:-54329}"

# Postgres refuses to run as root, so drop to the postgres user when needed.
if [ "$(id -u)" = "0" ]; then
  chown postgres "$DATA"
  RUN=(runuser -u postgres --)
else
  RUN=()
fi

cleanup() { "${RUN[@]}" "$PGBIN/pg_ctl" -D "$DATA" -m immediate stop >/dev/null 2>&1 || true; rm -rf "$DATA"; }
trap cleanup EXIT

"${RUN[@]}" "$PGBIN/initdb" -D "$DATA" -A trust -U postgres >/dev/null
"${RUN[@]}" "$PGBIN/pg_ctl" -D "$DATA" -o "-p $PORT -k /tmp -c listen_addresses=''" -l "$DATA/log" -w start >/dev/null

PSQL=("${RUN[@]}" "$PGBIN/psql" -h /tmp -p "$PORT" -U postgres -v ON_ERROR_STOP=1 -X -q)

"${PSQL[@]}" -c "create database comparelegal_test" -d postgres
DB=(-d comparelegal_test)

"${PSQL[@]}" "${DB[@]}" -f "$ROOT/tests/sql/00_supabase_stub.sql"
echo "== applying migration"
for f in "$ROOT"/supabase/migrations/*.sql; do "${PSQL[@]}" "${DB[@]}" -f "$f"; done
echo "== running tests"
"${PSQL[@]}" "${DB[@]}" -f "$ROOT/tests/sql/10_rls_tests.sql" 2>&1 | sed -e 's/^psql:[^ ]* //' -e 's/^NOTICE:  //'
