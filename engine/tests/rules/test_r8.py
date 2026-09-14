from datetime import date

from countercharge_engine.models import Bill, CodeType, LineItem, Provider, Setting, Totals
from countercharge_engine.refdata.base import DatasetInfo, HospitalPrice
from countercharge_engine.refdata.memory import MemoryRefData
from countercharge_engine.rules.common import AuditContext
from countercharge_engine.rules.r8_cash_price import RULE_ID, check

DOS = date(2026, 3, 1)


def _bill(self_pay=True, hospital_id="nyp", units=1, charge=90000):
    return Bill(
        provider=Provider(name="NYP", hospital_id=hospital_id),
        account_no="A1",
        statement_date=DOS,
        setting=Setting.OUTPATIENT,
        self_pay=self_pay,
        lines=[
            LineItem(
                line_id="l1",
                dos=DOS,
                code="99213",
                code_type=CodeType.CPT,
                units=units,
                charge_cents=charge,
            )
        ],
        totals=Totals(charges_cents=charge, patient_balance_cents=charge),
    )


def _refdata(prices):
    return MemoryRefData(
        prices=prices,
        infos={
            "HPT-nyp": DatasetInfo(
                dataset="HPT-nyp", version="2026-01-15", url="https://nyp.org/cms-hpt.txt"
            )
        },
    )


def _ctx(bill, refdata):
    return AuditContext(
        bill=bill, eob=None, household=None, gfe=None, refdata=refdata, as_of=DOS
    )


def _price(cash_cents):
    return HospitalPrice(
        hospital_id="nyp",
        code="99213",
        setting="OUTPATIENT",
        gross_cents=100000,
        cash_cents=cash_cents,
        min_cents=None,
        max_cents=None,
    )


def test_above_cash_price_fires():
    bill = _bill(charge=90000)
    refdata = _refdata({("nyp", "99213"): _price(60000)})

    findings = check(_ctx(bill, refdata))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == RULE_ID
    assert finding.disputable is True
    assert finding.line_ids == ["l1"]
    assert finding.amount_cents == 30000
    assert finding.citation.dataset == "HPT-nyp"


def test_at_cash_price_no_finding():
    bill = _bill(charge=60000)
    refdata = _refdata({("nyp", "99213"): _price(60000)})

    assert check(_ctx(bill, refdata)) == []


def test_insured_bill_no_finding():
    bill = _bill(self_pay=False, charge=90000)
    refdata = _refdata({("nyp", "99213"): _price(60000)})

    assert check(_ctx(bill, refdata)) == []


def test_no_price_row_no_finding():
    bill = _bill(charge=90000)
    refdata = _refdata({})

    assert check(_ctx(bill, refdata)) == []


def test_multiple_units_scales_cash_price():
    bill = _bill(units=3, charge=200000)
    refdata = _refdata({("nyp", "99213"): _price(60000)})

    findings = check(_ctx(bill, refdata))

    assert len(findings) == 1
    assert findings[0].amount_cents == 200000 - 60000 * 3
