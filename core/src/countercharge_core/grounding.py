"""Letter grounding verifier: every dollar amount and code cited in a dispute
letter must trace back to a cited finding or the underlying bill -- nothing
invented.
"""

import re
from dataclasses import dataclass

from countercharge_engine.models import Bill, Finding

AMOUNT_RE = re.compile(r"\$\s*(\d[\d,]*(?:\.\d{1,2})?)")
CODE_RE = re.compile(r"\b(?:[A-Z]\d{4}|\d{5})\b")


@dataclass
class GroundingResult:
    ok: bool
    unsupported_amounts: list[str]
    unsupported_codes: list[str]


def _dollars_to_cents(raw: str) -> int:
    raw = raw.replace(",", "")
    if "." in raw:
        dollars, cents = raw.split(".", 1)
        cents = (cents + "00")[:2]
    else:
        dollars, cents = raw, "00"
    dollars = dollars or "0"
    return int(dollars) * 100 + int(cents)


def _allowed_amounts_cents(findings: list[Finding], bill: Bill | None) -> set[int]:
    allowed: set[int] = set()
    for finding in findings:
        allowed.add(finding.amount_cents)
    disputable_sum = sum(f.amount_cents for f in findings if f.disputable)
    allowed.add(disputable_sum)
    if bill is not None:
        totals = bill.totals
        allowed.update(
            {
                totals.charges_cents,
                totals.adjustments_cents,
                totals.payments_cents,
                totals.patient_balance_cents,
            }
        )
        lines_by_id = {line.line_id: line for line in bill.lines}
        for finding in findings:
            for line_id in finding.line_ids:
                line = lines_by_id.get(line_id)
                if line is not None:
                    allowed.add(line.charge_cents)
    return allowed


def _allowed_codes(bill: Bill | None) -> set[str]:
    if bill is None:
        return set()
    return {line.code for line in bill.lines}


def verify_letter(letter_markdown: str, findings: list[Finding], bill: Bill | None) -> GroundingResult:
    amount_matches = list(AMOUNT_RE.finditer(letter_markdown))

    # Mask out matched dollar-amount spans before scanning for codes so a
    # 5-digit run inside a dollar figure (e.g. "$50000") is never mistaken
    # for an HCPCS/CPT code.
    masked = list(letter_markdown)
    for match in amount_matches:
        for i in range(match.start(), match.end()):
            masked[i] = " "
    text_without_amounts = "".join(masked)

    allowed_amounts = _allowed_amounts_cents(findings, bill)
    allowed_codes = _allowed_codes(bill)

    unsupported_amounts: list[str] = []
    seen_amounts: set[str] = set()
    for match in amount_matches:
        cents = _dollars_to_cents(match.group(1))
        if cents not in allowed_amounts and match.group(0) not in seen_amounts:
            unsupported_amounts.append(match.group(0))
            seen_amounts.add(match.group(0))

    unsupported_codes: list[str] = []
    seen_codes: set[str] = set()
    for code in CODE_RE.findall(text_without_amounts):
        if code not in allowed_codes and code not in seen_codes:
            unsupported_codes.append(code)
            seen_codes.add(code)

    ok = not unsupported_amounts and not unsupported_codes
    return GroundingResult(ok=ok, unsupported_amounts=unsupported_amounts, unsupported_codes=unsupported_codes)
