"""Translates a strands `Agent.stream_async()` stream into the C4 SSE event sequence.

Two things the generated AgentCore templates get wrong for our purposes (see
``internal/spikes/agentcore/FINDINGS.md``, "Strands 1.55.1 interrupts"):

- The terminal item from ``stream_async()`` is ``{"result": AgentResult}``, which a
  naive ``"event" in item`` filter silently drops -- callers would never learn the
  ``interrupt_id`` to resume with. :func:`translate` detects it explicitly and emits
  a C4 ``interrupt`` event per raised interrupt.
- Tool start/end and finding events aren't part of the raw model stream at all; they
  come from :class:`ToolEventHook`, which pushes onto a shared, same-thread
  :class:`EventCollector` that :func:`translate` drains between upstream events.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

from countercharge_core.events import (
    DoneEvent,
    Event,
    FindingEvent,
    InterruptAction,
    InterruptEvent,
    TextEvent,
    ToolEndEvent,
    ToolStartEvent,
)

from countercharge_agent.gateway import classify_denial, result_text

AUDIT_TOOL_NAME = "engine___audit_case"


class EventCollector:
    """Same-thread sink hooks push onto; drained by :func:`translate` between model chunks.

    Hook callbacks run synchronously on the same asyncio task that drives
    ``Agent.stream_async()`` (strands awaits each hook in turn), so a plain list is
    enough -- no cross-thread synchronization is needed.
    """

    def __init__(self) -> None:
        self._buffer: list[Event] = []

    def emit(self, event: Event) -> None:
        self._buffer.append(event)

    def drain(self) -> list[Event]:
        buffered, self._buffer = self._buffer, []
        return buffered


class ToolEventHook(HookProvider):
    """Emits C4 ``tool_start``/``tool_end`` for every tool call, and ``finding`` after an audit."""

    def __init__(self, *, collector: EventCollector):
        self._collector = collector

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before)
        registry.add_callback(AfterToolCallEvent, self._after)

    def _before(self, event: BeforeToolCallEvent) -> None:
        tool_use = event.tool_use
        self._collector.emit(
            ToolStartEvent(
                tool=tool_use["name"],
                tool_use_id=tool_use["toolUseId"],
                input=tool_use.get("input") or {},
            )
        )

    def _after(self, event: AfterToolCallEvent) -> None:
        tool_use = event.tool_use
        tool_name = tool_use["name"]
        tool_use_id = tool_use["toolUseId"]

        if event.cancel_message is not None or classify_denial(result_text(event.result)) is not None:
            self._collector.emit(
                ToolEndEvent(tool=tool_name, tool_use_id=tool_use_id, status="denied", output=result_text(event.result))
            )
            return

        status = "success" if isinstance(event.result, dict) and event.result.get("status") == "success" else "error"
        output = parse_tool_output(event.result)
        self._collector.emit(ToolEndEvent(tool=tool_name, tool_use_id=tool_use_id, status=status, output=output))

        if tool_name == AUDIT_TOOL_NAME and status == "success":
            for finding in _extract_findings(output):
                self._collector.emit(FindingEvent(finding=finding))


def parse_tool_output(result: Any) -> Any:
    """Best-effort JSON parse of a tool result's text content, else the raw text (or ``None``)."""
    text = result_text(result)
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def _extract_findings(output: Any) -> list[dict]:
    if not isinstance(output, dict):
        return []
    report = output.get("report")
    if not isinstance(report, dict):
        return []
    findings = report.get("findings")
    return findings if isinstance(findings, list) else []


def interrupt_to_event(interrupt: Any) -> InterruptEvent:
    """Convert a raised ``strands.interrupt.Interrupt`` into a C4 ``interrupt`` event.

    ``interrupt.reason`` is exactly the dict :class:`~countercharge_agent.hooks.ApprovalHook`
    passed to ``event.interrupt(...)``: ``{action_id, tool, tool_input, preview_markdown}``.
    """
    reason = interrupt.reason or {}
    return InterruptEvent(
        interrupt_id=interrupt.id,
        action=InterruptAction(
            action_id=reason.get("action_id", ""),
            tool=reason.get("tool", ""),
            tool_input=reason.get("tool_input", {}),
            preview_markdown=reason.get("preview_markdown", ""),
        ),
    )


async def translate(agent_stream: AsyncIterator[Any], collector: EventCollector) -> AsyncIterator[Event]:
    """Consume ``agent.stream_async(...)`` and yield the C4 event sequence.

    Text deltas come from the raw model stream chunks (``{"event": {...}}``); tool and
    finding events come from ``collector``, drained right before each upstream item is
    handled so their relative ordering is preserved; the terminal ``{"result":
    AgentResult}`` item becomes zero or more ``interrupt`` events followed by ``done``.
    """
    async for raw in agent_stream:
        for buffered in collector.drain():
            yield buffered

        if isinstance(raw, dict) and "result" in raw:
            result = raw["result"]
            stop_reason = getattr(result, "stop_reason", None)
            interrupts = getattr(result, "interrupts", None)
            if stop_reason == "interrupt" and interrupts:
                for interrupt in interrupts:
                    yield interrupt_to_event(interrupt)
            yield DoneEvent(stop_reason=stop_reason or "end_turn")
            continue

        if not isinstance(raw, dict) or "event" not in raw:
            continue

        chunk = raw["event"]
        content_block_start = chunk.get("contentBlockStart")
        if content_block_start is not None and not content_block_start.get("start"):
            continue

        text = chunk.get("contentBlockDelta", {}).get("delta", {}).get("text")
        if text:
            yield TextEvent(delta=text)

    for buffered in collector.drain():
        yield buffered
