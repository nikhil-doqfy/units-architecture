---
title: Units Finance Module — Frontend (Phase 2)
created: 2026-09-03
updated: 2026-09-03
status: final
---

**Revision (2026-09-03):** added §4.4 (Ledger Drill-Down & Chart of Accounts, closing three concept gaps: journal/ledger-line drill-down, a standalone CoA view, and reversed/bounced-entry traceability), extended FFR-3 with an explicit "PMC not yet Finance-activated" empty state, and added FFR-12 (reconciliation outstanding-items summary) to §4.3 — see FR Coverage Map for the full gap list this closes.

# PRD: Units Finance Module — Frontend (Phase 2)

**Relationship to the backend PRD:** This document is the direct continuation of `docs/finance/prd.md` §6.2's deferred item: *"units-frontend UI — deferred to Phase 2... The 'Finance' section in the existing Units Angular frontend... is real, intended, and not removed from this document, but is explicitly out of scope for this Phase 1 build."* Phase 1 (backend) is `status: final` and integration-tested against `units-backend`; all Ledger posting (Epic 2) and reporting/reconciliation APIs (Epics 3–4) already exist and are live. This PRD scopes Phase 2: the Angular UI in `microservices/units-frontend` that surfaces those existing APIs to logged-in Owners and PMC admins. It does not re-derive or restate the backend's functional requirements (FR-1 through FR-15 in `docs/finance/prd.md`) — it consumes them as a fixed, already-built contract and defines only the presentation layer on top.

## 0. Document Purpose

This PRD scopes Phase 2 of the Finance module: the frontend UI, for a solo developer continuing the same 5-day-style build discipline used for Phase 1. It builds on `docs/finance/prd.md` (functional requirements, glossary, roles) and `docs/finance/epics.md` (the exact API contracts: endpoints, request/response shape, auth, PMC-scoping) — both finalized and already implemented in `microservices/units-finance`. This document does not duplicate those contracts; it references them and turns "what data exists" into "what screens show it."

## 1. Vision

`units-backend` already manages properties, leases, tenants, and cheque-based rent collection with a full Angular frontend (`microservices/units-frontend`) serving three roles: Owner, Tenant, and PMC (`COMPANY_USER`). `units-finance` now sits alongside it as a fully working accounting microservice — Ledger, Trial Balance, P&L, Balance Sheet, Ageing, and Bank Reconciliation are all real, tested, and reachable via JWT-authenticated, PMC-scoped API endpoints. None of this is visible anywhere in the UI today: an Owner or PMC admin has no way to see their own books without going around the product (Swagger/Postman), which defeats the point of having built the module.

This PRD closes that gap. "Done" for Phase 2 means: a new **Finance** section exists in the existing Angular app, reachable from the same navigation shell Owners and PMC admins already use, rendering live data from the existing report/reconciliation endpoints, plus a Chart of Accounts view and Ledger drill-down that make each report's figures traceable back to their source postings (§4.4) — the latter is the one area of this PRD where a small, additive backend read endpoint may be needed (see Open Questions 6–8), everywhere else the backend contract is treated as frozen.

## 2. Target User

### 2.1 Jobs To Be Done

- As a PMC admin, logged into the existing Units app, I need to see Trial Balance, P&L, Balance Sheet, and Ageing for each PMC I manage, so I can answer basic financial questions without exporting data or opening a separate tool.
- As a PMC admin, I need to upload a bank statement CSV and reconcile it against the Ledger, so bank reconciliation is a normal part of my existing workflow instead of a manual offline process.
- As a property Owner, logged into the existing Units app, I need to see the same four reports for PMCs I have visibility into, so I can independently verify my books without relying on the PMC's word.
- As Rahul (the builder), I need this UI to slot into the existing `units-frontend` app — reusing its shell, auth, routing, and role-guard patterns — so Phase 2 doesn't reopen architectural questions Phase 1 already settled.

### 2.2 Non-Users (v2)

- End tenants (renters) — unchanged from Phase 1; Finance remains backend/reporting, not tenant-facing.
- Accountants/bookkeepers doing manual journal entry — unchanged from Phase 1; there is still no manual journal-entry UI, because there is no manual journal-entry API to call.

### 2.3 Key User Journeys

- **UJ-1. PMC admin checks Trial Balance for a PMC.** A PMC admin, already authenticated in the existing Units app, opens the new "Finance" nav item, picks a PMC (if they manage more than one) and a date range, and sees the Trial Balance render inline, with a clear balanced/unbalanced indicator. → consumes `GET reports/trial-balance`.
- **UJ-2. PMC admin reconciles a bank statement.** A PMC admin uploads a CSV of bank statement lines, sees the system's suggested matches against unreconciled Bank Journal entries, confirms the correct ones, rejects or manually re-pairs the wrong ones, and the reconciliation queue empties out as they work through it. → consumes `POST reconciliation/bank-statement-import`, `GET reconciliation/suggested-matches`, `POST reconciliation/match`.
- **UJ-3. Owner reviews their Ageing report.** An Owner opens Finance → Ageing and sees which tenants are overdue and by how much, scoped strictly to PMCs they can reach — the same scoping rule already enforced server-side in Phase 1. → consumes `GET reports/ageing`.
- **UJ-4. PMC admin drills into a Trial Balance line.** A PMC admin sees Rent Income totals 60,000 on the Trial Balance and needs to know why; they click through to that Account's Ledger detail and see the individual `JournalEntry`/`LedgerLine` rows that sum to it, including a visible link from any reversed entry (e.g. a bounce) back to the original posting it reversed. → consumes a new Ledger drill-down read, layered over existing `JournalEntry`/`LedgerLine` data.

## 3. Glossary

Inherits the full glossary from `docs/finance/prd.md` §3 (FinancePMCProfile, Ledger, Journal, Account, CoA, Trial Balance, P&L, Balance Sheet, Ageing, Bank Reconciliation, PDC, Commission, Security Deposit) without change. Frontend-specific terms added here:

- **Finance section** — the new top-level navigation area in `units-frontend` (parallel to existing sections like Properties, Owners, Tenants) hosting all Finance pages.
- **PMC selector** — a UI control letting a user who reaches more than one `FinancePMCProfile`-activated PMC choose which one's books they're viewing; not needed for a user scoped to exactly one PMC.
- **Reconciliation workspace** — the interactive page (distinct from the four read-only report pages) where a PMC admin uploads statements and works through suggested/manual matches.
- **Ledger drill-down** — a detail view under an Account showing the individual `JournalEntry`/`LedgerLine` rows that sum to that Account's reported balance, including any reversal linkage (`reversed_journal_entry_id`).
- **Chart of Accounts (CoA) view** — a standalone page listing a PMC's fixed, seeded Accounts (name, type, current balance) as a reference, independent of any single report.

## 4. Features

### 4.1 Finance Navigation & PMC Scoping Shell

**Description:** Before any report renders, the frontend needs one shared entry point and one shared PMC-scoping mechanism, reused by every Finance page — mirroring how the backend already centralized JWT/PMC-scoping in Story 3.1's `get_pmc_ids_for_user()` bridge rather than re-deriving it per endpoint.

**Functional Requirements:**

#### FFR-1: Finance nav entry point

A new "Finance" item appears in the existing dashboard navigation shell, visible to Owner and PMC (`COMPANY_USER`) roles, hidden for Tenant — reusing the existing `permission.service.ts` role-check pattern already used by other nav items (e.g. `pmc`, `owners`).

**Consequences (testable):**
- Logging in as Tenant never shows a Finance nav item.
- Logging in as Owner or PMC shows it, landing on the Finance Overview page (FFR-2).

#### FFR-2: Finance Overview

A landing page under Finance summarizing, per reachable PMC: current-month net P&L, total outstanding AR (from Ageing), and a Trial Balance balanced/unbalanced indicator — each a small card linking through to its full report page.

**Consequences (testable):**
- A user reachable to N PMCs sees N sets of summary cards (or a PMC selector plus one set, per FFR-3), never another PMC's figures.

#### FFR-3: PMC selector (multi-PMC users only)

For a user who reaches more than one `FinancePMCProfile`-scoped PMC, every Finance page exposes a PMC selector; for a single-PMC user, the selector is omitted and that PMC is implicit. The selector (and the Overview page) must distinguish a PMC that has **no `FinancePMCProfile` yet** (Finance not activated for it — backend FR-1 activation is still a management-command-only step) from a PMC with an activated profile but zero activity, since both currently return an empty/near-empty response and would otherwise look identical and confusing.

**Consequences (testable):**
- Switching the PMC selector re-fetches and re-renders the current page's data for the newly selected PMC only — no stale data from the previous selection remains visible.
- A user with exactly one reachable PMC never sees a selector control at all.
- A PMC reachable by the user but never activated for Finance (no `FinancePMCProfile` row) shows an explicit "Finance not yet set up for this PMC" empty state on every Finance page, rather than a bare empty table or a generic error.
- A PMC that IS activated but has zero posted activity yet shows a distinct "no activity yet" empty state, never the same message as the not-activated case.

**Out of Scope:**
- Organization-level roll-up across PMCs — unchanged Phase 2-of-Phase-2 deferral, per backend PRD §6.2.

### 4.2 Static Reports: Trial Balance, P&L, Balance Sheet, Ageing

**Description:** Four read-only report pages, each a thin rendering layer over an already-built, already-tested endpoint. Trial Balance, P&L, and Balance Sheet share one reusable "report table" component (date-range or as-of-date picker + account rows + totals row); Ageing reuses the same shell but renders paginated bucketed rows instead. Realizes UJ-1 and UJ-3.

**Functional Requirements:**

#### FFR-4: Trial Balance page

Renders `GET reports/trial-balance` for the selected PMC and a date-range picker; shows every Account's debit/credit totals and a footer row; visually flags if debits ≠ credits (should never happen given backend NFR-3, but the UI must not hide a violation if the invariant is ever broken).

**Consequences (testable):**
- Selecting a new date range re-fetches and re-renders without a full page reload.
- If the API ever returns debits ≠ credits, the page displays a visible warning rather than silently showing mismatched totals.

#### FFR-5: Profit & Loss page

Renders `GET reports/profit-loss` for the selected PMC and a date-range picker; shows Income accounts, Expense accounts, and the resulting net P&L figure, with income/expense visually distinguished (e.g. color or section headers).

**Consequences (testable):**
- The displayed net figure always equals sum(Income) − sum(Expense) as returned by the API — the UI never recomputes or overrides the backend's number.

#### FFR-6: Balance Sheet page

Renders `GET reports/balance-sheet` for the selected PMC and an as-of-date picker; shows Asset, Liability, and Equity sections and confirms Assets = Liabilities + Equity.

**Consequences (testable):**
- Changing the as-of-date re-fetches and re-renders the full breakdown for that date.

#### FFR-7: Ageing page

Renders `GET reports/ageing` for the selected PMC; shows outstanding AR bucketed by days overdue (current, 1-30, 31-60, 61-90, 90+), paginated per the backend's `paginator` response shape (the only one of the four reports that paginates, per backend AD-1/Structural Seed).

**Consequences (testable):**
- The page uses the existing shared table-pagination component (`dashboard/component/table-pagination`) already used elsewhere in the app, not a bespoke pagination control.
- Bucket columns render in the same left-to-right order as the API returns them (current → 90+), with no client-side bucket recomputation.

**Out of Scope:**
- Any client-side recomputation of report figures — every number displayed is exactly what the corresponding endpoint returned; the frontend renders, it does not calculate.
- A dedicated PDC register view — unchanged deferral from backend PRD §4.3 Notes; Ageing's buckets remain the only overdue-cheque surface.

### 4.3 Bank Reconciliation Workspace

**Description:** The one genuinely interactive Finance page: CSV upload plus a matching queue, for PMC admins only (not Owners — reconciliation is PMC operator work per backend UJ-2). Realizes UJ-2.

**Functional Requirements:**

#### FFR-8: Bank statement CSV upload

A PMC admin uploads a CSV via the existing shared upload-document component pattern (`dashboard/component/upload-document`); on success, shows a count of statement lines created, matching `POST reconciliation/bank-statement-import`'s per-row creation contract.

**Consequences (testable):**
- Uploading a CSV with N valid rows shows a confirmation reflecting N created lines; a malformed CSV surfaces the backend's error message via the standard `{content, message, status}` envelope rather than a generic failure.

#### FFR-9: Suggested matches queue

Renders `GET reconciliation/suggested-matches`: a list of statement lines paired with their suggested unreconciled Bank Journal entry (or none, if no suggestion exists), each with Confirm and Reject actions.

**Consequences (testable):**
- Confirming a suggested match calls `POST reconciliation/match` and removes both sides from the queue.
- Rejecting a suggested match leaves both sides in the queue, available for a different pairing (manual, FFR-10) — it does not delete the statement line.

#### FFR-10: Manual match pairing

For a statement line with no suggestion, or one whose suggestion was rejected, the PMC admin can manually select any other unreconciled Bank Journal entry from the same PMC's queue to pair it with.

**Consequences (testable):**
- The manual pairing UI only ever lists Bank Journal entries scoped to the currently selected PMC — never a cross-PMC entry.
- Attempting to pair an already-matched entry a second time is rejected by the backend (FR-15 constraint) and the UI surfaces that rejection rather than optimistically marking it matched.

#### FFR-11: Un-reconcile / correct a wrong match

A PMC admin can un-reconcile a previously confirmed pair, returning both sides to the open queue, then confirm a different pairing.

**Consequences (testable):**
- After un-reconciling, both the statement line and the Ledger entry reappear in the open queue (FFR-9/FFR-10) rather than remaining hidden.

#### FFR-12: Reconciliation outstanding-items summary

The reconciliation workspace shows a running count of unmatched statement lines and unmatched Bank Journal entries for the selected PMC, so a PMC admin can tell when a period's reconciliation is actually complete (both counts at zero) versus still in progress.

**Consequences (testable):**
- The counts update immediately after any confirm, reject, manual-match, or un-reconcile action, without requiring a manual page refresh.
- When both counts reach zero, the workspace visibly indicates the period is fully reconciled rather than just showing an empty list with no explanation.

**Out of Scope:**
- Live bank feed/API integration — unchanged from backend PRD §6.2; this workspace only ever operates on manually uploaded CSVs.
- Owner access to this workspace — reconciliation stays PMC-admin-only, consistent with UJ-2's framing in both this document and the backend PRD.

### 4.4 Ledger Drill-Down & Chart of Accounts

**Description:** Three related concept gaps identified during frontend PRD review, all stemming from the same root cause: the four report pages in §4.2 are summaries, and nothing in the original scope let a user see the underlying accounting detail those summaries are built from. This feature closes that gap with two new read-only pages layered over data (`Account`, `JournalEntry`, `LedgerLine`) that already exists and is already returned in some form by existing endpoints — no new backend write path, and only additive read access is assumed (see Open Questions if a new endpoint/query proves necessary). Realizes UJ-4.

**Functional Requirements:**

#### FFR-13: Chart of Accounts view

A standalone page listing the selected PMC's full set of seeded Accounts (name, type — Asset/Liability/Income/Expense — and current balance), independent of any single report, reusing the same PMC selector and empty-state rules as FFR-3.

**Consequences (testable):**
- Every Account seeded for a PMC's `FinancePMCProfile` (backend FR-3's fixed 7+ Account set) appears exactly once, correctly labeled by type.
- The page is read-only — consistent with backend FR-3's Notes that CoA is fixed/non-configurable in Phase 1, this view never exposes an edit/add-Account action.

#### FFR-14: Ledger drill-down per Account

From the Chart of Accounts view (FFR-13) or directly from a Trial Balance line (FFR-4), a user can open an Account's Ledger detail: the individual `JournalEntry`/`LedgerLine` rows posted to it for a selected date range, each showing date, source (e.g. `source_lease_transaction_id`), debit/credit amount, and running balance.

**Consequences (testable):**
- The sum of all displayed `LedgerLine` amounts for an Account and date range exactly equals that Account's total shown on the Trial Balance for the same range — the drill-down is provably a decomposition of the summary figure, never a separately computed number.
- Every row is traceable to its originating event via the displayed source reference, consistent with backend AD-14's traceability guarantee.

#### FFR-15: Reversed/bounced entry linkage

Where a `JournalEntry` carries a `reversed_journal_entry_id` (a bounce reversal, per backend FR-6/AD-16), the Ledger drill-down (FFR-14) visibly links the reversing entry to the original it reversed, in both directions.

**Consequences (testable):**
- Viewing a reversed entry shows a visible link/reference to the reversal that followed it; viewing the reversal shows a visible link back to the original.
- The original entry is always displayed as-is alongside its reversal — consistent with backend AD-16, the UI never hides, merges, or nets the two rows into one, since the original is never edited or deleted server-side.

**Out of Scope:**
- Any manual journal-entry creation or editing from this view — this feature is read-only drill-down, not the manual journal-entry UI already excluded in §5/§6.2.
- A general free-form Ledger search/filter UI beyond per-Account, per-date-range drill-down — deferred; revisit only if FFR-14's scoped view proves insufficient in practice.

## 5. Non-Goals (Explicit)

- This PRD does not add, remove, or modify any `units-finance` API endpoint — the backend contract (Epics 1–4, already implemented) is treated as frozen.
- This PRD does not build a manual journal-entry UI — unchanged from backend PRD §5; there is no corresponding API to call.
- This PRD does not build Organization-level consolidated views across PMCs — unchanged Phase-2-of-Phase-2 deferral (backend PRD §6.2).
- This PRD does not change existing non-Finance pages, navigation items, or role-guard logic beyond adding the one new Finance nav entry (FFR-1) — no refactor of `units-frontend`'s existing structure.
- This PRD does not build a non-UAE PMC experience — unchanged from backend PRD §6.2; every PMC this UI renders is by definition UAE/AED/5%-VAT, since that's all `FinancePMCProfile` supports today.

## 6. MVP Scope

### 6.1 In Scope

- One new "Finance" section in the existing `units-frontend` Angular app, gated to Owner and PMC (`COMPANY_USER`) roles via the existing `permission.service.ts` pattern.
- Finance Overview landing page summarizing key figures per reachable PMC (§4.1).
- A shared PMC selector for multi-PMC users, reusing existing shared-component conventions (§4.1).
- Four read-only report pages — Trial Balance, P&L, Balance Sheet, Ageing — each a thin render layer over its already-built endpoint, sharing one reusable report-table component where the report shape allows (§4.2).
- One interactive Bank Reconciliation workspace (upload + suggested-match queue + confirm/reject/manual-pair/un-reconcile + outstanding-items summary), PMC-admin-only (§4.3).
- A standalone Chart of Accounts view and a per-Account Ledger drill-down (including reversed/bounced-entry linkage), both read-only (§4.4).
- An explicit "Finance not yet activated for this PMC" empty state, distinct from a "activated but no activity yet" state, applied consistently across every Finance page (§4.1 FFR-3).
- Reuse of existing frontend infrastructure throughout: JWT auth (already shared with `units-finance` per backend AD-17), role guards, shared table/pagination/upload components, and the existing dashboard shell — no new auth mechanism, no new component library.

### 6.2 Out of Scope for MVP (Phase 2)

- **Organization-level consolidated Finance views** — deferred until the backend itself supports it (backend PRD §6.2); building a frontend roll-up ahead of the API would require client-side aggregation this PRD explicitly disallows (§4.2 Out of Scope).
- **Non-UAE PMC UI treatment** (e.g. multi-currency display, different VAT labeling) — deferred alongside backend non-UAE PMC support.
- **Manual journal-entry UI** — deferred indefinitely; no corresponding write API exists to call.
- **Live bank feed integration UI** — deferred alongside the backend's manual-CSV-only scope.
- **A dedicated PDC register view** distinct from the Ageing page — unchanged deferral, backend PRD §4.3 Notes.
- **Commission-detail redaction for Owners** — Open Question 9 in the backend PRD (whether an Owner's Trial Balance should exclude PMC-internal commission detail) is not yet resolved server-side; until it is, the frontend renders exactly what the API returns for the authenticated role, with no additional client-side redaction layer invented to compensate.
- **Free-form Ledger search/filter beyond per-Account drill-down** — §4.4 FFR-14 scopes drill-down to one Account/date-range at a time; a general cross-Account Ledger search UI is deferred until a concrete need is shown.
- **Editing or annotating the Chart of Accounts** — FFR-13 is read-only by design, consistent with the backend's fixed, non-configurable CoA (backend FR-3 Notes).
- **Mobile-specific responsive redesign** — Phase 2 targets the existing desktop-first dashboard shell's existing breakpoints; a dedicated mobile Finance experience is not in scope.

## 7. Success Metrics

**Primary**
- **SM-1**: Every figure rendered on any Finance page exactly matches the corresponding API response for the same PMC/date range/as-of-date, verified by spot-checking at least one page per report type against a direct API call. Validates §4.2's "render, don't calculate" constraint.
- **SM-2**: A PMC admin can complete the full reconciliation loop (upload → suggested match → confirm) for a realistic CSV without needing to leave the Finance workspace or use Swagger/Postman as a fallback. Validates §4.3/UJ-2.

**Secondary**
- **SM-3**: An Owner logged in with visibility into PMC A but not PMC B never sees PMC B's data anywhere in the Finance section, including in the PMC selector's own options list. Validates §4.1 FFR-3 scoping.
- **SM-4**: 100% of existing non-Finance pages remain unchanged in behavior and layout after this build — verified by exercising the existing Owner/PMC/Tenant flows once Finance ships. Validates §5's non-goal of not touching existing structure.
- **SM-5**: For at least one Account on one PMC, the sum of Ledger drill-down rows (FFR-14) exactly reconciles to that Account's Trial Balance figure for the same period, checked by spot-check. Validates §4.4's decomposition guarantee.
- **SM-6**: A PMC never reachable-but-unactivated for Finance shows the correct empty state (FFR-3) on first visit to every Finance page, with no bare error or blank table observed. Validates the activation-vs-no-activity distinction closing gap 4.

**Counter-metrics (do not optimize)**
- **SM-C1**: Visual polish on the four report pages should not come at the expense of SM-1 (rendering fidelity) — a beautifully styled page showing a stale or client-recomputed number is worse than a plain table showing the exact API figure.

## 8. Open Questions

1. Should the Finance Overview page (FFR-2) call all four/five endpoints on load for every reachable PMC, or lazy-load per-PMC only once a user selects it via the PMC selector? Affects perceived load time for multi-PMC users — recommend resolving during `bmad-architecture` or the first spec pass, since it shapes the Overview page's data-fetching pattern reused nowhere else.
2. Does the existing `units-frontend` app have a shared HTTP-error-envelope handler already parsing the `{content, message, status}` shape backend-wide, or does Finance need to introduce the first one? If one already exists (likely, given the rest of the app already talks to `units-backend`'s own `prepare_response()`-shaped responses), Finance should reuse it rather than add a second error-handling convention.
3. Backend PRD Open Question 9 (commission-detail redaction for Owners) remains unresolved server-side — flagged here as a dependency: if it resolves before this PRD's stories are built, Trial Balance/P&L rendering may need an Owner-specific column-hiding rule added late. Recommend checking backend status before finalizing FFR-4/FFR-5's story specs.
4. Should the PMC selector's state (last-selected PMC) persist across a session/page reload (e.g. via localStorage), or always reset to a default on every login? Minor UX decision, doesn't block starting the build.
5. What's the minimum acceptable CSV format/validation feedback for FFR-8 — does the existing `upload-document` shared component already surface row-level CSV errors, or does Finance need a custom error list for malformed bank-statement rows? Worth checking the shared component's existing capability before speccing FFR-8 in detail.
6. Does `units-finance` already expose enough query surface (e.g. via the existing report endpoints' underlying querysets) to build FFR-13/FFR-14 (Chart of Accounts view, Ledger drill-down) as pure additional frontend reads against existing data, or does closing this gap require a small new read-only endpoint (e.g. `GET accounts/`, `GET accounts/{id}/ledger-lines`) on the backend? This PRD assumes the latter is likely needed, since no existing endpoint currently returns raw `Account`/`LedgerLine` rows — recommend a short backend spike to confirm before speccing FFR-13/FFR-14 stories in `bmad-create-epics-and-stories`.
7. Same question as #6 for FFR-12 (reconciliation outstanding-items summary): do `suggested-matches`/reconciliation endpoints already return enough to derive unmatched counts client-side, or is a small dedicated summary read needed?
8. For FFR-3's empty-state distinction (gap 4), does any existing endpoint indicate "no `FinancePMCProfile` exists for this PMC" as a distinguishable response (e.g. a specific 404/error code) versus "profile exists, zero data," or would every current endpoint currently return the same empty-looking response for both cases? If the latter, this is also a small backend gap, not purely a frontend one.

## 9. Assumptions Index

- §4.1: The existing `permission.service.ts` role-check pattern (already gating other nav items like `pmc`, `owners`) is assumed reusable as-is for gating the new Finance nav entry — no new permission-checking mechanism is assumed necessary.
- §4.2 / §4.3: All five Finance-consuming pages are assumed to authenticate using the JWT the existing frontend already attaches to other authenticated requests (per backend AD-17's shared `JWT_SECRET_KEY` cutover) — no new login flow or token type is assumed needed for Finance specifically.
- §4.2: The existing `dashboard/component/table-pagination` component is assumed directly reusable for the Ageing page's pagination without modification, since the backend's pagination envelope follows the same platform-wide `prepare_response()` convention the rest of the app already consumes.
- §4.3 FFR-8: The existing `dashboard/component/upload-document` shared component is assumed reusable for CSV upload with minimal adaptation — see Open Question 5 if this assumption proves wrong once inspected in detail.
- §6.2: Commission-detail redaction for Owners is assumed *not* required for MVP, since it remains an open, unresolved question on the backend side (backend PRD Open Question 9) — the frontend renders whatever the API returns per role until/unless the backend introduces redaction.
- §4.4 FFR-13/FFR-14: Assumed to require one or two small new read-only backend endpoints (Chart of Accounts list, per-Account Ledger-line detail) that don't currently exist — see Open Question 6. Unlike the rest of this PRD, this is the one place where "zero backend changes" may not fully hold; flagged explicitly rather than silently assumed away.
- §4.3 FFR-12: Assumed derivable purely client-side from existing `suggested-matches` response data (counting unmatched rows) without a new endpoint — see Open Question 7 if this proves insufficient.
- §4.1 FFR-3: Assumed a not-yet-activated PMC and an activated-but-empty PMC are currently indistinguishable from existing API responses alone, meaning the frontend may need a small backend signal (or a documented convention on an existing error/empty response) to tell them apart — see Open Question 8.
