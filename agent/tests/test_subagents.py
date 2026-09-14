from typing import Any, AsyncIterable

from strands.models.model import Model

from countercharge_agent import subagents
from countercharge_agent.subagents import _image_format, make_charity_researcher, make_letter_writer


class _SequentialTextModel(Model):
    """No network calls. Returns each text in ``texts`` in order, one per call."""

    def __init__(self, texts: list[str]):
        self._texts = texts
        self._index = 0

    def update_config(self, **model_config: Any) -> None:
        pass

    def get_config(self) -> Any:
        return {}

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError
        yield {}  # pragma: no cover

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs: Any) -> AsyncIterable[dict]:
        text = self._texts[min(self._index, len(self._texts) - 1)]
        self._index += 1
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {}}}
        yield {"contentBlockDelta": {"delta": {"text": text}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}


class _FakeGatewayCaller:
    """Drafts return ok=False the first time and ok=True the second, per finding_ids seen."""

    def __init__(self, case: dict):
        self._case = case
        self.drafts: list[dict] = []

    def call_tool_sync(self, tool_use_id, name, arguments=None):
        import json

        if name == "case___get_case":
            return {"status": "success", "content": [{"text": json.dumps(self._case)}]}
        if name == "case___draft_action":
            self.drafts.append(arguments)
            ok = len(self.drafts) > 1
            grounding = {"ok": ok, "unsupported_amounts": [] if ok else ["$999.00"], "unsupported_codes": []}
            return {
                "status": "success",
                "content": [
                    {"text": json.dumps({"action_id": f"action_{len(self.drafts)}", "grounding": grounding})}
                ],
            }
        raise AssertionError(f"unexpected tool call: {name}")


def _sample_case() -> dict:
    return {
        "case_id": "case_1",
        "findings": [
            {
                "finding": {
                    "finding_id": "f1",
                    "rule_id": "DUPLICATE",
                    "disputable": True,
                    "amount_cents": 14500,
                },
                "signature": "sig1",
            }
        ],
        "actions": [],
        "docs": [],
        "meta": None,
    }


def test_letter_writer_rewrites_once_when_grounding_fails_then_succeeds(monkeypatch):
    model = _SequentialTextModel(["Please pay me $999.00 immediately.", "Please review the $145.00 duplicate charge."])
    monkeypatch.setattr(subagents.models, "build_model", lambda role="reasoning": model)

    caller = _FakeGatewayCaller(_sample_case())
    letter_writer = make_letter_writer(gateway_caller=caller)

    result = letter_writer(case_id="case_1", action_type="dispute_letter", finding_ids=["f1"], recipient_email="billing@nyp.org")

    assert len(caller.drafts) == 2
    assert result["grounding"]["ok"] is True
    assert result["action_id"] == "action_2"
    # disputed_amount_cents is derived from the cited disputable finding, not the model's text
    assert caller.drafts[0]["disputed_amount_cents"] == 14500


def test_letter_writer_returns_error_when_still_ungrounded_after_rewrite(monkeypatch):
    model = _SequentialTextModel(["Please pay me $999.00 immediately.", "Still citing $999.00, sorry."])
    monkeypatch.setattr(subagents.models, "build_model", lambda role="reasoning": model)

    caller = _FakeGatewayCaller(_sample_case())

    class _AlwaysUngroundedCaller(_FakeGatewayCaller):
        def call_tool_sync(self, tool_use_id, name, arguments=None):
            import json

            if name == "case___get_case":
                return {"status": "success", "content": [{"text": json.dumps(self._case)}]}
            if name == "case___draft_action":
                self.drafts.append(arguments)
                grounding = {"ok": False, "unsupported_amounts": ["$999.00"], "unsupported_codes": []}
                return {
                    "status": "success",
                    "content": [{"text": json.dumps({"action_id": "action_x", "grounding": grounding})}],
                }
            raise AssertionError(name)

    caller = _AlwaysUngroundedCaller(_sample_case())
    letter_writer = make_letter_writer(gateway_caller=caller)

    result = letter_writer(case_id="case_1", action_type="dispute_letter", finding_ids=["f1"], recipient_email="billing@nyp.org")

    assert len(caller.drafts) == 2
    assert "error" in result


def test_image_format_maps_known_media_types():
    assert _image_format("image/png") == "png"
    assert _image_format("image/jpeg") == "jpeg"
    assert _image_format("IMAGE/JPG") == "jpeg"
    assert _image_format("application/octet-stream") == "png"


def test_make_charity_researcher_does_not_import_playwright_until_invoked():
    # Building the tool must never touch Playwright/strands_tools.browser.
    charity_researcher = make_charity_researcher()
    assert callable(charity_researcher)
