"""Fake Gateway tools (C1): ``@tool`` functions named exactly like ``target___tool``,
standing in for the real AgentCore Gateway MCP tools in unit tests.
"""

from strands import tool

SAMPLE_FINDING = {
    "finding_id": "f1",
    "rule_id": "DUPLICATE",
    "disputable": True,
    "line_ids": ["L2", "L3"],
    "amount_cents": 14500,
    "title": "Duplicate lab charge",
    "detail": "Line L3 duplicates the complete blood count already billed on L2.",
    "citation": {"dataset": "engine", "version": "test-1", "url": "https://example.org/duplicate", "record": {}},
    "evidence": {"target_key": "L3"},
}


def make_fake_gateway_tools(*, store=None):
    """Build a fresh set of fake Gateway tools plus a shared ``calls`` log for assertions."""
    calls: list[tuple[str, dict]] = []

    @tool
    def engine___audit_case(case_id: str) -> dict:
        calls.append(("engine___audit_case", {"case_id": case_id}))
        return {
            "report": {
                "findings": [SAMPLE_FINDING],
                "advisory": [],
                "disputable_cents": 14500,
                "fap": None,
                "refdata_version": "test-1",
            },
            "signed": [{"finding_id": "f1", "signature": "sig1"}],
        }

    @tool
    def case___get_case(case_id: str) -> dict:
        calls.append(("case___get_case", {"case_id": case_id}))
        if store is None:
            return {"case_id": case_id, "meta": None, "docs": [], "findings": [], "actions": []}
        return store.get_case(case_id)

    @tool
    def case___draft_action(
        case_id: str,
        type: str,
        recipient_email: str,
        finding_ids: list,
        letter_markdown: str,
        disputed_amount_cents: int,
    ) -> dict:
        calls.append(("case___draft_action", {"case_id": case_id, "type": type}))
        action_id = "action_1"
        if store is not None:
            action_id = store.put_action(
                case_id,
                type=type,
                status="pending_approval",
                tool="actions___send_dispute_letter",
                tool_input={
                    "case_id": case_id,
                    "recipient_email": recipient_email,
                    "finding_ids": finding_ids,
                    "disputed_amount_cents": disputed_amount_cents,
                },
                input_hash="fake-hash",
                recipient_email=recipient_email,
                finding_ids=finding_ids,
                disputed_amount_cents=disputed_amount_cents,
                letter_markdown=letter_markdown,
            )
        return {
            "action_id": action_id,
            "tool": "actions___send_dispute_letter",
            "tool_input": {
                "case_id": case_id,
                "action_id": action_id,
                "recipient_email": recipient_email,
                "finding_ids": finding_ids,
                "disputed_amount_cents": disputed_amount_cents,
            },
            "input_hash": "fake-hash",
            "grounding": {"ok": True, "unsupported_amounts": [], "unsupported_codes": []},
        }

    @tool
    def actions___send_dispute_letter(
        case_id: str,
        action_id: str,
        recipient_email: str,
        finding_ids: list,
        disputed_amount_cents: int,
        approval_token: str = "",
    ) -> dict:
        calls.append(("actions___send_dispute_letter", {"approval_token": approval_token}))
        if not approval_token:
            return {"status": "error", "content": [{"text": "DENIED:lambda:missing approval token"}]}
        return {
            "status": "success",
            "content": [{"text": '{"status": "sent", "message_id": "msg_1", "action_id": "' + action_id + '"}'}],
        }

    @tool
    def actions___file_escalation(
        case_id: str,
        channel: str,
        finding_ids: list,
        approval_token: str = "",
    ) -> dict:
        calls.append(("actions___file_escalation", {"approval_token": approval_token}))
        if not approval_token:
            return {"status": "error", "content": [{"text": "DENIED:lambda:missing approval token"}]}
        return {
            "status": "success",
            "content": [{"text": '{"status": "sent", "message_id": "msg_2", "action_id": "action_2"}'}],
        }

    return {
        "tools": [
            engine___audit_case,
            case___get_case,
            case___draft_action,
            actions___send_dispute_letter,
            actions___file_escalation,
        ],
        "calls": calls,
    }
