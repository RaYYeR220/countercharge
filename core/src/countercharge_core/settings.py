"""Environment-driven configuration shared by tools, agent and infra scripts."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    table_name: str = "cc-cases"
    bucket: str = ""
    kms_key_id: str = ""
    local_hmac_secret: str = ""
    region: str = "us-east-1"
    ses_sender: str = "countercharge.demo@gmail.com"
    hospitals_path: Path
    refdata_path: Path | None = None
    refdata_s3_uri: str = ""
