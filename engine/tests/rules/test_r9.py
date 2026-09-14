from datetime import date

from countercharge_engine.models import (
    Bill,
    CodeType,
    FapTier,
    Household,
    LineItem,
    Provider,
    Setting,
    Totals,
)
from countercharge_engine.refdata.base import DatasetInfo, HospitalFap
from countercharge_engine.refdata.memory import MemoryRefData
from countercharge_engine.rules.common import AuditContext
from countercharge_engine.rules.r9_fap import RULE_ID, check, evaluate_fap

STATEMENT_DATE = date(2026, 3, 15)
_FPL_2026 = {(2026, "48"): (1_596_000, 568_000)}


def _bill(nonprofit=True, hospital_id="nyp", patient_balance=90000):
    return Bill(
        provider=Provider(name="NYP", nonprofit=nonprofit, hospital_id=hospital_id),
        account_no="A1",
        statement_date=STATEMENT_DATE,
        setting=Setting.OUTPATIENT,
        lines=[
            LineItem(
                line_id="l1",
                dos=STATEMENT_DATE,
                code="99213",
                code_type=CodeType.CPT,
                charge_cents=90000,
            )
        ],
        totals=Totals(charges_cents=90000, patient_balance_cents=patient_balance),
    )


def _fap(free_max_fpl, discount_max_fpl):
    return HospitalFap(
        hospital_id="nyp",
        name="NewYork-Presbyterian",
        free_max_fpl=free_max_fpl,
        discount_max_fpl=discount_max_fpl,
        agb_pct=20,
        source_url="https://nyp.org/fap",
        retrieved=date(2026, 1, 15),
    )


def _refdata(fap):
    faps = {"nyp": fap} if fap else {}
    return MemoryRefData(
        fpl=_FPL_2026,
        faps=faps,
        infos={
            "FAP-nyp": DatasetInfo(
                dataset="FAP-nyp", version="2026-01-15", url="https://nyp.org/fap"
            )
        },
    )


def _ctx(bill, household, refdata):
    return AuditContext(
        bill=bill,
        eob=None,
        household=household,
        gfe=None,
        refdata=refdata,
        as_of=STATEMENT_DATE,
    )


def test_household_of_four_under_free_threshold_is_free():
    bill = _bill(patient_balance=90000)
    household = Household(size=4, annual_income_cents=5_000_000, state="NY")
    refdata = _refdata(_fap(free_max_fpl=250, discount_max_fpl=None))

    result = evaluate_fap(_ctx(bill, household, refdata))

    assert result.tier == FapTier.FREE
    assert result.fpl_percent == 151

    findings = check(_ctx(bill, household, refdata))
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == RULE_ID
    assert finding.disputable is False
    assert finding.amount_cents == 90000
    assert finding.title == "Likely eligible for free care"
    assert finding.evidence == {"fpl_percent": 151, "tier": "FREE"}


def test_household_of_one_between_free_and_discount_is_discount():
    bill = _bill(patient_balance=90000)
    household = Household(size=1, annual_income_cents=6_000_000, state="TX")
    refdata = _refdata(_fap(free_max_fpl=250, discount_max_fpl=400))

    result = evaluate_fap(_ctx(bill, household, refdata))

    assert result.tier == FapTier.DISCOUNT
    assert result.fpl_percent == 375

    findings = check(_ctx(bill, household, refdata))
    assert len(findings) == 1
    finding = findings[0]
    assert finding.disputable is False
    assert finding.amount_cents == 0
    assert finding.evidence["tier"] == "DISCOUNT"


def test_for_profit_provider_no_result():
    bill = _bill(nonprofit=False)
    household = Household(size=4, annual_income_cents=5_000_000, state="NY")
    refdata = _refdata(_fap(free_max_fpl=250, discount_max_fpl=400))

    assert evaluate_fap(_ctx(bill, household, refdata)) is None
    assert check(_ctx(bill, household, refdata)) == []


def test_missing_thresholds_is_unknown_and_no_finding():
    bill = _bill()
    household = Household(size=4, annual_income_cents=5_000_000, state="NY")
    refdata = _refdata(_fap(free_max_fpl=None, discount_max_fpl=None))

    result = evaluate_fap(_ctx(bill, household, refdata))
    assert result.tier == FapTier.UNKNOWN

    assert check(_ctx(bill, household, refdata)) == []


def test_above_all_thresholds_is_none():
    bill = _bill()
    household = Household(size=1, annual_income_cents=6_000_000, state="TX")
    refdata = _refdata(_fap(free_max_fpl=100, discount_max_fpl=200))

    result = evaluate_fap(_ctx(bill, household, refdata))
    assert result.tier == FapTier.NONE

    assert check(_ctx(bill, household, refdata)) == []


def test_no_household_no_result():
    bill = _bill()
    refdata = _refdata(_fap(free_max_fpl=250, discount_max_fpl=400))

    assert evaluate_fap(_ctx(bill, None, refdata)) is None
    assert check(_ctx(bill, None, refdata)) == []
