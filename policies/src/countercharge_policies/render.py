"""Render Cedar authorization policies for the Countercharge AgentCore Gateway.

Each rendered policy is validated (Cedar-parseable) before it is returned or
written to disk, so a broken template fails at render time, not at deploy time.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from string import Template
from typing import Any, Iterable, Mapping

import cedarpy

DEFAULT_DEMO_BILLING_EMAIL = "countercharge.demo+billing@gmail.com"

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# Order is stable so `render()` output and rendered filenames are deterministic.
_POLICY_NAMES = [
    "read_engine",
    "case_tools",
    "send_dispute_letter",
    "fap_and_itemized",
    "file_escalation",
    "schedule_followup",
    "forbid_integrator_writes",
]


def _hospital_get(hospital: Any, key: str, default: Any = None) -> Any:
    """Read a field off a hospital record, whether it's a dict or an object."""
    if isinstance(hospital, Mapping):
        return hospital.get(key, default)
    return getattr(hospital, key, default)


def _recipient_allowed_expr(hospitals: Iterable[Any]) -> str:
    """Build the Cedar boolean expression for "recipient_email is a legitimate
    hospital billing address": a `like` clause per hospital billing domain,
    OR'd with an exact match on each hospital's demo billing address.

    A hospital missing `demo_billing_email` defaults to
    `DEFAULT_DEMO_BILLING_EMAIL` (per the shared demo-inbox convention).
    """
    domain_clauses: list[str] = []
    demo_emails: list[str] = []
    seen_emails: set[str] = set()

    for hospital in hospitals:
        domain = _hospital_get(hospital, "billing_email_domain")
        if domain:
            domain_clauses.append(f'context.input.recipient_email like "*@{domain}"')

        demo_email = _hospital_get(hospital, "demo_billing_email") or DEFAULT_DEMO_BILLING_EMAIL
        if demo_email not in seen_emails:
            seen_emails.add(demo_email)
            demo_emails.append(demo_email)

    if not demo_emails:
        demo_emails = [DEFAULT_DEMO_BILLING_EMAIL]

    email_clauses = [f'context.input.recipient_email == "{email}"' for email in demo_emails]
    clauses = domain_clauses + email_clauses
    joined = " ||\n    ".join(clauses)
    return f"(\n    {joined}\n  )"


def _load_template(name: str) -> Template:
    text = (_TEMPLATES_DIR / f"{name}.cedar.tmpl").read_text(encoding="utf-8")
    return Template(text)


def render(gateway_arn: str, hospitals: Iterable[Any]) -> dict[str, str]:
    """Render every Cedar policy for the given gateway ARN and hospital registry.

    Returns a mapping of policy name -> Cedar policy statement text. Each
    statement is validated for Cedar syntax before being returned; a template
    bug raises `ValueError` here rather than surfacing later at deploy time.
    """
    hospitals = list(hospitals)
    recipient_allowed = _recipient_allowed_expr(hospitals)

    rendered: dict[str, str] = {}
    for name in _POLICY_NAMES:
        statement = _load_template(name).substitute(
            gateway_arn=gateway_arn,
            recipient_allowed=recipient_allowed,
        )
        try:
            cedarpy.PolicySet.from_str(statement)
        except ValueError as exc:
            raise ValueError(f"rendered policy {name!r} failed to parse: {exc}") from exc
        rendered[name] = statement

    return rendered


def _load_hospitals(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"expected a JSON list of hospitals in {path}")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render Cedar policies for the Countercharge AgentCore Gateway."
    )
    parser.add_argument("--gateway-arn", required=True, help="AgentCore Gateway ARN")
    parser.add_argument("--hospitals", required=True, type=Path, help="path to hospitals.json")
    parser.add_argument("--out", required=True, type=Path, help="output directory for .cedar files")
    args = parser.parse_args(argv)

    hospitals = _load_hospitals(args.hospitals)
    policies = render(args.gateway_arn, hospitals)

    args.out.mkdir(parents=True, exist_ok=True)
    for name, statement in policies.items():
        (args.out / f"{name}.cedar").write_text(statement, encoding="utf-8")

    print(f"wrote {len(policies)} policies to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
