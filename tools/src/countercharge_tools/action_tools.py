"""Gateway target ``actions`` (external side effects, C1) -- fail closed.

Every one of these tools follows the same order before it does anything
externally visible: load the pending action, verify the approval token,
recompute and compare the input hash against what was drafted, check every
cited finding belongs to this case and is properly signed, check the
recipient is allowed for this case's hospital, then send. Any failure calls
``store.add_decision(layer="lambda", outcome="deny", ...)`` and raises
``ToolDenied`` -- left unhandled so the Lambda invocation fails and the
Gateway/agent sees ``DENIED:lambda:<reason>``. Success logs a matching
``outcome="allow"`` decision.
"""

import json
import time
from datetime import datetime, timedelta, timezone

from countercharge_core.approval import input_hash, verify_token
from countercharge_core.email import EmailSender
from countercharge_core.hospitals import Hospital, recipient_allowed
from countercharge_core.ids import new_id
from countercharge_core.signing import Signer, verify_finding
from countercharge_core.store import CaseStore

from countercharge_tools.deps import ToolDenied

_TYPE_BY_TOOL = {
    "actions___send_dispute_letter": "dispute_letter",
    "actions___submit_fap_application": "fap_application",
    "actions___request_itemized_bill": "itemized_request",
    "actions___file_escalation": "escalation",
}

_SUBJECT_BY_TOOL = {
    "actions___send_dispute_letter": "Dispute of billing charges",
    "actions___submit_fap_application": "Financial assistance application",
    "actions___request_itemized_bill": "Request for itemized bill",
    "actions___file_escalation": "Complaint escalation",
}


def _without_token(tool_input: dict) -> dict:
    return {k: v for k, v in tool_input.items() if k != "approval_token"}


def _deny(store: CaseStore, case_id: str, tool: str, action_id: str | None, reason: str) -> None:
    store.add_decision(case_id, layer="lambda", tool=tool, outcome="deny", reason=reason, action_id=action_id)
    raise ToolDenied(reason)


def _load_pending_action(store: CaseStore, case_id: str, action_id: str, tool: str) -> dict:
    action = store.get_action(case_id, action_id)
    if action is None:
        _deny(store, case_id, tool, action_id, "action_not_found")
    if action["status"] != "pending_approval":
        _deny(store, case_id, tool, action_id, "action_status")
    if action["type"] != _TYPE_BY_TOOL[tool]:
        _deny(store, case_id, tool, action_id, "type_mismatch")
    return action


def _check_token(
    store: CaseStore, signer: Signer, case_id: str, action_id: str, tool: str, tool_input: dict, now: int
) -> None:
    check = verify_token(
        signer, tool_input.get("approval_token") or "", tool=tool, tool_input=_without_token(tool_input), now=now
    )
    if not check.ok:
        _deny(store, case_id, tool, action_id, check.reason or "bad_token")


def _check_hash(store: CaseStore, case_id: str, action_id: str, tool: str, action: dict, tool_input: dict) -> None:
    if input_hash(tool_input) != action["input_hash"]:
        _deny(store, case_id, tool, action_id, "input_hash_mismatch")


def _cited_findings(
    store: CaseStore,
    signer: Signer,
    case_id: str,
    action_id: str,
    tool: str,
    finding_ids: list[str],
    *,
    require_disputable: bool,
):
    if not finding_ids:
        _deny(store, case_id, tool, action_id, "empty_finding_ids")
    by_id = {f.finding_id: (f, sig) for f, sig in store.get_findings(case_id)}
    cited = []
    for finding_id in finding_ids:
        entry = by_id.get(finding_id)
        if entry is None:
            _deny(store, case_id, tool, action_id, "foreign_finding")
        finding, signature = entry
        if not verify_finding(signer, finding, signature):
            _deny(store, case_id, tool, action_id, "bad_finding_signature")
        if require_disputable and not finding.disputable:
            _deny(store, case_id, tool, action_id, "not_disputable")
        cited.append(finding)
    return cited


def _case_meta(store: CaseStore, case_id: str, tool: str, action_id: str) -> dict:
    case = store.get_case(case_id)
    meta = case.get("meta")
    if meta is None:
        _deny(store, case_id, tool, action_id, "case_not_found")
    return meta


def _check_recipient(
    store: CaseStore,
    case_id: str,
    tool: str,
    action_id: str,
    hospitals: dict[str, Hospital],
    hospital_id: str | None,
    recipient_email: str,
) -> None:
    hospital = hospitals.get(hospital_id) if hospital_id else None
    if hospital is None or not recipient_allowed(hospital, recipient_email):
        _deny(store, case_id, tool, action_id, "recipient_not_allowed")


def _finish(
    store: CaseStore,
    email_sender: EmailSender,
    *,
    case_id: str,
    org: str,
    tool: str,
    action_id: str,
    recipient_email: str,
    subject: str,
    letter_markdown: str,
    reply_to: str,
    new_status: str,
) -> dict:
    message_id = email_sender.send(
        to=recipient_email, subject=subject, markdown=letter_markdown, reply_to=reply_to or None
    )
    store.update_action_status(case_id, action_id, "sent", message_id=message_id)
    store.add_event(
        case_id, "action_sent", f"{tool} sent to {recipient_email}",
        {"action_id": action_id, "message_id": message_id},
    )
    store.update_summary(case_id, org, status=new_status)
    store.add_decision(case_id, layer="lambda", tool=tool, outcome="allow", reason="", action_id=action_id)
    return {"status": "sent", "message_id": message_id, "action_id": action_id}


def send_dispute_letter(
    store: CaseStore,
    signer: Signer,
    hospitals: dict[str, Hospital],
    email_sender: EmailSender,
    *,
    case_id: str,
    action_id: str,
    recipient_email: str,
    finding_ids: list[str],
    disputed_amount_cents: int,
    approval_token: str,
    now: int | None = None,
) -> dict:
    tool = "actions___send_dispute_letter"
    now = now if now is not None else int(time.time())
    tool_input = {
        "case_id": case_id,
        "action_id": action_id,
        "recipient_email": recipient_email,
        "finding_ids": finding_ids,
        "disputed_amount_cents": disputed_amount_cents,
        "approval_token": approval_token,
    }

    action = _load_pending_action(store, case_id, action_id, tool)
    _check_token(store, signer, case_id, action_id, tool, tool_input, now)
    _check_hash(store, case_id, action_id, tool, action, _without_token(tool_input))
    cited = _cited_findings(store, signer, case_id, action_id, tool, finding_ids, require_disputable=True)

    cap = sum(f.amount_cents for f in cited)
    if disputed_amount_cents > cap:
        _deny(store, case_id, tool, action_id, "amount_exceeds_findings")

    meta = _case_meta(store, case_id, tool, action_id)
    _check_recipient(store, case_id, tool, action_id, hospitals, meta.get("hospital_id"), recipient_email)

    return _finish(
        store, email_sender,
        case_id=case_id, org=meta["org"], tool=tool, action_id=action_id,
        recipient_email=recipient_email, subject=f"{_SUBJECT_BY_TOOL[tool]} -- case {case_id}",
        letter_markdown=action["letter_markdown"], reply_to=meta.get("reply_to_email") or "",
        new_status="sent",
    )


def submit_fap_application(
    store: CaseStore,
    signer: Signer,
    hospitals: dict[str, Hospital],
    email_sender: EmailSender,
    *,
    case_id: str,
    action_id: str,
    recipient_email: str,
    approval_token: str,
    now: int | None = None,
) -> dict:
    tool = "actions___submit_fap_application"
    now = now if now is not None else int(time.time())
    tool_input = {
        "case_id": case_id,
        "action_id": action_id,
        "recipient_email": recipient_email,
        "approval_token": approval_token,
    }

    action = _load_pending_action(store, case_id, action_id, tool)
    _check_token(store, signer, case_id, action_id, tool, tool_input, now)
    _check_hash(store, case_id, action_id, tool, action, _without_token(tool_input))

    meta = _case_meta(store, case_id, tool, action_id)
    _check_recipient(store, case_id, tool, action_id, hospitals, meta.get("hospital_id"), recipient_email)

    return _finish(
        store, email_sender,
        case_id=case_id, org=meta["org"], tool=tool, action_id=action_id,
        recipient_email=recipient_email, subject=f"{_SUBJECT_BY_TOOL[tool]} -- case {case_id}",
        letter_markdown=action["letter_markdown"], reply_to=meta.get("reply_to_email") or "",
        new_status="sent",
    )


def request_itemized_bill(
    store: CaseStore,
    signer: Signer,
    hospitals: dict[str, Hospital],
    email_sender: EmailSender,
    *,
    case_id: str,
    action_id: str,
    recipient_email: str,
    approval_token: str,
    now: int | None = None,
) -> dict:
    tool = "actions___request_itemized_bill"
    now = now if now is not None else int(time.time())
    tool_input = {
        "case_id": case_id,
        "action_id": action_id,
        "recipient_email": recipient_email,
        "approval_token": approval_token,
    }

    action = _load_pending_action(store, case_id, action_id, tool)
    _check_token(store, signer, case_id, action_id, tool, tool_input, now)
    _check_hash(store, case_id, action_id, tool, action, _without_token(tool_input))

    meta = _case_meta(store, case_id, tool, action_id)
    _check_recipient(store, case_id, tool, action_id, hospitals, meta.get("hospital_id"), recipient_email)

    return _finish(
        store, email_sender,
        case_id=case_id, org=meta["org"], tool=tool, action_id=action_id,
        recipient_email=recipient_email, subject=f"{_SUBJECT_BY_TOOL[tool]} -- case {case_id}",
        letter_markdown=action["letter_markdown"], reply_to=meta.get("reply_to_email") or "",
        new_status="sent",
    )


def file_escalation(
    store: CaseStore,
    signer: Signer,
    hospitals: dict[str, Hospital],
    email_sender: EmailSender,
    *,
    case_id: str,
    action_id: str,
    channel: str,
    finding_ids: list[str],
    approval_token: str,
    now: int | None = None,
) -> dict:
    tool = "actions___file_escalation"
    now = now if now is not None else int(time.time())
    tool_input = {
        "case_id": case_id,
        "action_id": action_id,
        "channel": channel,
        "finding_ids": finding_ids,
        "approval_token": approval_token,
    }

    action = _load_pending_action(store, case_id, action_id, tool)
    _check_token(store, signer, case_id, action_id, tool, tool_input, now)
    _check_hash(store, case_id, action_id, tool, action, _without_token(tool_input))
    _cited_findings(store, signer, case_id, action_id, tool, finding_ids, require_disputable=False)

    meta = _case_meta(store, case_id, tool, action_id)
    hospital_id = meta.get("hospital_id")
    hospital = hospitals.get(hospital_id) if hospital_id else None
    if hospital is None:
        _deny(store, case_id, tool, action_id, "recipient_not_allowed")

    # Demo honest-limit: no real regulator inbox is wired up -- every
    # escalation channel lands in the same hospital demo billing inbox used
    # for every other action tool, per NOTES.md "demo letters go to a test
    # inbox".
    recipient_email = hospital.demo_billing_email

    return _finish(
        store, email_sender,
        case_id=case_id, org=meta["org"], tool=tool, action_id=action_id,
        recipient_email=recipient_email,
        subject=f"{_SUBJECT_BY_TOOL[tool]} ({channel}) -- case {case_id}",
        letter_markdown=action["letter_markdown"], reply_to=meta.get("reply_to_email") or "",
        new_status="escalated",
    )


def schedule_followup(
    store: CaseStore,
    scheduler_client,
    *,
    case_id: str,
    days: int,
    reason: str,
    followup_lambda_arn: str,
    scheduler_role_arn: str,
    scheduler_group: str = "cc-followups",
    demo_time_compression_seconds: int | None = None,
    now: datetime | None = None,
) -> dict:
    tool = "actions___schedule_followup"
    if days < 1 or days > 90:
        _deny(store, case_id, tool, None, "invalid_days")

    now = now if now is not None else datetime.now(timezone.utc)
    if demo_time_compression_seconds:
        due_at = now + timedelta(seconds=demo_time_compression_seconds * days)
    else:
        due_at = now + timedelta(days=days)

    schedule_name = new_id("followup")
    expr = f"at({due_at.strftime('%Y-%m-%dT%H:%M:%S')})"
    scheduler_client.create_schedule(
        Name=schedule_name,
        GroupName=scheduler_group,
        ScheduleExpression=expr,
        FlexibleTimeWindow={"Mode": "OFF"},
        Target={
            "Arn": followup_lambda_arn,
            "RoleArn": scheduler_role_arn,
            "Input": json.dumps({"case_id": case_id, "reason": reason}),
        },
    )

    due_at_iso = due_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    store._table.put_item(
        Item={
            "PK": f"CASE#{case_id}",
            "SK": f"FOLLOWUP#{schedule_name}",
            "due_at": due_at_iso,
            "reason": reason,
            "status": "scheduled",
        }
    )
    store.add_decision(case_id, layer="lambda", tool=tool, outcome="allow", reason="", action_id=None)

    return {"status": "scheduled", "schedule_name": schedule_name, "due_at": due_at_iso}
