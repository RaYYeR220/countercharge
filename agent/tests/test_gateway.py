import json

import pytest

from countercharge_agent.gateway import call_gateway_tool, classify_denial, result_text


def test_result_text_concatenates_text_blocks():
    result = {"status": "success", "content": [{"text": "one"}, {"text": "two"}]}
    assert result_text(result) == "one\ntwo"


def test_result_text_handles_missing_content():
    assert result_text({"status": "success"}) == ""
    assert result_text(None) == ""
    assert result_text("not a dict") == ""


def test_classify_denial_lambda_layer():
    layer, reason = classify_denial("DENIED:lambda:bad approval token")
    assert layer == "lambda"
    assert reason == "bad approval token"


def test_classify_denial_policy_layer_from_gateway_error():
    text = (
        'Tool execution failed: {"jsonrpc":"2.0","error":{"code":-32002,"message":'
        '"Tool Execution Denied: Tool call not allowed due to policy enforcement '
        '[No policy applies to the request (denied by default).]"}}'
    )
    layer, reason = classify_denial(text)
    assert layer == "policy"
    assert reason == text


def test_classify_denial_returns_none_for_ordinary_error():
    assert classify_denial("Tool execution failed: connection timed out") is None
    assert classify_denial("") is None


class _FakeCaller:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def call_tool_sync(self, tool_use_id, name, arguments=None):
        self.calls.append((tool_use_id, name, arguments))
        return self.result


def test_call_gateway_tool_parses_json_success():
    caller = _FakeCaller({"status": "success", "content": [{"text": json.dumps({"doc_id": "doc_1"})}]})

    output = call_gateway_tool(caller, "case___save_extraction", case_id="case_1", kind="bill")

    assert output == {"doc_id": "doc_1"}
    assert caller.calls[0][1] == "case___save_extraction"
    assert caller.calls[0][2] == {"case_id": "case_1", "kind": "bill"}


def test_call_gateway_tool_raises_on_error_status():
    caller = _FakeCaller({"status": "error", "content": [{"text": "DENIED:lambda:bad recipient"}]})

    with pytest.raises(RuntimeError, match="DENIED:lambda:bad recipient"):
        call_gateway_tool(caller, "actions___send_dispute_letter")
