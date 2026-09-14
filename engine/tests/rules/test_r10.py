from datetime import date

from countercharge_engine.models import Bill, CodeType, LineItem, Provider, Setting, Totals
from countercharge_engine.refdata.base import DatasetInfo
from countercharge_engine.refdata.memory import MemoryRefData
from countercharge_engine.rules.common import AuditContext
from countercharge_engine.rules.r10_benchmark import RULE_ID, check

DOS = date(2026, 3, 1)


def _bill(setting, units=1, charge=50000):
    return Bill(
        provider=Provider(name="Test Hospital"),
        account_no="A1",
        statement_date=DOS,
        setting=setting,
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


def _refdata(rate_cents, facility):
    return MemoryRefData(
        rates={("99213", facility): rate_cents},
        infos={
            "PFS-RVU": DatasetInfo(
                dataset="PFS-RVU", version="RVU26B", url="https://cms.gov/pfs"
            )
        },
    )


def _ctx(bill, refdata):
    return AuditContext(
        bill=bill, eob=None, household=None, gfe=None, refdata=refdata, as_of=DOS
    )


def test_five_times_rate_is_advisory():
    bill = _bill(Setting.OUTPATIENT, charge=50000)
    refdata = _refdata(rate_cents=10000, facility=True)

    findings = check(_ctx(bill, refdata))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == RULE_ID
    assert finding.disputable is False
    assert finding.amount_cents == 40000
    assert finding.evidence["ratio_x100"] == 500
    assert finding.citation.dataset == "PFS-RVU"


def test_two_times_rate_no_finding():
    bill = _bill(Setting.OUTPATIENT, charge=20000)
    refdata = _refdata(rate_cents=10000, facility=True)

    assert check(_ctx(bill, refdata)) == []


def test_professional_setting_uses_non_facility_rate():
    bill = _bill(Setting.PROFESSIONAL, charge=50000)
    refdata = _refdata(rate_cents=10000, facility=False)

    findings = check(_ctx(bill, refdata))

    assert len(findings) == 1


def test_no_rate_no_finding():
    bill = _bill(Setting.OUTPATIENT, charge=50000)
    refdata = MemoryRefData()

    assert check(_ctx(bill, refdata)) == []
