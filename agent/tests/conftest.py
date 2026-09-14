"""Shared test fixtures: a deterministic, network-free scripted model.

Mirrors the proven spike pattern (``internal/spikes/agentcore/ccspike/app/agent/
interrupt_test/scripted_model.py``), generalized to a list of scripted turns so the
same class can drive an audit-then-explain flow, an approval-then-send flow, etc.
"""

import json
from typing import Any, AsyncIterable

from strands.models.model import Model


def _count_tool_results(messages: Any) -> int:
    count = 0
    for message in messages or []:
        for block in message.get("content", []) or []:
            if isinstance(block, dict) and "toolResult" in block:
                count += 1
    return count


class ScriptedModel(Model):
    """No network calls. Emits one scripted step per model turn.

    Each step is ``{"tool": name, "input": dict}`` (emits a tool_use) or
    ``{"text": str}`` (emits final text with stop_reason ``end_turn``). Advances by
    counting tool results already present in the conversation, so it drives the same
    way whether the ``Agent`` is fresh or rehydrated from a session manager in a new
    process (see FINDINGS.md's interrupt spike).
    """

    def __init__(self, steps: list[dict], **config: Any):
        self._steps = steps
        self._config = dict(config)

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> Any:
        return self._config

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError("ScriptedModel does not support structured_output")
        yield {}  # pragma: no cover - keeps this an async generator

    async def stream(
        self,
        messages,
        tool_specs=None,
        system_prompt=None,
        **kwargs: Any,
    ) -> AsyncIterable[dict]:
        step_index = min(_count_tool_results(messages), len(self._steps) - 1)
        step = self._steps[step_index]

        yield {"messageStart": {"role": "assistant"}}
        if "tool" in step:
            tool_use_id = f"scripted-{step_index}"
            yield {"contentBlockStart": {"start": {"toolUse": {"name": step["tool"], "toolUseId": tool_use_id}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(step.get("input", {}))}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": step["text"]}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
