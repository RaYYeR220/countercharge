from datetime import datetime, timezone

import boto3
import pytest
from boto3.dynamodb.conditions import Key

from countercharge_core.approval import input_hash, issue_token
from countercharge_core.email import MemoryEmailSender
from countercharge_core.signing import sign_finding
from countercharge_engine.canonical import with_id
from countercharge_engine.models import Citation, Finding

from countercharge_tools import action_tools, case_tools
from countercharge_tools.deps import ToolDenied

NOW = 1_800_000_000


def _finding(amount_cents=5000, disputable=True, rule_id="ARITHMETIC"):
    citation = Citation(dataset="BILL", version="n/a", url="", record={})
    return with_id(
        Finding(
            rule_id=rule_id, disputable=disputable, line_ids=[], amount_cents=amount_cents,
            title="t", detail="d", citation=citation,
        )
    )


def _manual_pending_action(store, *, case_id, type_, tool, tool_input, letter_markdown="A letter."):
    store.put_action(
        case_id,
        type=type_, status="pending_approval", tool=tool, tool_input=tool_input,
        input_hash=input_hash(tool_input), recipient_email=tool_input.get("recipient_email", ""),
        finding_ids=tool_input.get("finding_ids", []),
        disputed_amount_cents=tool_input.get("disputed_amount_cents", 0),
        letter_markdown=letter_markdown, action_id=tool_input["action_id"],
    )


@pytest.fixture
def case_id(store):
    return store.create_case(
        org="org1", patient_name="Pat", patient_email="pat@example.com", patient_state="NY",
        reply_to_email="reply@example.com", hospital_id="nyp",
    )


@pytest.fixture
def email_sender():
    return MemoryEmailSender()


@pytest.fixture
def scheduler_client(aws):
    client = boto3.client("scheduler", region_name="us-east-1")
    client.create_schedule_group(Name="cc-followups")
    return client


@pytest.fixture
def dispute_setup(store, signer, case_id):
    finding = _finding(amount_cents=5000)
    store.put_findings(case_id, [finding], [sign_finding(signer, finding)])
    letter = "We are disputing $50.00 for this charge."
    drafted = case_tools.draft_action(
        store, case_id=case_id, type="dispute_letter", recipient_email="billing@nyp.org",
        finding_ids=[finding.finding_id], letter_markdown=letter, disputed_amount_cents=5000,
    )
    assert drafted["grounding"]["ok"] is True
    token = issue_token(
        signer, action_id=drafted["action_id"], case_id=case_id, tool=drafted["tool"],
        tool_input=drafted["tool_input"], approver_sub="advocate1", now=NOW,
    )
    return {"case_id": case_id, "finding": finding, "drafted": drafted, "token": token}


def _send(store, signer, hospitals, email_sender, setup, **overrides):
    kwargs = dict(
        case_id=setup["case_id"], action_id=setup["drafted"]["action_id"],
        recipient_email="billing@nyp.org", finding_ids=[setup["finding"].finding_id],
        disputed_amount_cents=5000, approval_token=setup["token"], now=NOW,
    )
    kwargs.update(overrides)
    return action_tools.send_dispute_letter(store, signer, hospitals, email_sender, **kwargs)


# --- send_dispute_letter: allow + every deny branch -------------------------


def test_send_dispute_letter_allow(store, signer, hospitals, email_sender, dispute_setup):
    result = _send(store, signer, hospitals, email_sender, dispute_setup)

    assert result["status"] == "sent"
    assert len(email_sender.sent) == 1
    assert email_sender.sent[0]["to"] == "billing@nyp.org"
    assert email_sender.sent[0]["reply_to"] == "reply@example.com"

    action = store.get_action(dispute_setup["case_id"], dispute_setup["drafted"]["action_id"])
    assert action["status"] == "sent"
    assert action["message_id"] == result["message_id"]

    decisions = store.list_decisions(dispute_setup["case_id"])
    assert decisions[-1]["outcome"] == "allow"
    assert decisions[-1]["tool"] == "actions___send_dispute_letter"

    summary = store.list_cases("org1")[0]
    assert summary["status"] == "sent"


def test_send_dispute_letter_deny_action_not_found(store, signer, hospitals, email_sender, case_id):
    bogus_input = {
        "case_id": case_id, "action_id": "action_missing", "recipient_email": "billing@nyp.org",
        "finding_ids": [], "disputed_amount_cents": 0,
    }
    token = issue_token(
        signer, action_id="action_missing", case_id=case_id, tool="actions___send_dispute_letter",
        tool_input=bogus_input, approver_sub="a", now=NOW,
    )
    with pytest.raises(ToolDenied) as exc:
        action_tools.send_dispute_letter(
            store, signer, hospitals, email_sender,
            case_id=case_id, action_id="action_missing", recipient_email="billing@nyp.org",
            finding_ids=[], disputed_amount_cents=0, approval_token=token, now=NOW,
        )
    assert str(exc.value) == "DENIED:lambda:action_not_found"
    assert store.list_decisions(case_id)[-1]["reason"] == "action_not_found"


def test_send_dispute_letter_deny_when_already_sent(store, signer, hospitals, email_sender, dispute_setup):
    _send(store, signer, hospitals, email_sender, dispute_setup)
    with pytest.raises(ToolDenied) as exc:
        _send(store, signer, hospitals, email_sender, dispute_setup)
    assert str(exc.value) == "DENIED:lambda:action_status"


def test_send_dispute_letter_deny_bad_mac(store, signer, hospitals, email_sender, dispute_setup):
    bad_token = dispute_setup["token"][:-4] + "abcd"
    with pytest.raises(ToolDenied) as exc:
        _send(store, signer, hospitals, email_sender, dispute_setup, approval_token=bad_token)
    assert str(exc.value) == "DENIED:lambda:bad_mac"


def test_send_dispute_letter_deny_expired(store, signer, hospitals, email_sender, dispute_setup):
    with pytest.raises(ToolDenied) as exc:
        _send(store, signer, hospitals, email_sender, dispute_setup, now=NOW + 901)
    assert str(exc.value) == "DENIED:lambda:expired"


def test_send_dispute_letter_deny_tool_mismatch(store, signer, hospitals, email_sender, case_id):
    finding = _finding(amount_cents=5000)
    store.put_findings(case_id, [finding], [sign_finding(signer, finding)])
    drafted = case_tools.draft_action(
        store, case_id=case_id, type="dispute_letter", recipient_email="billing@nyp.org",
        finding_ids=[finding.finding_id], letter_markdown="We are disputing $50.00 for this charge.",
        disputed_amount_cents=5000,
    )
    wrong_tool_token = issue_token(
        signer, action_id=drafted["action_id"], case_id=case_id, tool="actions___submit_fap_application",
        tool_input=drafted["tool_input"], approver_sub="a", now=NOW,
    )
    with pytest.raises(ToolDenied) as exc:
        action_tools.send_dispute_letter(
            store, signer, hospitals, email_sender,
            case_id=case_id, action_id=drafted["action_id"], recipient_email="billing@nyp.org",
            finding_ids=[finding.finding_id], disputed_amount_cents=5000,
            approval_token=wrong_tool_token, now=NOW,
        )
    assert str(exc.value) == "DENIED:lambda:tool_mismatch"


def test_send_dispute_letter_deny_tampered_amount_after_draft(store, signer, hospitals, email_sender, dispute_setup):
    with pytest.raises(ToolDenied) as exc:
        _send(store, signer, hospitals, email_sender, dispute_setup, disputed_amount_cents=999999)
    assert str(exc.value) == "DENIED:lambda:input_mismatch"


def test_send_dispute_letter_deny_tampered_recipient_after_draft(store, signer, hospitals, email_sender, dispute_setup):
    with pytest.raises(ToolDenied) as exc:
        _send(store, signer, hospitals, email_sender, dispute_setup, recipient_email="attacker@evil.example")
    assert str(exc.value) == "DENIED:lambda:input_mismatch"


def test_send_dispute_letter_deny_type_mismatch(store, signer, hospitals, email_sender, case_id):
    action_id = "action_wrongtype1"
    fap_input = {"case_id": case_id, "action_id": action_id, "recipient_email": "billing@nyp.org"}
    _manual_pending_action(
        store, case_id=case_id, type_="fap_application", tool="actions___submit_fap_application",
        tool_input=fap_input,
    )
    dispute_input = {
        "case_id": case_id, "action_id": action_id, "recipient_email": "billing@nyp.org",
        "finding_ids": [], "disputed_amount_cents": 0,
    }
    token = issue_token(
        signer, action_id=action_id, case_id=case_id, tool="actions___send_dispute_letter",
        tool_input=dispute_input, approver_sub="a", now=NOW,
    )
    with pytest.raises(ToolDenied) as exc:
        action_tools.send_dispute_letter(
            store, signer, hospitals, email_sender,
            case_id=case_id, action_id=action_id, recipient_email="billing@nyp.org",
            finding_ids=[], disputed_amount_cents=0, approval_token=token, now=NOW,
        )
    assert str(exc.value) == "DENIED:lambda:type_mismatch"


def test_send_dispute_letter_deny_empty_finding_ids(store, signer, hospitals, email_sender, case_id):
    action_id = "action_empty1"
    tool_input = {
        "case_id": case_id, "action_id": action_id, "recipient_email": "billing@nyp.org",
        "finding_ids": [], "disputed_amount_cents": 0,
    }
    _manual_pending_action(
        store, case_id=case_id, type_="dispute_letter", tool="actions___send_dispute_letter", tool_input=tool_input
    )
    token = issue_token(
        signer, action_id=action_id, case_id=case_id, tool="actions___send_dispute_letter",
        tool_input=tool_input, approver_sub="a", now=NOW,
    )
    with pytest.raises(ToolDenied) as exc:
        action_tools.send_dispute_letter(
            store, signer, hospitals, email_sender,
            case_id=case_id, action_id=action_id, recipient_email="billing@nyp.org",
            finding_ids=[], disputed_amount_cents=0, approval_token=token, now=NOW,
        )
    assert str(exc.value) == "DENIED:lambda:empty_finding_ids"


def test_send_dispute_letter_deny_foreign_finding(store, signer, hospitals, email_sender, case_id):
    other_case_id = store.create_case(
        org="org1", patient_name="Other", patient_email="o@example.com", patient_state="NY",
        reply_to_email="o@example.com", hospital_id="nyp",
    )
    other_finding = _finding(amount_cents=5000)
    store.put_findings(other_case_id, [other_finding], [sign_finding(signer, other_finding)])

    action_id = "action_foreign1"
    tool_input = {
        "case_id": case_id, "action_id": action_id, "recipient_email": "billing@nyp.org",
        "finding_ids": [other_finding.finding_id], "disputed_amount_cents": 5000,
    }
    _manual_pending_action(
        store, case_id=case_id, type_="dispute_letter", tool="actions___send_dispute_letter", tool_input=tool_input
    )
    token = issue_token(
        signer, action_id=action_id, case_id=case_id, tool="actions___send_dispute_letter",
        tool_input=tool_input, approver_sub="a", now=NOW,
    )
    with pytest.raises(ToolDenied) as exc:
        action_tools.send_dispute_letter(
            store, signer, hospitals, email_sender,
            case_id=case_id, action_id=action_id, recipient_email="billing@nyp.org",
            finding_ids=[other_finding.finding_id], disputed_amount_cents=5000,
            approval_token=token, now=NOW,
        )
    assert str(exc.value) == "DENIED:lambda:foreign_finding"


def test_send_dispute_letter_deny_bad_finding_signature(store, signer, hospitals, email_sender, case_id):
    finding = _finding(amount_cents=5000)
    store.put_findings(case_id, [finding], ["not-a-real-signature"])
    action_id = "action_badsig1"
    tool_input = {
        "case_id": case_id, "action_id": action_id, "recipient_email": "billing@nyp.org",
        "finding_ids": [finding.finding_id], "disputed_amount_cents": 5000,
    }
    _manual_pending_action(
        store, case_id=case_id, type_="dispute_letter", tool="actions___send_dispute_letter", tool_input=tool_input
    )
    token = issue_token(
        signer, action_id=action_id, case_id=case_id, tool="actions___send_dispute_letter",
        tool_input=tool_input, approver_sub="a", now=NOW,
    )
    with pytest.raises(ToolDenied) as exc:
        action_tools.send_dispute_letter(
            store, signer, hospitals, email_sender,
            case_id=case_id, action_id=action_id, recipient_email="billing@nyp.org",
            finding_ids=[finding.finding_id], disputed_amount_cents=5000,
            approval_token=token, now=NOW,
        )
    assert str(exc.value) == "DENIED:lambda:bad_finding_signature"


def test_send_dispute_letter_deny_not_disputable_finding(store, signer, hospitals, email_sender, case_id):
    finding = _finding(amount_cents=5000, disputable=False)
    store.put_findings(case_id, [finding], [sign_finding(signer, finding)])
    action_id = "action_notdisp1"
    tool_input = {
        "case_id": case_id, "action_id": action_id, "recipient_email": "billing@nyp.org",
        "finding_ids": [finding.finding_id], "disputed_amount_cents": 5000,
    }
    _manual_pending_action(
        store, case_id=case_id, type_="dispute_letter", tool="actions___send_dispute_letter", tool_input=tool_input
    )
    token = issue_token(
        signer, action_id=action_id, case_id=case_id, tool="actions___send_dispute_letter",
        tool_input=tool_input, approver_sub="a", now=NOW,
    )
    with pytest.raises(ToolDenied) as exc:
        action_tools.send_dispute_letter(
            store, signer, hospitals, email_sender,
            case_id=case_id, action_id=action_id, recipient_email="billing@nyp.org",
            finding_ids=[finding.finding_id], disputed_amount_cents=5000,
            approval_token=token, now=NOW,
        )
    assert str(exc.value) == "DENIED:lambda:not_disputable"


def test_send_dispute_letter_deny_amount_exceeds_findings(store, signer, hospitals, email_sender, case_id):
    finding = _finding(amount_cents=5000, disputable=True)
    store.put_findings(case_id, [finding], [sign_finding(signer, finding)])
    action_id = "action_overamt1"
    tool_input = {
        "case_id": case_id, "action_id": action_id, "recipient_email": "billing@nyp.org",
        "finding_ids": [finding.finding_id], "disputed_amount_cents": 999999,
    }
    _manual_pending_action(
        store, case_id=case_id, type_="dispute_letter", tool="actions___send_dispute_letter", tool_input=tool_input
    )
    token = issue_token(
        signer, action_id=action_id, case_id=case_id, tool="actions___send_dispute_letter",
        tool_input=tool_input, approver_sub="a", now=NOW,
    )
    with pytest.raises(ToolDenied) as exc:
        action_tools.send_dispute_letter(
            store, signer, hospitals, email_sender,
            case_id=case_id, action_id=action_id, recipient_email="billing@nyp.org",
            finding_ids=[finding.finding_id], disputed_amount_cents=999999,
            approval_token=token, now=NOW,
        )
    assert str(exc.value) == "DENIED:lambda:amount_exceeds_findings"


def test_send_dispute_letter_deny_recipient_not_allowed(store, signer, hospitals, email_sender, case_id):
    finding = _finding(amount_cents=5000)
    store.put_findings(case_id, [finding], [sign_finding(signer, finding)])
    action_id = "action_badrecip1"
    tool_input = {
        "case_id": case_id, "action_id": action_id, "recipient_email": "attacker@evil.example",
        "finding_ids": [finding.finding_id], "disputed_amount_cents": 5000,
    }
    _manual_pending_action(
        store, case_id=case_id, type_="dispute_letter", tool="actions___send_dispute_letter", tool_input=tool_input
    )
    token = issue_token(
        signer, action_id=action_id, case_id=case_id, tool="actions___send_dispute_letter",
        tool_input=tool_input, approver_sub="a", now=NOW,
    )
    with pytest.raises(ToolDenied) as exc:
        action_tools.send_dispute_letter(
            store, signer, hospitals, email_sender,
            case_id=case_id, action_id=action_id, recipient_email="attacker@evil.example",
            finding_ids=[finding.finding_id], disputed_amount_cents=5000,
            approval_token=token, now=NOW,
        )
    assert str(exc.value) == "DENIED:lambda:recipient_not_allowed"


# --- submit_fap_application / request_itemized_bill -------------------------


def test_submit_fap_application_allow(store, signer, hospitals, email_sender, case_id):
    drafted = case_tools.draft_action(
        store, case_id=case_id, type="fap_application", recipient_email="billing@nyp.org",
        finding_ids=[], letter_markdown="Applying for financial assistance.", disputed_amount_cents=0,
    )
    token = issue_token(
        signer, action_id=drafted["action_id"], case_id=case_id, tool=drafted["tool"],
        tool_input=drafted["tool_input"], approver_sub="advocate1", now=NOW,
    )
    result = action_tools.submit_fap_application(
        store, signer, hospitals, email_sender,
        case_id=case_id, action_id=drafted["action_id"], recipient_email="billing@nyp.org",
        approval_token=token, now=NOW,
    )
    assert result["status"] == "sent"
    assert email_sender.sent[0]["to"] == "billing@nyp.org"


def test_submit_fap_application_deny_bad_mac(store, signer, hospitals, email_sender, case_id):
    drafted = case_tools.draft_action(
        store, case_id=case_id, type="fap_application", recipient_email="billing@nyp.org",
        finding_ids=[], letter_markdown="Applying for financial assistance.", disputed_amount_cents=0,
    )
    token = issue_token(
        signer, action_id=drafted["action_id"], case_id=case_id, tool=drafted["tool"],
        tool_input=drafted["tool_input"], approver_sub="advocate1", now=NOW,
    )
    bad_token = token[:-4] + "abcd"
    with pytest.raises(ToolDenied) as exc:
        action_tools.submit_fap_application(
            store, signer, hospitals, email_sender,
            case_id=case_id, action_id=drafted["action_id"], recipient_email="billing@nyp.org",
            approval_token=bad_token, now=NOW,
        )
    assert str(exc.value) == "DENIED:lambda:bad_mac"


def test_submit_fap_application_deny_recipient_not_allowed(store, signer, hospitals, email_sender, case_id):
    action_id = "action_fap_badrecip"
    tool_input = {"case_id": case_id, "action_id": action_id, "recipient_email": "attacker@evil.example"}
    _manual_pending_action(
        store, case_id=case_id, type_="fap_application", tool="actions___submit_fap_application",
        tool_input=tool_input,
    )
    token = issue_token(
        signer, action_id=action_id, case_id=case_id, tool="actions___submit_fap_application",
        tool_input=tool_input, approver_sub="a", now=NOW,
    )
    with pytest.raises(ToolDenied) as exc:
        action_tools.submit_fap_application(
            store, signer, hospitals, email_sender,
            case_id=case_id, action_id=action_id, recipient_email="attacker@evil.example",
            approval_token=token, now=NOW,
        )
    assert str(exc.value) == "DENIED:lambda:recipient_not_allowed"


def test_request_itemized_bill_allow(store, signer, hospitals, email_sender, case_id):
    drafted = case_tools.draft_action(
        store, case_id=case_id, type="itemized_request", recipient_email="billing@nyp.org",
        finding_ids=[], letter_markdown="Please send an itemized bill.", disputed_amount_cents=0,
    )
    token = issue_token(
        signer, action_id=drafted["action_id"], case_id=case_id, tool=drafted["tool"],
        tool_input=drafted["tool_input"], approver_sub="advocate1", now=NOW,
    )
    result = action_tools.request_itemized_bill(
        store, signer, hospitals, email_sender,
        case_id=case_id, action_id=drafted["action_id"], recipient_email="billing@nyp.org",
        approval_token=token, now=NOW,
    )
    assert result["status"] == "sent"


def test_request_itemized_bill_deny_expired(store, signer, hospitals, email_sender, case_id):
    drafted = case_tools.draft_action(
        store, case_id=case_id, type="itemized_request", recipient_email="billing@nyp.org",
        finding_ids=[], letter_markdown="Please send an itemized bill.", disputed_amount_cents=0,
    )
    token = issue_token(
        signer, action_id=drafted["action_id"], case_id=case_id, tool=drafted["tool"],
        tool_input=drafted["tool_input"], approver_sub="advocate1", now=NOW,
    )
    with pytest.raises(ToolDenied) as exc:
        action_tools.request_itemized_bill(
            store, signer, hospitals, email_sender,
            case_id=case_id, action_id=drafted["action_id"], recipient_email="billing@nyp.org",
            approval_token=token, now=NOW + 901,
        )
    assert str(exc.value) == "DENIED:lambda:expired"


# --- file_escalation ---------------------------------------------------------


def test_file_escalation_allow_uses_hospital_demo_inbox(store, signer, hospitals, email_sender, case_id):
    finding = _finding(amount_cents=5000, disputable=False)
    store.put_findings(case_id, [finding], [sign_finding(signer, finding)])
    drafted = case_tools.draft_action(
        store, case_id=case_id, type="escalation", recipient_email="billing@nyp.org",
        finding_ids=[finding.finding_id], letter_markdown="Filing a complaint about this bill.",
        disputed_amount_cents=0, channel="CFPB",
    )
    token = issue_token(
        signer, action_id=drafted["action_id"], case_id=case_id, tool=drafted["tool"],
        tool_input=drafted["tool_input"], approver_sub="advocate1", now=NOW,
    )
    result = action_tools.file_escalation(
        store, signer, hospitals, email_sender,
        case_id=case_id, action_id=drafted["action_id"], channel="CFPB",
        finding_ids=[finding.finding_id], approval_token=token, now=NOW,
    )
    assert result["status"] == "sent"
    assert email_sender.sent[0]["to"] == "countercharge.demo+billing@gmail.com"
    assert store.list_cases("org1")[0]["status"] == "escalated"


def test_file_escalation_deny_empty_finding_ids(store, signer, hospitals, email_sender, case_id):
    action_id = "action_esc_empty"
    tool_input = {"case_id": case_id, "action_id": action_id, "channel": "CFPB", "finding_ids": []}
    _manual_pending_action(
        store, case_id=case_id, type_="escalation", tool="actions___file_escalation", tool_input=tool_input
    )
    token = issue_token(
        signer, action_id=action_id, case_id=case_id, tool="actions___file_escalation",
        tool_input=tool_input, approver_sub="a", now=NOW,
    )
    with pytest.raises(ToolDenied) as exc:
        action_tools.file_escalation(
            store, signer, hospitals, email_sender,
            case_id=case_id, action_id=action_id, channel="CFPB", finding_ids=[],
            approval_token=token, now=NOW,
        )
    assert str(exc.value) == "DENIED:lambda:empty_finding_ids"


# --- schedule_followup (no approval) -----------------------------------------


def test_schedule_followup_allow_creates_schedule_and_followup_item(store, scheduler_client, case_id):
    result = action_tools.schedule_followup(
        store, scheduler_client, case_id=case_id, days=7, reason="check for a reply",
        followup_lambda_arn="arn:aws:lambda:us-east-1:123456789012:function:cc-followup",
        scheduler_role_arn="arn:aws:iam::123456789012:role/cc-scheduler-role",
    )
    assert result["status"] == "scheduled"

    schedules = scheduler_client.list_schedules(GroupName="cc-followups")["Schedules"]
    assert len(schedules) == 1
    assert schedules[0]["Name"] == result["schedule_name"]

    followups = store._table.query(
        KeyConditionExpression=(
            Key("PK").eq(f"CASE#{case_id}") & Key("SK").begins_with("FOLLOWUP#")
        )
    )["Items"]
    assert len(followups) == 1
    assert followups[0]["reason"] == "check for a reply"

    decisions = store.list_decisions(case_id)
    assert decisions[-1]["outcome"] == "allow"
    assert decisions[-1]["tool"] == "actions___schedule_followup"


def test_schedule_followup_demo_time_compression(store, scheduler_client, case_id):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = action_tools.schedule_followup(
        store, scheduler_client, case_id=case_id, days=7, reason="demo",
        followup_lambda_arn="arn:aws:lambda:us-east-1:123456789012:function:cc-followup",
        scheduler_role_arn="arn:aws:iam::123456789012:role/cc-scheduler-role",
        demo_time_compression_seconds=1, now=now,
    )
    assert result["due_at"].startswith("2026-01-01T00:00:07")


@pytest.mark.parametrize("days", [0, 91])
def test_schedule_followup_deny_invalid_days(store, scheduler_client, case_id, days):
    with pytest.raises(ToolDenied) as exc:
        action_tools.schedule_followup(
            store, scheduler_client, case_id=case_id, days=days, reason="x",
            followup_lambda_arn="arn:aws:lambda:us-east-1:123456789012:function:cc-followup",
            scheduler_role_arn="arn:aws:iam::123456789012:role/cc-scheduler-role",
        )
    assert str(exc.value) == "DENIED:lambda:invalid_days"
