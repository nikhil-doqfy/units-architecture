#!/bin/bash
cd "$(dirname "$0")"
docker exec -i -e DRY_RUN=${DRY_RUN:-1} units_backend python manage.py shell < backend_pmd.py > /tmp/pmd_backend.log 2>&1
grep -E '^R ' /tmp/pmd_backend.log | grep '"ok": false'
grep -E '^(SUMMARY|DRY)' /tmp/pmd_backend.log
if grep -q Traceback /tmp/pmd_backend.log; then grep -B2 -A0 'File "<string>"' /tmp/pmd_backend.log | tail -4; tail -3 /tmp/pmd_backend.log; fi
