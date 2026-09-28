# Finance bulk upload

Finance → Settings → **Bulk Upload** creates many master records from an Excel template. **Import History** lists every upload.

The flow is: select type → download template → fill → upload → validate → review (errors, duplicates, preview) → confirm → import → summary.

Nothing is written until the user confirms. On confirm the server validates every row again against current data, then creates all valid rows in one transaction.

## Which entities can be bulk-uploaded

| Entity | Where | Bulk upload | Behaviour / reason |
|---|---|---|---|
| Charges & Fees (agreement, miscellaneous, cheque return fee, card / gateway fee, termination penalty) | units-backend `charges.Charge` | **Yes (v1)** | Create; duplicates skipped. |
| Cheque Return Reasons | units-backend `billing.PDCReturnReason` | **Yes (v1)** | Create; duplicate codes skipped. |
| Vendors | units-finance `ledger.Vendor` | Next | Master data with unique `(pmc, code)`. Payable account must be a Liability account. |
| Expense Types | units-finance `ledger.ExpenseMaster` | Next | Unique `(pmc, code)`. Expense account must be an active, postable Expense account. |
| Chart of Accounts (new accounts) | units-finance `ledger.Account` | Later | Already has a JSON import (`masters/accounts/import`). An accountant-controlled, low-volume change. |
| Payment Methods | units-backend `payment.PaymentMethod` | No | `settles_to` / `is_cheque` decide where collected money is booked. Few rows. |
| Tax Rules (VAT) | units-backend `charges.TaxRule` | No | Change VAT deliberately, one rule at a time. |
| Company Bank Accounts | units-backend `payment.CompanyBankAccount` | No | Linked to ledger bank accounts. Few rows. Sensitive. |
| Account Mappings | units-finance `ledger.AccountMapping` | No | Posting configuration. Missing mappings must be confirmed, not guessed. |
| Accounting Periods | units-finance | No | Controlled close/reopen workflow. |
| Journals, adjustments, invoices, cheques, collections, receipts, settlements, payouts, refunds | both | **Never** | Created only through their workflows: posting rules, maker-checker, period locks, immutable journals. |
| Owner expenses / owner charges | units-finance | Possible later | Each row posts a journal. It would have to call `record_expense` / `record_owner_charge` per row, need Owner Accounting create, and respect period locks. Not master data. |

## How it works (units-backend `bulk_upload` app)

- `registry.py`: the `UploadType` base class and `Column` spec. Each type declares:
  - columns (kind, required, allowed values, help text, example) and sample rows
  - `validate_row`: must use the same serializer as the record's own screen
  - `duplicate_keys` / `existing_keys`
  - `create`: must use the same serializer's `save`
  - the permission (`module`, `action`)
- `workbook.py`: builds and reads the Excel files.
  - The template has four parts: an empty **Upload Data** sheet (dropdowns and number checks), **Instructions**, **Examples**, and a hidden lists sheet.
  - A very-hidden **Template Info** sheet holds the type and version, and is checked on upload.
  - Reading checks the file type, size (5 MB), row limit (1,000), template identity and version, and the columns. Cells are converted to typed values.
  - The **error report** has the original columns plus *Original Row*, *Status* and *Error / Validation Message*. It is itself a valid template: fix the rows in it and upload it again.
- `engine.py`
  - Validate: stores an `ImportBatch` (file bytes, SHA-256, each row's values and errors).
  - Confirm: locks the batch (so a double click imports once) and takes a per-PMC/type advisory lock. It then re-validates every row and creates the valid rows inside a savepoint. Any unexpected error rolls all of them back and marks the batch FAILED.
  - Batches not confirmed within 24 h expire.
  - Uploading a file identical to an already-imported one shows a warning. Its rows come out as duplicates anyway.
- The original file is stored in the database, not under `media/`, which is served without authentication.
- Audit: audit log entries for validate, confirm, cancel, and every created record.

**Permissions** (existing Roles & Permissions):
- Finance *view*: see types, history, error reports and original files.
- The type's own right (Finance Setup *create* for both v1 types): download templates, upload, confirm and cancel.
- Property managers only; batches are scoped to the user's PMCs.

## API

All endpoints are under `/api/finance/bulk-upload`:

| Method | Path | Purpose |
|---|---|---|
| GET | `types?pmc_id=` | Upload types the user can use |
| GET | `types/{key}/template?pmc_id=` | Download the template |
| POST | `validate` | Body `{pmc_id, upload_type, file_name, file: base64}`; multipart is also accepted |
| GET | `batches?pmc_id=&upload_type=&status=&page=` | Import history |
| GET | `batches/{id}` | One upload |
| POST | `batches/{id}/confirm` | Import the valid rows |
| POST | `batches/{id}/cancel` | Cancel the upload |
| GET | `batches/{id}/error-report` | Download the error report |
| GET | `batches/{id}/file` | Download the original file |

## Adding an upload type

1. Add a module under `bulk_upload/types/`: subclass `UploadType`, decorate it with `@register`, and import it in `BulkUploadConfig.ready()`.
2. Validate and create through the entity's existing serializer or service. Never write ledger entries.
3. Add `FINANCE_BULK_TYPE_<KEY>` and `FINANCE_BULK_TYPE_<KEY>_HELP` to the frontend `en.json` and `ar.json`. To add a button on the entity's screen, set `bulkUploadType` in its master config.
4. For a units-finance entity (vendors, expense types), keep the same flow: either add a proxy upload type that calls units-finance's create API per row, or port this app to units-finance (it needs openpyxl there).
