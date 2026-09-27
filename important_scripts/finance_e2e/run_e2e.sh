#!/usr/bin/env bash
# Finance end-to-end tests (Phase 14): the 20 required flows across units-backend and units-finance.
#
# Runs against a throwaway database (units_db_e2e) that is created, migrated and dropped here,
# with a temporary units-finance server on :8011 receiving the real accounting events over HTTP.
# Never point this at a real database: posted journals are immutable.
#
# Usage: important_scripts/finance_e2e/run_e2e.sh   (from the units-architecture repo root)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DB=units_db_e2e
LOG="$(mktemp -d)"
BACKEND=(docker exec -i -e DB_NAME=$DB -e FINANCE_SERVICE_URL=http://units-finance:8011)
FINANCE=(docker exec -i -e DB_NAME=$DB)

sql() { docker exec units_backend python manage.py shell -c "
from django.db import connection
c = connection.cursor()
$1" >/dev/null 2>&1; }

stop_server() { docker exec -i units_finance python - <<'PY' >/dev/null 2>&1
import os, signal
for pid in os.listdir("/proc"):
    if pid.isdigit():
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if "runserver" in cmd and "8011" in cmd:
            os.kill(int(pid), signal.SIGTERM)
PY
}

cleanup() {
  stop_server; sleep 2
  sql "c.execute(\"select pg_terminate_backend(pid) from pg_stat_activity where datname='$DB'\"); c.execute('DROP DATABASE IF EXISTS $DB')"
}
trap cleanup EXIT

echo "▸ scratch database"; cleanup; sql "c.execute('CREATE DATABASE $DB')"
docker exec -e DB_NAME=$DB units_backend python manage.py migrate --noinput > "$LOG/migrate_backend.log" 2>&1 || { echo "backend migrate failed"; exit 2; }
docker exec -e DB_NAME=$DB units_finance python manage.py migrate --noinput > "$LOG/migrate_finance.log" 2>&1 || { echo "finance migrate failed"; exit 2; }
docker exec -d -e DB_NAME=$DB units_finance sh -c 'python manage.py runserver 0.0.0.0:8011 > /tmp/e2e_finance.log 2>&1'
sleep 6

run() {  # run <label> <container cmd...> < script
  local label=$1; shift
  "$@" python manage.py shell > "$LOG/$label.log" 2>&1
  grep -E "^E2E_RESULT" "$LOG/$label.log" >> "$LOG/results"
  if grep -qE "^Traceback" "$LOG/$label.log"; then
    echo "  ✗ $label crashed:"; grep -A30 "^Traceback" "$LOG/$label.log" | tail -15; return 1
  fi
}

echo "▸ backend: build scenario";   run build   "${BACKEND[@]}" -e STAGE=build units_backend < "$HERE/backend_stage.py" || exit 1
CONTEXT=$(grep -E "^E2E_CONTEXT " "$LOG/build.log" | sed 's/^E2E_CONTEXT //')
echo "▸ finance: activate";         run activate "${FINANCE[@]}" -e STAGE=activate -e E2E_CONTEXT="$CONTEXT" units_finance < "$HERE/finance_stage.py" || exit 1
echo "▸ backend: deliver events";   run deliver "${BACKEND[@]}" -e STAGE=deliver -e E2E_CONTEXT="$CONTEXT" units_backend < "$HERE/backend_stage.py" || exit 1
echo "▸ finance: verify ledger";    run verify  "${FINANCE[@]}" -e STAGE=verify -e E2E_CONTEXT="$CONTEXT" units_finance < "$HERE/finance_stage.py" || exit 1
echo "▸ backend: retry + trace";    run trace   "${BACKEND[@]}" -e STAGE=trace -e E2E_CONTEXT="$CONTEXT" units_backend < "$HERE/backend_stage.py" || exit 1

python3 - "$LOG/results" <<'PY'
import json, sys
names = {1: "New lease", 2: "Contract renewal", 3: "Cheque collection", 4: "Cheque posted to bank", 5: "Cheque cleared",
         6: "First cheque return", 7: "Second cheque return", 8: "No third cheque", 9: "Payment channel charge",
         10: "Discount", 11: "Residential VAT", 12: "Commercial VAT", 13: "Expense", 14: "Multiple owners",
         15: "Owner payout", 16: "Termination", 17: "Posted journal", 18: "Journal correction", 19: "Closed period",
         20: "Unit traceability"}
rows = [json.loads(line.split(" ", 1)[1]) for line in open(sys.argv[1])]
failed = 0
for test in sorted(names):
    checks = [r for r in rows if r["test"] == test]
    ok = checks and all(r["ok"] for r in checks)
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  Test {test:>2} — {names[test]} ({sum(r['ok'] for r in checks)}/{len(checks)})")
    for r in checks:
        if not r["ok"]:
            print(f"        ✗ {r['check']}: {r['detail']}")
print(f"\n{20 - failed}/20 tests passed")
sys.exit(1 if failed else 0)
PY
status=$?
echo "(logs: $LOG)"
exit $status
