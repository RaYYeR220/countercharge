"""Field-level scoring of one extracted ``Bill``/``EOB`` against its case-JSON
ground truth (Task B).

Pure comparison logic -- no network, no model calls, no filesystem access --
so it can be unit tested with hand-built fixtures and re-run against cached
raw extractor outputs without ever touching Venice. ``run_extraction.py`` is
the only module here that calls the vision extractor or does I/O.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass, field

from countercharge_engine.models import EOB, Bill, EOBLine, LineItem

# -- provider name (fuzzy, normalized) --------------------------------------


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace."""
    text = name.strip().lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def provider_name_matches(expected: str, actual: str, *, threshold: float = 0.85) -> bool:
    """Fuzzy match: exact after normalization, or a near-miss above ``threshold``."""
    norm_expected, norm_actual = normalize_name(expected), normalize_name(actual)
    if not norm_expected or not norm_actual:
        return False
    if norm_expected == norm_actual:
        return True
    return difflib.SequenceMatcher(None, norm_expected, norm_actual).ratio() >= threshold


# -- line items (exact: code, units, charge_cents, dos) ----------------------

LineKey = tuple[str, int, int, str]
EobLineKey = tuple[str, str, int, int, int, int]


def _line_key(line: LineItem) -> LineKey:
    return (line.code, line.units, line.charge_cents, line.dos.isoformat())


def _eob_line_key(line: EOBLine) -> EobLineKey:
    return (line.code, line.dos.isoformat(), line.billed_cents, line.allowed_cents, line.plan_paid_cents, line.patient_resp_cents)


@dataclass
class LineMatchResult:
    expected_count: int
    actual_count: int
    matched_count: int
    missing: list[tuple] = field(default_factory=list)
    extra: list[tuple] = field(default_factory=list)

    @property
    def exact_match(self) -> bool:
        return not self.missing and not self.extra and self.expected_count == self.actual_count


def _score_multiset(expected_keys: list[tuple], actual_keys: list[tuple]) -> LineMatchResult:
    expected_counter = Counter(expected_keys)
    actual_counter = Counter(actual_keys)
    matched = expected_counter & actual_counter
    return LineMatchResult(
        expected_count=sum(expected_counter.values()),
        actual_count=sum(actual_counter.values()),
        matched_count=sum(matched.values()),
        missing=sorted((expected_counter - actual_counter).elements()),
        extra=sorted((actual_counter - expected_counter).elements()),
    )


def score_bill_lines(expected: list[LineItem], actual: list[LineItem]) -> LineMatchResult:
    """Multiset comparison on ``(code, units, charge_cents, dos)`` -- line ids are not
    printed on the rendered bill, so line order/identity is not part of the contract."""
    return _score_multiset([_line_key(line) for line in expected], [_line_key(line) for line in actual])


def score_eob_lines(expected: list[EOBLine], actual: list[EOBLine]) -> LineMatchResult:
    return _score_multiset([_eob_line_key(line) for line in expected], [_eob_line_key(line) for line in actual])


# -- totals / scalars (exact) -------------------------------------------------


def totals_match(expected: Bill, actual: Bill) -> bool:
    return (
        expected.totals.charges_cents == actual.totals.charges_cents
        and expected.totals.adjustments_cents == actual.totals.adjustments_cents
        and expected.totals.payments_cents == actual.totals.payments_cents
        and expected.totals.patient_balance_cents == actual.totals.patient_balance_cents
    )


def eob_scalars_match(expected: EOB, actual: EOB) -> bool:
    return (
        expected.payer.strip() == actual.payer.strip()
        and expected.claim_no.strip() == actual.claim_no.strip()
        and expected.network == actual.network
        and expected.emergency == actual.emergency
        and expected.total_patient_resp_cents == actual.total_patient_resp_cents
    )


# -- per-document result ------------------------------------------------------


@dataclass
class DocResult:
    """The full comparison of one extracted document (a bill or an EOB) against
    its ground truth. ``issues`` lists every mismatch found, in plain English --
    never hidden or summarized away."""

    case_id: str
    kind: str  # "bill" | "eob"
    category: str
    lines: LineMatchResult
    provider_match: bool | None = None  # bill only
    totals_ok: bool | None = None  # bill only
    scalars_ok: bool | None = None  # eob only
    issues: list[str] = field(default_factory=list)
    extraction_error: str | None = None

    @property
    def exact_match(self) -> bool:
        if self.extraction_error is not None:
            return False
        if not self.lines.exact_match:
            return False
        if self.kind == "bill":
            return bool(self.totals_ok) and bool(self.provider_match)
        return bool(self.scalars_ok)


def score_bill(case_id: str, category: str, expected: Bill, actual: Bill) -> DocResult:
    lines = score_bill_lines(expected.lines, actual.lines)
    provider_ok = provider_name_matches(expected.provider.name, actual.provider.name)
    tot_ok = totals_match(expected, actual)

    issues: list[str] = []
    if not lines.exact_match:
        if lines.missing:
            issues.append(f"missing line(s): {lines.missing}")
        if lines.extra:
            issues.append(f"unexpected line(s): {lines.extra}")
    if not provider_ok:
        issues.append(f"provider name mismatch: expected {expected.provider.name!r}, got {actual.provider.name!r}")
    if not tot_ok:
        issues.append(
            "totals mismatch: expected "
            f"{expected.totals.model_dump()}, got {actual.totals.model_dump()}"
        )

    return DocResult(
        case_id=case_id,
        kind="bill",
        category=category,
        lines=lines,
        provider_match=provider_ok,
        totals_ok=tot_ok,
        issues=issues,
    )


def score_eob(case_id: str, category: str, expected: EOB, actual: EOB) -> DocResult:
    lines = score_eob_lines(expected.lines, actual.lines)
    scalars_ok = eob_scalars_match(expected, actual)

    issues: list[str] = []
    if not lines.exact_match:
        if lines.missing:
            issues.append(f"missing eob line(s): {lines.missing}")
        if lines.extra:
            issues.append(f"unexpected eob line(s): {lines.extra}")
    if not scalars_ok:
        issues.append(
            "eob field mismatch: expected "
            f"{{'payer': {expected.payer!r}, 'claim_no': {expected.claim_no!r}, 'network': {expected.network!r}, "
            f"'emergency': {expected.emergency!r}, 'total_patient_resp_cents': {expected.total_patient_resp_cents!r}}}, got "
            f"{{'payer': {actual.payer!r}, 'claim_no': {actual.claim_no!r}, 'network': {actual.network!r}, "
            f"'emergency': {actual.emergency!r}, 'total_patient_resp_cents': {actual.total_patient_resp_cents!r}}}"
        )

    return DocResult(case_id=case_id, kind="eob", category=category, lines=lines, scalars_ok=scalars_ok, issues=issues)


def failed_doc(case_id: str, kind: str, category: str, error: str) -> DocResult:
    """A document whose extraction call never produced usable output (exhausted retries)."""
    return DocResult(
        case_id=case_id,
        kind=kind,
        category=category,
        lines=LineMatchResult(expected_count=0, actual_count=0, matched_count=0),
        issues=[f"extraction failed: {error}"],
        extraction_error=error,
    )


# -- aggregate scorecard -------------------------------------------------------


@dataclass
class ExtractionScorecard:
    docs: list[DocResult] = field(default_factory=list)

    def add(self, result: DocResult) -> None:
        self.docs.append(result)

    def _by_kind(self, kind: str) -> list[DocResult]:
        return [d for d in self.docs if d.kind == kind]

    def _rate(self, docs: list[DocResult], predicate) -> float | None:
        if not docs:
            return None
        return sum(1 for d in docs if predicate(d)) / len(docs)

    def per_category(self) -> dict[str, dict]:
        categories = sorted({d.category for d in self.docs})
        out: dict[str, dict] = {}
        for cat in categories:
            docs = [d for d in self.docs if d.category == cat]
            out[cat] = {
                "documents": len(docs),
                "exact_match_documents": sum(1 for d in docs if d.exact_match),
                "exact_match_rate": self._rate(docs, lambda d: d.exact_match),
            }
        return out

    def to_dict(self) -> dict:
        bills = self._by_kind("bill")
        eobs = self._by_kind("eob")
        return {
            "documents_total": len(self.docs),
            "bill_documents": len(bills),
            "eob_documents": len(eobs),
            "bill_line_exact_match_rate": self._rate(bills, lambda d: d.lines.exact_match),
            "bill_totals_exact_match_rate": self._rate(bills, lambda d: bool(d.totals_ok)),
            "provider_name_fuzzy_match_rate": self._rate(bills, lambda d: bool(d.provider_match)),
            "eob_line_exact_match_rate": self._rate(eobs, lambda d: d.lines.exact_match),
            "eob_scalar_exact_match_rate": self._rate(eobs, lambda d: bool(d.scalars_ok)),
            "documents_exact_match_rate": self._rate(self.docs, lambda d: d.exact_match),
            "extraction_failures": [
                {"case_id": d.case_id, "kind": d.kind, "error": d.extraction_error}
                for d in self.docs
                if d.extraction_error is not None
            ],
            "per_category": self.per_category(),
            "mismatches": [
                {
                    "case_id": d.case_id,
                    "kind": d.kind,
                    "category": d.category,
                    "issues": d.issues,
                }
                for d in self.docs
                if not d.exact_match
            ],
        }
