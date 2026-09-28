# Finance end-to-end tests

The 20 required flows from the integration brief (§56), plus split cheques (test 21), run through the real units-backend and units-finance code with real HTTP delivery of accounting events.

```bash
./important_scripts/finance_e2e/run_e2e.sh
```

What it does:
1. Creates a throwaway database `units_db_e2e`, migrates both services and starts a temporary units-finance server on `:8011`.
2. **backend: build.** Creates one company with four leases:
   - A: commercial, managed, two owners 60/40
   - B: residential, internal
   - C: residential, renewed
   - a renewal of C, on equal cheques
   - D: commercial, internal, on equal cheques

   Leases A–C use one cheque per item, which is how the migration converts existing leases (one allocation each). D runs split cheques: cheque 1 pays the commission, the deposit and part of rent 1. A returned cheque is replaced by one covering the same items. The ledger must show one line per allocation.

   It then runs the operational flows: schedule, invoices, cheques deposited/cleared/returned, collections, termination (one balance due, one refund), and renewal.
3. **finance: activate.** Activates Finance for that company and closes January 2026.
4. **backend: deliver.** Posts every accounting event to units-finance. Everything dated in the closed month must fail with the period-closed error.
5. **finance: verify.** Checks the ledger for every flow, runs owner accounting (expense, owner charge, three monthly settlements, a payout) and the journal controls (immutability, reversal and correction, closed period), then reopens January.
6. **backend: trace.** Retries the failed postings, which now post, and traces unit ↔ ledger in both directions.
7. Drops the database and prints PASS/FAIL per test. It exits non-zero on any failure, and logs go to a temp directory.

Tests 6–8 follow the approved cheque rule (maximum 2 returns): after the first return only a replacement cheque is accepted; after the second, no cheque is accepted on any path, and payment must be non-cheque.

Never point the scripts at a real database: posted journals are immutable.
