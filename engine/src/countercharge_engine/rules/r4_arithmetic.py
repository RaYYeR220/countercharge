"""R4 -- bill-internal arithmetic checks.

Two independent checks, both bill-internal (no CMS reference data
needed): (a) the line charges should sum to the reported total
charges; (b) charges minus adjustments minus payments should equal the
reported patient balance. Only positive overcharges are reported --
an understatement in the patient's favor is not a dispute.
"""

from countercharge_engine.canonical import with_id
from countercharge_engine.models import Citation, Finding
from countercharge_engine.rules.common import AuditContext

RULE_ID = "ARITHMETIC"


def _finding(title: str, detail: str, amount: int, record: dict) -> Finding:
    return with_id(
        Finding(
            rule_id=RULE_ID,
            disputable=True,
            line_ids=[],
            amount_cents=amount,
            title=title,
            detail=detail,
            citation=Citation(dataset="BILL", version="n/a", url="", record=record),
            evidence={},
        )
    )


def check(ctx: AuditContext) -> list[Finding]:
    totals = ctx.bill.totals
    findings: list[Finding] = []

    sum_lines = sum(line.charge_cents for line in ctx.bill.lines)
    if sum_lines != totals.charges_cents:
        overcharge = max(0, totals.charges_cents - sum_lines)
        if overcharge > 0:
            findings.append(
                _finding(
                    "Charges don't add up",
                    "The sum of the individual line charges doesn't match the "
                    "total charges reported on the bill.",
                    overcharge,
                    {"sum_lines_cents": sum_lines, "reported_charges_cents": totals.charges_cents},
                )
            )

    expected_balance = totals.charges_cents - totals.adjustments_cents - totals.payments_cents
    if expected_balance != totals.patient_balance_cents:
        overcharge = max(0, totals.patient_balance_cents - expected_balance)
        if overcharge > 0:
            findings.append(
                _finding(
                    "Patient balance doesn't add up",
                    "Charges minus adjustments minus payments doesn't match the "
                    "patient balance reported on the bill.",
                    overcharge,
                    {
                        "expected_balance_cents": expected_balance,
                        "reported_balance_cents": totals.patient_balance_cents,
                    },
                )
            )

    return findings
