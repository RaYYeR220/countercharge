"""Canonical JSON encoding and content-addressed finding ids.

``finding_id`` must be stable regardless of dict key insertion order and
independent of any previously-set ``finding_id`` value, so two findings
that differ only by that field hash identically.
"""

import hashlib
import json
from datetime import date, datetime

from pydantic import BaseModel

from countercharge_engine.models import Finding


def _default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _strip_finding_id(value: object) -> object:
    if isinstance(value, dict):
        return {k: _strip_finding_id(v) for k, v in value.items() if k != "finding_id"}
    if isinstance(value, list):
        return [_strip_finding_id(v) for v in value]
    return value


def canonical_json(obj: BaseModel | dict) -> bytes:
    data = obj.model_dump(mode="json") if isinstance(obj, BaseModel) else obj
    data = _strip_finding_id(data)
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=_default
    ).encode("utf-8")


def finding_id(f: Finding) -> str:
    digest = hashlib.sha256(canonical_json(f)).hexdigest()
    return f"f_{digest[:16]}"


def with_id(f: Finding) -> Finding:
    return f.model_copy(update={"finding_id": finding_id(f)})
