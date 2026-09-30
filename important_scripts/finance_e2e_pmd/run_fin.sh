#!/bin/bash
cd "$(dirname "$0")"
docker exec -i -e DRY_RUN=${DRY_RUN:-1} -e CTX="$(cat ctx.json)" units_finance python manage.py shell < finance_pmd.py > /tmp/pmd_finance.log 2>&1
grep -E '^R ' /tmp/pmd_finance.log | grep '"ok": false'
grep -E '^(SUMMARY|DRY|TOTALS)' /tmp/pmd_finance.log
if grep -q Traceback /tmp/pmd_finance.log; then grep -A1 'File "<string>"' /tmp/pmd_finance.log | tail -4; tail -3 /tmp/pmd_finance.log; fi
