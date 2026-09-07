# Finance Module - Brainstorm Intent

## 1. Project Intent

Build a standalone, self-hosted Finance module for a property management SaaS, deployable on-premises for UAE and USA clients who require in-house/on-prem hosting of financial data.

## 2. Scope (Phase 1)

Phase 1 feature list:

- Ledger
- Journal
- Accounts Receivable (AR)
- Accounts Payable (AP)
- Bank Reconciliation
- Ageing
- Trial Balance
- Profit & Loss (P&L)
- Balance Sheet

Modeled loosely on Odoo Accounting's structure (reference nav groupings: Customers / Vendors / Accounting / Review / Reporting / Configuration). This is Phase 1 only — later phases will cover the remaining Odoo-equivalent nav items not listed above.

## 3. Hard Architectural Constraints (Non-Negotiable)

- Finance ships as a separate, standalone containerized service — never runs in-process with the host app.
- **Revised:** Finance shares the existing app's Postgres instance/database (not a dedicated database) — this reverses the original "own dedicated Postgres" brainstorm decision, made once the existing schema (Section 10) was reviewed.
- Finance reads AND writes existing tables directly (`Lease`, `LeaseTransaction`, `Unit`, `Charge`, `Bank`) — it is a co-owner of that data, not merely a consumer of it via API.
- Finance also owns and writes its own new tables in the same database for Ledger, Journal, Trial Balance, P&L, Balance Sheet, and Ageing (these have no existing analog — see Section 10).
- The Finance container itself is still always a separate service/container from the host app, even though the database is now shared.
- Docker/networking: Finance's own container still needs a namespaced port for its own API, but no longer needs its own Postgres container or data volume — it connects to the existing Postgres instance/network.

## 4. Domain Model

- Hierarchy: **Tenant → Company → Ledger/Books**.
- One tenant can own multiple legal companies (e.g. a UAE freezone entity and a USA LLC).
- Each company has its own: chart of accounts, fiscal year, base currency, and tax rules (UAE VAT/Corporate Tax vs. USA tax).
- Every property/unit maps to exactly one company for revenue posting.
- Two billing-party types:
  - **Property Owner** (direct)
  - **PMC** (Property Management Company)
- Per-property commission/revenue-split rules apply between these parties, may cross company boundaries, and can carry different tax treatment per company (e.g. UAE PMC commission attracts 5% VAT, USA does not).
- **Revised (Section 10):** `commission` already exists as a field on `Lease` (with `commission_percent` as a `Unit`-level default) — Finance derives the PMC/owner split from this existing field rather than a new commission table. VAT on commission is computed via the existing `Charge` model (`tax_code` %, auto-computed `vat_amount`).
- **Revised (Section 10):** `Unit` can have multiple `UnitOwner`s — Finance's revenue-split logic must extend beyond a simple Owner-vs-PMC split to support dividing one unit's rent across multiple owners plus the PMC commission.

## 5. Core Payment Instrument — PDC (Post-Dated Cheques)

**Revised to use the existing schema directly** (Section 10) rather than a new dedicated sub-ledger table.

- Tenants (renters) commonly pay 12/24 months of rent upfront via post-dated cheques, recorded in the existing `LeaseTransaction` table (`payment_type = PDC`, `cheque_date`/`cheque_number`, FK → `Bank` for origin and settlement).
- Cheque lifecycle uses `LeaseTransaction.status`: **BALANCE → CREDITED → REALIZED / BOUNCED**. Finance reads and writes this status directly (no separate PDC table).
- Each status transition triggers Finance to post the corresponding Ledger/Journal entry (e.g. BOUNCED reverses AR and adds a penalty/fee line, sourced via `Charge` when applicable).
- Bounce handling requires: a bounce fee (modeled as a `Charge` row linked to the `LeaseTransaction`) + a collections/follow-up flag with due date for the property team.
- Needs a PDC register/ageing view (new Finance reporting capability) built by querying `LeaseTransaction` rows by status and `cheque_date`, surfacing cheques due for deposit and cheques overdue/bounced needing follow-up.

## 6. Security Deposits

**Revised to use the existing schema directly.**

- `security_deposit` already exists as a field on `Lease` (and a baseline default on `Unit`) — Finance treats this as the authoritative source amount, not a re-entered value.
- Finance still books it as a refundable **liability** in its own Ledger (new tables, Section 3), keyed to the `Lease` record, distinct from rent AR.
- Refund flow: Finance nets the deposit against any pending dues (unpaid `LeaseTransaction` rows, damage-related `Charge` rows) before computing the refund balance returned to the tenant.

## 7. Multi-Currency

- UAE properties/payments in AED; USA properties/payments in USD.
- Per-company base currency — no shared/global currency assumption.
- Cross-company consolidated reporting requires an FX conversion overlay that is **reporting-only** and never alters underlying local-currency ledger entries (keeps each company's books legally clean).

## 8. Integration Model

- **Revised:** since Finance shares the database and reads/writes existing tables directly (Section 3, Section 10), the event/webhook layer is no longer the primary integration path for state that lives in shared tables — direct row read/write replaces polling or event-driven sync for `LeaseTransaction`, `Lease`, `Unit`, `Charge`, `Bank`.
- An event/webhook layer (e.g. `invoice.paid`, `cheque.bounced`, `cheque.cleared`) is still useful for the host app's *own* UI/notification needs (e.g. push a tenant notification when a cheque bounces), but is now a convenience layer, not the system of record for financial state.
- Finance's genuinely new data (Ledger, Journal, Trial Balance, P&L, Balance Sheet, Ageing) lives only in Finance's own new tables — the host app reads these via Finance's reporting API, since it has no existing analog to query directly.

## 10. Integration with Existing Units Backend

Grounded in `units_workflow.md` and the confirmed schema below (existing Tenant/Owner/PMC system). No new entities invented for data Finance already has access to — Finance extends this schema rather than duplicating it.

**Confirmed existing schema (source of truth for Finance's read/write layer):**

- `Bank` (`payment/models.py`) — bank details: name, city (FK→Country's City), branch/IFSC/bank codes.
- `Charge` (`charges/models.py`) — reusable fee/tax template: `amount`, `tax_code` (%), auto-computed `vat_amount = amount × tax_code / 100`, `total_amount = amount + vat`. Scoped to a `Country`.
- `LeaseTransaction` (`lease/models.py`) — the payment/cheque ledger: FK→`Lease`; FK→`Bank` (`origin_bank`, `settlement_bank`); FK→`Charge` (for non-rent "other charge" lines); fields `amount`, `cheque_date`/`cheque_number`, `cheque_type` (RENT/ADDITIONAL/OTHER_CHARGE), `payment_type` (CHEQUE/CASH/BANK_TRANSFER/PDC), `status` (BALANCE/CREDITED/REALIZED/BOUNCED), `vat`, `total`; also extends `Documents` (cheque images/files).
- `Lease` (`lease/models.py`) — commercial terms: `annual_amount`, `booking_amount`, `security_deposit`, `commission`, `maintenance_charges`, `rent`, `discount`, `contract_amount`. FK→`Unit`, FK→`Tenant`.
- `Unit` (`property/models.py`) — baseline per-unit defaults: `rent`, `security_deposit`, `booking_amount`, `maintenance_charges`, `commission_percent`.
- Relations: `Unit` ← `Lease` (baseline seeds lease terms) · `Lease` → `Tenant` · `Lease` ←(cascade)— `LeaseTransaction` · `LeaseTransaction` → `Bank` (origin), `Bank` (settlement) · `LeaseTransaction` → `Charge` (optional) · `Charge` → `Country` · `LeaseTransaction` → `Documents`/`DocumentType`.
- No standalone `Payment`, `Invoice`, `Deposit`, `Commission`, or `Tax` tables exist today — these are columns on `Lease`/`Unit`/`Charge`/`LeaseTransaction`. Cheque/payment enums live in `utilities/constants.py` (~lines 400–470).

**Design implications:**

- **Lease terms already exist — don't duplicate.** Finance's AR and security deposit tracking pull `rent`, `security_deposit`, `commission`, `maintenance_charges` etc. directly from `Lease`/`Unit` rows, not a re-entered copy.
- **Cheque/PDC state — resolved.** `LeaseTransaction.status` (BALANCE/CREDITED/REALIZED/BOUNCED) is the single source of truth for cheque state. Finance reads and writes this field directly (confirmed: Finance reads AND writes existing tables — Section 3). No separate PDC table, no source-of-truth ambiguity.
- **Multi-owner units require multi-way revenue split.** `Unit` can have multiple `UnitOwner`s — Finance's revenue-split logic (Section 4) must divide one unit's rent across multiple owners plus PMC commission (from `Lease.commission` / `Unit.commission_percent`), not only a two-party split.
- **VAT handling reuses the existing `Charge` model.** `Charge.tax_code`/`vat_amount`/`total_amount` already compute VAT — Finance's tax logic (Section 4) should call/extend this model rather than reimplement VAT calculation.
- **Existing reporting overlaps Finance's Phase 1 scope — migration decision needed.** The existing system currently computes rent analytics, cheque aging, revenue dashboards, and property comparison client-side/in the property app, presumably by querying `LeaseTransaction`/`Lease` directly. Introducing Finance's Ageing, Trial Balance, P&L, and Balance Sheet means deciding whether these existing reports get replaced by Finance's reporting API or continue running in parallel (risk of divergent numbers) — still open, see Section 11.
- **Per-actor cheque visibility must be preserved.** Visibility differs by role today — Owner sees `payment/rental_payments`, PMC sees `lease/all-cheques`. Finance's reporting API needs to preserve or improve this per-actor visibility scoping, not flatten it to one shared view.
- **EJARI and e-signature stay out of scope.** EJARI (Dubai legal lease registration) and lease e-signature flows remain fully owned by the existing system — explicitly out of scope for the Finance module.

## 11. Open Questions / Needs Further Definition

- Exact cheque-bounce operational workflow: bank charge amounts, grace period length, who owns the collections task.
- FX rate source for consolidated reporting: daily central bank rate vs. fixed monthly rate.
- Later-phase scope: everything beyond the Phase 1 feature list (e.g. tax returns, fixed assets/depreciation, multi-ledger consolidation reports, budgeting).
- Reporting migration: do existing client-side/property-app reports (rent analytics, cheque aging, revenue dashboards, property comparison) get replaced by Finance's reporting API, or continue running in parallel? (See Section 10.)
- Concurrency/ownership: since Finance now writes to shared tables (`LeaseTransaction.status`, etc.) that the existing app also writes to, what's the conflict-resolution rule if both try to update the same row (e.g. existing app marks a cheque bounced via its own UI at the same time Finance processes a bank reconciliation import)?
- Migration/backfill: how does Finance bootstrap its own new Ledger/Journal tables from the *existing* historical `LeaseTransaction`/`Lease` data already in the database, so Trial Balance/P&L/Balance Sheet aren't starting from zero?
