---
stepsCompleted: [1, 2, 3]
inputDocuments: [docs/finance/prd.md, docs/finance/ARCHITECTURE-SPINE.md, docs/finance/.model-correction-note.md]
---

# Units Finance Module (Phase 1) - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for the Units Finance Module (Phase 1), decomposing the requirements from `docs/finance/prd.md` and `docs/finance/ARCHITECTURE-SPINE.md` into implementable stories.

## Requirements Inventory

### Functional Requirements

**Model correction (2026-09-01):** there is no standalone "Company" entity. `PropertyManagmentCompany` ("PMC", existing units-backend model) IS the accounting entity, 1:1. `Organization` (existing) sits above PMC: `Organization` → `PropertyManagmentCompany` → `Property` → `PropertyBlocks` → `Unit` (all existing, unmodified). Finance adds one new table, `FinancePMCProfile`, holding only accounting settings (base_currency, country, fiscal_year_start_month) keyed by a required `pmc_id` field (value of `PropertyManagmentCompany.id`, not a cross-DB FK). Every FR below that referenced "Company" now references `FinancePMCProfile`/PMC. See `docs/finance/.model-correction-note.md` and `docs/finance/prd.md`'s 2026-09-01 revision note for the full rationale.

FR-1: An operator can create a `FinancePMCProfile` record (accounting settings: base currency, fiscal year start month, country) for an *existing* `PropertyManagmentCompany`, identified by `pmc_id` — this activates that PMC for Finance, it does not create a new legal entity. Every Phase 1 `FinancePMCProfile` must be UAE-registered (AED, 5% VAT); non-UAE country values are rejected/flagged.

FR-2: **[REMOVED]** — no new Unit-to-Company mapping table. `Unit` already resolves to its PMC via the existing `Unit.parent_property.pmc` chain (units-backend, `property` app); Finance resolves a `Unit`'s `FinancePMCProfile` by matching `FinancePMCProfile.pmc_id == Unit.parent_property.pmc.id`. Posting fails loudly (logged, no partial post) if no `FinancePMCProfile` exists for the resolved PMC, or if `Unit.parent_property`/its `pmc` is unset. This FR number stays retired (not reused) so other FRs' references to "FR-2" remain traceable.

FR-3: The system provides a seed/fixture creating the standard Chart of Accounts (Rent Income, Security Deposits Held, VAT Payable, Bank, AR — Tenants, AP — PMC Commission, Commission Expense, Bank Charges/Fees) for a new `FinancePMCProfile` on creation.

FR-4: When a new `LeaseTransaction` row is created with `cheque_type = RENT` (or cash/bank transfer for rent), the system posts a balanced Journal Entry debiting AR — Tenants and crediting Rent Income for the `FinancePMCProfile` resolved via `Lease.unit.parent_property.pmc`.

FR-5: When `LeaseTransaction.status` transitions to `REALIZED`/`CREDITED`, the system posts the corresponding Bank Journal entry (debit Bank, credit AR — Tenants), idempotent on `LeaseTransaction.id` + target status.

FR-6: When `LeaseTransaction.status` transitions to `BOUNCED`, the system reverses the original AR posting (credit AR — Tenants, debit Bounced Cheques suspense Account).

FR-7: When a bounce fee `Charge` row is linked to a bounced `LeaseTransaction`, the system posts a Journal Entry debiting AR — Tenants and crediting Bank Charges/Fee Income, including a separate VAT Payable line when `Charge.vat_amount` is non-zero.

FR-8: When a `Lease` is activated (or a security-deposit-tagged `LeaseTransaction` is recorded), the system posts a Journal Entry debiting Bank/AR and crediting Security Deposits Held (liability) for `Lease.security_deposit`; security deposits never post to Rent Income.

FR-9: When a rent AR posting (FR-4) resolves to a PMC-managed Unit, the system additionally posts a commission split (credit Commission Expense / debit AP — PMC Commission) for the managing PMC's share plus 5% VAT, proportional across multiple `UnitOwner`s by ownership share, and across both PMCs' (`FinancePMCProfile`) Ledgers when the managing PMC differs from the owning PMC for that Unit (a cross-PMC management arrangement).

FR-10: The system returns, per `FinancePMCProfile` and date range, every Account's total debit and credit, with total debits == total credits. **Phase 1 ships this as a backend API endpoint only** (no frontend UI — see new Deferred item below).

FR-11: The system returns, per `FinancePMCProfile` and date range, all Income and Expense Account balances and the resulting net profit/loss, independently recomputable from FR-10's output. **API-only in Phase 1.**

FR-12: The system returns, per `FinancePMCProfile`, as-of-date balances for Asset, Liability, and Equity accounts, with Assets = Liabilities + Equity. **API-only in Phase 1.**

FR-13: The system returns, per `FinancePMCProfile`, outstanding AR — Tenants balances bucketed by days overdue (current, 1-30, 31-60, 61-90, 90+), derived from unresolved `LeaseTransaction` rows and their due date. **API-only in Phase 1.**

FR-14: An operator can upload a CSV of bank statement lines (date, amount, reference) for a `FinancePMCProfile`, creating one statement-line record per row.

FR-15: The system suggests matches between imported statement lines and unreconciled Bank Journal entries by amount and date proximity (exact-amount + date-window heuristic); an operator can confirm or reject a match; a confirmed match marks both sides reconciled; an entry matches at most one statement line.

### NonFunctional Requirements

NFR-1: All postings (FR-4 through FR-9) are atomic — a `LeaseTransaction` event either fully posts a balanced Journal Entry or posts nothing; no partial/unbalanced entries ever land in the Ledger.

NFR-2: Bank reconciliation auto-match (FR-15) uses a simple exact-amount + date-window heuristic — no fuzzy/ML matching, consistent with the 5-day build budget.

NFR-3: Trial Balance balances (debits = credits) for every Company, checked automatically on every report generation (SM-1) — a structural invariant, not just a display check.

NFR-4: The Finance module deploys into the existing `docker-compose.yml` and starts successfully alongside existing Units services with zero manual schema conflicts (SM-2).

NFR-5: Posting correctness/atomicity is never traded for posting speed (SM-C1) — a fast but occasionally-unbalanced Ledger is worse than a slightly slower, fully-correct one.

### Additional Requirements

- No starter template — brownfield, extends the existing `microservices/` sibling pattern (AD-10).
- Finance is a new sibling Django project at `microservices/units-finance/` with its own `manage.py`, added as a new `units_finance` service in root `docker-compose.yml` with `depends_on: postgres` (AD-10).
- Finance has its own `requirements.txt`/Dockerfile, independent of units-backend's; pins Python 3.12.x and Django 5.x LTS (current at build time) rather than units-backend's EOL Django 4.0.0 (AD-12, AD-13).
- Finance connects to the same Postgres instance/database as units-backend via the same root `.env` vars — no separate database, no schema-per-service split; owns its own tables (`FinancePMCProfile`, `Account`, `JournalEntry`, `LedgerLine`) via its own app + migrations (AD-2).
- Root `.env`'s `DB_HOST` must be fixed from `localhost` to `postgres` (compose service name) for both `units_backend` and `units_finance`, requiring a coordinated `docker-compose up -d --force-recreate` of `units_backend` — a one-time operational cutover, not incidental (AD-3, Deferred).
- Django project package is named `finance_service`; primary domain app is named `ledger` (AD-11).
- A new `signals.py` (the first in units-backend) adds a `post_save` signal on `LeaseTransaction` as the sole posting trigger — no polling (AD-4).
- units-backend's signal handler calls Finance only via synchronous internal HTTP (`POST /internal/lease-transactions/{id}/sync`); units-backend never imports Finance models or writes Ledger/JournalEntry tables directly (AD-5).
- On failure, the signal handler retries synchronously in-request with short backoff (0.5s/1s/2s, 2-3 attempts); exhausted retries are logged loudly for manual reconciliation; no Celery/Redis dependency anywhere in this build (AD-6, AD-8).
- Every endpoint under Finance's `/internal/` namespace requires an `X-Internal-Token` header checked against `FINANCE_INTERNAL_TOKEN`, rejecting (401/403) missing/wrong values (AD-7).
- All Finance endpoints are function-based `@api_view` with hand-rolled `serialize_*()` functions — no `ModelSerializer`/`APIView`/`ViewSet` (AD-1).
- Every Finance endpoint's HTTP response (success and failure) is wrapped via `utilities.helper_functions.prepare_response()` → `{content, message, status}`; on failure, `message` carries a short summary and `content` carries structured error detail (AD-1, inherited platform AD-3).
- `UnitOwner` gains an `ownership_percent` field, `NOT NULL` with a mandatory backfill migration so every existing multi-owner `Unit`'s percentages sum to 100 before Finance goes live; the posting engine fails loudly (no partial post) if this invariant is violated at posting time (AD-15).
- `FinancePMCProfile` is a new Finance-owned table (accounting settings only: `base_currency`, `country`, `fiscal_year_start_month`, required `pmc_id` field) — not a new legal-entity model; it activates an existing `PropertyManagmentCompany` for Finance rather than creating one (AD-19, model correction).
- Every `JournalEntry` stores `source_lease_transaction_id` and `source_status_transition`; the idempotency check is the exact `(lease_transaction_id, from_status, to_status)` tuple, not just `(id, target_status)` (AD-14).
- Reversals (FR-6) always post a new `JournalEntry` with a `reversed_journal_entry_id` FK to the original; the original is never edited/deleted; balance queries sum all rows unconditionally (AD-16).
- External reporting endpoints (FR-10 through FR-13) authenticate via the same stateless JWT units-backend issues (`Authorization: Bearer <token>`) — **prerequisite:** units-backend's `SECRET_KEY` must first be extracted from its current hardcoded value in `settings.py` into the shared root `.env` as `JWT_SECRET_KEY`, since it is not currently environment-sourced (AD-17, verified gap).
- External reporting endpoints are PMC-scoped via `utilities.org_scope.get_pmc_ids_for_user()` (inherited platform AD-2); `FinancePMCProfile.pmc_id` IS the direct link to units-backend's `PropertyManagmentCompany.id` (not a join to resolve — see model correction above); one shared helper bridges the JWT payload into the `UserProfile` row `get_pmc_ids_for_user()` needs, reused by every reporting view (AD-18, AD-19).
- Trial Balance, P&L, and Balance Sheet are returned whole, never paginated (snapshot documents); Ageing MUST paginate via `prepare_response()`'s `paginator` argument, never nested inside `content` (Structural Seed, inherited platform AD-3).
- **Organization-level consolidated reporting (summing across all PMCs under one `Organization`) is explicitly Phase 2** — Phase 1 reporting (FR-10–FR-13) stays scoped strictly per-`FinancePMCProfile`. No epic in this breakdown builds Organization roll-up; `FinancePMCProfile.pmc_id` is shaped to support it later without a data model change.
- **units-frontend UI work is explicitly Phase 2** — Phase 1 delivers the Finance backend and reporting API only, verified via Swagger/Postman/automated tests. No epic in this breakdown includes Angular screens; the PRD's UJ-1 (Owner/PMC viewing Trial Balance in-app) is a real intended Phase 2 journey, not built now.

### UX Design Requirements

Not applicable — Finance (Phase 1) is a system-to-system posting engine plus a small set of reporting/reconciliation endpoints consumed by the existing Units frontend; no dedicated UX design contract exists or is needed for this phase (PRD §2.3 explicitly downscales UX narrative for this internal, largely system-to-system module).

### FR Coverage Map

FR1: Epic 1 - FinancePMCProfile creation
FR2: (removed — no new Unit-to-PMC mapping; existing `Unit.parent_property.pmc` chain reused, no story)
FR3: Epic 1 - Standard Chart of Accounts seed
FR4: Epic 2 - Rent AR posting on cheque receipt
FR5: Epic 2 - Cheque clearing posting
FR6: Epic 2 - Bounce reversal posting
FR7: Epic 2 - Bounce fee posting
FR8: Epic 2 - Security deposit posting
FR9: Epic 2 - Owner/PMC commission split posting
FR10: Epic 3 - Trial Balance report
FR11: Epic 3 - Profit & Loss report
FR12: Epic 3 - Balance Sheet report
FR13: Epic 3 - Ageing report
FR14: Epic 4 - Manual bank statement import
FR15: Epic 4 - Match statement lines to Ledger entries

## Epic List

### Epic 1: Finance Foundation & PMC Activation
Stand up the Finance service and let an operator activate an existing PMC for accounting — the prerequisite for every other epic.
**FRs covered:** FR-1, FR-3
**Also carries:** service scaffolding (AD-10, AD-11, AD-12), shared-Postgres wiring including the `DB_HOST` cutover (AD-2, AD-3), `FinancePMCProfile.pmc_id` (AD-19)

### Epic 2: Posting Engine
Every relevant `LeaseTransaction`/`Lease` event becomes a correct, balanced Ledger entry automatically, including the units-backend↔Finance integration that triggers it.
**FRs covered:** FR-4, FR-5, FR-6, FR-7, FR-8, FR-9
**Also carries:** units-backend signal + Finance internal sync API + retry + `X-Internal-Token` auth (AD-4–AD-8), idempotency (AD-14), additive-only reversals (AD-16), `UnitOwner.ownership_percent` backfill (AD-15), atomicity (NFR-1, NFR-5)

### Epic 3: Financial Reporting API
A PMC/Owner-authenticated caller can retrieve Trial Balance, P&L, Balance Sheet, and Ageing for their PMC via the API (no UI — Phase 2).
**FRs covered:** FR-10, FR-11, FR-12, FR-13
**Also carries:** JWT auth reuse + `SECRET_KEY` extraction prerequisite (AD-17), PMC-scoping (AD-18), response envelope/pagination (AD-1, AD-3), structural balance invariant (NFR-3)

### Epic 4: Bank Reconciliation
An operator can upload a bank statement and reconcile it against posted Ledger entries — standalone once Epic 2 exists.
**FRs covered:** FR-14, FR-15
**Also carries:** simple heuristic matching (NFR-2)

**Dependency flow:** Epic 1 → Epic 2 → {Epic 3, Epic 4} (Epics 3 and 4 are independent of each other).

## Epic 1: Finance Foundation & PMC Activation

Stand up the Finance service and let an operator activate an existing PMC for accounting — the prerequisite for every other epic.

### Story 1.1: Stand up the Finance service

As a developer (Rahul),
I want a new `units-finance` Django service scaffolded and running in the existing docker-compose stack, connected to the shared Postgres instance,
So that all subsequent Finance work has a running service to build against, with zero risk to the existing units-backend/units-frontend deployment.

**Acceptance Criteria:**

**Given** the root `docker-compose.yml` and `.env`
**When** a new `microservices/units-finance/` Django project (`finance_service` package, `ledger` app) is created with its own `requirements.txt` and `Dockerfile`, independent of `units-backend`'s (AD-12)
**Then** `docker-compose.yml` gains a new `units_finance` service block with `depends_on: postgres`, using the same `.env`-driven DB connection pattern as `units_backend` (AD-2, AD-10)

**Given** the root `.env` currently has `DB_HOST=localhost` (Postgres commented out)
**When** the cutover is applied
**Then** `DB_HOST` is set to `postgres` (the compose service name) for both `units_backend` and `units_finance`, and `units_backend` is recreated (`docker-compose up -d --force-recreate`) so the new env value takes effect (AD-3)

**And** `docker-compose up` brings up `units_finance` alongside the existing services with zero manual schema conflicts (NFR-4), confirmed by `python manage.py migrate` running cleanly against the shared database with no app yet defining conflicting table names

**And** the Finance container is reachable on its own port inside the Docker network, with no changes required to `units-backend` or `units-frontend`'s own containers beyond the `DB_HOST` cutover

### Story 1.2: Activate a PMC for Finance (`FinancePMCProfile` creation)

As an operator (Rahul, via Django management command),
I want to create a `FinancePMCProfile` for an existing UAE `PropertyManagmentCompany`, specifying its accounting settings,
So that a PMC has the accounting configuration Finance needs before any Ledger posting can happen for its Units.

**Acceptance Criteria:**

**Given** the Finance service is running (Story 1.1) and an existing `PropertyManagmentCompany` row with a known `pmc_id` in the shared Postgres database
**When** an operator runs the management command with `pmc_id`, `base_currency`, `country`, and `fiscal_year_start_month`
**Then** a `FinancePMCProfile` record is created with a non-null `pmc_id`, `base_currency`, and `country` (FR-1)

**Given** a `country` value other than `UAE`
**When** the management command is run
**Then** the command rejects (or flags, per operator confirmation) the creation, since cross-country logic is not built in Phase 1 (FR-1 Out of Scope)

**Given** two different `pmc_id` values, each with an existing `PropertyManagmentCompany` row
**When** a `FinancePMCProfile` is created for each
**Then** both records coexist independently, each later resolving to its own Units (via the existing `Unit.parent_property.pmc` chain), Ledger, and reports without cross-contamination (FR-1)

**Given** a `pmc_id` that does not correspond to any existing `PropertyManagmentCompany` row
**When** the management command is run
**Then** creation fails with a clear error — a `FinancePMCProfile` is never created for a PMC that doesn't exist (AD-19)

### Story 1.3: Seed the standard Chart of Accounts

As the Finance system,
I want to automatically create the standard Chart of Accounts for a `FinancePMCProfile` when it's activated,
So that posting (Epic 2) and reporting (Epic 3) have Accounts to reference from the moment a PMC goes live — no manual CoA setup step.

**Acceptance Criteria:**

**Given** a `FinancePMCProfile` is created (Story 1.2)
**When** creation completes
**Then** the standard CoA fixture runs automatically and creates all 7+ Accounts (Rent Income, Security Deposits Held, VAT Payable, Bank, AR — Tenants, AP — PMC Commission, Commission Expense, Bank Charges/Fees) scoped to that `FinancePMCProfile`, each with the correct Account type (Asset/Liability/Income/Expense) (FR-3)

**Given** two separate `FinancePMCProfile` records
**When** each is created
**Then** each gets its own independent set of standard Accounts — no Account row is shared across `FinancePMCProfile`s

**Given** the standard CoA is already seeded for a `FinancePMCProfile`
**When** the seed fixture is inspected
**Then** it is a fixed, non-configurable set for Phase 1 — no CoA editor UI or per-PMC customization exists (FR-3 Notes, confirming this is a hard scope boundary carried into the story)

## Epic 2: Posting Engine

Every relevant `LeaseTransaction`/`Lease` event becomes a correct, balanced Ledger entry automatically, including the units-backend↔Finance integration that triggers it.

### Story 2.1: units-backend → Finance sync integration

As the Finance system,
I want a reliable, authenticated, synchronous channel from units-backend's `LeaseTransaction` events into Finance,
So that every subsequent posting story (2.2–2.7) has a trigger mechanism to build on, without units-backend ever writing to Finance's tables directly.

**Prerequisite (already exists, verified — no new work needed):** the UI-facing cheque status-change API already exists in units-backend — `lease_cheque_view` (the real handler; `lease_cheque_status`/`api/lease/cheque-status` is a legacy alias delegating to it) updates `LeaseTransaction.status` when a UI user changes a cheque's status (e.g. marking it `REALIZED` or `BOUNCED`). This is the write path AD-4's new `post_save` signal listens on — Epic 2 does not build a status-change API, only the signal that reacts to this existing one.

**Acceptance Criteria:**

**Given** units-backend has no existing `signals.py`
**When** this story is implemented
**Then** a new `signals.py` is added to units-backend with a single Django `post_save` signal on `LeaseTransaction` — the sole trigger mechanism; no polling or periodic scan exists anywhere in the system (AD-4)

**Given** the signal fires on a `LeaseTransaction` create or status change
**When** the signal handler runs
**Then** it issues a synchronous HTTP POST to Finance's `POST /internal/lease-transactions/{id}/sync` endpoint, and units-backend never imports Finance models or writes to any Finance-owned table directly (AD-5)

**Given** the Finance endpoint is unreachable or returns an error
**When** the signal handler's HTTP call fails
**Then** it retries synchronously within the same request cycle using short backoff (0.5s / 1s / 2s, 2–3 attempts total), and if all retries are exhausted, the failure is logged loudly with the source `LeaseTransaction.id` for manual reconciliation — no queue, Celery worker, or Redis is introduced (AD-6, AD-8)

**Given** a request to any endpoint under Finance's `/internal/` namespace
**When** the request is missing the `X-Internal-Token` header, or presents a value that doesn't match `FINANCE_INTERNAL_TOKEN`
**Then** Finance rejects it with 401/403 — Docker network isolation alone is never relied on as the trust boundary (AD-7)

**And** the internal sync endpoint's response follows the standard envelope: `prepare_response()` → `{content, message, status}`, with `message` carrying a short summary and `content` carrying structured detail on failure (AD-1, inherited platform AD-3)

### Story 2.2: Rent AR posting on cheque receipt

As the Finance system,
I want to post a balanced Journal Entry whenever a new rent `LeaseTransaction` is created,
So that rent due becomes an accounting fact in the Ledger, not just a row on an operational table.

**Acceptance Criteria:**

**Given** the sync integration exists (Story 2.1) and a `FinancePMCProfile` with seeded CoA exists for the `Unit`'s PMC (Epic 1)
**When** a new `LeaseTransaction` row is created with `cheque_type = RENT` (or a `payment_type` indicating cash/bank transfer for rent)
**Then** the system posts a Journal Entry debiting AR — Tenants and crediting Rent Income, for the `FinancePMCProfile` resolved via `Lease.unit.parent_property.pmc` (FR-4)

**Given** a posted Journal Entry
**When** its `LedgerLine`s are inspected
**Then** debit total equals credit total equals `LeaseTransaction.total`, and the entry references `source_lease_transaction_id` and `source_status_transition` for traceability and idempotency (FR-4, AD-14)

**Given** the same triggering event fires more than once (e.g. a retried sync call from Story 2.1)
**When** the posting handler checks for an existing `JournalEntry` with the same `(lease_transaction_id, from_status, to_status)` tuple
**Then** the duplicate is skipped — exactly one Journal Entry is ever posted for that exact transition (AD-14)

**Given** any failure partway through constructing the Journal Entry
**When** the posting attempt does not complete
**Then** no partial or unbalanced entry lands in the Ledger — the posting is fully atomic (one DB transaction, all-or-nothing) (NFR-1, NFR-5)

**Given** a `Unit` with no resolvable `FinancePMCProfile` (no `FinancePMCProfile` exists for its PMC)
**When** a RENT `LeaseTransaction` is created for that `Unit`
**Then** posting fails loudly (logged error, no partial post) rather than silently skipping or guessing a PMC (FR-2 resolution note)

### Story 2.3: Cheque clearing posting

As the Finance system,
I want to post a Bank Journal entry when a cheque's `LeaseTransaction.status` transitions to `REALIZED`/`CREDITED`,
So that a cleared cheque moves from AR into an actual Bank balance in the Ledger.

**Acceptance Criteria:**

**Given** an existing `LeaseTransaction` with a prior Rent AR posting (Story 2.2) and a status of `BALANCE` or `CREDITED`
**When** `LeaseTransaction.status` transitions to `REALIZED` (or `CREDITED`, per the existing status flow)
**Then** the system posts a Journal Entry debiting Bank and crediting AR — Tenants, for the cheque amount, scoped to the same `FinancePMCProfile` as the original rent posting (FR-5)

**Given** a status transition to `REALIZED` that was previously `BALANCE`/`CREDITED`
**When** the transition event fires twice (e.g. a retried sync call)
**Then** exactly one new Journal Entry is posted — the idempotency check on `(lease_transaction_id, from_status, to_status)` prevents a duplicate (FR-5, AD-14)

**Given** a `LeaseTransaction` that has no prior Rent AR posting (e.g. its RENT-type original entry failed to post)
**When** its status transitions to `REALIZED`
**Then** the clearing posting still fails loudly if it cannot resolve the amount/AR balance it's meant to clear — no clearing entry is posted against a nonexistent AR balance (consistent with FR-4's fail-loudly precedent)

**And** the posting is fully atomic — no partial or unbalanced Bank Journal entry ever lands in the Ledger (NFR-1, NFR-5)

### Story 2.4: Bounce reversal posting

As the Finance system,
I want to reverse the original AR posting when a cheque bounces,
So that a bounced cheque's accounting impact reflects reality — the tenant still owes the money, but it never actually cleared the bank.

**Acceptance Criteria:**

**Given** an existing `LeaseTransaction` with a prior Rent AR posting (Story 2.2)
**When** `LeaseTransaction.status` transitions to `BOUNCED`
**Then** the system posts a reversing Journal Entry crediting AR — Tenants and debiting a Bounced Cheques suspense Account (not Bank — the cheque never cleared) for that transaction's amount (FR-6)

**Given** a BOUNCED transition
**When** the reversing entry is posted
**Then** its amount always matches the original posting for that `LeaseTransaction.id`, and it is a *new* Journal Entry carrying a `reversed_journal_entry_id` FK pointing at the original — the original entry is never edited or deleted (FR-6, AD-16)

**Given** a `LeaseTransaction` that legitimately cycles through `BOUNCED` more than once across a replace cycle (e.g. `BOUNCED → REPLACED → BOUNCED` again on a new cheque)
**When** each `BOUNCED` transition has a distinct `(from_status, to_status)` pair in its status history
**Then** each occurrence posts its own reversal — only an exact-match duplicate transition is treated as a duplicate and skipped (AD-14)

**And** the posting is fully atomic — no partial reversal ever lands in the Ledger (NFR-1, NFR-5)

### Story 2.5: Bounce fee posting

As the Finance system,
I want to post a Journal Entry for a bounce fee once one is charged,
So that the fee owed by the tenant becomes a Ledger fact, including any VAT.

**Acceptance Criteria:**

**Given** a `LeaseTransaction` that has been bounced (Story 2.4) and a bounce fee `Charge` row linked to it (created via the existing Units flow — Finance does not auto-create this `Charge`)
**When** the linked `Charge` row is detected
**Then** the system posts a Journal Entry debiting AR — Tenants (the fee is now owed) and crediting Bank Charges/Fee Income, for the resolved `FinancePMCProfile` (FR-7)

**Given** the linked `Charge.vat_amount` is non-zero
**When** the Journal Entry is constructed
**Then** it includes a separate VAT Payable line equal to `Charge.vat_amount` (FR-7)

**Given** the linked `Charge.vat_amount` is zero
**When** the Journal Entry is constructed
**Then** no VAT Payable line is added — the entry balances on the fee amount alone

**Given** no bounce fee `Charge` row exists yet for a bounced `LeaseTransaction`
**When** Finance checks for one to post against
**Then** it posts nothing and waits — Finance never creates the bounce-fee `Charge` row itself (FR-7 Out of Scope)

**And** the posting is fully atomic and idempotent on `(lease_transaction_id, from_status, to_status)`, consistent with the rest of the posting engine (NFR-1, AD-14)

### Story 2.6: Security deposit posting

As the Finance system,
I want to post a Journal Entry when a security deposit is recorded,
So that a deposit is correctly booked as a liability, never mistaken for rent income.

**Acceptance Criteria:**

**Given** a `Lease` is activated (or a security-deposit-tagged `LeaseTransaction` is recorded)
**When** the triggering event fires
**Then** the system posts a Journal Entry debiting Bank/AR and crediting Security Deposits Held (liability), for `Lease.security_deposit`, scoped to the resolved `FinancePMCProfile` (FR-8)

**Given** a posted security deposit entry
**When** the credited Account is inspected
**Then** it is always Security Deposits Held — never Rent Income, under any circumstance (FR-8)

**And** the posting is fully atomic and idempotent on `(lease_transaction_id, from_status, to_status)` (or the equivalent `Lease`-activation trigger key), consistent with the rest of the posting engine (NFR-1, AD-14)

### Story 2.7: Owner/PMC commission split posting

As the Finance system,
I want to post the owner/PMC commission split whenever a rent AR posting resolves to a PMC-managed Unit,
So that PMC commission liability and multi-owner rent splits are correctly reflected in the Ledger — including when the managing and owning PMCs differ.

**Acceptance Criteria:**

**Given** a `UnitOwner` migration adds `ownership_percent` (`NOT NULL`, mandatory backfill so every existing multi-owner `Unit`'s percentages sum to 100) as a prerequisite (AD-15)
**When** a rent AR posting (Story 2.2) resolves to a `Unit` that is PMC-managed
**Then** the system additionally posts a split crediting Commission Expense and debiting AP — PMC Commission, for the PMC's share computed from `Lease.commission`/`Unit.commission_percent`, including 5% UAE VAT via `Charge` (FR-9)

**Given** a `Unit` with `commission_percent = 8` and a rent posting of 10,000
**When** the commission split posts
**Then** the AP — PMC Commission credit equals 800 plus 5% VAT (40), for a total of 840 (FR-9)

**Given** a `Unit` with multiple `UnitOwner`s
**When** the commission split posts
**Then** the owner's net rent income (after commission) is split across owners proportional to their `ownership_percent` (FR-9)

**Given** the managing PMC's `FinancePMCProfile` differs from the owning PMC's `FinancePMCProfile` for a Unit (a cross-PMC management arrangement, both UAE, both 5% VAT)
**When** the commission split posts
**Then** it posts across both PMCs' Ledgers — commission expense debited in the owning PMC's books, commission income credited in the managing PMC's books — never within a single PMC's books alone (FR-9)

**Given** a `Unit`'s `UnitOwner` set does not sum to exactly 100 at posting time (a data-quality violation the backfill should have prevented)
**When** the posting engine detects this
**Then** the post fails loudly (logged, no partial post) rather than guessing a split (AD-15)

**And** the entire split (commission + multi-owner + cross-PMC, where applicable) posts as one atomic operation — no partial split ever lands in the Ledger (NFR-1, NFR-5)

## Epic 3: Financial Reporting API

A PMC/Owner-authenticated caller can retrieve Trial Balance, P&L, Balance Sheet, and Ageing for their PMC via the API (no UI — Phase 2).

### Story 3.1: Reporting auth & PMC-scoping foundation

As the Finance system,
I want reporting endpoints to authenticate via the same JWT as units-backend — signed with a dedicated JWT secret, not Django's general-purpose `SECRET_KEY` — and scope every query to the requesting user's reachable PMCs,
So that no report leaks another PMC's financial data, Finance never gains the ability to forge units-backend sessions/CSRF tokens, and the existing Units frontend can call Finance without any new auth mechanism.

**Acceptance Criteria:**

**Given** `utilities/config.py` currently sets `JWT_SECRET_KEY = settings.SECRET_KEY` — JWT signing piggybacks on Django's general-purpose secret, used elsewhere for sessions/CSRF/other signing
**When** this story is implemented
**Then** units-backend gets a new, dedicated `JWT_SECRET_KEY` env var (a fresh random value, independent of `SECRET_KEY`), `utilities/config.py` is changed to read it via `os.getenv("JWT_SECRET_KEY")`, and only this narrower secret is added to the shared root `.env` — Django's `SECRET_KEY` itself is never extracted into `.env` and never shared with Finance (AD-17)

**Given** the `.env` cutover
**When** it's applied
**Then** it's coordinated in the same rollout step as the `DB_HOST` cutover (Story 1.1) — a deliberate, single cutover, not incidental to an unrelated deploy

**Given** a request to any Finance reporting endpoint (Trial Balance, P&L, Balance Sheet, Ageing)
**When** the request carries a valid `Authorization: Bearer <token>` issued by units-backend
**Then** Finance validates it using the same `JWT_SECRET_KEY` and algorithm as units-backend — never a separate Finance-issued token (AD-17)

**Given** a request with a missing, expired, or invalid JWT
**When** it reaches any reporting endpoint
**Then** Finance rejects it (401) before any query runs

**Given** a valid JWT
**When** a reporting view needs to scope its query
**Then** it bridges the JWT payload (`{user_id, email, exp}`) to the corresponding `UserProfile` row via one shared helper function (defined once, reused by every reporting view — never re-derived per-endpoint), then calls `utilities.org_scope.get_pmc_ids_for_user()` and filters results to `FinancePMCProfile`s whose `pmc_id` is in the returned list (AD-18, AD-19)

**Given** a user who can reach PMC A but not PMC B
**When** they request a report scoped to PMC B
**Then** they receive no data for PMC B — scoping is enforced server-side on every reporting call, not just in the UI (AD-18)

**And** every reporting response follows the standard envelope: `prepare_response()` → `{content, message, status}` (AD-1, inherited platform AD-3)

### Story 3.2: Trial Balance report

As a PMC admin or Owner (via an authenticated API caller),
I want to retrieve the Trial Balance for my PMC and a date range,
So that I can confirm the Ledger is balanced and see every Account's debit/credit totals.

**Acceptance Criteria:**

**Given** the auth & scoping foundation exists (Story 3.1) and posted Journal Entries exist for a `FinancePMCProfile` (Epic 2)
**When** an authenticated, authorized caller requests Trial Balance for that `FinancePMCProfile` and a date range
**Then** the system returns every Account's total debit and credit for the period (FR-10)

**Given** any Trial Balance response
**When** the totals are checked
**Then** sum(debits) == sum(credits) across all Accounts — a structural invariant verified on every report generation, not just a display check (FR-10, NFR-3)

**Given** the report is a point-in-time/period snapshot document
**When** it's returned
**Then** it is returned whole, never paginated, per the spine's Structural Seed pagination rule

**And** the response is scoped strictly to the requesting user's reachable `FinancePMCProfile`s (Story 3.1) and follows the standard envelope (AD-1, AD-18)

### Story 3.3: Profit & Loss report

As a PMC admin or Owner (via an authenticated API caller),
I want to retrieve the Profit & Loss statement for my PMC and a date range,
So that I can see net profit/loss derived from Income and Expense Accounts.

**Acceptance Criteria:**

**Given** the auth & scoping foundation exists (Story 3.1) and posted Journal Entries exist for a `FinancePMCProfile` (Epic 2)
**When** an authenticated, authorized caller requests P&L for that `FinancePMCProfile` and a date range
**Then** the system returns all Income and Expense Account balances and the resulting net profit/loss (FR-11)

**Given** a P&L response and a Trial Balance response (Story 3.2) for the same `FinancePMCProfile`/period
**When** both are compared
**Then** the P&L's net figure equals sum(Income account balances) − sum(Expense account balances), independently recomputable from the Trial Balance output (FR-11)

**Given** the report is a point-in-time/period snapshot document
**When** it's returned
**Then** it is returned whole, never paginated (Structural Seed)

**And** the response is scoped strictly to the requesting user's reachable `FinancePMCProfile`s and follows the standard envelope (AD-1, AD-18)

### Story 3.4: Balance Sheet report

As a PMC admin or Owner (via an authenticated API caller),
I want to retrieve the Balance Sheet for my PMC as of a given date,
So that I can see Asset, Liability, and Equity balances and confirm the accounting equation holds.

**Acceptance Criteria:**

**Given** the auth & scoping foundation exists (Story 3.1) and posted Journal Entries exist for a `FinancePMCProfile` (Epic 2)
**When** an authenticated, authorized caller requests Balance Sheet for that `FinancePMCProfile` as of a given date
**Then** the system returns Asset, Liability, and Equity account balances as of that date (FR-12)

**Given** any Balance Sheet response
**When** the totals are checked
**Then** Assets total equals Liabilities + Equity total — a structural invariant for any as-of-date query (FR-12)

**Given** the report is a point-in-time snapshot document
**When** it's returned
**Then** it is returned whole, never paginated (Structural Seed)

**And** the response is scoped strictly to the requesting user's reachable `FinancePMCProfile`s and follows the standard envelope (AD-1, AD-18)

### Story 3.5: Ageing report

As a PMC admin or Owner (via an authenticated API caller),
I want to retrieve an Ageing report of outstanding AR bucketed by days overdue,
So that I can see which tenants are overdue and by how much.

**Acceptance Criteria:**

**Given** the auth & scoping foundation exists (Story 3.1) and unresolved `LeaseTransaction` rows exist (status not `REALIZED`) with `cheque_date`/due dates in the past, for a `FinancePMCProfile` (Epic 2)
**When** an authenticated, authorized caller requests Ageing for that `FinancePMCProfile`
**Then** the system returns outstanding AR — Tenants balances bucketed by days overdue: current, 1-30, 31-60, 61-90, 90+ (FR-13)

**Given** overdue days computed as `today − LeaseTransaction.cheque_date`
**When** bucket assignment is applied
**Then** bucket boundaries are inclusive of the lower bound (e.g., day 31 falls in 31-60, not 1-30) (FR-13)

**Given** a `LeaseTransaction` with status `BOUNCED` and `cheque_date` 45 days in the past, still unresolved
**When** Ageing is generated
**Then** it appears in the 31-60 bucket until resolved (FR-13)

**Given** Ageing is a naturally growing list (one row per open item), unlike the other three reports
**When** the response is returned
**Then** it MUST paginate via `prepare_response()`'s `paginator` argument, never nested inside `content` (Structural Seed, inherited platform AD-3) — the only one of the four reports that paginates

**And** the response is scoped strictly to the requesting user's reachable `FinancePMCProfile`s and follows the standard envelope (AD-1, AD-18)

## Epic 4: Bank Reconciliation

An operator can upload a bank statement and reconcile it against posted Ledger entries — standalone once Epic 2 exists.

### Story 4.1: Manual bank statement import

As an operator,
I want to upload a CSV of bank statement lines for a PMC's bank account,
So that Finance has statement data to reconcile against posted Ledger entries.

**Acceptance Criteria:**

**Given** a `FinancePMCProfile` exists (Epic 1)
**When** an operator uploads a CSV of bank statement lines (date, amount, reference) for that `FinancePMCProfile`
**Then** the system creates one statement-line record per CSV row, associated with that `FinancePMCProfile` and its bank account (FR-14)

**Given** a CSV with N rows
**When** the upload completes
**Then** exactly N statement-line records exist, scoped to the correct `FinancePMCProfile` — no cross-PMC leakage

**And** this is manual CSV import only — no live bank feed/API integration exists in Phase 1 (FR-14 Out of Scope)

### Story 4.2: Match statement lines to Ledger entries

As an operator,
I want the system to suggest matches between imported statement lines and unreconciled Bank Journal entries, confirm or reject each suggestion, and manually adjust a match myself when the suggestion is wrong or missing,
So that Bank Reconciliation always completes correctly, even when the simple heuristic doesn't find (or gets wrong) the right pairing.

**Acceptance Criteria:**

**Given** imported statement lines (Story 4.1) and unreconciled Bank Journal entries for the same `FinancePMCProfile`
**When** the matching logic runs
**Then** it suggests matches by amount and date proximity — a simple exact-amount + date-window heuristic, no fuzzy/ML matching (FR-15, NFR-2)

**Given** a suggested match
**When** an operator confirms it
**Then** both the statement line and the Ledger entry are marked reconciled (FR-15)

**Given** a suggested match
**When** an operator rejects it
**Then** neither side is marked reconciled, and both remain available for a different match

**Given** a statement line with no suggested match, or one whose suggested match the operator rejected
**When** the operator manually selects a different unreconciled Bank Journal entry to pair it with
**Then** the system accepts the manual pairing and marks both sides reconciled — a manual match is not restricted to the heuristic's suggestions

**Given** a statement line and Ledger entry already matched and confirmed (whether by suggestion or manual pairing)
**When** an operator wants to correct a wrong match
**Then** they can un-reconcile the pair, returning both sides to unreconciled status, and then confirm a different pairing

**Given** a Bank Journal entry already matched and confirmed
**When** the system attempts to suggest or manually pair it with a second statement line
**Then** it is rejected — an entry cannot be matched to more than one statement line at a time (FR-15)
