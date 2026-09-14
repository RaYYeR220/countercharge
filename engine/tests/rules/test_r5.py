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
from countercharge_engine.rules.r5_eob import RULE_ID, check

DOS = date(2026, 3, 1)


def _line():
    return LineItem(
        line_id="l1", dos=DOS, code="99213", code_type=CodeType.CPT, charge_cents=30000
    )


def _bill(patient_balance):
    return Bill(
        provider=Provider(name="Test Hospital"),
        account_no="A1",
        statement_date=DOS,
        setting=Setting.OUTPATIENT,
        lines=[_line()],
        totals=Totals(charges_cents=30000, patient_balance_cents=patient_balance),
    )


def _eob(network, total_patient_resp):
    return EOB(
        payer="Acme Health",
        claim_no="C1",
        network=network,
        lines=[],
        total_patient_resp_cents=total_patient_resp,
    )


def _ctx(bill, eob):
    return AuditContext(
        bill=bill, eob=eob, household=None, gfe=None, refdata=MemoryRefData(), as_of=DOS
    )


def test_balance_over_eob_resp_in_network_fires():
    bill = _bill(patient_balance=50000)
    eob = _eob(Network.IN, total_patient_resp=20000)

    findings = check(_ctx(bill, eob))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == RULE_ID
    assert finding.disputable is True
    assert finding.amount_cents == 30000
    assert finding.citation.dataset == "EOB"
    assert finding.citation.record == {"claim_no": "C1", "payer": "Acme Health"}


def test_balance_equal_to_eob_resp_no_finding():
    bill = _bill(patient_balance=20000)
    eob = _eob(Network.IN, total_patient_resp=20000)

    assert check(_ctx(bill, eob)) == []


def test_out_of_network_no_finding():
    bill = _bill(patient_balance=50000)
    eob = _eob(Network.OUT, total_patient_resp=20000)

    assert check(_ctx(bill, eob)) == []


def test_no_eob_no_finding():
    bill = _bill(patient_balance=50000)

    assert check(_ctx(bill, None)) == []
