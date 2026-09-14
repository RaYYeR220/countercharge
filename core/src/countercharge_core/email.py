"""Outbound email for actions Lambdas (SES in prod, in-memory for tests)."""

import uuid
from typing import Protocol

import boto3


class EmailSender(Protocol):
    def send(self, *, to: str, subject: str, markdown: str, reply_to: str | None) -> str: ...


class SesEmailSender:
    def __init__(self, sender: str, client=None):
        self._sender = sender
        self._client = client or boto3.client("ses")

    def send(self, *, to: str, subject: str, markdown: str, reply_to: str | None) -> str:
        kwargs: dict = {
            "Source": self._sender,
            "Destination": {"ToAddresses": [to]},
            "Message": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": markdown, "Charset": "UTF-8"}},
            },
        }
        if reply_to:
            kwargs["ReplyToAddresses"] = [reply_to]
        response = self._client.send_email(**kwargs)
        return response["MessageId"]


class MemoryEmailSender:
    """Records sent messages in-process; used by tests and local runs."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, *, to: str, subject: str, markdown: str, reply_to: str | None) -> str:
        message_id = f"mem_{uuid.uuid4().hex[:16]}"
        self.sent.append(
            {
                "message_id": message_id,
                "to": to,
                "subject": subject,
                "markdown": markdown,
                "reply_to": reply_to,
            }
        )
        return message_id
