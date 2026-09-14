"""Unit tests for the extraction scoring functions (Task B) -- no network, no
model calls, hand-built ground truth vs "extracted" fixtures only."""

from datetime import date

import pytest

from countercharge_engine.models import (
    EOB,
    Bill,
    EOBLine,
    LineItem,
    Network,
    Provider,
    Setting,
    Totals,
)

from countercharge_evals.extraction_score import (
    DocResult,
    ExtractionScorecard,
    eob_scalars_match,
    failed_doc,
    normalize_name,
    provider_name_matches,
    score_bill,
    score_bill_lines,
    score_eob,
    score_eob_lines,
    totals_match,
)


def _line(line_id="L1", code="99284", units=1, charge_cents=18500, dos="2026-07-23") -> LineItem:
    return LineItem(line_id=line_id, dos=date.fromisoformat(dos), code=code, code_type="CPT", units=units, charge_cents=charge_cents)


def _bill(*, provider_name="Cleveland Clinic", lines=None, charges_cents=18500, patient_balance_cents=18500) -> Bill:
    return Bill(
        provider=Provider(name=provider_name),
        account_no="ACC-1",
        statement_date=date(2026, 8, 1),
        setting=Setting.ER,
        lines=lines if lines is not None else [_line()],
        totals=Totals(charges_cents=charges_cents, patient_balance_cents=patient_balance_cents),
    )


def _eob_line(code="99284", dos="2026-07-23", billed_cents=185000, allowed_cents=84000, plan_paid_cents=64000, patient_resp_cents=20000) -> EOBLine:
    return EOBLine(
        code=code,
        dos=date.fromisoformat(dos),
        billed_cents=billed_cents,
        allowed_cents=allowed_cents,
        plan_paid_cents=plan_paid_cents,
        patient_resp_cents=patient_resp_cents,
    )


def _eob(*, payer="Anthem BCBS", claim_no="CLM-1001", network=Network.IN, emergency=False, lines=None, total_patient_resp_cents=20000) -> EOB:
    return EOB(
        payer=payer,
        claim_no=claim_no,
        network=network,
        emergency=emergency,
        lines=lines if lines is not None else [_eob_line()],
        total_patient_resp_cents=total_patient_resp_cents,
    )


# -- provider name fuzzy matching --------------------------------------------


def test_normalize_name_strips_punctuation_and_case():
    assert normalize_name("NewYork-Presbyterian Hospital") == "newyork presbyterian hospital"
    assert normalize_name("  Cleveland   Clinic. ") == "cleveland clinic"


def test_provider_name_matches_exact_after_normalization():
    assert provider_name_matches("Cleveland Clinic", "cleveland clinic") is True


def test_provider_name_matches_fuzzy_near_miss():
    # a plausible OCR-style near miss: missing hyphen / extra space
    assert provider_name_matches("NewYork-Presbyterian Hospital", "New York Presbyterian Hospital") is True


def test_provider_name_matches_rejects_a_different_hospital():
    assert provider_name_matches("Cleveland Clinic", "NewYork-Presbyterian Hospital") is False


def test_provider_name_matches_rejects_empty_strings():
    assert provider_name_matches("", "Cleveland Clinic") is False
    assert provider_name_matches("Cleveland Clinic", "") is False


# -- line item multiset matching ---------------------------------------------


def test_score_bill_lines_exact_match():
    expected = [_line(line_id="L1"), _line(line_id="L2", code="85025", charge_cents=11300)]
    actual = [_line(line_id="X1"), _line(line_id="X2", code="85025", charge_cents=11300)]  # different (unprinted) ids
    result = score_bill_lines(expected, actual)
    assert result.exact_match is True
    assert result.missing == []
    assert result.extra == []


def test_score_bill_lines_exact_match_ignores_order():
    a = _line(line_id="L1", code="85025", charge_cents=11300)
    b = _line(line_id="L2", code="99284", charge_cents=18500)
    result = score_bill_lines([a, b], [b, a])
    assert result.exact_match is True


def test_score_bill_lines_handles_true_duplicates_by_count():
    # DUPLICATE-category bills legitimately have two identical lines; the multiset
    # comparison must require the same *count*, not just "present at least once".
    dup = _line(line_id="L1", code="85025", charge_cents=11300)
    expected = [dup, _line(line_id="L2", code="85025", charge_cents=11300)]
    actual_missing_one = [dup]
    result = score_bill_lines(expected, actual_missing_one)
    assert result.exact_match is False
    assert result.missing == [("85025", 1, 11300, "2026-07-23")]


def test_score_bill_lines_reports_missing_and_extra():
    expected = [_line(code="99284", charge_cents=18500)]
    actual = [_line(code="99285", charge_cents=25000)]
    result = score_bill_lines(expected, actual)
    assert result.exact_match is False
    assert result.missing == [("99284", 1, 18500, "2026-07-23")]
    assert result.extra == [("99285", 1, 25000, "2026-07-23")]


def test_score_eob_lines_exact_and_mismatch():
    expected = [_eob_line()]
    assert score_eob_lines(expected, [_eob_line()]).exact_match is True
    assert score_eob_lines(expected, [_eob_line(allowed_cents=1)]).exact_match is False


# -- totals / eob scalars -----------------------------------------------------


def test_totals_match_true_and_false():
    expected = _bill()
    assert totals_match(expected, _bill()) is True
    assert totals_match(expected, _bill(charges_cents=99999)) is False


def test_eob_scalars_match_true_and_false():
    expected = _eob()
    assert eob_scalars_match(expected, _eob()) is True
    assert eob_scalars_match(expected, _eob(claim_no="CLM-9999")) is False
    assert eob_scalars_match(expected, _eob(network=Network.OUT)) is False


# -- full document scoring ----------------------------------------------------


def test_score_bill_exact_match_case():
    expected = _bill()
    result = score_bill("case_1", "CLEAN", expected, _bill())
    assert result.exact_match is True
    assert result.issues == []


def test_score_bill_mismatch_case_lists_every_issue():
    expected = _bill(provider_name="Cleveland Clinic", charges_cents=18500, patient_balance_cents=18500)
    actual = _bill(provider_name="Some Other Hospital", charges_cents=99999, patient_balance_cents=99999, lines=[_line(code="00000")])
    result = score_bill("case_1", "CLEAN", expected, actual)
    assert result.exact_match is False
    assert any("provider name mismatch" in issue for issue in result.issues)
    assert any("totals mismatch" in issue for issue in result.issues)
    assert any("missing line" in issue or "unexpected line" in issue for issue in result.issues)


def test_score_bill_fuzzy_provider_name_does_not_count_as_mismatch():
    expected = _bill(provider_name="NewYork-Presbyterian Hospital")
    actual = _bill(provider_name="New York Presbyterian Hospital")
    result = score_bill("case_1", "CLEAN", expected, actual)
    assert result.provider_match is True
    assert result.exact_match is True


def test_score_eob_exact_and_mismatch():
    expected = _eob()
    ok = score_eob("eob_1", "EOB_BALANCE_BILLING", expected, _eob())
    assert ok.exact_match is True

    bad = score_eob("eob_1", "EOB_BALANCE_BILLING", expected, _eob(payer="Some Other Payer"))
    assert bad.exact_match is False
    assert any("eob field mismatch" in issue for issue in bad.issues)


def test_failed_doc_is_never_an_exact_match():
    result = failed_doc("case_1", "bill", "CLEAN", "rate limited after 4 attempts")
    assert result.exact_match is False
    assert result.extraction_error == "rate limited after 4 attempts"
    assert "extraction failed" in result.issues[0]


# -- scorecard aggregation -----------------------------------------------------


def test_extraction_scorecard_aggregates_rates_and_mismatches():
    scorecard = ExtractionScorecard()
    scorecard.add(score_bill("c1", "CLEAN", _bill(), _bill()))
    scorecard.add(score_bill("c2", "DUPLICATE", _bill(), _bill(provider_name="Wrong Hospital Name")))
    scorecard.add(score_eob("c3", "EOB_BALANCE_BILLING", _eob(), _eob()))
    scorecard.add(failed_doc("c4", "bill", "MULTI", "timed out"))

    result = scorecard.to_dict()

    assert result["documents_total"] == 4
    assert result["bill_documents"] == 3
    assert result["eob_documents"] == 1
    assert result["bill_line_exact_match_rate"] == 1.0  # all three bills had matching lines
    assert result["documents_exact_match_rate"] == 0.5  # 2 of 4 exact
    assert len(result["mismatches"]) == 2
    assert {m["case_id"] for m in result["mismatches"]} == {"c2", "c4"}
    assert result["extraction_failures"] == [{"case_id": "c4", "kind": "bill", "error": "timed out"}]
    assert result["per_category"]["CLEAN"]["exact_match_rate"] == 1.0
    assert result["per_category"]["DUPLICATE"]["exact_match_rate"] == 0.0


def test_extraction_scorecard_handles_no_documents_of_a_kind():
    scorecard = ExtractionScorecard()
    scorecard.add(score_bill("c1", "CLEAN", _bill(), _bill()))
    result = scorecard.to_dict()
    assert result["eob_line_exact_match_rate"] is None
    assert result["eob_scalar_exact_match_rate"] is None
