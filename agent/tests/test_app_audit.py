import pytest

from countercharge_core.events import DoneEvent, FindingEvent, ToolEndEvent, ToolStartEvent

from countercharge_agent import streaming
from countercharge_agent.app import build_agent
from countercharge_agent.prompts import AUDIT_MODE_INSTRUCTION

from conftest import ScriptedModel
from fake_tools import make_fake_gateway_tools


class _FakeStore:
    def get_action(self, case_id, action_id):
        return None

    def add_decision(self, *args, **kwargs):
        pass


class _UnusedCaller:
    """No subagent tool is invoked in this test; any call is a bug."""

    def call_tool_sync(self, tool_use_id, name, arguments=None):
        raise AssertionError(f"unexpected direct gateway call: {name}")


@pytest.mark.asyncio
async def test_audit_mode_streams_tool_start_tool_end_finding_done():
    fake = make_fake_gateway_tools()
    model = ScriptedModel(
        steps=[
            {"tool": "engine___audit_case", "input": {"case_id": "case_1"}},
            {"text": "I found one duplicate lab charge worth $145.00 that you can dispute."},
        ]
    )
    collector = streaming.EventCollector()
    agent = build_agent(
        case_id="case_1",
        store=_FakeStore(),
        gateway_tools=fake["tools"],
        gateway_caller=_UnusedCaller(),
        collector=collector,
        model=model,
    )

    events = [event async for event in streaming.translate(agent.stream_async(AUDIT_MODE_INSTRUCTION), collector)]

    types_seen = [type(event) for event in events]
    assert ToolStartEvent in types_seen
    assert ToolEndEvent in types_seen
    assert FindingEvent in types_seen
    assert events[-1] == DoneEvent(stop_reason="end_turn")

    tool_start = next(event for event in events if isinstance(event, ToolStartEvent))
    assert tool_start.tool == "engine___audit_case"
    assert tool_start.input == {"case_id": "case_1"}

    tool_end = next(event for event in events if isinstance(event, ToolEndEvent))
    assert tool_end.tool == "engine___audit_case"
    assert tool_end.status == "success"

    finding_event = next(event for event in events if isinstance(event, FindingEvent))
    assert finding_event.finding["finding_id"] == "f1"
    assert finding_event.finding["disputable"] is True

    # engine___audit_case ran exactly once
    assert fake["calls"] == [("engine___audit_case", {"case_id": "case_1"})]
