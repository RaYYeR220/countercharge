"""Load ``SqliteRefData`` once per Lambda execution environment.

If ``REFDATA_S3_URI`` is set, the sqlite file is downloaded to
``/tmp/refdata.sqlite`` the first time a warm container needs it and reused
for the lifetime of that container (Lambda's 2048 MB ``/tmp`` ephemeral
storage, per the plan). Otherwise ``REFDATA_PATH`` (baked into the image, or
mounted) is read directly. The loaded instance is cached at module scope so
repeat invocations on the same warm container skip the disk/S3 work.
"""

from pathlib import Path

import boto3

from countercharge_engine.refdata.base import RefData
from countercharge_engine.refdata.sqlite import SqliteRefData

from countercharge_tools.deps import ToolError

_CACHE_PATH = Path("/tmp/refdata.sqlite")  # noqa: S108 - intentional Lambda /tmp cache
_cached: RefData | None = None


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    without_scheme = uri.removeprefix("s3://")
    bucket, _, key = without_scheme.partition("/")
    if not bucket or not key:
        raise ToolError(f"malformed REFDATA_S3_URI: {uri!r}")
    return bucket, key


def load_refdata(settings, *, s3_client=None, cache_path: Path = _CACHE_PATH) -> RefData:
    global _cached
    if _cached is not None:
        return _cached

    if settings.refdata_s3_uri:
        if not cache_path.exists():
            bucket, key = _parse_s3_uri(settings.refdata_s3_uri)
            client = s3_client or boto3.client("s3", region_name=settings.region)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, key, str(cache_path))
        _cached = SqliteRefData(cache_path)
    elif settings.refdata_path:
        _cached = SqliteRefData(Path(settings.refdata_path))
    else:
        raise ToolError("no refdata configured: set REFDATA_S3_URI or REFDATA_PATH")

    return _cached


def reset_cache_for_tests() -> None:
    global _cached
    _cached = None
