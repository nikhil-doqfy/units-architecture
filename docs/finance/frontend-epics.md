---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7]
inputDocuments: [docs/finance/frontend-prd.md, _bmad-output/planning-artifacts/architecture/architecture-units-finance-frontend-2026-09-03/ARCHITECTURE-SPINE.md]
---

# Units Finance Module — Frontend (Phase 2) - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for the Units Finance Module — Frontend (Phase 2), decomposing the requirements from `docs/finance/frontend-prd.md` and `_bmad-output/planning-artifacts/architecture/architecture-units-finance-frontend-2026-09-03/ARCHITECTURE-SPINE.md` into implementable stories. There is no UX design contract for this scope.

## Requirements Inventory

### Functional Requirements

FR1: A new "Finance" nav item appears in the existing dashboard navigation shell, visible to Owner and PMC (`COMPANY_USER`) roles, hidden for Tenant — reusing the existing `permission.service.ts` role-check pattern. (PRD FFR-1)

FR2: A Finance Overview landing page summarizes, per reachable PMC, current-month net P&L, total outstanding AR (from Ageing), and a Trial Balance balanced/unbalanced indicator, each linking through to its full report page. (PRD FFR-2)

FR3: For a user reaching more than one Finance-scoped PMC, every Finance page exposes a PMC selector; a single-PMC user never sees one. The selector and Overview must distinguish a PMC with no `FinancePMCProfile` yet ("Finance not yet set up") from an activated PMC with zero activity ("no activity yet") — two distinct empty states, never a bare table or generic error. (PRD FFR-3)

FR4: Trial Balance page renders `GET reports/trial-balance` for the selected PMC and a date-range picker; shows every Account's debit/credit totals and a footer row; visually flags if debits ≠ credits. (PRD FFR-4)

FR5: Profit & Loss page renders `GET reports/profit-loss` for the selected PMC and a date-range picker; shows Income accounts, Expense accounts, and the resulting net P&L exactly as returned by the API. (PRD FFR-5)

FR6: Balance Sheet page renders `GET reports/balance-sheet` for the selected PMC and an as-of-date picker; shows Asset, Liability, and Equity sections. (PRD FFR-6)

FR7: Ageing page renders `GET reports/ageing` for the selected PMC; shows outstanding AR bucketed by days overdue, paginated via the existing shared `table-pagination` component, with buckets in the API's own left-to-right order. (PRD FFR-7)

FR8: A PMC admin uploads a bank statement CSV via a new upload control; on success shows a count of statement lines created; a malformed CSV surfaces the backend's error via the standard envelope. (PRD FFR-8)

FR9: Suggested matches queue renders `GET reconciliation/suggested-matches` — statement lines paired with a suggested unreconciled Bank Journal entry (or none), each with Confirm and Reject actions; confirming removes both sides from the queue, rejecting leaves both available for manual pairing. (PRD FFR-9)

FR10: For a statement line with no suggestion (or a rejected one), a PMC admin can manually pair it with any other unreconciled Bank Journal entry from the same PMC; the picker is PMC-scoped and an already-matched-entry rejection from the backend is surfaced, never optimistically marked matched. (PRD FFR-10)

FR11: A PMC admin can un-reconcile a previously confirmed pair, returning both sides to the open queue for re-pairing. (PRD FFR-11)

FR12: The reconciliation workspace shows a running count of unmatched statement lines and unmatched Bank Journal entries for the selected PMC, updating immediately after any confirm/reject/manual-match/un-reconcile action, and visibly indicates when a period is fully reconciled (both counts zero). (PRD FFR-12)

FR13: A standalone Chart of Accounts view lists the selected PMC's full seeded Account set (name, type, current balance), read-only, reusing FFR-3's PMC selector and empty-state rules. (PRD FFR-13)

FR14: From the Chart of Accounts view or a Trial Balance line, a user can open an Account's Ledger drill-down: individual `JournalEntry`/`LedgerLine` rows for a date range, each showing date, source reference, debit/credit amount, and running balance; the sum must exactly equal the Account's Trial Balance figure for the same range. (PRD FFR-14)

FR15: Where a `JournalEntry` carries a `reversed_journal_entry_id`, the Ledger drill-down visibly links the reversing entry to the original in both directions; the original is always shown as-is alongside its reversal, never hidden, merged, or netted. (PRD FFR-15)

### NonFunctional Requirements

NFR1: The frontend never recomputes, overrides, or re-derives a reported figure — every rendered number is exactly what its endpoint returned ("render, don't calculate"). (PRD §4.2/§6.1, SM-1, SM-C1)

NFR2: PMC-scoping is absolute — a user must never see another PMC's data anywhere in the Finance section, including in the PMC selector's own options list. (PRD SM-3, FFR-3; Spine AD-2, inherited AD-18/AD-19)

NFR3: 100% of existing non-Finance pages must remain unchanged in behavior and layout after this build. (PRD SM-4; Spine AD-12 Blast-radius convention)

NFR4: Every displayed empty state (not-yet-activated vs. activated-zero-activity) must be correct on first visit to every Finance page — no bare error or blank table observed. (PRD SM-6, FFR-3; Spine AD-3)

NFR5: Switching the PMC selector re-fetches and re-renders the current page's data for the newly selected PMC only — no stale data from the previous selection remains visible. (PRD FFR-3 Consequences; Spine AD-2)

### Additional Requirements

- Finance gets a dedicated `finance-routing.module.ts`, a deliberate exception to the app's existing convention of declaring every dashboard section's routes inline in `dashboard.module.ts`. (Spine AD-1)
- Every Finance route carries a `:pmcId` path segment; PMC selection lives in the URL, never in a shared singleton service — no prior "current PMC" service exists anywhere in the app to build on. (Spine AD-2)
- PMC-Finance-activation status resolves once, centrally, via a route resolver (`ResolveFn` — this codebase's first use of the pattern) exposed under the fixed route-data key `financeActivation` as a 3-state union (`not_activated` | `activated_empty` | `activated`). (Spine AD-3)
- Last-selected PMC persists via `localStorage`, used only to compute the default redirect when landing on Finance with no `:pmcId` or when the stored PMC is no longer reachable; a valid `:pmcId` already in the URL is always authoritative. The active `:pmcId` is also re-validated by the resolver on every navigation (not just landing) to catch a PMC becoming unreachable mid-session. (Spine AD-4)
- Finance Overview lazy-loads: it fetches only what's needed to resolve/render the PMC selector on mount, then only the resolved PMC's summary cards — never fans out to every reachable PMC concurrently. (Spine AD-5)
- **Backend dependency (cross-team, not purely frontend):** `units-finance` needs a small additive companion change — `GET accounts/` and `GET accounts/<pk>/ledger-lines` (backing FR13/FR14/FR15), plus an explicit field in existing reporting endpoints' 200 responses (never an error code) carrying the 3-state activation signal (backing FR3/NFR4). FR13, FR14, FR15, and FR3's empty-state distinction are blocked until this backend work lands — a build-sequencing dependency, not a frontend design problem. (Spine AD-6)
- HTTP error handling is pure reuse of the existing interceptor + `AlertService` (no new error path); Finance adds one Finance-local envelope-unwrap helper for `{content, message, pagination}}`, since no project-wide envelope parser exists today. (Spine AD-7)
- Finance's API calls split into three services by concern, mirroring the backend's own module split: `FinanceReportsService` (Overview + 4 reports), `ReconciliationService` (upload, matches, un-reconcile, counts), `FinanceLedgerService` (Chart of Accounts, Ledger drill-down). Cross-service navigation (e.g. Trial Balance line → Ledger detail) happens by route navigation carrying `account_id`, never by one page injecting another concern's service. (Spine AD-8)
- FR8's CSV upload does not reuse the existing `dashboard/component/upload-document` (built for base64/image flows); a new Finance-owned upload control performs a real multipart POST directly to `reconciliation/bank-statement-import`, with the file under a `file` field and PMC scoping via `:pmcId`, never duplicated into the body. (Spine AD-9)
- The reconciliation workspace re-fetches its queue and derived counts (`ReconciliationService.refreshQueue()`) after every mutating action — never an optimistic local splice — so FR9/FR11/FR12's consistency guarantees hold structurally. (Spine AD-10)
- **Infra/deployment dependency:** the frontend has no existing route to `units-finance` today — `docker_config/nginx.conf` only proxies `/api/` to `units_backend`. A new `/api/finance/` nginx location + `units_finance` upstream block, and a new `FINANCE_SERVER_ADDRESS` constant in `environment.ts`/`environment.development.ts`, must be added before any Finance page can reach its API. (Spine AD-11)
- All Finance UI reuses existing components/theme — `dashboard/component/custom-select` for the PMC picker (not the app's other four specialized dropdowns), the global `.btnCommon` class for buttons (no shared button component exists), `shared/component/white-card` as the default content wrapper, the existing shell (`header`/`sidebar`/`footer`) inherited automatically via `router-outlet` and never re-imported, and the existing Bootstrap 5 + global `src/styles.css` CSS custom properties for all theming — no new design-system dependency. The one named exception: no date-picker/date-range component exists anywhere in the app today, so FR4/FR5/FR6's pickers are new, built as `ControlValueAccessor`s matching `custom-select`'s own pattern. (Spine AD-12)
- Exactly five existing files may be touched outside Finance's own tree, and no others: `dashboard.module.ts` (register the routing module + one new route), `shared/sidebar/sidebar.component.html` (one new nav item), `permission.service.ts` (add `'Finance'` as a module key), `environment.ts`/`environment.development.ts` (AD-11), `docker_config/nginx.conf` (AD-11). (Spine AD-12 Blast-radius convention)
- Route access: `canActivate: [permissionGuard]` + `data: { module: 'Finance' }` on every Finance route, gating both direct navigation and (via `sidebar.component.html`'s `canAccess('Finance')`) nav-item visibility. (Spine, Consistency Conventions)

### UX Design Requirements

No UX design contract exists for this scope (`{planning_artifacts}/ux-designs/`, `*ux*.md` search: no matches). Visual/interaction conventions are instead fixed by the Architecture Spine's AD-12 (UI reuse) — see Additional Requirements above.

### FR Coverage Map

FR1: Epic 1 - Finance nav entry point, gated by role
FR2: Epic 1 - Finance Overview landing page (per-PMC summary cards)
FR3: Epic 1 - PMC selector + activation-vs-empty empty-state distinction
FR4: Epic 2 - Trial Balance page
FR5: Epic 2 - Profit & Loss page
FR6: Epic 2 - Balance Sheet page
FR7: Epic 2 - Ageing page (paginated)
FR8: Epic 3 - Bank statement CSV upload
FR9: Epic 3 - Suggested matches queue (confirm/reject)
FR10: Epic 3 - Manual match pairing
FR11: Epic 3 - Un-reconcile / correct a wrong match
FR12: Epic 3 - Reconciliation outstanding-items summary
FR13: Epic 4 - Chart of Accounts view
FR14: Epic 4 - Ledger drill-down per Account
FR15: Epic 4 - Reversed/bounced entry linkage

NFR1 (render, don't calculate): Epic 2, Epic 4 - every report/ledger figure rendered as returned, never recomputed
NFR2 (PMC-scoping): Epic 1 - enforced structurally by the route/resolver shell every later epic depends on
NFR3 (no regression on existing pages): Epic 1 - bounded by the AD-12 blast-radius convention (exactly 5 existing files touched, all in Epic 1's foundational stories)
NFR4 (correct empty states): Epic 1 - the activation resolver and its two empty states
NFR5 (no stale data on PMC switch): Epic 1 - the `:pmcId`-driven re-fetch mechanism

## Epic List

### Epic 1: Finance Section Access & PMC Context
A PMC admin or Owner can open a new "Finance" section from the existing dashboard nav, land on an Overview summarizing key figures for each PMC they manage, and — for multi-PMC users — switch between PMCs via a selector that correctly distinguishes a PMC whose books aren't set up yet from one that's active but has no activity. This epic stands up the foundation every later epic depends on: the routing shell, `:pmcId` scoping, the activation resolver, and the one-time infra/deployment change (new nginx route + environment constant) that lets the frontend reach `units-finance` at all.

**Dependency note:** Stories 1.3–1.5 rely on the same backend companion signal named in Spine AD-6 (the activation-status field distinguishing "not activated" from "activated, zero activity"), which does not exist on `units-finance` yet — the same dependency Epic 4 carries for its own endpoints. Story 1.3's resolver is built against the agreed 3-state contract with a mocked data source so Epic 1 is not blocked from starting, but Stories 1.4/1.5 cannot be verified end-to-end against real data until that backend work lands.

**FRs covered:** FR1, FR2, FR3
**NFRs covered:** NFR2, NFR3, NFR4, NFR5
**Architecture:** AD-1, AD-2, AD-3, AD-4, AD-5, AD-11, AD-12 (blast-radius convention)

### Epic 2: Financial Reports
A PMC admin or Owner can view Trial Balance, Profit & Loss, Balance Sheet, and Ageing for any PMC they can reach — the core "see my books without Swagger/Postman" value the PRD exists to deliver. Each report is a thin, faithful rendering of its existing backend endpoint.
**FRs covered:** FR4, FR5, FR6, FR7
**NFRs covered:** NFR1
**Architecture:** AD-7 (envelope helper), AD-8 (`FinanceReportsService`), AD-12 (shared `report-table`, `table-pagination`, new date/date-range pickers)

## Epic 2: Financial Reports

A PMC admin or Owner can view Trial Balance, Profit & Loss, Balance Sheet, and Ageing for any PMC they can reach — the core "see my books without Swagger/Postman" value the PRD exists to deliver.

### Story 2.1: Shared report-table component and date pickers

As a developer building any of the four report pages,
I want one shared table-rendering component and one shared pair of date-picker controls,
So that Trial Balance, P&L, Balance Sheet, and Ageing render consistently and don't each hand-roll their own table markup or date input.

**Acceptance Criteria:**

**Given** the four report pages share a common rows/totals shape (TB/P&L/BS) or a paginated bucketed shape (Ageing)
**When** the shared `report-table` sub-component (`pages/finance/component/report-table`) is built
**Then** it accepts `@Input() columns: ReportColumn[]` (`{ key, label, align? }`), `@Input() rows: Record<string, string | number>[]`, `@Input() totals?: Record<string, string | number>` (rendering a totals row only when provided), and `@Input() paginated = false` (rendering the shared `table-pagination` component beneath the rows when true), per Spine AD-12's pinned contract
**And** the component performs no formatting or computation on the values it's given — it renders exactly the pre-formatted strings/numbers each page passes in (NFR1, "render, don't calculate")

**Given** no shared date-picker or date-range component exists anywhere in the app (verified: zero prior usage)
**When** the new date-range and as-of-date picker controls are built
**Then** each is implemented as a `ControlValueAccessor`, matching `dashboard/component/custom-select`'s own implementation pattern (Spine AD-12), so they integrate with reactive forms the same way every other form control in the app does
**And** the date-range picker emits a `{from: Date, to: Date}` value; the as-of-date picker emits a single `Date` value

### Story 2.2: Trial Balance page

As a PMC admin or Owner,
I want to see the Trial Balance for a PMC and date range I choose,
So that I can verify the PMC's books are balanced without exporting data or using Swagger/Postman.

**Acceptance Criteria:**

**Given** I am viewing Finance for a PMC with `financeActivation === 'activated'`
**When** I open the Trial Balance page
**Then** it renders `GET reports/trial-balance` for the active `:pmcId` and a default date range, via `FinanceReportsService` (Spine AD-8), showing every Account's debit/credit totals and a footer totals row through the shared `report-table` component (Story 2.1)

**Given** the Trial Balance page is open
**When** I change the date range via the shared date-range picker (Story 2.1)
**Then** the page re-fetches and re-renders without a full page reload (FR4 Consequences)

**Given** the API response shows debits ≠ credits (should never happen per the backend's own invariant, but the UI must not hide a violation if it occurs)
**When** the page renders that response
**Then** a visible warning is displayed rather than silently showing mismatched totals (FR4)

**Given** the PMC's `financeActivation` is `'not_activated'` or `'activated_empty'`
**When** I open the Trial Balance page for that PMC
**Then** the corresponding empty state from Story 1.5 is shown instead of an empty/erroring report table

### Story 2.3: Profit & Loss page

As a PMC admin or Owner,
I want to see the Profit & Loss statement for a PMC and date range I choose,
So that I know the PMC's net income or loss for that period.

**Acceptance Criteria:**

**Given** I am viewing Finance for a PMC with `financeActivation === 'activated'`
**When** I open the Profit & Loss page
**Then** it renders `GET reports/profit-loss` for the active `:pmcId` and a default date range, showing Income accounts, Expense accounts, and the resulting net P&L figure, with income and expense visually distinguished (e.g. section headers or color) (FR5)

**Given** the P&L page is open
**When** I change the date range via the shared date-range picker
**Then** the page re-fetches and re-renders without a full page reload

**Given** the API returns a net P&L figure
**When** the page renders it
**Then** the displayed net figure is exactly the value the API returned — the UI never recomputes sum(Income) − sum(Expense) itself (FR5 Consequences, NFR1)

**Given** the PMC's `financeActivation` is `'not_activated'` or `'activated_empty'`
**When** I open the P&L page for that PMC
**Then** the corresponding empty state from Story 1.5 is shown

### Story 2.4: Balance Sheet page

As a PMC admin or Owner,
I want to see the Balance Sheet for a PMC as of a date I choose,
So that I can confirm the PMC's assets equal its liabilities plus equity at that point in time.

**Acceptance Criteria:**

**Given** I am viewing Finance for a PMC with `financeActivation === 'activated'`
**When** I open the Balance Sheet page
**Then** it renders `GET reports/balance-sheet` for the active `:pmcId` and a default as-of-date, showing Asset, Liability, and Equity sections and confirming Assets = Liabilities + Equity (FR6)

**Given** the Balance Sheet page is open
**When** I change the as-of-date via the shared as-of-date picker (Story 2.1)
**Then** the page re-fetches and re-renders the full breakdown for that date (FR6 Consequences)

**Given** the PMC's `financeActivation` is `'not_activated'` or `'activated_empty'`
**When** I open the Balance Sheet page for that PMC
**Then** the corresponding empty state from Story 1.5 is shown

### Story 2.5: Ageing page

As a PMC admin or Owner,
I want to see outstanding AR bucketed by days overdue for a PMC,
So that I know which tenants are overdue and by how much.

**Acceptance Criteria:**

**Given** I am viewing Finance for a PMC with `financeActivation === 'activated'`
**When** I open the Ageing page
**Then** it renders `GET reports/ageing` for the active `:pmcId`, showing outstanding AR bucketed by days overdue (current, 1-30, 31-60, 61-90, 90+), via the shared `report-table` component with `paginated = true` (Story 2.1)

**Given** the Ageing report is the only one of the four that paginates (per the backend's own Structural Seed)
**When** the page renders
**Then** it uses the existing shared `dashboard/component/table-pagination` component, not a bespoke pagination control (FR7 Consequences)
**And** bucket columns render in the same left-to-right order the API returns them (current → 90+), with no client-side bucket recomputation (FR7 Consequences, NFR1)

**Given** the PMC's `financeActivation` is `'not_activated'` or `'activated_empty'`
**When** I open the Ageing page for that PMC
**Then** the corresponding empty state from Story 1.5 is shown

### Epic 3: Bank Reconciliation Workspace
A PMC admin can complete the full reconciliation loop — upload a bank statement CSV, work through suggested matches, confirm or reject them, manually pair anything left over, correct a wrong match by un-reconciling it, and see at a glance when a period is fully reconciled — without leaving the Finance section or falling back to Swagger/Postman.
**FRs covered:** FR8, FR9, FR10, FR11, FR12
**Architecture:** AD-7, AD-8 (`ReconciliationService`), AD-9 (new CSV upload control), AD-10 (refresh-after-mutation, no optimistic state), AD-13 (PMC-admin-only access)

## Epic 3: Bank Reconciliation Workspace

A PMC admin can complete the full reconciliation loop — upload a bank statement CSV, work through suggested matches, confirm or reject them, manually pair anything left over, correct a wrong match by un-reconciling it, and see at a glance when a period is fully reconciled — without leaving the Finance section.

### Story 3.1: Bank statement CSV upload

As a PMC admin,
I want to upload a bank statement CSV for a PMC I manage,
So that its statement lines exist in the system for reconciliation.

**Acceptance Criteria:**

**Given** I am a PMC admin viewing the Reconciliation Workspace for a PMC with `financeActivation === 'activated'`
**When** I upload a valid CSV with N rows
**Then** a new Finance-owned upload control (not `dashboard/component/upload-document`, per Spine AD-9) performs a real multipart POST directly to `reconciliation/bank-statement-import`, with the file under a `file` field and `pmc_id` (the active `:pmcId`'s value) also included as a multipart field — the real backend view reads `pmc_id` from `request.data`, not a URL/query param, for this one endpoint (Spine AD-9, corrected 2026-09-04)
**And** on success, a confirmation reflects N created statement lines (FR8 Consequences)

**Given** I upload a malformed CSV
**When** the backend rejects it
**Then** the backend's error message is surfaced via the standard response envelope (unwrapped by the Finance-local envelope helper, Spine AD-7) rather than a generic failure message

**Given** I am an Owner, not a PMC admin
**When** I attempt to view the Reconciliation Workspace
**Then** the route guard's second condition — `permissionService.isPropertyManager()` (Spine AD-13, the existing `user_role === 'COMPANY_USER'` check) — evaluates false and I am redirected away, exactly as `permissionGuard` already redirects a disallowed role elsewhere in the app; the Reconciliation nav entry within Finance is also hidden for me by the same check (PRD §4.3, FFR-8 scope)

### Story 3.2: Suggested matches queue with confirm/reject

As a PMC admin,
I want to see statement lines paired with the system's suggested Bank Journal entry and confirm or reject each suggestion,
So that I can work through reconciliation without manually searching for every match myself.

**Acceptance Criteria:**

**Given** statement lines exist for the active PMC (Story 3.1)
**When** I open the Reconciliation Workspace
**Then** it renders `GET reconciliation/suggested-matches` via `ReconciliationService` (Spine AD-8) — a list of statement lines each paired with a suggested unreconciled Bank Journal entry, or none if no suggestion exists — each row showing Confirm and Reject actions (FR9)

**Given** a statement line has a suggested match
**When** I click Confirm
**Then** `POST reconciliation/match` is called, and on success `ReconciliationService.refreshQueue()` re-fetches the full queue (Spine AD-10) — both sides no longer appear in the queue (FR9 Consequences)

**Given** a statement line has a suggested match I don't want to accept
**When** I click Reject
**Then** the rejection is recorded and `refreshQueue()` re-fetches (Spine AD-10) — both the statement line and the suggested entry remain in the queue, available for a different pairing (Story 3.3), and the statement line is never deleted (FR9 Consequences)

### Story 3.3: Manual match pairing

As a PMC admin,
I want to manually pair a statement line that has no suggestion (or whose suggestion I rejected) with another unreconciled Bank Journal entry,
So that reconciliation can proceed even when the system can't suggest a match.

**Dependency note:** no backend endpoint exists to browse a PMC's unreconciled Bank Journal entries (Spine AD-15, discovered during this story's planning) — only the pairing *action* itself (`POST reconciliation/match`) is real and already accepts any PMC-scoped pair, not only heuristic-suggested ones. Until AD-15's browsable-list endpoint exists, the target entry is entered as a manual `journal_entry_id`, not selected from a dropdown/browsable list.

**Acceptance Criteria:**

**Given** a statement line in the queue has no suggestion, or its suggestion was rejected (Story 3.2)
**When** I choose to manually pair it
**Then** I can enter a `journal_entry_id` to pair with (Spine AD-15 — no browsable list exists yet; this becomes a selectable list once that backend endpoint ships)

**Given** I confirm a manual pairing
**When** the pairing is submitted
**Then** `ReconciliationService.refreshQueue()` re-fetches the queue (Spine AD-10) and both sides no longer appear in the open queue

**Given** the backend rejects a manual pairing because the target entry was already matched by someone else in the meantime, or the `journal_entry_id` doesn't exist / isn't scoped to this PMC (backend FR-15 constraint)
**When** that rejection is returned
**Then** the UI surfaces the rejection rather than optimistically marking the pairing as matched (FR10 Consequences)

### Story 3.4: Un-reconcile a confirmed match

As a PMC admin,
I want to undo a previously confirmed match,
So that I can correct a wrong pairing and re-match it correctly.

**Dependency note — RESOLVED (2026-09-04):** this story was fully blocked on backend work (Spine AD-16), but all three named dependencies now exist: `BankStatementMatch` gained a fourth `unreconciled` status, `apply_match_decision` gained a matching `_unreconcile_match` branch (mirroring `_confirm_match` in reverse, 409s if the pair isn't currently confirmed), and the trigger is a new `"unreconcile"` value on the existing `POST reconciliation/match` action field — no new endpoint. Story 3.4 is unblocked; see AD-16's RESOLVED note in the spine for full verification detail.

**Acceptance Criteria:**

**Given** a statement line and Bank Journal entry were previously confirmed as matched (Story 3.2 or 3.3)
**When** I choose to un-reconcile that pair
**Then** the un-reconcile action is submitted via `ReconciliationService`, and on success `refreshQueue()` re-fetches the queue (Spine AD-10) — both the statement line and the Ledger entry reappear in the open queue rather than remaining hidden (FR11 Consequences)

**Given** both sides have reappeared in the open queue
**When** I view the queue afterward
**Then** I can confirm a different pairing for either side, using the same suggested-match (Story 3.2) or manual-pairing (Story 3.3) flows

### Story 3.5: Reconciliation outstanding-items summary

As a PMC admin,
I want to see a running count of unmatched statement lines and unmatched Bank Journal entries,
So that I know when a period's reconciliation is actually complete.

**Dependency note:** Spine AD-6's original claim that unmatched counts can be derived client-side from `suggested-matches` is wrong (corrected as AD-17, discovered during this story's planning) — that endpoint only returns pairs the heuristic actually matched; a line/entry with no candidate never appears in it at all, so its length undercounts real unmatched items. A real "reconciliation complete" signal needs a new backend capability (AD-17) that doesn't exist yet. Until it ships, this story surfaces a "pending suggestions" count (the real `suggested-matches` length, honestly labeled) rather than a number presented as a completeness indicator.

**Acceptance Criteria:**

**Given** I am viewing the Reconciliation Workspace for the active PMC
**When** the workspace renders
**Then** it shows a count of pending suggested matches, derived client-side from the existing `suggested-matches` response — labeled as "pending suggestions," never as "unmatched items" or a completeness signal (Spine AD-17)

**Given** I confirm, reject, manually match, or un-reconcile an item (Stories 3.2–3.4, all now unblocked)
**When** that action completes and `refreshQueue()` re-fetches (Spine AD-10)
**Then** the pending-suggestions count updates immediately, without requiring a manual page refresh (FR12 Consequences, partially — full unmatched-item counts still await AD-17's backend work)

**Given** the pending-suggestions count reaches zero
**When** the workspace renders
**Then** it indicates no pending suggestions remain — explicitly not "fully reconciled," since real unmatched items with no suggestion are invisible to this count until AD-17 ships (Spine AD-17)

### Epic 4: Ledger Traceability (Chart of Accounts & Drill-Down)
A PMC admin or Owner can see a PMC's full Chart of Accounts and drill from any report figure down to the individual postings that produced it, including a visible link between a bounced/reversed entry and the original it reversed — closing the "I can see the total but not why" gap the first three epics leave open. This epic is gated on a small additive backend companion change (new read endpoints + an activation-status field) and is kept separate from Epic 2 for exactly that reason: it carries a real dependency/risk boundary the other epics don't.
**FRs covered:** FR13, FR14, FR15
**NFRs covered:** NFR1
**Architecture:** AD-6 (backend companion dependency), AD-7, AD-8 (`FinanceLedgerService`), AD-12

## Epic 1: Finance Section Access & PMC Context

A PMC admin or Owner can open a new "Finance" section from the existing dashboard nav, land on an Overview summarizing key figures for each PMC they manage, and — for multi-PMC users — switch between PMCs via a selector that correctly distinguishes a PMC whose books aren't set up yet from one that's active but has no activity.

**Dependency note:** Stories 1.3–1.5 rely on the same not-yet-existing backend activation-status signal named in Spine AD-6 that Epic 4 also depends on. Story 1.3's resolver is built against the agreed contract with a mocked data source so this epic is not blocked from starting, but full end-to-end verification of Stories 1.4/1.5 waits on that backend work.

### Story 1.1: Frontend can reach the units-finance API

As a developer building any Finance page,
I want the frontend to have a working network path to the `units-finance` service,
So that every later Finance story has a real API to call instead of one silently returning nothing.

**Acceptance Criteria:**

**Given** the existing `docker_config/nginx.conf` only proxies `/api/` to `units_backend`
**When** this story is complete
**Then** `docker_config/nginx.conf` has a new `location /api/finance/` block that rewrites and proxies to a new `units_finance` upstream (`units_finance:8001`), mirroring the existing `/api/` → `units_backend` block's rewrite/proxy_pass/header shape exactly (Spine AD-11)
**And** `environment.ts` and `environment.development.ts` each carry a new `FINANCE_SERVER_ADDRESS` constant, independent of the existing `SERVER_ADDRESS`
**And** `units_backend`'s existing `/api/` location and `SERVER_ADDRESS`'s existing value are unmodified
**And** a manual request to `FINANCE_SERVER_ADDRESS` (e.g. hitting an existing `units-finance` reporting endpoint with a valid JWT) returns the endpoint's real response through the new proxy path, confirming the route works end-to-end

### Story 1.2: Finance nav entry point

As an Owner or PMC admin,
I want a "Finance" item in the dashboard's existing navigation,
So that I can discover the Finance section without being told a URL.

**Acceptance Criteria:**

**Given** I am logged in as an Owner or PMC (`COMPANY_USER`) role
**When** I view the dashboard sidebar
**Then** a new "Finance" nav item appears, added to `shared/sidebar/sidebar.component.html` and gated by `canAccess('Finance')` (Spine AD-12 Blast-radius convention), alongside the existing `owners`/`properties` items, with no restructuring of surrounding markup
**And** `permission.service.ts`'s permissions map gains `'Finance'` as a new module key, with no change to `hasPermission`/`canAccessModule`'s existing logic

**Given** I am logged in as a Tenant
**When** I view the dashboard sidebar
**Then** no "Finance" nav item appears anywhere

**Given** a `finance-routing.module.ts` exists (registered as a lazy child from `dashboard.module.ts`, per Spine AD-1) with a stub landing route
**When** I click the "Finance" nav item
**Then** I am routed into the Finance section, gated by `canActivate: [authGuard, permissionGuard]` with `data: { module: 'Finance' }` — `authGuard` added because `permissionGuard`/`canAccessModule` alone does not exclude Tenant (it returns unrestricted `true` for any non-`COMPANY_USER` role, a pre-existing property shared with `owners`/`properties`, not new to Finance)
**And** a Tenant attempting to navigate directly to a Finance URL is redirected away by `authGuard` (to `/dashboard/properties`, its existing redirect target), even without a visible nav item

### Story 1.3: PMC-scoped Finance routing with activation resolution

As a PMC admin or Owner reachable to one or more PMCs,
I want every Finance page to be scoped to a specific PMC and to know that PMC's Finance-activation status before rendering,
So that I never see a mix of PMCs' data or a confusing blank page for a PMC that isn't set up yet.

**Acceptance Criteria:**

**Given** the Finance routing module from Story 1.2
**When** any Finance route is defined
**Then** it is shaped `dashboard/finance/:pmcId/<page>` (Spine AD-2) — no Finance route omits the `:pmcId` segment

**Given** I land on `dashboard/finance` with no `:pmcId` in the URL
**When** the app resolves where to send me
**Then** it reads a `localStorage` key holding my last-selected PMC id and redirects to that PMC's Overview if it is still in my reachable-PMC list; otherwise it redirects to my first reachable PMC (Spine AD-4)

**Given** a valid `:pmcId` is present in the URL
**When** any Finance route is entered
**Then** a route resolver (`ResolveFn`) fetches that PMC's Finance-activation status and exposes it under the fixed route-data key `financeActivation` as one of exactly three values — `'not_activated'`, `'activated_empty'`, `'activated'` (Spine AD-3) — before the page component renders

**Given** the active `:pmcId` was valid when I landed on it
**When** I navigate to another Finance page within the same session and that PMC has since become unreachable to me (e.g. access revoked in another tab)
**Then** the resolver re-validates my reachable-PMC list on that navigation (not only on the original no-`:pmcId` landing) and redirects me back to `dashboard/finance` (re-entering the Story's landing-redirect logic) rather than letting the page mount and fail on its first data call (Spine AD-4)

**Given** the backend companion signal for activation status does not yet exist (blocked on Epic 4's backend dependency, Spine AD-6)
**When** this story is implemented
**Then** the resolver is built against the agreed 3-state contract (`not_activated` | `activated_empty` | `activated`) with a placeholder/mocked data source, so pages built in later stories integrate against a stable interface regardless of backend timing — this is a noted integration point to revisit, not a blocker for this story

### Story 1.4: Finance Overview summary cards

As a PMC admin or Owner,
I want to see current-month net P&L, total outstanding AR, and a Trial Balance balanced/unbalanced indicator for each PMC I manage as soon as I open Finance,
So that I can answer basic financial questions without opening a full report.

**Dependency note:** the total-outstanding-AR card depends on a not-yet-existing backend aggregate (Spine AD-14, discovered during this story's planning) — `reports/ageing/` returns only paginated individual rows with no sum. Until that backend work lands, this story ships the P&L and Trial Balance cards against the real endpoints and renders the AR card as an explicit "not yet available" placeholder, never a client-side-summed number.

**Acceptance Criteria:**

**Given** I have exactly one reachable PMC
**When** I open the Finance section
**Then** I land directly on that PMC's Overview, with no PMC selector shown, and cards for current-month net P&L and a Trial Balance balanced/unbalanced indicator render with real data, each linking through to its full report page (FR2); the total outstanding AR card renders an explicit "not yet available" placeholder (Spine AD-14)

**Given** I am reachable to more than one PMC
**When** I open Finance with no `:pmcId` resolved yet
**Then** the Overview fetches only what's needed to resolve/render the PMC selector — it does not fetch summary-card data for every reachable PMC concurrently (Spine AD-5)

**Given** a `:pmcId` is active (implicit or selected)
**When** the Overview renders
**Then** it fetches only that PMC's P&L and Trial Balance summary data via `FinanceReportsService` (Spine AD-8), reading the response envelope through the Finance-local unwrap helper (Spine AD-7)

**Given** a PMC's `financeActivation` resolves to `'not_activated'` or `'activated_empty'`
**When** its Overview page renders
**Then** the summary cards are replaced by the corresponding empty state (Story 1.5) rather than attempting to render zero/undefined figures as if they were real data

### Story 1.5: PMC selector with distinct empty states

As a PMC admin or Owner reachable to more than one Finance-scoped PMC,
I want to switch which PMC's books I'm viewing, and see a clear message when a PMC isn't set up for Finance yet versus one that's active but has no activity,
So that I never mistake "not set up" for "no data" or accidentally view the wrong PMC's figures.

**Acceptance Criteria:**

**Given** I am reachable to more than one Finance-scoped PMC
**When** I view any Finance page
**Then** a PMC selector is shown, built on the existing `dashboard/component/custom-select` component (Spine AD-12) — not one of the app's other specialized dropdowns

**Given** I am reachable to exactly one PMC
**When** I view any Finance page
**Then** no PMC selector control appears at all

**Given** I select a different PMC from the selector
**When** the selection changes
**Then** the app navigates to the same page with the new PMC's `:pmcId` (Spine AD-2), re-triggering the activation resolver and the page's own data fetch, and no data from the previously selected PMC remains visible at any point during the transition (FR3 Consequences, NFR5)

**Given** a PMC reachable to me has no `FinancePMCProfile` yet (`financeActivation === 'not_activated'`)
**When** I view any Finance page scoped to that PMC
**Then** I see an explicit "Finance not yet set up for this PMC" empty state — never a bare empty table or a generic error

**Given** a PMC reachable to me has an activated `FinancePMCProfile` but zero posted activity (`financeActivation === 'activated_empty'`)
**When** I view any Finance page scoped to that PMC
**Then** I see a distinct "no activity yet" empty state, visibly different from the "not yet set up" message

**Given** I select a PMC
**When** the selection is made
**Then** it is written to the `localStorage` key Story 1.3's landing-redirect reads (Spine AD-4), so returning to Finance later without a `:pmcId` in the URL defaults back to this PMC if still reachable

## Epic 4: Ledger Traceability (Chart of Accounts & Drill-Down)

A PMC admin or Owner can see a PMC's full Chart of Accounts and drill from any report figure down to the individual postings that produced it, including a visible link between a bounced/reversed entry and the original it reversed.

**Dependency note:** every story in this epic is blocked on the small additive backend companion change named in Spine AD-6 — `GET accounts/` and `GET accounts/<pk>/ledger-lines` do not exist yet on `units-finance`. This is a build-sequencing dependency, not a frontend design gap; these stories can be built against the agreed contract shape in parallel with that backend work, but cannot be verified end-to-end until it ships.

### Story 4.1: Chart of Accounts view

As a PMC admin or Owner,
I want to see a PMC's full set of seeded Accounts with their type and current balance,
So that I have a reference of the PMC's accounting structure independent of any single report.

**Acceptance Criteria:**

**Given** I am viewing Finance for a PMC with `financeActivation === 'activated'`
**When** I open the Chart of Accounts view
**Then** it renders `GET accounts/` (the new backend companion endpoint, Spine AD-6) via `FinanceLedgerService` (Spine AD-8), listing every Account seeded for that PMC's `FinancePMCProfile` exactly once, correctly labeled by type (Asset/Liability/Income/Expense) (FR13 Consequences)
**And** the view reuses the same PMC selector and empty-state rules as Story 1.5 (FR13)

**Given** the Chart of Accounts is fixed and non-configurable in Phase 1 (backend FR-3 Notes)
**When** I view this page
**Then** it is read-only — no edit or add-Account action is exposed anywhere on the page (FR13 Consequences)

**Given** the PMC's `financeActivation` is `'not_activated'` or `'activated_empty'`
**When** I open the Chart of Accounts view for that PMC
**Then** the corresponding empty state from Story 1.5 is shown

### Story 4.2: Ledger drill-down per Account

As a PMC admin or Owner,
I want to open an Account's Ledger detail from the Chart of Accounts or a Trial Balance line,
So that I can see exactly which postings sum to a reported figure.

**Acceptance Criteria:**

**Given** I am viewing the Chart of Accounts (Story 4.1) or the Trial Balance page (Story 2.2)
**When** I select an Account (from the CoA list) or click a Trial Balance line
**Then** I navigate to that Account's Ledger detail using its numeric `account_id` — never its display name — via route navigation, not by `TrialBalancePage` injecting `FinanceLedgerService` directly (Spine AD-8 cross-service drill-through rule)

**Given** I am viewing an Account's Ledger detail for a selected date range
**When** the page renders
**Then** it shows the individual `JournalEntry`/`LedgerLine` rows posted to that Account via `GET accounts/<pk>/ledger-lines` (the new backend companion endpoint, Spine AD-6), each row showing date, source reference (e.g. `source_lease_transaction_id`), debit/credit amount, and running balance (FR14)

**Given** a Ledger drill-down is displayed for an Account and date range
**When** I sum all displayed `LedgerLine` amounts
**Then** that sum exactly equals the Account's total shown on the Trial Balance for the same range — the drill-down is provably a decomposition of the summary figure, never a separately computed number (FR14 Consequences, NFR1)

### Story 4.3: Reversed/bounced entry linkage

As a PMC admin or Owner,
I want to see a visible link between a reversed entry and the reversal that followed it, in both directions,
So that I can trace a bounce/reversal without hunting for the matching entry myself.

**Acceptance Criteria:**

**Given** a `JournalEntry` shown in the Ledger drill-down (Story 4.2) carries a `reversed_journal_entry_id` (a bounce reversal, per backend FR-6/AD-16)
**When** I view that reversing entry
**Then** I see a visible link/reference to the original entry it reversed (FR15 Consequences)

**Given** I view the original entry that was later reversed
**When** the Ledger drill-down renders it
**Then** I see a visible link back to the reversal that followed it (FR15 Consequences)

**Given** an entry has been reversed
**When** both the original and its reversal are displayed
**Then** the original entry is always shown as-is alongside its reversal — the UI never hides, merges, or nets the two rows into one, since the original is never edited or deleted server-side (FR15 Consequences)
