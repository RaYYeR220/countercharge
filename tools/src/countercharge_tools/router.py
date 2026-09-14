"""Single Lambda entrypoint for the ``engine``/``case``/``actions`` Gateway
targets (``CC_TARGET`` env var picks which one this container instance is).

Per the AgentCore Gateway Lambda-target contract: the event is a flat dict
of the tool's ``inputSchema`` properties, and the (target-prefixed) tool
name is at ``context.client_context.custom["bedrockAgentCoreToolName"]``
(``"<target>___<tool>"``); the prefix is stripped here before dispatch.

Any exception raised by a tool function is left to propagate: Lambda then
reports the invocation as a ``FunctionError``, which is what makes
AgentCore Gateway return an MCP tool result with ``isError: true`` whose
text is the exception message -- see ``deps.ToolDenied``/``ToolError`` and
the task report for why raising (not returning ``{"error": ...}``) is the
one that actually sets ``isError``.
"""

import json
import logging
import os

from countercharge_tools import action_tools, case_tools, engine_tools
from countercharge_tools.deps import (
    ToolError,
    get_email_sender,
    get_hospitals,
    get_scheduler_client,
    get_settings,
    get_signer,
    get_store,
)
from countercharge_tools.refdata_loader import load_refdata

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _tool_name(context) -> str:
    try:
        raw = context.client_context.custom.get("bedrockAgentCoreToolName", "") or ""
    except Exception:
        raw = ""
    return raw.split("___")[-1] if "___" in raw else raw


def _dispatch_engine(tool: str, event: dict, settings) -> object:
    refdata = load_refdata(settings)
    if tool == "audit_case":
        return engine_tools.audit_case(
            get_store(settings), get_signer(settings), refdata, case_id=event["case_id"]
        )
    if tool == "check_code_pair":
        return engine_tools.check_code_pair(
            refdata,
            code1=event["code1"], code2=event["code2"], setting=event["setting"], dos=event["dos"],
        )
    if tool == "check_units":
        return engine_tools.check_units(
            refdata, code=event["code"], units=event["units"], setting=event["setting"]
        )
    if tool == "fap_eligibility":
        return engine_tools.fap_eligibility(
            refdata,
            hospital_id=event["hospital_id"], household_size=event["household_size"],
            annual_income_cents=event["annual_income_cents"], state=event["state"],
        )
    if tool == "hospital_price":
        return engine_tools.hospital_price(refdata, hospital_id=event["hospital_id"], code=event["code"])
    if tool == "explain_rule":
        return engine_tools.explain_rule(rule_id=event["rule_id"])
    raise ToolError(f"unknown engine tool: {tool}")


def _dispatch_case(tool: str, event: dict, settings) -> object:
    store = get_store(settings)
    if tool == "get_case":
        return case_tools.get_case(store, case_id=event["case_id"])
    if tool == "save_extraction":
        return case_tools.save_extraction(
            store,
            case_id=event["case_id"], kind=event["kind"], payload=event["payload"],
            confidence=event["confidence"],
        )
    if tool == "draft_action":
        return case_tools.draft_action(
            store,
            case_id=event["case_id"], type=event["type"], recipient_email=event["recipient_email"],
            finding_ids=event["finding_ids"], letter_markdown=event["letter_markdown"],
            disputed_amount_cents=event["disputed_amount_cents"], channel=event.get("channel"),
        )
    raise ToolError(f"unknown case tool: {tool}")


def _dispatch_actions(tool: str, event: dict, settings) -> object:
    store = get_store(settings)
    signer = get_signer(settings)
    hospitals = get_hospitals(settings)
    email_sender = get_email_sender(settings)

    if tool == "send_dispute_letter":
        return action_tools.send_dispute_letter(
            store, signer, hospitals, email_sender,
            case_id=event["case_id"], action_id=event["action_id"],
            recipient_email=event["recipient_email"], finding_ids=event["finding_ids"],
            disputed_amount_cents=event["disputed_amount_cents"],
            approval_token=event.get("approval_token", ""),
        )
    if tool == "submit_fap_application":
        return action_tools.submit_fap_application(
            store, signer, hospitals, email_sender,
            case_id=event["case_id"], action_id=event["action_id"],
            recipient_email=event["recipient_email"], approval_token=event.get("approval_token", ""),
        )
    if tool == "request_itemized_bill":
        return action_tools.request_itemized_bill(
            store, signer, hospitals, email_sender,
            case_id=event["case_id"], action_id=event["action_id"],
            recipient_email=event["recipient_email"], approval_token=event.get("approval_token", ""),
        )
    if tool == "file_escalation":
        return action_tools.file_escalation(
            store, signer, hospitals, email_sender,
            case_id=event["case_id"], action_id=event["action_id"], channel=event["channel"],
            finding_ids=event["finding_ids"], approval_token=event.get("approval_token", ""),
        )
    if tool == "schedule_followup":
        return action_tools.schedule_followup(
            store, get_scheduler_client(settings),
            case_id=event["case_id"], days=event["days"], reason=event["reason"],
            followup_lambda_arn=os.environ["FOLLOWUP_LAMBDA_ARN"],
            scheduler_role_arn=os.environ["SCHEDULER_ROLE_ARN"],
            scheduler_group=os.environ.get("SCHEDULER_GROUP", "cc-followups"),
            demo_time_compression_seconds=_int_env("DEMO_TIME_COMPRESSION_SECONDS"),
        )
    raise ToolError(f"unknown actions tool: {tool}")


def _int_env(name: str) -> int | None:
    raw = os.environ.get(name)
    return int(raw) if raw else None


_DISPATCH_BY_TARGET = {
    "engine": _dispatch_engine,
    "case": _dispatch_case,
    "actions": _dispatch_actions,
}


def handler(event: dict, context) -> object:
    tool = _tool_name(context)
    target = os.environ.get("CC_TARGET", "")

    logger.info(json.dumps({"target": target, "tool": tool}))

    dispatch = _DISPATCH_BY_TARGET.get(target)
    if dispatch is None:
        raise ToolError(f"unknown CC_TARGET: {target!r}")

    settings = get_settings()
    return dispatch(tool, event, settings)
