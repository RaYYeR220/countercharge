"""R3 -- Medically Unlikely Edits (MUE).

Units are summed per (code, dos) across every line that reports that
code on that day. If the total exceeds the code's MUE value, the
excess units are priced at the (floored) average per-unit charge for
that code/dos and flagged as disputable.
"""

from countercharge_engine.canonical import with_id
from countercharge_engine.models import Finding, LineItem
from countercharge_engine.rules.common import AuditContext, citation, mue_dataset, mue_table

RULE_ID = "MUE"


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

        total_units = sum(line.units for line in lines)
        if total_units <= mue_edit.mue_value:
            continue

        total_charge = sum(line.charge_cents for line in lines)
        excess_units = total_units - mue_edit.mue_value
        unit_price = total_charge // total_units
        amount = excess_units * unit_price

        finding = Finding(
            rule_id=RULE_ID,
            disputable=True,
            line_ids=[line.line_id for line in lines],
            amount_cents=amount,
            title="Units exceed Medicare's per-day limit",
            detail=(
                f"Code {code} was billed {total_units} units on this date, "
                f"more than the {mue_edit.mue_value} unit(s) Medicare allows per day."
            ),
            citation=citation(
                ctx.refdata,
                dataset,
                {"code": code, "dos": dos.isoformat(), "mue_value": mue_edit.mue_value},
            ),
            evidence={
                "mai": mue_edit.mai,
                "mue_value": mue_edit.mue_value,
                "total_units": total_units,
            },
        )
        findings.append(with_id(finding))
    return findings
