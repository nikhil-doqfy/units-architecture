#!/bin/bash
# One-time sync for an EXISTING dev database after the Phase 0 migration-history reset
# (feat/finance-foundation). The regenerated migrations describe the same schema your
# database already has, so the initial ones are recorded as applied ("faked"); later
# migrations (e.g. money_decimal) then run for real.
#
# Prerequisite: your DB was fully migrated on the OLD history (pre-reset n_develop).
# Run from units-architecture/ with the containers up, BEFORE starting the new code's
# `migrate` (container restart). A fresh/empty database needs none of this -- just migrate.
set -euo pipefail

APPS="'broadcast','charges','complaint','lead','lease','notification','payment','property','property_management','support','terms','user_service','ledger'"

docker exec units_postgres sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"delete from django_migrations where app in ($APPS)\""
docker exec units_backend python manage.py migrate --fake-initial
docker exec units_finance python manage.py migrate ledger --fake-initial
docker exec units_backend python manage.py migrate --plan
docker exec units_finance python manage.py migrate --plan
