# Units Finance — Product Accounting Model

Status: **approved** (2026-09-27; decisions D1–D8 and B4 confirmed, see §8). Supersedes the posting rules in `prd.md` §4.2 and the 9-account seed.
Business rules: approved answers Q1–Q25 and B1–B8. Open decisions are listed in §8; nothing marked open is implemented.

---

## 1. Principles

1. **Operational documents drive accounting.** Invoices, cheques, collections, expenses, settlements and terminations raise accounting events (outbox). Posting rules turn events into journals. Nobody types debits and credits, except in approval-gated adjustment journals.
2. **Posting rules use logical roles, never accounts.** A rule says "debit `AR_TENANT`". `AccountMapping` resolves the role to an account in the PMC's own chart of accounts (block override, then property override, then PMC default). An unmapped role stops the posting with an error, and the event shows in Failed Postings.
3. **The product has its own Chart of Accounts** (§3). No ARESTA code or name is copied or hardcoded. ARESTA is used only to check that every existing business scenario is covered (§7).
4. **Detail comes from dimensions, not from extra accounts.** One `AR_TENANT` account serves every tenant; one `OWNER_PAYABLE` serves every owner. Each ledger line carries property, block, unit, lease, tenant, owner, installment and vendor ids, so statements per unit, tenant or owner are queries, not sub-ledgers. (ARESTA keeps per-building and per-landlord accounts; this product doesn't need to.)
5. **Rates come from masters, never code.** VAT comes from `TaxRule`, by what is being taxed (rent, leasing commission, management fee; system defaults: rent 0% residential and 5% commercial/industrial, commission 5%, fee 5%) and the unit's usage. The management fee % comes from `Unit.commission_percent`. Return charges, channel charges and penalties (amount and VAT %) come from the Charges master. Every rate used is snapshotted on the document it applies to.

## 2. Posting roles (`AccountMapping` keys)

| Role | Purpose |
|---|---|
| `AR_TENANT` | Amounts tenants owe (invoices, debit notes) |
| `DEFERRED_RENTAL_INCOME` | Invoiced rent not yet earned |
| `RENTAL_INCOME` | Earned rent on INTERNAL (company) units |
| `RENTAL_INCOME_CLEARING` | Earned rent on MANAGED units, held for owners until the monthly allocation; nets to zero each month |
| `DISCOUNT_ALLOWED` | Lease discounts (see §8 D3) |
| `VAT_OUTPUT` | Output VAT on rent and tenant charges |
| `MANAGEMENT_FEE_VAT` | Output VAT on management fees (may map to the same account as `VAT_OUTPUT`) |
| `VAT_INPUT` | Recoverable VAT on vendor bills *(not in the B8 list; needed for expenses with VAT, see §7)* |
| `SECURITY_DEPOSIT_HELD` | Tenant security deposits (liability) |
| `TENANT_REFUND_PAYABLE` | Refunds owed to tenants (termination and deposit refunds) |
| `PDC_CHEQUES_IN_HAND` | Reserved. Collected PDCs stay in the PDC register with **no GL entry** (Q6), so no rule posts to it |
| `CHEQUES_IN_BANK` | Cheques deposited, awaiting clearing |
| `SETTLEMENT_PENDING` | Card, gateway and online collections awaiting bank settlement |
| `BANK` | Default bank account; each `CompanyBankAccount` can point at its own account |
| `CASH_IN_HAND` | Cash collections *(not in the B8 list; needed for cash receipts)* |
| `OWNER_PAYABLE` | Amounts due to owners of MANAGED units |
| `MANAGEMENT_FEE_INCOME` | Management fee earned on MANAGED units |
| `RETURN_CHARGE_INCOME` | Cheque-return charges |
| `CHANNEL_CHARGE_INCOME` | Payment-channel charges passed to tenants |
| `PENALTY_INCOME` | Termination and other penalties |
| `OTHER_CHARGE_INCOME` | Other tenant charges |
| `BANK_GATEWAY_FEE_EXPENSE` | Fees charged to the company by banks and gateways |
| `EXPENSE` | Default operating expense (each Expense Master type maps its own account) |
| `AP_VENDOR` | Vendor payables |

The code currently implements the earlier role list; it will be renamed to this list when implementation starts.

## 3. Product Chart of Accounts (standard template)

Seeded for every newly activated PMC, with each role pre-mapped. It is editable per PMC through the chart-of-accounts master (add sub-accounts, e.g. one bank account per `CompanyBankAccount`), and codes and names are the PMC's to change. Headers are non-postable.

| Code | Account | Type | Default role |
|---|---|---|---|
| 1000 | **Assets** | Asset (header) | |
| 1100 | Cash & Bank | Asset (header) | |
| 1110 | Cash in Hand | Asset | `CASH_IN_HAND` |
| 1120 | Bank – Operating | Asset | `BANK` |
| 1130 | Cheques in Bank (Clearing) | Asset | `CHEQUES_IN_BANK` |
| 1140 | Card / Gateway Settlement Pending | Asset | `SETTLEMENT_PENDING` |
| 1200 | Receivables | Asset (header) | |
| 1210 | Tenant Receivables | Asset | `AR_TENANT` |
| 1220 | VAT Input Recoverable | Asset | `VAT_INPUT` |
| 2000 | **Liabilities** | Liability (header) | |
| 2100 | Tenant Deposits & Refunds | Liability (header) | |
| 2110 | Security Deposits Held | Liability | `SECURITY_DEPOSIT_HELD` |
| 2120 | Tenant Refunds Payable | Liability | `TENANT_REFUND_PAYABLE` |
| 2200 | Deferred Rental Income | Liability | `DEFERRED_RENTAL_INCOME` |
| 2300 | Owner Accounts | Liability (header) | |
| 2310 | Rental Income Clearing – Managed Units | Liability | `RENTAL_INCOME_CLEARING` |
| 2320 | Owner Payables | Liability | `OWNER_PAYABLE` |
| 2400 | VAT Payable | Liability (header) | |
| 2410 | VAT Output – Rent & Charges | Liability | `VAT_OUTPUT` |
| 2420 | VAT Output – Management Fees | Liability | `MANAGEMENT_FEE_VAT` |
| 2500 | Vendor Payables | Liability | `AP_VENDOR` |
| 3000 | **Equity** | Equity (header) | |
| 3100 | Owner's Capital | Equity | |
| 4000 | **Income** | Income (header) | |
| 4100 | Rental Income – Company Units | Income | `RENTAL_INCOME` |
| 4190 | Discount Allowed on Rent (contra) | Income | `DISCOUNT_ALLOWED` |
| 4200 | Management Fee Income | Income | `MANAGEMENT_FEE_INCOME` |
| 4300 | Tenant Charges Income | Income (header) | |
| 4310 | Cheque Return Charges | Income | `RETURN_CHARGE_INCOME` |
| 4320 | Payment Channel Charges | Income | `CHANNEL_CHARGE_INCOME` |
| 4330 | Penalty Income | Income | `PENALTY_INCOME` |
| 4340 | Other Charges Income | Income | `OTHER_CHARGE_INCOME` |
| 5000 | **Expenses** | Expense (header) | |
| 5100 | Property Operating Expenses | Expense | `EXPENSE` |
| 5200 | Bank & Gateway Fees | Expense | `BANK_GATEWAY_FEE_EXPENSE` |
| 5300 | Payroll (manual allocation) | Expense | |

Retained earnings stay derived in the Balance Sheet (unchanged). `RENTAL_INCOME_CLEARING` is a **liability**, because it is owner money held by the company. ARESTA files its equivalent under receivables; see §7.

## 4. Documents and amounts

- **Lease (contract):** gross contract amount, lease-level discount (B3), net contract amount = gross − discount.
- **Installments (rent):** the net contract amount is split evenly across `payment_count` installments, and the last installment absorbs rounding. Each installment keeps its original share, discount share and net amount, so Σ original = gross, Σ discount = discount and Σ net = net. VAT = net × the `TaxRule` rate for the unit's usage on the invoice date (Residential 0%, Commercial and Industrial 5%). Total = net + VAT.
  - Example (B3): gross 120,000, discount 12,000, 12 installments → each installment is original 10,000, discount 1,000, net 9,000 (plus 450 VAT if commercial).
- **Invoice (per rent / other-charge installment):** invoice date = due date = cheque date (B2). Output VAT is booked on the invoice (B1). There is no invoice for the deposit installment (B5).
- **Deposit installment:** cheque-only (2-return rule applies). It is never invoiced, never revenue, and never part of revenue recognition.
- **Management-fee invoice (per MANAGED unit per month):** separate from rent installments (B6). Fee % = `Unit.commission_percent` (currently 5%). VAT = the Management Fee charge's VAT % (currently 5%). There is none for INTERNAL units.
- **Owner settlement (per owner, at their frequency):** built from the monthly allocations (B7). The frequency only changes when payouts happen, never the monthly calculation.

## 5. Posting rules

Every journal carries dimensions: unit, lease, tenant and installment where applicable, property and block always, and owner/vendor where applicable. Idempotency is one journal per event per rule. `entry_date` is the business date shown.

| # | Event (source) | Date | Debit | Credit |
|---|---|---|---|---|
| R1 | **Rent installment invoiced** | invoice date | `AR_TENANT` total | `DEFERRED_RENTAL_INCOME` net; `VAT_OUTPUT` VAT (0 → no line) |
| R2 | **Other-charge installment / debit note invoiced** | invoice date | `AR_TENANT` total | `OTHER_CHARGE_INCOME` (or the charge's role) amount; `VAT_OUTPUT` VAT |
| R3 | **Monthly rent recognition, INTERNAL unit** | month end | `DEFERRED_RENTAL_INCOME` month net | `RENTAL_INCOME` month net *(discount presentation: §8 D3)* |
| R4 | **Monthly rent recognition, MANAGED unit** | month end | `DEFERRED_RENTAL_INCOME` month net | `RENTAL_INCOME_CLEARING` month net |
| R5 | **Rent cheque deposited** | deposit date | `CHEQUES_IN_BANK` | `AR_TENANT` |
| R6 | **Deposit cheque deposited** | deposit date | `CHEQUES_IN_BANK` | `SECURITY_DEPOSIT_HELD` |
| R7 | **Cheque cleared** | clearing date | `BANK` (the account's bank) | `CHEQUES_IN_BANK` |
| R8 | **Cheque returned** | return date | `AR_TENANT` (rent) or `SECURITY_DEPOSIT_HELD` (deposit) | `CHEQUES_IN_BANK` |
| R9 | **Return charge** (debit note, collected separately, Q8) | return date | `AR_TENANT` | `RETURN_CHARGE_INCOME`; `VAT_OUTPUT` per charge |
| R10 | **Non-cheque collection settled** | collection date | `CASH_IN_HAND` / `BANK` / `SETTLEMENT_PENDING` (card, gateway, online) for the total | `AR_TENANT` base; `CHANNEL_CHARGE_INCOME` charge; `VAT_OUTPUT` charge VAT |
| R11 | **Gateway/card batch settled by provider** | settlement date | `BANK` net received; `BANK_GATEWAY_FEE_EXPENSE` provider fee (+ `VAT_INPUT`) | `SETTLEMENT_PENDING` gross |
| R12 | **Expense on a MANAGED unit/building** | bill date | `RENTAL_INCOME_CLEARING` net (+ `VAT_INPUT`) | `AP_VENDOR` |
| R13 | **Expense on an INTERNAL unit / company** | bill date | expense type's account (`EXPENSE`) (+ `VAT_INPUT`) | `AP_VENDOR` |
| R14 | **Monthly management fee, MANAGED unit** | month end | `RENTAL_INCOME_CLEARING` fee + VAT | `MANAGEMENT_FEE_INCOME` fee; `MANAGEMENT_FEE_VAT` VAT |
| R15 | **Monthly owner allocation, MANAGED unit** | month end | `RENTAL_INCOME_CLEARING` remaining balance | `OWNER_PAYABLE`, split by the ownership % in force that month (one line per owner) |
| R16 | **Owner payout** (monthly, quarterly or half-yearly) | payout date | `OWNER_PAYABLE` | `BANK` |
| R17 | **Vendor payment** | payment date | `AP_VENDOR` | `BANK` |
| R18 | **Deposit carried to renewal lease** | renewal activation | `SECURITY_DEPOSIT_HELD` (old lease dims) | `SECURITY_DEPOSIT_HELD` (new lease dims) |
| R19 | **Termination / refund** | Phase 10; B4 still open | | |

The monthly close order per MANAGED unit is: recognition (R4), then the month's expenses (R12), then fee (R14), then allocation (R15). After that, `RENTAL_INCOME_CLEARING` is zero for the unit.

**Worked month (MANAGED commercial unit, owners A 60% / B 40%, fee 5%, one 1,000 expense, fee base per §8 D1 option A):**

| Step | Dr | Cr |
|---|---|---|
| R1 invoice (1 Oct) | AR 9,450 | Deferred 9,000; VAT out 450 |
| R5 → R7 cheque deposited, then cleared | Cheques in bank 9,450 → Bank 9,450 | AR 9,450 → Cheques in bank 9,450 |
| R4 recognition (31 Oct) | Deferred 9,000 | Clearing 9,000 |
| R12 expense | Clearing 1,000 | AP vendor 1,000 |
| R14 fee: 8,000 × 5% = 400, VAT 20 | Clearing 420 | Mgmt fee income 400; Mgmt fee VAT 20 |
| R15 allocation: 7,580 | Clearing 7,580 | Owner payable A 4,548; B 3,032 |

Clearing ends the month at 0, and Deferred holds the unearned balance of any multi-month installment.

## 6. Cheque lifecycle (unchanged, confirmed)

Collected (register only, no GL) → deposited (R5/R6, expected clearing date = deposit date + the bank account's clearing days; a post-dated cheque can only be deposited on or after its cheque date) → cleared (R7), or returned (R8, plus the optional R9). Maximum **2 returns per installment**, deposit included:
- After return #1, a replacement cheque is required (recorded by the accountant).
- After return #2, no further cheque is accepted on any creation path. Only a non-cheque method is allowed (R10).

## 7. ARESTA validation (reference only; nothing copied)

| Existing ARESTA scenario | Covered by | Notes |
|---|---|---|
| Rent receivable per building | `AR_TENANT` + property dimension | Per-building accounts replaced by dimensions |
| PDC account (PDCs in the GL) | PDC register, no GL (Q6) | **Difference**: ARESTA books PDCs in hand; this product doesn't (confirmed Q6) |
| Rental income posting account | `RENTAL_INCOME_CLEARING` | ARESTA classifies it as an asset; here it's a liability (owner money) |
| Deferred income (tenants) | `DEFERRED_RENTAL_INCOME` | Same flow |
| Rental revenue per building | `RENTAL_INCOME` + property dimension | INTERNAL units only |
| Accounts payable – landlords (per landlord) | `OWNER_PAYABLE` + owner dimension | |
| Commission on rent collection | `MANAGEMENT_FEE_INCOME` | |
| Security deposit received (tenants) | `SECURITY_DEPOSIT_HELD` | |
| Output tax payable | `VAT_OUTPUT` / `MANAGEMENT_FEE_VAT` | |
| Cheque return penalties | `RETURN_CHARGE_INCOME` | |
| Bank charges | `BANK_GATEWAY_FEE_EXPENSE` | |
| Accounts payable – suppliers; cost of rental revenue | `AP_VENDOR`; `EXPENSE` + dimensions | |
| **Input tax receivable / irrecoverable input tax** | `VAT_INPUT` *(added)* | Needed for expenses with VAT; §8 D7 |
| **Renewal commissions; commission received (new)** | *not covered yet* | Tenant-paid leasing/renewal commission, §8 D2 |
| **Booking advance (refundable)** | *not covered yet* | The proposal holding amount, §8 D7 |
| **Cheque holding penalties** | *not covered yet* | §8 D7 |
| **Leased buildings / prepaid rent / management fee expense** | *not covered yet* | Buildings the company leases in and sublets, §8 D7 |
| Electricity deposits; payroll provisions and accruals; bad-debt provision | out of current scope | Payroll is manual allocation (Q22) |

## 8. Confirmed decisions (2026-09-27)

- **D1** Management fee = **net distributable** × fee %, where net distributable = recognized rent − applicable expenses − applicable charges. VAT 5% on the fee. Example: 100,000 − 10,000 − 5,000 = 85,000; fee 4,250; VAT 212.50; invoice 4,462.50.
- **D2** Tenant-paid leasing commission (`Lease.commission`): invoiced once at lease start, 5% VAT, company income (`LEASING_COMMISSION_INCOME`). Not rental income and never part of owner distributable revenue.
- **D3** Discount is lease-level. Revenue is recognized **net** of discount for both INTERNAL and MANAGED units; on MANAGED units the owner bears the discount. There is no separate discount line. Gross, discount and net stay on the lease and on every installment. (`DISCOUNT_ALLOWED` is kept as a role for future use; no rule posts to it now.)
- **D4** Equal monthly recognition (installment net ÷ months covered), starting from the period's start month, with no day proration. The last month absorbs rounding.
- **D5** Invoices are created with the schedule as SCHEDULED. A daily job issues each one on its invoice date (= cheque due date) and posts R1 with VAT then. No future-dated journals.
- **D6** Owners are settled on earned (recognized) rent, independent of tenant collection. The company carries collection risk, and tenant AR stays separate from owner settlement.
- **D7** In scope now: everything in §5, plus leasing commission, termination charges/penalties, refunds, debit and credit notes, and INTERNAL units. Future scope, with the design left extensible: input VAT on vendor bills, booking/holding advances, cheque holding penalties, leased-in buildings.
- **D8** Remove the ARESTA data file and import command, delete the 430 ARESTA accounts in the dev database (PMC 2), and seed the §3 product template instead.
- **B4** Termination penalty = 2 × `Lease.rent` (monthly), via the Charges master (`MONTHS_OF_RENT`, months = 2). An authorized user may waive it partly or fully. The waiver is audited (calculated, waived and final amount, who, when, reason) and never overwrites the calculation.

Consequences for §5: R3/R4 credit the **net** month amount (no discount line). R14's base is the month's net distributable (recognized rent − expenses − charges). New rule R20: **leasing commission invoiced at lease start**, Dr `AR_TENANT` / Cr `LEASING_COMMISSION_INCOME`, `VAT_OUTPUT`.

## 9. Owner accounting as implemented (Phase 9)

- **Owner charge** (charges the company makes to a MANAGED unit's owners, confirmed 2026-09-27): chosen from the Charges master (ADMINISTRATIVE or OTHER, fixed amount; editable only if the charge is editable), entered per unit and date. Dr `RENTAL_INCOME_CLEARING` amount + VAT / Cr `OTHER_CHARGE_INCOME`, `VAT_OUTPUT` (VAT = the charge's tax code).
- **Expenses (R12/R13)** are posted when recorded: Cr `AP_VENDOR` (or the vendor's own payable account) with the vendor dimension, or the bank/cash account it was paid from. The amount is gross; input VAT is future scope (D7).
- **Settlement (R14 + R15, one journal)** per MANAGED unit and month, dated month end, available once the month has ended:
  - revenue = the unit's `RENTAL_INCOME_CLEARING` revenue-recognition lines in the month
  - net distributable = revenue − expenses − owner charges (charge VAT excluded from the fee base)
  - fee = net distributable × `Unit.commission_percent` when positive, else 0; fee VAT from the `MANAGEMENT_FEE` tax rule
  - owners' total = net distributable − charge VAT − fee − fee VAT, split by the ownership in force at month end (must total 100%; the last owner absorbs rounding). A negative total is posted as a debit to the owners' payable.
  - fee invoice number `MGMT-{pmc}-{n}`.
  - Refused while the unit has undelivered (PENDING/FAILED) accounting events, or if the unit has no commission %, no tax rule or ownership ≠ 100%.
  - Maker-checker: prepared as DRAFT, approved and posted by a different user. Approval recalculates and refuses if the figures changed since the draft. Expenses and charges cannot be dated into a settled month.
- **Payout (R16)** per owner: DRAFT, approved by a different user, limited to the owner's payable balance. Dr `OWNER_PAYABLE` (owner dimension) / Cr the chosen bank account.
- **Payouts due**: per owner, the payable balance for completed settlement periods (monthly, quarterly or half-yearly, counted from the PMC's fiscal-year start month) less payouts made.
- **Owner statement**: owner → unit → month (settlement figures, owner %, owner amount) → lease → installment → journals, plus expenses, charges and the payable movements with running balance. Owners see only their own statement.
