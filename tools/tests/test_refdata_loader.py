from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws

from countercharge_engine.refdata.sqlite import SqliteRefData

from countercharge_tools import refdata_loader
from countercharge_tools.deps import ToolError


def _settings(**overrides):
    base = {"refdata_s3_uri": "", "refdata_path": None, "region": "us-east-1"}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_load_refdata_from_path(refdata_db_path):
    settings = _settings(refdata_path=refdata_db_path)
    result = refdata_loader.load_refdata(settings)
    assert isinstance(result, SqliteRefData)
    assert result.version == "test-fixture"


def test_load_refdata_is_cached_across_calls(refdata_db_path):
    settings = _settings(refdata_path=refdata_db_path)
    first = refdata_loader.load_refdata(settings)
    second = refdata_loader.load_refdata(settings)
    assert first is second


def test_load_refdata_raises_when_unconfigured():
    settings = _settings()
    with pytest.raises(ToolError):
        refdata_loader.load_refdata(settings)


def test_load_refdata_downloads_from_s3(refdata_db_path, tmp_path):
    cache_path = tmp_path / "cached-refdata.sqlite"
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="cc-data")
        s3.upload_file(str(refdata_db_path), "cc-data", "refdata/refdata.sqlite")

        settings = _settings(refdata_s3_uri="s3://cc-data/refdata/refdata.sqlite")
        result = refdata_loader.load_refdata(settings, s3_client=s3, cache_path=cache_path)

        assert isinstance(result, SqliteRefData)
        assert cache_path.exists()
        assert result.version == "test-fixture"


def test_load_refdata_reuses_existing_cache_file_without_redownload(refdata_db_path, tmp_path):
    cache_path = tmp_path / "cached-refdata.sqlite"
    cache_path.write_bytes(refdata_db_path.read_bytes())

    class ExplodingS3:
        def download_file(self, *args, **kwargs):
            raise AssertionError("should not re-download when cache file already exists")

    settings = _settings(refdata_s3_uri="s3://cc-data/refdata/refdata.sqlite")
    result = refdata_loader.load_refdata(settings, s3_client=ExplodingS3(), cache_path=cache_path)
    assert isinstance(result, SqliteRefData)
