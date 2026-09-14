import json
import time

import jwt
import pytest
from jwt.algorithms import RSAAlgorithm

from countercharge_agent.auth import AuthError, issuer_url, validate_access_token

POOL_ID = "us-east-1_TESTPOOL"
REGION = "us-east-1"
ISSUER = issuer_url(POOL_ID, REGION)
KID = "test-key-1"


@pytest.fixture(scope="module")
def keypair():
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture(scope="module")
def jwks(keypair):
    _, public_key = keypair
    jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk["kid"] = KID
    jwk["use"] = "sig"
    return {"keys": [jwk]}


def _sign(keypair, *, claims: dict, kid: str = KID) -> str:
    private_key, _ = keypair
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def _base_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "sub": "user-123",
        "token_use": "access",
        "iss": ISSUER,
        "iat": now,
        "exp": now + 3600,
        "client_id": "web-client",
        "username": "patient1",
        "role": "patient",
        "org": "org1",
    }
    claims.update(overrides)
    return claims


def test_valid_token_is_accepted(keypair, jwks):
    token = _sign(keypair, claims=_base_claims())

    claims = validate_access_token(token, jwks=jwks, issuer=ISSUER)

    assert claims.sub == "user-123"
    assert claims.role == "patient"
    assert claims.org == "org1"
    assert claims.client_id == "web-client"


def test_wrong_signature_is_rejected(jwks):
    from cryptography.hazmat.primitives.asymmetric import rsa

    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(_base_claims(), other_key, algorithm="RS256", headers={"kid": KID})

    with pytest.raises(AuthError):
        validate_access_token(token, jwks=jwks, issuer=ISSUER)


def test_expired_token_is_rejected(keypair, jwks):
    now = int(time.time())
    token = _sign(keypair, claims=_base_claims(iat=now - 7200, exp=now - 3600))

    with pytest.raises(AuthError):
        validate_access_token(token, jwks=jwks, issuer=ISSUER)


def test_id_token_is_rejected(keypair, jwks):
    token = _sign(keypair, claims=_base_claims(token_use="id"))

    with pytest.raises(AuthError, match="token_use"):
        validate_access_token(token, jwks=jwks, issuer=ISSUER)


def test_wrong_issuer_is_rejected(keypair, jwks):
    token = _sign(keypair, claims=_base_claims(iss="https://cognito-idp.us-east-1.amazonaws.com/other-pool"))

    with pytest.raises(AuthError):
        validate_access_token(token, jwks=jwks, issuer=ISSUER)


def test_disallowed_client_id_is_rejected(keypair, jwks):
    token = _sign(keypair, claims=_base_claims(client_id="integrator-client"))

    with pytest.raises(AuthError, match="client_id"):
        validate_access_token(token, jwks=jwks, issuer=ISSUER, allowed_client_ids={"web-client"})


def test_allowed_client_id_passes(keypair, jwks):
    token = _sign(keypair, claims=_base_claims(client_id="web-client"))

    claims = validate_access_token(token, jwks=jwks, issuer=ISSUER, allowed_client_ids={"web-client", "system-client"})

    assert claims.client_id == "web-client"


def test_missing_role_claim_is_rejected(keypair, jwks):
    claims = _base_claims()
    del claims["role"]
    token = _sign(keypair, claims=claims)

    with pytest.raises(AuthError, match="role"):
        validate_access_token(token, jwks=jwks, issuer=ISSUER)


def test_unknown_kid_is_rejected(keypair, jwks):
    token = _sign(keypair, claims=_base_claims(), kid="not-in-jwks")

    with pytest.raises(AuthError, match="signing key"):
        validate_access_token(token, jwks=jwks, issuer=ISSUER)


def test_malformed_token_is_rejected(jwks):
    with pytest.raises(AuthError):
        validate_access_token("not-a-jwt", jwks=jwks, issuer=ISSUER)
