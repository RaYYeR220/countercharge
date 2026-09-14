"""Approval interrupt across two separate ``Agent`` builds sharing one S3-backed
session -- the same durability proof as the spike's two-process invocation1/
invocation2 pair (FINDINGS.md), but via moto instead of two real processes.
"""

import boto3
import pytest
from moto import mock_aws
from strands.session.s3_session_manager import S3SessionManager

from countercharge_core.events import InterruptEvent, PolicyDeniedEvent, ToolEndEvent

from countercharge_agent import streaming
from countercharge_agent.app import build_agent

from conftest import ScriptedModel
from fake_tools import make_fake_gateway_tools

BUCKET = "test-sessions-bucket"


class _FakeStore:
    def __init__(self, action=None):
        self._action = action
        self.decisions = []

    def get_action(self, case_id, action_id):
        return self._action

    def add_decision(self, case_id, layer, tool, outcome, reason, action_id=None):
        self.decisions.append({"layer": layer, "tool": tool, "outcome": outcome, "reason": reason})


class _UnusedCaller:
    def call_tool_sync(self, tool_use_id, name, arguments=None):
        raise AssertionError(f"unexpected direct gateway call: {name}")


@pytest.fixture
def s3_bucket():
    """moto's ``@mock_aws`` decorator breaks pytest-asyncio's coroutine-function
    detection when applied directly to an async test, so it is entered as a plain
    context manager around the fixture's lifetime instead."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        yield


@pytest.mark.asyncio
async def test_action_without_token_interrupts_then_resume_injects_token(s3_bucket):
    case_id = "case_approve"
    store = _FakeStore(action={"letter_markdown": "Dear NYP Billing, please correct this charge."})

    # --- "invocation 1": fresh Agent, fresh fake tools, persisted S3 session ---
    fake1 = make_fake_gateway_tools()
    model1 = ScriptedModel(
        steps=[
            {
                "tool": "actions___send_dispute_letter",
                "input": {
                    "case_id": case_id,
                    "action_id": "action_1",
                    "recipient_email": "billing@nyp.org",
                    "finding_ids": ["f1"],
                    "disputed_amount_cents": 14500,
                },
            },
            {"text": "Letter sent, thanks for approving."},
        ]
    )
    collector1 = streaming.EventCollector()
    session1 = S3SessionManager(session_id=case_id, bucket=BUCKET, prefix="sessions/")
    agent1 = build_agent(
        case_id=case_id,
        store=store,
        gateway_tools=fake1["tools"],
        gateway_caller=_UnusedCaller(),
        collector=collector1,
        model=model1,
        session_manager=session1,
    )

    events1 = [event async for event in streaming.translate(agent1.stream_async("please send the letter"), collector1)]

    interrupt_events = [event for event in events1 if isinstance(event, InterruptEvent)]
    assert len(interrupt_events) == 1
    interrupt_event = interrupt_events[0]
    assert interrupt_event.name == "approval_required"
    assert interrupt_event.action.action_id == "action_1"
    assert interrupt_event.action.tool == "actions___send_dispute_letter"
    assert interrupt_event.action.preview_markdown == "Dear NYP Billing, please correct this charge."

    # The tool must never have actually run yet.
    assert fake1["calls"] == []

    # --- "invocation 2": brand new Agent + fake tools + session manager, same session id ---
    fake2 = make_fake_gateway_tools()
    model2 = ScriptedModel(
        steps=[
            {
                "tool": "actions___send_dispute_letter",
                "input": {
                    "case_id": case_id,
                    "action_id": "action_1",
                    "recipient_email": "billing@nyp.org",
                    "finding_ids": ["f1"],
                    "disputed_amount_cents": 14500,
                },
            },
            {"text": "Letter sent, thanks for approving."},
        ]
    )
    collector2 = streaming.EventCollector()
    session2 = S3SessionManager(session_id=case_id, bucket=BUCKET, prefix="sessions/")
    agent2 = build_agent(
        case_id=case_id,
        store=store,
        gateway_tools=fake2["tools"],
        gateway_caller=_UnusedCaller(),
        collector=collector2,
        model=model2,
        session_manager=session2,
    )

    responses = [
        {"interruptResponse": {"interruptId": interrupt_event.interrupt_id, "response": {"approval_token": "tok-abc"}}}
    ]
    events2 = [event async for event in streaming.translate(agent2.stream_async(responses), collector2)]

    assert fake2["calls"] == [("actions___send_dispute_letter", {"approval_token": "tok-abc"})]

    tool_ends = [event for event in events2 if isinstance(event, ToolEndEvent)]
    assert any(event.tool == "actions___send_dispute_letter" and event.status == "success" for event in tool_ends)
    assert not any(isinstance(event, PolicyDeniedEvent) for event in events2)


@pytest.mark.asyncio
async def test_action_rejected_never_calls_the_tool(s3_bucket):
    case_id = "case_reject"
    store = _FakeStore(action=None)

    fake1 = make_fake_gateway_tools()
    model1 = ScriptedModel(
        steps=[
            {
                "tool": "actions___file_escalation",
                "input": {"case_id": case_id, "channel": "STATE_AG", "finding_ids": ["f1"]},
            },
            {"text": "Understood, I will not file that escalation."},
        ]
    )
    collector1 = streaming.EventCollector()
    session1 = S3SessionManager(session_id=case_id, bucket=BUCKET, prefix="sessions/")
    agent1 = build_agent(
        case_id=case_id,
        store=store,
        gateway_tools=fake1["tools"],
        gateway_caller=_UnusedCaller(),
        collector=collector1,
        model=model1,
        session_manager=session1,
    )

    events1 = [event async for event in streaming.translate(agent1.stream_async("file an escalation"), collector1)]
    interrupt_event = next(event for event in events1 if isinstance(event, InterruptEvent))
    assert fake1["calls"] == []

    fake2 = make_fake_gateway_tools()
    model2 = ScriptedModel(
        steps=[
            {
                "tool": "actions___file_escalation",
                "input": {"case_id": case_id, "channel": "STATE_AG", "finding_ids": ["f1"]},
            },
            {"text": "Understood, I will not file that escalation."},
        ]
    )
    collector2 = streaming.EventCollector()
    session2 = S3SessionManager(session_id=case_id, bucket=BUCKET, prefix="sessions/")
    agent2 = build_agent(
        case_id=case_id,
        store=store,
        gateway_tools=fake2["tools"],
        gateway_caller=_UnusedCaller(),
        collector=collector2,
        model=model2,
        session_manager=session2,
    )

    responses = [
        {
            "interruptResponse": {
                "interruptId": interrupt_event.interrupt_id,
                "response": {"decision": "reject", "note": "not authorized"},
            }
        }
    ]
    events2 = [event async for event in streaming.translate(agent2.stream_async(responses), collector2)]

    # The tool must never actually run when rejected.
    assert fake2["calls"] == []

    denied = [event for event in events2 if isinstance(event, PolicyDeniedEvent)]
    assert len(denied) == 1
    assert denied[0].layer == "hook"
    assert denied[0].reason == "not authorized"
    assert store.decisions == [
        {"layer": "hook", "tool": "actions___file_escalation", "outcome": "deny", "reason": "not authorized"}
    ]
