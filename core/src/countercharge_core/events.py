"""Pydantic models for the C4 SSE event stream, plus SSE-line rendering."""

from typing import Literal, Union

from pydantic import BaseModel, Field


class TextEvent(BaseModel):
    type: Literal["text"] = "text"
    delta: str


class ToolStartEvent(BaseModel):
    type: Literal["tool_start"] = "tool_start"
    tool: str
    tool_use_id: str
    input: dict = Field(default_factory=dict)


class ToolEndEvent(BaseModel):
    type: Literal["tool_end"] = "tool_end"
    tool: str
    tool_use_id: str
    status: Literal["success", "error", "denied"]
    output: object = None


class PolicyDeniedEvent(BaseModel):
    type: Literal["policy_denied"] = "policy_denied"
    tool: str
    layer: Literal["hook", "policy", "lambda"]
    reason: str


class FindingEvent(BaseModel):
    type: Literal["finding"] = "finding"
    finding: dict


class InterruptAction(BaseModel):
    action_id: str
    tool: str
    tool_input: dict
    preview_markdown: str


class InterruptEvent(BaseModel):
    type: Literal["interrupt"] = "interrupt"
    interrupt_id: str
    name: Literal["approval_required"] = "approval_required"
    action: InterruptAction


class CaseStateEvent(BaseModel):
    type: Literal["case_state"] = "case_state"
    case: dict


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    stop_reason: str


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


Event = Union[
    TextEvent,
    ToolStartEvent,
    ToolEndEvent,
    PolicyDeniedEvent,
    FindingEvent,
    InterruptEvent,
    CaseStateEvent,
    DoneEvent,
    ErrorEvent,
]


def to_sse(event: BaseModel) -> str:
    """Render one C4 event model as an SSE ``data: <json>`` line (with terminator)."""
    return f"data: {event.model_dump_json()}\n\n"
