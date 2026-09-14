"""Shared fixtures for the extraction-eval tests: a tiny local sqlite refdata
fixture (no network, no AWS) mirroring the one in ``tools/tests/conftest.py``,
plus a minimal ``Bill`` builder."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from countercharge_engine.models import Bill, CodeType, LineItem, Provider, Setting, Totals
from countercharge_engine.refdata.sqlite import SCHEMA_SQL, SqliteRefData

TODAY_YEAR = date.today().year


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
        "INSERT INTO mue VALUES (?, ?, ?, ?, ?)",
        ("opps", "99213", 1, 1, "one per day"),
    )
    conn.execute("INSERT INTO rates VALUES (?, ?, ?)", ("99213", 0, 9260))
    conn.execute("INSERT INTO fpl VALUES (?, ?, ?, ?)", (TODAY_YEAR, "48", 1596000, 568000))
    conn.execute(
        "INSERT INTO hospital_price VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("nyp", "99213", "ER", 50000, 10000, 8000, 60000),
    )
    conn.execute(
        "INSERT INTO hospital_fap VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("nyp", "NewYork-Presbyterian", 200, 400, 20, "https://example.com/fap", "2026-01-01"),
    )
    for dataset in ("NCCI-PTP-OPPS", "NCCI-MUE-OPPS", "FPL", "HPT-nyp", "FAP-nyp"):
        conn.execute(
            "INSERT INTO datasets VALUES (?, ?, ?)", (dataset, "2026", "https://example.com/refdata")
        )
    conn.execute("INSERT INTO meta VALUES (?, ?)", ("version", "test-fixture"))
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def refdata(refdata_db_path):
    return SqliteRefData(refdata_db_path)


def make_bill(*, hospital_id="nyp", charge_cents=20000, patient_balance_cents=20000, code="99213", units=1) -> Bill:
    return Bill(
        provider=Provider(
            name="NewYork-Presbyterian Hospital", nonprofit=True, billing_email_domain="nyp.org", hospital_id=hospital_id
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
