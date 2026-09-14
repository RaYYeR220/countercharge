"""R5 -- balance billed above the in-network plan's patient responsibility.

An in-network provider cannot bill a patient more than the amount the
patient's own insurer says the patient owes for that claim.
"""

from countercharge_engine.canonical import with_id
from countercharge_engine.models import Citation, Finding, Network
from countercharge_engine.rules.common import AuditContext

RULE_ID = "EOB_BALANCE_BILLING"


def check(ctx: AuditContext) -> list[Finding]:
    eob = ctx.eob
    if eob is None or eob.network != Network.IN:
        return []

    diff = ctx.bill.totals.patient_balance_cents - eob.total_patient_resp_cents
    if diff <= 0:
        return []

    finding = Finding(
        rule_id=RULE_ID,
        disputable=True,
        line_ids=[],
        amount_cents=diff,
        title="Billed above what your insurance says you owe",
        detail=(
            "This is an in-network claim. Your insurer's explanation of "
            "benefits says you owe less than the balance shown on this bill; "
            "in-network providers can't bill you above that amount."
        ),
        citation=Citation(
            dataset="EOB",
            version="n/a",
            url="",
            record={"claim_no": eob.claim_no, "payer": eob.payer},
        ),
        evidence={
            "billed_patient_balance_cents": ctx.bill.totals.patient_balance_cents,
            "eob_patient_resp_cents": eob.total_patient_resp_cents,
        },
    )
    return [with_id(finding)]
