"""R10 -- advisory comparison against the Medicare fee schedule.

Lines charged at three times or more the national Medicare rate are
flagged for the patient's awareness. This is informational only
(``disputable=False``) -- Medicare's rate isn't a legal ceiling on what
a provider may charge -- so it belongs in the advisory list, not the
disputable one.
"""

from countercharge_engine.canonical import with_id
from countercharge_engine.models import Finding, Setting
from countercharge_engine.rules.common import AuditContext, citation

RULE_ID = "MEDICARE_BENCHMARK"

_RATIO_THRESHOLD_X100 = 300


def check(ctx: AuditContext) -> list[Finding]:
    facility = ctx.bill.setting != Setting.PROFESSIONAL

    findings: list[Finding] = []
    for line in ctx.bill.lines:
        rate = ctx.refdata.medicare_rate_cents(line.code, facility)
        if rate is None or rate <= 0:
            continue

        expected = rate * line.units
        if expected <= 0:
            continue

        ratio_x100 = (line.charge_cents * 100) // expected
        if ratio_x100 < _RATIO_THRESHOLD_X100:
            continue

        finding = Finding(
            rule_id=RULE_ID,
            disputable=False,
            line_ids=[line.line_id],
            amount_cents=line.charge_cents - expected,
            title="Charge is far above the Medicare rate",
            detail=(
                f"Code {line.code} was charged well above the national "
                "Medicare rate for this service."
            ),
            citation=citation(
                ctx.refdata,
                "PFS-RVU",
                {"code": line.code, "facility": int(facility)},
            ),
            evidence={"ratio_x100": ratio_x100},
        )
        findings.append(with_id(finding))
    return findings
