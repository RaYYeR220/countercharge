from countercharge_core.approval import input_hash, issue_token, verify_token
from countercharge_core.signing import LocalHmacSigner

NOW = 1_700_000_000

TOOL_INPUT = {
    "case_id": "case_1",
    "action_id": "action_1",
    "recipient_email": "billing@nyp.org",
    "finding_ids": ["f_1", "f_2"],
    "disputed_amount_cents": 5000,
}


def make_token(signer=None, **overrides):
    signer = signer or LocalHmacSigner(b"secret")
    tool_input = {**TOOL_INPUT, **overrides}
    token = issue_token(
        signer,
        action_id=tool_input["action_id"],
        case_id=tool_input["case_id"],
        tool="actions___send_dispute_letter",
        tool_input=tool_input,
        approver_sub="sub-1",
        now=NOW,
    )
    return signer, token, tool_input


def test_input_hash_ignores_approval_token_key():
    a = input_hash({**TOOL_INPUT, "approval_token": "whatever"})
    b = input_hash(TOOL_INPUT)
    assert a == b


def test_input_hash_is_order_independent():
    reordered = dict(reversed(list(TOOL_INPUT.items())))
    assert input_hash(TOOL_INPUT) == input_hash(reordered)


def test_input_hash_changes_with_amount():
    a = input_hash(TOOL_INPUT)
    b = input_hash({**TOOL_INPUT, "disputed_amount_cents": 6000})
    assert a != b


def test_issue_and_verify_token_ok():
    signer, token, tool_input = make_token()
    result = verify_token(
        signer, token, tool="actions___send_dispute_letter", tool_input=tool_input, now=NOW + 10
    )
    assert result.ok
    assert result.payload["action_id"] == "action_1"
    assert result.payload["case_id"] == "case_1"


def test_verify_token_expired():
    signer, token, tool_input = make_token()
    result = verify_token(
        signer,
        token,
        tool="actions___send_dispute_letter",
        tool_input=tool_input,
        now=NOW + 901,
    )
    assert not result.ok
    assert result.reason == "expired"


def test_verify_token_within_ttl_boundary_ok():
    signer, token, tool_input = make_token()
    result = verify_token(
        signer, token, tool="actions___send_dispute_letter", tool_input=tool_input, now=NOW + 900
    )
    assert result.ok


def test_verify_token_wrong_tool():
    signer, token, tool_input = make_token()
    result = verify_token(
        signer, token, tool="actions___submit_fap_application", tool_input=tool_input, now=NOW
    )
    assert not result.ok
    assert result.reason == "tool_mismatch"


def test_verify_token_tampered_amount_after_approval():
    signer, token, tool_input = make_token()
    tampered = {**tool_input, "disputed_amount_cents": 999999}
    result = verify_token(
        signer, token, tool="actions___send_dispute_letter", tool_input=tampered, now=NOW
    )
    assert not result.ok
    assert result.reason == "input_mismatch"


def test_verify_token_tampered_recipient_after_approval():
    signer, token, tool_input = make_token()
    tampered = {**tool_input, "recipient_email": "billing@evil.example"}
    result = verify_token(
        signer, token, tool="actions___send_dispute_letter", tool_input=tampered, now=NOW
    )
    assert not result.ok
    assert result.reason == "input_mismatch"


def test_verify_token_bad_mac_wrong_secret():
    signer, token, tool_input = make_token()
    other_signer = LocalHmacSigner(b"different-secret")
    result = verify_token(
        other_signer,
        token,
        tool="actions___send_dispute_letter",
        tool_input=tool_input,
        now=NOW,
    )
    assert not result.ok
    assert result.reason == "bad_mac"


def test_verify_token_malformed_token_string():
    signer = LocalHmacSigner(b"secret")
    result = verify_token(
        signer, "not-a-token", tool="actions___send_dispute_letter", tool_input=TOOL_INPUT, now=NOW
    )
    assert not result.ok
    assert result.reason == "malformed"


def test_verify_token_wrong_case_id_in_tool_input():
    signer, token, tool_input = make_token()
    tampered = {**tool_input, "case_id": "case_other"}
    result = verify_token(
        signer, token, tool="actions___send_dispute_letter", tool_input=tampered, now=NOW
    )
    assert not result.ok
    assert result.reason == "case_mismatch"


def test_verify_token_foreign_action_id():
    signer, token, tool_input = make_token()
    tampered = {**tool_input, "action_id": "action_other"}
    result = verify_token(
        signer, token, tool="actions___send_dispute_letter", tool_input=tampered, now=NOW
    )
    assert not result.ok
    assert result.reason == "action_mismatch"
