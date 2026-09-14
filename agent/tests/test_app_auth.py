"""C4 entrypoint auth guard: a bad/missing bearer token never reaches the agent."""

from types import SimpleNamespace

import pytest

from countercharge_agent import app as app_module


@pytest.mark.asyncio
async def test_invoke_yields_error_event_for_missing_bearer_token(monkeypatch):
    monkeypatch.setenv("COGNITO_POOL_ID", "us-east-1_TEST")
    monkeypatch.setenv("COGNITO_REGION", "us-east-1")
    app_module._JWKS_CACHE["us-east-1_TEST"] = {"keys": []}

    payload = {"case_id": "case_1", "mode": "chat", "message": "hi"}
    context = SimpleNamespace(request_headers={}, session_id="case_1")

    events = [event async for event in app_module.invoke(payload, context)]

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "bearer" in events[0]["message"].lower()


@pytest.mark.asyncio
async def test_invoke_yields_error_event_for_invalid_token(monkeypatch):
    monkeypatch.setenv("COGNITO_POOL_ID", "us-east-1_TEST")
    monkeypatch.setenv("COGNITO_REGION", "us-east-1")
    app_module._JWKS_CACHE["us-east-1_TEST"] = {"keys": []}

    payload = {"case_id": "case_1", "mode": "chat", "message": "hi"}
    context = SimpleNamespace(request_headers={"Authorization": "Bearer not-a-real-jwt"}, session_id="case_1")

    events = [event async for event in app_module.invoke(payload, context)]

    assert len(events) == 1
    assert events[0]["type"] == "error"


def test_build_prompt_audit_mode():
    prompt = app_module._build_prompt({"mode": "audit"})
    assert "audit" in prompt.lower()


def test_build_prompt_followup_mode():
    prompt = app_module._build_prompt({"mode": "followup", "days": 14, "reason": "sent dispute letter"})
    assert "14 days" in prompt
    assert "sent dispute letter" in prompt


def test_build_prompt_chat_mode_uses_message():
    prompt = app_module._build_prompt({"mode": "chat", "message": "how much do I owe?"})
    assert prompt == "how much do I owe?"


def test_build_prompt_prefers_interrupt_responses():
    payload = {
        "mode": "chat",
        "message": "ignored",
        "interrupt_responses": [{"interrupt_id": "int-1", "approval_token": "tok-1"}],
    }
    prompt = app_module._build_prompt(payload)
    assert prompt == [{"interruptResponse": {"interruptId": "int-1", "response": {"approval_token": "tok-1"}}}]


def test_build_prompt_reject_response_shape():
    payload = {"interrupt_responses": [{"interrupt_id": "int-1", "decision": "reject", "note": "no"}]}
    prompt = app_module._build_prompt(payload)
    assert prompt == [{"interruptResponse": {"interruptId": "int-1", "response": {"decision": "reject", "note": "no"}}}]
