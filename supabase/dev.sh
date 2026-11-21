#!/usr/bin/env bash
# Work with the Supabase *dev* project (never production).
# Credentials are read from ~/.config/magnm/supabase-dev.env (or $MAGNM_SUPABASE_ENV) and are never printed.
#
#   supabase/dev.sh push      apply new migrations in supabase/migrations
#   supabase/dev.sh reset     wipe the dev database and re-apply all migrations
#   supabase/dev.sh test      run supabase/tests/*.sql (each test rolls back its own changes)
#   supabase/dev.sh sql FILE  run one SQL file
#
# With DB_URL set (for example CI's local database), that database is used instead of the dev file.
set -euo pipefail
cd "$(dirname "$0")/.."

CLI="npx -y supabase@2.122.1"
ENV_FILE="${MAGNM_SUPABASE_ENV:-$HOME/.config/magnm/supabase-dev.env}"

if [ -z "${DB_URL:-}" ]; then
  [ -r "$ENV_FILE" ] || { echo "Missing $ENV_FILE (see ROADMAP Phase 4 setup)." >&2; exit 2; }
  DB_URL="$(grep -E '^SUPABASE_DB_URL=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d "\"'")"
  [ -n "$DB_URL" ] || { echo "SUPABASE_DB_URL is empty in $ENV_FILE." >&2; exit 2; }
  case "$DB_URL" in
    *fetsrthktatspwucwrwk*) ;;
    *) echo "Refusing: SUPABASE_DB_URL is not the magnm-dev project." >&2; exit 2 ;;
  esac
fi

# Hide connection strings in any output.
mask() { sed -E 's#postgres(ql)?://[^ ]+#<db-url>#g'; }

case "${1:-}" in
  push)  $CLI db push --db-url "$DB_URL" --yes 2>&1 | mask ;;
  reset) $CLI db reset --db-url "$DB_URL" --no-seed --yes 2>&1 | mask ;;
  test)
    status=0
    for f in supabase/tests/*.sql; do
      [ -e "$f" ] || { echo "No tests in supabase/tests/."; exit 1; }
      if PGCONNECT_TIMEOUT=20 psql "$DB_URL" -X -q -v ON_ERROR_STOP=1 -f "$f" 2>&1 | mask; then
        echo "PASS $f"
      else
        echo "FAIL $f"; status=1
      fi
    done
    exit $status ;;
  sql)
    [ -n "${2:-}" ] || { echo "usage: supabase/dev.sh sql FILE" >&2; exit 2; }
    PGCONNECT_TIMEOUT=20 psql "$DB_URL" -X -v ON_ERROR_STOP=1 -f "$2" 2>&1 | mask ;;
  *) sed -n 2,10p "$0"; exit 2 ;;
esac
