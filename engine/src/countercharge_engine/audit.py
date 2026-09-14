"""``audit()`` orchestrator: run rules R1-R10 over a bill and roll their
findings into a single :class:`~countercharge_engine.models.AuditReport`.

Rule ids and their category:

- Line-level disputable rules: ``DUPLICATE``, ``NCCI_PTP``, ``MUE``,
  ``CASH_PRICE`` -- each finding points at one or more specific bill
  lines via ``line_ids``.
- Balance-level disputable rules: ``ARITHMETIC``, ``EOB_BALANCE_BILLING``,
  ``NSA_EMERGENCY``, ``NSA_GFE`` -- each finding is about the bill's
  totals as a whole, not any one line.
- ``FAP_501R`` findings are always non-disputable (an application
  opportunity, not a dispute) and never contribute to ``disputable_cents``.
- ``MEDICARE_BENCHMARK`` is advisory only and goes in ``advisory``, not
  ``findings``.
"""

from datetime import date

from countercharge_engine.models import AuditReport, Bill, EOB, Finding, GFE, Household
from countercharge_engine.refdata.base import RefData
from countercharge_engine.rules import (
    r1_duplicate,
    r2_ncci_ptp,
    r3_mue,
    r4_arithmetic,
    r5_eob,
    r6_nsa_emergency,
    r7_nsa_gfe,
    r8_cash_price,
    r9_fap,
    r10_benchmark,
)
from countercharge_engine.rules.common import AuditContext, RuleFn

RULES: list[RuleFn] = [
    r1_duplicate.check,
    r2_ncci_ptp.check,
    r3_mue.check,
    r4_arithmetic.check,
    r5_eob.check,
    r6_nsa_emergency.check,
    r7_nsa_gfe.check,
    r8_cash_price.check,
    r9_fap.check,
    r10_benchmark.check,
]

_ADVISORY_RULE_IDS = frozenset({r10_benchmark.RULE_ID})

_LINE_LEVEL_RULE_IDS = frozenset(
    {r1_duplicate.RULE_ID, r2_ncci_ptp.RULE_ID, r3_mue.RULE_ID, r8_cash_price.RULE_ID}
)
_BALANCE_LEVEL_RULE_IDS = frozenset(
    {r4_arithmetic.RULE_ID, r5_eob.RULE_ID, r6_nsa_emergency.RULE_ID, r7_nsa_gfe.RULE_ID}
)


def _disputable_cents(findings: list[Finding], patient_balance_cents: int) -> int:
    """Roll disputable findings into a single deduplicated dollar total.

    Line-level findings often overlap: the same line can be claimed by more
    than one rule (e.g. a duplicated line that's also above the hospital's
    cash price). To avoid double-counting the same dollars, line-level
    findings are merged by *connected component* over their ``line_ids``
    (union-find): any two findings that share at least one line id join the
    same component, and each component contributes only its single largest
    ``amount_cents`` to the total -- not the sum of every finding that
    touched it.

    Balance-level findings are about the bill's totals as a whole rather
    than any particular line, so they can't be merged the same way; instead
    only the single largest balance-level amount is counted, since they are
    different ways of describing the same overbilled balance.

    The final total is ``line_level_sum + balance_level_max``, capped at the
    bill's reported patient balance whenever that balance is positive (a
    patient can't be found to owe a dispute credit larger than what they
    were actually billed). When the reported balance is zero or negative,
    the cap is skipped and only the line-level sum is reported.
    """
    line_level = [f for f in findings if f.disputable and f.rule_id in _LINE_LEVEL_RULE_IDS]
    balance_level = [f for f in findings if f.disputable and f.rule_id in _BALANCE_LEVEL_RULE_IDS]

    parent: dict[str, str] = {}

    def find(line_id: str) -> str:
        parent.setdefault(line_id, line_id)
        root = line_id
        while parent[root] != root:
            root = parent[root]
        while parent[line_id] != root:
            parent[line_id], line_id = root, parent[line_id]
        return root

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_a] = root_b

    for finding in line_level:
        for line_id in finding.line_ids[1:]:
            union(finding.line_ids[0], line_id)

    component_max: dict[str, int] = {}
    for finding in line_level:
        if not finding.line_ids:
            continue
        root = find(finding.line_ids[0])
        component_max[root] = max(component_max.get(root, 0), finding.amount_cents)

    line_level_sum = sum(component_max.values())
    balance_level_max = max((f.amount_cents for f in balance_level), default=0)

    if patient_balance_cents > 0:
        return min(line_level_sum + balance_level_max, patient_balance_cents)
    return line_level_sum


def audit(
    bill: Bill,
    *,
    refdata: RefData,
    eob: EOB | None = None,
    household: Household | None = None,
    gfe: GFE | None = None,
    as_of: date | None = None,
) -> AuditReport:
    """Run rules R1-R10 against ``bill`` and return the combined report.

    ``findings`` holds every non-advisory finding from R1-R9 (including
    ``FAP_501R``'s non-disputable ones); ``advisory`` holds only R10's
    ``MEDICARE_BENCHMARK`` findings. ``disputable_cents`` is the deduplicated
    dollar total described in :func:`_disputable_cents`. ``as_of`` defaults
    to ``bill.statement_date`` when not given.
    """
    ctx = AuditContext(
        bill=bill,
        eob=eob,
        household=household,
        gfe=gfe,
        refdata=refdata,
        as_of=as_of if as_of is not None else bill.statement_date,
    )

    findings: list[Finding] = []
    advisory: list[Finding] = []
    for rule in RULES:
        for finding in rule(ctx):
            if finding.rule_id in _ADVISORY_RULE_IDS:
                advisory.append(finding)
            else:
                findings.append(finding)

    disputable_cents = _disputable_cents(findings, bill.totals.patient_balance_cents)
    fap = r9_fap.evaluate_fap(ctx)

    return AuditReport(
        findings=findings,
        advisory=advisory,
        disputable_cents=disputable_cents,
        fap=fap,
        refdata_version=refdata.version,
    )
