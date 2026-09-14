"""Cognito access-token validation (C5).

Validates against a JWKS document rather than strands/PyJWT's own network-fetching
``PyJWKClient``, so unit tests can supply a fixed local JWKS with no network access;
:func:`fetch_jwks` performs the one real HTTP call the running service needs, cached
by the caller (see ``app.py``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.request import urlopen

import jwt
from jwt.algorithms import RSAAlgorithm


class AuthError(Exception):
    """Raised when a bearer token fails validation. Callers must fail closed on this."""


@dataclass
class AuthClaims:
    sub: str
    role: str
    org: str
    username: str
    client_id: str


def jwks_url(pool_id: str, region: str) -> str:
    return f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/jwks.json"


def issuer_url(pool_id: str, region: str) -> str:
    return f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"


def fetch_jwks(pool_id: str, region: str, *, timeout: float = 5.0) -> dict:
    """Fetch a Cognito user pool's JWKS document. Real network call -- not used by unit tests."""
    with urlopen(jwks_url(pool_id, region), timeout=timeout) as response:  # noqa: S310 (fixed https host)
        return json.loads(response.read())


def _signing_key(jwks: dict, kid: str) -> Any:
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return RSAAlgorithm.from_jwk(json.dumps(key))
    raise AuthError(f"no signing key for kid={kid!r}")


def validate_access_token(
    token: str,
    *,
    jwks: dict,
    issuer: str,
    allowed_client_ids: set[str] | None = None,
) -> AuthClaims:
    """Validate a Cognito access token (C5): signature, issuer, ``token_use``, client id.

    ``role`` and ``org`` are plain top-level claims injected by the pool's pre-token-
    generation V2 Lambda trigger (C5) -- not the ``custom:`` attribute names Cognito
    uses internally.

    Raises:
        AuthError: on any validation failure. Always fail closed.
    """
    try:
        header = jwt.get_unverified_header(token)
        signing_key = _signing_key(jwks, header.get("kid", ""))
        payload: dict[str, Any] = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=issuer,
            # Cognito access tokens carry no `aud` claim (only id tokens do); client
            # identity is verified below via the `client_id` claim instead.
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"invalid token: {exc}") from exc

    if payload.get("token_use") != "access":
        raise AuthError(f"token_use must be 'access', got {payload.get('token_use')!r}")

    client_id = payload.get("client_id", "")
    if allowed_client_ids is not None and client_id not in allowed_client_ids:
        raise AuthError(f"client_id not allowed: {client_id!r}")

    role = payload.get("role")
    if not role:
        raise AuthError("missing 'role' claim")

    return AuthClaims(
        sub=payload.get("sub", ""),
        role=role,
        org=payload.get("org", ""),
        username=payload.get("username", ""),
        client_id=client_id,
    )
