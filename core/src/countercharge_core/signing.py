"""MAC signing for findings: local HMAC (tests) and KMS HMAC_SHA_256 (prod)."""

import base64
import hashlib
import hmac
from typing import Protocol

import boto3

from countercharge_engine.canonical import canonical_json
from countercharge_engine.models import Finding


class Signer(Protocol):
    def mac(self, data: bytes) -> bytes: ...

    def verify(self, data: bytes, mac: bytes) -> bool: ...


class LocalHmacSigner:
    def __init__(self, secret: bytes):
        self._secret = secret

    def mac(self, data: bytes) -> bytes:
        return hmac.new(self._secret, data, hashlib.sha256).digest()

    def verify(self, data: bytes, mac: bytes) -> bool:
        return hmac.compare_digest(self.mac(data), mac)


class KmsHmacSigner:
    """KMS ``GenerateMac``/``VerifyMac`` with algorithm ``HMAC_SHA_256``."""

    def __init__(self, key_id: str, client=None):
        self._key_id = key_id
        self._client = client or boto3.client("kms")

    def mac(self, data: bytes) -> bytes:
        response = self._client.generate_mac(
            KeyId=self._key_id, Message=data, MacAlgorithm="HMAC_SHA_256"
        )
        return response["Mac"]

    def verify(self, data: bytes, mac: bytes) -> bool:
        response = self._client.verify_mac(
            KeyId=self._key_id, Message=data, MacAlgorithm="HMAC_SHA_256", Mac=mac
        )
        return bool(response["MacValid"])


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def sign_finding(signer: Signer, finding: Finding) -> str:
    return b64url_encode(signer.mac(canonical_json(finding)))


def verify_finding(signer: Signer, finding: Finding, signature: str) -> bool:
    try:
        mac = b64url_decode(signature)
    except Exception:
        return False
    return signer.verify(canonical_json(finding), mac)
