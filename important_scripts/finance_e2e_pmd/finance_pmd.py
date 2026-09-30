"""PMD (PMC 3) finance end-to-end test -- units-finance side.

    docker exec -i -e DRY_RUN=1 -e CTX="$(cat ctx.json)" units_finance python manage.py shell < finance_pmd.py

Verifies every journal the backend flows produced (exact accounts and amounts), then runs
the finance-side flows (owner accounting, payouts, prepaid schedules, provider settlements,
bank reconciliation, adjustments, periods), the reports (Trial Balance, General Ledger,
Consolidated / Oracle) and the integrity checks, and reconciles every account against an
independent expected ledger. Prints `R {...}` lines and `ACCOUNTS {...}` for the report.
DRY_RUN=1 rolls everything back.
"""
import datetime as dt
import io
import json
import os
from decimal import Decimal as D

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from ledger import owner_accounting as oa
from ledger import prepaid as pp
from ledger import statements
from ledger.accounting import (
    PeriodClosedError, WorkflowError, approve_adjustment, create_adjustment, reverse_journal, submit_adjustment,
)
from ledger.consolidation_export import build_workbook
from ledger.consolidation_report import Filters, consolidated
from ledger.models import (
    Account, AccountingPeriod, AccountMapping as M, BankStatementLine, BankStatementMatch, ExpenseMaster,
    FinanceEventRef, FinancePMCProfile, ImmutableJournalError, JournalEntry, LedgerLine, OwnerPayout, OwnerSettlement,
    PrepaidSchedule, ProviderSettlement, Vendor,
)
from ledger.posting_rules import process_event
from ledger.provider_settlements import record_provider_settlement
from ledger.reconciliation import apply_match_decision, find_suggested_matches, import_bank_statement_csv
from ledger.reports import compute_balance_sheet, compute_chart_of_accounts, compute_trial_balance

DRY_RUN = os.environ.get("DRY_RUN") == "1"
ctx = json.loads(os.environ["CTX"])
MAKER, CHECKER = ctx["maker_user_id"], ctx["checker_user_id"]
TODAY = dt.date(2026, 9, 29)
d = dt.date
RESULTS = []
FINDINGS = []


def R(sec, check, ok, detail=""):
    RESULTS.append(ok)
    print("R " + json.dumps({"sec": sec, "check": check, "ok": bool(ok), "detail": str(detail)[:700]}))


def expect_error(sec, check, exc_type, func):
    try:
        with transaction.atomic():
            func()
    except exc_type as exc:
        R(sec, check, True, f"{type(exc).__name__}: {exc}")
    else:
        R(sec, check, False, "no error raised")


p = FinancePMCProfile.objects.get(pmc_id=ctx["pmc_id"])
ACC = {a.account_code: a for a in Account.objects.filter(finance_pmc_profile=p) if a.account_code}
BANK = ACC["1120"]


def posted(**kw):
    return LedgerLine.objects.filter(journal_entry__finance_pmc_profile=p, journal_entry__status=JournalEntry.POSTED, **kw)


def bal(code, **dims):
    t = posted(account=ACC[code], **dims).aggregate(dr=Sum("debit"), cr=Sum("credit"))
    return (t["dr"] or D("0")) - (t["cr"] or D("0"))


def jset(entry):
    """{(account_code, debit, credit)} of a journal."""
    return sorted((l.account.account_code, l.debit, l.credit) for l in entry.lines.select_related("account"))


def J(source_type, source_id, memo=None):
    qs = JournalEntry.objects.filter(finance_pmc_profile=p, status=JournalEntry.POSTED, source_type=source_type,
                                     source_id=source_id, reversed_journal_entry__isnull=True)
    if memo:
        qs = qs.filter(memo__startswith=memo)
    return qs


def lines(*triples):
    return sorted((code, D(str(dr)), D(str(cr))) for code, dr, cr in triples)


def sql(query, params=()):
    with connection.cursor() as cur:
        cur.execute(query, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def run():
    # ══ §3 Chart of Accounts ══
    need = {
        "1110": ("Cash in Hand", M.CASH_IN_HAND), "1120": ("Bank", M.BANK), "1210": ("Tenant Receivables", M.AR_TENANT),
        "1300": ("Prepaid Expenses", M.PREPAID_EXPENSE), "1130": ("Cheques in Bank / Clearing", M.CHEQUES_IN_BANK),
        "1140": ("Card/Gateway Settlement Pending", M.SETTLEMENT_PENDING), "2120": ("Tenant Refunds Payable", M.TENANT_REFUND_PAYABLE),
        "2110": ("Security Deposits Held", M.SECURITY_DEPOSIT_HELD), "2200": ("Deferred Rental Income", M.DEFERRED_RENTAL_INCOME),
        "2320": ("Owner Payables", M.OWNER_PAYABLE), "2500": ("Vendor Payables", M.AP_VENDOR), "1220": ("VAT Input", M.VAT_INPUT),
        "2410": ("VAT Output", M.VAT_OUTPUT), "2420": ("VAT Output - management fees", M.MANAGEMENT_FEE_VAT),
        "4100": ("Rental Income", M.RENTAL_INCOME), "2310": ("Rental Income Clearing", M.RENTAL_INCOME_CLEARING),
        "4200": ("Management Fee Income", M.MANAGEMENT_FEE_INCOME), "4250": ("Leasing Commission Income", M.LEASING_COMMISSION_INCOME),
        "4260": ("Renewal Commission Income", M.RENEWAL_COMMISSION_INCOME), "4310": ("Cheque Return Charges", M.RETURN_CHARGE_INCOME),
        "4340": ("Other Charges", M.OTHER_CHARGE_INCOME), "4330": ("Contract-breaking / termination penalty", M.PENALTY_INCOME),
        "4110": ("Parking Rental Income", None), "5100": ("Property Operating Expenses", M.EXPENSE),
        "5200": ("Bank/Gateway Fees", M.BANK_GATEWAY_FEE_EXPENSE), "4190": ("Discount Allowed", M.DISCOUNT_ALLOWED),
        "4320": ("Payment Channel Charges", M.CHANNEL_CHARGE_INCOME),
    }
    missing = []
    for code, (label, role) in need.items():
        a = ACC.get(code)
        ok = a is not None and a.is_active and a.is_postable and (
            role is None or M.objects.filter(finance_pmc_profile=p, role=role, account=a, property_id=None).exists())
        if not ok:
            missing.append((code, label))
    R(3, f"all {len(need)} required accounts exist, active, postable and mapped to their posting role", not missing, missing)
    R(3, "no separate 'Termination Penalty' account: termination and contract-breaking penalties share 4330 Penalty Income",
      ACC["4330"].name == "Penalty Income", ACC["4330"].name)

    # ══ §5 / §7 every issued invoice -> its journal, to the cent ══
    invoices = sql("SELECT i.id, i.invoice_type, i.invoice_date, i.net_amount, i.vat_amount, i.total, i.lease_id, "
                   "ch.charge_type, l.renewed_from_id FROM billing_invoice i JOIN lease_lease l ON l.id = i.lease_id "
                   "LEFT JOIN charges_charge ch ON ch.id = i.charge_id WHERE i.pmc_id = %s AND i.status = 'ISSUED'", [p.pmc_id])
    credit_account = {"RENT": "2200", "COMMISSION": "4250"}
    other = {"CHEQUE_RETURN": "4310", "PAYMENT_CHANNEL": "4320", "TERMINATION_PENALTY": "4330"}
    bad = []
    for inv in invoices:
        acct = credit_account.get(inv["invoice_type"]) if inv["invoice_type"] != "OTHER_CHARGE" else other.get(inv["charge_type"], "4340")
        if inv["invoice_type"] == "COMMISSION" and inv["renewed_from_id"]:
            acct = "4260"
        want = lines(("1210", inv["total"], 0), (acct, 0, inv["net_amount"]), *([("2410", 0, inv["vat_amount"])] if inv["vat_amount"] else []))
        js = list(J(JournalEntry.INVOICE, inv["id"]))
        if len(js) != 1 or jset(js[0]) != want or js[0].entry_date != inv["invoice_date"]:
            bad.append((inv["id"], inv["invoice_type"], [jset(j) for j in js], want))
    R(5, f"every issued invoice ({len(invoices)}) has exactly one journal: Dr AR total / Cr deferred-or-income net / Cr VAT, dated the invoice date",
      not bad and len(invoices) == 50, bad[:3] or len(invoices))
    R(5, "L1 rent 1: Dr Tenant Receivables 5,250 / Cr Deferred Rental Income 5,000 / Cr VAT Output 250",
      jset(J(JournalEntry.INVOICE, ctx["L1_rent1_invoice"]).get()) == lines(("1210", 5250, 0), ("2200", 0, 5000), ("2410", 0, 250)))
    R(5, "L2 rent 1 (residential): Dr AR 27,000 / Cr Deferred 27,000, no VAT line",
      jset(J(JournalEntry.INVOICE, ctx["L2_rent1_invoice"]).get()) == lines(("1210", 27000, 0), ("2200", 0, 27000)))
    R(7, "L1 leasing commission: Dr AR 2,100 / Cr Leasing Commission Income 2,000 / Cr VAT 100",
      jset(J(JournalEntry.INVOICE, ctx["L1_commission_invoice"]).get()) == lines(("1210", 2100, 0), ("4250", 0, 2000), ("2410", 0, 100)))
    R(23, "renewal commission: Dr AR 2,100 / Cr Renewal Commission Income 2,000 / Cr VAT 100",
      jset(J(JournalEntry.INVOICE, ctx["L1R_commission_invoice"]).get()) == lines(("1210", 2100, 0), ("4260", 0, 2000), ("2410", 0, 100)))
    R(7, "leasing commission income only from the 2 initial leases (2,000 + 6,000), never in rental income or owner revenue",
      bal("4250") == D("-8000") and not posted(account=ACC["2310"], journal_entry__source_type=JournalEntry.INVOICE).exists(), bal("4250"))

    # ══ §9 / §10 / §11 every cheque event -> journal, per allocation ══
    cheques = sql("SELECT t.documents_ptr_id AS id, t.status, t.total, t.deposit_date, t.actual_clearing_date, t.return_count "
                  "FROM lease_leasetransaction t JOIN lease_lease l ON l.id = t.lease_id JOIN property_unit u ON u.id = l.unit_id "
                  "WHERE u.id = ANY(%s) AND t.deposit_date IS NOT NULL", [ctx["units"]])
    alloc = {}
    for row in sql("SELECT a.cheque_id, a.amount, a.installment_id, i.installment_type FROM lease_chequeallocation a "
                   "JOIN lease_leaseinstallment i ON i.id = a.installment_id WHERE a.cheque_id = ANY(%s)", [[c_["id"] for c_ in cheques]]):
        alloc.setdefault(row["cheque_id"], []).append(row)
    bad = []
    for ch in cheques:
        credits = sorted(("2110" if a["installment_type"] == "DEPOSIT" else "1210", D("0"), a["amount"]) for a in alloc[ch["id"]])
        want_dep = sorted([("1130", ch["total"], D("0"))] + credits)
        dep = list(J(JournalEntry.CHEQUE, ch["id"], "Cheque deposited"))
        if len(dep) != 1 or jset(dep[0]) != want_dep or dep[0].entry_date != ch["deposit_date"]:
            bad.append(("deposit", ch["id"], [jset(j) for j in dep], want_dep))
        if ch["status"] == "CREDITED":
            clr = list(J(JournalEntry.CHEQUE, ch["id"], "Cheque cleared"))
            if len(clr) != 1 or jset(clr[0]) != lines(("1120", ch["total"], 0), ("1130", 0, ch["total"])) or clr[0].entry_date != ch["actual_clearing_date"]:
                bad.append(("clear", ch["id"], [jset(j) for j in clr]))
        if ch["status"] == "BOUNCED":
            ret = list(J(JournalEntry.CHEQUE, ch["id"], "Cheque returned"))
            want_ret = sorted([("1130", D("0"), ch["total"])] + [(code, cr, D("0")) for code, _, cr in credits])
            if len(ret) != 1 or jset(ret[0]) != want_ret:
                bad.append(("return", ch["id"], [jset(j) for j in ret], want_ret))
    R(9, f"every deposited cheque ({len(cheques)}): Dr Cheques in Bank face value / Cr AR or Security Deposit Held per allocation, on the deposit date",
      not [b for b in bad if b[0] == "deposit"], [b for b in bad if b[0] == "deposit"][:2])
    R(10, "every cleared cheque: Dr Bank / Cr Cheques in Bank, face value, on the clearing date",
      not [b for b in bad if b[0] == "clear"], [b for b in bad if b[0] == "clear"][:2])
    R(11, "every returned cheque: Dr AR / Security Deposit Held per allocation / Cr Cheques in Bank",
      not [b for b in bad if b[0] == "return"], [b for b in bad if b[0] == "return"][:2])
    c1 = ctx["L1_cheques"][0]
    R(9, "L1 cheque 1 deposit: Dr Cheques in Bank 5,841.67 / Cr AR 2,100 (commission) / Cr Security Deposit Held 3,741.67",
      jset(J(JournalEntry.CHEQUE, c1, "Cheque deposited").get()) == lines(("1130", "5841.67", 0), ("1210", 0, 2100), ("2110", 0, "3741.67")))
    R(11, "L5 multi-item cheque returned: Dr AR 1,000 + Dr Deposit Held 2,000 + Dr AR 7,000 / Cr Cheques in Bank 10,000",
      jset(J(JournalEntry.CHEQUE, ctx["L5_c1"], "Cheque returned").get())
      == lines(("1130", 0, 10000), ("1210", 1000, 0), ("1210", 7000, 0), ("2110", 2000, 0)))
    R(12, "L5 rent 1 after return + replacement: one invoice journal, AR for the item closed (no duplicate receivable)",
      J(JournalEntry.INVOICE, None).count() == 0 and bal("1210", installment_id=ctx["L5_rent1"]) == 0
      and posted(installment_id=ctx["L5_rent1"], journal_entry__source_type=JournalEntry.INVOICE).values("journal_entry").distinct().count() == 1)
    R(10, "Cheques in Bank is zero: every deposited cheque was cleared or returned", bal("1130") == 0, bal("1130"))

    # ══ §6 security deposit ══
    dep_insts = [r["id"] for r in sql("SELECT i.id FROM lease_leaseinstallment i JOIN lease_lease l ON l.id=i.lease_id "
                                      "WHERE l.unit_id = ANY(%s) AND i.installment_type='DEPOSIT'", [ctx["units"]])]
    R(6, "deposit never touches revenue: no income or deferred-rent line carries a deposit installment",
      not posted(installment_id__in=dep_insts, account__account_type="Income").exists()
      and not posted(installment_id__in=dep_insts, account=ACC["2200"]).exists())
    R(6, "every Security Deposit Held line is traceable to unit, lease and tenant",
      not posted(account=ACC["2110"]).filter(unit_id__isnull=True).exists()
      and not posted(account=ACC["2110"], lease_id__isnull=True).exists() and not posted(account=ACC["2110"], tenant_id__isnull=True).exists())
    R(6, "deposit held: L2 5,000 (active); L1 moved to renewal then applied at termination; L4 / L5 applied at termination",
      (bal("2110", lease_id=ctx["L2"]), bal("2110", lease_id=ctx["L1"]), bal("2110", lease_id=ctx["L1R"]),
       bal("2110", lease_id=ctx["L4"]), bal("2110", lease_id=ctx["L5"])) == (D("-5000"), 0, 0, 0, 0),
      [bal("2110", lease_id=ctx[k]) for k in ("L2", "L1", "L1R", "L4", "L5")])
    tr = J(JournalEntry.RENEWAL, ctx["L1R"]).get()
    R(23, "deposit carried to the renewal: Dr Deposit Held 5,000 (old lease) / Cr Deposit Held 5,000 (renewal); no new deposit",
      sorted((l.lease_id, l.debit, l.credit) for l in tr.lines.all()) == sorted([(ctx["L1"], D("5000"), D("0")), (ctx["L1R"], D("0"), D("5000"))]))

    # ══ §13 / §14 / §15 revenue recognition ══
    R(13, "L1 full period: 12 recognitions of 5,000 -> Rental Income 60,000; Deferred for L1 = 0",
      (bal("4100", lease_id=ctx["L1"]), bal("2200", lease_id=ctx["L1"]),
       posted(lease_id=ctx["L1"], account=ACC["4100"]).count()) == (D("-60000"), 0, 12))
    month_ends = sorted(set(posted(lease_id=ctx["L1"], account=ACC["4100"]).values_list("journal_entry__entry_date", flat=True)))
    R(13, "recognition dated each month end (30 Sep 2025 ... 31 Aug 2026)",
      month_ends[0] == d(2025, 9, 30) and month_ends[-1] == d(2026, 8, 31) and len(month_ends) == 12, month_ends[:2])
    l2 = posted(lease_id=ctx["L2"], account=ACC["2310"], journal_entry__source_type=JournalEntry.REVENUE_RECOGNITION)
    R(14, "L2 quarterly 27,000: recognised 9,000 / 9,000 / 9,000 = exactly 27,000 per quarter (Jan-Aug: 72,000) to Rental Income Clearing",
      sorted(set(l2.values_list("credit", flat=True))) == [D("9000")] and l2.count() == 8 and bal("2200", lease_id=ctx["L2"]) == D("-9000"),
      (l2.count(), bal("2200", lease_id=ctx["L2"])))
    R(15, "MANAGED discount borne by the owner: revenue recognised on net 9,000 (not gross 10,000); no discount line (4190 = 0)",
      bal("4190") == 0 and not posted(account=ACC["4100"], lease_id=ctx["L2"]).exists())
    R(15, "INTERNAL discount: L4 recognised on net 5,400 per month (Mar-Aug) + 3,600 prorated Sep = 36,000",
      bal("4100", lease_id=ctx["L4"]) == D("-36000"), bal("4100", lease_id=ctx["L4"]))
    R(13, "no revenue recognised for a future month (latest recognition <= today)",
      not posted(journal_entry__source_type=JournalEntry.REVENUE_RECOGNITION, journal_entry__entry_date__gt=TODAY).exists())

    # ══ §21 collections through other methods ══
    col = lambda ref: sql("SELECT id FROM billing_collection WHERE reference = %s", [ref])[0]["id"]
    R(21, "cash: Dr Cash in Hand 210 / Cr AR 210",
      jset(J(JournalEntry.COLLECTION, col("TFIN-CASH-1")).get()) == lines(("1110", 210, 0), ("1210", 0, 210)))
    R(21, "bank transfer: Dr Bank 5,000.02 / Cr AR 5,000.02",
      jset(J(JournalEntry.COLLECTION, col("TFIN-TT-004")).get()) == lines(("1120", "5000.02", 0), ("1210", 0, "5000.02")))
    R(21, "payment gateway: Dr Settlement Pending 1,243.81 / Cr AR 1,233.31, Channel Charges 10, VAT 0.50",
      jset(J(JournalEntry.COLLECTION, col("TFIN-PG-004")).get())
      == lines(("1140", "1243.81", 0), ("1210", 0, "1233.31"), ("4320", 0, 10), ("2410", 0, "0.50")))
    R(21, "card: Dr Settlement Pending 214.41 / Cr AR 210, Channel Charges 4.20, VAT 0.21",
      jset(J(JournalEntry.COLLECTION, col("TFIN-CARD-004")).get())
      == lines(("1140", "214.41", 0), ("1210", 0, 210), ("4320", 0, "4.20"), ("2410", 0, "0.21")))
    ps_card = record_provider_settlement(p, kind=ProviderSettlement.GATEWAY_SETTLEMENT, settlement_date=d(2026, 9, 6), bank_account=BANK,
                                         gross_amount="214.41", fee_amount="5.36", reference="TFIN-CARD-BATCH-1", created_by_user_id=MAKER)
    ps_pg = record_provider_settlement(p, kind=ProviderSettlement.GATEWAY_SETTLEMENT, settlement_date=d(2026, 9, 12), bank_account=BANK,
                                       gross_amount="1243.81", fee_amount="24.88", reference="TFIN-PG-BATCH-1", created_by_user_id=MAKER)
    ps_fee = record_provider_settlement(p, kind=ProviderSettlement.BANK_CHARGE, settlement_date=d(2026, 9, 15), bank_account=BANK,
                                        fee_amount="50", reference="TFIN-BANK-CHG-SEP", created_by_user_id=MAKER)
    R(21, "provider settlement (card batch): Dr Bank 209.05 + Dr Bank/Gateway Fees 5.36 / Cr Settlement Pending 214.41",
      jset(ps_card.journal_entry) == lines(("1120", "209.05", 0), ("5200", "5.36", 0), ("1140", 0, "214.41")))
    R(21, "provider settlement (gateway): Dr Bank 1,218.93 + Fees 24.88 / Cr Settlement Pending 1,243.81",
      jset(ps_pg.journal_entry) == lines(("1120", "1218.93", 0), ("5200", "24.88", 0), ("1140", 0, "1243.81")))
    R(21, "bank charge: Dr Bank/Gateway Fees 50 / Cr Bank 50", jset(ps_fee.journal_entry) == lines(("5200", 50, 0), ("1120", 0, 50)))
    R(21, "Settlement Pending cleared to 0 after the provider settlements", bal("1140") == 0, bal("1140"))
    # §30 provider settlement idempotency: the same batch recorded twice
    before = JournalEntry.objects.filter(finance_pmc_profile=p).count()
    try:
        with transaction.atomic():
            dup = record_provider_settlement(p, kind=ProviderSettlement.GATEWAY_SETTLEMENT, settlement_date=d(2026, 9, 12), bank_account=BANK,
                                             gross_amount="1243.81", fee_amount="24.88", reference="TFIN-PG-BATCH-1", created_by_user_id=MAKER)
    except Exception as exc:  # noqa: BLE001
        R(30, "same provider batch (same reference) recorded twice: refused", True, exc)
    else:
        R(30, "same provider batch (same reference) recorded twice: refused", False,
          f"a second settlement {dup.id} / journal {dup.journal_entry.number} was posted; Settlement Pending now {bal('1140')}")
        FINDINGS.append({"test": "Provider settlement idempotency", "journal": dup.journal_entry.number, "settlement": dup.id})
        reversal, _ = reverse_journal(dup.journal_entry, reason="TEST-FIN duplicate provider settlement (idempotency test)",
                                      posted_by_user_id=CHECKER)
        R(29, "duplicate corrected by a reversal journal (not an edit); Settlement Pending back to 0",
          reversal.reversed_journal_entry_id == dup.journal_entry_id and bal("1140") == 0, reversal.number)
    del before

    # ══ §16-§19 owner accounting ══
    vendor = Vendor.objects.create(finance_pmc_profile=p, code="TFIN-ABC", name="TEST-FIN ABC Vendor")
    maint = ExpenseMaster.objects.create(finance_pmc_profile=p, code="TFIN-MAINT", name="TEST-FIN Maintenance", expense_account=ACC["5100"])
    repair = ExpenseMaster.objects.create(finance_pmc_profile=p, code="TFIN-REPAIR", name="TEST-FIN Repairs", expense_account=ACC["5100"])
    u1, u2, u3, u4, u5 = ctx["units"]
    o1, o2, o3 = ctx["owners"]
    aug, jul = d(2026, 8, 1), d(2026, 7, 1)
    base = oa.calculate_settlement(p, u2, aug, today=TODAY)
    R(16, "U2 Aug before expense/charge: revenue 9,000 -> net 9,000, fee 450 (5%), fee VAT 22.50, owners 8,527.50",
      (base["revenue"], base["net_distributable"], base["fee"], base["fee_vat"], base["owners_total"])
      == (D("9000"), D("9000"), D("450"), D("22.50"), D("8527.50")), {k: base[k] for k in ("revenue", "net_distributable", "fee", "fee_vat", "owners_total")})
    exp2 = oa.record_expense(p, unit_id=u2, expense_type=maint, amount="1000", expense_date=d(2026, 8, 15), vendor=vendor,
                             description="TEST-FIN AC maintenance", created_by_user_id=MAKER)
    R(17, "owner expense 1,000: Dr Rental Income Clearing / Cr Vendor Payables, with unit, property and vendor dimensions",
      jset(exp2.journal_entry) == lines(("2310", 1000, 0), ("2500", 0, 1000))
      and all(l.unit_id == u2 and l.property_id == ctx["property_id"] for l in exp2.journal_entry.lines.all())
      and exp2.journal_entry.lines.get(account=ACC["2500"]).vendor_id == vendor.id)
    after_exp = oa.calculate_settlement(p, u2, aug, today=TODAY)
    R(17, "net distributable decreases by exactly 1,000 (9,000 -> 8,000)",
      base["net_distributable"] - after_exp["net_distributable"] == D("1000"), after_exp["net_distributable"])
    ch2 = oa.record_owner_charge(p, unit_id=u2, charge_id=ctx["admin_charge"], charge_date=d(2026, 8, 20), created_by_user_id=MAKER)
    R(18, "owner charge 500 + VAT 25: Dr Rental Income Clearing 525 / Cr Other Charges 500 / Cr VAT 25 -- no tenant AR, no rental income",
      jset(ch2.journal_entry) == lines(("2310", 525, 0), ("4340", 0, 500), ("2410", 0, 25)))
    calc = oa.calculate_settlement(p, u2, aug, today=TODAY)
    R(18, "Aug: 9,000 - 1,000 expenses - 500 charges = 7,500 net distributable; fee 375; VAT 18.75; owners 7,081.25 (charge VAT 25 borne by owner)",
      (calc["net_distributable"], calc["fee"], calc["fee_vat"], calc["charges_vat"], calc["owners_total"])
      == (D("7500"), D("375"), D("18.75"), D("25"), D("7081.25")), {k: calc[k] for k in ("net_distributable", "fee", "fee_vat", "charges_vat", "owners_total")})
    exp3 = oa.record_expense(p, unit_id=u3, expense_type=repair, amount="10000", expense_date=d(2026, 7, 20), vendor=vendor,
                             description="TEST-FIN lobby repairs", created_by_user_id=MAKER)
    oa.record_owner_charge(p, unit_id=u3, charge_id=ctx["service_charge"], charge_date=d(2026, 7, 25), created_by_user_id=MAKER)
    draft = oa.prepare_settlement(p, u3, jul, created_by_user_id=MAKER, today=TODAY)
    expect_error(16, "settlement maker cannot approve it", WorkflowError, lambda: oa.approve_settlement(draft, MAKER, today=TODAY))
    s_jul = oa.approve_settlement(draft, CHECKER, today=TODAY)
    R(19, "U3 Jul: 100,000 - 10,000 - 5,000 = 85,000; fee 4,250.00; fee VAT 212.50; owners 80,287.50",
      (s_jul.revenue, s_jul.expenses, s_jul.charges, s_jul.net_distributable, s_jul.fee, s_jul.fee_vat, s_jul.owners_total)
      == (D("100000"), D("10000"), D("5000"), D("85000"), D("4250"), D("212.50"), D("80287.50")))
    R(19, "U3 Jul journal: Dr Clearing 84,750 / Cr Mgmt Fee Income 4,250 / Cr Mgmt Fee VAT 212.50 / Cr Owner Payable 48,172.50 (60%) + 32,115 (40%)",
      jset(s_jul.journal_entry) == lines(("2310", 84750, 0), ("4200", 0, 4250), ("2420", 0, "212.50"), ("2320", 0, "48172.50"), ("2320", 0, 32115))
      and {l.owner_id: l.credit for l in s_jul.journal_entry.lines.filter(account=ACC["2320"])} == {o2: D("48172.50"), o3: D("32115")})
    expect_error(30, "an approved settlement cannot be approved again", WorkflowError, lambda: oa.approve_settlement(s_jul, CHECKER, today=TODAY))
    expect_error(30, "a settled month cannot be prepared again", WorkflowError,
                 lambda: oa.prepare_settlement(p, u3, jul, created_by_user_id=MAKER, today=TODAY))
    expect_error(16, "an expense cannot be dated into a settled month", Exception,
                 lambda: oa.record_expense(p, unit_id=u3, expense_type=repair, amount="10", expense_date=d(2026, 7, 28), vendor=vendor,
                                           created_by_user_id=MAKER))
    s_aug3 = oa.approve_settlement(oa.prepare_settlement(p, u3, aug, created_by_user_id=MAKER, today=TODAY), CHECKER, today=TODAY)
    R(19, "U3 Aug (no expenses): fee 5,000, VAT 250, owners 94,750 (56,850 / 37,900)",
      (s_aug3.fee, s_aug3.fee_vat, s_aug3.owners_total) == (D("5000"), D("250"), D("94750"))
      and {l.owner_id: l.credit for l in s_aug3.journal_entry.lines.filter(account=ACC["2320"])} == {o2: D("56850"), o3: D("37900")})
    settled2 = []
    for m in range(1, 9):
        settled2.append(oa.approve_settlement(oa.prepare_settlement(p, u2, d(2026, m, 1), created_by_user_id=MAKER, today=TODAY),
                                              CHECKER, today=TODAY))
    R(16, "U2 Jan-Jul each: revenue 9,000, fee 450, VAT 22.50, owners 8,527.50; Aug: 7,081.25",
      [(s.fee, s.owners_total) for s in settled2] == [(D("450"), D("8527.50"))] * 7 + [(D("375"), D("7081.25"))])
    expect_error(16, "September cannot be settled before the month ends", Exception,
                 lambda: oa.prepare_settlement(p, u2, d(2026, 9, 1), created_by_user_id=MAKER, today=TODAY))
    R(16, "Rental Income Clearing nets to 0 for both managed units after settlement", (bal("2310", unit_id=u2), bal("2310", unit_id=u3)) == (0, 0),
      (bal("2310", unit_id=u2), bal("2310", unit_id=u3)))
    R(16, "management fee is company income: 4200 = 3,525 (U2) + 9,250 (U3); VAT 2420 = 176.25 + 462.50",
      (bal("4200"), bal("2420")) == (D("-12775"), D("-638.75")), (bal("4200"), bal("2420")))

    # §20 payouts: owner 1 quarterly, owners 2 / 3 monthly
    end1, due1 = oa.payout_due(p, o1, "QUARTERLY", today=TODAY)
    R(20, "owner 1 (quarterly): due for the quarter ended 30 Jun = Jan-Jun 6 x 8,527.50 = 51,165.00 (Jul/Aug settled monthly, paid next quarter)",
      (end1, due1) == (d(2026, 6, 30), D("51165")), (end1, due1))
    pay1 = oa.create_payout(p, owner_id=o1, amount=str(due1), payout_date=TODAY, bank_account=BANK, reference="TFIN-PAYOUT-O1-Q2",
                            created_by_user_id=MAKER)
    expect_error(20, "payout maker cannot approve it", WorkflowError, lambda: oa.approve_payout(pay1, MAKER))
    pay1 = oa.approve_payout(pay1, CHECKER)
    R(20, "payout: Dr Owner Payables 51,165 (owner 1) / Cr Bank 51,165; owner 1 still owed Jul + Aug 15,608.75",
      jset(pay1.journal_entry) == lines(("2320", 51165, 0), ("1120", 0, 51165))
      and pay1.journal_entry.lines.get(account=ACC["2320"]).owner_id == o1 and oa.owner_balance(p, o1) == D("15608.75"),
      oa.owner_balance(p, o1))
    end2, due2 = oa.payout_due(p, o2, "MONTHLY", today=TODAY)
    pay2 = oa.approve_payout(oa.create_payout(p, owner_id=o2, amount=str(due2), payout_date=TODAY, bank_account=BANK,
                                              reference="TFIN-PAYOUT-O2", created_by_user_id=MAKER), CHECKER)
    R(20, "owner 2 (monthly): due to 31 Aug = 48,172.50 + 56,850 = 105,022.50, paid; balance 0",
      (end2, due2, oa.owner_balance(p, o2)) == (d(2026, 8, 31), D("105022.50"), 0), (end2, due2))
    over = oa.create_payout(p, owner_id=o3, amount="80000", payout_date=TODAY, bank_account=BANK, created_by_user_id=MAKER)
    expect_error(20, "payout above the owner's payable balance (70,015) refused", Exception, lambda: oa.approve_payout(over, CHECKER))
    R(20, "owner 3 payable 70,015 (32,115 + 37,900), unpaid", oa.owner_balance(p, o3) == D("70015"), oa.owner_balance(p, o3))

    # vendor payment for the U2 expense (no vendor-payment function: an approved adjustment journal)
    vp = create_adjustment(p, [{"account_id": ACC["2500"].id, "debit": "1000", "vendor_id": vendor.id, "unit_id": u2},
                               {"account_id": BANK.id, "credit": "1000", "vendor_id": vendor.id}],
                           reason="TEST-FIN pay ABC for U2 AC maintenance", entry_date=d(2026, 9, 10), created_by_user_id=MAKER)
    submit_adjustment(vp, MAKER)
    expect_error(29, "adjustment maker cannot approve it", WorkflowError, lambda: approve_adjustment(vp, MAKER))
    approve_adjustment(vp, CHECKER)
    R(17, "vendor paid via approved adjustment: Vendor Payables = 10,000 (U3 repairs still owed)", bal("2500", vendor_id=vendor.id) == D("-10000"),
      bal("2500", vendor_id=vendor.id))

    # ══ §24 prepaid schedules ══
    amc = ExpenseMaster.objects.create(finance_pmc_profile=p, code="TFIN-AMC", name="TEST-FIN AMC", expense_account=ACC["5100"])
    ins = ExpenseMaster.objects.create(finance_pmc_profile=p, code="TFIN-INS", name="TEST-FIN Insurance", expense_account=ACC["5100"],
                                       vat_applicable=True, vat_rate=D("5"))
    lic = ExpenseMaster.objects.create(finance_pmc_profile=p, code="TFIN-LIC", name="TEST-FIN Licences", expense_account=ACC["5100"])

    def schedule(desc, et, amount, start, end, freq, mode="PAID", pay=None, **kw):
        s = pp.create_draft(p, vendor=vendor, description=desc, expense_type=et, amount=D(amount), start_date=start, end_date=end,
                            frequency=freq, payment_mode=mode, payment_date=pay or start,
                            paid_from_account=BANK if mode == "PAID" else None, created_by_user_id=MAKER, **kw)
        return pp.activate(s, user_id=MAKER, today=TODAY)

    P1 = schedule("TEST-FIN ABC annual AMC", amc, "12000", d(2026, 1, 1), d(2026, 12, 31), PrepaidSchedule.MONTHLY)
    R(24, "AMC 12,000 monthly: 12 lines of 1,000 on each month end", list(P1.lines.values_list("amount", flat=True)) == [D("1000")] * 12
      and P1.lines.order_by("recognition_date").first().recognition_date == d(2026, 1, 31))
    R(24, "AMC booked: Dr Prepaid Expenses 12,000 / Cr Bank 12,000 (no VAT on this expense type)",
      jset(P1.journal_entry) == lines(("1300", 12000, 0), ("1120", 0, 12000)))
    P2 = schedule("TEST-FIN building insurance", ins, "10000", d(2026, 1, 1), d(2026, 12, 31), PrepaidSchedule.QUARTERLY, mode="OWED")
    R(24, "insurance 10,000 quarterly owed to vendor: Dr Prepaid 10,000 + VAT Input 500 / Cr Vendor Payables 10,500; 4 lines of 2,500",
      jset(P2.journal_entry) == lines(("1300", 10000, 0), ("1220", 500, 0), ("2500", 0, 10500))
      and list(P2.lines.values_list("amount", flat=True)) == [D("2500")] * 4)
    P2 = pp.record_payment(P2, account=BANK, payment_date=d(2026, 2, 1), user_id=MAKER, today=TODAY)
    P3 = schedule("TEST-FIN trade licence 2025", lic, "1200", d(2025, 1, 1), d(2025, 12, 31), PrepaidSchedule.YEARLY)
    P4 = schedule("TEST-FIN rounding 1,000 / 3 months", amc, "1000", d(2026, 6, 1), d(2026, 8, 31), PrepaidSchedule.MONTHLY)
    R(24, "rounding: 1,000 over 3 months = 333.33 + 333.33 + 333.34 (last absorbs)",
      list(P4.lines.order_by("recognition_date").values_list("amount", flat=True)) == [D("333.33"), D("333.33"), D("333.34")])
    P5 = schedule("TEST-FIN software subscription", lic, "6000", d(2026, 4, 1), d(2027, 3, 31), PrepaidSchedule.MONTHLY)
    P6 = schedule("TEST-FIN June 2025 service (period-lock test)", amc, "600", d(2025, 6, 1), d(2025, 6, 30), PrepaidSchedule.MONTHLY,
                  pay=d(2025, 5, 31))
    jun25 = AccountingPeriod.objects.create(finance_pmc_profile=p, name="TEST-FIN Jun 2025", start_date=d(2025, 6, 1), end_date=d(2025, 6, 30),
                                            status=AccountingPeriod.CLOSED, closed_by_user_id=CHECKER, closed_at=timezone.now())
    run1 = pp.post_due(TODAY, profile=p)
    run2 = pp.post_due(TODAY, profile=p)
    line6 = P6.lines.get()
    R(24, "daily job: AMC Jan-Aug, insurance Q1-Q2, licence, rounding lines, subscription Apr-Aug posted; nothing dated after today",
      (P1.lines.filter(status="POSTED").count(), P2.lines.filter(status="POSTED").count(), P3.lines.filter(status="POSTED").count(),
       P4.lines.filter(status="POSTED").count(), P5.lines.filter(status="POSTED").count()) == (8, 2, 1, 3, 5)
      and not P1.lines.filter(status="POSTED", recognition_date__gt=TODAY).exists(), (run1["counts"], run2["counts"]))
    R(30, "prepaid job run twice: second run posts nothing (no duplicate journals per line)",
      run2["counts"]["posted"] == 0 and all(JournalEntry.objects.filter(idempotency_key=f"PREPAID_LINE:{l.id}:RECOGNIZED").count() <= 1
                                            for l in P1.lines.all()), run2["counts"])
    R(24, "period lock: the June 2025 line stays Scheduled while the period is closed (flagged, not posted)",
      line6.status == "SCHEDULED" and "closed" in (line6.last_error or "").lower(), line6.last_error)
    adj = create_adjustment(p, [{"account_id": ACC["5200"].id, "debit": "10"}, {"account_id": BANK.id, "credit": "10"}],
                            reason="TEST-FIN closed-period test", entry_date=d(2025, 6, 15), created_by_user_id=MAKER)
    submit_adjustment(adj, MAKER)
    expect_error(29, "adjustment dated in a closed period is refused", PeriodClosedError, lambda: approve_adjustment(adj, CHECKER))
    jun25.status, jun25.reopened_by_user_id, jun25.reopened_at, jun25.reopen_reason = AccountingPeriod.OPEN, CHECKER, timezone.now(), "TEST-FIN lock test done"
    jun25.save()
    pp.post_due(TODAY, profile=p)
    line6.refresh_from_db()
    P6.refresh_from_db()
    R(24, "after reopening, the June 2025 line posts; schedule Completed", (line6.status, P6.status) == ("POSTED", "COMPLETED"), (line6.status, P6.status))
    P3.refresh_from_db()
    P4.refresh_from_db()
    R(24, "completion: yearly licence and the 3-month schedule are Completed", (P3.status, P4.status) == ("COMPLETED", "COMPLETED"))
    first_line = P1.lines.order_by("recognition_date").first()
    R(24, "monthly recognition: Dr AMC expense (5100) 1,000 / Cr Prepaid 1,000",
      jset(first_line.journal_entry) == lines(("5100", 1000, 0), ("1300", 0, 1000)))
    P5 = pp.cancel(P5, mode="REFUND", cancel_date=TODAY, reason="TEST-FIN subscription cancelled", account=BANK, user_id=MAKER, today=TODAY)
    R(24, "cancellation with refund: Dr Bank 3,500 / Cr Prepaid 3,500; 7 unposted lines Skipped, 5 posted lines kept",
      P5.status == "CANCELLED" and jset(JournalEntry.objects.get(idempotency_key=f"PREPAID:{P5.id}:CANCELLED")) == lines(("1120", 3500, 0), ("1300", 0, 3500))
      and (P5.lines.filter(status="SKIPPED").count(), P5.lines.filter(status="POSTED").count()) == (7, 5))
    rep = pp.report(p, TODAY)["totals"]
    R(24, "prepaid report reconciles with the ledger: remaining 9,000 (AMC 4,000 + insurance 5,000) = Prepaid Expenses balance",
      (rep["remaining"], rep["ledger_balance"], rep["reconciled"], bal("1300")) == (D("9000"), D("9000"), True, D("9000")), rep)

    # correction by reversal (wrong bank-charge adjustment)
    wrong = create_adjustment(p, [{"account_id": ACC["5200"].id, "debit": "100"}, {"account_id": BANK.id, "credit": "100"}],
                              reason="TEST-FIN bank charge Sep (keyed wrong)", entry_date=d(2026, 9, 20), created_by_user_id=MAKER)
    submit_adjustment(wrong, MAKER)
    approve_adjustment(wrong, CHECKER)
    rev, _ = reverse_journal(wrong, reason="TEST-FIN wrong amount", posted_by_user_id=CHECKER)
    right = create_adjustment(p, [{"account_id": ACC["5200"].id, "debit": "90"}, {"account_id": BANK.id, "credit": "90"}],
                              reason="TEST-FIN bank charge Sep (correct)", entry_date=d(2026, 9, 20), created_by_user_id=MAKER)
    submit_adjustment(right, MAKER)
    approve_adjustment(right, CHECKER)
    R(29, "correction: reversal mirrors the original; net effect is the corrected 90",
      rev.reversed_journal_entry_id == wrong.id and jset(rev) == lines(("5200", 0, 100), ("1120", 100, 0)))
    expect_error(29, "a reversal cannot itself be reversed", WorkflowError, lambda: reverse_journal(rev, reason="again"))
    entry = J(JournalEntry.INVOICE, ctx["L1_rent1_invoice"]).get()
    blocked = []
    for attempt in (lambda: (setattr(entry, "memo", "edited"), entry.save()),
                    lambda: JournalEntry.objects.filter(pk=entry.pk).update(memo="edited"),
                    lambda: entry.delete(),
                    lambda: LedgerLine.objects.filter(journal_entry=entry).update(debit=0),
                    lambda: LedgerLine.objects.filter(journal_entry=entry).delete()):
        try:
            with transaction.atomic():
                attempt()
            blocked.append(False)
        except ImmutableJournalError:
            blocked.append(True)
    entry.refresh_from_db()
    R(29, "posted journals are immutable: save, update, delete of the journal and of its lines all refused", all(blocked), blocked)

    # ══ §30 posting idempotency: redeliver already-posted events ══
    evs = sql("SELECT id, event_type FROM billing_financeevent WHERE pmc_id = %s AND status = 'POSTED' AND event_type = ANY(%s) ORDER BY id",
              [p.pmc_id, ["INVOICE_ISSUED", "REVENUE_RECOGNIZED", "CHEQUE_CLEARED", "CHEQUE_DEPOSITED", "COLLECTION_SETTLED",
                          "TERMINATION_SETTLED", "DEPOSIT_TRANSFERRED"]])
    count0 = JournalEntry.objects.filter(finance_pmc_profile=p).count()
    replay = {}
    for kind in {e["event_type"] for e in evs}:
        event = FinanceEventRef.objects.get(pk=next(e["id"] for e in evs if e["event_type"] == kind))
        replay[kind] = process_event(event).get("status")
    R(30, "each posted event processed again (7 event types): same journal returned, no new journal",
      JournalEntry.objects.filter(finance_pmc_profile=p).count() == count0 and all(v in ("POSTED", "SKIPPED") for v in replay.values()), replay)

    # ══ §25 bank reconciliation ══
    jcount = JournalEntry.objects.filter(finance_pmc_profile=p).count()
    l3_sep = J(JournalEntry.CHEQUE, ctx["L3_cheques"][2], "Cheque cleared").get()
    bal_ch = J(JournalEntry.CHEQUE, ctx["L5_bal_cheque"], "Cheque cleared").get()
    csv_rows = [("2026-09-29", "105000.00", "TFIN L3 Sep cheque"), ("2026-09-29", "209.05", "TFIN card batch"),
                ("2026-09-29", "1218.93", "TFIN gateway batch"), ("2026-09-29", "8247.50", "TFIN L1R transfer"),
                ("2026-09-29", "15660.00", "TFIN balance cheque less fee"), ("2026-09-29", "-51165.00", "TFIN owner 1 payout"),
                ("2026-09-29", "123.45", "TFIN bank interest")]
    body = "date,amount,reference\n" + "\n".join(",".join(r) for r in csv_rows)
    created = import_bank_statement_csv(p, SimpleUploadedFile("tfin_statement.csv", body.encode()))
    stl = {s.reference: s for s in BankStatementLine.objects.filter(finance_pmc_profile=p, reference__startswith="TFIN")}
    R(25, "statement import: 7 lines", created == 7 and len(stl) == 7, created)
    sugg = find_suggested_matches(p)
    by_line = {}
    for s in sugg:
        by_line.setdefault(s["bank_statement_line"].reference, set()).add(s["journal_entry"].id)
    R(25, "suggestions: exact-amount money-in journals (L3 cheque: 3 candidates of 105,000; card/gateway/transfer: 1 each)",
      l3_sep.id in by_line.get("TFIN L3 Sep cheque", set()) and len(by_line.get("TFIN card batch", ())) == 1
      and ps_pg.journal_entry_id in by_line.get("TFIN gateway batch", ()) and len(by_line.get("TFIN L1R transfer", ())) == 1,
      {k: len(v) for k, v in by_line.items()})
    reversed_ids = set(JournalEntry.objects.filter(finance_pmc_profile=p, reversing_entries__isnull=False).values_list("id", flat=True))
    offered = [j for ids in by_line.values() for j in ids if j in reversed_ids]
    R(25, "a reversed journal is not offered as a bank match", not offered,
      f"reversed journal(s) {offered} suggested for the gateway line" if offered else "none")
    R(25, "no suggestion for the fee-reduced amount, the payout (money out) or the interest line",
      not {"TFIN balance cheque less fee", "TFIN owner 1 payout", "TFIN bank interest"} & set(by_line))
    for ref, jid in (("TFIN card batch", ps_card.journal_entry_id), ("TFIN gateway batch", ps_pg.journal_entry_id),
                     ("TFIN L1R transfer", next(iter(by_line["TFIN L1R transfer"])))):
        apply_match_decision(p, stl[ref].id, jid, BankStatementMatch.CONFIRMED)
    apply_match_decision(p, stl["TFIN L3 Sep cheque"].id, l3_sep.id, BankStatementMatch.CONFIRMED)
    others = [j for j in by_line["TFIN L3 Sep cheque"] if j != l3_sep.id]
    apply_match_decision(p, stl["TFIN L3 Sep cheque"].id, others[0], BankStatementMatch.REJECTED)
    expect_error(25, "a statement line already matched cannot be confirmed to a second journal", Exception,
                 lambda: apply_match_decision(p, stl["TFIN L3 Sep cheque"].id, others[1], BankStatementMatch.CONFIRMED))
    apply_match_decision(p, stl["TFIN balance cheque less fee"].id, bal_ch.id, BankStatementMatch.CONFIRMED)
    apply_match_decision(p, stl["TFIN owner 1 payout"].id, pay1.journal_entry_id, BankStatementMatch.CONFIRMED)
    for s in stl.values():
        s.refresh_from_db()
    R(25, "manual matches confirmed: balance cheque (15,660 vs 15,662.50) and owner payout (money out)",
      stl["TFIN balance cheque less fee"].reconciled and stl["TFIN owner 1 payout"].reconciled)
    R(25, "unmatched: only the interest line stays unreconciled",
      [r for r, s in stl.items() if not s.reconciled] == ["TFIN bank interest"], [r for r, s in stl.items() if not s.reconciled])
    R(25, "reconciliation created no journal (no duplicate accounting)",
      JournalEntry.objects.filter(finance_pmc_profile=p).count() == jcount, JournalEntry.objects.filter(finance_pmc_profile=p).count() - jcount)
    FINDINGS.append({"test": "Manual bank match", "note": "confirm accepted 15,660.00 against a 15,662.50 journal with no amount warning"})
    dup_body = "date,amount,reference\n2026-09-29,1.00,TFIN duplicate-import test\n"
    import_bank_statement_csv(p, SimpleUploadedFile("dup.csv", dup_body.encode()))
    import_bank_statement_csv(p, SimpleUploadedFile("dup.csv", dup_body.encode()))
    dups = BankStatementLine.objects.filter(finance_pmc_profile=p, reference="TFIN duplicate-import test").count()
    R(25, "re-importing the same statement file is detected (no duplicate statement lines)", dups == 1, f"{dups} identical lines after importing the file twice")

    # ══ §26 Consolidated (Oracle) report ══
    rep = consolidated(p, Filters(date_from=d(2025, 1, 1), date_to=TODAY))
    ext = sql("SELECT a.external_code, SUM(l.debit) dr, SUM(l.credit) cr, COUNT(DISTINCT l.journal_entry_id) n "
              "FROM ledger_ledgerline l JOIN ledger_account a ON a.id = l.account_id JOIN ledger_journalentry j ON j.id = l.journal_entry_id "
              "WHERE j.finance_pmc_profile_id = %s AND j.status = 'POSTED' AND j.entry_date BETWEEN %s AND %s GROUP BY 1",
              [p.id, d(2025, 1, 1), TODAY])
    want = {(r["external_code"] or None): (r["dr"], r["cr"], r["n"]) for r in ext}
    got = {g["external_code"]: (D(str(g["total_debit"])), D(str(g["total_credit"])), g["transaction_count"]) for g in rep["groups"]}
    R(26, "consolidated by external (Oracle) code: debit, credit and journal count per code match the ledger",
      all(got.get(k) == v for k, v in want.items()), {k: (got.get(k), v) for k, v in want.items() if got.get(k) != v})
    shared = {g["external_code"]: sorted(a["account_code"] for a in g["accounts"]) for g in rep["groups"] if g["mapped"] and len(g["accounts"]) > 1}
    R(26, "several product accounts roll up into one code (VAT out 2410+2420 -> 202000102; payables 2320+2500 -> 202050101)",
      shared.get("202000102") == ["2410", "2420"] and shared.get("202050101") == ["2320", "2500"], shared)
    pending = next((g for g in rep["groups"] if not g["mapped"]), None)
    R(26, "unmapped accounts grouped as 'Mapping pending', no invented code",
      pending is not None and pending["external_code"] is None and rep["totals"]["unmapped_account_count"] >= 1,
      sorted(a["account_code"] for a in (pending or {}).get("accounts", [])))
    R(26, "report totals: debit = credit, and = whole ledger", rep["reconciliation"]["report_balanced"] and rep["reconciliation"]["ledger_balanced"]
      and D(str(rep["totals"]["total_debit"])) == sum(v[0] for v in want.values()), rep["totals"])
    wb_bytes = build_workbook(p, Filters(date_from=d(2025, 1, 1), date_to=TODAY), generated_by="TEST-FIN")
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(wb_bytes), read_only=True)
    total_row = next(r for r in wb["Consolidated"].iter_rows(values_only=True) if r and r[0] == "Total")
    details = [r for r in wb["Transaction Details"].iter_rows(values_only=True)]
    mapping = [r for r in wb["Mapping"].iter_rows(values_only=True)]
    R(26, "Excel export: sheets Consolidated / Transaction Details / Mapping / Report Info",
      wb.sheetnames == ["Consolidated", "Transaction Details", "Mapping", "Report Info"], wb.sheetnames)
    R(26, "Excel Consolidated total row = report totals",
      (D(str(total_row[6])), D(str(total_row[7]))) == (D(str(rep["totals"]["total_debit"])), D(str(rep["totals"]["total_credit"]))), total_row)
    n_lines = posted(journal_entry__entry_date__range=(d(2025, 1, 1), TODAY)).count()
    detail_rows = [r for r in details if r and len(r) >= 15 and isinstance(r[0], (dt.date, dt.datetime))]
    R(26, "Excel Transaction Details: one row per ledger line, debits/credits add up",
      len(detail_rows) == n_lines and round(sum(float(r[13] or 0) for r in detail_rows), 2) == float(rep["totals"]["total_debit"]),
      (len(detail_rows), n_lines))
    R(26, "Excel Mapping sheet lists every account with its mapping status ('Mapping pending' for the unmapped)",
      sum(1 for r in mapping if r and len(r) > 6 and r[1] in ACC) == len(ACC) and any(r and len(r) > 6 and r[6] == "Mapping pending" for r in mapping), [r for r in mapping if r and len(r) > 6][:3])

    # ══ §27 Trial Balance + General Ledger ══
    tb = compute_trial_balance(p, d(2025, 1, 1), TODAY)
    tdr = sum((D(str(a["total_debit"])) for a in tb["accounts"]), D("0"))
    tcr = sum((D(str(a["total_credit"])) for a in tb["accounts"]), D("0"))
    R(27, f"Trial Balance: total debit {tdr} = total credit {tcr}", tb["balanced"] and tdr == tcr, (tdr, tcr))
    gl = statements.general_ledger(p, d(2026, 1, 1), TODAY, include_lines=False)
    gl_bad = [(a["account_code"], a["opening_balance"], a["total_debit"], a["total_credit"], a["closing_balance"]) for a in gl["accounts"]
              if D(a["opening_balance"]) + D(a["total_debit"]) - D(a["total_credit"]) != D(a["closing_balance"])]
    R(27, "General Ledger (2026 YTD, 2025 as opening): opening + debits - credits = closing for every account", not gl_bad, gl_bad)
    gl_close = {a["account_code"]: D(a["closing_balance"]) for a in gl["accounts"]}

    # ══ §28 account-by-account reconciliation against the independent expected ledger ══
    expected = {
        "1210": D("8666.63"), "1120": D("439975.54"), "1110": D("420"), "1130": D("0"), "1140": D("0"), "1220": D("500"),
        "1300": D("9000"), "2110": D("-5000"), "2120": D("0"), "2200": D("-109000"), "2310": D("0"), "2320": D("-85623.75"),
        "2410": D("-20672.38"), "2420": D("-638.75"), "2500": D("-10000"), "4100": D("-183458.33"), "4110": D("0"), "4190": D("0"),
        "4200": D("-12775"), "4250": D("-8000"), "4260": D("-2000"), "4310": D("-600"), "4320": D("-14.20"), "4330": D("-29500"),
        "4340": D("-9750"), "5100": D("18300"), "5200": D("170.24"), "3100": D("0"), "5300": D("0"),
    }
    table = []
    for code, want_bal in expected.items():
        actual = bal(code)
        table.append({"code": code, "name": ACC[code].name, "expected": str(want_bal), "actual": str(actual),
                      "gl": str(gl_close.get(code, D("0"))), "diff": str(actual - want_bal), "ok": actual == want_bal})
        R(28, f"{code} {ACC[code].name}: expected {want_bal} actual {actual}", actual == want_bal and gl_close.get(code, D("0")) == actual)
    print("ACCOUNTS " + json.dumps(table))
    R(28, "expected ledger itself balances (sum of expected balances = 0)", sum(expected.values()) == 0, sum(expected.values()))
    tenant_ar = {n: bal("1210", tenant_id=t) for n, t in enumerate(ctx["tenants"], 1)}
    R(28, "tenant balances: T1 5,841.63 (L1 cheque 12 in hand), T2 2,825 (L2 rent 3 on cheque 4), others 0",
      tenant_ar == {1: D("5841.63"), 2: D("2825"), 3: 0, 4: 0, 5: 0}, tenant_ar)

    # Balance Sheet / Chart of Accounts against the ledger (backdated journals)
    bs = compute_balance_sheet(p, TODAY)
    bs_ar = next((D(str(a["balance"])) for a in bs["asset_accounts"] if a["id"] == ACC["1210"].id), None)
    R(27, "Balance Sheet balances (assets = liabilities + equity)", bs["balanced"], bs.get("total_equity"))
    R(27, "Balance Sheet Tenant Receivables = ledger balance 8,666.63", bs_ar == expected["1210"], f"balance sheet shows {bs_ar}")
    coa = compute_chart_of_accounts(p)
    coa_rows = coa if isinstance(coa, list) else coa.get("accounts", coa.get("content", []))
    coa_bank = next((r for r in coa_rows if isinstance(r, dict) and r.get("id") == ACC["1120"].id), None)
    R(27, "Chart of Accounts balance for Bank = ledger balance", coa_bank is not None and D(str(coa_bank.get("balance", "nan"))) == expected["1120"],
      coa_bank)

    # ══ §29 integrity ══
    ub = sql("SELECT j.number FROM ledger_journalentry j JOIN ledger_ledgerline l ON l.journal_entry_id = j.id "
             "WHERE j.finance_pmc_profile_id = %s AND j.status = 'POSTED' GROUP BY j.id HAVING SUM(l.debit) <> SUM(l.credit)", [p.id])
    R(29, "no unbalanced posted journal", not ub, ub)
    orphan = sql("SELECT COUNT(*) n FROM ledger_ledgerline l LEFT JOIN ledger_journalentry j ON j.id = l.journal_entry_id WHERE j.id IS NULL")[0]["n"]
    R(29, "no orphan ledger lines", orphan == 0, orphan)
    badline = posted().filter(debit__lt=0).count() + posted().filter(credit__lt=0).count() + posted().filter(debit__gt=0, credit__gt=0).count()
    R(29, "no negative line or line with both debit and credit", badline == 0, badline)
    nonpost = posted().filter(account__is_postable=False).count()
    R(29, "nothing posted to header (non-postable) accounts", nonpost == 0, nonpost)
    events = sql("SELECT idempotency_key FROM billing_financeevent WHERE pmc_id = %s AND status = 'POSTED'", [p.pmc_id])
    main = set(JournalEntry.objects.filter(finance_pmc_profile=p, idempotency_key__startswith="EVT:").values_list("idempotency_key", flat=True))
    missing_j = [e["idempotency_key"] for e in events if f"EVT:{e['idempotency_key']}:main" not in main]
    R(29, f"every posted business event ({len(events)}) has its journal; no journal without an event",
      not missing_j and len(main) == len(events), (missing_j[:5], len(main), len(events)))
    dup_src = sql("SELECT source_type, source_id, memo, COUNT(*) n FROM ledger_journalentry WHERE finance_pmc_profile_id = %s "
                  "AND status = 'POSTED' AND reversed_journal_entry_id IS NULL AND source_type NOT IN ('ADJUSTMENT', 'MANUAL', 'PROVIDER_FEE', 'PREPAID_RECOGNITION') "
                  "GROUP BY 1, 2, 3 HAVING COUNT(*) > 1", [p.id])
    R(29, "no duplicate posting for the same source document and step", not dup_src, dup_src[:5])
    future = JournalEntry.objects.filter(finance_pmc_profile=p, status=JournalEntry.POSTED, entry_date__gt=TODAY).count()
    R(29, "no future-dated journal", future == 0, future)
    sources = set(JournalEntry.objects.filter(finance_pmc_profile=p).values_list("source_type", flat=True))
    adjs = JournalEntry.objects.filter(finance_pmc_profile=p, source_type=JournalEntry.ADJUSTMENT, status=JournalEntry.POSTED)
    R(29, "no direct ledger entries: every journal comes from an operational source or an approved adjustment (maker != checker, reason given)",
      "MANUAL" not in sources and all(j.approved_by_user_id and j.approved_by_user_id != j.created_by_user_id and j.reason for j in adjs)
      or all(j.reversed_journal_entry_id for j in adjs if not j.approved_by_user_id), sorted(sources))
    neg = {
        "Cheques in Bank": bal("1130"), "Settlement Pending": bal("1140"), "Tenant Refunds Payable": bal("2120"),
        "Clearing": bal("2310"), "Cash (>=0)": bal("1110"), "Prepaid (>=0)": bal("1300"),
    }
    per_lease_dep = {l: bal("2110", lease_id=ctx[l]) for l in ("L1", "L1R", "L2", "L3", "L4", "L5")}
    R(29, "no unexpected balances: clearing accounts 0, cash/prepaid >= 0, no lease with a debit deposit balance",
      neg["Cheques in Bank"] == 0 and neg["Settlement Pending"] == 0 and neg["Tenant Refunds Payable"] == 0 and neg["Clearing"] == 0
      and neg["Cash (>=0)"] >= 0 and neg["Prepaid (>=0)"] >= 0 and all(v <= 0 for v in per_lease_dep.values()), (neg, per_lease_dep))
    owner_neg = {o: oa.owner_balance(p, o) for o in ctx["owners"]}
    R(29, "no owner with a negative payable", all(v >= 0 for v in owner_neg.values()), owner_neg)
    print("FINDINGS " + json.dumps(FINDINGS))
    print("TOTALS " + json.dumps({"tb_debit": str(tdr), "tb_credit": str(tcr), "journals": JournalEntry.objects.filter(finance_pmc_profile=p, status="POSTED").count(),
                                  "lines": posted().count()}))


class Rollback(Exception):
    pass


try:
    with transaction.atomic():
        run()
        if DRY_RUN:
            raise Rollback()
except Rollback:
    print("DRY RUN -- rolled back")
print(f"SUMMARY {sum(RESULTS)}/{len(RESULTS)} passed")
