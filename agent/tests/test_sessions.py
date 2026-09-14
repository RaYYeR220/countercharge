import boto3
from moto import mock_aws
from strands.session.s3_session_manager import S3SessionManager

from countercharge_agent.sessions import build_session_manager


def test_build_session_manager_returns_configured_s3_session_manager():
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="cc-data")

        manager = build_session_manager("case_123", bucket="cc-data", prefix="sessions/")

        assert isinstance(manager, S3SessionManager)
        assert manager.bucket == "cc-data"
        assert manager.prefix == "sessions/"
        assert manager.session_id == "case_123"


def test_build_session_manager_default_prefix():
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="cc-data")

        manager = build_session_manager("case_456", bucket="cc-data")

        assert manager.prefix == "sessions/"
