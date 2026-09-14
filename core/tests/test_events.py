import json

from countercharge_core.events import (
    CaseStateEvent,
    DoneEvent,
    ErrorEvent,
    FindingEvent,
    InterruptAction,
    InterruptEvent,
    PolicyDeniedEvent,
    TextEvent,
    ToolEndEvent,
    ToolStartEvent,
    to_sse,
)


def test_text_event_defaults_type():
    event = TextEvent(delta="hello")
    assert event.type == "text"


def test_tool_start_event_fields():
    event = ToolStartEvent(tool="engine___audit_case", tool_use_id="t1", input={"case_id": "c1"})
    assert event.type == "tool_start"
    assert event.input == {"case_id": "c1"}


def test_tool_end_event_status_literal():
    event = ToolEndEvent(tool="engine___audit_case", tool_use_id="t1", status="denied", output=None)
    assert event.status == "denied"


def test_policy_denied_event_layer_literal():
    event = PolicyDeniedEvent(tool="actions___send_dispute_letter", layer="lambda", reason="bad_mac")
    assert event.layer == "lambda"


def test_finding_event_wraps_dict():
    event = FindingEvent(finding={"finding_id": "f_1", "rule_id": "R1"})
    assert event.finding["rule_id"] == "R1"


def test_interrupt_event_shape():
    action = InterruptAction(
        action_id="action_1",
        tool="actions___send_dispute_letter",
        tool_input={"case_id": "c1"},
        preview_markdown="# Dear Hospital",
    )
    event = InterruptEvent(interrupt_id="int_1", action=action)
    assert event.name == "approval_required"
    assert event.action.action_id == "action_1"


def test_case_state_and_done_and_error_events():
    assert CaseStateEvent(case={"status": "audited"}).case["status"] == "audited"
    assert DoneEvent(stop_reason="end_turn").stop_reason == "end_turn"
    assert ErrorEvent(message="boom").message == "boom"


def test_to_sse_renders_data_line_with_type_discriminator():
    line = to_sse(TextEvent(delta="hi"))
    assert line.startswith("data: ")
    assert line.endswith("\n\n")
    payload = json.loads(line[len("data: ") : -2])
    assert payload == {"type": "text", "delta": "hi"}


def test_to_sse_roundtrips_tool_end_event():
    event = ToolEndEvent(tool="x", tool_use_id="u1", status="success", output={"ok": True})
    payload = json.loads(to_sse(event)[len("data: ") : -2])
    assert payload["status"] == "success"
    assert payload["output"] == {"ok": True}
