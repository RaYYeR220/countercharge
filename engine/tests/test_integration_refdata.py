"""Integration coverage against a real ``SqliteRefData`` backend.

Every unit test elsewhere in this suite exercises rules against
``MemoryRefData``, whose fixtures are seeded with whatever dataset key each
rule happens to ask for -- which is exactly how the R3 dataset-key mismatch
(rules/r3_mue.py once asking for "MUE-OPPS" while the builder wrote
"NCCI-MUE-OPPS") shipped through a fully green suite: nothing ever ran a
rule that resolves a citation against a schema-correct sqlite seeded the
way the real builder seeds it.

This module closes that gap two ways:

- ``test_audit_against_builder_seeded_sqlite_*`` builds a small sqlite via
  the builder's own ``_connect``/``SCHEMA_SQL`` and inserts ``datasets``
  rows using the exact ``dataset``/``version``/``url`` the builder's own
  ``refdata/build/sources.py`` records, then runs ``audit()`` against it
  and asserts every citation resolves and matches.
- ``test_audit_smoke_against_real_refdata`` runs ``audit()`` against the
  actual production ``refdata.sqlite`` named by the ``COUNTERCHARGE_REFDATA``
  environment variable, skipped when that variable isn't set to an existing
  file (this test file is the one place in the suite allowed to read an env
  var -- the engine itself never does).
"""

import os
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from countercharge_engine import datasets
from countercharge_engine.audit import audit
from countercharge_engine.models import (
    Bill,
    CodeType,
    Household,
    LineItem,
    Provider,
    Setting,
    Totals,
)
from countercharge_engine.refdata.build import sources
from countercharge_engine.refdata.build.__main__ import _connect
from countercharge_engine.refdata.sqlite import SqliteRefData

DOS = date(2026, 3, 1)


def _seed(conn: sqlite3.Connection) -> None:
    """Seed a schema-correct sqlite the way the real builder does: real
    table rows, and `datasets` rows carrying the exact dataset/version/url
    each `Source` in refdata/build/sources.py records."""

    def record(source: sources.Source) -> None:
        conn.execute(
            "INSERT INTO datasets (dataset, version, url) VALUES (?,?,?)",
            (source.dataset, source.version, source.page_url),
        )

    conn.execute(
        "INSERT INTO ptp (tbl, col1, col2, modifier_ind, effective, deleted, rationale) "
        "VALUES (?,?,?,?,?,?,?)",
        ("opps", "99285", "36415", 0, "2026-01-01", None, "standards of medical/surgical practice"),
    )
    record(sources.NCCI_PTP_OPPS)
    record(sources.NCCI_PTP_PRAC)

    conn.execute(
        "INSERT INTO mue (tbl, code, mue_value, mai, rationale) VALUES (?,?,?,?,?)",
        ("opps", "96374", 1, 2, "medically unlikely"),
    )
    record(sources.NCCI_MUE_OPPS)
    record(sources.NCCI_MUE_PRAC)
    record(sources.NCCI_MUE_DME)

    conn.execute("INSERT INTO rates (code, facility, cents) VALUES (?,?,?)", ("99213", 1, 3000))
    record(sources.PFS_RVU)

    conn.execute(
        "INSERT INTO hcpcs2 (code, short_desc) VALUES (?,?)", ("J1885", "Ketorolac tromethamine inj")
    )
    record(sources.HCPCS2)

    conn.execute(
        "INSERT INTO fpl (year, state, first_cents, add_cents) VALUES (?,?,?,?)",
        (2026, "48", 1_596_000, 568_000),
    )
    record(sources.FPL)

    conn.execute(
        "INSERT INTO hospital_price "
        "(hospital_id, code, setting, gross_cents, cash_cents, min_cents, max_cents) "
        "VALUES (?,?,?,?,?,?,?)",
        ("nyp", "99213", "OUTPATIENT", 100000, 60000, None, None),
    )
    record(sources.HPT_NYP)

    conn.execute(
        "INSERT INTO hospital_fap "
        "(hospital_id, name, free_max_fpl, discount_max_fpl, agb_pct, source_url, retrieved) "
        "VALUES (?,?,?,?,?,?,?)",
        ("nyp", "NewYork-Presbyterian", 250, None, 20, "https://nyp.org/fap", "2026-01-15"),
    )
    conn.execute(
        "INSERT INTO datasets (dataset, version, url) VALUES (?,?,?)",
        (datasets.fap_dataset("nyp"), "policy retrieved 2026-01-15", "https://nyp.org/fap"),
    )

    conn.execute("INSERT INTO meta (key, value) VALUES ('version', ?)", ("2026Q4 (test build)",))
    conn.commit()


@pytest.fixture
def refdata(tmp_path: Path) -> SqliteRefData:
    conn = _connect(tmp_path / "refdata.sqlite")
    _seed(conn)
    conn.close()
    return SqliteRefData(tmp_path / "refdata.sqlite")


def test_audit_against_builder_seeded_sqlite_resolves_ptp_mue_and_arithmetic(refdata):
    lines = [
        LineItem(line_id="l1", dos=DOS, code="99285", code_type=CodeType.CPT, units=1, charge_cents=80000),
        LineItem(line_id="l2", dos=DOS, code="36415", code_type=CodeType.CPT, units=1, charge_cents=1500),
        LineItem(line_id="l3", dos=DOS, code="36415", code_type=CodeType.CPT, units=1, charge_cents=1500),
        LineItem(line_id="l4", dos=DOS, code="96374", code_type=CodeType.CPT, units=3, charge_cents=30000),
    ]
    bill = Bill(
        provider=Provider(name="Test Hospital"),
        account_no="I1",
        statement_date=DOS,
        setting=Setting.ER,
        lines=lines,
        totals=Totals(charges_cents=113000, adjustments_cents=0, payments_cents=0, patient_balance_cents=113500),
    )

    report = audit(bill, refdata=refdata)

    by_rule: dict[str, list] = {}
    for finding in report.findings:
        by_rule.setdefault(finding.rule_id, []).append(finding)

    assert {"DUPLICATE", "NCCI_PTP", "MUE", "ARITHMETIC"} <= set(by_rule)

    for finding in by_rule["NCCI_PTP"]:
        assert finding.citation.dataset == sources.NCCI_PTP_OPPS.dataset
        assert finding.citation.version == sources.NCCI_PTP_OPPS.version

    for finding in by_rule["MUE"]:
        assert finding.citation.dataset == sources.NCCI_MUE_OPPS.dataset
        assert finding.citation.version == sources.NCCI_MUE_OPPS.version

    assert report.refdata_version == "2026Q4 (test build)"


def test_audit_against_builder_seeded_sqlite_resolves_cash_price_fap_and_benchmark(refdata):
    bill = Bill(
        provider=Provider(name="NYP", nonprofit=True, hospital_id="nyp"),
        account_no="I2",
        statement_date=DOS,
        setting=Setting.OUTPATIENT,
        self_pay=True,
        lines=[
            LineItem(line_id="l1", dos=DOS, code="99213", code_type=CodeType.CPT, units=1, charge_cents=90000)
        ],
        totals=Totals(charges_cents=90000, patient_balance_cents=90000),
    )
    household = Household(size=4, annual_income_cents=5_000_000, state="NY")

    report = audit(bill, refdata=refdata, household=household)

    by_rule = {f.rule_id: f for f in report.findings}
    assert {"CASH_PRICE", "FAP_501R"} <= set(by_rule)

    assert by_rule["CASH_PRICE"].citation.dataset == sources.HPT_NYP.dataset
    assert by_rule["CASH_PRICE"].citation.version == sources.HPT_NYP.version

    assert by_rule["FAP_501R"].citation.dataset == datasets.fap_dataset("nyp")
    assert by_rule["FAP_501R"].citation.version == "policy retrieved 2026-01-15"

    assert report.fap is not None
    assert report.fap.citation.dataset == datasets.fap_dataset("nyp")

    assert [f.rule_id for f in report.advisory] == ["MEDICARE_BENCHMARK"]
    benchmark = report.advisory[0]
    assert benchmark.citation.dataset == sources.PFS_RVU.dataset
    assert benchmark.citation.version == sources.PFS_RVU.version


# --------------------------------------------------------------------------
# Smoke test against the real, built refdata.sqlite
# --------------------------------------------------------------------------

_REAL_REFDATA_PATH = os.environ.get("COUNTERCHARGE_REFDATA")


@pytest.mark.skipif(
    not _REAL_REFDATA_PATH or not Path(_REAL_REFDATA_PATH).exists(),
    reason="COUNTERCHARGE_REFDATA is not set to an existing refdata.sqlite",
)
def test_audit_smoke_against_real_refdata():
    refdata = SqliteRefData(Path(_REAL_REFDATA_PATH))
    bill = Bill(
        provider=Provider(name="Test Hospital", hospital_id="nyp"),
        account_no="SMOKE1",
        statement_date=date(2026, 3, 15),
        setting=Setting.ER,
        self_pay=True,
        lines=[
            LineItem(line_id="l1", dos=date(2026, 3, 15), code="99285", code_type=CodeType.CPT, units=1, charge_cents=80000),
            LineItem(line_id="l2", dos=date(2026, 3, 15), code="36415", code_type=CodeType.CPT, units=1, charge_cents=1500),
            LineItem(line_id="l3", dos=date(2026, 3, 15), code="96374", code_type=CodeType.CPT, units=3, charge_cents=30000),
            LineItem(line_id="l4", dos=date(2026, 3, 15), code="99213", code_type=CodeType.CPT, units=1, charge_cents=90000),
        ],
        totals=Totals(charges_cents=201500, patient_balance_cents=201500),
    )

    report = audit(bill, refdata=refdata)

    assert isinstance(report.disputable_cents, int)
    assert report.refdata_version != ""
    for finding in report.findings + report.advisory:
        info = refdata.info(finding.citation.dataset)
        assert info.version == finding.citation.version
