from pathlib import Path

import pytest
from pydantic import ValidationError

from countercharge_core.settings import Settings


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("BUCKET", raising=False)
    monkeypatch.delenv("KMS_KEY_ID", raising=False)
    monkeypatch.delenv("REGION", raising=False)
    settings = Settings(hospitals_path="engine/data/hospitals.json")
    assert settings.table_name == "cc-cases"
    assert settings.bucket == ""
    assert settings.region == "us-east-1"
    assert settings.ses_sender == "countercharge.demo@gmail.com"
    assert settings.hospitals_path == Path("engine/data/hospitals.json")
    assert settings.refdata_path is None
    assert settings.refdata_s3_uri == ""


def test_settings_hospitals_path_is_required():
    with pytest.raises(ValidationError):
        Settings()


def test_settings_reads_from_env(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "cc-cases-test")
    monkeypatch.setenv("BUCKET", "cc-1234-data")
    monkeypatch.setenv("KMS_KEY_ID", "alias/cc-hmac")
    monkeypatch.setenv("REGION", "us-west-2")
    monkeypatch.setenv("HOSPITALS_PATH", "/tmp/hospitals.json")
    monkeypatch.setenv("REFDATA_S3_URI", "s3://cc-bucket/refdata/refdata.sqlite")
    settings = Settings()
    assert settings.table_name == "cc-cases-test"
    assert settings.bucket == "cc-1234-data"
    assert settings.kms_key_id == "alias/cc-hmac"
    assert settings.region == "us-west-2"
    assert settings.hospitals_path == Path("/tmp/hospitals.json")
    assert settings.refdata_s3_uri == "s3://cc-bucket/refdata/refdata.sqlite"


def test_settings_refdata_path_optional_override(monkeypatch):
    monkeypatch.setenv("HOSPITALS_PATH", "/tmp/hospitals.json")
    monkeypatch.setenv("REFDATA_PATH", "/tmp/refdata.sqlite")
    settings = Settings()
    assert settings.refdata_path == Path("/tmp/refdata.sqlite")
