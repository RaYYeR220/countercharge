"""R6 -- No Surprises Act protection for out-of-network emergency care.

Out-of-network emergency care (either the claim is flagged emergency,
or the bill's own setting is the ER) is protected against balance
billing above the plan's patient responsibility, same as in-network
care would be.
"""

from countercharge_engine.canonical import with_id
from countercharge_engine.models import Citation, Finding, Network, Setting
from countercharge_engine.rules.common import AuditContext

RULE_ID = "NSA_EMERGENCY"

_NSA_URL = "https://www.cms.gov/nosurprises"


def check(ctx: AuditContext) -> list[Finding]:
    eob = ctx.eob
    if eob is None or eob.network != Network.OUT:
        return []
    if not (eob.emergency or ctx.bill.setting == Setting.ER):
        return []

    diff = ctx.bill.totals.patient_balance_cents - eob.total_patient_resp_cents
    if diff <= 0:
        return []

    finding = Finding(
        rule_id=RULE_ID,
        disputable=True,
        line_ids=[],
        amount_cents=diff,
        title="Out-of-network emergency care billed above plan responsibility",
        detail=(
            "This was out-of-network emergency care. Federal law caps what "
            "you can be billed at your plan's in-network patient "
            "responsibility amount, and this bill is above that."
        ),
        citation=Citation(
            dataset="NSA",
            version="45 CFR 149.110",
            url=_NSA_URL,
            record={"claim_no": eob.claim_no, "payer": eob.payer},
        ),
        evidence={
            "billed_patient_balance_cents": ctx.bill.totals.patient_balance_cents,
            "eob_patient_resp_cents": eob.total_patient_resp_cents,
        },
    )
    return [with_id(finding)]
