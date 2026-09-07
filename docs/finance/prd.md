---
title: Units Finance Module
created: 2026-08-31
updated: 2026-09-01
status: final
---

# PRD: Units Finance Module

**Note on this revision (2026-09-01):** during epics/stories breakdown, the user (Rahul) clarified the real domain model: there is no standalone "Company" entity. `PropertyManagmentCompany` ("PMC") already IS the accounting entity, 1:1 — each PMC gets its own books. `Organization` sits above PMC (`Organization` → `PMC` → `Property` → `PropertyBlocks` → `Unit`, all existing units-backend tables). This revision replaces every prior "Company" reference with `FinancePMCProfile` (a new Finance-owned table holding only accounting-specific settings, keyed by the existing `PropertyManagmentCompany.id`) and drops FR-2 (the previously-proposed Unit-to-Company mapping table), since Unit already resolves to its PMC via the existing `Unit.parent_property.pmc` chain. Organization-level consolidated reporting and the units-frontend UI are both explicitly deferred to Phase 2 — see §6.2.

## 0. Document Purpose

This PRD scopes Phase 1 of the Finance module for the Units property-management backend, for a solo developer building against a 5-day deadline. It is written for one reader who is also the sole builder — its job is to fix scope so it doesn't drift mid-build, not to coordinate a team. It builds on `_bmad-output/brainstorming/brainstorm-finance-module-2026-08-30/brainstorm-intent.md`, which captured the brainstorming session and the confirmed integration decisions against the existing Units schema (`Lease`, `LeaseTransaction`, `Unit`, `Charge`, `Bank`). This PRD does not duplicate that schema detail — it references it and turns it into functional requirements. Terms are defined once in the Glossary and used verbatim throughout.

## 1. Vision

Units currently manages properties, leases, tenants, and cheque-based rent collection, but has no double-entry accounting layer — financial facts (rent due, cheques received, VAT charged) live as columns on operational tables (`Lease`, `LeaseTransaction`, `Charge`), not as ledger postings a trial balance or balance sheet can be built from. The Finance module adds that missing layer: a Django-based microservice, running in its own container, sharing the existing Units Postgres database, that reads the operational tables Units already writes and produces proper accounting artifacts — General Ledger, Journal, Trial Balance, Profit & Loss, Balance Sheet, Ageing, and Bank Reconciliation — while adding Accounts Receivable/Payable postings for cheque lifecycle events (received, deposited, cleared, bounced) and the owner/PMC commission split.

It matters because Units' target clients — property owners and PMCs in the UAE and USA — require on-premises deployment with no external SaaS dependency for their financial data, and because without a real ledger, questions like "what's our trial balance this month" or "which tenants are overdue" have no authoritative answer today; they'd need to be reconstructed ad hoc from `LeaseTransaction` rows.

For this Phase 1, "done" means: the module runs as one more service in the existing `docker-compose.yml`, connects to the existing Postgres, and produces correct Ledger/Journal/Trial Balance/P&L/Balance Sheet/Ageing/Bank Reconciliation output for **one or more UAE PMCs (via `FinancePMCProfile`)**, starting clean from go-live (no historical backfill — see §6.2). Multi-PMC support is in scope, but deliberately narrowed: every Phase 1 `FinancePMCProfile` is UAE-registered (AED base currency, 5% VAT), so the module never has to reconcile cross-currency or cross-tax-regime rules in this phase. A non-UAE PMC is an explicit, deferred Phase 2 concern (see §6.2, §8); the `FinancePMCProfile` model supports it without rework, but Phase 1 activates and validates only UAE PMCs. Phase 1 also ships backend and API only — the units-frontend UI that surfaces this to logged-in Owners/PMCs is explicit Phase 2 scope (see §6.2).

## 2. Target User

### 2.1 Jobs To Be Done

- As the Units platform (system-to-system), I need a service that turns cheque and lease events into proper double-entry ledger postings, so financial reports are derived from accounting truth, not recomputed ad hoc from operational tables each time.
- As a property owner or PMC admin (end user, via the existing Units frontend calling this module's API), I need to see Trial Balance, P&L, Balance Sheet, and Ageing for my company's books, so I can answer basic financial questions without exporting data to spreadsheets or a third-party accounting tool.
- As Rahul (the builder), I need this module to drop into the existing docker-compose setup with minimal integration risk, so a 5-day solo build doesn't get consumed by deployment surprises.

### 2.2 Non-Users (v1)

- End tenants (renters) are not direct users of this module in Phase 1 — they interact with Units' existing lease/payment flows; Finance is a backend/reporting layer, not tenant-facing.
- Accountants/bookkeepers doing manual journal entry are not a Phase 1 audience — Phase 1 posts journals automatically from existing events; manual journal entry UI is out of scope (see §6.2).

### 2.3 Key User Journeys

*Downscaled per PRD Discipline: this is an internal, largely system-to-system PRD with a single technical operator (Rahul) and API consumers that are existing Units roles already documented in `units_workflow.md`. Full narrative UJs would restate JTBDs above without adding information. Two lighter-form journeys anchor the two consequential flows.*

- **UJ-1. Owner checks Trial Balance for their company.** An Owner/PMC admin, already authenticated in the existing Units app, opens a new "Finance" section and requests Trial Balance for the current month; the Units frontend calls the Finance module's API and renders the response inline. → FR-10.
- **UJ-2. A tenant's post-dated cheque bounces.** A cheque recorded in `LeaseTransaction` transitions to `BOUNCED` (via existing Units flows); the Finance module detects this transition, reverses the original AR posting, posts a bounce-fee `Charge`-linked entry, and the bounce is visible in the next Ageing/PDC report the Owner/PMC pulls. → FR-6, FR-7, FR-13.

## 3. Glossary

- **Company** (business/colloquial term; the accounting entity is `FinancePMCProfile`, see below) — A legal entity for which a distinct chart of accounts, fiscal year, and base currency is maintained. In this system, a "Company" maps 1:1 to an existing units-backend `PropertyManagmentCompany` ("PMC") — a PMC IS the accounting entity, not a separate thing. `Organization` (existing units-backend model) sits one level above PMC: one Organization can have multiple PMCs, each with its own books (e.g. Al-Yusuf Organization → 3 PMCs, each independently reportable; Organization-level consolidation is Phase 2, see §6.2). The existing chain `Organization` → `PropertyManagmentCompany` → `Property` → `PropertyBlocks` → `Unit` is reused as-is; Finance adds no new relationship to it.
- **FinancePMCProfile** — A new Finance-owned table holding only the accounting-specific settings for one existing `PropertyManagmentCompany` (base currency, country, fiscal year start month, VAT settings), keyed by a required `pmc_id` field holding the value of that `PropertyManagmentCompany.id`. Every Ledger/Account/JournalEntry FK in Finance references `FinancePMCProfile.id`, never a PMC row directly. Every `Unit` resolves to its `FinancePMCProfile` via the existing chain (`Unit.parent_property.pmc.id` → matching `FinancePMCProfile.pmc_id`) — no new Unit-level mapping is created (see FR-2).
- **Ledger** — The Finance module's own table of posted, immutable accounting entries (debits/credits against accounts), the system of record for all derived reports (Trial Balance, P&L, Balance Sheet, Ageing).
- **Journal** — A named grouping of Ledger entries by source/type (e.g., Sales Journal for rent AR, Bank Journal for cheque clearing); each Journal has a 1:many relationship to the Ledger entries it groups.
- **Account** — A node in a `FinancePMCProfile`'s chart of accounts (e.g., "Rent Income," "Security Deposits Held," "VAT Payable"). Every Ledger entry references exactly one Account.
- **Chart of Accounts (CoA)** — The full set of Accounts defined for a `FinancePMCProfile`.
- **Journal Entry** — One atomic, balanced (debits = credits) posting to the Ledger, composed of two or more Ledger lines.
- **AR (Accounts Receivable)** — Amounts owed to a `FinancePMCProfile` by tenants (unpaid rent, uncleared cheques), tracked as Ledger balances against tenant-linked Accounts.
- **AP (Accounts Payable)** — Amounts a `FinancePMCProfile` owes (e.g., commission payable to a different managing PMC, or vendor-style payables). Phase 1 AP is scoped to owner/PMC commission payables (see FR-9).
- **Trial Balance** — A report listing every Account's debit/credit balance for a period, used to confirm the Ledger is balanced.
- **P&L (Profit & Loss)** — A report of income and expense Accounts over a period, deriving net profit/loss.
- **Balance Sheet** — A report of asset, liability, and equity Account balances as of a point in time.
- **Ageing** — A report bucketing outstanding AR (and AP) by how overdue they are (e.g., current, 30/60/90+ days), derived from unresolved `LeaseTransaction` rows and their Ledger postings.
- **Bank Reconciliation** — The process/report of matching `LeaseTransaction` cheque/payment rows against actual bank statement data to confirm cleared amounts agree.
- **PDC (Post-Dated Cheque)** — A cheque dated for future deposit, recorded in the existing `LeaseTransaction` table with `payment_type = PDC`; its lifecycle is tracked via `LeaseTransaction.status` (BALANCE → CREDITED → REALIZED / BOUNCED).
- **Commission** — The share of rent revenue payable to a PMC when a PMC manages a Unit, sourced from `Lease.commission` / `Unit.commission_percent`.
- **Security Deposit** — A refundable amount held from a tenant, sourced from `Lease.security_deposit`, booked as a Ledger liability distinct from rent AR.
- **Units backend** — The existing property-management Django backend (`Lease`, `LeaseTransaction`, `Unit`, `Charge`, `Bank`, `Tenant`, `UnitOwner` models) that the Finance module shares a database with and extends.

## 4. Features

### 4.1 Chart of Accounts & FinancePMCProfile Setup

**Description:** Before any Ledger posting can happen, at least one existing UAE `PropertyManagmentCompany` must have a `FinancePMCProfile` (accounting settings) and Chart of Accounts; Phase 1 supports activating multiple PMCs, provided each is UAE-registered (AED base currency, 5% VAT). Given the 5-day timeline, this is intentionally minimal: a seed script/fixture defines a standard CoA (Rent Income, Security Deposits Held, VAT Payable, Bank, AR — Tenants, AP — PMC Commission, Commission Expense, Bank Charges/Fees) per `FinancePMCProfile`, not a full configurable CoA builder UI. `[ASSUMPTION: FinancePMCProfile records are created via a management command/fixture, not an admin UI, for Phase 1 — a UI is out of scope given the timeline.]` The `FinancePMCProfile`/CoA model is deliberately generic so a non-UAE PMC can be activated in Phase 2 without rework (see §6.2), but Phase 1 validation and testing covers UAE PMCs only.

**Functional Requirements:**

#### FR-1: FinancePMCProfile creation

An operator (Rahul, via Django management command or admin) can create a `FinancePMCProfile` record — accounting settings (base currency, fiscal year start month, country) — for an existing `PropertyManagmentCompany`, identified by `pmc_id`. This activates that PMC for Finance; it does not create a new legal entity, since the PMC already exists in units-backend. Phase 1 supports activating multiple PMCs, but every `FinancePMCProfile` created in Phase 1 must be UAE-registered (base currency AED, country UAE, 5% VAT).

**Consequences (testable):**
- A created `FinancePMCProfile` has a non-null `pmc_id`, base currency, and country; Phase 1 validation rejects (or flags) a non-UAE country value, since cross-country logic is not built yet.
- `pmc_id` must reference an existing `PropertyManagmentCompany.id`; creation fails if no such PMC exists.
- Multiple `FinancePMCProfile` records can coexist and each resolve independently to their own Units (via the existing PMC→Property→Unit chain), Ledger, and reports (FR-10–FR-13) without cross-contamination.

**Out of Scope:**
- Non-UAE PMCs (e.g. USA) — the `country` field and model support it, but Phase 1 only builds and tests the UAE code path (single currency, single tax regime). See §6.2.

#### FR-2: [REMOVED — Unit-to-PMC resolution reuses the existing chain]

There is no new Unit-to-Company/PMC mapping table. `Unit` already resolves to its `PropertyManagmentCompany` via the existing chain `Unit.parent_property.pmc` (units-backend, `property` app); Finance resolves a `Unit`'s `FinancePMCProfile` by looking up `FinancePMCProfile.pmc_id == Unit.parent_property.pmc.id`. This FR number is retired rather than reused, so later FRs' references to "FR-2" in earlier drafts are traceable to this note.

**Consequences (testable):**
- Every `Unit` referenced by an active `Lease` has a resolvable `FinancePMCProfile` at the time any Ledger posting is attempted for that Unit (via the existing PMC chain, provided a `FinancePMCProfile` exists for that PMC per FR-1); posting fails loudly (logged error, no partial post) if no `FinancePMCProfile` exists for the resolved PMC, or if `Unit.parent_property` or its `pmc` is unset.

#### FR-3: Standard Chart of Accounts seed

The system provides a seed/fixture that creates the standard CoA (as listed above) for a new `FinancePMCProfile` on creation.

**Consequences (testable):**
- After FR-1 creates a `FinancePMCProfile`, the standard 7+ Accounts listed above exist for that `FinancePMCProfile` with correct Account types (Asset/Liability/Income/Expense).

**Notes:** `[NOTE FOR PM]` A configurable CoA editor is explicitly deferred — revisit only if a client needs non-standard accounts before Phase 2.

### 4.2 Ledger & Journal Posting Engine

**Description:** The core of the module: a posting engine that reads `Lease`, `LeaseTransaction`, and `Charge` rows and writes balanced Journal Entries to the Finance module's own Ledger tables. This is triggered by `LeaseTransaction` state changes (new row created, `status` transitions) rather than a manual entry UI. The Ledger starts clean from go-live — no historical backfill of pre-existing `LeaseTransaction` rows (see §6.2); only events occurring after Finance goes live are posted. Realizes UJ-2.

**Functional Requirements:**

#### FR-4: Rent AR posting on cheque receipt

When a new `LeaseTransaction` row is created with `cheque_type = RENT` (or `payment_type` indicating cash/bank transfer for rent), the system posts a balanced Journal Entry debiting AR — Tenants and crediting Rent Income for the relevant `FinancePMCProfile` (resolved via `Lease.unit.parent_property.pmc` → matching `FinancePMCProfile.pmc_id`).

**Consequences (testable):**
- One `LeaseTransaction` row of type RENT produces exactly one Journal Entry with debit = credit = `LeaseTransaction.total`.
- The Journal Entry references the source `LeaseTransaction.id` for traceability.

#### FR-5: Cheque clearing posting

When `LeaseTransaction.status` transitions to `REALIZED` (or `CREDITED`, per existing status flow), the system posts the corresponding Bank Journal entry (debit Bank, credit AR — Tenants) for the cheque amount.

**Consequences (testable):**
- A status transition to REALIZED that was previously BALANCE/CREDITED produces exactly one new Journal Entry, not a duplicate if the transition event fires twice (idempotency check on `LeaseTransaction.id` + target status).

#### FR-6: Bounce reversal posting

When `LeaseTransaction.status` transitions to `BOUNCED`, the system reverses the original AR posting: credit AR — Tenants, debit a Bounced Cheques suspense Account (not Bank — the cheque never actually cleared the bank) for that transaction's amount.

**Consequences (testable):**
- A BOUNCED transition always produces a reversing entry whose amount matches the original posting for that `LeaseTransaction.id`.

#### FR-7: Bounce fee posting

When a bounce fee `Charge` row is linked to a bounced `LeaseTransaction`, the system posts a Journal Entry debiting AR — Tenants (the fee is now owed) and crediting a Bank Charges/Fee Income Account, including any VAT per `Charge.tax_code`.

**Consequences (testable):**
- If the linked `Charge.vat_amount` is non-zero, the Journal Entry includes a separate VAT Payable line equal to `Charge.vat_amount`.

**Out of Scope:**
- Automatically creating the bounce-fee `Charge` row — Phase 1 assumes the existing Units flow (or a manual step) creates it; Finance only posts once it exists. `[ASSUMPTION]`
- The collections/follow-up workflow itself (a task/reminder with due date for the property team to chase a replacement cheque, per brainstorm-intent.md §5) — Finance posts the accounting entries for a bounce, but does not own or generate the follow-up task. `[NOTE FOR PM]` Who owns this workflow (existing Units complaint/task system vs. a new mechanism) is unresolved — see Open Question 8.

#### FR-8: Security deposit posting

When a `Lease` is activated (or a security-deposit-tagged `LeaseTransaction` is recorded), the system posts a Journal Entry debiting Bank/AR and crediting Security Deposits Held (liability), for `Lease.security_deposit`.

**Consequences (testable):**
- Security deposit amounts never post to Rent Income; they always post to Security Deposits Held.

#### FR-9: Owner/PMC commission split posting

When a rent AR posting (FR-4) resolves to a `Lease` where the Unit is PMC-managed, the system additionally posts a split: crediting Commission Expense / debiting AP — PMC Commission for the PMC's share, computed from `Lease.commission` or `Unit.commission_percent`, including 5% UAE VAT via `Charge`.

**Consequences (testable):**
- For a Unit with `commission_percent = 8` and rent posting of 10,000, the AP — PMC Commission credit equals 800 plus 5% VAT (40), for a total of 840.
- For a Unit with multiple `UnitOwner`s, the owner's net rent income (after commission) is split across owners proportional to their recorded ownership share. `[ASSUMPTION: UnitOwner carries a percentage/share field usable for this split — confirm field name during build.]`
- When the managing PMC's `FinancePMCProfile` differs from the owning PMC's `FinancePMCProfile` for a Unit (both UAE, both 5% VAT) — i.e. a cross-PMC management arrangement — the split posts across both PMCs' Ledgers (commission expense debited in the owning PMC's books, commission income credited in the managing PMC's books) rather than within a single PMC's books.

**Out of Scope:**
- Cross-country commission splits (managing or owning PMC registered outside the UAE) — deferred to Phase 2 alongside non-UAE PMC support (§6.2).

**Feature-specific NFRs:**
- All postings in this feature must be atomic (a `LeaseTransaction` event either fully posts a balanced Journal Entry or posts nothing — no partial/unbalanced entries ever land in the Ledger).

### 4.3 Reporting: Trial Balance, P&L, Balance Sheet, Ageing

**Description:** Read-only reporting endpoints/queries over the Ledger built by 4.2, scoped per `FinancePMCProfile` and period. Realizes UJ-1. Phase 1 ships these as backend API endpoints only (tested via Swagger/Postman/automated tests) — the units-frontend "Finance" section that renders these for a logged-in Owner/PMC user is Phase 2 (see §6.2).

**Functional Requirements:**

#### FR-10: Trial Balance report

The system can return, per `FinancePMCProfile` and date range, every Account's total debit and credit, confirming total debits = total credits.

**Consequences (testable):**
- For any `FinancePMCProfile`, sum(debits) == sum(credits) across all Accounts for a given period (structural invariant, not just a display check).

#### FR-11: Profit & Loss report

The system can return, per `FinancePMCProfile` and date range, all Income and Expense Account balances and the resulting net profit/loss.

**Consequences (testable):**
- Net P&L figure equals sum(Income account balances) − sum(Expense account balances) for the period, independently recomputable from FR-10's Trial Balance output for the same `FinancePMCProfile`/period.

#### FR-12: Balance Sheet report

The system can return, per `FinancePMCProfile`, as-of-date balances for Asset, Liability, and Equity accounts, with Assets = Liabilities + Equity.

**Consequences (testable):**
- Assets total equals Liabilities + Equity total for any as-of-date query (structural invariant).

#### FR-13: Ageing report (AR and PDC)

The system can return, per `FinancePMCProfile`, outstanding AR — Tenants balances bucketed by days overdue (current, 1-30, 31-60, 61-90, 90+), derived from unresolved `LeaseTransaction` rows (status not REALIZED) and their `cheque_date`/due date.

**Consequences (testable):**
- Overdue days are computed as `today − LeaseTransaction.cheque_date`, with bucket boundaries inclusive of the lower bound (e.g., day 31 falls in 31-60, not 1-30). A `LeaseTransaction` with status BOUNCED and `cheque_date` 45 days in the past appears in the 31-60 bucket until resolved.

**Notes:** `[NOTE FOR PM]` Separate AP ageing (PMC commission payable) is a likely fast-follow if time allows within the 5 days, otherwise Phase 1.1. Also, this is a narrower cut than brainstorm-intent.md §5 originally envisioned: the intent doc called for a dedicated PDC register view (cheques due for deposit, cheques overdue/bounced) as its own reporting surface. Phase 1 folds that need into the general AR ageing buckets above rather than building a separate PDC-specific view — flagging this as a deliberate scope narrowing, not an oversight, since a separate register is mostly a different slice of the same underlying data.

**Out of Scope:**
- A dedicated PDC register view distinct from general AR ageing (see note above) — deferred; Phase 1's ageing buckets serve this need generically.

### 4.4 Bank Reconciliation

**Description:** A minimal reconciliation capability: given a bank statement (manually uploaded, e.g. CSV) for a `FinancePMCProfile`'s bank account, mark which `LeaseTransaction`/Ledger Bank Journal entries correspond to which statement lines, surfacing unmatched entries on either side.

**Functional Requirements:**

#### FR-14: Manual bank statement import

An operator can upload a CSV of bank statement lines (date, amount, reference) for a `FinancePMCProfile`.

**Consequences (testable):**
- Uploading a CSV with N rows creates N statement-line records associated with the `FinancePMCProfile` and its bank account.

**Out of Scope:**
- Live bank feed/API integration — explicitly out of scope for Phase 1 (see §6.2).

#### FR-15: Match statement lines to Ledger entries

The system suggests matches between imported statement lines and unreconciled Bank Journal entries by amount and date proximity; an operator can confirm or reject a suggested match.

**Consequences (testable):**
- A confirmed match marks both the statement line and the Ledger entry as reconciled; an entry cannot be matched to more than one statement line.

**Feature-specific NFRs:**
- Given the 5-day budget, auto-match logic can be a simple exact-amount + date-window heuristic — no fuzzy/ML matching. `[ASSUMPTION]`

## 5. Non-Goals (Explicit)

- The Finance module is not becoming a general-ledger product for arbitrary businesses — it is purpose-built for Units' property-management domain and its existing schema.
- Finance is not replacing Units' lease, tenant, or property management flows — those remain fully owned by the existing Units backend.
- Finance is not providing a manual journal-entry UI for accountants in Phase 1 — all Phase 1 postings are system-generated from `LeaseTransaction`/`Lease` events.
- Finance is not integrating with any third-party accounting SaaS (QuickBooks, Xero, Odoo, etc.) — this is explicitly the reason it's being built in-house (on-prem requirement).
- Finance is not handling payroll, fixed-asset depreciation, budgeting, or tax return filing in Phase 1 (see brainstorm-intent.md §11 for later-phase scope).
- Finance does not touch EJARI (Dubai legal lease registration) or lease e-signature flows — these remain fully owned by the existing Units backend (per brainstorm-intent.md §10).

## 6. MVP Scope

### 6.1 In Scope

- Finance service as its own container, sharing the existing Units Postgres instance/database, reading and writing `Lease`, `LeaseTransaction`, `Unit`, `Charge`, `Bank` directly.
- **Multiple UAE PMCs** (all AED, all 5% VAT), each activated via its own `FinancePMCProfile` (§4.1) + standard seeded Chart of Accounts. Unit resolution reuses the existing `Unit.parent_property.pmc` chain — no new mapping table.
- Ledger starts clean from Finance's go-live date — **no historical backfill** of pre-existing `LeaseTransaction`/`Lease` data.
- Automatic Journal/Ledger posting for: rent AR, cheque clearing, cheque bounce + reversal, bounce fee, security deposit, owner/PMC commission split including multi-owner split and cross-PMC (UAE-to-UAE) commission splits (§4.2).
- Reporting: Trial Balance, P&L, Balance Sheet, Ageing — each scoped per `FinancePMCProfile` (§4.3), API-only in Phase 1 (see §6.2).
- Manual-import Bank Reconciliation with simple auto-match (§4.4).
- The `FinancePMCProfile` data model is built generically enough to support a non-UAE PMC later without rework, even though Phase 1 validates UAE PMCs only.

### 6.2 Out of Scope for MVP

- **Historical backfill.** Pre-existing `LeaseTransaction`/`Lease` rows created before Finance's go-live are not posted to the Ledger; the Ledger's history begins at go-live. `[NOTE FOR PM]` This means Trial Balance/P&L/Balance Sheet will only reflect activity from go-live forward for some time — flag this clearly to Owners/PMCs viewing early reports so they don't mistake it for missing data.
- **Non-UAE PMCs (e.g. a USA entity) — explicitly deferred to Phase 2.** Phase 1 validates the full pipeline (posting → reporting) across one or more UAE PMCs only; a non-UAE PMC, and any cross-currency or cross-tax-regime logic that comes with it, moves to Phase 2.
- Cross-country commission splits (FR-9 Out of Scope) — deferred alongside non-UAE PMC support.
- Manual journal-entry UI for accountants — deferred; Phase 1 posting is fully automated (see §5).
- Live bank feed/API integration for reconciliation — deferred to Phase 1.1+; Phase 1 is manual CSV import only.
- Multi-currency FX conversion overlay for consolidated cross-PMC reporting — not needed yet since all Phase 1 PMCs share one currency (AED); revisit once a non-UAE PMC (Phase 2) exists.
- Configurable Chart of Accounts editor — deferred; Phase 1 uses the fixed seeded CoA (§4.1).
- Tax return filing, fixed-asset/depreciation schedules, budgeting/forecasting — deferred to a later phase per brainstorm-intent.md.
- Event/webhook layer for host-app notifications (e.g., push tenant notification on bounce) — deferred; Phase 1 relies on direct DB read since Finance shares the database (per brainstorm-intent.md §8 revision).
- Separate AP ageing report (PMC commission payable ageing) — likely Phase 1.1, not a hard Phase 1 requirement.
- **Organization-level consolidated reporting — deferred to Phase 2.** Summing Trial Balance/P&L/Balance Sheet/Ageing across all PMCs under one `Organization` (via `PropertyManagmentCompany.organization_id`) is not built in Phase 1, which reports strictly per-`FinancePMCProfile`. This is achievable later without a data model change, since `FinancePMCProfile.pmc_id` already anchors to the correct PMC row — Phase 2 only needs a new aggregation query, not a schema change. (Clarified 2026-09-01, replaces the earlier "Company" framing.)
- **units-frontend UI — deferred to Phase 2.** Phase 1 ships the Finance backend and reporting API only (FR-10–FR-13), verified via Swagger/Postman/automated tests. The "Finance" section in the existing Units Angular frontend — where a logged-in Owner or PMC admin views Trial Balance/P&L/Balance Sheet/Ageing directly (as narratively described in UJ-1, §2.3) — is real, intended, and not removed from this document, but is explicitly out of scope for this Phase 1 build. (Clarified 2026-09-01.)

## 7. Success Metrics

**Primary**
- **SM-1**: Trial Balance always balances (debits = credits) for every `FinancePMCProfile`, checked automatically on every report generation. Validates FR-10.
- **SM-2**: Finance module deploys into the existing `docker-compose.yml` and starts successfully alongside the existing Units services with zero manual schema conflicts. Validates the integration goal in §1.

**Secondary**
- **SM-3**: 100% of new `LeaseTransaction` rows (RENT type, created after Finance goes live) produce a corresponding Journal Entry within the same request/transaction cycle. Validates FR-4.
- **SM-4**: Ageing report bucket assignment matches manual spot-check for a sample of at least 10 real overdue `LeaseTransaction` rows. Validates FR-13.

**Counter-metrics (do not optimize)**
- **SM-C1**: Posting *speed* should not be optimized at the expense of atomicity (§4.2 NFR) — a fast but occasionally-unbalanced Ledger is worse than a slightly slower fully-correct one. Counterbalances SM-3.

*Given hobby/internal-tool-adjacent stakes and a 5-day solo build, this section stays intentionally lean — the real test is "does Rahul trust the Trial Balance enough to show an Owner."*

## 8. Open Questions

1. Does `UnitOwner` already carry a percentage/ownership-share field usable for the multi-owner rent split in FR-9, or does this need to be added? (`[ASSUMPTION]` in FR-9.)
2. What is the exact `LeaseTransaction.status` transition Finance should listen for as the trigger mechanism — a Django signal on save, a periodic poll, or a shared event bus? This is a build-time architecture decision not resolved in this PRD; recommend resolving in `bmad-architecture` immediately after this PRD, given it affects nearly every FR in §4.2.
3. ~~Which Companies/Units need to be live for the initial demo~~ — **Resolved:** multiple UAE PMCs (via `FinancePMCProfile`) in scope for Phase 1 (§6.1); any non-UAE PMC is explicit Phase 2 scope (§6.2).
4. What VAT rate and rule set applies for UAE Companies (flat 5% assumed — confirm this is still current and correct)?
5. Concurrency: since Finance now writes `LeaseTransaction.status`-adjacent data while the existing Units app may also write to the same rows, what's the conflict rule if both write concurrently? (Carried over from brainstorm-intent.md §11 — unresolved.)
6. ~~Does Phase 1 need to backfill Ledger entries~~ — **Resolved:** no backfill; the Ledger starts clean from Finance's go-live date (§6.2).
7. Since reports (Trial Balance, P&L, Balance Sheet) will only reflect post-go-live activity for a while (no backfill), is there a minimum "look sensible" data window needed before these reports are shown to Owners/PMCs, or is an honest "data from {date} onward" caveat in the UI sufficient?
8. Who owns the bounce collections/follow-up workflow (task + due date for the property team to chase a replacement cheque, per brainstorm-intent.md §5)? Finance posts the accounting side of a bounce (FR-6, FR-7) but does not generate or track this follow-up task — does it belong in the existing Units complaint/task system, or is a new lightweight mechanism needed? Carried over unresolved from brainstorm-intent.md.
9. Per-actor reporting visibility: the existing Units backend already scopes cheque visibility differently per role (Owner sees `payment/rental_payments`, PMC sees `lease/all-cheques`, per `units_workflow.md`). This PRD's reporting FRs (FR-10–FR-13) are written per-`FinancePMCProfile` but don't yet specify whether/how they need equivalent per-role scoping (e.g., should an Owner's Trial Balance view exclude PMC-internal commission detail?). **Partially resolved** in `ARCHITECTURE-SPINE.md` AD-18 (PMC-level scoping via `get_pmc_ids_for_user()`) — the finer-grained question of commission-detail redaction within a PMC an Owner can already see remains open.
10. Reporting migration: the existing Units app currently computes rent analytics, cheque aging, revenue dashboards, and property comparison client-side/in the property app (per `units_workflow.md`). Once Finance's Ageing/Trial Balance/P&L/Balance Sheet exist, do these existing reports get replaced by calls to Finance's API, or keep running in parallel (risk of the two disagreeing)? Carried over unresolved from brainstorm-intent.md §10/§11 — not a Phase 1 blocker, but should be decided before Phase 1 ships to avoid confusing dual sources of truth.

**`[NOTE FOR PM]` Timeline risk, updated:** dropping backfill removes the largest single risk from the 5-day plan (unknown historical data quality). Multi-PMC (UAE-only) is a much smaller risk than the original any-country version, since it avoids new currency/tax-regime logic — the main remaining complexity is the cross-PMC commission-split posting in FR-9, which is now the highest-risk single item in the build. Recommend prototyping FR-9's cross-PMC split first, since if it proves too complex for the timeline, single-PMC-only is a safe fallback that doesn't require touching any other FR.

## 9. Assumptions Index

- §4.1 FR-1 note: `FinancePMCProfile` records are created via management command/fixture, not an admin UI, for Phase 1.
- §4.1 / §6.1: Phase 1 supports multiple PMCs (via `FinancePMCProfile`) but every one is assumed UAE-registered (AED, 5% VAT); non-UAE PMCs are Phase 2 scope.
- **2026-09-01 domain correction:** the original draft assumed a standalone "Company" model, distinct from `PropertyManagmentCompany`. Corrected per user clarification: PMC IS the accounting entity (1:1, via the new `FinancePMCProfile` table); `Organization` is the roll-up level above PMC. FR-2 (Unit-to-Company mapping) was dropped as a result — the existing `Unit.parent_property.pmc` chain is reused instead. Organization-level consolidated reporting and the units-frontend UI are both explicitly deferred to Phase 2 (§6.2).
- §4.2 FR-7 Out of Scope: Finance does not auto-create bounce-fee `Charge` rows; assumes they already exist via the existing Units flow.
- §4.2 FR-9: `UnitOwner` is assumed to carry a usable ownership-share field for multi-owner rent splitting (see Open Question 1).
- §4.4 FR-15 NFR: Auto-match logic for bank reconciliation is a simple exact-amount + date-window heuristic, not fuzzy/ML matching.
