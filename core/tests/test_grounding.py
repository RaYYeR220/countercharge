from datetime import date

from countercharge_engine.models import (
    Citation,
    Finding,
    LineItem,
    Bill,
    Provider,
    Totals,
    CodeType,
    Setting,
)

from countercharge_core.grounding import GroundingResult, verify_letter


def make_finding(amount_cents=12345, line_ids=("l1",), disputable=True, rule_id="R1"):
    return Finding(
        rule_id=rule_id,
        disputable=disputable,
        line_ids=list(line_ids),
        amount_cents=amount_cents,
        title="Duplicate charge",
        detail="detail",
        citation=Citation(dataset="ds", version="v1", url="https://example.com", record={}),
    )


def make_bill():
    return Bill(
        provider=Provider(name="NYP", hospital_id="nyp"),
        account_no="acct-1",
        statement_date=date(2026, 1, 1),
        setting=Setting.OUTPATIENT,
        lines=[
            LineItem(
                line_id="l1",
                dos=date(2026, 1, 1),
                code="99213",
                code_type=CodeType.CPT,
                units=1,
                charge_cents=12345,
                description="office visit",
            ),
            LineItem(
                line_id="l2",
                dos=date(2026, 1, 1),
                code="J1100",
                code_type=CodeType.HCPCS,
                units=1,
                charge_cents=5000,
                description="injection",
            ),
        ],
        totals=Totals(charges_cents=17345, patient_balance_cents=17345),
    )


def test_letter_citing_exact_finding_amount_is_ok():
    finding = make_finding(amount_cents=12345)
    letter = "We are disputing a duplicate charge of $123.45 billed under code 99213."
    result = verify_letter(letter, [finding], make_bill())
    assert isinstance(result, GroundingResult)
    assert result.ok
    assert result.unsupported_amounts == []
    assert result.unsupported_codes == []


def test_invented_amount_is_unsupported():
    finding = make_finding(amount_cents=12345)
    letter = "We are disputing a charge of $123.45, and also demand $999.00 in damages."
    result = verify_letter(letter, [finding], make_bill())
    assert not result.ok
    assert "$999.00" in result.unsupported_amounts
    assert "$123.45" not in result.unsupported_amounts


def test_code_not_on_bill_is_unsupported():
    finding = make_finding(amount_cents=12345, line_ids=["l1"])
    letter = "Code 99213 was billed, but code 71046 was never performed."
    result = verify_letter(letter, [finding], make_bill())
    assert not result.ok
    assert "71046" in result.unsupported_codes
    assert "99213" not in result.unsupported_codes


def test_hcpcs_code_on_bill_is_supported():
    finding = make_finding(amount_cents=5000, line_ids=["l2"])
    letter = "The injection under J1100 for $50.00 should not have been charged."
    result = verify_letter(letter, [finding], make_bill())
    assert result.ok


def test_commas_and_decimals_are_parsed():
    finding = make_finding(amount_cents=1234500)
    letter = "The total disputed amount is $12,345.00 exactly."
    result = verify_letter(letter, [finding], make_bill())
    assert result.ok


def test_commas_without_decimals_are_parsed():
    finding = make_finding(amount_cents=1234500)
    letter = "The total disputed amount is $12,345 exactly."
    result = verify_letter(letter, [finding], make_bill())
    assert result.ok


def test_sum_of_cited_disputable_amounts_is_allowed():
    f1 = make_finding(amount_cents=10000, line_ids=["l1"], rule_id="R1")
    f2 = make_finding(amount_cents=5000, line_ids=["l2"], rule_id="R2")
    letter = "Combined, these two errors total $150.00 in overcharges."
    result = verify_letter(letter, [f1, f2], make_bill())
    assert result.ok


def test_bill_total_patient_balance_is_allowed():
    finding = make_finding(amount_cents=10000)
    letter = "Your total patient balance of $173.45 includes disputed charges."
    result = verify_letter(letter, [finding], make_bill())
    assert result.ok


def test_no_bill_still_checks_amounts_against_findings():
    finding = make_finding(amount_cents=12345)
    letter = "We dispute $123.45."
    result = verify_letter(letter, [finding], None)
    assert result.ok


def test_no_bill_any_code_is_unsupported():
    finding = make_finding(amount_cents=12345)
    letter = "Code 99213 should not have been billed."
    result = verify_letter(letter, [finding], None)
    assert not result.ok
    assert "99213" in result.unsupported_codes


def test_five_digit_dollar_amount_not_mistaken_for_code():
    finding = make_finding(amount_cents=5000000)
    letter = "The disputed charge of $50000 is excessive."
    result = verify_letter(letter, [finding], make_bill())
    assert result.unsupported_codes == []
