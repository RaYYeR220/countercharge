from datetime import date

import pytest

from countercharge_tools import engine_tools
from countercharge_tools.deps import ToolError

from conftest import TODAY_YEAR, make_bill


def test_check_code_pair_modifier_1_is_disputable_without_modifier(refdata):
    result = engine_tools.check_code_pair(refdata, code1="99213", code2="99214", setting="ER", dos="2026-01-10")
    assert result["disputable"] is True
    assert result["edit"]["modifier_ind"] == 1
    assert "modifier" in result["explanation"].lower()


def test_check_code_pair_modifier_0_always_disputable(refdata):
    result = engine_tools.check_code_pair(refdata, code1="99215", code2="99216", setting="ER", dos="2026-01-10")
    assert result["disputable"] is True
    assert result["edit"]["modifier_ind"] == 0


def test_check_code_pair_checks_reverse_direction(refdata):
    # Edit is stored as (col1=99213, col2=99214); querying reversed order
    # should still find it (a caller doesn't know which is "column 1").
    result = engine_tools.check_code_pair(refdata, code1="99214", code2="99213", setting="ER", dos="2026-01-10")
    assert result["disputable"] is True


def test_check_code_pair_no_edit(refdata):
    result = engine_tools.check_code_pair(refdata, code1="00001", code2="00002", setting="ER", dos="2026-01-10")
    assert result == {"edit": None, "disputable": False, "explanation": result["explanation"]}
    assert result["edit"] is None
    assert result["disputable"] is False


def test_check_units_exceeds(refdata):
    result = engine_tools.check_units(refdata, code="99213", units=3, setting="ER")
    assert result["exceeds"] is True
    assert result["mue"]["mue_value"] == 1


def test_check_units_within_limit(refdata):
    result = engine_tools.check_units(refdata, code="99213", units=1, setting="ER")
    assert result["exceeds"] is False


def test_check_units_unknown_code(refdata):
    result = engine_tools.check_units(refdata, code="00000", units=5, setting="ER")
    assert result == {"mue": None, "exceeds": False}


def test_fap_eligibility_free_tier(refdata):
    result = engine_tools.fap_eligibility(
        refdata, hospital_id="nyp", household_size=1, annual_income_cents=100000, state="NY"
    )
    assert result["tier"] == "FREE"
    assert result["hospital_id"] == "nyp"


def test_fap_eligibility_discount_tier(refdata):
    # ~300% FPL for household of 1 in 2026 guideline (15,960 + 0) -> pick an
    # income between the free (<=200%) and discount (<=400%) thresholds.
    guideline = 1596000
    income = int(guideline * 3.0)
    result = engine_tools.fap_eligibility(
        refdata, hospital_id="nyp", household_size=1, annual_income_cents=income, state="NY"
    )
    assert result["tier"] == "DISCOUNT"


def test_fap_eligibility_none_tier(refdata):
    guideline = 1596000
    income = int(guideline * 5.0)
    result = engine_tools.fap_eligibility(
        refdata, hospital_id="nyp", household_size=1, annual_income_cents=income, state="NY"
    )
    assert result["tier"] == "NONE"


def test_fap_eligibility_unknown_hospital_returns_none(refdata):
    assert engine_tools.fap_eligibility(
        refdata, hospital_id="unknown", household_size=1, annual_income_cents=100000, state="NY"
    ) is None


def test_hospital_price_found(refdata):
    result = engine_tools.hospital_price(refdata, hospital_id="nyp", code="99213")
    assert result["cash_cents"] == 10000


def test_hospital_price_not_found(refdata):
    assert engine_tools.hospital_price(refdata, hospital_id="nyp", code="00000") is None


def test_explain_rule_known():
    result = engine_tools.explain_rule(rule_id="NCCI_PTP")
    assert result["rule_id"] == "NCCI_PTP"
    assert result["title"]
    assert result["plain_english"]
    assert result["legal_basis"]


def test_explain_rule_covers_every_engine_rule_id():
    for rule_id in (
        "DUPLICATE", "NCCI_PTP", "MUE", "ARITHMETIC", "EOB_BALANCE_BILLING",
        "NSA_EMERGENCY", "NSA_GFE", "CASH_PRICE", "FAP_501R", "MEDICARE_BENCHMARK",
    ):
        result = engine_tools.explain_rule(rule_id=rule_id)
        assert result["rule_id"] == rule_id


def test_explain_rule_unknown_raises():
    with pytest.raises(ToolError):
        engine_tools.explain_rule(rule_id="NOT_A_RULE")


def test_audit_case_stores_signed_findings_and_updates_summary(store, signer, refdata):
    case_id = store.create_case(
        org="org1", patient_name="Pat", patient_email="pat@example.com", patient_state="NY",
        reply_to_email="pat@example.com", hospital_id="nyp",
    )
    # Charges match line sum (20000) but patient_balance_cents is 5000 too
    # high -> triggers ARITHMETIC (bill-internal, no dataset citation needed).
    bill = make_bill(charge_cents=20000, patient_balance_cents=25000)
    store.put_doc(case_id, kind="bill", payload=bill.model_dump(mode="json"), confidence=0.9)

    result = engine_tools.audit_case(store, signer, refdata, case_id=case_id)

    assert result["report"]["disputable_cents"] == 5000
    findings = result["report"]["findings"]
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "ARITHMETIC"
    assert len(result["signed"]) == 1
    assert result["signed"][0]["finding_id"] == findings[0]["finding_id"]

    stored_findings = store.get_findings(case_id)
    assert len(stored_findings) == 1
    finding, signature = stored_findings[0]
    assert finding.rule_id == "ARITHMETIC"
    assert signature == result["signed"][0]["signature"]

    case = store.get_case(case_id)
    # update_summary lives on the ORG# partition, not the case's own META --
    # confirm it actually wrote by re-listing the org.
    summaries = store.list_cases("org1")
    assert summaries[0]["status"] == "audited"
    assert summaries[0]["billed_cents"] == 20000
    assert summaries[0]["disputable_cents"] == 5000


def test_audit_case_no_bill_raises(store, signer, refdata):
    case_id = store.create_case(
        org="org1", patient_name="Pat", patient_email="pat@example.com", patient_state="NY",
        reply_to_email="pat@example.com", hospital_id="nyp",
    )
    with pytest.raises(ToolError):
        engine_tools.audit_case(store, signer, refdata, case_id=case_id)


def test_audit_case_unknown_case_raises(store, signer, refdata):
    with pytest.raises(ToolError):
        engine_tools.audit_case(store, signer, refdata, case_id="case_does_not_exist")
