from datetime import date, timedelta

from countercharge_engine.models import Bill, CodeType, GFE, LineItem, Provider, Setting, Totals
from countercharge_engine.refdata.memory import MemoryRefData
from countercharge_engine.rules.common import AuditContext
from countercharge_engine.rules.r7_nsa_gfe import RULE_ID, check

DOS = date(2026, 3, 1)
STATEMENT_DATE = date(2026, 3, 15)


def _bill(charge_cents):
    return Bill(
        provider=Provider(name="Test Hospital"),
        account_no="A1",
        statement_date=STATEMENT_DATE,
        setting=Setting.OUTPATIENT,
        self_pay=True,
        lines=[
            LineItem(
                line_id="l1",
                dos=DOS,
                code="99213",
                code_type=CodeType.CPT,
                charge_cents=charge_cents,
            )
        ],
        totals=Totals(charges_cents=charge_cents, patient_balance_cents=charge_cents),
    )


def _ctx(bill, gfe):
    return AuditContext(
        bill=bill,
        eob=None,
        household=None,
        gfe=gfe,
        refdata=MemoryRefData(),
        as_of=STATEMENT_DATE,
    )


def test_under_threshold_no_finding():
    bill = _bill(charge_cents=100000 + 39999)
    gfe = GFE(total_cents=100000, date=DOS)

    assert check(_ctx(bill, gfe)) == []


def test_at_threshold_fires_with_deadline():
    bill = _bill(charge_cents=100000 + 40000)
    gfe = GFE(total_cents=100000, date=DOS)

    findings = check(_ctx(bill, gfe))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == RULE_ID
    assert finding.disputable is True
    assert finding.amount_cents == 40000
    assert finding.citation.dataset == "NSA"
    assert finding.citation.version == "45 CFR 149.620"
    expected_deadline = (STATEMENT_DATE + timedelta(days=120)).isoformat()
    assert finding.evidence["ppdr_deadline"] == expected_deadline


def test_not_self_pay_no_finding():
    bill = _bill(charge_cents=200000)
    bill = bill.model_copy(update={"self_pay": False})
    gfe = GFE(total_cents=100000, date=DOS)

    assert check(_ctx(bill, gfe)) == []


def test_no_gfe_no_finding():
    bill = _bill(charge_cents=200000)

    assert check(_ctx(bill, None)) == []
