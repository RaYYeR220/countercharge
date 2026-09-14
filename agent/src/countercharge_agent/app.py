"""BedrockAgentCoreApp entrypoint (C4): validates the caller's JWT, builds the case
agent for this request, and streams the C4 event sequence.

``BedrockAgentCoreApp`` wraps whatever this generator yields into ``data: <json>\\n\\n``
itself (``bedrock_agentcore.runtime.app._convert_to_sse`` / ``json.dumps``), so
``invoke`` yields each event's plain dict form -- never ``countercharge_core.events.
to_sse``'s pre-rendered SSE string, which would be double-encoded.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, AsyncIterator

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.session.session_manager import SessionManager

from countercharge_core.events import CaseStateEvent, DoneEvent, ErrorEvent, Event
from countercharge_core.settings import Settings
from countercharge_core.store import CaseStore

from countercharge_agent import auth, streaming
from countercharge_agent.gateway import GatewayCaller, build_gateway_client
from countercharge_agent.hooks import ApprovalHook, DenialCaptureHook, ToolAllowlistHook
from countercharge_agent.models import build_model
from countercharge_agent.prompts import AUDIT_MODE_INSTRUCTION, CASE_AGENT_SYSTEM_PROMPT, followup_mode_instruction
from countercharge_agent.sessions import build_session_manager
from countercharge_agent.subagents import make_charity_researcher, make_intake_extractor, make_letter_writer

app = BedrockAgentCoreApp()
log = app.logger

# Subagent (agent-as-tool) names, on top of C1's Gateway tools -- both are allowed
# through ToolAllowlistHook.
SUBAGENT_TOOL_NAMES = frozenset({"intake_extractor", "charity_researcher", "letter_writer"})

_JWKS_CACHE: dict[str, dict] = {}


def _settings() -> Settings:
    return Settings(
        table_name=os.environ.get("TABLE_NAME", "cc-cases"),
        bucket=os.environ.get("BUCKET", ""),
        kms_key_id=os.environ.get("KMS_KEY_ID", ""),
        local_hmac_secret=os.environ.get("LOCAL_HMAC_SECRET", ""),
        region=os.environ.get("REGION", "us-east-1"),
        hospitals_path=Path(os.environ.get("HOSPITALS_PATH", "data/hospitals.json")),
    )


def _case_store(settings: Settings) -> CaseStore:
    import boto3

    table = boto3.resource("dynamodb", region_name=settings.region).Table(settings.table_name)
    return CaseStore(table)


def _jwks(pool_id: str, region: str) -> dict:
    if pool_id not in _JWKS_CACHE:
        _JWKS_CACHE[pool_id] = auth.fetch_jwks(pool_id, region)
    return _JWKS_CACHE[pool_id]


def _bearer_token(headers: dict) -> str:
    value = headers.get("Authorization") or headers.get("authorization") or ""
    if not value.lower().startswith("bearer "):
        raise auth.AuthError("missing bearer token")
    return value[len("bearer ") :]


def _authenticate(headers: dict) -> auth.AuthClaims:
    token = _bearer_token(headers)
    pool_id = os.environ["COGNITO_POOL_ID"]
    region = os.environ.get("COGNITO_REGION", os.environ.get("REGION", "us-east-1"))
    allowed = os.environ.get("COGNITO_ALLOWED_CLIENT_IDS", "")
    allowed_ids = {value.strip() for value in allowed.split(",") if value.strip()} or None
    return auth.validate_access_token(
        token,
        jwks=_jwks(pool_id, region),
        issuer=auth.issuer_url(pool_id, region),
        allowed_client_ids=allowed_ids,
    )


def build_agent(
    *,
    case_id: str,
    store: CaseStore,
    gateway_tools: Any,
    gateway_caller: GatewayCaller,
    collector: streaming.EventCollector,
    session_manager: SessionManager | None = None,
    model: Any = None,
    bucket: str = "",
) -> Agent:
    """Build one case agent for a single invocation. Fully injectable for tests.

    ``gateway_tools`` is whatever strands accepts in its ``tools=[...]`` list for the
    Gateway -- a real ``MCPClient`` (a ``ToolProvider``, auto-discovered) in
    production, or a plain list of ``@tool``-decorated fakes in tests.
    ``gateway_caller`` is the object subagents use for direct tool calls (C1) outside
    of a model turn; a real ``MCPClient`` satisfies both roles at once.
    """
    intake_extractor = make_intake_extractor(gateway_caller=gateway_caller, bucket=bucket)
    charity_researcher = make_charity_researcher()
    letter_writer = make_letter_writer(gateway_caller=gateway_caller)

    tools = gateway_tools if isinstance(gateway_tools, list) else [gateway_tools]

    return Agent(
        model=model or build_model(role="reasoning"),
        system_prompt=CASE_AGENT_SYSTEM_PROMPT,
        tools=[*tools, intake_extractor, charity_researcher, letter_writer],
        hooks=[
            ToolAllowlistHook(extra_allowed=SUBAGENT_TOOL_NAMES),
            streaming.ToolEventHook(collector=collector),
            ApprovalHook(case_id=case_id, store=store),
            DenialCaptureHook(case_id=case_id, store=store, emit=collector.emit),
        ],
        session_manager=session_manager,
        callback_handler=None,
    )


def _build_prompt(payload: dict) -> Any:
    interrupt_responses = payload.get("interrupt_responses")
    if interrupt_responses:
        return [_interrupt_response_content(item) for item in interrupt_responses]

    mode = payload.get("mode", "chat")
    case_id = payload.get("case_id", "")
    if mode == "audit":
        return f"Case ID: {case_id}. {AUDIT_MODE_INSTRUCTION}"
    if mode == "followup":
        instruction = followup_mode_instruction(payload.get("days", 0), payload.get("reason", ""))
        return f"Case ID: {case_id}. {instruction}"
    return payload.get("message", "")


def _interrupt_response_content(item: dict) -> dict:
    if "decision" in item:
        response: dict[str, Any] = {"decision": item["decision"], "note": item.get("note", "")}
    else:
        response = {"approval_token": item.get("approval_token", "")}
    return {"interruptResponse": {"interruptId": item["interrupt_id"], "response": response}}


@app.entrypoint
async def invoke(payload: dict, context: Any) -> AsyncIterator[dict]:
    headers = getattr(context, "request_headers", None) or {}

    try:
        _authenticate(headers)
    except auth.AuthError as exc:
        yield _dump(ErrorEvent(message=str(exc)))
        return

    case_id = payload.get("case_id") or getattr(context, "session_id", "") or ""
    settings = _settings()
    store = _case_store(settings)
    collector = streaming.EventCollector()

    gateway_client = build_gateway_client(os.environ["GATEWAY_URL"], _bearer_token(headers))
    session_manager = build_session_manager(
        case_id, bucket=os.environ.get("SESSION_BUCKET", settings.bucket), prefix="sessions/"
    )

    try:
        agent = build_agent(
            case_id=case_id,
            store=store,
            gateway_tools=gateway_client,
            gateway_caller=gateway_client,
            collector=collector,
            session_manager=session_manager,
            bucket=settings.bucket,
        )
        prompt = _build_prompt(payload)

        async for event in streaming.translate(agent.stream_async(prompt), collector):
            if isinstance(event, DoneEvent):
                yield _dump(CaseStateEvent(case=store.get_case(case_id)))
            yield _dump(event)
    except Exception as exc:  # fail closed: a bare 500 is never acceptable mid-stream
        log.exception("case agent invocation failed")
        yield _dump(ErrorEvent(message=str(exc)))


def _dump(event: Event) -> dict:
    return event.model_dump(mode="json")


if __name__ == "__main__":
    app.run()
