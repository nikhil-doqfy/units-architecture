"""PMD finance E2E -- UI/API vs ledger (§31). Runs on the host against :8000 / :8001, logged in
as the TEST-FIN maker (never the user's own account: one session per user).

    python3 api_pmd.py ctx.json
"""
import json
import subprocess
import sys
from decimal import Decimal as D

import requests

ctx = json.load(open(sys.argv[1]))
B, F = "http://localhost:8000", "http://localhost:8001"
PMC = ctx["pmc_id"]
RESULTS = []


def R(sec, check, ok, detail=""):
    RESULTS.append(bool(ok))
    print("R " + json.dumps({"sec": sec, "check": check, "ok": bool(ok), "detail": str(detail)[:600]}))


def ledger(sql):
    out = subprocess.run(["docker", "exec", "-i", "units_postgres", "psql", "-U", "units_user", "-d", "units_db_dev", "-Atc", sql],
                         capture_output=True, text=True, check=True).stdout.strip()
    return out


def bal(code, where=""):
    return D(ledger(f"SELECT COALESCE(SUM(l.debit - l.credit), 0) FROM ledger_ledgerline l JOIN ledger_account a ON a.id = l.account_id "
                    f"JOIN ledger_journalentry j ON j.id = l.journal_entry_id JOIN ledger_financepmcprofile p ON p.id = j.finance_pmc_profile_id "
                    f"WHERE p.pmc_id = {PMC} AND j.status = 'POSTED' AND a.account_code = '{code}' {where}"))


login = requests.post(f"{B}/auth/login", json={"email": "testfin.maker@units.local", "password": "TestFin#2026", "user_role": "COMPANY_USER"})
token = (login.json().get("content") or {}).get("access_token")
R(31, "API login as the TEST-FIN maker (property manager)", login.status_code == 200 and token, login.text[:200])
H = {"Authorization": f"Bearer {token}"}
get = lambda base, path, **params: requests.get(f"{base}{path}", params={"pmc_id": PMC, **params}, headers=H)

# Lease Accounts screen -> installments
r = get(B, "/api/lease/installments", lease_id=ctx["L2"]).json()["content"]
rent = [i for i in r if i["installment_type"] == "RENT"]
R(31, "Lease Accounts (L2): API shows 4 x original 30,000 / discount 3,000 / net 27,000 -- same as the invoice journals",
  [(D(str(i["original_amount"])), D(str(i["discount_amount"])), D(str(i["net_amount"]))) for i in rent] == [(D("30000"), D("3000"), D("27000"))] * 4)
api_out = sum(D(str(i["outstanding"])) for i in r if (i.get("invoice") or {}).get("status") == "ISSUED")
R(31, "Lease Accounts (L2): API outstanding of invoiced items = ledger AR for the lease (2,825); rent 4 (27,000, not yet invoiced) is schedule, not AR",
  api_out == bal("1210", f"AND l.lease_id = {ctx['L2']}") == D("2825"), (api_out, bal("1210", f"AND l.lease_id = {ctx['L2']}")))

# Invoices screen
inv = get(B, "/api/finance/invoices", status="ALL", page_size=500).json()["content"]
issued_total = sum(D(str(i["total"])) for i in inv["invoices"] if i["status"] == "ISSUED")
led_inv = D(ledger(f"SELECT COALESCE(SUM(l.debit), 0) FROM ledger_ledgerline l JOIN ledger_journalentry j ON j.id = l.journal_entry_id "
                   f"JOIN ledger_account a ON a.id = l.account_id JOIN ledger_financepmcprofile p ON p.id = j.finance_pmc_profile_id "
                   f"WHERE p.pmc_id = {PMC} AND j.status = 'POSTED' AND j.source_type = 'INVOICE' AND a.account_code = '1210'"))
R(31, "Invoices screen: issued invoice totals = AR debits of the invoice journals", issued_total == led_inv, (issued_total, led_inv, inv["totals"]))

# Collections + PDC reports
col = get(B, "/api/reports/collections", date_from="2025-01-01", date_to="2026-09-29").json()["content"]
R(31, "Collections report: total collected = ledger debits of cleared cheques + collections to cash / bank / settlement pending",
  D(str(col["totals"]["total_collected"])) == D(ledger(
      f"SELECT SUM(l.debit) FROM ledger_ledgerline l JOIN ledger_journalentry j ON j.id = l.journal_entry_id JOIN ledger_account a ON a.id = l.account_id "
      f"JOIN ledger_financepmcprofile p ON p.id = j.finance_pmc_profile_id WHERE p.pmc_id = {PMC} AND j.status = 'POSTED' AND "
      f"((j.source_type = 'CHEQUE' AND j.memo LIKE 'Cheque cleared%%' AND a.account_code = '1120') OR (j.source_type = 'COLLECTION' AND a.account_code IN ('1110','1120','1140')))")),
  {m["code"]: m["total"] for m in col["by_method"]})
pdc = get(B, "/api/reports/pdc-status", as_of="2026-09-29").json()["content"]
R(31, "PDC status: nothing in clearing (matches Cheques in Bank = 0)", pdc["by_status"].get("DEPOSITED", {"count": 0})["count"] == 0 and bal("1130") == 0,
  pdc["by_status"])
fail = requests.get(f"{B}/api/accounting-events", params={"pmc_id": PMC, "status": "FAILED"}, headers=H).json()
R(31, "Failed postings screen: none for PMD", len(fail.get("content") or []) == 0, fail.get("content"))

# Trial Balance screen
tb = get(F, "/reports/trial-balance", start_date="2025-01-01", end_date="2026-09-29").json()["content"]
dr = sum(D(str(a["total_debit"])) for a in tb["accounts"])
cr = sum(D(str(a["total_credit"])) for a in tb["accounts"])
R(31, "Trial Balance screen: debit = credit = 2,949,915.74, same as the ledger", tb["balanced"] and dr == cr == D("2949915.74"), (dr, cr))

# General Ledger screen
gl = get(F, "/reports/general-ledger", date_from="2026-01-01", date_to="2026-09-29", lines=0).json()["content"]
diffs = [(a["account_code"], a["closing_balance"], str(bal(a["account_code"]))) for a in gl["accounts"]
         if D(str(a["closing_balance"])) != bal(a["account_code"])]
R(31, "General Ledger screen: closing balance of every account = ledger", not diffs, diffs)

# Consolidated (Oracle) screen + export
cons = get(F, "/reports/consolidated", date_from="2025-01-01", date_to="2026-09-29").json()["content"]
R(31, "Consolidated Accounting screen: totals debit = credit = Trial Balance",
  D(str(cons["totals"]["total_debit"])) == dr and D(str(cons["totals"]["total_credit"])) == cr and cons["reconciliation"]["report_balanced"],
  cons["totals"])
pending = [g for g in cons["groups"] if not g["mapped"]]
R(26, "Consolidated screen: unmapped accounts shown as a pending group (no code)",
  len(pending) == 1 and pending[0]["external_code"] is None, sorted(a["account_code"] for a in pending[0]["accounts"]) if pending else None)
xl = get(F, "/reports/consolidated/export", date_from="2025-01-01", date_to="2026-09-29")
R(26, "Excel export endpoint returns an .xlsx", xl.status_code == 200 and xl.content[:2] == b"PK" and "xlsx" in xl.headers.get("Content-Disposition", ""),
  (xl.status_code, xl.headers.get("Content-Disposition")))
open("consolidated_pmd.xlsx", "wb").write(xl.content)

# Owner screens
o1, o2, o3 = ctx["owners"]
st = get(F, "/owners/statement", owner_id=o1, **{"from": "2026-01", "to": "2026-09"}).json()["content"]
months = st["units"][0]["months"]
R(31, "Owner statement (owner 1): 8 months settled, closing 15,608.75 = ledger owner payable",
  len(months) == 8 and D(str(st["closing_balance"])) == -bal("2320", f"AND l.owner_id = {o1}") == D("15608.75"), (len(months), st["closing_balance"]))
aug = next(m for m in months if str(m["month"]).startswith("2026-08"))
R(31, "Owner statement Aug: net 7,500, fee 375, VAT 18.75, owner amount 7,081.25 with the expense and charge drill-down",
  (D(str(aug["net_distributable"])), D(str(aug["fee"])), D(str(aug["fee_vat"])), D(str(aug["owner_amount"]))) == (D("7500"), D("375"), D("18.75"), D("7081.25"))
  and aug["expense_items"] and aug["charge_items"] and aug["leases"], {k: aug[k] for k in ("net_distributable", "fee", "fee_vat", "owner_amount")})
due = {r_["owner_id"]: D(str(r_["due"])) for r_ in get(F, "/owners/payouts-due", as_of="2026-09-29").json()["content"]}
R(20, "Payouts due screen: owner 1 0 (Q2 paid, Q3 not ended), owner 2 0, owner 3 70,015", (due.get(o1), due.get(o2), due.get(o3)) == (0, 0, D("70015")), due)
sets = get(F, "/owners/settlements", unit_id=ctx["units"][2]).json()["content"]
R(31, "Owner settlements screen (U3): Jul fee 4,250 / VAT 212.50, Aug fee 5,000 / VAT 250, both POSTED",
  sorted((s["month"][:7], D(str(s["fee"])), D(str(s["fee_vat"])), s["status"]) for s in sets)
  == [("2026-07", D("4250"), D("212.50"), "POSTED"), ("2026-08", D("5000"), D("250"), "POSTED")], sets[:1])

# Statements
ts = get(F, "/reports/tenant-statement", tenant_id=ctx["tenants"][0], date_from="2025-01-01", date_to="2026-09-29").json()["content"]
R(31, "Tenant statement (tenant 1, both leases): closing 5,841.63 = ledger AR", D(str(ts["closing_balance"])) == D("5841.63"), ts["closing_balance"])
us = get(F, "/reports/unit-statement", unit_id=ctx["units"][2], date_from="2026-01-01", date_to="2026-09-29", lines=0).json()["content"]["summary"]
R(31, "Unit statement (U3): management fee 9,250, owners distributed 175,037.50, unearned rent 100,000",
  (D(str(us["management_fee"])), D(str(us["distributed_to_owners"])), D(str(us["unearned_rent"]))) == (D("9250"), D("175037.50"), D("100000")), us)
prep = get(F, "/prepaid-schedules/report", as_of="2026-09-29").json()["content"]["totals"]
R(31, "Prepaid report screen: remaining 9,000 = Prepaid Expenses ledger", D(str(prep["remaining"])) == bal("1300") == D("9000") and prep["reconciled"], prep)
bs = get(F, "/reports/balance-sheet", as_of_date="2026-09-29").json()["content"]
ar_bs = next((a["balance"] for a in bs["asset_accounts"] if a["name"] == "Tenant Receivables"), None)
R(31, "Balance Sheet screen: Tenant Receivables = ledger 8,666.63", D(str(ar_bs)) == D("8666.63"), f"screen shows {ar_bs}")
coa = get(F, "/accounts").json()["content"]
rows = coa if isinstance(coa, list) else coa.get("accounts", [])
bank_row = next((a for a in rows if a.get("name", "").startswith("Bank")), {})
R(31, "Chart of Accounts screen: Bank balance = ledger 439,975.54", D(str(bank_row.get("balance", "0"))) == D("439975.54"),
  f"screen shows {bank_row.get('balance')}")
jr = get(F, "/journals", source_type="INVOICE", source_id=ctx["L1_rent1_invoice"]).json()["content"]
jd = requests.get(f"{F}/journals/{jr[0]['id']}", params={"pmc_id": PMC}, headers=H).json()["content"]
R(31, "Journal drill-down (L1 rent 1): Dr 1210 5,250 / Cr 2200 5,000 / Cr 2410 250",
  sorted((l["account_code"], D(str(l["debit"])), D(str(l["credit"]))) for l in jd["lines"])
  == [("1210", D("5250"), D("0")), ("2200", D("0"), D("5000")), ("2410", D("0"), D("250"))])

# §30 re-delivery over HTTP (the backend -> finance path)
token_int = subprocess.run(["docker", "exec", "units_backend", "sh", "-c", "echo $FINANCE_INTERNAL_TOKEN"], capture_output=True, text=True).stdout.strip()
ev = ledger(f"SELECT id FROM billing_financeevent WHERE pmc_id = {PMC} AND event_type = 'CHEQUE_CLEARED' ORDER BY id LIMIT 1")
count0 = ledger(f"SELECT COUNT(*) FROM ledger_journalentry j JOIN ledger_financepmcprofile p ON p.id = j.finance_pmc_profile_id WHERE p.pmc_id = {PMC}")
resp = requests.post(f"{F}/internal/events/{ev}/process", headers={"X-Internal-Token": token_int})
count1 = ledger(f"SELECT COUNT(*) FROM ledger_journalentry j JOIN ledger_financepmcprofile p ON p.id = j.finance_pmc_profile_id WHERE p.pmc_id = {PMC}")
R(30, "cheque-clearing event re-sent over HTTP: 200, same journal, no new journal", resp.status_code == 200 and count0 == count1,
  (resp.status_code, resp.json().get("content"), count0, count1))
print(f"SUMMARY {sum(RESULTS)}/{len(RESULTS)} passed")
