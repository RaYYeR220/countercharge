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


def _is_mue_group_finding(finding: Finding) -> bool:
    """True for an MAI 2/3 grouped MUE finding, whose target is a
    ``"{code}|{dos}"`` key standing in for every line in the group -- as
    opposed to an MAI 1 MUE finding, whose target is a single line id like
    any other line-level rule's."""
    return finding.rule_id == r3_mue.RULE_ID and finding.evidence.get("mai") in (2, 3)


def _line_level_sum(findings: list[Finding]) -> int:
    """Sum disputable line-level findings, deduplicated by ``evidence
    ["target_key"]``: the single bill line (or, for a grouped MAI 2/3 MUE
    finding, the ``"{code}|{dos}"`` group) each finding proves an overcharge
    against. Two findings that name the same target key contribute only the
    larger of their two amounts; findings with different target keys are
    independently proven overcharges and are summed in full.

    An MUE group key and a plain line key can legitimately overlap on the
    same lines (e.g. a line that's both part of an MAI 2/3 MUE group and
    separately over its hospital's cash price). Summing both in full would
    double-count part of the same dollars, but simply taking the group's own
    amount could under-count a group whose lines are individually proven to
    be overcharged by more than the MUE excess alone. So for each MUE group,
    if any of its lines is also the target of another finding, the group
    contributes ``max(group amount, sum of those overlapping line-key
    amounts)`` instead, and those overlapping line keys are excluded from
    the plain per-key sum (they've already been accounted for by the group).
    """
    group_findings = [f for f in findings if _is_mue_group_finding(f)]
    other_findings = [f for f in findings if not _is_mue_group_finding(f)]

    other_max_by_key: dict[str, int] = {}
    for finding in other_findings:
        key = finding.evidence.get("target_key")
        if key is None:
            continue
        other_max_by_key[key] = max(other_max_by_key.get(key, 0), finding.amount_cents)

    consumed_keys: set[str] = set()
    group_total = 0
    for finding in group_findings:
        overlapping = [other_max_by_key[lid] for lid in finding.line_ids if lid in other_max_by_key]
        group_total += max(finding.amount_cents, sum(overlapping)) if overlapping else finding.amount_cents
        consumed_keys.update(lid for lid in finding.line_ids if lid in other_max_by_key)

    other_total = sum(amount for key, amount in other_max_by_key.items() if key not in consumed_keys)
    return other_total + group_total


def _disputable_cents(findings: list[Finding], patient_balance_cents: int) -> int:
    """Roll disputable findings into a single deduplicated dollar total.

    Line-level findings often overlap: the same line can be claimed by more
    than one rule (e.g. a duplicated line that's also above the hospital's
    cash price). To avoid double-counting the same dollars, each line-level
    finding records the specific target its amount is proven against in
    ``evidence["target_key"]`` (the duplicate copy's line id, the NCCI PTP
    column-2 line id, the MUE MAI-1 line id, the MUE MAI-2/3 group's
    ``"{code}|{dos}"`` key, or the cash-price line id), and ``_line_level_sum``
    sums those findings deduplicated by that key rather than by any line id
    the finding happens to mention (see its docstring for the MUE-group
    overlap case). This fixes the previous connected-component (union-find)
    approach, which merged findings by *any* shared line id -- including a
    line that's merely a *reference* (e.g. a duplicate group's "original"
    line) rather than the one actually proven overcharged -- and so could
    collapse multiple genuinely distinct overcharges on the same physical
    line into a single amount.

    Balance-level findings are about the bill's totals as a whole rather
    than any particular line, so they can't be deduplicated the same way;
    instead only the single largest balance-level amount is counted, since
    they are different ways of describing the same overbilled balance.

    The final total is ``line_level_sum + balance_level_max``, capped at the
    bill's reported patient balance whenever that balance is positive (a
    patient can't be found to owe a dispute credit larger than what they
    were actually billed). When the reported balance is zero or negative,
    the cap is skipped and only the line-level sum is reported.
    """
    line_level = [f for f in findings if f.disputable and f.rule_id in _LINE_LEVEL_RULE_IDS]
    balance_level = [f for f in findings if f.disputable and f.rule_id in _BALANCE_LEVEL_RULE_IDS]

    line_level_sum = _line_level_sum(line_level)
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
