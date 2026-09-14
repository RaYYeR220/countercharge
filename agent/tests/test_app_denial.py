"""-32002 Gateway policy denial -> policy_denied event + Decision recorded in DynamoDB (moto)."""

import boto3
import pytest
from moto import mock_aws
from strands import tool

from countercharge_core.events import DoneEvent, PolicyDeniedEvent
from countercharge_core.store import CaseStore

from countercharge_agent import streaming
from countercharge_agent.app import build_agent

from conftest import ScriptedModel

TABLE_NAME = "cc-cases-test"

_POLICY_DENIAL_TEXT = (
    'Tool execution failed: {"jsonrpc":"2.0","error":{"code":-32002,"message":'
    '"Tool Execution Denied: Tool call not allowed due to policy enforcement '
    "[No policy applies to the request (denied by default).]\"}}"
)


@tool
def actions___schedule_followup(case_id: str, days: int, reason: str) -> dict:
    """Fake Gateway tool whose result mimics a real MCPClient surfacing a -32002 Gateway denial."""
    return {"status": "error", "content": [{"text": _POLICY_DENIAL_TEXT}]}


class _UnusedCaller:
    def call_tool_sync(self, tool_use_id, name, arguments=None):
        raise AssertionError(f"unexpected direct gateway call: {name}")


def _create_table(dynamodb):
    dynamodb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
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
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def case_store():
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        _create_table(dynamodb)
        store = CaseStore(dynamodb.Table(TABLE_NAME))
        store.create_case(
            org="org1",
            patient_name="Pat Test",
            patient_email="pat@example.com",
            patient_state="NY",
            reply_to_email="pat@example.com",
            hospital_id="nyp",
            case_id="case_1",
        )
        yield store


@pytest.mark.asyncio
async def test_gateway_policy_denial_emits_event_and_records_decision(case_store):
    model = ScriptedModel(
        steps=[
            {"tool": "actions___schedule_followup", "input": {"case_id": "case_1", "days": 30, "reason": "no reply"}},
            {"text": "Understood -- I could not schedule that follow-up."},
        ]
    )
    collector = streaming.EventCollector()
    agent = build_agent(
        case_id="case_1",
        store=case_store,
        gateway_tools=[actions___schedule_followup],
        gateway_caller=_UnusedCaller(),
        collector=collector,
        model=model,
    )

    events = [event async for event in streaming.translate(agent.stream_async("schedule a followup"), collector)]

    denied = [event for event in events if isinstance(event, PolicyDeniedEvent)]
    assert len(denied) == 1
    assert denied[0].layer == "policy"
    assert denied[0].tool == "actions___schedule_followup"
    assert events[-1] == DoneEvent(stop_reason="end_turn")

    decisions = case_store.list_decisions("case_1")
    assert len(decisions) == 1
    assert decisions[0]["layer"] == "policy"
    assert decisions[0]["tool"] == "actions___schedule_followup"
    assert decisions[0]["outcome"] == "deny"
