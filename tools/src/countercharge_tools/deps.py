"""Shared exceptions and lazily-constructed AWS/core dependencies.

Business logic in ``engine_tools``/``case_tools``/``action_tools`` takes its
dependencies (store, signer, refdata, email sender, ...) as plain arguments
so it can be unit-tested with fakes/moto without touching the environment.
``router.py`` is the only place that reads env vars and builds the real
things, via the ``get_*`` functions here.
"""

import os

import boto3

from countercharge_core.email import EmailSender, MemoryEmailSender, SesEmailSender
from countercharge_core.hospitals import Hospital, load_hospitals
from countercharge_core.settings import Settings
from countercharge_core.signing import KmsHmacSigner, LocalHmacSigner, Signer
from countercharge_core.store import CaseStore


class ToolDenied(Exception):
    """Fail-closed denial raised by an ``actions`` target tool.

    ``str(exc)`` is always ``"DENIED:lambda:<reason>"`` -- this exception is
    meant to propagate out of the Lambda handler unhandled, so the Lambda
    runtime reports a ``FunctionError`` and AgentCore Gateway surfaces an MCP
    tool result with ``isError: true`` whose text is that message (see
    tools/README notes in the task report for why raising, not returning
    ``{"error": ...}``, is what the Gateway needs to set ``isError``).
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"DENIED:lambda:{reason}")


class ToolError(Exception):
    """Plain application-level tool error (bad input, not found, misconfigured).

    Also meant to propagate unhandled -- it still yields ``isError: true``,
    just without the ``DENIED:lambda:`` prefix reserved for actions-target
    fail-closed denials.
    """


def get_settings() -> Settings:
    return Settings()


def get_signer(settings: Settings | None = None) -> Signer:
    settings = settings or get_settings()
    if settings.kms_key_id:
        return KmsHmacSigner(settings.kms_key_id)
    return LocalHmacSigner(settings.local_hmac_secret.encode("utf-8"))


def get_store(settings: Settings | None = None) -> CaseStore:
    settings = settings or get_settings()
    table = boto3.resource("dynamodb", region_name=settings.region).Table(settings.table_name)
    s3 = boto3.client("s3", region_name=settings.region) if settings.bucket else None
    return CaseStore(table, s3=s3, bucket=settings.bucket)


def get_email_sender(settings: Settings | None = None) -> EmailSender:
    settings = settings or get_settings()
    if os.environ.get("CC_MEMORY_EMAIL"):
        return MemoryEmailSender()
    client = boto3.client("ses", region_name=settings.region)
    return SesEmailSender(settings.ses_sender, client=client)


def get_hospitals(settings: Settings | None = None) -> dict[str, Hospital]:
    settings = settings or get_settings()
    return load_hospitals(settings.hospitals_path)


def get_scheduler_client(settings: Settings | None = None):
    settings = settings or get_settings()
    return boto3.client("scheduler", region_name=settings.region)
