"""Gmail read-only identity bridge (AgentCore Identity, 3LO) -- interface stub.

Scope trim: not implemented or wired into the case agent in this task. Kept as a
narrow, dependency-light interface so a later task can implement it against boto3's
``bedrock-agentcore`` data-plane calls (``get_workload_access_token_for_jwt``,
``get_resource_oauth2_token``) without reshaping any caller. Nothing in ``app.py``
references this module yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class GmailAccessResult:
    """Either a usable OAuth2 access token, or a URL the patient must visit to connect Gmail."""

    access_token: str | None
    authorization_url: str | None


class GmailIdentityProvider(Protocol):
    """Resolves Gmail read-only access for a case's patient via AgentCore Identity."""

    def get_access(self, *, user_token: str) -> GmailAccessResult: ...


class NotConfiguredGmailIdentityProvider:
    """Default provider: Gmail intake is out of scope for this task."""

    def get_access(self, *, user_token: str) -> GmailAccessResult:
        raise NotImplementedError("Gmail identity bridge is not implemented yet")
