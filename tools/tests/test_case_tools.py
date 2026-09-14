import pytest

from countercharge_core.signing import sign_finding
from countercharge_engine.canonical import with_id
from countercharge_engine.models import Citation, Finding

from countercharge_tools import case_tools
from countercharge_tools.deps import ToolError

from conftest import make_bill


def _make_finding(amount_cents=5000, disputable=True):
    citation = Citation(dataset="BILL", version="n/a", url="", record={})
    return with_id(
        Finding(
            rule_id="ARITHMETIC", disputable=disputable, line_ids=[], amount_cents=amount_cents,
            title="t", detail="d", citation=citation,
        )
    )


@pytest.fixture
def case_id(store):
    return store.create_case(
        org="org1", patient_name="Pat", patient_email="pat@example.com", patient_state="NY",
        reply_to_email="pat@example.com", hospital_id="nyp",
    )


def test_get_case_returns_store_shape(store, case_id):
    result = case_tools.get_case(store, case_id=case_id)
    assert result["case_id"] == case_id
    assert result["meta"]["org"] == "org1"


def test_save_extraction_valid_bill(store, case_id):
    bill = make_bill()
    result = case_tools.save_extraction(
        store, case_id=case_id, kind="bill", payload=bill.model_dump(mode="json"), confidence=0.9
    )
    assert "doc_id" in result
    docs = store.list_docs(case_id)
    assert docs[0]["kind"] == "bill"


def test_save_extraction_invalid_payload_raises(store, case_id):
    with pytest.raises(ToolError):
        case_tools.save_extraction(
            store, case_id=case_id, kind="bill", payload={"not": "a bill"}, confidence=0.9
        )


def test_save_extraction_unknown_kind_raises(store, case_id):
    with pytest.raises(ToolError):
        case_tools.save_extraction(store, case_id=case_id, kind="xray", payload={}, confidence=0.5)


def test_draft_action_dispute_letter_grounded_ok(store, signer, case_id):
    finding = _make_finding(amount_cents=5000)
    store.put_findings(case_id, [finding], [sign_finding(signer, finding)])
    letter = f"We are disputing ${5000/100:.2f} for this charge."

    result = case_tools.draft_action(
        store, case_id=case_id, type="dispute_letter", recipient_email="billing@nyp.org",
        finding_ids=[finding.finding_id], letter_markdown=letter, disputed_amount_cents=5000,
    )

    assert result["grounding"]["ok"] is True
    assert result["tool"] == "actions___send_dispute_letter"
    action = store.get_action(case_id, result["action_id"])
    assert action["status"] == "pending_approval"
    assert action["input_hash"] == result["input_hash"]
    assert action["tool_input"] == result["tool_input"]


def test_draft_action_ungrounded_amount_is_rejected(store, signer, case_id):
    finding = _make_finding(amount_cents=5000)
    store.put_findings(case_id, [finding], [sign_finding(signer, finding)])
    letter = "We are disputing $999.00 for this charge."

    result = case_tools.draft_action(
        store, case_id=case_id, type="dispute_letter", recipient_email="billing@nyp.org",
        finding_ids=[finding.finding_id], letter_markdown=letter, disputed_amount_cents=5000,
    )

    assert result["grounding"]["ok"] is False
    assert "$999.00" in result["grounding"]["unsupported_amounts"]
    action = store.get_action(case_id, result["action_id"])
    assert action["status"] == "rejected_grounding"


def test_draft_action_unknown_finding_id_raises(store, case_id):
    with pytest.raises(ToolError):
        case_tools.draft_action(
            store, case_id=case_id, type="dispute_letter", recipient_email="billing@nyp.org",
            finding_ids=["f_doesnotexist"], letter_markdown="no amounts here",
            disputed_amount_cents=0,
        )


def test_draft_action_escalation_requires_channel(store, case_id):
    with pytest.raises(ToolError):
        case_tools.draft_action(
            store, case_id=case_id, type="escalation", recipient_email="billing@nyp.org",
            finding_ids=[], letter_markdown="a complaint", disputed_amount_cents=0,
        )


def test_draft_action_escalation_tool_input_has_no_recipient(store, case_id):
    result = case_tools.draft_action(
        store, case_id=case_id, type="escalation", recipient_email="billing@nyp.org",
        finding_ids=[], letter_markdown="a complaint", disputed_amount_cents=0,
        channel="CFPB",
    )
    assert "recipient_email" not in result["tool_input"]
    assert result["tool_input"]["channel"] == "CFPB"


def test_draft_action_unknown_type_raises(store, case_id):
    with pytest.raises(ToolError):
        case_tools.draft_action(
            store, case_id=case_id, type="not_a_type", recipient_email="billing@nyp.org",
            finding_ids=[], letter_markdown="x", disputed_amount_cents=0,
        )
