from types import SimpleNamespace

from countercharge_agent.hooks import ApprovalHook, DenialCaptureHook, ToolAllowlistHook


class _FakeStore:
    def __init__(self, action=None):
        self._action = action
        self.decisions = []

    def get_action(self, case_id, action_id):
        return self._action

    def add_decision(self, case_id, layer, tool, outcome, reason, action_id=None):
        self.decisions.append(
            {"case_id": case_id, "layer": layer, "tool": tool, "outcome": outcome, "reason": reason}
        )


class _FakeInterruptEvent:
    """Stands in for BeforeToolCallEvent -- exercises only what ApprovalHook touches."""

    def __init__(self, tool_name, tool_input, response):
        self.tool_use = {"name": tool_name, "input": dict(tool_input)}
        self._response = response
        self.cancel_tool = False
        self.reason = None

    def interrupt(self, name, reason=None):
        self.reason = reason
        return self._response


def test_approval_hook_ignores_tools_that_do_not_need_approval():
    hook = ApprovalHook(case_id="case_1", store=_FakeStore())
    event = _FakeInterruptEvent("engine___audit_case", {}, response=None)

    hook.approve(event)

    assert event.reason is None
    assert event.cancel_tool is False


def test_approval_hook_skips_when_token_already_present():
    hook = ApprovalHook(case_id="case_1", store=_FakeStore())
    event = _FakeInterruptEvent(
        "actions___send_dispute_letter", {"approval_token": "already-there"}, response=None
    )

    hook.approve(event)

    assert event.reason is None


def test_approval_hook_injects_token_on_approval():
    store = _FakeStore(action={"letter_markdown": "Dear Hospital, ..."})
    hook = ApprovalHook(case_id="case_1", store=store)
    event = _FakeInterruptEvent(
        "actions___send_dispute_letter",
        {"action_id": "action_1", "case_id": "case_1", "recipient_email": "billing@nyp.org"},
        response={"approval_token": "tok-123"},
    )

    hook.approve(event)

    assert event.tool_use["input"]["approval_token"] == "tok-123"
    assert event.cancel_tool is False
    assert event.reason["preview_markdown"] == "Dear Hospital, ..."
    assert event.reason["action_id"] == "action_1"
    assert event.reason["tool"] == "actions___send_dispute_letter"


def test_approval_hook_cancels_on_rejection_with_note():
    hook = ApprovalHook(case_id="case_1", store=_FakeStore())
    event = _FakeInterruptEvent(
        "actions___file_escalation",
        {"action_id": "action_2"},
        response={"decision": "reject", "note": "amount looks wrong"},
    )

    hook.approve(event)

    assert event.cancel_tool == "amount looks wrong"
    assert "approval_token" not in event.tool_use["input"]


def test_approval_hook_cancels_on_rejection_without_note():
    hook = ApprovalHook(case_id="case_1", store=_FakeStore())
    event = _FakeInterruptEvent("actions___submit_fap_application", {}, response={"decision": "reject"})

    hook.approve(event)

    assert event.cancel_tool == "approval was not granted"


def test_tool_allowlist_hook_allows_gateway_tools():
    hook = ToolAllowlistHook()
    event = SimpleNamespace(tool_use={"name": "engine___audit_case"}, cancel_tool=False)

    hook.check(event)

    assert event.cancel_tool is False


def test_tool_allowlist_hook_allows_extra_subagent_tools():
    hook = ToolAllowlistHook(extra_allowed=frozenset({"intake_extractor"}))
    event = SimpleNamespace(tool_use={"name": "intake_extractor"}, cancel_tool=False)

    hook.check(event)

    assert event.cancel_tool is False


def test_tool_allowlist_hook_denies_unknown_tool():
    hook = ToolAllowlistHook()
    event = SimpleNamespace(tool_use={"name": "delete_everything"}, cancel_tool=False)

    hook.check(event)

    assert event.cancel_tool == "tool not allowed: delete_everything"


def test_denial_capture_hook_records_hook_layer_cancellation():
    store = _FakeStore()
    emitted = []
    hook = DenialCaptureHook(case_id="case_1", store=store, emit=emitted.append)
    event = SimpleNamespace(
        tool_use={"name": "actions___send_dispute_letter"},
        cancel_message="approval was not granted",
        result={"status": "error", "content": [{"text": "approval was not granted"}]},
    )

    hook.capture(event)

    assert store.decisions == [
        {
            "case_id": "case_1",
            "layer": "hook",
            "tool": "actions___send_dispute_letter",
            "outcome": "deny",
            "reason": "approval was not granted",
        }
    ]
    assert emitted[0].layer == "hook"
    assert emitted[0].tool == "actions___send_dispute_letter"


def test_denial_capture_hook_records_policy_denial():
    store = _FakeStore()
    emitted = []
    hook = DenialCaptureHook(case_id="case_1", store=store, emit=emitted.append)
    event = SimpleNamespace(
        tool_use={"name": "actions___send_dispute_letter"},
        cancel_message=None,
        result={"status": "error", "content": [{"text": "Tool execution failed: Tool Execution Denied: nope"}]},
    )

    hook.capture(event)

    assert store.decisions[0]["layer"] == "policy"
    assert emitted[0].layer == "policy"


def test_denial_capture_hook_ignores_successful_calls():
    store = _FakeStore()
    emitted = []
    hook = DenialCaptureHook(case_id="case_1", store=store, emit=emitted.append)
    event = SimpleNamespace(
        tool_use={"name": "engine___audit_case"},
        cancel_message=None,
        result={"status": "success", "content": [{"text": "{}"}]},
    )

    hook.capture(event)

    assert store.decisions == []
    assert emitted == []
