"""Id and sort-key generation helpers shared across the core package.

Entity ids are short, prefixed, random tokens (``case_...``, ``doc_...``,
``action_...``). DynamoDB range-key suffixes for append-only item families
(``DECISION#<iso_ts>#<rand4>``, ``EVENT#<iso_ts>#<rand4>``) combine an
ISO-8601 UTC timestamp with a short random suffix so concurrent writes in
the same millisecond never collide.
"""

import secrets
import uuid
from datetime import UTC, datetime


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def new_case_id() -> str:
    return new_id("case")


def new_doc_id() -> str:
    return new_id("doc")


def new_action_id() -> str:
    return new_id("action")


def iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def rand4() -> str:
    return secrets.token_hex(2)


def sort_key(prefix: str, ts: str | None = None) -> str:
    """Build a ``<prefix>#<iso_ts>#<rand4>`` DynamoDB SK per contract C3."""
    ts = ts or iso_now()
    return f"{prefix}#{ts}#{rand4()}"
