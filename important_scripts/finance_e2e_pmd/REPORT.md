# Finance & Accounting end-to-end test — PMD (PMC 3)

Run on 29 Sep 2026 against the local dev database (`units_db_dev`), through the real service code of units-backend and units-finance, with real accounting-event delivery. Every check compares amounts to the cent. Expected values were worked out by hand from the business inputs and the approved rules, not read back from the system.

## Summary

| | Count |
|---|---:|
| Total checks | 263 |
| Passed | 255 |
| Failed | 8 (5 defects) |
| Blocked | 0 |

| Stage | Checks | Failed |
|---|---:|---:|
| Backend operations (schedule, cheques, collections, terminations, renewal) | 100 | 0 |
| Finance ledger, owner accounting, prepaid, banking, reports, integrity | 139 | 5 |
| Screen APIs vs ledger | 23 | 2 (the same defect as in the finance stage) |
| Follow-up ledger query (Deferred Rental Income at month end) | 1 | 1 |

**No accounting logic was changed.** One fix was prepared for the Balance Sheet window but not applied (see F1).

## Accounting summary

| | AED |
|---|---:|
| Total debit (Trial Balance, 1 Jan 2025 – 29 Sep 2026) | 2,949,915.74 |
| Total credit | 2,949,915.74 |
| Difference | 0.00 |
| Posted journals / ledger lines | 230 / 576 |
| Accounting events delivered | 177, all POSTED, none failed |

## Test data (PMD)

Everything is named `TEST-FIN…`. PMD had no property and no units, and Finance wasn't activated, so both were set up first (profile 4, product chart of accounts seed, January fiscal year).

| Unit | Type | Lease | Used for |
|---|---|---|---|
| TEST-FIN-001 | INTERNAL, commercial (5% VAT) | L1 (LS00028): Sep 2025 – Aug 2026, 5,000/month, deposit 5,000, commission 2,000. Renewed as L1R (LS00029), 5,500/month | Test case A, full-period recognition, renewal, termination of a renewed lease |
| TEST-FIN-002 | MANAGED, residential; owner 1 100% (quarterly) | L2: 2026, 120,000 − 12,000 discount, 4 quarterly installments | Discount, quarterly recognition, owner expense, owner charge, settlement, payout |
| TEST-FIN-003 | MANAGED, commercial; owners 2 / 3 at 60 / 40 (monthly) | L3: from Jul 2026, 100,000/month | The §19 management-fee example to the cent; one cheque → one installment |
| TEST-FIN-004 | INTERNAL, residential | L4: from Mar 2026, 72,000 − 7,200 discount, deposit 10,000 | Cheque return chain; card, gateway, cash and transfer collections; termination with refund |
| TEST-FIN-005 | INTERNAL, residential | L5: 2026, 9,750/month, deposit 2,000, agreement charge 1,000 | Cheque-allocation scenarios, multi-item return and replacement; termination with balance due paid by cheque |

Other test data:
- **Masters:** vendor "TEST-FIN ABC Vendor", bank account "TEST-FIN Operating" (2 clearing days), and 9 PMD charges: return, card 2%, gateway 10, penalty 2 months, admin, other, breakage, owner service, agreement.
- **Users:** maker and checker users `testfin.maker@units.local` and `testfin.checker@units.local`, with password `TestFin#2026`.

## Results by area

| § | Area | Result |
|---|---|---|
| 3 | Chart of Accounts | PASS. All 27 required accounts exist, are postable and mapped to a role. There is no separate "contract-breaking" account: termination and contract-breaking penalties both post to 4330 Penalty Income. Parking Rental Income 4110 exists but nothing posts to it. |
| 4 | Schedule (test case A) | PASS. 12 installments of 5,000 + 250 VAT, with the net amount split evenly. 12 equal cheques of 5,841.67 (the last one 5,841.63). Every allocation matches an independent pay-order model. Invoice date = due date = date of the first cheque paying the item on all 5 leases. |
| 5 | Invoice journals | PASS. All 50 issued invoices were checked line by line: Dr AR total / Cr Deferred (or income) net / Cr VAT, dated on the invoice date. Example: Dr 1210 5,250 / Cr 2200 5,000 / Cr 2410 250. Residential rent has no VAT line. |
| 6 | Security deposit | PASS. Posted as Dr Cheques in Bank / Cr Security Deposit Held, then Dr Bank at clearing. It never reaches income or Deferred, and every line carries unit, lease and tenant. |
| 7 | Leasing commission | PASS. One invoice: Dr AR 2,100 / Cr 4250 2,000 / Cr VAT 100. Charged once per initial lease. |
| 8 | Cheque allocation | PASS. All four scenarios work: one cheque → one item (L3, 105,000); one cheque → charge 1,000 + deposit 2,000 + rent 7,000; rent 1 paid by 7,000 + 2,750; one cheque → two rents. No cheque is allocated above its face value. |
| 9–10 | Deposit and clearing | PASS for all 44 banked cheques. Deposit: Dr 1130 face value / Cr AR or Deposit Held, one line per allocation. Clearing: Dr Bank / Cr 1130. Cheques in Bank ends at 0. Double clearing is refused. |
| 11–12 | Return and replacement | PASS. The whole cheque is returned; all items go back to unpaid with allocations kept; the return charge (200 + 10 VAT) is a separate debit note. The replacement covers the same items, with no duplicate receivable. On the chain, return #2 blocks a 3rd cheque on every path (see note N1). |
| 13–14 | Recognition | PASS. L1: 12 × 5,000 at month end, Rental Income 60,000, Deferred 0. The quarterly 27,000 is recognised as 9,000 × 3, with no day proration. |
| 15 | Discount | PASS. 120,000 − 12,000 = 108,000, kept on every installment and invoice. Revenue is recognised on the net amount, INTERNAL and MANAGED alike. |
| 16–19 | Owner accounting | PASS. The expense lowers net distributable by exactly 1,000. Owner charge: Dr Clearing 525 / Cr 4340 500 / VAT 25. U3 July: 100,000 − 10,000 − 5,000 = 85,000; fee 4,250; VAT 212.50; owners 48,172.50 / 32,115. Maker-checker is enforced; Clearing ends at 0. |
| 20 | Owner payout | PASS. The quarterly owner is due 51,165 (Q2) while Jul–Aug stay owed; payout posts Dr 2320 / Cr Bank. A monthly owner was paid 105,022.50. A payout above the owner's balance is refused. |
| 21 | Other payment methods | PASS. Cash → 1110; transfer → Bank; card and gateway → 1140 with channel charge and VAT. Provider settlements clear 1140 to 0, with the provider fee to 5200. |
| 22 | Termination | PASS. The date rules hold (future date refused, previous month refused), and a cheque in clearing blocks termination. Only the terminated lease's items are included, not the previous lease's unpaid rent 12. Penalty = 2 × rent, and the waiver is audited. Outcomes: L1R due 8,247.50 (paid by transfer); L4 refund 7,318.31 as one payment, approved by a second user; L5 due 15,662.50 (paid by cheque). |
| 23 | Renewal | PASS. No new deposit; the 5,000 moves to the new lease. Renewal commission = the initial 2,000 + VAT, posted to 4260. |
| 24 | Prepaid | PASS. AMC booking Dr 1300 12,000 / Cr Bank; monthly Dr 5100 1,000 / Cr 1300. Covered: monthly, quarterly (with VAT input 500), yearly, rounding 333.33 / 333.33 / 333.34, completion, cancellation with refund 3,500, and a closed-period line that is held and posts after reopening. A second job run posts nothing. |
| 25 | Bank reconciliation | 2 FAIL (F3, F4). Import, suggestions, confirm/reject, manual match and the unmatched list all work. Reconciliation created no journal. |
| 26 | Consolidated / Oracle | PASS. Debit, credit and journal count agree with the ledger for every external code. 2410 + 2420 roll up into 202000102; 2320 + 2500 into 202050101. Unmapped accounts show as "Mapping pending", and no code was invented. The Excel export has all 4 sheets and its totals and detail rows agree. |
| 27 | Trial Balance / General Ledger | PASS (2 FAIL, F1). Trial Balance debit = credit; in the General Ledger, opening + debits − credits = closing for every account. |
| 28 | Account reconciliation | PASS for all 29 accounts (table below). |
| 29 | Integrity | PASS (1 FAIL, F5). No unbalanced or orphan journals, no duplicate postings, no future-dated journals, nothing posted to header accounts. Posted journals refuse save, update and delete; corrections go through reversal. |
| 30 | Idempotency | PASS (1 FAIL, F2). Re-running invoices, recognition, prepaid, settlement approval and cheque clearing, or re-sending 7 event types (also over HTTP), creates no new journal. |
| 31 | UI/API vs ledger | PASS (2 FAIL, F1). Lease Accounts, Invoices, Collections, PDC status, Trial Balance, General Ledger, Consolidated, Owner statement, Payouts due, Settlements, Tenant and Unit statements, Prepaid report and journal drill-down all match the ledger. |

## Account-by-account reconciliation (debit + / credit −)

| Account | Expected | Actual | Difference | Status |
|---|---:|---:|---:|---|
| 1210 Tenant Receivables | 8,666.63 | 8,666.63 | 0.00 | PASS |
| 1120 Bank – Operating | 439,975.54 | 439,975.54 | 0.00 | PASS |
| 1110 Cash in Hand | 420.00 | 420.00 | 0.00 | PASS |
| 1130 Cheques in Bank | 0.00 | 0.00 | 0.00 | PASS |
| 1140 Card / Gateway Settlement Pending | 0.00 | 0.00 | 0.00 | PASS |
| 1220 VAT Input | 500.00 | 500.00 | 0.00 | PASS |
| 1300 Prepaid Expenses | 9,000.00 | 9,000.00 | 0.00 | PASS |
| 2110 Security Deposits Held | −5,000.00 | −5,000.00 | 0.00 | PASS |
| 2120 Tenant Refunds Payable | 0.00 | 0.00 | 0.00 | PASS |
| 2200 Deferred Rental Income | −109,000.00 | −109,000.00 | 0.00 | PASS |
| 2310 Rental Income Clearing | 0.00 | 0.00 | 0.00 | PASS |
| 2320 Owner Payables | −85,623.75 | −85,623.75 | 0.00 | PASS |
| 2410 VAT Output – Rent & Charges | −20,672.38 | −20,672.38 | 0.00 | PASS |
| 2420 VAT Output – Management Fees | −638.75 | −638.75 | 0.00 | PASS |
| 2500 Vendor Payables | −10,000.00 | −10,000.00 | 0.00 | PASS |
| 4100 Rental Income | −183,458.33 | −183,458.33 | 0.00 | PASS |
| 4200 Management Fee Income | −12,775.00 | −12,775.00 | 0.00 | PASS |
| 4250 Leasing Commission Income | −8,000.00 | −8,000.00 | 0.00 | PASS |
| 4260 Renewal Commission Income | −2,000.00 | −2,000.00 | 0.00 | PASS |
| 4310 Cheque Return Charges | −600.00 | −600.00 | 0.00 | PASS |
| 4320 Payment Channel Charges | −14.20 | −14.20 | 0.00 | PASS |
| 4330 Penalty Income | −29,500.00 | −29,500.00 | 0.00 | PASS |
| 4340 Other Charges Income | −9,750.00 | −9,750.00 | 0.00 | PASS |
| 5100 Property Operating Expenses | 18,300.00 | 18,300.00 | 0.00 | PASS |
| 5200 Bank & Gateway Fees | 170.24 | 170.24 | 0.00 | PASS |
| 4110 Parking / 4190 Discount / 3100 Capital / 5300 Payroll | 0.00 | 0.00 | 0.00 | PASS |

What the balances are made of:
- **Tenant Receivables:** tenant 1 5,841.63 (L1 cheque 12, still in hand) + tenant 2 2,825 (L2 rent 3, on the October cheque).
- **Owner Payables:** owner 1 15,608.75 (Jul + Aug, paid next quarter) + owner 3 70,015.
- **Vendor Payables:** the U3 repair, not paid yet.
- **Deferred Rental Income:** L2 September 9,000 + L3 September 100,000.

## Failed tests

**F1: The Balance Sheet and Chart of Accounts leave out everything dated before Finance was activated.**
- **Expected:** Tenant Receivables 8,666.63 and Bank 439,975.54.
- **Actual:** the Balance Sheet shows Tenant Receivables −8,247.50; the Chart of Accounts shows Bank −137,214.74. Both the API and the screen are affected.
- **Journals:** every journal dated before 29 Sep 2026 is left out, for example JE0000005 (L1 rent 1 invoice, 1 Oct 2025).
- **Root cause:** [reports.py:173](../../microservices/units-finance/ledger/reports.py#L173) (`compute_balance_sheet`) and [reports.py:376](../../microservices/units-finance/ledger/reports.py#L376) (`compute_chart_of_accounts`) start their window at `profile.created.date()`. Any PMC that activates Finance with past leases or migrated history gets wrong statements. The Balance Sheet still reports itself as balanced, because both sides are cut off the same way.
- **Recommended fix:** start the window at the earlier of the profile's creation date and the first journal date. A 16-line patch is ready at `finance_report_window_fix.patch` (this folder). It isn't applied, because edits to the shared finance tree were blocked; it needs your go-ahead.

**F2: The same provider batch can be recorded twice.**
- **Expected:** the second record of `TFIN-PG-BATCH-1` (1,243.81) is refused.
- **Actual:** settlement 20 was posted as journal JE0000400, and Settlement Pending went to −1,243.81. I corrected it with reversal JE0000401, so the balance is back to 0.
- **Root cause:** [provider_settlements.py:33](../../microservices/units-finance/ledger/provider_settlements.py#L33) uses the new row's id as its idempotency key, so a double submit always posts again.
- **Recommended fix:** make reference + kind + date unique per PMC (or per bank account), or ask for confirmation when the reference already exists. **This needs a business decision.**

**F3: Bank suggestions include reversed journals.**
- **Expected:** a reversed journal is not offered as a match.
- **Actual:** JE0000400 (the reversed duplicate) was suggested for the 1,218.93 statement line.
- **Root cause:** `_unreconciled_bank_journal_entries` ([reconciliation.py:175](../../microservices/units-finance/ledger/reconciliation.py#L175)) doesn't exclude reversed journals or reversals.
- **Recommended fix:** exclude both. This is included in the same patch.

**F4: Re-importing a bank statement duplicates its lines.**
- **Expected:** the second import of the same file is detected.
- **Actual:** there are 2 identical "TFIN duplicate-import test" lines.
- **Root cause:** `import_bank_statement_csv` ([reconciliation.py:63](../../microservices/units-finance/ledger/reconciliation.py#L63)) has no duplicate check.
- **Recommended fix:** refuse (or warn on) lines identical in date + amount + reference, or keep a hash per imported file. **This needs a business decision**, because two genuinely identical lines can exist.

**F5: Deferred Rental Income goes into debit at 8 month ends.**
- **Expected:** a credit or zero balance at every month end.
- **Actual:**
  - L1 has a debit of 5,000 at the Sep, Oct and Nov 2025 month ends.
  - L4 has a debit of 5,400 at the Mar–Jul 2026 month ends.
  - Example: rent 1 is recognised by JE0000039 (dated 30 Sep 2025) but invoiced by JE0000005 (dated 1 Oct 2025).
  - The final balances are correct.
- **Root cause:** the approved rules interact:
  - A rent's invoice date is the date of the first cheque that pays it.
  - In the equal-cheque layout, the first cheques pay the commission and the deposit first.
  - So rent 1 is invoiced a month or more after its period, while recognition starts in the period's own month.
  - For those month ends the ledger shows earned rent with no receivable: an accrued amount sitting as a negative liability.
- **Recommended fix: needs a business decision.** Options:
  - (a) post earned-but-not-invoiced rent to an accrued-income asset;
  - (b) date the rent invoice at the start of its period, while the cheque stays as scheduled;
  - (c) recognise a month only once its invoice has been issued.

## Notes (not failures)

- **N1: return limit.**
  - Your §11 says "#2 replacement allowed, #3 blocked".
  - The rule you approved on 28 Sep (limit 2) is "#1 → replacement, #2 → no more cheques".
  - I tested the approved rule. Setting `max_cheque_returns = 3` would give the §11 behaviour.
- **N2: manual bank matching doesn't check the amount.** A 15,660.00 statement line was confirmed against a 15,662.50 journal with no warning.
- **N3: deposit posting route.** The deposit is booked Dr Cheques in Bank / Cr Deposit Held on deposit, then Dr Bank at clearing. The end effect is §6's Dr Bank / Cr Security Deposits Held.

## Missing functionality

- **Vendor payment:** there is no function to pay a vendor bill; I paid the owner-expense vendor through an approved adjustment journal. Only owed prepaid schedules have "Record payment".
- **Parking Rental Income (4110):** the account exists, but no flow posts to it.
- **Discount Allowed (4190):** reserved; nothing posts to it, by design (D3).
- **Input VAT on unit and owner expenses:** out of scope for now, by design (D7); only prepaid schedules book VAT input.
- **Browser check of the screens:** I didn't click through the Angular screens. I checked the exact endpoints each screen calls, as the TEST-FIN maker.

## Data integrity

| Check | Result |
|---|---|
| Duplicate postings | None from business events. One duplicate provider settlement (F2), posted by the test and reversed |
| Unbalanced journals | None |
| Incorrect account mappings | None found; all 50 invoices and 44 cheques post to the expected accounts |
| Incorrect VAT | None; rent 5% commercial, 0% residential; commission, fee, charges and penalty all 5% |
| Incorrect owner settlement | None |
| Incorrect receivable / bank / prepaid balances | Ledger balances correct. The Balance Sheet and Chart of Accounts screens are wrong (F1) |
| Incorrect intermediate balances | Deferred Rental Income in debit at 8 month ends (F5) |
| Incorrect Oracle consolidation | None |

## Re-running

The data is permanent, because posted journals are immutable. Don't run the scripts against PMD again.
- The scripts are in this folder: `backend_pmd.py` (backend), `finance_pmd.py` (finance) and `api_pmd.py` (APIs), with `DRY_RUN=1` to roll back.
- The ids are in `ctx.json`, and the full check logs are the `*_run.log` files.
- The Excel export is in `consolidated_pmd.xlsx`.
