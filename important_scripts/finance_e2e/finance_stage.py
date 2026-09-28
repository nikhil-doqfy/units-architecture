"""Finance E2E -- units-finance side (run inside the units_finance container, scratch DB).

    STAGE=activate  activate Finance for the scenario PMC and close January 2026 (test 19)
    STAGE=verify    check the ledger for every flow (tests 1-16, 21), run owner accounting
                    (tests 13-15), journal controls (17-19), then reopen January so the
                    backend can retry its failed posting

Reads E2E_CONTEXT (printed by backend_stage.py build). Prints E2E_RESULT lines.
"""
import datetime
import json
import os
from decimal import Decimal as D

from django.core.management import call_command
from django.db.models import Sum
from django.utils import timezone

from ledger import owner_accounting as oa
from ledger import statements
from ledger.accounting import (
    PeriodClosedError, WorkflowError, approve_adjustment, create_adjustment, reverse_journal, submit_adjustment,
)
from ledger.models import (
    AccountingPeriod, AccountMapping as R, ExpenseMaster, FinancePMCProfile, ImmutableJournalError, JournalEntry,
    LedgerLine, Vendor,
)
from ledger.reports import compute_trial_balance

STAGE = os.environ.get("STAGE", "verify")
ctx = json.loads(os.environ["E2E_CONTEXT"])
MAKER, CHECKER = 9001, 9002
TODAY = datetime.date(2026, 9, 27)


def result(test, check, ok, detail=""):
    print("E2E_RESULT " + json.dumps({"test": test, "check": check, "ok": bool(ok), "detail": str(detail)}))


def profile():
    return FinancePMCProfile.objects.get(pmc_id=ctx["pmc_id"])


def activate():
    call_command("create_finance_pmc_profile", pmc_id=ctx["pmc_id"], country="UAE", fiscal_year_start_month=1)
    AccountingPeriod.objects.create(
        finance_pmc_profile=profile(), name="Jan 2026", start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 1, 31), status=AccountingPeriod.CLOSED, closed_by_user_id=CHECKER,
        closed_at=timezone.now(),
    )
    result(19, "January 2026 closed", AccountingPeriod.objects.filter(status=AccountingPeriod.CLOSED).count() == 1)


def bal(role, **dims):
    """Debit - credit over posted lines on the role's accounts, filtered by dimensions."""
    lines = LedgerLine.objects.filter(journal_entry__status=JournalEntry.POSTED, account__role_mappings__role=role, **dims)
    totals = lines.distinct().aggregate(dr=Sum("debit"), cr=Sum("credit"))
    return (totals["dr"] or D("0")) - (totals["cr"] or D("0"))


def journals(source_type, source_id):
    return JournalEntry.objects.filter(status=JournalEntry.POSTED, source_type=source_type, source_id=source_id)


def verify():
    p = profile()
    A, B = ctx["lease_a"], ctx["lease_b"]

    # ── Tests 1, 10, 11, 12: invoices post AR / deferred rent / VAT ──
    inv_a = journals(JournalEntry.INVOICE, ctx["first_invoice_a"]).get()
    lines = {(l.account.role_mappings.first().role, l.debit, l.credit) for l in inv_a.lines.select_related("account")}
    result(1, "invoice journal: Dr AR 9,450 / Cr deferred rent 9,000, VAT 450",
           lines == {(R.AR_TENANT, D("9450.00"), D("0")), (R.DEFERRED_RENTAL_INCOME, D("0"), D("9000.00")),
                     (R.VAT_OUTPUT, D("0"), D("450.00"))}, lines)
    result(10, "discount: net 9,000 recognized, no separate discount line",
           not inv_a.lines.filter(account__role_mappings__role=R.DISCOUNT_ALLOWED).exists())
    result(12, "commercial VAT 450 posted to output VAT", inv_a.lines.filter(account__role_mappings__role=R.VAT_OUTPUT, credit=D("450.00")).exists())
    inv_b = journals(JournalEntry.INVOICE, ctx["first_invoice_b"]).get()
    result(11, "residential invoice: no VAT line (AR 5,000 / deferred 5,000)", inv_b.lines.count() == 2
           and not inv_b.lines.filter(account__role_mappings__role=R.VAT_OUTPUT).exists())

    # ── Tests 3-5: the PDC lifecycle in the ledger ──
    result(3, "PDC in hand has no journal", not journals(JournalEntry.CHEQUE, ctx["oct_cheque_a"]).exists())
    jun = {"installment_id": ctx["jun_installment_a"]}
    result(4, "deposit: Dr cheques in bank 9,450 / Cr AR", journals(JournalEntry.CHEQUE, ctx["jun_cheque_a"]).count() == 2
           and bal(R.CHEQUES_IN_BANK, **jun) == 0 and bal(R.AR_TENANT, **jun) == 0)
    result(5, "cleared: bank Dr 9,450 for the installment", bal(R.BANK, **jun) == D("9450.00"), bal(R.BANK, **jun))

    # ── Tests 6-8: returns and the non-cheque collection ──
    sep_b = {"installment_id": ctx["sep_installment_b"]}
    result(6, "both returns reversed their deposits; nothing left in cheques-in-bank or AR",
           bal(R.CHEQUES_IN_BANK, **sep_b) == 0 and bal(R.AR_TENANT, **sep_b) == 0,
           f"AR {bal(R.AR_TENANT, **sep_b)} (paid by transfer; unearned part credited and closed in the termination)")
    result(7, "two return charges income 200 + VAT", bal(R.RETURN_CHARGE_INCOME, lease_id=B) == D("-200.00"),
           bal(R.RETURN_CHARGE_INCOME, lease_id=B))
    result(8, "transfer collected into the bank", bal(R.BANK, **sep_b) == D("5000.00"), bal(R.BANK, **sep_b))

    # ── Test 9: card collection lands in settlement pending with the channel charge ──
    com = {"installment_id": ctx["commission_a"]}
    result(9, "card: settlement pending 1,072.05, channel charge 21, VAT 1.05",
           (bal(R.SETTLEMENT_PENDING, **com), bal(R.CHANNEL_CHARGE_INCOME, **com)) == (D("1072.05"), D("-21.00"))
           and bal(R.VAT_OUTPUT, **com) == D("-51.05"), (bal(R.SETTLEMENT_PENDING, **com), bal(R.VAT_OUTPUT, **com)))

    # ── Test 2: renewal ──
    result(2, "renewal commission invoiced 500 + 25 VAT to leasing commission income",
           journals(JournalEntry.INVOICE, ctx["renewal_commission_invoice"]).exists()
           and bal(R.LEASING_COMMISSION_INCOME, lease_id=ctx["renewal"]) == D("-500.00"))
    result(2, "held deposit moved from the old lease to the renewal",
           (bal(R.SECURITY_DEPOSIT_HELD, lease_id=ctx["lease_c"]), bal(R.SECURITY_DEPOSIT_HELD, lease_id=ctx["renewal"]))
           == (D("0.00"), D("-3000.00")))

    # ── Test 16: termination A (due, paid) and B (refund, paid) ──
    t_a = statements.tenant_statement(p, ctx["tenant_a"], None, TODAY, lease_id=A)
    result(16, "A: credit note, penalty, charge and settlement journals posted",
           journals(JournalEntry.TERMINATION, ctx["termination_a"]).exists()
           and JournalEntry.objects.filter(source_type=JournalEntry.CREDIT_NOTE, status=JournalEntry.POSTED,
                                           lines__lease_id=A).exists())
    result(16, "A: tenant statement closes at 0 after the balance was paid; deposit applied",
           (t_a["closing_balance"], t_a["deposit_held"]) == ("0.00", "0.00"), t_a["closing_balance"])
    result(16, "A: penalty income 15,000 and cleaning 500 (company income)",
           (bal(R.PENALTY_INCOME, lease_id=A), bal(R.OTHER_CHARGE_INCOME, lease_id=A)) == (D("-15000.00"), D("-500.00")))
    result(16, "A: no unearned rent left", bal(R.DEFERRED_RENTAL_INCOME, lease_id=A) == 0, bal(R.DEFERRED_RENTAL_INCOME, lease_id=A))
    result(16, "B: refund payable raised and paid",
           journals(JournalEntry.REFUND, ctx["refund_b"]).exists() and bal(R.TENANT_REFUND_PAYABLE, lease_id=B) == 0)

    # ── Test 21: split cheques post each allocation to its own account ──
    Dl = ctx["lease_d"]
    dep = journals(JournalEntry.CHEQUE, ctx["d_cheque_1"]).get(memo__startswith="Cheque deposited")
    credits = sorted((l.account.role_mappings.first().role, l.installment_id, l.credit)
                     for l in dep.lines.filter(credit__gt=0).select_related("account"))
    result(21, "cheque 1 deposit: Cr AR commission 1,050, Cr deposit held 5,000, Cr AR rent 1 3,904.17",
           credits == sorted([(R.AR_TENANT, ctx["d_commission"], D("1050.00")),
                              (R.SECURITY_DEPOSIT_HELD, ctx["d_deposit"], D("5000.00")),
                              (R.AR_TENANT, ctx["d_rent1"], D("3904.17"))])
           and dep.lines.get(debit__gt=0).debit == D("9954.17"), credits)
    result(21, "deposit held 5,000 for lease D; nothing left in cheques-in-bank",
           (bal(R.SECURITY_DEPOSIT_HELD, lease_id=Dl), bal(R.CHEQUES_IN_BANK, lease_id=Dl)) == (D("-5000.00"), D("0")),
           (bal(R.SECURITY_DEPOSIT_HELD, lease_id=Dl), bal(R.CHEQUES_IN_BANK, lease_id=Dl)))
    result(21, "returned cheque 2 reversed per allocation (deposit + return journals)",
           journals(JournalEntry.CHEQUE, ctx["d_cheque_2"]).count() == 2
           and journals(JournalEntry.CHEQUE, ctx["d_cheque_2"]).get(memo__startswith="Cheque returned").lines.filter(debit__gt=0).count() == 2)
    result(21, "rent 1 receivable closed by two cheques; commission receivable closed",
           (bal(R.AR_TENANT, installment_id=ctx["d_rent1"]), bal(R.AR_TENANT, installment_id=ctx["d_commission"])) == (0, 0),
           (bal(R.AR_TENANT, installment_id=ctx["d_rent1"]), bal(R.AR_TENANT, installment_id=ctx["d_commission"])))
    result(21, "bank received cheque 1 and the replacement (2 x 9,954.17)",
           bal(R.BANK, lease_id=Dl) == D("19908.34"), bal(R.BANK, lease_id=Dl))

    # ── Tests 13-15: expense, owner charge, multi-owner settlement, payout ──
    vendor = Vendor.objects.create(finance_pmc_profile=p, code="V1", name="AC Services")
    expense_type = ExpenseMaster.objects.create(finance_pmc_profile=p, code="MAINT", name="Maintenance",
                                                expense_account=R.objects.get(finance_pmc_profile=p, role=R.EXPENSE, property_id=None).account)
    expense = oa.record_expense(p, unit_id=ctx["unit_a"], expense_type=expense_type, amount="1500",
                                expense_date=datetime.date(2026, 8, 12), vendor=vendor, created_by_user_id=MAKER)
    result(13, "expense on a managed unit: Dr rent clearing / Cr vendor payable",
           bal(R.RENTAL_INCOME_CLEARING, unit_id=ctx["unit_a"], journal_entry=expense.journal_entry) == D("1500.00")
           and bal(R.AP_VENDOR, vendor_id=vendor.id) == D("-1500.00"))
    oa.record_owner_charge(p, unit_id=ctx["unit_a"], charge_id=ctx["admin_fee_charge"], charge_date=datetime.date(2026, 8, 31),
                           created_by_user_id=MAKER)
    settled = []
    for month in (datetime.date(2026, 6, 1), datetime.date(2026, 7, 1), datetime.date(2026, 8, 1)):
        draft = oa.prepare_settlement(p, ctx["unit_a"], month, created_by_user_id=MAKER, today=TODAY)
        try:
            oa.approve_settlement(draft, MAKER, today=TODAY)
            result(14, "settlement maker cannot approve", False)
        except WorkflowError:
            pass
        settled.append(oa.approve_settlement(draft, CHECKER, today=TODAY))
    aug = settled[-1]
    result(15, "Aug: 9,000 earned - 1,500 expense - 500 charge = 7,000 net; fee 350 + 17.50 VAT",
           (aug.net_distributable, aug.fee, aug.fee_vat, aug.owners_total) == (D("7000.00"), D("350.00"), D("17.50"), D("6607.50")),
           (aug.net_distributable, aug.fee, aug.fee_vat, aug.owners_total))
    shares = {line.owner_id: line.amount for line in aug.lines.all()}
    result(14, "Aug split 60/40: 3,964.50 / 2,643.00", shares == {ctx["owner_a"]: D("3964.50"), ctx["owner_b"]: D("2643.00")}, shares)
    result(14, "owner ledgers: A 14,197.50, B 9,465.00 payable",
           (oa.owner_balance(p, ctx["owner_a"]), oa.owner_balance(p, ctx["owner_b"])) == (D("14197.50"), D("9465.00")),
           (oa.owner_balance(p, ctx["owner_a"]), oa.owner_balance(p, ctx["owner_b"])))
    result(14, "rent clearing for Jun-Aug fully distributed",
           sum((bal(R.RENTAL_INCOME_CLEARING, unit_id=ctx["unit_a"], journal_entry__entry_date__month=m) for m in (6, 7, 8)), D("0")) == 0)
    bank = R.objects.get(finance_pmc_profile=p, role=R.BANK, property_id=None).account
    payout = oa.create_payout(p, owner_id=ctx["owner_a"], amount="14197.50", payout_date=TODAY, bank_account=bank,
                              created_by_user_id=MAKER)
    oa.approve_payout(payout, CHECKER)
    result(15, "owner A paid out in full; payable 0", oa.owner_balance(p, ctx["owner_a"]) == 0)
    statement = oa.owner_balance(p, ctx["owner_b"])
    result(15, "owner B still owed 9,465.00 (quarterly owner, not yet paid)", statement == D("9465.00"), statement)

    # ── Test 22: prepaid schedule (AMC paid upfront, recognised monthly) ──
    from ledger import prepaid as pp
    from ledger.models import PrepaidSchedule

    amc = ExpenseMaster.objects.create(finance_pmc_profile=p, code="AMC", name="AMC", vat_applicable=True, vat_rate=D("5"),
                                       expense_account=R.objects.get(finance_pmc_profile=p, role=R.EXPENSE, property_id=None).account)
    schedule = pp.create_draft(p, vendor=vendor, description="Annual AMC", expense_type=amc, amount=D("12000"),
                               start_date=datetime.date(2026, 6, 1), end_date=datetime.date(2027, 5, 31),
                               frequency=PrepaidSchedule.MONTHLY, payment_mode=PrepaidSchedule.PAID,
                               payment_date=datetime.date(2026, 6, 1), paid_from_account=bank, created_by_user_id=MAKER)
    result(22, "12 monthly lines of 1,000 adding up to the prepaid amount",
           list(schedule.lines.values_list("amount", flat=True)) == [D("1000.00")] * 12)
    schedule = pp.activate(schedule, user_id=MAKER, today=TODAY)
    booking = schedule.journal_entry
    result(22, "booked: Dr prepaid 12,000 + input VAT 600 / Cr bank 12,600, no expense yet",
           {(l.account.role_mappings.first().role, l.debit, l.credit) for l in booking.lines.select_related("account")}
           == {(R.PREPAID_EXPENSE, D("12000.00"), D("0")), (R.VAT_INPUT, D("600.00"), D("0")), (R.BANK, D("0"), D("12600.00"))})
    first = pp.post_due(TODAY, profile=p)
    again = pp.post_due(TODAY, profile=p)
    result(22, "daily job posts Jun-Aug (due by 27 Sep) once", (first["counts"]["posted"], again["counts"]["posted"]) == (3, 0),
           (first["counts"], again["counts"]))
    report = pp.report(p, TODAY)["totals"]
    result(22, "remaining 9,000 reconciles with the prepaid ledger account",
           (report["remaining"], report["ledger_balance"], report["reconciled"]) == (D("9000.00"), D("9000.00"), True), report)

    # ── Test 17: posted journals are immutable ──
    entry = inv_a
    blocked = []
    for attempt in (
        lambda: (setattr(entry, "memo", "edited"), entry.save()),
        lambda: JournalEntry.objects.filter(pk=entry.pk).update(memo="edited"),
        lambda: entry.delete(),
        lambda: LedgerLine.objects.filter(journal_entry=entry).update(debit=0),
    ):
        try:
            attempt()
            blocked.append(False)
        except ImmutableJournalError:
            blocked.append(True)
    entry.refresh_from_db()
    result(17, "edit, update, delete of a posted journal and its lines are all blocked",
           all(blocked) and entry.memo != "edited", blocked)

    # ── Test 18: correction by reversal + correct journal (maker-checker) ──
    expense_acc = R.objects.get(finance_pmc_profile=p, role=R.EXPENSE, property_id=None).account
    lines = lambda amount: [{"account_id": expense_acc.id, "debit": amount}, {"account_id": bank.id, "credit": amount}]
    wrong = create_adjustment(p, lines("100"), reason="Bank charge booked", entry_date=datetime.date(2026, 9, 10),
                              created_by_user_id=MAKER)
    submit_adjustment(wrong, MAKER)
    approve_adjustment(wrong, CHECKER)
    reversal, _ = reverse_journal(wrong, reason="Wrong amount", posted_by_user_id=CHECKER)
    right = create_adjustment(p, lines("90"), reason="Bank charge (correct)", entry_date=datetime.date(2026, 9, 10),
                              created_by_user_id=MAKER)
    submit_adjustment(right, MAKER)
    approve_adjustment(right, CHECKER)
    net = sum((l.debit - l.credit for e in (wrong, reversal, right) for l in e.lines.filter(account=expense_acc)), D("0"))
    result(18, "reversal mirrors the original; net effect is the corrected 90",
           reversal.reversed_journal_entry_id == wrong.id and net == D("90.00"), net)
    try:
        reverse_journal(reversal, reason="again")
        result(18, "a reversal cannot itself be reversed", False)
    except WorkflowError:
        result(18, "a reversal cannot itself be reversed", True)

    # ── Test 19: nothing posts into the closed period; reopen for the backend retry ──
    jan = create_adjustment(p, lines("10"), reason="Late entry", entry_date=datetime.date(2026, 1, 10), created_by_user_id=MAKER)
    submit_adjustment(jan, MAKER)
    try:
        approve_adjustment(jan, CHECKER)
        result(19, "adjustment into the closed period blocked", False)
    except PeriodClosedError as exc:
        result(19, "adjustment into the closed period blocked", True, exc)
    period = AccountingPeriod.objects.get(finance_pmc_profile=p, name="Jan 2026")
    period.status, period.reopen_reason, period.reopened_by_user_id = AccountingPeriod.OPEN, "E2E retry", CHECKER
    period.reopened_at = timezone.now()
    period.save()

    tb = compute_trial_balance(p, datetime.date(2025, 1, 1), datetime.date(2027, 12, 31))
    result(1, "trial balance balances", tb["balanced"])


{"activate": activate, "verify": verify}[STAGE]()
