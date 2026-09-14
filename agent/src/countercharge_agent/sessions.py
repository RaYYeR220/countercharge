"""Session persistence: one S3-backed strands session per case (C3 bucket, session id = case_id)."""

from __future__ import annotations

from strands.session.s3_session_manager import S3SessionManager
from strands.session.session_manager import SessionManager


def build_session_manager(case_id: str, *, bucket: str, prefix: str = "sessions/") -> SessionManager:
    """Build the durable session manager for ``case_id``.

    Required for interrupts to survive across HTTP invocations/processes (FINDINGS.md:
    "Needs durable session manager ... persist `_interrupt_state`").
    """
    return S3SessionManager(session_id=case_id, bucket=bucket, prefix=prefix)
