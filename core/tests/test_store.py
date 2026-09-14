from datetime import date

import boto3
import pytest
from moto import mock_aws

from countercharge_core.store import CaseStore
from countercharge_engine.canonical import with_id
from countercharge_engine.models import Citation, Finding


@pytest.fixture
def table():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="cc-cases",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                }
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        yield ddb.Table("cc-cases")


@pytest.fixture
def store(table):
    return CaseStore(table)


def make_finding(amount_cents=5000, rule_id="R1"):
    citation = Citation(dataset="ncci-ptp", version="2026q4", url="https://example.com", record={})
    finding = Finding(
        rule_id=rule_id,
        disputable=True,
        line_ids=["l1"],
        amount_cents=amount_cents,
        title="Duplicate charge",
        detail="Line billed twice",
        citation=citation,
    )
    return with_id(finding)


def test_create_case_and_get_case_meta(store):
    case_id = store.create_case(
        org="demo",
        patient_name="Jane Doe",
        patient_email="jane@example.com",
        patient_state="NY",
        reply_to_email="jane@example.com",
        hospital_id="nyp",
    )
    assert case_id.startswith("case_")
    case = store.get_case(case_id)
    assert case["meta"]["org"] == "demo"
    assert case["meta"]["patient"]["name"] == "Jane Doe"
    assert case["meta"]["hospital_id"] == "nyp"
    assert case["docs"] == []
    assert case["findings"] == []
    assert case["actions"] == []


def test_create_case_with_explicit_id_is_used(store):
    case_id = store.create_case(
        org="demo",
        patient_name="Jane Doe",
        patient_email="jane@example.com",
        patient_state="NY",
        reply_to_email="jane@example.com",
        hospital_id="nyp",
        case_id="case_fixed",
    )
    assert case_id == "case_fixed"


def test_create_case_writes_org_summary_row_visible_in_list_cases(store):
    case_id = store.create_case(
        org="demo",
        patient_name="Jane Doe",
        patient_email="jane@example.com",
        patient_state="NY",
        reply_to_email="jane@example.com",
        hospital_id="nyp",
    )
    cases = store.list_cases("demo")
    assert len(cases) == 1
    assert cases[0]["status"] == "intake"
    assert cases[0]["billed_cents"] == 0
    assert isinstance(cases[0]["billed_cents"], int)


def test_put_doc_and_list_docs_roundtrip_confidence_as_float(store):
    case_id = store.create_case(
        org="demo",
        patient_name="Jane Doe",
        patient_email="jane@example.com",
        patient_state="NY",
        reply_to_email="jane@example.com",
        hospital_id="nyp",
    )
    doc_id = store.put_doc(case_id, kind="bill", payload={"account_no": "acct-1"}, confidence=0.87)
    assert doc_id.startswith("doc_")
    docs = store.list_docs(case_id)
    assert len(docs) == 1
    assert docs[0]["kind"] == "bill"
    assert docs[0]["confidence"] == 0.87
    assert isinstance(docs[0]["confidence"], float)
    assert docs[0]["payload"]["account_no"] == "acct-1"


def test_put_doc_appears_in_get_case(store):
    case_id = store.create_case(
        org="demo",
        patient_name="Jane Doe",
        patient_email="jane@example.com",
        patient_state="NY",
        reply_to_email="jane@example.com",
        hospital_id="nyp",
    )
    store.put_doc(case_id, kind="eob", payload={"payer": "Aetna"}, confidence=1.0)
    case = store.get_case(case_id)
    assert len(case["docs"]) == 1
    assert case["docs"][0]["kind"] == "eob"


def test_put_findings_and_get_findings_roundtrip(store):
    case_id = store.create_case(
        org="demo",
        patient_name="Jane Doe",
        patient_email="jane@example.com",
        patient_state="NY",
        reply_to_email="jane@example.com",
        hospital_id="nyp",
    )
    finding = make_finding(amount_cents=12345)
    store.put_findings(case_id, [finding], ["sig-1"])
    results = store.get_findings(case_id)
    assert len(results) == 1
    got_finding, got_signature = results[0]
    assert isinstance(got_finding, Finding)
    assert got_finding.finding_id == finding.finding_id
    assert got_finding.amount_cents == 12345
    assert isinstance(got_finding.amount_cents, int)
    assert got_signature == "sig-1"


def test_put_findings_mismatched_lengths_raises(store):
    case_id = store.create_case(
        org="demo",
        patient_name="Jane Doe",
        patient_email="jane@example.com",
        patient_state="NY",
        reply_to_email="jane@example.com",
        hospital_id="nyp",
    )
    with pytest.raises(ValueError):
        store.put_findings(case_id, [make_finding()], [])


def test_findings_appear_in_get_case(store):
    case_id = store.create_case(
        org="demo",
        patient_name="Jane Doe",
        patient_email="jane@example.com",
        patient_state="NY",
        reply_to_email="jane@example.com",
        hospital_id="nyp",
    )
    finding = make_finding()
    store.put_findings(case_id, [finding], ["sig-1"])
    case = store.get_case(case_id)
    assert len(case["findings"]) == 1
    assert case["findings"][0]["signature"] == "sig-1"


def test_put_action_get_action_and_update_status(store):
    case_id = store.create_case(
        org="demo",
        patient_name="Jane Doe",
        patient_email="jane@example.com",
        patient_state="NY",
        reply_to_email="jane@example.com",
        hospital_id="nyp",
    )
    action_id = store.put_action(
        case_id,
        type="dispute_letter",
        status="pending_approval",
        tool="actions___send_dispute_letter",
        tool_input={"case_id": case_id, "recipient_email": "billing@nyp.org"},
        input_hash="deadbeef",
        recipient_email="billing@nyp.org",
        finding_ids=["f_1"],
        disputed_amount_cents=12345,
        letter_markdown="# Dear Hospital",
    )
    assert action_id.startswith("action_")

    action = store.get_action(case_id, action_id)
    assert action["status"] == "pending_approval"
    assert action["disputed_amount_cents"] == 12345
    assert isinstance(action["disputed_amount_cents"], int)
    assert action["finding_ids"] == ["f_1"]

    store.update_action_status(case_id, action_id, "sent", message_id="ses-msg-1")
    updated = store.get_action(case_id, action_id)
    assert updated["status"] == "sent"
    assert updated["message_id"] == "ses-msg-1"


def test_get_action_missing_returns_none(store):
    assert store.get_action("case_missing", "action_missing") is None


def test_actions_appear_in_get_case(store):
    case_id = store.create_case(
        org="demo",
        patient_name="Jane Doe",
        patient_email="jane@example.com",
        patient_state="NY",
        reply_to_email="jane@example.com",
        hospital_id="nyp",
    )
    store.put_action(
        case_id,
        type="dispute_letter",
        status="draft",
        tool="actions___send_dispute_letter",
        tool_input={},
        input_hash="h",
        recipient_email="billing@nyp.org",
        finding_ids=[],
        disputed_amount_cents=0,
    )
    case = store.get_case(case_id)
    assert len(case["actions"]) == 1
    assert case["actions"][0]["type"] == "dispute_letter"


def test_add_decision_and_list_decisions(store):
    case_id = store.create_case(
        org="demo",
        patient_name="Jane Doe",
        patient_email="jane@example.com",
        patient_state="NY",
        reply_to_email="jane@example.com",
        hospital_id="nyp",
    )
    store.add_decision(case_id, "lambda", "actions___send_dispute_letter", "deny", "bad_mac")
    store.add_decision(
        case_id, "policy", "actions___send_dispute_letter", "allow", "ok", action_id="action_1"
    )
    decisions = store.list_decisions(case_id)
    assert len(decisions) == 2
    reasons = {d["reason"] for d in decisions}
    assert reasons == {"bad_mac", "ok"}
    allow = next(d for d in decisions if d["outcome"] == "allow")
    assert allow["action_id"] == "action_1"


def test_add_event_and_list_events(store):
    case_id = store.create_case(
        org="demo",
        patient_name="Jane Doe",
        patient_email="jane@example.com",
        patient_state="NY",
        reply_to_email="jane@example.com",
        hospital_id="nyp",
    )
    store.add_event(case_id, "email_sent", "Dispute letter sent", data={"message_id": "m1"})
    events = store.list_events(case_id)
    assert len(events) == 1
    assert events[0]["kind"] == "email_sent"
    assert events[0]["data"]["message_id"] == "m1"


def test_list_cases_filtered_by_status(store):
    intake_id = store.create_case(
        org="demo",
        patient_name="Jane Doe",
        patient_email="jane@example.com",
        patient_state="NY",
        reply_to_email="jane@example.com",
        hospital_id="nyp",
    )
    audited_id = store.create_case(
        org="demo",
        patient_name="John Roe",
        patient_email="john@example.com",
        patient_state="OH",
        reply_to_email="john@example.com",
        hospital_id="ccf",
        status="audited",
    )
    intake_cases = store.list_cases("demo", status="intake")
    assert [c["patient_display"] for c in intake_cases] == ["Jane Doe"]
    audited_cases = store.list_cases("demo", status="audited")
    assert [c["patient_display"] for c in audited_cases] == ["John Roe"]
    assert len(store.list_cases("demo")) == 2


def test_update_summary_changes_status_and_gsi(store):
    case_id = store.create_case(
        org="demo",
        patient_name="Jane Doe",
        patient_email="jane@example.com",
        patient_state="NY",
        reply_to_email="jane@example.com",
        hospital_id="nyp",
    )
    store.update_summary(case_id, "demo", status="audited", disputable_cents=45000)
    assert store.list_cases("demo", status="intake") == []
    audited = store.list_cases("demo", status="audited")
    assert len(audited) == 1
    assert audited[0]["disputable_cents"] == 45000
    assert isinstance(audited[0]["disputable_cents"], int)


def test_update_summary_only_touches_given_fields(store):
    case_id = store.create_case(
        org="demo",
        patient_name="Jane Doe",
        patient_email="jane@example.com",
        patient_state="NY",
        reply_to_email="jane@example.com",
        hospital_id="nyp",
    )
    store.update_summary(case_id, "demo", recovered_cents=1000)
    cases = store.list_cases("demo", status="intake")
    assert len(cases) == 1
    assert cases[0]["recovered_cents"] == 1000
    assert cases[0]["billed_cents"] == 0
