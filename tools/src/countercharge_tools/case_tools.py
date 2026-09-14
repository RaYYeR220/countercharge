"""Gateway target ``case`` (internal writes, C1)."""

from pydantic import ValidationError

from countercharge_core.approval import input_hash
from countercharge_core.grounding import verify_letter
from countercharge_core.ids import new_action_id
from countercharge_core.store import CaseStore
from countercharge_engine.models import Bill, EOB, GFE, Household

from countercharge_tools.deps import ToolError

_KIND_MODELS = {"bill": Bill, "eob": EOB, "household": Household, "gfe": GFE}

# type -> fully-qualified Gateway tool name. This is the exact string that
# ends up in the drafted action's ``tool`` field, in the approval token's
# ``tool`` claim (via issue_token), and in what action_tools compares
# against when verifying that token -- all three must agree.
_ACTION_TOOL_BY_TYPE = {
    "dispute_letter": "actions___send_dispute_letter",
    "fap_application": "actions___submit_fap_application",
    "itemized_request": "actions___request_itemized_bill",
    "escalation": "actions___file_escalation",
}


def get_case(store: CaseStore, *, case_id: str) -> dict:
    return store.get_case(case_id)


def save_extraction(
    store: CaseStore, *, case_id: str, kind: str, payload: dict, confidence: float
) -> dict:
    model = _KIND_MODELS.get(kind)
    if model is None:
        raise ToolError(f"unknown document kind: {kind}")
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        raise ToolError(f"invalid {kind} payload: {exc}") from exc

    doc_id = store.put_doc(case_id, kind=kind, payload=payload, confidence=confidence)
    return {"doc_id": doc_id}


def _latest_doc(docs: list[dict], kind: str) -> dict | None:
    matching = [d for d in docs if d.get("kind") == kind]
    return matching[-1] if matching else None


def draft_action(
    store: CaseStore,
    *,
    case_id: str,
    type: str,
    recipient_email: str,
    finding_ids: list[str],
    letter_markdown: str,
    disputed_amount_cents: int,
    channel: str | None = None,
) -> dict:
    tool = _ACTION_TOOL_BY_TYPE.get(type)
    if tool is None:
        raise ToolError(f"unknown action type: {type}")
    if type == "escalation" and not channel:
        raise ToolError("escalation drafts require a channel")

    case = store.get_case(case_id)
    if case.get("meta") is None:
        raise ToolError(f"case not found: {case_id}")

    by_id = {finding.finding_id: finding for finding, _sig in store.get_findings(case_id)}
    cited = []
    for finding_id in finding_ids:
        finding = by_id.get(finding_id)
        if finding is None:
            raise ToolError(f"unknown finding_id: {finding_id}")
        cited.append(finding)

    bill_doc = _latest_doc(case["docs"], "bill")
    bill = Bill.model_validate(bill_doc["payload"]) if bill_doc else None

    grounding = verify_letter(letter_markdown, cited, bill)

    action_id = new_action_id()
    if type == "dispute_letter":
        tool_input = {
            "case_id": case_id,
            "action_id": action_id,
            "recipient_email": recipient_email,
            "finding_ids": finding_ids,
            "disputed_amount_cents": disputed_amount_cents,
        }
    elif type == "escalation":
        tool_input = {
            "case_id": case_id,
            "action_id": action_id,
            "channel": channel,
            "finding_ids": finding_ids,
        }
    else:  # fap_application, itemized_request
        tool_input = {
            "case_id": case_id,
            "action_id": action_id,
            "recipient_email": recipient_email,
        }

    hashed = input_hash(tool_input)
    status = "pending_approval" if grounding.ok else "rejected_grounding"

    store.put_action(
        case_id,
        type=type,
        status=status,
        tool=tool,
        tool_input=tool_input,
        input_hash=hashed,
        recipient_email=recipient_email,
        finding_ids=finding_ids,
        disputed_amount_cents=disputed_amount_cents,
        letter_markdown=letter_markdown,
        action_id=action_id,
    )
    store.add_event(
        case_id, "action_drafted", f"drafted {type}", {"action_id": action_id, "status": status}
    )

    return {
        "action_id": action_id,
        "tool": tool,
        "tool_input": tool_input,
        "input_hash": hashed,
        "grounding": {
            "ok": grounding.ok,
            "unsupported_amounts": grounding.unsupported_amounts,
            "unsupported_codes": grounding.unsupported_codes,
        },
    }
