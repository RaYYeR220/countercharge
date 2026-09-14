"""R1 -- duplicate line charges.

Lines equal on (code, dos, units, charge_cents, sorted modifiers) are
almost certainly the same service billed twice. The first line in each
matching group is treated as the original; every later line in the
group is flagged as a copy.
"""

from countercharge_engine.canonical import with_id
from countercharge_engine.models import Citation, Finding, LineItem
from countercharge_engine.rules.common import AuditContext

RULE_ID = "DUPLICATE"


def _key(line: LineItem) -> tuple:
    return (line.code, line.dos, line.units, line.charge_cents, tuple(sorted(line.modifiers)))


def check(ctx: AuditContext) -> list[Finding]:
    groups: dict[tuple, list[LineItem]] = {}
    for line in ctx.bill.lines:
        groups.setdefault(_key(line), []).append(line)

    findings: list[Finding] = []
    for (code, dos, units, charge_cents, _modifiers), lines in groups.items():
        if len(lines) < 2:
            continue
        original = lines[0]
        for copy in lines[1:]:
            finding = Finding(
                rule_id=RULE_ID,
                disputable=True,
                line_ids=[original.line_id, copy.line_id],
                amount_cents=copy.charge_cents,
                title="Duplicate charge",
                detail=(
                    f"Code {code} was billed twice for the same date of service "
                    "with identical units, charge and modifiers."
                ),
                citation=Citation(
                    dataset="BILL",
                    version="n/a",
                    url="",
                    record={
                        "code": code,
                        "dos": dos.isoformat(),
                        "units": units,
                        "charge_cents": charge_cents,
                    },
                ),
                evidence={},
            )
            findings.append(with_id(finding))
    return findings
