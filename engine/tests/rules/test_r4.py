from datetime import date

from countercharge_engine.models import Bill, CodeType, LineItem, Provider, Setting, Totals
from countercharge_engine.refdata.memory import MemoryRefData
from countercharge_engine.rules.common import AuditContext
from countercharge_engine.rules.r4_arithmetic import RULE_ID, check

DOS = date(2026, 3, 1)


def _line(line_id, charge):
    return LineItem(
        line_id=line_id,
        dos=DOS,
        code="99213",
        code_type=CodeType.CPT,
        units=1,
        charge_cents=charge,
    )


def _ctx(lines, totals):
    bill = Bill(
        provider=Provider(name="Test Hospital"),
        account_no="A1",
        statement_date=DOS,
        setting=Setting.OUTPATIENT,
        lines=lines,
        totals=totals,
    )
    return AuditContext(
        bill=bill,
        eob=None,
        household=None,
        gfe=None,
        refdata=MemoryRefData(),
        as_of=bill.statement_date,
    )


def test_consistent_bill_no_findings():
    lines = [_line("l1", 10000), _line("l2", 5000)]
    totals = Totals(charges_cents=15000, adjustments_cents=3000, payments_cents=2000, patient_balance_cents=10000)

    assert check(_ctx(lines, totals)) == []


def test_overstated_total_fires_with_difference():
    lines = [_line("l1", 10000), _line("l2", 5000)]
    totals = Totals(
        charges_cents=15000 + 12000, adjustments_cents=0, payments_cents=0,
        patient_balance_cents=15000 + 12000,
    )

    findings = check(_ctx(lines, totals))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == RULE_ID
    assert finding.disputable is True
    assert finding.amount_cents == 12000
    assert finding.title == "Charges don't add up"


def test_understated_total_no_finding():
    lines = [_line("l1", 10000), _line("l2", 5000)]
    totals = Totals(charges_cents=15000 - 12000, patient_balance_cents=15000 - 12000)

    assert check(_ctx(lines, totals)) == []


def test_balance_overstated_fires_with_difference():
    lines = [_line("l1", 10000)]
    totals = Totals(
        charges_cents=10000, adjustments_cents=2000, payments_cents=3000,
        patient_balance_cents=10000,
    )
    # expected balance = 10000 - 2000 - 3000 = 5000, reported 10000 -> diff 5000

    findings = check(_ctx(lines, totals))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == RULE_ID
    assert finding.amount_cents == 5000


def test_balance_understated_no_finding():
    lines = [_line("l1", 10000)]
    totals = Totals(
        charges_cents=10000, adjustments_cents=2000, payments_cents=3000,
        patient_balance_cents=1000,
    )
    # expected balance = 5000, reported 1000 -> understated, not reported

    assert check(_ctx(lines, totals)) == []
