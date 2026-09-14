import json

import boto3
import pytest
from moto import mock_aws

from countercharge_tools import followup


@pytest.fixture
def secret():
    with mock_aws():
        client = boto3.client("secretsmanager", region_name="us-east-1")
        client.create_secret(
            Name="cc/system-client",
            SecretString=json.dumps(
                {
                    "token_url": "https://example.auth.us-east-1.amazoncognito.com/oauth2/token",
                    "client_id": "abc123",
                    "client_secret": "shh",
                    "scope": "cc/invoke",
                }
            ),
        )
        yield client


def test_get_system_credentials_reads_secret(secret):
    creds = followup.get_system_credentials("cc/system-client", client=secret)
    assert creds["client_id"] == "abc123"
    assert creds["client_secret"] == "shh"
    assert creds["scope"] == "cc/invoke"


def test_invoke_followup_calls_token_then_posts_invocation():
    calls = []

    def get_token():
        calls.append("token")
        return "tok123"

    def post_invocation(case_id, token):
        calls.append(("post", case_id, token))

    result = followup.invoke_followup(
        "case_abc", "checking for a reply", get_token=get_token, post_invocation=post_invocation
    )

    assert result == {"status": "invoked", "case_id": "case_abc", "reason": "checking for a reply"}
    assert calls == ["token", ("post", "case_abc", "tok123")]


def test_invoke_followup_propagates_post_failure():
    def get_token():
        return "tok"

    def post_invocation(case_id, token):
        raise RuntimeError("agent unreachable")

    with pytest.raises(RuntimeError):
        followup.invoke_followup("case_abc", "r", get_token=get_token, post_invocation=post_invocation)
