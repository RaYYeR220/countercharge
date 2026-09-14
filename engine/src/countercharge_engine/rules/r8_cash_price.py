"""R8 -- self-pay charge above the hospital's own posted cash price.

Hospitals publish a discounted cash price for self-pay patients under
the price transparency rule. Charging a self-pay patient more than
that posted price for the same code is disputable on its own terms.
"""

from countercharge_engine import datasets
from countercharge_engine.canonical import with_id
from countercharge_engine.models import Finding
from countercharge_engine.rules.common import AuditContext, citation

RULE_ID = "CASH_PRICE"


def check(ctx: AuditContext) -> list[Finding]:
    bill = ctx.bill
    hospital_id = bill.provider.hospital_id
    if not bill.self_pay or not hospital_id:
        return []

    findings: list[Finding] = []
    for line in bill.lines:
        price = ctx.refdata.hospital_price(hospital_id, line.code)
        if price is None or price.cash_cents is None:
            continue

        expected = price.cash_cents * line.units
        if line.charge_cents <= expected:
            continue

        diff = line.charge_cents - expected
        finding = Finding(
            rule_id=RULE_ID,
            disputable=True,
            line_ids=[line.line_id],
            amount_cents=diff,
            title="Charged above the hospital's own cash price",
            detail=(
                f"For code {line.code}, this hospital publishes a discounted "
                "cash price for self-pay patients that is lower than what "
                "was charged on this line."
            ),
            citation=citation(
                ctx.refdata,
                datasets.hpt_dataset(hospital_id),
                {"code": line.code, "cash_cents": price.cash_cents},
            ),
            evidence={"cash_cents": price.cash_cents, "units": line.units},
        )
        findings.append(with_id(finding))
    return findings
