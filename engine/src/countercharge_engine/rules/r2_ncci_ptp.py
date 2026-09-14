"""R2 -- NCCI procedure-to-procedure (PTP) edits.

For every ordered pair of same-day lines, look up the PTP edit for
(a.code as column 1, b.code as column 2). A modifier indicator of 0
means the edit can never be unbundled; 1 means an appropriate NCCI
modifier on either line allows it; 9 means the pair is never bundled.
Because the lookup is directional, checking both orderings of a line
pair naturally applies only the actual edit row's direction -- the
reverse lookup returns ``None`` unless a distinct edit exists for it.
"""

from countercharge_engine.canonical import with_id
from countercharge_engine.models import Finding, LineItem
from countercharge_engine.rules.common import (
    NCCI_MODIFIERS,
    AuditContext,
    citation,
    ptp_dataset,
    ptp_table,
)

RULE_ID = "NCCI_PTP"


def _has_ncci_modifier(*lines: LineItem) -> bool:
    return any(m in NCCI_MODIFIERS for line in lines for m in line.modifiers)


def check(ctx: AuditContext) -> list[Finding]:
    table = ptp_table(ctx.bill.setting)
    dataset = ptp_dataset(table)
    lines = ctx.bill.lines

    findings: list[Finding] = []
    for col1_line in lines:
        for col2_line in lines:
            if col1_line is col2_line or col1_line.dos != col2_line.dos:
                continue
            edit = ctx.refdata.ptp_edit(col1_line.code, col2_line.code, table, col1_line.dos)
            if edit is None or edit.modifier_ind == 9:
                continue
            if edit.modifier_ind == 1 and _has_ncci_modifier(col1_line, col2_line):
                continue

            finding = Finding(
                rule_id=RULE_ID,
                disputable=True,
                line_ids=[col1_line.line_id, col2_line.line_id],
                amount_cents=col2_line.charge_cents,
                title="Bundled procedure billed separately",
                detail=(
                    f"Code {col2_line.code} is bundled into code {col1_line.code} "
                    "under Medicare's procedure-to-procedure edits and should not "
                    "be billed as a separate charge on the same day."
                ),
                citation=citation(
                    ctx.refdata,
                    dataset,
                    {"col1": col1_line.code, "col2": col2_line.code},
                ),
                evidence={
                    "modifier_ind": edit.modifier_ind,
                    "rationale": edit.rationale,
                    "target_key": col2_line.line_id,
                },
            )
            findings.append(with_id(finding))
    return findings
