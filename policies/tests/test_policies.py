"""Tests for Cedar policy rendering and the AgentCore Policy engine allow/deny matrix.

cedarpy 4.x (Rust-backed cedar-policy bindings) supports entity tags directly:
entity dicts accept a `tags` mapping and policies can call `principal.hasTag(...)`
/ `principal.getTag(...)` against it (verified against the installed version
during implementation). So requests below use real entity tags end-to-end --
there is no need for a fallback plain-attribute mapping.
"""

from __future__ import annotations

import json
from pathlib import Path

import cedarpy
import pytest

from countercharge_policies.render import DEFAULT_DEMO_BILLING_EMAIL, main, render

GATEWAY_ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/cc-gateway-test"

HOSPITALS = [
    # no demo_billing_email -> must default to DEFAULT_DEMO_BILLING_EMAIL
    {"hospital_id": "nyp", "name": "NewYork-Presbyterian", "billing_email_domain": "nyp.org"},
    {
        "hospital_id": "ccf",
        "name": "Cleveland Clinic",
        "billing_email_domain": "clevelandclinic.org",
        "demo_billing_email": DEFAULT_DEMO_BILLING_EMAIL,
    },
]

ENGINE_ACTIONS = [
    "engine___audit_case",
    "engine___check_code_pair",
    "engine___check_units",
    "engine___fap_eligibility",
    "engine___hospital_price",
    "engine___explain_rule",
]
CASE_ACTIONS = ["case___get_case", "case___save_extraction", "case___draft_action"]
ACTION_ACTIONS = [
    "actions___send_dispute_letter",
    "actions___submit_fap_application",
    "actions___request_itemized_bill",
    "actions___file_escalation",
    "actions___schedule_followup",
]


@pytest.fixture(scope="module")
def policies() -> dict[str, str]:
    return render(GATEWAY_ARN, HOSPITALS)


@pytest.fixture(scope="module")
def policy_set(policies: dict[str, str]) -> cedarpy.PolicySet:
    combined = "\n".join(policies.values())
    return cedarpy.PolicySet.from_str(combined)


def _entities(role: str | None, username: str = "u1") -> list[dict]:
    tags = {"username": username}
    if role is not None:
        tags["role"] = role
    return [
        {
            "uid": {"type": "AgentCore::Principal", "id": username},
            "attrs": {},
            "parents": [],
            "tags": tags,
        }
    ]


def authorize(
    policy_set: cedarpy.PolicySet,
    *,
    role: str | None,
    action: str,
    input: dict,
    username: str = "u1",
) -> cedarpy.Decision:
    request = {
        "principal": {"type": "AgentCore::Principal", "id": username},
        "action": {"type": "AgentCore::Action", "id": action},
        "resource": {"type": "AgentCore::Gateway", "id": GATEWAY_ARN},
        "context": {"input": input},
    }
    result = cedarpy.is_authorized(request, policy_set, _entities(role, username))
    return result.decision


ALLOW = cedarpy.Decision.Allow
DENY = cedarpy.Decision.Deny


# ---------------------------------------------------------------------------
# Rendering / syntax validation
# ---------------------------------------------------------------------------


def test_renders_one_policy_per_expected_name(policies: dict[str, str]) -> None:
    assert set(policies) == {
        "read_engine",
        "case_tools",
        "send_dispute_letter",
        "fap_and_itemized",
        "file_escalation",
        "schedule_followup",
        "forbid_integrator_writes",
    }


def test_every_rendered_policy_parses_individually(policies: dict[str, str]) -> None:
    for name, statement in policies.items():
        # Raises ValueError on a parse failure.
        cedarpy.PolicySet.from_str(statement)
        assert f'AgentCore::Gateway::"{GATEWAY_ARN}"' in statement


def test_isEmpty_checks_are_always_negated(policies: dict[str, str]) -> None:
    # finding_ids non-empty is expressed as `!....isEmpty()`, never an
    # empty-set-literal comparison like `finding_ids == []`.
    for statement in policies.values():
        if "isEmpty()" in statement:
            assert "!context.input.finding_ids.isEmpty()" in statement


def test_no_empty_set_literals(policies: dict[str, str]) -> None:
    for statement in policies.values():
        assert "[]" not in statement


def test_gateway_arn_is_interpolated_not_wildcarded(policies: dict[str, str]) -> None:
    for statement in policies.values():
        assert "==" in statement or "action in" in statement
        assert GATEWAY_ARN in statement


def test_default_demo_billing_email_used_when_hospital_field_absent(policies: dict[str, str]) -> None:
    # HOSPITALS[0] (nyp) has no demo_billing_email key.
    assert DEFAULT_DEMO_BILLING_EMAIL in policies["send_dispute_letter"]
    assert DEFAULT_DEMO_BILLING_EMAIL in policies["fap_and_itemized"]


def test_render_against_real_hospitals_json_is_defensive() -> None:
    # The real registry (as of writing) has no demo_billing_email field on any
    # hospital yet -- render() must still produce valid, deploy-ready policies.
    real_path = (
        Path(__file__).resolve().parents[2] / "engine" / "data" / "hospitals.json"
    )
    hospitals = json.loads(real_path.read_text(encoding="utf-8"))
    rendered = render(GATEWAY_ARN, hospitals)
    for statement in rendered.values():
        cedarpy.PolicySet.from_str(statement)
    assert DEFAULT_DEMO_BILLING_EMAIL in rendered["send_dispute_letter"]


# ---------------------------------------------------------------------------
# read_engine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["patient", "advocate", "system", "integrator"])
@pytest.mark.parametrize("action", ENGINE_ACTIONS)
def test_read_engine_allows_any_tagged_role(policy_set, role, action) -> None:
    assert authorize(policy_set, role=role, action=action, input={}) == ALLOW


def test_read_engine_denies_principal_without_role_tag(policy_set) -> None:
    assert authorize(policy_set, role=None, action="engine___audit_case", input={}) == DENY


# ---------------------------------------------------------------------------
# case_tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["patient", "advocate", "system"])
@pytest.mark.parametrize("action", CASE_ACTIONS)
def test_case_tools_allow_patient_advocate_system(policy_set, role, action) -> None:
    assert authorize(policy_set, role=role, action=action, input={}) == ALLOW


@pytest.mark.parametrize("action", CASE_ACTIONS)
def test_case_tools_deny_integrator(policy_set, action) -> None:
    assert authorize(policy_set, role="integrator", action=action, input={}) == DENY


# ---------------------------------------------------------------------------
# send_dispute_letter
# ---------------------------------------------------------------------------


def _dispute_input(**overrides) -> dict:
    base = dict(
        approval_token="tok-123",
        finding_ids=["f1"],
        recipient_email="billing@nyp.org",
        disputed_amount_cents=500_00,
    )
    base.update(overrides)
    return base


@pytest.mark.parametrize("role", ["patient", "advocate", "system"])
def test_send_dispute_letter_allows_when_all_conditions_met(policy_set, role) -> None:
    decision = authorize(
        policy_set, role=role, action="actions___send_dispute_letter", input=_dispute_input()
    )
    assert decision == ALLOW


def test_send_dispute_letter_denies_missing_approval_token(policy_set) -> None:
    decision = authorize(
        policy_set,
        role="patient",
        action="actions___send_dispute_letter",
        input=_dispute_input(approval_token=""),
    )
    assert decision == DENY


def test_send_dispute_letter_denies_empty_finding_ids(policy_set) -> None:
    decision = authorize(
        policy_set,
        role="patient",
        action="actions___send_dispute_letter",
        input=_dispute_input(finding_ids=[]),
    )
    assert decision == DENY


def test_send_dispute_letter_denies_attacker_recipient(policy_set) -> None:
    decision = authorize(
        policy_set,
        role="patient",
        action="actions___send_dispute_letter",
        input=_dispute_input(recipient_email="billing@evil.example"),
    )
    assert decision == DENY


def test_send_dispute_letter_denies_demo_billing_email_for_unlisted_hospital(policy_set) -> None:
    # Exact demo address for a *listed* hospital is fine (covered by allow
    # test); an address that merely resembles it should not sneak through.
    decision = authorize(
        policy_set,
        role="patient",
        action="actions___send_dispute_letter",
        input=_dispute_input(recipient_email="countercharge.demo+billing@evil.example"),
    )
    assert decision == DENY


def test_send_dispute_letter_allows_exact_demo_billing_email(policy_set) -> None:
    decision = authorize(
        policy_set,
        role="patient",
        action="actions___send_dispute_letter",
        input=_dispute_input(recipient_email=DEFAULT_DEMO_BILLING_EMAIL),
    )
    assert decision == ALLOW


def test_send_dispute_letter_over_limit_denied_for_patient_allowed_for_advocate(policy_set) -> None:
    over_limit = _dispute_input(disputed_amount_cents=1_000_001)
    assert (
        authorize(policy_set, role="patient", action="actions___send_dispute_letter", input=over_limit)
        == DENY
    )
    assert (
        authorize(policy_set, role="advocate", action="actions___send_dispute_letter", input=over_limit)
        == ALLOW
    )


def test_send_dispute_letter_at_limit_allowed_for_patient(policy_set) -> None:
    at_limit = _dispute_input(disputed_amount_cents=1_000_000)
    assert (
        authorize(policy_set, role="patient", action="actions___send_dispute_letter", input=at_limit)
        == ALLOW
    )


def test_send_dispute_letter_denies_integrator(policy_set) -> None:
    decision = authorize(
        policy_set, role="integrator", action="actions___send_dispute_letter", input=_dispute_input()
    )
    assert decision == DENY


# ---------------------------------------------------------------------------
# fap_and_itemized (submit_fap_application, request_itemized_bill)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["actions___submit_fap_application", "actions___request_itemized_bill"])
@pytest.mark.parametrize("role", ["patient", "advocate", "system"])
def test_fap_and_itemized_allow_with_token_and_allowed_recipient(policy_set, role, action) -> None:
    decision = authorize(
        policy_set,
        role=role,
        action=action,
        input={"approval_token": "tok", "recipient_email": "billing@clevelandclinic.org"},
    )
    assert decision == ALLOW


@pytest.mark.parametrize("action", ["actions___submit_fap_application", "actions___request_itemized_bill"])
def test_fap_and_itemized_deny_missing_token(policy_set, action) -> None:
    decision = authorize(
        policy_set,
        role="patient",
        action=action,
        input={"approval_token": "", "recipient_email": "billing@clevelandclinic.org"},
    )
    assert decision == DENY


@pytest.mark.parametrize("action", ["actions___submit_fap_application", "actions___request_itemized_bill"])
def test_fap_and_itemized_deny_disallowed_recipient(policy_set, action) -> None:
    decision = authorize(
        policy_set,
        role="patient",
        action=action,
        input={"approval_token": "tok", "recipient_email": "billing@evil.example"},
    )
    assert decision == DENY


@pytest.mark.parametrize("action", ["actions___submit_fap_application", "actions___request_itemized_bill"])
def test_fap_and_itemized_deny_integrator(policy_set, action) -> None:
    decision = authorize(
        policy_set,
        role="integrator",
        action=action,
        input={"approval_token": "tok", "recipient_email": "billing@clevelandclinic.org"},
    )
    assert decision == DENY


# ---------------------------------------------------------------------------
# file_escalation
# ---------------------------------------------------------------------------


def _escalation_input(**overrides) -> dict:
    base = dict(approval_token="tok", finding_ids=["f1"], channel="NSA_HELPDESK")
    base.update(overrides)
    return base


@pytest.mark.parametrize("role", ["advocate", "system"])
def test_file_escalation_allows_advocate_and_system(policy_set, role) -> None:
    decision = authorize(
        policy_set, role=role, action="actions___file_escalation", input=_escalation_input()
    )
    assert decision == ALLOW


def test_file_escalation_denies_patient(policy_set) -> None:
    decision = authorize(
        policy_set, role="patient", action="actions___file_escalation", input=_escalation_input()
    )
    assert decision == DENY


def test_file_escalation_denies_empty_finding_ids(policy_set) -> None:
    decision = authorize(
        policy_set,
        role="advocate",
        action="actions___file_escalation",
        input=_escalation_input(finding_ids=[]),
    )
    assert decision == DENY


def test_file_escalation_denies_missing_token(policy_set) -> None:
    decision = authorize(
        policy_set,
        role="advocate",
        action="actions___file_escalation",
        input=_escalation_input(approval_token=""),
    )
    assert decision == DENY


# ---------------------------------------------------------------------------
# schedule_followup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["patient", "advocate", "system"])
@pytest.mark.parametrize("days", [1, 90])
def test_schedule_followup_allows_within_bounds(policy_set, role, days) -> None:
    decision = authorize(
        policy_set,
        role=role,
        action="actions___schedule_followup",
        input={"days": days, "reason": "no response"},
    )
    assert decision == ALLOW


@pytest.mark.parametrize("days", [0, 91])
def test_schedule_followup_denies_out_of_bounds_days(policy_set, days) -> None:
    decision = authorize(
        policy_set,
        role="patient",
        action="actions___schedule_followup",
        input={"days": days, "reason": "no response"},
    )
    assert decision == DENY


def test_schedule_followup_denies_integrator(policy_set) -> None:
    decision = authorize(
        policy_set,
        role="integrator",
        action="actions___schedule_followup",
        input={"days": 30, "reason": "no response"},
    )
    assert decision == DENY


# ---------------------------------------------------------------------------
# forbid_integrator_writes (defense in depth over every case+actions tool)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", CASE_ACTIONS + ACTION_ACTIONS)
def test_forbid_integrator_writes_covers_every_write_tool(policy_set, action) -> None:
    # Fully-populated input so a missing field can't be the reason for the deny.
    input_ = {
        "approval_token": "tok",
        "finding_ids": ["f1"],
        "recipient_email": DEFAULT_DEMO_BILLING_EMAIL,
        "disputed_amount_cents": 100,
        "days": 30,
        "reason": "x",
        "channel": "NSA_HELPDESK",
    }
    assert authorize(policy_set, role="integrator", action=action, input=input_) == DENY


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_writes_one_cedar_file_per_policy(tmp_path: Path, policies: dict[str, str]) -> None:
    hospitals_path = tmp_path / "hospitals.json"
    hospitals_path.write_text(json.dumps(HOSPITALS), encoding="utf-8")
    out_dir = tmp_path / "out"

    exit_code = main(
        [
            "--gateway-arn",
            GATEWAY_ARN,
            "--hospitals",
            str(hospitals_path),
            "--out",
            str(out_dir),
        ]
    )

    assert exit_code == 0
    written = {p.stem: p.read_text(encoding="utf-8") for p in out_dir.glob("*.cedar")}
    assert set(written) == set(policies)
    for name, text in policies.items():
        assert written[name] == text
