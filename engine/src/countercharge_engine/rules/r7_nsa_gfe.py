"""R7 -- No Surprises Act good-faith-estimate dispute.

A self-pay patient who was given a good-faith estimate and is billed
$400 or more above it can initiate the patient-provider dispute
resolution (PPDR) process within 120 days of the bill's statement
date.
"""

from datetime import timedelta

from countercharge_engine.canonical import with_id
from countercharge_engine.models import Citation, Finding
from countercharge_engine.rules.common import AuditContext

RULE_ID = "NSA_GFE"

_NSA_URL = "https://www.cms.gov/nosurprises"
_THRESHOLD_CENTS = 40000
_PPDR_WINDOW_DAYS = 120


def check(ctx: AuditContext) -> list[Finding]:
    if not ctx.bill.self_pay or ctx.gfe is None:
        return []

    diff = ctx.bill.totals.charges_cents - ctx.gfe.total_cents
    if diff < _THRESHOLD_CENTS:
        return []

    deadline = ctx.bill.statement_date + timedelta(days=_PPDR_WINDOW_DAYS)
    finding = Finding(
        rule_id=RULE_ID,
        disputable=True,
        line_ids=[],
        amount_cents=diff,
        title="Billed $400 or more above your good-faith estimate",
        detail=(
            "You're self-pay and got a good-faith estimate for this care. "
            "The actual charges are at least $400 above that estimate, which "
            "qualifies you to dispute the difference."
        ),
        citation=Citation(
            dataset="NSA",
            version="45 CFR 149.620",
            url=_NSA_URL,
            record={
                "gfe_total_cents": ctx.gfe.total_cents,
                "charges_cents": ctx.bill.totals.charges_cents,
            },
        ),
        evidence={"ppdr_deadline": deadline.isoformat()},
    )
    return [with_id(finding)]
