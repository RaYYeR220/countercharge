"""EventBridge Scheduler entrypoint (``followup.handler``).

Not a Gateway/MCP target -- ``action_tools.schedule_followup`` points the
one-time schedule's ``Target.Arn`` directly at this Lambda (env
``FOLLOWUP_LAMBDA_ARN``), so it's invoked with the plain JSON payload the
schedule stores as ``Target.Input``: ``{"case_id", "reason"}``. On firing it
obtains a Cognito client-credentials (M2M) token for the system client
(stored in Secrets Manager, default id ``cc/system-client``, secret JSON
``{"token_url","client_id","client_secret","scope"}``) and POSTs the case
agent's ``/invocations`` endpoint in ``followup`` mode.

The orchestration (``invoke_followup``) takes its token/HTTP calls as
injectable callables so it's unit-testable without a live agent endpoint;
``handler`` wires the real ones from the environment.
"""

import json
import os
import urllib.request
from collections.abc import Callable

import boto3


def get_system_credentials(secret_id: str, *, client=None) -> dict:
    client = client or boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_id)
    return json.loads(response["SecretString"])


def fetch_m2m_token(token_url: str, client_id: str, client_secret: str, scope: str = "") -> str:
    data = f"grant_type=client_credentials&client_id={client_id}&client_secret={client_secret}"
    if scope:
        data += f"&scope={scope}"
    request = urllib.request.Request(
        token_url,
        data=data.encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - trusted Cognito URL
        body = json.loads(response.read().decode("utf-8"))
    return body["access_token"]


def post_followup_invocation(agent_url: str, case_id: str, token: str) -> None:
    body = json.dumps({"case_id": case_id, "mode": "followup"}).encode("utf-8")
    request = urllib.request.Request(
        f"{agent_url.rstrip('/')}/invocations",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": case_id,
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - trusted agent URL
        response.read()


def invoke_followup(
    case_id: str,
    reason: str,
    *,
    get_token: Callable[[], str],
    post_invocation: Callable[[str, str], None],
) -> dict:
    token = get_token()
    post_invocation(case_id, token)
    return {"status": "invoked", "case_id": case_id, "reason": reason}


def handler(event, context):
    case_id = event["case_id"]
    reason = event.get("reason", "")

    secret_id = os.environ.get("SYSTEM_CLIENT_SECRET_ID", "cc/system-client")
    agent_url = os.environ["AGENT_INVOKE_URL"]

    def get_token() -> str:
        creds = get_system_credentials(secret_id)
        return fetch_m2m_token(
            creds["token_url"], creds["client_id"], creds["client_secret"], creds.get("scope", "")
        )

    def post_invocation(cid: str, token: str) -> None:
        post_followup_invocation(agent_url, cid, token)

    return invoke_followup(case_id, reason, get_token=get_token, post_invocation=post_invocation)
