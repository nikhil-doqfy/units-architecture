"""Finance E2E -- units-backend side (run inside the units_backend container, scratch DB).

    STAGE=build    create the scenario data and run the operational flows (tests 1-12, 16);
                   prints E2E_CONTEXT {...} for the later stages
    STAGE=deliver  deliver every accounting event to units-finance over HTTP (test 19: the
                   event dated inside the closed period must FAIL)
    STAGE=trace    retry the closed-period event after the period was reopened (test 19) and
                   trace unit <-> ledger both ways (test 20)

Every check prints a line `E2E_RESULT {"test": n, "check": ..., "ok": ...}`.
Never run against a real database: journals are immutable.
"""
import json
import os
from datetime import date, datetime
from decimal import Decimal as D

from django.db import connection
from django.utils import timezone

import billing.finance_events as fe

fe._queue_delivery = lambda event_id: None  # delivered explicitly in STAGE=deliver

from billing.cheques import ChequeActionError, add_replacement_cheque, clear_cheque, deposit_cheque, return_cheque  # noqa: E402
from billing.collections import CollectionError, quote, settle  # noqa: E402
from billing.finance_jobs import issue_due_invoices, raise_debit_note, recognize_revenue  # noqa: E402
from billing.models import Collection, FinanceEvent, Invoice, PDCReturnReason, Receipt  # noqa: E402
from billing.schedule import generate_lease_schedule  # noqa: E402
from charges.models import Charge  # noqa: E402
from lease.lifecycle import create_renewal, on_lease_activated  # noqa: E402
from lease.models import ChequeNotAllowedError, Lease, LeaseInstallment, LeaseTransaction  # noqa: E402
from lease.termination import approve_refund, confirm, create_refund, preview  # noqa: E402
from lease.tests.factories import (  # noqa: E402
    LeaseFactory, OwnerFactory, TenantFactory, UnitFactory, build_standard_stack,
)
from payment.models import CompanyBankAccount, PaymentMethod  # noqa: E402
from property.ownership import set_unit_owners  # noqa: E402
from property_management.models import Country  # noqa: E402
from utilities import constants as c  # noqa: E402

STAGE = os.environ.get("STAGE", "build")


def result(test, check, ok, detail=""):
    print("E2E_RESULT " + json.dumps({"test": test, "check": check, "ok": bool(ok), "detail": str(detail)}))


def aware(y, m, d):
    return timezone.make_aware(datetime(y, m, d))


def rent(lease, n):
    return lease.installments.get(installment_type=c.INSTALLMENT_TYPE_RENT, installment_no=n)


def of_type(lease, installment_type):
    return lease.installments.get(installment_type=installment_type)


def bank_it(installment, on, account, user):
    cheque = installment.cheques.order_by("-attempt_no").first()
    deposit_cheque(cheque.id, deposit_account=account, deposit_date=on, user=user)
    clear_cheque(cheque.id, clearing_date=on, user=user)
    return cheque


def expect_error(test, check, exc_type, func, code=None):
    try:
        func()
    except exc_type as exc:
        result(test, check, code is None or getattr(exc, "code", None) == code, f"{exc_type.__name__}: {exc}")
    else:
        result(test, check, False, "no error raised")


# ── STAGE=build ────────────────────────────────────────────────────────────────

def build():
    stack = build_standard_stack()
    admin, pm_user, company = stack["admin"], stack["pm_user"], stack["company"]
    block = stack["block"]
    country = Country.objects.create(name="UAE", code="AE")
    account = CompanyBankAccount.objects.create(pmc=company, account_name="Operating", account_number="001",
                                                clearing_days=3, created_by=admin)
    cash = PaymentMethod.objects.get(pmc=None, code="CASH")
    card = PaymentMethod.objects.get(pmc=None, code="CREDIT_CARD")
    transfer = PaymentMethod.objects.get(pmc=None, code="BANK_TRANSFER")
    return_charge = Charge.objects.create(description="Cheque return charge", country=country,
                                          charge_type=c.CHARGE_TYPE_CHEQUE_RETURN, calc_type=c.CHARGE_CALC_FIXED,
                                          amount=D("100"), tax_code=D("5"), created_by=admin)
    reason = PDCReturnReason.objects.get(pmc=None, code="INSUFFICIENT_FUNDS")
    reason.charge = return_charge
    reason.save(update_fields=["charge"])
    Charge.objects.create(description="Card fee", country=country, charge_type=c.CHARGE_TYPE_PAYMENT_CHANNEL,
                          calc_type=c.CHARGE_CALC_PERCENT, rate=D("2"), tax_code=D("5"), payment_method=card,
                          created_by=admin)
    Charge.objects.create(description="Early termination penalty", country=country,
                          charge_type=c.CHARGE_TYPE_TERMINATION_PENALTY, calc_type=c.CHARGE_CALC_MONTHS_OF_RENT,
                          months=D("2"), tax_code=D("5"), created_by=admin)
    cleaning = Charge.objects.create(description="Cleaning", country=country, charge_type=c.CHARGE_TYPE_MISCELLANEOUS,
                                     calc_type=c.CHARGE_CALC_FIXED, amount=D("500"), tax_code=D("5"),
                                     is_editable=False, created_by=admin)
    admin_fee = Charge.objects.create(description="Owner admin fee", country=country,
                                      charge_type=c.CHARGE_TYPE_MISCELLANEOUS, calc_type=c.CHARGE_CALC_FIXED,
                                      amount=D("500"), tax_code=D("5"), pmc=company, created_by=admin)
    late_fee = Charge.objects.create(description="Late document fee", country=country, charge_type=c.CHARGE_TYPE_MISCELLANEOUS,
                                     calc_type=c.CHARGE_CALC_FIXED, amount=D("100"), tax_code=D("0"), created_by=admin)

    def unit(name, usage, management, commission=None):
        u = stack["unit"] if name == "A" else UnitFactory(block=block, created_by=admin)
        u.unit_name, u.unit_usage, u.management_type, u.status = f"Unit {name}", usage, management, c.UNIT_ACTIVE
        u.commission_percent = commission
        u.save()
        return u

    # ── Test 1 / 10 / 12: commercial MANAGED unit A, owners 60/40, 10,000 rent less 1,000 discount ──
    unit_a = unit("A", c.COMMERCIAL, c.UNIT_MANAGEMENT_MANAGED, D("5"))
    owner_a, owner_b = OwnerFactory(created_by=admin), OwnerFactory(created_by=admin)
    set_unit_owners(unit_a, [(owner_a, D("60")), (owner_b, D("40"))], admin, effective_date=date(2026, 1, 1))
    lease_a = LeaseFactory(unit=unit_a, tenant=stack["tenant"], created_by=admin, lease_status=c.ACTIVE,
                           start_date=aware(2026, 6, 1), end_date=aware(2027, 5, 31), contract_amount=D("120000"),
                           discount=D("12000"), payment_count=12, rent=D("10000"), security_deposit=D("5000"),
                           commission=D("1000"))
    generate_lease_schedule(lease_a, admin)
    # ── Test 11 / 6-8: residential INTERNAL unit B, 5,000 rent, no VAT ──
    unit_b = unit("B", c.RESIDENTIAL, c.UNIT_MANAGEMENT_INTERNAL)
    lease_b = LeaseFactory(unit=unit_b, tenant=TenantFactory(created_by=pm_user), created_by=admin,
                           lease_status=c.ACTIVE, start_date=aware(2026, 6, 1), end_date=aware(2027, 5, 31),
                           contract_amount=D("60000"), discount=D("0"), payment_count=12, rent=D("5000"),
                           security_deposit=D("5000"), commission=D("0"))
    generate_lease_schedule(lease_b, admin)
    # ── Test 2: residential INTERNAL unit C, lease ending 30 Sep 2026, renewed ──
    unit_c = unit("C", c.RESIDENTIAL, c.UNIT_MANAGEMENT_INTERNAL)
    lease_c = LeaseFactory(unit=unit_c, tenant=TenantFactory(created_by=pm_user), created_by=admin,
                           lease_status=c.ACTIVE, start_date=aware(2025, 10, 1), end_date=aware(2026, 9, 30),
                           contract_amount=D("48000"), discount=D("0"), payment_count=12, rent=D("4000"),
                           security_deposit=D("3000"), commission=D("500"))
    generate_lease_schedule(lease_c, admin)

    issue_due_invoices(today=date(2026, 9, 27))

    # ── Test 1: the lease's schedule, invoices and PDCs ──
    rents = lease_a.installments.filter(installment_type=c.INSTALLMENT_TYPE_RENT)
    result(1, "12 rent installments + deposit + commission",
           rents.count() == 12 and lease_a.installments.filter(installment_type__in=[c.INSTALLMENT_TYPE_DEPOSIT, c.INSTALLMENT_TYPE_COMMISSION]).count() == 2,
           [i.installment_type for i in lease_a.installments.all()])
    result(1, "one invoice per rent installment, issued up to today, scheduled after",
           Invoice.objects.filter(installment__in=rents, status=Invoice.ISSUED).count() == 4
           and Invoice.objects.filter(installment__in=rents, status=Invoice.SCHEDULED).count() == 8)
    result(1, "one PDC (attempt 1) per installment", all(i.cheques.filter(attempt_no=1).count() == 1 for i in rents))

    # ── Test 10 / 12: discount and commercial VAT on the installment and invoice ──
    first = rent(lease_a, 1)
    result(10, "installment 10,000 - 1,000 discount = 9,000 net",
           (first.original_amount, first.discount_amount, first.net_amount) == (D("10000.00"), D("1000.00"), D("9000.00")))
    result(10, "PDC for the net amount + VAT", first.cheques.get().total == D("9450.00"), first.cheques.get().total)
    result(12, "commercial rent: VAT 5% = 450 on the invoice",
           (first.invoice.vat_rate, first.invoice.vat_amount, first.invoice.total) == (D("5.000"), D("450.00"), D("9450.00")))
    first_b = rent(lease_b, 1)
    result(11, "residential rent: VAT 0%", (first_b.vat_amount, first_b.invoice.vat_amount, first_b.invoice.total)
           == (D("0.00"), D("0.00"), D("5000.00")))

    # ── Test 3: a collected PDC is in the register only ──
    oct_cheque = rent(lease_a, 5).cheques.get()
    result(3, "PDC created, linked to its installment, in hand",
           oct_cheque.installment_id == rent(lease_a, 5).id and oct_cheque.status == c.CHEQUE_STATUS_BALANCE)
    result(3, "no accounting event for a PDC in hand",
           not FinanceEvent.objects.filter(source_type="CHEQUE", source_id=oct_cheque.id).exists())

    # ── Tests 4 / 5: deposit then clear (deposit, Jun-Aug for A and B; deposit for C) ──
    jun = rent(lease_a, 1).cheques.get()
    deposit_cheque(jun.id, deposit_account=account, deposit_date=date(2026, 6, 1), user=admin)
    jun.refresh_from_db()
    result(4, "deposited: status DEPOSITED, expected clearing = deposit + 3 days",
           (jun.status, jun.expected_clearing_date) == (c.CHEQUE_STATUS_DEPOSITED, date(2026, 6, 4)))
    result(4, "CHEQUE_DEPOSITED event raised", FinanceEvent.objects.filter(event_type="CHEQUE_DEPOSITED", source_id=jun.id).exists())
    clear_cheque(jun.id, clearing_date=date(2026, 6, 3), user=admin)
    jun.refresh_from_db()
    result(5, "cleared: CREDITED and installment SETTLED",
           jun.status == c.CHEQUE_STATUS_CREDITED and rent(lease_a, 1).status == c.INSTALLMENT_STATUS_SETTLED)
    receipt = Receipt.objects.filter(collection__cheque=jun).first()
    result(5, "receipt issued for the cleared cheque", receipt is not None and receipt.number.startswith("REC-"), receipt)
    for lease, months in ((lease_a, (2, 3)), (lease_b, (1, 2, 3))):
        bank_it(of_type(lease, c.INSTALLMENT_TYPE_DEPOSIT), lease.start_date.date(), account, admin)
        for n in months:
            bank_it(rent(lease, n), date(2026, 5 + n, 1), account, admin)
    bank_it(of_type(lease_c, c.INSTALLMENT_TYPE_DEPOSIT), date(2025, 10, 1), account, admin)

    # ── Tests 6-8: cheque returns on lease B's September rent (limit: 2 returns) ──
    sep_b = rent(lease_b, 4)
    first_cheque = sep_b.cheques.get()
    deposit_cheque(first_cheque.id, deposit_account=account, deposit_date=date(2026, 9, 1), user=admin)
    _, charge_note_1 = return_cheque(first_cheque.id, reason=reason, return_date=date(2026, 9, 3), user=admin)
    sep_b.refresh_from_db()
    result(6, "first return: attempt count 1, return reason recorded",
           sep_b.cheque_return_count == 1 and first_cheque.__class__.objects.get(pk=first_cheque.pk).return_reason_id == reason.id)
    result(6, "return charge raised as a separate debit note (100 + 5% VAT)",
           charge_note_1 is not None and charge_note_1.total == D("105.00"), charge_note_1)
    expect_error(6, "only a replacement cheque is accepted (non-cheque refused)", CollectionError,
                 lambda: quote(sep_b, cash), "replacement_required")
    second_cheque = add_replacement_cheque(sep_b.id, user=admin, cheque_number="R-1", cheque_date=aware(2026, 9, 4))
    result(6, "replacement cheque allowed (attempt 2)", second_cheque.attempt_no == 2)
    deposit_cheque(second_cheque.id, deposit_account=account, deposit_date=date(2026, 9, 4), user=admin)
    return_cheque(second_cheque.id, reason=reason, return_date=date(2026, 9, 6), user=admin)
    sep_b.refresh_from_db()
    result(7, "second return: attempt count 2, non-cheque payment now required",
           (sep_b.cheque_return_count, sep_b.non_cheque_required) == (2, True))
    result(7, "second return charge raised",
           lease_b.installments.filter(installment_type=c.INSTALLMENT_TYPE_OTHER_CHARGE).count() == 2)
    expect_error(8, "no further cheque: replacement refused", ChequeActionError,
                 lambda: add_replacement_cheque(sep_b.id, user=admin, cheque_number="R-2", cheque_date=aware(2026, 9, 7)))
    expect_error(8, "no further cheque: direct creation refused", ChequeNotAllowedError,
                 lambda: LeaseTransaction(lease=lease_b, installment=sep_b, attempt_no=3,
                                          payment_type=c.PAYMENT_TYPE_CHEQUE, created_by=admin).save())
    expect_error(8, "cheque method refused for collection", CollectionError, lambda: quote(sep_b, PaymentMethod.objects.get(pmc=None, code="CHEQUE")), "cheque_method")
    q = quote(sep_b, transfer)
    settle(sep_b.id, transfer, expected_total=q["total"], user=admin, collection_date=date(2026, 9, 8),
           settlement_account=account, reference="TT-1")
    sep_b.refresh_from_db()
    result(8, "paid by bank transfer instead", sep_b.status == c.INSTALLMENT_STATUS_SETTLED)

    # ── Test 9: payment channel charge (card 2% + VAT) on lease A's commission ──
    commission = of_type(lease_a, c.INSTALLMENT_TYPE_COMMISSION)
    q = quote(commission, card)
    result(9, "quote: 1,050 + 2% card fee 21 + VAT 1.05 = 1,072.05",
           (q["outstanding"], q["charge_amount"], q["charge_vat"], q["total"]) == (D("1050.00"), D("21.00"), D("1.05"), D("1072.05")), q)
    expect_error(9, "partial / stale amount refused", CollectionError,
                 lambda: settle(commission.id, card, expected_total=D("1050.00"), user=admin), "quote_changed")
    collection, card_receipt = settle(commission.id, card, expected_total=q["total"], user=admin,
                                      collection_date=date(2026, 6, 2), reference="AUTH-9")
    result(9, "collected with a receipt", card_receipt.number.startswith("REC-") and collection.total_collected == D("1072.05"))

    recognize_revenue(today=date(2026, 9, 27))

    # ── Test 16: termination of A (amount due) and B (refund) ──
    terms_a = dict(charges=[{"charge_id": cleaning.id}], waived_amount="5000")
    plan_a = preview(lease_a, date(2026, 9, 15), **terms_a)
    result(16, "A: future installments / PDCs identified",
           (len(plan_a["cancelled_installments"]), len(plan_a["cheques_returned_to_tenant"])) == (8, 9), plan_a["cancelled_installments"])
    result(16, "A: 2-month penalty 20,000, 5,000 waived, VAT on 15,000",
           (plan_a["penalty"]["calculated"], plan_a["penalty"]["final"], plan_a["penalty"]["vat"]) == ("20000.00", "15000.00", "750.00"))
    result(16, "A: earned 33,075 vs collected 28,350; settlement due 16,000",
           (plan_a["rent_earned"], plan_a["rent_collected"], plan_a["net"]) == ("33075.00", "28350.00", "-16000.00"), plan_a)
    term_a = confirm(lease_a.id, termination_date=date(2026, 9, 15), reason="Business closing", expected_net=D(plan_a["net"]),
                     user=admin, waiver_reason="Long-standing tenant", today=date(2026, 9, 27), **terms_a)
    lease_a.refresh_from_db()
    unit_a.refresh_from_db()
    result(16, "A: lease closed, unit available",
           (lease_a.lease_status, unit_a.status) == (c.TERMINATED, c.UNIT_AVAILABLE))
    result(16, "A: waiver audited beside the calculation",
           (term_a.penalty_calculated, term_a.penalty_waived, term_a.waived_by_id) == (D("20000.00"), D("5000.00"), admin.id))
    q = quote(term_a.balance_installment, transfer)
    settle(term_a.balance_installment.id, transfer, expected_total=q["total"], user=admin,
           collection_date=date(2026, 9, 27), settlement_account=account)
    plan_b = preview(lease_b, date(2026, 9, 20), waived_amount="10000")
    result(16, "B: refund 6,456.67 (deposit + collected - earned - unpaid return charges)",
           (plan_b["net"], plan_b["outcome"]) == ("6456.67", "REFUND"), plan_b)
    term_b = confirm(lease_b.id, termination_date=date(2026, 9, 20), reason="Relocation", expected_net=D(plan_b["net"]),
                     user=admin, waived_amount="10000", waiver_reason="Medical", today=date(2026, 9, 27))
    refund = create_refund(term_b, bank_account=account, refund_date=date(2026, 9, 27), user=admin, today=date(2026, 9, 27))
    expect_error(16, "refund needs a second approver", Exception, lambda: approve_refund(refund.id, admin))
    refund = approve_refund(refund.id, pm_user)
    result(16, "B: one refund payment posted", refund.status == "POSTED" and refund.amount == D("6456.67"))

    # ── Test 2: renewal of C ──
    renewal = create_renewal(lease_c, {"end_date": aware(2027, 9, 30), "contract_amount": "54000", "payment_count": 12}, admin)
    generate_lease_schedule(renewal, admin)
    renewal.lease_status = c.ACTIVE
    renewal.save(update_fields=["lease_status"])
    on_lease_activated(renewal, admin)
    issue_due_invoices(today=date(2026, 10, 1))
    lease_c.refresh_from_db()
    result(2, "renewal is a new lease linked to the old one; old lease RENEWED",
           renewal.renewed_from_id == lease_c.id and lease_c.lease_status == "RENEWED")
    result(2, "new installments and PDCs for the new period (no new deposit)",
           renewal.installments.filter(installment_type=c.INSTALLMENT_TYPE_RENT).count() == 12
           and not renewal.installments.filter(installment_type=c.INSTALLMENT_TYPE_DEPOSIT).exists()
           and all(i.cheques.count() == 1 for i in renewal.installments.filter(installment_type=c.INSTALLMENT_TYPE_RENT)))
    renewal_commission = of_type(renewal, c.INSTALLMENT_TYPE_COMMISSION)
    result(2, "initial commission charged again (500 + VAT)", renewal_commission.total_amount == D("525.00"),
           renewal_commission.total_amount)
    result(2, "deposit transfer event raised", FinanceEvent.objects.filter(event_type="DEPOSIT_TRANSFERRED").exists())

    # ── Test 19 (backend part): a document dated inside a period finance will close (Jan 2026) ──
    jan_invoice = raise_debit_note(lease_c, late_fee, D("100"), date(2026, 1, 15), admin)
    jan_event = FinanceEvent.objects.get(source_type="INVOICE", source_id=jan_invoice.id)

    context = {
        "pmc_id": company.id, "admin_user_id": admin.id,
        "unit_a": unit_a.id, "unit_b": unit_b.id, "unit_c": unit_c.id,
        "lease_a": lease_a.id, "lease_b": lease_b.id, "lease_c": lease_c.id, "renewal": renewal.id,
        "tenant_a": lease_a.tenant_id, "tenant_b": lease_b.tenant_id,
        "owner_a": owner_a.id, "owner_b": owner_b.id, "admin_fee_charge": admin_fee.id,
        "jun_cheque_a": jun.id, "jun_installment_a": rent(lease_a, 1).id, "oct_cheque_a": oct_cheque.id,
        "sep_installment_b": sep_b.id, "commission_a": commission.id, "first_invoice_a": first.invoice.id,
        "first_invoice_b": first_b.invoice.id, "termination_a": term_a.id, "termination_b": term_b.id,
        "refund_b": refund.id, "jan_event": jan_event.id, "renewal_commission_invoice": renewal_commission.invoice.id,
    }
    print("E2E_CONTEXT " + json.dumps(context))


# ── STAGE=deliver ──────────────────────────────────────────────────────────────

def deliver_all():
    ctx = json.loads(os.environ["E2E_CONTEXT"])
    for event in FinanceEvent.objects.filter(status=FinanceEvent.PENDING).order_by("id"):
        fe.deliver(event.id)
    failed = list(FinanceEvent.objects.filter(status=FinanceEvent.FAILED))
    in_closed_month = [e for e in failed if "'Jan 2026' is closed" in e.last_error]
    result(19, "every posting dated in the closed month failed with the period-closed error (incl. the test document)",
           failed and len(in_closed_month) == len(failed) and ctx["jan_event"] in [e.id for e in failed],
           [(e.id, e.event_type, e.last_error[:60]) for e in failed])
    posted = FinanceEvent.objects.filter(status=FinanceEvent.POSTED).count()
    result(1, f"every accounting event outside the closed month posted ({posted})",
           FinanceEvent.objects.exclude(status__in=[FinanceEvent.POSTED, FinanceEvent.FAILED]).count() == 0)


# ── STAGE=trace ────────────────────────────────────────────────────────────────

def rows(sql, params):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def trace():
    ctx = json.loads(os.environ["E2E_CONTEXT"])
    retried = [fe.deliver(e.id) for e in FinanceEvent.objects.filter(status=FinanceEvent.FAILED).order_by("id")]
    result(19, f"after the period is reopened, the {len(retried)} failed postings are retried and post",
           retried and all(e.status == FinanceEvent.POSTED for e in retried), [(e.id, e.status) for e in retried])

    # Test 20: Unit -> Lease -> Installment -> Invoice -> PDC -> Payment -> Journal -> Ledger
    unit_id = ctx["unit_a"]
    lease = Lease.objects.get(unit_id=unit_id, pk=ctx["lease_a"])
    installment = lease.installments.get(pk=ctx["jun_installment_a"])
    invoice = installment.invoice
    cheque = installment.cheques.get(pk=ctx["jun_cheque_a"])
    payment = Collection.objects.get(cheque=cheque)
    journals = rows(
        "SELECT j.id, j.number, j.source_type, j.source_id FROM ledger_journalentry j "
        "WHERE j.status = 'POSTED' AND ((j.source_type = 'INVOICE' AND j.source_id = %s) "
        "OR (j.source_type = 'CHEQUE' AND j.source_id = %s))", [invoice.id, cheque.id])
    kinds = sorted(j["source_type"] for j in journals)
    result(20, "unit -> lease -> installment -> invoice -> PDC -> payment -> journals",
           kinds == ["CHEQUE", "CHEQUE", "INVOICE"] and payment.installment_id == installment.id, kinds)
    lines = rows("SELECT unit_id, lease_id, installment_id FROM ledger_ledgerline WHERE journal_entry_id = ANY(%s)",
                 [[j["id"] for j in journals]])
    result(20, "every ledger line of those journals carries the unit, lease and installment",
           lines and all((l["unit_id"], l["lease_id"], l["installment_id"]) == (unit_id, lease.id, installment.id) for l in lines))

    # Ledger -> Journal -> Invoice -> Installment -> Lease -> Tenant -> Unit
    line = rows(
        "SELECT l.id, l.installment_id, j.source_type, j.source_id FROM ledger_ledgerline l "
        "JOIN ledger_journalentry j ON j.id = l.journal_entry_id "
        "WHERE l.unit_id = %s AND j.source_type = 'INVOICE' AND j.status = 'POSTED' ORDER BY l.id LIMIT 1", [unit_id])[0]
    source_invoice = Invoice.objects.get(pk=line["source_id"])
    back_installment = LeaseInstallment.objects.get(pk=source_invoice.installment_id)
    back_lease = back_installment.lease
    result(20, "ledger line -> journal -> invoice -> installment -> lease -> tenant -> unit",
           back_installment.id == line["installment_id"] and back_lease.tenant_id == ctx["tenant_a"]
           and back_lease.unit_id == unit_id,
           f"line {line['id']} -> invoice {source_invoice.number} -> installment {back_installment.id} -> lease {back_lease.code}")


{"build": build, "deliver": deliver_all, "trace": trace}[STAGE]()
