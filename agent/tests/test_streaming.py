from types import SimpleNamespace

import pytest

from countercharge_core.events import DoneEvent, FindingEvent, InterruptEvent, TextEvent, ToolEndEvent, ToolStartEvent

from countercharge_agent.streaming import EventCollector, ToolEventHook, parse_tool_output, translate


async def _stream(items):
    for item in items:
        yield item


def _text_chunk(text: str) -> dict:
    return {"event": {"contentBlockDelta": {"delta": {"text": text}}}}


def _empty_content_block_start() -> dict:
    return {"event": {"contentBlockStart": {"start": {}}}}


@pytest.mark.asyncio
async def test_translate_emits_text_events_for_deltas():
    collector = EventCollector()
    stream = _stream(
        [
            _empty_content_block_start(),
            _text_chunk("Hello"),
            _text_chunk(" world"),
            {"result": SimpleNamespace(stop_reason="end_turn", interrupts=None)},
        ]
    )

    events = [event async for event in translate(stream, collector)]

    assert events == [TextEvent(delta="Hello"), TextEvent(delta=" world"), DoneEvent(stop_reason="end_turn")]


@pytest.mark.asyncio
async def test_translate_converts_terminal_interrupt_result():
    collector = EventCollector()
    interrupt = SimpleNamespace(
        id="int-1",
        name="approval_required",
        reason={
            "action_id": "action_1",
            "tool": "actions___send_dispute_letter",
            "tool_input": {"case_id": "case_1"},
            "preview_markdown": "Dear NYP, ...",
        },
    )
    stream = _stream([{"result": SimpleNamespace(stop_reason="interrupt", interrupts=[interrupt])}])

    events = [event async for event in translate(stream, collector)]

    assert len(events) == 2
    assert isinstance(events[0], InterruptEvent)
    assert events[0].interrupt_id == "int-1"
    assert events[0].action.action_id == "action_1"
    assert events[0].action.tool == "actions___send_dispute_letter"
    assert events[0].action.preview_markdown == "Dear NYP, ..."
    assert events[1] == DoneEvent(stop_reason="interrupt")


@pytest.mark.asyncio
async def test_translate_drains_collector_between_upstream_events():
    collector = EventCollector()
    collector.emit(ToolStartEvent(tool="engine___audit_case", tool_use_id="t1", input={}))
    stream = _stream([{"result": SimpleNamespace(stop_reason="end_turn", interrupts=None)}])

    events = [event async for event in translate(stream, collector)]

    assert events[0] == ToolStartEvent(tool="engine___audit_case", tool_use_id="t1", input={})
    assert events[1] == DoneEvent(stop_reason="end_turn")


def _before_event(tool_name, tool_input=None):
    return SimpleNamespace(tool_use={"name": tool_name, "toolUseId": "t1", "input": tool_input or {}})


def _after_event(tool_name, *, result, cancel_message=None):
    return SimpleNamespace(
        tool_use={"name": tool_name, "toolUseId": "t1"}, result=result, cancel_message=cancel_message
    )


def test_tool_event_hook_emits_start_and_success_end():
    collector = EventCollector()
    hook = ToolEventHook(collector=collector)

    hook._before(_before_event("engine___check_units", {"code": "99284"}))
    hook._after(
        _after_event(
            "engine___check_units",
            result={"status": "success", "content": [{"text": '{"exceeds": false}'}]},
        )
    )

    events = collector.drain()
    assert events[0] == ToolStartEvent(tool="engine___check_units", tool_use_id="t1", input={"code": "99284"})
    assert events[1] == ToolEndEvent(
        tool="engine___check_units", tool_use_id="t1", status="success", output={"exceeds": False}
    )


def test_tool_event_hook_emits_denied_status_on_hook_cancellation():
    collector = EventCollector()
    hook = ToolEventHook(collector=collector)

    hook._after(
        _after_event(
            "actions___send_dispute_letter",
            result={"status": "error", "content": [{"text": "approval was not granted"}]},
            cancel_message="approval was not granted",
        )
    )

    (event,) = collector.drain()
    assert event.status == "denied"
    assert event.tool == "actions___send_dispute_letter"


def test_tool_event_hook_emits_denied_status_on_policy_denial():
    collector = EventCollector()
    hook = ToolEventHook(collector=collector)

    hook._after(
        _after_event(
            "actions___send_dispute_letter",
            result={"status": "error", "content": [{"text": "Tool Execution Denied: nope"}]},
        )
    )

    (event,) = collector.drain()
    assert event.status == "denied"


def test_tool_event_hook_emits_findings_after_successful_audit():
    collector = EventCollector()
    hook = ToolEventHook(collector=collector)
    finding = {"finding_id": "f1", "rule_id": "DUPLICATE", "disputable": True, "amount_cents": 14500}
    output = {"report": {"findings": [finding], "advisory": [], "disputable_cents": 14500}}

    hook._after(
        _after_event(
            "engine___audit_case",
            result={"status": "success", "content": [{"text": __import__("json").dumps(output)}]},
        )
    )

    events = collector.drain()
    assert events[0].status == "success"
    assert events[1] == FindingEvent(finding=finding)


def test_parse_tool_output_falls_back_to_raw_text_on_non_json():
    result = {"status": "success", "content": [{"text": "not json"}]}
    assert parse_tool_output(result) == "not json"


def test_parse_tool_output_returns_none_for_empty_result():
    assert parse_tool_output({"status": "success", "content": []}) is None
