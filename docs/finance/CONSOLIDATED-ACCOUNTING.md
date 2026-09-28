# Consolidated Accounting (external codes)

The Consolidated Accounting report is at Finance → Reports → **Consolidated Accounting**. It shows the product ledger consolidated by each account's **external code**, for example the code of a group ERP such as ARESTA/PMD's Oracle chart. Finance can then reconcile and export in that structure.

It is a reporting layer only:
- The product Chart of Accounts stays the source of truth. No external chart is imported, and there are no per-building, per-landlord or per-supplier accounts. Detail comes from the ledger dimensions (property, unit, lease, owner, tenant, vendor).
- Posting rules never read the external code. A code change moves report rows; it does not touch any posted journal.

```
Lease → posting rules → product account → journal → ledger → external code (Account.external_code) → report / export
```

## Data

| Item | Where | Notes |
|---|---|---|
| `Account.external_code` | units-finance `ledger.Account` | Optional, up to 30 letters/digits/dots/dashes. Blank means mapping pending. Many accounts may share one code. |
| `AccountExternalCodeChange` | units-finance | Audit trail: previous code, new code, reason, user, time. Seeded codes are recorded as `system`. |
| Template codes | `ledger/seed.py` `TEMPLATE_EXTERNAL_CODES` | Set when an account is seeded. Migration 0015 applies them to existing companies, but only to template accounts that are unchanged and still unmapped. |

### Initial mapping (confirmed)

| Code | Account | External code |
|---|---|---|
| 1110 | Cash in Hand | 102050102 |
| 1210 | Tenant Receivables | 102030101 |
| 1220 | VAT Input Recoverable | 102000102 |
| 2110 | Security Deposits Held | 202050302 |
| 2200 | Deferred Rental Income | 202050338 |
| 2320 | Owner Payables | 202050101 |
| 2500 | Vendor Payables | 202050101 |
| 2410 | VAT Output – Rent & Charges | 202000102 |
| 2420 | VAT Output – Management Fees | 202000102 |
| 4100 | Rental Income – Company Units | 401030400 |
| 4110 | Parking Rental Income *(new)* | 401030500 |
| 4200 | Management Fee Income | 403020004 |
| 4250 | Leasing Commission Income (new leases) | 403020005 |
| 4260 | Renewal Commission Income *(new)* | 401030300 |
| 4310 | Cheque Return Charges | 403040000 |
| 4330 | Penalty Income (termination / contract breaking) | 401030600 |
| 4340 | Other Charges Income | 403060000 |
| 5100 | Property Operating Expenses | 501010015 |
| 5200 | Bank & Gateway Fees | 506030000 |

**Mapping pending, until Finance gives the codes (no code is invented):**
- The five accounts with no code in the reference files:
  - 1130 Cheques in Bank (Clearing)
  - 1140 Card/Gateway Settlement Pending
  - 2120 Tenant Refunds Payable
  - 4190 Discount Allowed
  - 4320 Payment Channel Charges
- 1120 Bank – Operating: each company bank account should have its own ledger account with its own bank code (102050402 / 403 / 404 / 406 / 422).
- 1300 Prepaid Expenses: the reference files split this into four codes (102040301 / 302 / 304 / 306).
- 2310 Rental Income Clearing (managed units): a liability here, but ARESTA files it under an asset code.
- 3100 Owner's Capital and 5300 Payroll.

### Posting changes that came with this report
- **Renewal commission** posts to its own role and account, `RENEWAL_COMMISSION_INCOME` (4260). units-backend flags the `INVOICE_ISSUED` event of a renewed lease's commission with `is_renewal`. It is still company income with VAT, charged like the initial commission.
- **Parking rental income** (4110) exists for manual and adjustment postings. No product flow posts parking rent separately yet.

## API (units-finance)

All report endpoints are read-only and need the Finance Reports *view* right; they are for company users only. Every endpoint takes the same filters:
- `pmc_id`
- `date_from` / `date_to`, or `period_id` (an accounting period; it sets both dates)
- `property_id`, `unit_id`, `lease_id`, `owner_id`, `tenant_id`, `vendor_id`
- `account_id` (a comma-separated list)
- `external_code` (matches codes that start with it)
- `mapping_status`: `MAPPED` or `UNMAPPED`
- `transaction_type`

| Endpoint | Returns |
|---|---|
| `GET reports/consolidated` | Per external code (mapped codes first, then pending): its accounts with opening balance, debit, credit, closing balance and journal count; totals; reconciliation. `page` / `page_size` page over the codes. |
| `GET reports/consolidated/breakdown?group_by=property_id\|unit_id\|lease_id\|owner_id\|tenant_id\|vendor_id` | Drill-down rows. Lines without that dimension are grouped as "not assigned". |
| `GET reports/consolidated/lines?page=` | The ledger lines behind a figure, with journal number and id, source, reference and dimension names. |
| `GET reports/consolidated/export` | Excel file (see below). |
| `GET reports/consolidated/mapping` | The whole Chart of Accounts in hierarchy order, with status `MAPPED`, `UNMAPPED` or `GROUP` (group accounts need no code). |
| `GET reports/consolidated/transaction-types` | The transaction types. |
| `POST masters/accounts/{id}/external-code` `{external_code, reason}` | Add, change or remove the code. Needs Finance Setup *edit*; audited. |
| `GET masters/accounts/{id}/external-code-history` | The account's change history. |

**Rules**
- Only posted journals count, by accounting date (`JournalEntry.entry_date`). All aggregation happens in the database.
- Balance = debit − credit, as in the General Ledger.
- Codes that nothing posts to never appear. Accounts without a code are always shown, as "mapping pending".
- **Reconciliation** has two checks:
  - The report's own debit, credit and difference. These must balance unless a filter drops part of each journal (property, unit, account, external code or mapping status).
  - A whole-ledger check for the same period, which must always balance.

  A filtered report is never flagged as an accounting error.
- **Transaction types** filter journals: rental, management fee (owner settlements), leasing commission, charges, deposit, collection, expense, owner settlement, refund/termination, bank/provider, other. A journal can match more than one type, for example a cheque that pays a deposit.

## Excel export

The export is built from the same aggregation as the screen, in streaming mode.

| Sheet | Contents |
|---|---|
| Consolidated | Per external code: a total row, then its accounts (code, name, hierarchy path, type, opening, debit, credit, closing, journals). Grand total, difference and the whole-ledger check. |
| Transaction Details | Every line with date, journal, source, external code, account, description, property, unit, lease, owner, tenant, vendor, debit, credit and reference. It totals to the Consolidated sheet. Capped at 200,000 lines, with a note when it is cut. |
| Mapping | The Chart of Accounts hierarchy (indented) with each account's code, status and last change. |
| Report Info | Company, period, filters, and who generated it and when. |

Every data sheet has a frozen header, an autofilter and number formats.

## Screen

Finance → Reports → Consolidated Accounting has two tabs:
- **Consolidated report**
  - Filters: accounting period or dates, property, unit, owner, account, external code, mapping status, transaction type.
  - Summary figures and the reconciliation banner.
  - External code rows expand to their accounts. Clicking an account drills down: by property, then by unit, then journal lines, each linked to its journal.
- **Account mapping**: edit a code (Finance Setup *edit*) and view its history. The Chart of Accounts shows the code and has a "Set external code" action that opens this tab.
