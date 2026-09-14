from datetime import date

from countercharge_engine.models import Bill, CodeType, LineItem, Provider, Setting, Totals
from countercharge_engine.refdata.memory import MemoryRefData
from countercharge_engine.rules.common import AuditContext
from countercharge_engine.rules.r1_duplicate import RULE_ID, check


def _line(line_id, code="99213", dos=date(2026, 3, 1), units=1, charge=10000, modifiers=None):
    return LineItem(
        line_id=line_id,
        dos=dos,
        code=code,
        code_type=CodeType.CPT,
        modifiers=modifiers or [],
        units=units,
        charge_cents=charge,
    )


def _ctx(lines):
    bill = Bill(
        provider=Provider(name="Test Hospital"),
        account_no="A1",
        statement_date=date(2026, 3, 15),
        setting=Setting.ER,
        lines=lines,
        totals=Totals(
            charges_cents=sum(line.charge_cents for line in lines),
            patient_balance_cents=0,
        ),
    )
    return AuditContext(
        bill=bill,
        eob=None,
        household=None,
        gfe=None,
        refdata=MemoryRefData(),
        as_of=bill.statement_date,
    )


def test_exact_duplicate_fires_one_finding():
    lines = [_line("l1"), _line("l2")]
    findings = check(_ctx(lines))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == RULE_ID
    assert finding.disputable is True
    assert finding.line_ids == ["l1", "l2"]
    assert finding.amount_cents == 10000
    assert finding.finding_id.startswith("f_")


def test_same_code_different_dos_no_finding():
    lines = [_line("l1", dos=date(2026, 3, 1)), _line("l2", dos=date(2026, 3, 2))]
    assert check(_ctx(lines)) == []


def test_same_code_different_modifiers_no_finding():
    lines = [_line("l1", modifiers=["59"]), _line("l2", modifiers=["25"])]
    assert check(_ctx(lines)) == []


def test_triple_duplicate_fires_two_findings():
    lines = [_line("l1"), _line("l2"), _line("l3")]
    findings = check(_ctx(lines))

    assert len(findings) == 2
    assert [f.line_ids for f in findings] == [["l1", "l2"], ["l1", "l3"]]
    assert all(f.amount_cents == 10000 for f in findings)
