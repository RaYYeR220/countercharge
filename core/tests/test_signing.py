import boto3
import pytest
from botocore.stub import Stubber

from countercharge_core.signing import (
    KmsHmacSigner,
    LocalHmacSigner,
    b64url_decode,
    b64url_encode,
    sign_finding,
    verify_finding,
)
from countercharge_engine.canonical import canonical_json, with_id
from countercharge_engine.models import Citation, Finding


def make_finding(amount_cents: int = 5000) -> Finding:
    citation = Citation(dataset="ncci-ptp", version="2026q4", url="https://example.com", record={})
    finding = Finding(
        rule_id="R1",
        disputable=True,
        line_ids=["l1"],
        amount_cents=amount_cents,
        title="Duplicate charge",
        detail="Line billed twice",
        citation=citation,
    )
    return with_id(finding)


def test_b64url_roundtrip():
    data = b"\x00\x01hello\xff"
    assert b64url_decode(b64url_encode(data)) == data


def test_local_hmac_signer_mac_is_deterministic():
    signer = LocalHmacSigner(b"secret")
    data = b"payload"
    assert signer.mac(data) == signer.mac(data)


def test_local_hmac_signer_verify_true_for_own_mac():
    signer = LocalHmacSigner(b"secret")
    data = b"payload"
    assert signer.verify(data, signer.mac(data))


def test_local_hmac_signer_verify_false_for_wrong_secret():
    a = LocalHmacSigner(b"secret-a")
    b = LocalHmacSigner(b"secret-b")
    data = b"payload"
    assert not b.verify(data, a.mac(data))


def test_local_hmac_signer_verify_false_for_tampered_data():
    signer = LocalHmacSigner(b"secret")
    mac = signer.mac(b"payload")
    assert not signer.verify(b"tampered", mac)


def test_sign_finding_and_verify_roundtrip():
    signer = LocalHmacSigner(b"secret")
    finding = make_finding()
    signature = sign_finding(signer, finding)
    assert verify_finding(signer, finding, signature)


def test_verify_finding_fails_for_tampered_amount():
    signer = LocalHmacSigner(b"secret")
    finding = make_finding()
    signature = sign_finding(signer, finding)
    tampered = finding.model_copy(update={"amount_cents": 999999})
    assert not verify_finding(signer, tampered, signature)


def test_verify_finding_ignores_finding_id_changes():
    # finding_id is content-addressed and stripped from the canonical payload,
    # so re-deriving it must not invalidate a signature computed before.
    signer = LocalHmacSigner(b"secret")
    finding = make_finding()
    signature = sign_finding(signer, finding)
    relabeled = finding.model_copy(update={"finding_id": "f_deadbeef00000000"})
    assert verify_finding(signer, relabeled, signature)


def test_verify_finding_rejects_malformed_signature():
    signer = LocalHmacSigner(b"secret")
    finding = make_finding()
    assert not verify_finding(signer, finding, "not-valid-base64!!!")


def test_kms_hmac_signer_mac_uses_generate_mac_hmac_sha_256():
    client = boto3.client("kms", region_name="us-east-1")
    stubber = Stubber(client)
    finding = make_finding()
    data = canonical_json(finding)
    stubber.add_response(
        "generate_mac",
        {"Mac": b"fixed-mac-bytes", "MacAlgorithm": "HMAC_SHA_256", "KeyId": "key-1"},
        {"KeyId": "key-1", "Message": data, "MacAlgorithm": "HMAC_SHA_256"},
    )
    with stubber:
        signer = KmsHmacSigner("key-1", client=client)
        assert signer.mac(data) == b"fixed-mac-bytes"
    stubber.assert_no_pending_responses()


def test_kms_hmac_signer_verify_true():
    client = boto3.client("kms", region_name="us-east-1")
    stubber = Stubber(client)
    data = b"payload-bytes"
    stubber.add_response(
        "verify_mac",
        {"MacValid": True, "MacAlgorithm": "HMAC_SHA_256", "KeyId": "key-1"},
        {"KeyId": "key-1", "Message": data, "MacAlgorithm": "HMAC_SHA_256", "Mac": b"some-mac"},
    )
    with stubber:
        signer = KmsHmacSigner("key-1", client=client)
        assert signer.verify(data, b"some-mac") is True
    stubber.assert_no_pending_responses()


def test_kms_hmac_signer_verify_false():
    client = boto3.client("kms", region_name="us-east-1")
    stubber = Stubber(client)
    data = b"payload-bytes"
    stubber.add_response(
        "verify_mac",
        {"MacValid": False, "MacAlgorithm": "HMAC_SHA_256", "KeyId": "key-1"},
        {"KeyId": "key-1", "Message": data, "MacAlgorithm": "HMAC_SHA_256", "Mac": b"bad-mac"},
    )
    with stubber:
        signer = KmsHmacSigner("key-1", client=client)
        assert signer.verify(data, b"bad-mac") is False
    stubber.assert_no_pending_responses()


def test_kms_hmac_signer_sign_finding_end_to_end():
    client = boto3.client("kms", region_name="us-east-1")
    stubber = Stubber(client)
    finding = make_finding()
    data = canonical_json(finding)
    stubber.add_response(
        "generate_mac",
        {"Mac": b"deadbeef", "MacAlgorithm": "HMAC_SHA_256", "KeyId": "key-1"},
        {"KeyId": "key-1", "Message": data, "MacAlgorithm": "HMAC_SHA_256"},
    )
    stubber.add_response(
        "verify_mac",
        {"MacValid": True, "MacAlgorithm": "HMAC_SHA_256", "KeyId": "key-1"},
        {"KeyId": "key-1", "Message": data, "MacAlgorithm": "HMAC_SHA_256", "Mac": b"deadbeef"},
    )
    with stubber:
        signer = KmsHmacSigner("key-1", client=client)
        signature = sign_finding(signer, finding)
        assert verify_finding(signer, finding, signature)
    stubber.assert_no_pending_responses()
