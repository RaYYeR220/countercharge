from datetime import date

from countercharge_engine.models import Bill, CodeType, LineItem, Provider, Setting, Totals
from countercharge_engine.refdata.base import DatasetInfo, PtpEdit
from countercharge_engine.refdata.memory import MemoryRefData
from countercharge_engine.rules.common import AuditContext
from countercharge_engine.rules.r2_ncci_ptp import RULE_ID, check

DOS = date(2026, 3, 1)


def _line(line_id, code, modifiers=None, dos=DOS, charge=5000):
    return LineItem(
        line_id=line_id,
        dos=dos,
        code=code,
        code_type=CodeType.CPT,
        modifiers=modifiers or [],
        units=1,
        charge_cents=charge,
    )


def _ctx(lines, setting=Setting.ER, refdata=None):
    bill = Bill(
        provider=Provider(name="Test Hospital"),
        account_no="A1",
        statement_date=DOS,
        setting=setting,
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
        refdata=refdata if refdata is not None else MemoryRefData(),
        as_of=bill.statement_date,
    )


def _refdata(edits_all=None, edits_opps=None, edits_prac=None, infos=None):
    return MemoryRefData(
        ptp=edits_all or [],
        ptp_opps=edits_opps or [],
        ptp_prac=edits_prac or [],
        infos=infos
        or {
            "NCCI-PTP-OPPS": DatasetInfo(
                dataset="NCCI-PTP-OPPS", version="2026Q4 v323r0", url="https://cms.gov/ncci"
            ),
            "NCCI-PTP-PRAC": DatasetInfo(
                dataset="NCCI-PTP-PRAC", version="2026Q4 v323r0", url="https://cms.gov/ncci"
            ),
        },
    )


def _edit(modifier_ind, effective=date(2026, 1, 1), deleted=None):
    return PtpEdit(
        col1="99285",
        col2="36415",
        modifier_ind=modifier_ind,
        effective=effective,
        deleted=deleted,
        rationale="standards of medical/surgical practice",
    )


def test_modifier_ind_0_fires_even_with_modifier_59():
    lines = [_line("l1", "99285", modifiers=["59"]), _line("l2", "36415")]
    refdata = _refdata(edits_all=[_edit(0)])

    findings = check(_ctx(lines, refdata=refdata))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == RULE_ID
    assert finding.disputable is True
    assert finding.line_ids == ["l1", "l2"]
    assert finding.amount_cents == 5000


def test_modifier_ind_1_with_modifier_no_finding():
    lines = [_line("l1", "99285"), _line("l2", "36415", modifiers=["59"])]
    refdata = _refdata(edits_all=[_edit(1)])

    assert check(_ctx(lines, refdata=refdata)) == []


def test_modifier_ind_1_without_modifier_fires():
    lines = [_line("l1", "99285"), _line("l2", "36415")]
    refdata = _refdata(edits_all=[_edit(1)])

    findings = check(_ctx(lines, refdata=refdata))

    assert len(findings) == 1
    assert findings[0].line_ids == ["l1", "l2"]


def test_modifier_ind_9_never_fires():
    lines = [_line("l1", "99285"), _line("l2", "36415")]
    refdata = _refdata(edits_all=[_edit(9)])

    assert check(_ctx(lines, refdata=refdata)) == []


def test_deleted_edit_before_dos_no_finding():
    lines = [_line("l1", "99285"), _line("l2", "36415", dos=date(2026, 7, 1))]
    refdata = _refdata(edits_all=[_edit(0, deleted=date(2026, 6, 1))])

    assert check(_ctx(lines, refdata=refdata)) == []


def test_different_dos_no_finding():
    lines = [
        _line("l1", "99285", dos=date(2026, 3, 1)),
        _line("l2", "36415", dos=date(2026, 3, 2)),
    ]
    refdata = _refdata(edits_all=[_edit(0)])

    assert check(_ctx(lines, refdata=refdata)) == []


def test_uses_opps_table_for_er_setting():
    lines = [_line("l1", "99285"), _line("l2", "36415")]
    refdata = _refdata(edits_opps=[_edit(0)])

    assert len(check(_ctx(lines, setting=Setting.ER, refdata=refdata))) == 1
    assert check(_ctx(lines, setting=Setting.PROFESSIONAL, refdata=refdata)) == []
