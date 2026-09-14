from datetime import date

from countercharge_engine.models import Bill, CodeType, LineItem, Provider, Setting, Totals
from countercharge_engine.refdata.base import DatasetInfo, MueEdit
from countercharge_engine.refdata.memory import MemoryRefData
from countercharge_engine.rules.common import AuditContext
from countercharge_engine.rules.r3_mue import RULE_ID, check

DOS = date(2026, 3, 1)


def _line(line_id, code="96374", units=1, charge=10000, dos=DOS):
    return LineItem(
        line_id=line_id,
        dos=dos,
        code=code,
        code_type=CodeType.CPT,
        units=units,
        charge_cents=charge,
    )


def _ctx(lines, refdata):
    bill = Bill(
        provider=Provider(name="Test Hospital"),
        account_no="A1",
        statement_date=DOS,
        setting=Setting.ER,
        lines=lines,
        totals=Totals(
            charges_cents=sum(line.charge_cents for line in lines),
            patient_balance_cents=0,
        ),
    )
    return AuditContext(
        bill=bill, eob=None, household=None, gfe=None, refdata=refdata, as_of=bill.statement_date
    )


def _refdata(mue_edits):
    return MemoryRefData(
        mue=mue_edits,
        # Dataset key must match what the refdata builder actually writes into
        # the sqlite `datasets` table for the OPPS MUE table (see
        # refdata/build/sources.py's NCCI_MUE_OPPS) -- not a bare "MUE-OPPS".
        infos={
            "NCCI-MUE-OPPS": DatasetInfo(
                dataset="NCCI-MUE-OPPS", version="2026Q4", url="https://cms.gov/mue"
            )
        },
    )


def test_excess_units_fires_with_amount_times_unit_charge():
    lines = [_line("l1", units=3, charge=30000)]
    refdata = _refdata([MueEdit(code="96374", mue_value=1, mai=2, rationale="policy")])

    findings = check(_ctx(lines, refdata))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == RULE_ID
    assert finding.disputable is True
    assert finding.line_ids == ["l1"]
    assert finding.amount_cents == 20000
    assert finding.evidence["mai"] == 2


def test_exactly_at_mue_no_finding():
    lines = [_line("l1", units=1, charge=10000)]
    refdata = _refdata([MueEdit(code="96374", mue_value=1, mai=2, rationale="policy")])

    assert check(_ctx(lines, refdata)) == []


def test_units_split_across_two_lines_summed():
    lines = [
        _line("l1", units=2, charge=20000),
        _line("l2", units=1, charge=10000),
    ]
    refdata = _refdata([MueEdit(code="96374", mue_value=1, mai=2, rationale="policy")])

    findings = check(_ctx(lines, refdata))

    assert len(findings) == 1
    finding = findings[0]
    assert set(finding.line_ids) == {"l1", "l2"}
    assert finding.amount_cents == 20000


def test_no_mue_row_no_finding():
    lines = [_line("l1", units=5, charge=50000)]
    refdata = _refdata([])

    assert check(_ctx(lines, refdata)) == []
