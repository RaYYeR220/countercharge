import json
import sqlite3
from datetime import date
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from countercharge_core.hospitals import load_hospitals
from countercharge_core.signing import LocalHmacSigner
from countercharge_core.store import CaseStore
from countercharge_engine.models import (
    Bill,
    CodeType,
    LineItem,
    Provider,
    Setting,
    Totals,
)
from countercharge_engine.refdata.sqlite import SCHEMA_SQL, SqliteRefData

TODAY_YEAR = date.today().year


@pytest.fixture(autouse=True)
def _reset_refdata_cache():
    """``refdata_loader`` caches its ``SqliteRefData`` at module scope (by
    design -- one load per warm Lambda container); reset it around every
    test so a tmp_path sqlite file from one test can't leak into another."""
    from countercharge_tools import refdata_loader

    refdata_loader.reset_cache_for_tests()
    yield
    refdata_loader.reset_cache_for_tests()


@pytest.fixture
def aws():
    with mock_aws():
        yield


@pytest.fixture
def ddb_table(aws):
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    ddb.create_table(
        TableName="cc-cases",
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "GSI1",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
            }
        ],
        BillingMode="PROVISIONED",
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
    )
    return ddb.Table("cc-cases")


@pytest.fixture
def store(ddb_table):
    return CaseStore(ddb_table)


@pytest.fixture
def signer():
    return LocalHmacSigner(b"test-secret")


@pytest.fixture
def hospitals_path(tmp_path: Path) -> Path:
    data = [
        {
            "hospital_id": "nyp",
            "name": "NewYork-Presbyterian Hospital",
            "nonprofit": True,
            "billing_email_domain": "nyp.org",
            "demo_billing_email": "countercharge.demo+billing@gmail.com",
            "free_max_fpl": 200,
            "discount_max_fpl": 400,
            "agb_pct": None,
            "source_url": "https://example.com/fap",
            "retrieved": "2026-01-01",
            "note": "",
        }
    ]
    path = tmp_path / "hospitals.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def hospitals(hospitals_path):
    return load_hospitals(hospitals_path)


@pytest.fixture
def refdata_db_path(tmp_path: Path) -> Path:
    db_path = tmp_path / "refdata.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO ptp VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("opps", "99213", "99214", 1, "2020-01-01", None, "bundled"),
    )
    conn.execute(
        "INSERT INTO ptp VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("opps", "99215", "99216", 0, "2020-01-01", None, "never separately payable"),
    )
    conn.execute(
        "INSERT INTO mue VALUES (?, ?, ?, ?, ?)",
        ("opps", "99213", 1, 1, "one per day"),
    )
    conn.execute("INSERT INTO rates VALUES (?, ?, ?)", ("99213", 0, 9260))
    conn.execute("INSERT INTO hcpcs2 VALUES (?, ?)", ("J1885", "Ketorolac tromethamine inj"))
    conn.execute(
        "INSERT INTO fpl VALUES (?, ?, ?, ?)", (TODAY_YEAR, "48", 1596000, 568000)
    )
    conn.execute(
        "INSERT INTO hospital_price VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("nyp", "99213", "ER", 50000, 10000, 8000, 60000),
    )
    conn.execute(
        "INSERT INTO hospital_fap VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("nyp", "NewYork-Presbyterian", 200, 400, 20, "https://example.com/fap", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO datasets VALUES (?, ?, ?)",
        ("FAP-nyp", "2026", "https://example.com/fap"),
    )
    conn.execute("INSERT INTO meta VALUES (?, ?)", ("version", "test-fixture"))
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def refdata(refdata_db_path):
    return SqliteRefData(refdata_db_path)


def make_bill(*, hospital_id="nyp", charge_cents=20000, patient_balance_cents=20000, code="99213", units=1):
    return Bill(
        provider=Provider(
            name="Test Hospital", nonprofit=True, billing_email_domain="nyp.org", hospital_id=hospital_id
        ),
        account_no="ACC-1",
        statement_date=date(2026, 1, 15),
        setting=Setting.ER,
        lines=[
            LineItem(
                line_id="l1",
                dos=date(2026, 1, 10),
                code=code,
                code_type=CodeType.CPT,
                units=units,
                charge_cents=charge_cents,
            )
        ],
        totals=Totals(charges_cents=charge_cents, patient_balance_cents=patient_balance_cents),
        self_pay=False,
    )


class FakeClientContext:
    def __init__(self, tool_name: str):
        self.custom = {"bedrockAgentCoreToolName": tool_name}


class FakeContext:
    def __init__(self, tool_name: str):
        self.client_context = FakeClientContext(tool_name)
