"""PMD (PMC 3) finance end-to-end test -- units-backend side.

Runs inside the units_backend container against the dev DB:
    docker exec -i -e DRY_RUN=1 units_backend python manage.py shell < backend_pmd.py

Creates the TEST-FIN dataset in PMC 3 and drives every operational flow through the
real service functions. Every check prints `R {"sec": ..., "check": ..., "ok": ...}`;
the ids the later stages need are printed as `CTX {...}`.

DRY_RUN=1 rolls everything back. Without it the data is committed and every accounting
event is delivered to units-finance. Posted journals are immutable: run for real once.
"""
import json
import os
from datetime import date, datetime, timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

import billing.finance_events as fe

fe._queue_delivery = lambda event_id: None  # delivered explicitly after commit

from billing.allocations import allocated_on, outstanding  # noqa: E402
from billing.cheques import (  # noqa: E402
    ChequeActionError, add_replacement_cheque, clear_cheque, deposit_cheque, replace_cheque, return_cheque,
)
from billing.collections import CollectionError, quote, settle  # noqa: E402
from billing.finance_jobs import business_today, issue_invoice, recognize_revenue  # noqa: E402
from billing.models import FinanceEvent, FinanceSettings, Invoice, PDCReturnReason, Receipt  # noqa: E402
from billing.schedule import generate_lease_schedule  # noqa: E402
from charges.models import Charge  # noqa: E402
from lease.lifecycle import create_renewal, on_lease_activated, set_unit_status  # noqa: E402
from lease.models import ChequeAllocation, ChequeNotAllowedError, Lease, LeaseInstallment, LeaseTransaction  # noqa: E402
from lease.termination import TerminationError, approve_refund, confirm, create_refund, preview  # noqa: E402
from lease.tests.factories import grant_finance_role  # noqa: E402
from payment.models import CompanyBankAccount, PaymentMethod  # noqa: E402
from property.models import PMCPMMapping, Property, PropertyBlocks, PropertyManagmentCompany, Unit  # noqa: E402
from property.ownership import set_unit_owners  # noqa: E402
from property_management.models import Country  # noqa: E402
from user_service.models import DocumentType, Owner, PropertyManager, Tenant  # noqa: E402
from utilities import constants as c  # noqa: E402

DRY_RUN = os.environ.get("DRY_RUN") == "1"
PMC_ID = 3
PASSWORD = "TestFin#2026"
RESULTS = []


def R(sec, check, ok, detail=""):
    RESULTS.append(ok)
    print("R " + json.dumps({"sec": sec, "check": check, "ok": bool(ok), "detail": str(detail)[:600]}))


def aware(y, m, d):
    return timezone.make_aware(datetime(y, m, d))


def day(value):
    return value.date() if hasattr(value, "date") else value


def expect_error(sec, check, exc_type, func, code=None, contains=None):
    try:
        with transaction.atomic():
            func()
    except exc_type as exc:
        ok = (code is None or getattr(exc, "code", None) == code) and (contains is None or contains in str(exc))
        R(sec, check, ok, f"{exc_type.__name__}({getattr(exc, 'code', '')}): {exc}")
    else:
        R(sec, check, False, "no error raised")


def rent(lease, n):
    return lease.installments.get(installment_type=c.INSTALLMENT_TYPE_RENT, installment_no=n)


def of_type(lease, kind):
    return lease.installments.get(installment_type=kind)


def cheques(lease):
    return list(lease.lease_cheques.filter(is_payment=True, allocations__isnull=False, attempt_no=1, replaces__isnull=True)
                .distinct().order_by("cheque_date", "id"))


def split(cheque):
    return [(a.installment.installment_type, a.installment.installment_no, a.amount)
            for a in cheque.allocations.select_related("installment").order_by("sequence", "id")]


def reload(obj):
    obj.refresh_from_db()
    return obj


def expected_cheque_plan(items, cheque_amounts, cheque_dates):
    """Independent model of the equal-cheque layout: fill cheques in pay order.
    items = [(key, amount)]. Returns ({key: first cheque date}, [[(key, amount), ...] per cheque])."""
    queue = [[k, D(a)] for k, a in items]
    plan, first = [], {}
    for amount, when in zip(cheque_amounts, cheque_dates):
        room, rows = D(amount), []
        while room > 0 and queue:
            take = min(room, queue[0][1])
            rows.append((queue[0][0], take))
            first.setdefault(queue[0][0], when)
            room -= take
            queue[0][1] -= take
            if queue[0][1] == 0:
                queue.pop(0)
        plan.append(rows)
    return first, plan


def check_cheque_layout(sec, lease, items, count, first_date, months_step=1):
    """Cheque amounts, allocations and invoice dates against the independent model."""
    grand = sum((D(a) for _, a in items), D("0"))
    each = (grand / count).quantize(D("0.01"))
    amounts = [each] * (count - 1) + [grand - each * (count - 1)]
    dates = [first_date + relativedelta_months(i * months_step) for i in range(count)]
    first, plan = expected_cheque_plan(items, amounts, dates)
    actual = cheques(lease)
    R(sec, f"{lease.code}: {count} equal cheques for the grand total {grand}",
      [ch.total for ch in actual] == amounts and [day(ch.cheque_date) for ch in actual] == dates,
      [(day(ch.cheque_date), ch.total) for ch in actual])
    got = [[(f"{t}{n}", a) for t, n, a in split(ch)] for ch in actual]
    want = [[(k, a) for k, a in rows] for rows in plan]
    R(sec, f"{lease.code}: every cheque's allocations match the pay-order model", got == want, got[:3])
    R(8, f"{lease.code}: no cheque allocated above its face value",
      all(allocated_on(ch) <= ch.total for ch in lease.lease_cheques.filter(allocations__isnull=False)))
    wrong = []
    for inst in lease.installments.exclude(installment_type=c.INSTALLMENT_TYPE_DEPOSIT):
        inv = Invoice.objects.filter(installment=inst).first()
        key = f"{inst.installment_type}{inst.installment_no}"
        if inv is None or key not in first:
            continue
        if not (inv.invoice_date == inst.due_date == inst.invoice_date == first[key]):
            wrong.append((key, inv.invoice_date, inst.due_date, first[key]))
    R(4, f"{lease.code}: invoice date = due date = date of the first cheque paying it (not the generation date)",
      not wrong, wrong or "all match")
    return actual


def relativedelta_months(n):
    from dateutil.relativedelta import relativedelta
    return relativedelta(months=n)


def issue_due(lease, today):
    issued = []
    for inv in Invoice.objects.filter(lease=lease, status=Invoice.SCHEDULED, invoice_date__lte=today).order_by("invoice_date", "id"):
        issued.append(issue_invoice(inv.id))
    return [i for i in issued if i]


def activate(lease, user, today):
    lease.handover_completed_at = timezone.now()
    lease.lease_stage = c.ACTIVATED
    lease.lease_status = c.ACTIVE
    lease.save()
    set_unit_status(lease, c.UNIT_ACTIVE)
    on_lease_activated(lease, user)
    return issue_due(lease, today)


def bank(cheque, account, user, deposit_on=None, clear_after=2):
    deposit_on = deposit_on or day(cheque.cheque_date)
    deposit_cheque(cheque.id, deposit_account=account, deposit_date=deposit_on, user=user)
    clear_cheque(cheque.id, clearing_date=deposit_on + timedelta(days=clear_after), user=user)
    return reload(cheque)


# ─────────────────────────────────────────────────────────────────────────────

def run():
    TODAY = business_today()
    R(0, "business date is 2026-09-29 (expected figures assume it)", TODAY == date(2026, 9, 29), TODAY)
    pmc = PropertyManagmentCompany.objects.get(pk=PMC_ID)
    R(2, "PMC 3 is PMD", pmc.name == "PMD", pmc.name)

    # ── Users: maker + checker property managers with every finance right ──
    def pm_user(email, first, last):
        user = User.objects.create_user(username=email, email=email, first_name=first, last_name=last, password=PASSWORD)
        pm = PropertyManager.objects.create(user=user, email=email, company=pmc, created_by=user, status="ACTIVE")
        PMCPMMapping.objects.create(pmc=pmc, pm=pm, created_by=user)
        return user, pm

    maker, maker_pm = pm_user("testfin.maker@units.local", "TEST-FIN", "Maker")
    checker, checker_pm = pm_user("testfin.checker@units.local", "TEST-FIN", "Checker")
    grant_finance_role(maker_pm, created_by=maker, name="TEST-FIN Finance Admin")
    grant_finance_role(checker_pm, created_by=maker, name="TEST-FIN Finance Checker")

    # ── Masters: settings, bank account, charges, return reason ──
    FinanceSettings.objects.update_or_create(pmc=pmc, defaults={"max_cheque_returns": 2, "created_by": maker,
                                                                "company_trn": "100000000000003"})
    account = CompanyBankAccount.objects.create(pmc=pmc, account_name="TEST-FIN Operating", account_number="TESTFIN-001",
                                                iban="AE000000TESTFIN0001", clearing_days=2, created_by=maker)
    country = Country.objects.get(pk=1)

    def charge(desc, ctype, calc=c.CHARGE_CALC_FIXED, **kw):
        return Charge.objects.create(description=desc, country=country, charge_type=ctype, calc_type=calc, pmc=pmc,
                                     created_by=maker, **kw)

    cash = PaymentMethod.objects.get(pmc=None, code="CASH")
    card = PaymentMethod.objects.get(pmc=None, code="CREDIT_CARD")
    gateway = PaymentMethod.objects.get(pmc=None, code="PAYMENT_GATEWAY")
    transfer = PaymentMethod.objects.get(pmc=None, code="BANK_TRANSFER")
    cheque_method = PaymentMethod.objects.get(pmc=None, code="CHEQUE")
    return_charge = charge("TEST-FIN Cheque Return Charge", c.CHARGE_TYPE_CHEQUE_RETURN, amount=D("200"), tax_code=D("5"))
    charge("TEST-FIN Card Fee 2%", c.CHARGE_TYPE_PAYMENT_CHANNEL, c.CHARGE_CALC_PERCENT, rate=D("2"), tax_code=D("5"),
           payment_method=card)
    charge("TEST-FIN Gateway Fee", c.CHARGE_TYPE_PAYMENT_CHANNEL, amount=D("10"), tax_code=D("5"), payment_method=gateway)
    charge("TEST-FIN Termination Penalty", c.CHARGE_TYPE_TERMINATION_PENALTY, c.CHARGE_CALC_MONTHS_OF_RENT,
           months=D("2"), tax_code=D("5"))
    admin_charge = charge("TEST-FIN Administrative Charge", c.CHARGE_TYPE_MISCELLANEOUS, amount=D("500"),
                          tax_code=D("5"), is_editable=False)
    other_charge = charge("TEST-FIN Other Charge", c.CHARGE_TYPE_MISCELLANEOUS, amount=D("300"), tax_code=D("5"))
    breakage = charge("TEST-FIN Breakage / Damage Repair", c.CHARGE_TYPE_MISCELLANEOUS, amount=D("750"), tax_code=D("5"))
    service_charge = charge("TEST-FIN Owner Service Charge", c.CHARGE_TYPE_MISCELLANEOUS, amount=D("5000"),
                            tax_code=D("5"), is_editable=False)
    agreement = charge("TEST-FIN Agreement Charge", c.CHARGE_TYPE_AGREEMENT, amount=D("1000"), tax_code=D("0"))
    reason = PDCReturnReason.objects.create(pmc=pmc, code="TFIN_NSF", reason="TEST-FIN Insufficient funds",
                                            charge=return_charge, created_by=maker)

    # ── Property, block, units ──
    prop = Property.objects.create(property_name="TEST-FIN-PROP Finance Test Tower", no_of_blocks=1, no_of_units=5,
                                   address_line_1="TEST-FIN Street 1", address_line_2="", landmark="TEST-FIN",
                                   pincode="00000", pmc=pmc, created_by=maker)
    block = PropertyBlocks.objects.create(property=prop, block_name="TEST-FIN-BLK A", no_of_floors=5, no_of_parking=5,
                                          no_of_units=5, created_by=maker)

    def unit(code, usage, management, rent_amount, **kw):
        return Unit.objects.create(unit_name=code, parent_property=prop, property_block_tower=block, unit_usage=usage,
                                   management_type=management, rent=D(rent_amount), cycle="12", created_by=maker,
                                   status=c.UNIT_AVAILABLE, **kw)

    u1 = unit("TEST-FIN-001", c.COMMERCIAL, c.UNIT_MANAGEMENT_INTERNAL, "5000", security_deposit=D("5000"),
              commission_amount=D("2000"))
    u2 = unit("TEST-FIN-002", c.RESIDENTIAL, c.UNIT_MANAGEMENT_MANAGED, "10000", security_deposit=D("5000"),
              commission_percent=D("5"))
    u3 = unit("TEST-FIN-003", c.COMMERCIAL, c.UNIT_MANAGEMENT_MANAGED, "100000", commission_percent=D("5"))
    u4 = unit("TEST-FIN-004", c.RESIDENTIAL, c.UNIT_MANAGEMENT_INTERNAL, "6000", security_deposit=D("10000"))
    u5 = unit("TEST-FIN-005", c.RESIDENTIAL, c.UNIT_MANAGEMENT_INTERNAL, "9750", security_deposit=D("2000"))

    def person(model, n, first, last, **kw):
        email = f"testfin.{model.__name__.lower()}{n}@units.local"
        user = User.objects.create_user(username=email, email=email, first_name=first, last_name=last, password=PASSWORD)
        obj = model.objects.create(user=user, email=email, created_by=maker, contact_number=f"05000000{n:02d}", **kw)
        obj.pmc.add(pmc)
        return obj

    owner_kw = lambda n, freq: dict(trade_license_number=f"TFIN-TL-{n}", license_issuer="TEST-FIN", license_number=f"TFIN-L-{n}",
                                    bank_account_name=f"TEST-FIN Owner {n}", bank_name="TEST-FIN Bank",
                                    iban=f"AE00TESTFINOWNER{n}", trn=f"TFIN-TRN-OWN-{n}", settlement_frequency=freq)
    o1 = person(Owner, 1, "TEST-FIN", "Owner One", **owner_kw(1, "QUARTERLY"))
    o2 = person(Owner, 2, "TEST-FIN", "Owner Two", **owner_kw(2, "MONTHLY"))
    o3 = person(Owner, 3, "TEST-FIN", "Owner Three", **owner_kw(3, "MONTHLY"))
    t = [person(Tenant, n, "TEST-FIN", f"Tenant {n}") for n in range(1, 6)]
    set_unit_owners(u2, [(o1, D("100"))], maker, effective_date=date(2025, 1, 1))
    set_unit_owners(u3, [(o2, D("60")), (o3, D("40"))], maker, effective_date=date(2025, 1, 1))
    R(2, "dataset: 1 property, 5 units (3 INTERNAL, 2 MANAGED), 3 owners, 5 tenants, 1 bank account, 9 charges",
      Unit.objects.filter(parent_property=prop).count() == 5 and Charge.objects.filter(pmc=pmc).count() == 9)

    def lease(u, tenant, start, end, **kw):
        return Lease.objects.create(unit=u, tenant=tenant, created_by=maker, start_date=aware(*start), end_date=aware(*end),
                                    lease_status="DRAFT", **kw)

    ctx = {"pmc_id": PMC_ID, "maker_user_id": maker.id, "checker_user_id": checker.id, "bank_account_id": account.id,
           "property_id": prop.id, "block_id": block.id, "units": [u1.id, u2.id, u3.id, u4.id, u5.id],
           "owners": [o1.id, o2.id, o3.id], "tenants": [x.id for x in t], "admin_charge": admin_charge.id,
           "service_charge": service_charge.id}

    # ══ Test Case A (§4-§7, §9-§10, §13): L1, INTERNAL commercial, 5,000/month, 12 months (Sep 2025 - Aug 2026) ══
    L1 = lease(u1, t[0], (2025, 9, 1), (2026, 8, 31), rent=D("5000"), contract_amount=D("60000"), annual_amount=D("60000"),
               discount=D("0"), payment_count=12, payment_frequency="MONTHLY", security_deposit=D("5000"),
               commission=D("2000"))
    generate_lease_schedule(L1, maker)
    rents = list(L1.installments.filter(installment_type=c.INSTALLMENT_TYPE_RENT).order_by("installment_no"))
    R(4, "L1: 12 rent installments of 5,000 net + 250 VAT (5% commercial) = 5,250",
      len(rents) == 12 and all((i.original_amount, i.discount_amount, i.net_amount, i.vat_rate, i.vat_amount, i.total_amount)
                               == (D("5000"), D("0"), D("5000"), D("5.000"), D("250"), D("5250")) for i in rents))
    R(4, "L1: gross 60,000 - discount 0 = net 60,000 over the installments",
      sum(i.original_amount for i in rents) == D("60000") and sum(i.net_amount for i in rents) == D("60000"))
    comm = of_type(L1, c.INSTALLMENT_TYPE_COMMISSION)
    R(7, "L1: one leasing commission 2,000 + VAT 5% 100 = 2,100, own invoice",
      (comm.net_amount, comm.vat_amount, comm.total_amount) == (D("2000"), D("100"), D("2100"))
      and L1.installments.filter(installment_type=c.INSTALLMENT_TYPE_COMMISSION).count() == 1
      and Invoice.objects.filter(installment=comm, invoice_type=Invoice.TYPE_COMMISSION).count() == 1)
    dep1 = of_type(L1, c.INSTALLMENT_TYPE_DEPOSIT)
    R(6, "L1: security deposit 5,000 installment, no VAT, no invoice",
      (dep1.total_amount, dep1.vat_amount) == (D("5000"), D("0")) and not Invoice.objects.filter(installment=dep1).exists())
    l1_items = [("COMMISSION1", "2100"), ("DEPOSIT1", "5000")] + [(f"RENT{n}", "5250") for n in range(1, 13)]
    l1_cheques = check_cheque_layout(4, L1, l1_items, 12, date(2025, 9, 1))
    R(4, "L1: rent 1 (Sep 2025) is invoiced on 1 Oct 2025, the date of cheque 2 -- the first cheque paying it",
      rent(L1, 1).invoice.invoice_date == date(2025, 10, 1), rent(L1, 1).invoice.invoice_date)
    R(3, "PDC in hand has no accounting event",
      not FinanceEvent.objects.filter(source_type="CHEQUE", source_id__in=[ch.id for ch in l1_cheques]).exists())
    expect_error(9, "cheque cannot be deposited before the lease is activated", ChequeActionError,
                 lambda: deposit_cheque(l1_cheques[0].id, deposit_account=account, deposit_date=date(2025, 9, 1), user=maker))
    issued = activate(L1, maker, TODAY)
    R(5, "L1 activated: 13 invoices issued (12 rent + commission), all dated on or before today",
      len(issued) == 13 and all(i.invoice_date <= TODAY for i in issued), len(issued))
    R(5, "INVOICE_ISSUED events carry net / VAT / total of the invoice",
      all(FinanceEvent.objects.get(idempotency_key=f"INVOICE:{i.id}:ISSUED").payload["total"] == str(i.total) for i in issued))
    # §9 deposit then §10 clear cheque 1 (commission + part of the deposit)
    ch1 = l1_cheques[0]
    before = split(ch1)
    deposit_cheque(ch1.id, deposit_account=account, deposit_date=date(2025, 9, 1), user=maker)
    reload(ch1)
    R(9, "deposited: status DEPOSITED, expected clearing 3 Sep (deposit + 2 clearing days), allocations intact",
      (ch1.status, ch1.expected_clearing_date, split(ch1)) == (c.CHEQUE_STATUS_DEPOSITED, date(2025, 9, 3), before))
    ev = FinanceEvent.objects.get(idempotency_key=f"CHEQUE:{ch1.id}:CHEQUE_DEPOSITED")
    R(9, "deposit event: one line per allocation (commission 2,100 to AR, 3,741.67 to deposit held)",
      [(a["installment_type"], a["amount"]) for a in ev.payload["allocations"]] == [("COMMISSION", "2100.00"), ("DEPOSIT", "3741.67")],
      ev.payload["allocations"])
    clear_cheque(ch1.id, clearing_date=date(2025, 9, 3), user=maker)
    reload(ch1)
    R(10, "cleared: CREDITED, commission settled, deposit part-paid 3,741.67, 2 receipts (one per allocation)",
      ch1.status == c.CHEQUE_STATUS_CREDITED and reload(comm).status == c.INSTALLMENT_STATUS_SETTLED
      and reload(dep1).paid_amount == D("3741.67") and Receipt.objects.filter(collection__cheque=ch1).count() == 2)
    expect_error(30, "clearing the same cheque twice is refused", ChequeActionError,
                 lambda: clear_cheque(ch1.id, clearing_date=date(2025, 9, 4), user=maker))
    for ch in l1_cheques[1:11]:
        bank(ch, account, maker)
    R(10, "L1 cheques 2-11 cleared; deposit settled 5,000; rents 1-10 settled; cheque 12 still in hand",
      reload(dep1).status == c.INSTALLMENT_STATUS_SETTLED and all(rent(L1, n).status == c.INSTALLMENT_STATUS_SETTLED for n in range(1, 11))
      and reload(l1_cheques[11]).status == c.CHEQUE_STATUS_BALANCE, [rent(L1, n).paid_amount for n in (10, 11, 12)])
    recognize_revenue(today=TODAY, lease=L1)
    l1_rev = FinanceEvent.objects.filter(event_type="REVENUE_RECOGNIZED", payload__dimensions__lease_id=L1.id)
    R(13, "L1 recognition: 12 monthly events of 5,000 (Sep 2025 - Aug 2026), dated month end, total 60,000",
      l1_rev.count() == 12 and sum(D(e.payload["amount"]) for e in l1_rev) == D("60000")
      and sorted(e.payload["entry_date"] for e in l1_rev)[0] == "2025-09-30" and sorted(e.payload["entry_date"] for e in l1_rev)[-1] == "2026-08-31",
      [(e.payload["month"], e.payload["amount"]) for e in l1_rev.order_by("id")][:3])
    R(30, "recognition job run again: no new events", recognize_revenue(today=TODAY, lease=L1) == [])
    R(30, "invoice job run again: nothing issued twice", issue_due(L1, TODAY) == [] and issue_invoice(rent(L1, 1).invoice.id) is None)
    ctx.update(L1=L1.id, L1_cheques=[ch.id for ch in l1_cheques], L1_rent1_invoice=rent(L1, 1).invoice.id,
               L1_commission_invoice=comm.invoice.id, L1_deposit=dep1.id)

    # ══ §23 Renewal of L1 (Sep 2026 - Aug 2027, 5,500/month) ══
    L1R = create_renewal(L1, {"start_date": aware(2026, 9, 1), "end_date": aware(2027, 8, 31), "contract_amount": "66000",
                              "payment_count": 12, "rent": "5500"}, maker)
    generate_lease_schedule(L1R, maker)
    rcomm = of_type(L1R, c.INSTALLMENT_TYPE_COMMISSION)
    R(23, "renewal: no new security deposit installment; deposit amount carried on the lease",
      not L1R.installments.filter(installment_type=c.INSTALLMENT_TYPE_DEPOSIT).exists() and L1R.security_deposit == D("5000"))
    R(23, "renewal commission = the initial lease's commission (2,000) + 5% VAT, charged once",
      (rcomm.net_amount, rcomm.vat_amount, rcomm.total_amount) == (D("2000"), D("100"), D("2100"))
      and L1R.installments.filter(installment_type=c.INSTALLMENT_TYPE_COMMISSION).count() == 1)
    l1r_items = [("COMMISSION1", "2100")] + [(f"RENT{n}", "5775") for n in range(1, 13)]
    l1r_cheques = check_cheque_layout(23, L1R, l1r_items, 12, date(2026, 9, 1))
    activate(L1R, maker, TODAY)
    R(23, "renewal activated: old lease RENEWED, deposit transfer event 5,000 old lease -> renewal",
      reload(L1).lease_status == "RENEWED" and FinanceEvent.objects.filter(
          idempotency_key=f"LEASE:{L1R.id}:DEPOSIT_TRANSFERRED", payload__amount="5000.00").exists())
    R(23, "renewal commission invoice is flagged as renewal (own income account)",
      FinanceEvent.objects.get(idempotency_key=f"INVOICE:{rcomm.invoice.id}:ISSUED").payload["is_renewal"] is True)
    bank(l1r_cheques[0], account, maker)
    ctx.update(L1R=L1R.id, L1R_cheques=[ch.id for ch in l1r_cheques], L1R_commission_invoice=rcomm.invoice.id)

    # ══ §15/§16 L2: MANAGED residential, gross 120,000 - discount 12,000, 4 quarterly installments ══
    L2 = lease(u2, t[1], (2026, 1, 1), (2026, 12, 31), rent=D("10000"), contract_amount=D("120000"),
               annual_amount=D("120000"), discount=D("12000"), payment_count=4, payment_frequency="QUARTERLY",
               security_deposit=D("5000"), commission=D("6000"), commission_percent=D("5"))
    generate_lease_schedule(L2, maker)
    r2 = list(L2.installments.filter(installment_type=c.INSTALLMENT_TYPE_RENT).order_by("installment_no"))
    R(15, "L2: 120,000 gross - 12,000 discount = 108,000 net; 4 x (30,000 - 3,000 = 27,000), residential VAT 0",
      [(i.original_amount, i.discount_amount, i.net_amount, i.vat_amount) for i in r2] == [(D("30000"), D("3000"), D("27000"), D("0"))] * 4
      and sum(i.discount_amount for i in r2) == D("12000"), [(i.original_amount, i.discount_amount, i.net_amount) for i in r2])
    R(15, "L2: discount kept on the invoices (subtotal 30,000, discount 3,000, net 27,000)",
      all((i.invoice.subtotal, i.invoice.discount, i.invoice.net_amount) == (D("30000"), D("3000"), D("27000")) for i in r2))
    R(7, "L2: commission 5% x 12 x 10,000 = 6,000 + 300 VAT", of_type(L2, c.INSTALLMENT_TYPE_COMMISSION).total_amount == D("6300"))
    l2_items = [("COMMISSION1", "6300"), ("DEPOSIT1", "5000")] + [(f"RENT{n}", "27000") for n in range(1, 5)]
    l2_cheques = check_cheque_layout(14, L2, l2_items, 4, date(2026, 1, 1), months_step=3)
    activate(L2, maker, TODAY)
    for ch in l2_cheques[:3]:
        bank(ch, account, maker)
    recognize_revenue(today=TODAY, lease=L2)
    ev2 = FinanceEvent.objects.filter(event_type="REVENUE_RECOGNIZED", payload__dimensions__lease_id=L2.id)
    R(14, "quarterly installment of 27,000 net recognised 9,000 x 3 months, no day proration (Jan-Aug = 8 x 9,000)",
      ev2.count() == 8 and all(e.payload["amount"] == "9000.00" and e.payload["management_type"] == "MANAGED" for e in ev2),
      [(e.payload["month"], e.payload["amount"]) for e in ev2.order_by("id")])
    ctx.update(L2=L2.id, L2_cheques=[ch.id for ch in l2_cheques], L2_rent1_invoice=r2[0].invoice.id)

    # ══ §19 L3: MANAGED commercial, 100,000/month, no deposit or commission (one cheque -> one installment) ══
    L3 = lease(u3, t[2], (2026, 7, 1), (2027, 6, 30), rent=D("100000"), contract_amount=D("1200000"),
               annual_amount=D("1200000"), discount=D("0"), payment_count=12, payment_frequency="MONTHLY",
               security_deposit=D("0"), commission=D("0"))
    generate_lease_schedule(L3, maker)
    l3_cheques = check_cheque_layout(8, L3, [(f"RENT{n}", "105000") for n in range(1, 13)], 12, date(2026, 7, 1))
    R(8, "scenario 1 -- one cheque pays one installment (105,000 each)",
      all(len(split(ch)) == 1 and ch.total == D("105000") for ch in l3_cheques))
    activate(L3, maker, TODAY)
    for ch in l3_cheques[:3]:
        bank(ch, account, maker)
    recognize_revenue(today=TODAY, lease=L3)
    ctx.update(L3=L3.id, L3_cheques=[ch.id for ch in l3_cheques])

    # ══ §8 L5: INTERNAL residential -- one 10,000 cheque pays charge 1,000 + deposit 2,000 + rent 7,000 ══
    L5 = lease(u5, t[4], (2026, 1, 1), (2026, 12, 31), rent=D("9750"), contract_amount=D("117000"), annual_amount=D("117000"),
               discount=D("0"), payment_count=12, payment_frequency="MONTHLY", security_deposit=D("2000"), commission=D("0"))
    doc_type, _ = DocumentType.objects.get_or_create(name=c.RENT_CHEQUE, section=c.LEASE_CHEQUE, defaults={"created_by": maker})
    LeaseTransaction.objects.create(lease=L5, cheque_type=c.OTHER_CHARGE, charge=agreement, amount=D("1000"),
                                    description=agreement.description, status=c.CHEQUE_STATUS_BALANCE, document_type=doc_type,
                                    file_name="", file_path="", created_by=maker)
    generate_lease_schedule(L5, maker)
    l5_items = [("OTHER_CHARGE1", "1000"), ("DEPOSIT1", "2000")] + [(f"RENT{n}", "9750") for n in range(1, 13)]
    l5_cheques = check_cheque_layout(8, L5, l5_items, 12, date(2026, 1, 1))
    c1 = l5_cheques[0]
    R(8, "scenario 2 -- one cheque (10,000) pays charge 1,000 + deposit 2,000 + rent 7,000",
      split(c1) == [("OTHER_CHARGE", 1, D("1000")), ("DEPOSIT", 1, D("2000")), ("RENT", 1, D("7000"))], split(c1))
    r5_1 = rent(L5, 1)
    R(8, "scenario 3 -- one item (rent 1, 9,750) paid by two cheques (7,000 + 2,750)",
      sorted(a.amount for a in r5_1.allocations.all()) == [D("2750"), D("7000")])
    R(8, "scenario 4 -- cheque 2 pays two items (rent 1 2,750 + rent 2 7,250)",
      split(l5_cheques[1]) == [("RENT", 1, D("2750")), ("RENT", 2, D("7250"))], split(l5_cheques[1]))
    activate(L5, maker, TODAY)
    # §11 return the multi-item cheque 1
    deposit_cheque(c1.id, deposit_account=account, deposit_date=date(2026, 1, 1), user=maker)
    returned, note = return_cheque(c1.id, reason=reason, return_date=date(2026, 1, 3), user=maker)
    items = {a.installment.installment_type: reload(a.installment) for a in returned.allocations.select_related("installment")}
    R(11, "whole cheque returned: BOUNCED, return #1, all 3 allocations kept",
      (returned.status, returned.return_count, returned.allocations.count()) == (c.CHEQUE_STATUS_BOUNCED, 1, 3))
    R(11, "every item the cheque covered is unpaid again (charge, deposit, rent 1 paid 0)",
      all(i.paid_amount == 0 and i.status != c.INSTALLMENT_STATUS_SETTLED for i in items.values()),
      [(k, i.paid_amount, i.status) for k, i in items.items()])
    R(11, "return charge: separate debit note 200 + 5% VAT = 210 (not added to the replacement)",
      note is not None and (note.net_amount, note.vat_amount, note.total) == (D("200"), D("10"), D("210")), note)
    ret_ev = FinanceEvent.objects.get(idempotency_key=f"CHEQUE:{c1.id}:CHEQUE_RETURNED")
    R(11, "return event reverses each allocation (charge 1,000, deposit 2,000, rent 7,000)",
      [(a["installment_type"], a["amount"]) for a in ret_ev.payload["allocations"]]
      == [("OTHER_CHARGE", "1000.00"), ("DEPOSIT", "2000.00"), ("RENT", "7000.00")])
    expect_error(11, "after return #1 a non-cheque payment is refused (replacement required)", CollectionError,
                 lambda: quote(r5_1, cash), "replacement_required")
    repl = replace_cheque(c1.id, user=maker, cheque_number="TFIN-005-R1", cheque_date=aware(2026, 1, 5))
    R(12, "replacement covers the same items for the same amounts, linked to the returned cheque",
      (repl.replaces_id, repl.total, split(repl)) == (c1.id, D("10000"), split(c1)), split(repl))
    bank(repl, account, maker, deposit_on=date(2026, 1, 5))
    R(12, "replacement cleared: charge, deposit settled; rent 1 paid 7,000 -- once, no duplicate",
      [reload(i).paid_amount for i in items.values()] == [D("1000"), D("2000"), D("7000")])
    q = quote(note.installment, cash)
    R(21, "cash: return charge 210, no channel charge", (q["outstanding"], q["charge_amount"], q["total"]) == (D("210"), D("0"), D("210")), q)
    settle(note.installment.id, cash, expected_total=q["total"], user=maker, collection_date=date(2026, 1, 10), reference="TFIN-CASH-1")
    for ch in l5_cheques[1:8]:
        bank(ch, account, maker)
    c9 = l5_cheques[8]
    deposit_cheque(c9.id, deposit_account=account, deposit_date=date(2026, 9, 1), user=maker)
    plan = preview(L5, date(2026, 9, 15))
    R(22, "termination preview lists the cheque in clearing as a blocker", bool(plan["blockers"]), plan["blockers"])
    expect_error(22, "termination blocked while a cheque is deposited and not cleared / returned", TerminationError,
                 lambda: confirm(L5.id, termination_date=date(2026, 9, 15), reason="TEST-FIN", expected_net=D(plan["net"]),
                                 user=maker, today=TODAY), "cheque_in_clearing")
    clear_cheque(c9.id, clearing_date=date(2026, 9, 3), user=maker)
    recognize_revenue(today=TODAY, lease=L5)
    ctx.update(L5=L5.id, L5_cheques=[ch.id for ch in l5_cheques], L5_c1=c1.id, L5_repl=repl.id,
               L5_return_note=note.id, L5_rent1=r5_1.id)

    # ══ §11 L4: INTERNAL residential with discount; cheque chain return #1 -> replacement -> return #2 -> blocked ══
    L4 = lease(u4, t[3], (2026, 3, 1), (2027, 2, 28), rent=D("6000"), contract_amount=D("72000"), annual_amount=D("72000"),
               discount=D("7200"), payment_count=12, payment_frequency="MONTHLY", security_deposit=D("10000"), commission=D("0"))
    generate_lease_schedule(L4, maker)
    r4 = list(L4.installments.filter(installment_type=c.INSTALLMENT_TYPE_RENT).order_by("installment_no"))
    R(15, "L4 (INTERNAL): 72,000 - 7,200 = 64,800; each 6,000 - 600 = 5,400",
      all((i.original_amount, i.discount_amount, i.net_amount) == (D("6000"), D("600"), D("5400")) for i in r4))
    l4_items = [("DEPOSIT1", "10000")] + [(f"RENT{n}", "5400") for n in range(1, 13)]
    l4_cheques = check_cheque_layout(8, L4, l4_items, 12, date(2026, 3, 1))
    activate(L4, maker, TODAY)
    for ch in l4_cheques[:6]:
        bank(ch, account, maker)
    c7 = l4_cheques[6]
    R(8, "L4 cheque 7 pays the rest of rent 6 (5,000.02) and part of rent 7 (1,233.31)",
      split(c7) == [("RENT", 6, D("5000.02")), ("RENT", 7, D("1233.31"))], split(c7))
    deposit_cheque(c7.id, deposit_account=account, deposit_date=date(2026, 9, 1), user=maker)
    c7, note1 = return_cheque(c7.id, reason=reason, return_date=date(2026, 9, 3), user=maker)
    R(11, "return #1: count 1, replacement allowed", c7.return_count == 1 and not rent(L4, 6).non_cheque_required)
    r1 = replace_cheque(c7.id, user=maker, cheque_number="TFIN-004-R1", cheque_date=aware(2026, 9, 5))
    R(12, "replacement 1 covers rent 6 5,000.02 + rent 7 1,233.31", split(r1) == split(c7), split(r1))
    deposit_cheque(r1.id, deposit_account=account, deposit_date=date(2026, 9, 5), user=maker)
    r1, note2 = return_cheque(r1.id, reason=reason, return_date=date(2026, 9, 8), user=maker)
    R(11, "return #2 of the chain: count 2, both items now need a non-cheque payment",
      r1.return_count == 2 and rent(L4, 6).non_cheque_required and rent(L4, 7).non_cheque_required,
      (r1.return_count, rent(L4, 6).cheque_return_count))
    expect_error(11, "3rd cheque blocked: replacement of the returned replacement", ChequeActionError,
                 lambda: replace_cheque(r1.id, user=maker, cheque_number="TFIN-004-R2", cheque_date=aware(2026, 9, 9)))
    expect_error(11, "3rd cheque blocked: new cheque for the item", ChequeActionError,
                 lambda: add_replacement_cheque(rent(L4, 6).id, user=maker, cheque_number="TFIN-004-R3", cheque_date=aware(2026, 9, 9)))
    expect_error(11, "3rd cheque blocked: direct cheque record", ChequeNotAllowedError,
                 lambda: LeaseTransaction(lease=L4, installment=rent(L4, 6), attempt_no=3, payment_type=c.PAYMENT_TYPE_CHEQUE,
                                          created_by=maker).save())
    expect_error(11, "3rd cheque blocked: cheque as a collection method", CollectionError,
                 lambda: quote(rent(L4, 6), cheque_method), "cheque_method")
    q = quote(rent(L4, 6), transfer)
    R(21, "bank transfer quote: 5,000.02, no channel charge", q["total"] == D("5000.02"), q)
    settle(rent(L4, 6).id, transfer, expected_total=q["total"], user=maker, collection_date=date(2026, 9, 10),
           settlement_account=account, reference="TFIN-TT-004")
    q = quote(rent(L4, 7), gateway)
    R(21, "gateway quote: 1,233.31 (rest is on the cheque in hand) + fee 10 + VAT 0.50 = 1,243.81",
      (q["outstanding"], q["charge_amount"], q["charge_vat"], q["total"]) == (D("1233.31"), D("10"), D("0.50"), D("1243.81")), q)
    expect_error(21, "stale quote refused", CollectionError,
                 lambda: settle(rent(L4, 7).id, gateway, expected_total=D("1233.31"), user=maker), "quote_changed")
    settle(rent(L4, 7).id, gateway, expected_total=q["total"], user=maker, collection_date=date(2026, 9, 10), reference="TFIN-PG-004")
    q = quote(note1.installment, card)
    R(21, "card quote: 210 + 2% fee 4.20 + VAT 0.21 = 214.41",
      (q["charge_amount"], q["charge_vat"], q["total"]) == (D("4.20"), D("0.21"), D("214.41")), q)
    settle(note1.installment.id, card, expected_total=q["total"], user=maker, collection_date=date(2026, 9, 4), reference="TFIN-CARD-004")
    q = quote(note2.installment, cash)
    settle(note2.installment.id, cash, expected_total=q["total"], user=maker, collection_date=date(2026, 9, 9), reference="TFIN-CASH-004")
    recognize_revenue(today=TODAY, lease=L4)
    ctx.update(L4=L4.id, L4_cheques=[ch.id for ch in l4_cheques], L4_c7=c7.id, L4_r1=r1.id)

    # ══ §22 Terminations ══
    expect_error(22, "termination date after today refused", TerminationError,
                 lambda: confirm(L1R.id, termination_date=TODAY + timedelta(days=1), reason="TEST-FIN", expected_net=D("0"),
                                 user=maker, today=TODAY), "future_date")
    expect_error(22, "termination date in a previous month refused", TerminationError,
                 lambda: confirm(L1R.id, termination_date=date(2026, 8, 31), reason="TEST-FIN", expected_net=D("0"),
                                 user=maker, today=TODAY), "past_month")
    # L1R: renewed lease, 25 Sep; penalty 11,000 with 1,000 waived; admin + other + breakage (edited to 900)
    terms = dict(charges=[{"charge_id": admin_charge.id}, {"charge_id": other_charge.id}, {"charge_id": breakage.id, "amount": "900"}],
                 waived_amount="1000")
    p1 = preview(L1R, date(2026, 9, 25), **terms)
    R(22, "L1R penalty = 2 x Lease.rent 5,500 = 11,000; 1,000 waived; VAT on 10,000 = 500",
      (p1["penalty"]["calculated"], p1["penalty"]["waived"], p1["penalty"]["final"], p1["penalty"]["vat"]) == ("11000.00", "1000.00", "10000.00", "500.00"),
      p1["penalty"])
    R(22, "L1R misc charges: admin 525, other 315, breakage 945 (edited)",
      [c_["total"] for c_ in p1["charges"]] == ["525.00", "315.00", "945.00"], p1["charges"])
    own_ids = set(L1R.installments.values_list("id", flat=True))
    R(22, "L1R: only the terminated lease's items are in the settlement (previous lease's unpaid rent 12 excluded)",
      all(i["installment_id"] in own_ids for i in p1["installments"]) and rent(L1, 12).status != c.INSTALLMENT_STATUS_SETTLED,
      [(i["type"], i["installment_id"]) for i in p1["installments"]])
    # earned: Sep 5,500 x 25/30 = 4,583.33 + VAT 229.17 = 4,812.50; paid 3,850 on rent 1; deposit held 5,000
    R(22, "L1R: earned 4,812.50, collected 3,850, deposit 5,000 -> tenant owes 8,247.50",
      (p1["rent_earned"], p1["rent_collected"], p1["deposit_held"], p1["net"], p1["outcome"]) == ("4812.50", "3850.00", "5000.00", "-8247.50", "DUE"),
      {k: p1[k] for k in ("rent_earned", "rent_collected", "deposit_held", "other_dues", "net", "outcome")})
    R(22, "L1R breakdown lines add up to the final balance",
      sum(D(line["amount"]) for line in p1["lines"]) == D("8247.50"), [(l_["kind"], l_["amount"]) for l_ in p1["lines"]])
    tr1 = confirm(L1R.id, termination_date=date(2026, 9, 25), reason="TEST-FIN early exit", expected_net=D(p1["net"]), user=maker,
                  waiver_reason="TEST-FIN goodwill", today=TODAY, **terms)
    R(22, "waiver audited beside the calculation (calculated 11,000, waived 1,000, by maker)",
      (tr1.penalty_calculated, tr1.penalty_waived, tr1.waived_by_id) == (D("11000"), D("1000"), maker.id))
    R(22, "L1R terminated, unit available; previous lease's cheque 12 untouched (still in hand)",
      reload(L1R).lease_status == c.TERMINATED and reload(u1).status == c.UNIT_AVAILABLE
      and LeaseTransaction.objects.get(pk=l1_cheques[11].id).status == c.CHEQUE_STATUS_BALANCE)
    bal1 = tr1.balance_installment
    q = quote(bal1, transfer)
    settle(bal1.id, transfer, expected_total=q["total"], user=maker, collection_date=TODAY, settlement_account=account, reference="TFIN-TT-L1R")
    R(22, "L1R balance due 8,247.50 paid by bank transfer", reload(bal1).status == c.INSTALLMENT_STATUS_SETTLED and q["total"] == D("8247.50"))

    # L4: 20 Sep, penalty 12,000 fully waived, other charge 300 -> refund
    p4 = preview(L4, date(2026, 9, 20), charges=[{"charge_id": other_charge.id}], waived_amount="12000")
    R(22, "L4 penalty 12,000 (2 x 6,000) fully waived", (p4["penalty"]["calculated"], p4["penalty"]["total"]) == ("12000.00", "0.00"), p4["penalty"])
    R(22, "L4 refund = 10,000 deposit + 33,633.31 paid - 36,000 earned - 315 charge = 7,318.31",
      (p4["rent_collected"], p4["rent_earned"], p4["net"], p4["outcome"]) == ("33633.31", "36000.00", "7318.31", "REFUND"),
      {k: p4[k] for k in ("deposit_held", "rent_collected", "rent_earned", "other_dues", "charges_total", "net")})
    tr4 = confirm(L4.id, termination_date=date(2026, 9, 20), reason="TEST-FIN relocation", expected_net=D(p4["net"]), user=maker,
                  charges=[{"charge_id": other_charge.id}], waived_amount="12000", waiver_reason="TEST-FIN hardship", today=TODAY)
    R(22, "L4: undeposited PDCs returned to tenant (cheques 8-12)",
      LeaseTransaction.objects.filter(pk__in=[ch.id for ch in l4_cheques[7:]], status=c.CHEQUE_STATUS_RETURNED_TO_TENANT).count() == 5)
    refund = create_refund(tr4, bank_account=account, refund_date=TODAY, user=maker, today=TODAY)
    expect_error(22, "refund cannot be approved by its maker", Exception, lambda: approve_refund(refund.id, maker))
    refund = approve_refund(refund.id, checker)
    R(22, "L4 refund: one payment of the full final amount 7,318.31, posted",
      (refund.status, refund.amount) == ("POSTED", D("7318.31")) and tr4.refunds.count() == 1 if hasattr(tr4, "refunds") else refund.amount == D("7318.31"))

    # L5: 15 Sep, penalty 19,500 + admin + breakage -> balance due paid by cheque
    p5 = preview(L5, date(2026, 9, 15), charges=[{"charge_id": admin_charge.id}, {"charge_id": breakage.id}])
    R(22, "L5 balance due = 2,000 + 87,000 - 82,875 - 20,475 - 1,312.50 = 15,662.50",
      (p5["rent_collected"], p5["rent_earned"], p5["penalty"]["total"], p5["charges_total"], p5["net"])
      == ("87000.00", "82875.00", "20475.00", "1312.50", "-15662.50"),
      {k: p5[k] for k in ("deposit_held", "rent_collected", "rent_earned", "other_dues", "charges_total", "net")})
    tr5 = confirm(L5.id, termination_date=date(2026, 9, 15), reason="TEST-FIN breach", expected_net=D(p5["net"]), user=maker,
                  charges=[{"charge_id": admin_charge.id}, {"charge_id": breakage.id}], today=TODAY)
    bal5 = tr5.balance_installment
    bal_cheque = add_replacement_cheque(bal5.id, user=maker, cheque_number="TFIN-005-BAL", cheque_date=aware(2026, 9, 27))
    R(22, "L5 balance due accepted as a cheque (15,662.50, one allocation)",
      bal_cheque.total == D("15662.50") and split(bal_cheque) == [("TERMINATION_BALANCE", bal5.installment_no, D("15662.50"))], split(bal_cheque))
    bank(bal_cheque, account, maker, deposit_on=date(2026, 9, 27))
    R(22, "L5 balance cheque cleared: balance settled", reload(bal5).status == c.INSTALLMENT_STATUS_SETTLED)
    ctx.update(T_L1R=tr1.id, T_L4=tr4.id, T_L5=tr5.id, refund=refund.id, L5_bal_cheque=bal_cheque.id)

    # ── §30 backend idempotency: jobs again ──
    again = sum(len(recognize_revenue(today=TODAY, lease=L)) + len(issue_due(L, TODAY)) for L in (L1, L1R, L2, L3, L4, L5))
    R(30, "all jobs re-run for every lease: nothing new emitted", again == 0, again)
    keys = list(FinanceEvent.objects.filter(pmc_id=PMC_ID).values_list("idempotency_key", flat=True))
    R(29, "one accounting event per business event (unique idempotency keys)", len(keys) == len(set(keys)), len(keys))
    print("CTX " + json.dumps(ctx))
    return ctx


class Rollback(Exception):
    pass


try:
    with transaction.atomic():
        CTX = run()
        if DRY_RUN:
            raise Rollback()
except Rollback:
    print("DRY RUN -- rolled back")
else:
    pending = list(FinanceEvent.objects.filter(pmc_id=PMC_ID, status=FinanceEvent.PENDING).order_by("id").values_list("id", flat=True))
    for event_id in pending:
        fe.deliver(event_id)
    bad = FinanceEvent.objects.filter(pmc_id=PMC_ID).exclude(status=FinanceEvent.POSTED)
    R(1, f"all {len(pending)} accounting events delivered and POSTED in units-finance", not bad.exists(),
      [(e.id, e.event_type, e.status, e.last_error[:120]) for e in bad[:10]])
print(f"SUMMARY {sum(RESULTS)}/{len(RESULTS)} passed")
