import boto3
import pytest
from moto import mock_aws

from countercharge_core.email import MemoryEmailSender, SesEmailSender


def test_memory_email_sender_records_and_returns_id():
    sender = MemoryEmailSender()
    message_id = sender.send(
        to="billing@nyp.org",
        subject="Dispute",
        markdown="# Dispute letter",
        reply_to="patient@example.com",
    )
    assert message_id
    assert len(sender.sent) == 1
    assert sender.sent[0] == {
        "message_id": message_id,
        "to": "billing@nyp.org",
        "subject": "Dispute",
        "markdown": "# Dispute letter",
        "reply_to": "patient@example.com",
    }


def test_memory_email_sender_ids_are_unique():
    sender = MemoryEmailSender()
    a = sender.send(to="a@x.com", subject="s", markdown="m", reply_to=None)
    b = sender.send(to="b@x.com", subject="s", markdown="m", reply_to=None)
    assert a != b


@pytest.fixture
def ses_client():
    with mock_aws():
        client = boto3.client("ses", region_name="us-east-1")
        client.verify_email_identity(EmailAddress="countercharge.demo@gmail.com")
        yield client


def test_ses_email_sender_sends_and_returns_message_id(ses_client):
    sender = SesEmailSender("countercharge.demo@gmail.com", client=ses_client)
    message_id = sender.send(
        to="billing@nyp.org",
        subject="Itemized bill request",
        markdown="Please send an itemized bill.",
        reply_to="patient@example.com",
    )
    assert message_id


def test_ses_email_sender_without_reply_to(ses_client):
    sender = SesEmailSender("countercharge.demo@gmail.com", client=ses_client)
    message_id = sender.send(to="billing@nyp.org", subject="s", markdown="m", reply_to=None)
    assert message_id
