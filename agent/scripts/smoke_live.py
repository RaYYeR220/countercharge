"""Live smoke test against Venice (real model, fake Gateway tools): audit mode on a
sample bill -> agent calls engine___audit_case then drafts -> interrupt appears ->
resume with a fake approval token -> action tool called with the injected token.

Loads VENICE_API_KEY from internal/secrets.env at runtime; never prints or commits it.
Usage: uv run --project agent python agent/scripts/smoke_live.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "agent" / "src"))

SECRETS_PATH = REPO_ROOT.parent / "internal" / "secrets.env"


def _load_secrets(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_secrets(SECRETS_PATH)
os.environ.setdefault("MODEL_PROVIDER", "venice")

from strands import tool  # noqa: E402
from strands.session.file_session_manager import FileSessionManager  # noqa: E402

from countercharge_core.events import to_sse  # noqa: E402

from countercharge_agent import streaming  # noqa: E402
from countercharge_agent.app import _build_prompt, build_agent  # noqa: E402

SAMPLE_BILL_PATH = REPO_ROOT / "evals" / "corpus" / "cases" / "duplicate_01.json"


def _load_sample_case() -> dict:
    return json.loads(SAMPLE_BILL_PATH.read_text(encoding="utf-8"))


def _build_finding(case: dict) -> dict:
    bill = case["bill"]
    lines = {line["line_id"]: line for line in bill["lines"]}
    amount = lines["L3"]["charge_cents"]
    return {
        "finding_id": "f1",
        "rule_id": "DUPLICATE",
        "disputable": True,
        "line_ids": ["L2", "L3"],
        "amount_cents": amount,
        "title": "Duplicate lab charge",
        "detail": "Line L3 duplicates the complete blood count already billed on L2.",
        "citation": {"dataset": "engine", "version": "smoke-1", "url": "https://example.org/duplicate", "record": {}},
        "evidence": {"target_key": "L3"},
    }


class FakeStore:
    def __init__(self):
        self.actions: dict[str, dict] = {}
        self.decisions: list[dict] = []

    def get_action(self, case_id, action_id):
        return self.actions.get(action_id)

    def add_decision(self, case_id, layer, tool, outcome, reason, action_id=None):
        self.decisions.append({"layer": layer, "tool": tool, "outcome": outcome, "reason": reason})


class UnusedCaller:
    def call_tool_sync(self, tool_use_id, name, arguments=None):
        raise AssertionError(f"unexpected direct gateway call in smoke test: {name}")


def make_fake_gateway_tools(case: dict, store: FakeStore):
    finding = _build_finding(case)
    calls: list[dict] = []

    @tool
    def engine___audit_case(case_id: str) -> dict:
        calls.append({"tool": "engine___audit_case", "input": {"case_id": case_id}})
        return {
            "report": {
                "findings": [finding],
                "advisory": [],
                "disputable_cents": finding["amount_cents"],
                "fap": None,
                "refdata_version": "smoke-1",
            },
            "signed": [{"finding_id": "f1", "signature": "sig1"}],
        }

    @tool
    def case___draft_action(
        case_id: str,
        type: str,
        recipient_email: str,
        finding_ids: list,
        letter_markdown: str,
        disputed_amount_cents: int,
    ) -> dict:
        action_id = "action_1"
        store.actions[action_id] = {"letter_markdown": letter_markdown}
        calls.append({"tool": "case___draft_action", "input": {"finding_ids": finding_ids}})
        return {
            "action_id": action_id,
            "tool": "actions___send_dispute_letter",
            "tool_input": {
                "case_id": case_id,
                "action_id": action_id,
                "recipient_email": recipient_email,
                "finding_ids": finding_ids,
                "disputed_amount_cents": disputed_amount_cents,
            },
            "input_hash": "smoke-hash",
            "grounding": {"ok": True, "unsupported_amounts": [], "unsupported_codes": []},
        }

    @tool
    def actions___send_dispute_letter(
        case_id: str,
        action_id: str,
        recipient_email: str,
        finding_ids: list,
        disputed_amount_cents: int,
        approval_token: str = "",
    ) -> dict:
        calls.append({"tool": "actions___send_dispute_letter", "input": {"approval_token": approval_token}})
        if not approval_token:
            return {"status": "error", "content": [{"text": "DENIED:lambda:missing approval token"}]}
        return {
            "status": "success",
            "content": [{"text": json.dumps({"status": "sent", "message_id": "msg_smoke_1", "action_id": action_id})}],
        }

    return [engine___audit_case, case___draft_action, actions___send_dispute_letter], calls


async def main() -> None:
    if not os.environ.get("VENICE_API_KEY"):
        print("VENICE_API_KEY not set (checked internal/secrets.env) -- aborting live smoke", file=sys.stderr)
        sys.exit(1)

    case = _load_sample_case()
    case_id = case["case_id"]
    store = FakeStore()
    tools, calls = make_fake_gateway_tools(case, store)

    transcript: list[dict] = []

    def record(event) -> None:
        transcript.append(json.loads(event.model_dump_json()))
        line = to_sse(event).encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        sys.stderr.buffer.write(line.encode("utf-8", errors="replace"))

    with tempfile.TemporaryDirectory() as session_dir:
        # --- turn 1: audit mode, ending at the approval interrupt ---
        collector1 = streaming.EventCollector()
        agent1 = build_agent(
            case_id=case_id,
            store=store,
            gateway_tools=tools,
            gateway_caller=UnusedCaller(),
            collector=collector1,
            session_manager=FileSessionManager(session_id=case_id, storage_dir=session_dir),
        )

        prompt = _build_prompt({"case_id": case_id, "mode": "audit"}) + (
            " The patient has already decided: draft and send a dispute letter for every "
            "disputable finding right now, addressed to billing@nyp.org. Do not ask "
            "further questions -- proceed directly to case___draft_action and then the "
            "matching actions___* tool."
        )
        interrupt_id = None
        async for event in streaming.translate(agent1.stream_async(prompt), collector1):
            record(event)
            if event.type == "interrupt":
                interrupt_id = event.interrupt_id

        if interrupt_id is None:
            print("no interrupt was raised -- the model may not have reached the send step", file=sys.stderr)
        else:
            # --- turn 2: brand new Agent, same on-disk session -> resume with a fake approval token ---
            tools2, calls2 = make_fake_gateway_tools(case, store)
            collector2 = streaming.EventCollector()
            agent2 = build_agent(
                case_id=case_id,
                store=store,
                gateway_tools=tools2,
                gateway_caller=UnusedCaller(),
                collector=collector2,
                session_manager=FileSessionManager(session_id=case_id, storage_dir=session_dir),
            )
            responses = [
                {
                    "interruptResponse": {
                        "interruptId": interrupt_id,
                        "response": {"approval_token": "smoke-fake-token"},
                    }
                }
            ]
            async for event in streaming.translate(agent2.stream_async(responses), collector2):
                record(event)
            calls.extend(calls2)

    out_path = REPO_ROOT.parent / "internal" / "deploy" / "agent-transcript.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(transcript, indent=2), encoding="utf-8")

    print(f"\n\n--- tool calls ---\n{json.dumps(calls, indent=2)}", file=sys.stderr)
    print(f"--- transcript written to {out_path} ({len(transcript)} events) ---", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
