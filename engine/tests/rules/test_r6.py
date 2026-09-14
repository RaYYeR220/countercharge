from datetime import date

from countercharge_engine.models import (
    Bill,
    CodeType,
    EOB,
    LineItem,
    Network,
    Provider,
    Setting,
    Totals,
)
from countercharge_engine.refdata.memory import MemoryRefData
from countercharge_engine.rules.common import AuditContext
from countercharge_engine.rules.r6_nsa_emergency import RULE_ID, check

DOS = date(2026, 3, 1)


def _line():
    return LineItem(
        line_id="l1", dos=DOS, code="99284", code_type=CodeType.CPT, charge_cents=80000
    )


def _bill(setting, patient_balance=80000):
    return Bill(
        provider=Provider(name="Test Hospital"),
        account_no="A1",
        statement_date=DOS,
        setting=setting,
        lines=[_line()],
        totals=Totals(charges_cents=80000, patient_balance_cents=patient_balance),
    )


def _eob(emergency, total_patient_resp=20000):
    return EOB(
        payer="Acme Health",
        claim_no="C1",
        network=Network.OUT,
        emergency=emergency,
        lines=[],
        total_patient_resp_cents=total_patient_resp,
    )


def _ctx(bill, eob):
    return AuditContext(
        bill=bill, eob=eob, household=None, gfe=None, refdata=MemoryRefData(), as_of=DOS
    )


def test_oon_er_over_resp_fires():
    bill = _bill(Setting.ER)
    eob = _eob(emergency=False)

    findings = check(_ctx(bill, eob))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == RULE_ID
    assert finding.disputable is True
    assert finding.amount_cents == 60000
    assert finding.citation.dataset == "NSA"
    assert finding.citation.version == "45 CFR 149.110"
    assert finding.citation.url == "https://www.cms.gov/nosurprises"


def test_oon_emergency_flag_on_non_er_setting_fires():
    bill = _bill(Setting.OUTPATIENT)
    eob = _eob(emergency=True)

    findings = check(_ctx(bill, eob))

    assert len(findings) == 1
    assert findings[0].amount_cents == 60000


def test_oon_non_emergency_outpatient_no_finding():
    bill = _bill(Setting.OUTPATIENT)
    eob = _eob(emergency=False)

    assert check(_ctx(bill, eob)) == []


def test_in_network_no_finding():
    bill = _bill(Setting.ER)
    eob = EOB(
        payer="Acme Health",
        claim_no="C1",
        network=Network.IN,
        emergency=True,
        lines=[],
        total_patient_resp_cents=20000,
    )

    assert check(_ctx(bill, eob)) == []
