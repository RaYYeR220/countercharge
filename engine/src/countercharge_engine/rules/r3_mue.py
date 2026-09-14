"""R3 -- Medically Unlikely Edits (MUE).

The MUE Adjudication Indicator (``mai``) determines *how* Medicare checks
the edit, not just whether an override is possible:

- **MAI 1** ("claim line edit"): each line's own ``units`` is compared to
  the code's MUE value independently. Units are never summed across lines
  -- a code legitimately split across two lines, each individually at or
  under the MUE value, never fires even though their sum might exceed it.
  One finding is emitted per offending line, priced at that line's own
  (floored) per-unit charge times its excess units.
- **MAI 2/3** ("date-of-service edit"): units *are* summed across every
  line reporting that code on that date. If the total exceeds the MUE
  value, the excess units are priced at the (floored) average per-unit
  charge across those lines and flagged as one grouped finding.
"""

from datetime import date

from countercharge_engine.canonical import with_id
from countercharge_engine.models import Finding, LineItem
from countercharge_engine.refdata.base import MueEdit, RefData
from countercharge_engine.rules.common import AuditContext, citation, mue_dataset, mue_table

RULE_ID = "MUE"

_TITLE = "Units exceed Medicare's per-day limit"


def _line_finding(refdata: RefData, dataset: str, line: LineItem, mue_edit: MueEdit) -> Finding:
    excess_units = line.units - mue_edit.mue_value
    unit_price = line.charge_cents // line.units
    amount = excess_units * unit_price

    return with_id(
        Finding(
            rule_id=RULE_ID,
            disputable=True,
            line_ids=[line.line_id],
            amount_cents=amount,
            title=_TITLE,
            detail=(
                f"Code {line.code} was billed {line.units} units on this line, "
                f"more than the {mue_edit.mue_value} unit(s) Medicare allows on "
                "a single claim line."
            ),
            citation=citation(
                refdata,
                dataset,
                {"code": line.code, "dos": line.dos.isoformat(), "mue_value": mue_edit.mue_value},
            ),
            evidence={
                "mai": mue_edit.mai,
                "mue_value": mue_edit.mue_value,
                "line_units": line.units,
                "target_key": line.line_id,
            },
        )
    )


def _group_finding(
    refdata: RefData, dataset: str, code: str, dos: date, lines: list[LineItem], mue_edit: MueEdit
) -> Finding:
    total_units = sum(line.units for line in lines)
    total_charge = sum(line.charge_cents for line in lines)
    excess_units = total_units - mue_edit.mue_value
    unit_price = total_charge // total_units
    amount = excess_units * unit_price

    return with_id(
        Finding(
            rule_id=RULE_ID,
            disputable=True,
            line_ids=[line.line_id for line in lines],
            amount_cents=amount,
            title=_TITLE,
            detail=(
                f"Code {code} was billed {total_units} units on this date, "
                f"more than the {mue_edit.mue_value} unit(s) Medicare allows per day."
            ),
            citation=citation(
                refdata,
                dataset,
                {"code": code, "dos": dos.isoformat(), "mue_value": mue_edit.mue_value},
            ),
            evidence={
                "mai": mue_edit.mai,
                "mue_value": mue_edit.mue_value,
                "total_units": total_units,
                "target_key": f"{code}|{dos.isoformat()}",
            },
        )
    )


def check(ctx: AuditContext) -> list[Finding]:
    table = mue_table(ctx.bill.setting)
    dataset = mue_dataset(table)

    groups: dict[tuple, list[LineItem]] = {}
    for line in ctx.bill.lines:
        groups.setdefault((line.code, line.dos), []).append(line)

    findings: list[Finding] = []
    for (code, dos), lines in groups.items():
        mue_edit = ctx.refdata.mue(code, table)
        if mue_edit is None:
            continue

        if mue_edit.mai == 1:
            for line in lines:
                if line.units > mue_edit.mue_value:
                    findings.append(_line_finding(ctx.refdata, dataset, line, mue_edit))
            continue

        total_units = sum(line.units for line in lines)
        if total_units <= mue_edit.mue_value:
            continue
        findings.append(_group_finding(ctx.refdata, dataset, code, dos, lines, mue_edit))

    return findings
