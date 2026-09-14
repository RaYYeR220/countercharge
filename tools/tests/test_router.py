import boto3
import pytest

from countercharge_tools import router
from countercharge_tools.deps import ToolDenied, ToolError

from conftest import FakeContext


@pytest.fixture(autouse=True)
def base_env(monkeypatch, hospitals_path, refdata_db_path):
    monkeypatch.setenv("HOSPITALS_PATH", str(hospitals_path))
    monkeypatch.setenv("REFDATA_PATH", str(refdata_db_path))
    monkeypatch.setenv("TABLE_NAME", "cc-cases")
    monkeypatch.setenv("REGION", "us-east-1")
    monkeypatch.setenv("LOCAL_HMAC_SECRET", "test-secret")
    monkeypatch.setenv("CC_MEMORY_EMAIL", "1")


def test_tool_name_strips_target_prefix():
    assert router._tool_name(FakeContext("engine___audit_case")) == "audit_case"
    assert router._tool_name(FakeContext("check_units")) == "check_units"


def test_tool_name_missing_client_context_returns_empty():
    class Empty:
        pass

    assert router._tool_name(Empty()) == ""


def test_router_dispatch_engine_explain_rule(monkeypatch):
    monkeypatch.setenv("CC_TARGET", "engine")
    result = router.handler({"rule_id": "NCCI_PTP"}, FakeContext("engine___explain_rule"))
    assert result["rule_id"] == "NCCI_PTP"


def test_router_dispatch_engine_check_code_pair(monkeypatch):
    monkeypatch.setenv("CC_TARGET", "engine")
    result = router.handler(
        {"code1": "99213", "code2": "99214", "setting": "ER", "dos": "2026-01-10"},
        FakeContext("engine___check_code_pair"),
    )
    assert result["disputable"] is True


def test_router_dispatch_case_get_case(monkeypatch, ddb_table):
    monkeypatch.setenv("CC_TARGET", "case")
    from countercharge_core.store import CaseStore

    case_id = CaseStore(ddb_table).create_case(
        org="org1", patient_name="Pat", patient_email="p@example.com", patient_state="NY",
        reply_to_email="p@example.com", hospital_id="nyp",
    )
    result = router.handler({"case_id": case_id}, FakeContext("case___get_case"))
    assert result["case_id"] == case_id
    assert result["meta"]["org"] == "org1"


def test_router_unknown_target(monkeypatch):
    monkeypatch.setenv("CC_TARGET", "bogus")
    with pytest.raises(ToolError):
        router.handler({}, FakeContext("bogus___whatever"))


def test_router_dispatch_engine_unknown_tool(monkeypatch):
    monkeypatch.setenv("CC_TARGET", "engine")
    with pytest.raises(ToolError):
        router.handler({}, FakeContext("engine___not_a_real_tool"))


def test_router_actions_deny_propagates_as_tool_denied(monkeypatch, ddb_table):
    monkeypatch.setenv("CC_TARGET", "actions")
    monkeypatch.setenv("FOLLOWUP_LAMBDA_ARN", "arn:aws:lambda:us-east-1:123456789012:function:cc-followup")
    monkeypatch.setenv("SCHEDULER_ROLE_ARN", "arn:aws:iam::123456789012:role/cc-scheduler-role")

    from countercharge_core.store import CaseStore

    case_id = CaseStore(ddb_table).create_case(
        org="org1", patient_name="Pat", patient_email="p@example.com", patient_state="NY",
        reply_to_email="p@example.com", hospital_id="nyp",
    )

    with boto3_scheduler_group():
        with pytest.raises(ToolDenied) as exc:
            router.handler(
                {"case_id": case_id, "days": 0, "reason": "x"},
                FakeContext("actions___schedule_followup"),
            )
    assert str(exc.value) == "DENIED:lambda:invalid_days"


class boto3_scheduler_group:
    """``ddb_table`` already opened the ``mock_aws()`` context (via the
    ``aws`` fixture); this just ensures the scheduler group router code
    expects exists, without opening a second mock context."""

    def __enter__(self):
        boto3.client("scheduler", region_name="us-east-1").create_schedule_group(Name="cc-followups")
        return self

    def __exit__(self, *exc_info):
        return False
