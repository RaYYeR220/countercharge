"""R9 -- IRS 501(r) financial assistance policy (FAP) eligibility.

Nonprofit hospitals must publish a financial assistance policy under
26 USC 501(r). ``evaluate_fap`` compares the household's income (as a
percentage of the federal poverty level) against the hospital's own
published free-care and discount thresholds. A FREE result is an
application opportunity, not a dispute, so its finding is advisory
(``disputable=False``).
"""

from countercharge_engine import datasets
from countercharge_engine.canonical import with_id
from countercharge_engine.fpl import fpl_percent
from countercharge_engine.models import FapResult, FapTier, Finding
from countercharge_engine.rules.common import AuditContext, citation

RULE_ID = "FAP_501R"


def evaluate_fap(ctx: AuditContext) -> FapResult | None:
    provider = ctx.bill.provider
    if not provider.nonprofit or not provider.hospital_id or ctx.household is None:
        return None

    fap = ctx.refdata.hospital_fap(provider.hospital_id)
    if fap is None:
        return None

    year = ctx.bill.statement_date.year
    pct = fpl_percent(ctx.household, ctx.refdata, year)

    if fap.free_max_fpl is not None and pct <= fap.free_max_fpl:
        tier = FapTier.FREE
    elif fap.discount_max_fpl is not None and pct <= fap.discount_max_fpl:
        tier = FapTier.DISCOUNT
    elif fap.free_max_fpl is None and fap.discount_max_fpl is None:
        tier = FapTier.UNKNOWN
    else:
        tier = FapTier.NONE

    return FapResult(
        hospital_id=provider.hospital_id,
        fpl_percent=pct,
        tier=tier,
        free_max_fpl=fap.free_max_fpl,
        discount_max_fpl=fap.discount_max_fpl,
        citation=citation(
            ctx.refdata,
            datasets.fap_dataset(provider.hospital_id),
            {"hospital_id": provider.hospital_id},
        ),
    )


def check(ctx: AuditContext) -> list[Finding]:
    result = evaluate_fap(ctx)
    if result is None:
        return []

    if result.tier == FapTier.FREE:
        finding = Finding(
            rule_id=RULE_ID,
            disputable=False,
            line_ids=[],
            amount_cents=ctx.bill.totals.patient_balance_cents,
            title="Likely eligible for free care",
            detail=(
                "Based on your household size and income relative to the "
                "federal poverty level, this hospital's financial assistance "
                "policy likely qualifies you for free care."
            ),
            citation=result.citation,
            evidence={"fpl_percent": result.fpl_percent, "tier": result.tier.value},
        )
        return [with_id(finding)]

    if result.tier == FapTier.DISCOUNT:
        finding = Finding(
            rule_id=RULE_ID,
            disputable=False,
            line_ids=[],
            amount_cents=0,
            title="May qualify for a discounted rate",
            detail=(
                "Based on your household size and income relative to the "
                "federal poverty level, this hospital's financial assistance "
                "policy may qualify you for a discounted rate."
            ),
            citation=result.citation,
            evidence={"fpl_percent": result.fpl_percent, "tier": result.tier.value},
        )
        return [with_id(finding)]

    return []
