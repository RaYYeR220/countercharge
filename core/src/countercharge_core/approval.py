"""Approval tokens (C2): short-lived, MAC-signed, bound to one tool call."""

import hashlib
import json
from dataclasses import dataclass

from countercharge_engine.canonical import canonical_json

from countercharge_core.signing import Signer, b64url_decode, b64url_encode


def input_hash(tool_input: dict) -> str:
    """sha256 hex of the canonical JSON of ``tool_input`` minus ``approval_token``."""
    payload = {k: v for k, v in tool_input.items() if k != "approval_token"}
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def issue_token(
    signer: Signer,
    *,
    action_id: str,
    case_id: str,
    tool: str,
    tool_input: dict,
    approver_sub: str,
    ttl_s: int = 900,
    now: int,
) -> str:
    payload = {
        "v": 1,
        "action_id": action_id,
        "case_id": case_id,
        "tool": tool,
        "input_hash": input_hash(tool_input),
        "approver_sub": approver_sub,
        "exp": now + ttl_s,
    }
    payload_bytes = canonical_json(payload)
    mac = signer.mac(payload_bytes)
    return f"{b64url_encode(payload_bytes)}.{b64url_encode(mac)}"


@dataclass
class TokenCheck:
    ok: bool
    reason: str
    payload: dict | None


def verify_token(signer: Signer, token: str, *, tool: str, tool_input: dict, now: int) -> TokenCheck:
    """Fail-closed verification: mac, exp, tool, action_id, case_id, input_hash."""
    try:
        payload_b64, mac_b64 = token.split(".", 1)
        payload_bytes = b64url_decode(payload_b64)
        mac = b64url_decode(mac_b64)
    except Exception:
        return TokenCheck(ok=False, reason="malformed", payload=None)

    if not signer.verify(payload_bytes, mac):
        return TokenCheck(ok=False, reason="bad_mac", payload=None)

    try:
        payload = json.loads(payload_bytes)
    except ValueError:
        return TokenCheck(ok=False, reason="malformed", payload=None)

    if not isinstance(payload, dict):
        return TokenCheck(ok=False, reason="malformed", payload=None)

    if payload.get("exp") is None or now > payload["exp"]:
        return TokenCheck(ok=False, reason="expired", payload=payload)

    if payload.get("tool") != tool:
        return TokenCheck(ok=False, reason="tool_mismatch", payload=payload)

    if payload.get("action_id") != tool_input.get("action_id"):
        return TokenCheck(ok=False, reason="action_mismatch", payload=payload)

    if payload.get("case_id") != tool_input.get("case_id"):
        return TokenCheck(ok=False, reason="case_mismatch", payload=payload)

    if payload.get("input_hash") != input_hash(tool_input):
        return TokenCheck(ok=False, reason="input_mismatch", payload=payload)

    return TokenCheck(ok=True, reason="", payload=payload)
