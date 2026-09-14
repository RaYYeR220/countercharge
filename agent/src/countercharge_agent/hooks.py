"""Hook providers enforcing C1's tool contract and C4's human-approval interrupt.

Interrupt mechanics follow the proven spike pattern (``internal/spikes/agentcore/
ccspike/app/agent/interrupt_test``): ``event.interrupt(name, reason=...)`` inside a
``BeforeToolCallEvent`` callback raises internally and is caught by strands, stopping
the agent loop with ``AgentResult.stop_reason == "interrupt"``; resuming re-delivers
the same call with ``event.interrupt(...)`` now returning the caller's response.
"""

from __future__ import annotations

from typing import Any, Callable

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

from countercharge_core.events import Event, PolicyDeniedEvent
from countercharge_core.store import CaseStore

from countercharge_agent.gateway import classify_denial, result_text

# C1: action tools that must pause for a human approval token before they run.
# schedule_followup is explicitly excluded (C1: "(no approval)").
ACTION_TOOLS_REQUIRING_APPROVAL = frozenset(
    {
        "actions___send_dispute_letter",
        "actions___submit_fap_application",
        "actions___request_itemized_bill",
        "actions___file_escalation",
    }
)

# C1: the complete, literal set of Gateway tool names the agent is ever allowed to call.
GATEWAY_TOOL_NAMES = frozenset(
    {
        "engine___audit_case",
        "engine___check_code_pair",
        "engine___check_units",
        "engine___fap_eligibility",
        "engine___hospital_price",
        "engine___explain_rule",
        "case___get_case",
        "case___save_extraction",
        "case___draft_action",
        "actions___send_dispute_letter",
        "actions___submit_fap_application",
        "actions___request_itemized_bill",
        "actions___file_escalation",
        "actions___schedule_followup",
    }
)

EmitFn = Callable[[Event], None]


class ApprovalHook(HookProvider):
    """Pauses every action tool that needs one for human approval (C4 ``interrupt`` event)."""

    def __init__(self, *, case_id: str, store: CaseStore):
        self._case_id = case_id
        self._store = store

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.approve)

    def approve(self, event: BeforeToolCallEvent) -> None:
        tool_name = event.tool_use["name"]
        if tool_name not in ACTION_TOOLS_REQUIRING_APPROVAL:
            return

        tool_input = dict(event.tool_use.get("input") or {})
        if tool_input.get("approval_token"):
            # Already carries a token (e.g. a resumed call) -- let the Gateway/Lambda verify it.
            return

        action_id = tool_input.get("action_id", "")
        action = self._store.get_action(self._case_id, action_id) if action_id else None
        preview_markdown = (action or {}).get("letter_markdown") or _default_preview(tool_name, tool_input)

        response = event.interrupt(
            "approval_required",
            reason={
                "action_id": action_id,
                "tool": tool_name,
                "tool_input": tool_input,
                "preview_markdown": preview_markdown,
            },
        )

        if isinstance(response, dict) and response.get("approval_token"):
            event.tool_use["input"]["approval_token"] = response["approval_token"]
            return

        note = response.get("note") if isinstance(response, dict) else None
        event.cancel_tool = note or "approval was not granted"


def _default_preview(tool_name: str, tool_input: dict) -> str:
    recipient = tool_input.get("recipient_email", "the hospital")
    return f"{tool_name} to {recipient}"


class ToolAllowlistHook(HookProvider):
    """Denies any tool call outside C1's Gateway tools plus the agent's own subagent tools.

    Belt-and-suspenders: the Gateway's own Cedar policies are the real enforcement point,
    but a model that hallucinates a tool name should never even reach the Gateway.
    """

    def __init__(self, *, extra_allowed: frozenset[str] = frozenset()):
        self._allowed = GATEWAY_TOOL_NAMES | extra_allowed

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.check)

    def check(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use["name"]
        if name not in self._allowed:
            event.cancel_tool = f"tool not allowed: {name}"


class DenialCaptureHook(HookProvider):
    """Detects a fail-closed denial -- hook, Gateway policy, or Lambda -- and records it.

    Every side-effecting path fails closed and records a Decision (Global Constraints):
    a hook-level cancellation surfaces on ``AfterToolCallEvent.cancel_message``; a Gateway
    policy or Lambda denial surfaces as an error-status tool result whose text
    :func:`countercharge_agent.gateway.classify_denial` recognizes.
    """

    def __init__(self, *, case_id: str, store: CaseStore, emit: EmitFn):
        self._case_id = case_id
        self._store = store
        self._emit = emit

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self.capture)

    def capture(self, event: AfterToolCallEvent) -> None:
        tool_name = event.tool_use["name"]

        if event.cancel_message is not None:
            self._record(layer="hook", tool_name=tool_name, reason=event.cancel_message)
            return

        classified = classify_denial(result_text(event.result))
        if classified is None:
            return
        layer, reason = classified
        self._record(layer=layer, tool_name=tool_name, reason=reason)

    def _record(self, *, layer: str, tool_name: str, reason: str) -> None:
        self._store.add_decision(self._case_id, layer=layer, tool=tool_name, outcome="deny", reason=reason)
        self._emit(PolicyDeniedEvent(tool=tool_name, layer=layer, reason=reason))
